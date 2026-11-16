"""Шлюз инструментов: единственный разрешённый путь записи для агента.

Рантайм агента не получает прямого доступа к файлам — он вызывает шлюз, а шлюз
спрашивает политику и хуки. Роль и активная задача берутся из окружения процесса,
а `allowed_files` — из брифа в управляющем процессе, а не из аргументов агента
(иначе агент просто расширил бы себе права одной строкой JSON).

Это не песочница: шлюз ограничивает действия, которые идут через него. Обход
(прямая запись из своего процесса) ловит финализатор по write-set.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Any

from . import hooks
from .hooks import HookContext
from .policy import Policy, deny, resolve_target, service_base
from .snapshot import compute_for_scope
from .state import Store, now

TOOLS = ("read_file", "list_dir", "write_file", "run_command", "snapshot")
DEFAULT_TIMEOUT = 120
MAX_READ_BYTES = 400_000


class Gateway:
    def __init__(self, root: str, policy: Policy, store: Store):
        self.root = os.path.abspath(root)
        self.policy = policy
        self.store = store

    # ------------------------------------------------------------------ контекст
    def _task(self, task_id: str | None) -> dict[str, Any] | None:
        if not task_id:
            return None
        return self.store.find_task(task_id)

    def _allowed_files(self, task: dict[str, Any] | None) -> list[str]:
        if not task or not task.get("brief_json"):
            return []
        brief = json.loads(task["brief_json"])
        return [str(x) for x in brief.get("allowed_files", [])]

    def _services(self, task: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not task or not task.get("brief_json"):
            return []
        brief = json.loads(task["brief_json"])
        return [s for s in brief.get("services", []) if isinstance(s, dict)]

    # ------------------------------------------------------------------ обработка
    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        env_role = os.environ.get("HARNESS_ROLE")
        requested_role = request.get("role")
        role = str(env_role or requested_role or "")
        claimed_role = (
            str(requested_role)
            if env_role and requested_role and str(requested_role) != str(env_role)
            else request.get("claimed_role")
        )
        task_id = request.get("task_id") or os.environ.get("HARNESS_TASK_ID")
        tool = str(request.get("tool") or "")
        args = request.get("args") or {}

        if not role:
            return self._deny(None, "", tool, "R-ROLE-1", "роль не выдана запускающим процессом", task_id)
        if tool not in TOOLS:
            return self._deny(role, "", tool, "R-WORK-2", f"инструмент {tool!r} не входит в шлюз", task_id)

        task = self._task(task_id)
        if task is not None and task["state"] != "RUNNING":
            return self._deny(
                role, "", tool, "R-STATE-1",
                f"задача в состоянии {task['state']}: инструменты исполнителя недоступны", task_id,
            )
        if task is not None and task.get("deadline_ts") and now() > float(task["deadline_ts"]):
            return self._deny(role, "", tool, "R-TIMEOUT-1", "срок диспетча истёк: полномочия отозваны", task_id)

        path = args.get("path")
        ctx = HookContext(
            event="pretooluse",
            tool=tool if tool != "run_command" else "bash",
            role=role,
            claimed_role=claimed_role,
            task_id=task_id,
            path=path,
            command=args.get("command"),
            target=args.get("path"),
            allowed_files=self._allowed_files(task),
            services=self._services(task),
            policy=self.policy,
            root=self.root,
        )
        decision = hooks.run("pretooluse", ctx)
        if decision["decision"] == "deny":
            return self._deny(role, path, tool, decision["rule"], decision["reason"], task_id,
                              details=decision.get("details"))

        try:
            result = self._execute(tool, args, role, self._services(task))
        except GatewayError as exc:
            return self._deny(role, path, tool, exc.rule, str(exc), task_id)

        self.store.audit_tool(
            role=role, tool=tool, decision=decision["decision"], rule=decision.get("rule", ""),
            task_id=task_id, path=path, summary=_summary(tool, args, result),
        )
        return {
            "ok": True,
            "decision": decision["decision"],
            "rule": decision.get("rule", ""),
            "tool": tool,
            "result": result,
        }

    def _deny(self, role: str | None, path: str | None, tool: str, rule: str, reason: str,
              task_id: str | None, details: dict[str, Any] | None = None) -> dict[str, Any]:
        if role:
            self.store.audit_tool(role=role, tool=tool, decision="deny", rule=rule,
                                  task_id=task_id, path=path, summary=reason)
        out = {"ok": False, "decision": "deny", "rule": rule, "reason": reason, "tool": tool}
        if details:
            out["details"] = details
        return out

    # ------------------------------------------------------------------ исполнение
    def _execute(self, tool: str, args: dict[str, Any], role: str,
                 services: list[dict[str, Any]] | None = None) -> Any:
        services = services or []
        if tool == "read_file":
            return self._read(args, services)
        if tool == "list_dir":
            return self._list(args, services)
        if tool == "write_file":
            return self._write(args, services)
        if tool == "run_command":
            return self._run(args)
        if tool == "snapshot":
            scope = str(args.get("scope") or "")
            return {"snapshot_id": compute_for_scope(self.root, scope), "scope": scope}
        raise GatewayError("R-WORK-2", f"инструмент {tool!r} не реализован")

    def _base_for(self, key: str, services: list[dict[str, Any]]) -> tuple[str, str]:
        if key.startswith("service:"):
            name = key.split("/", 1)[0][len("service:"):]
            inner = key.split("/", 1)[1] if "/" in key else ""
            service = next((s for s in services if str(s.get("name")) == name), None)
            if service is None:
                raise GatewayError("R-PROT-1", f"сервис {name!r} не объявлен в брифе")
            return service_base(self.root, service), inner
        return self.root, key

    def _resolve(self, raw: str | None, services: list[dict[str, Any]]) -> str:
        if not raw:
            raise GatewayError("R-PROT-1", "не указан путь")
        key = resolve_target(self.root, raw, services)
        if key == "<outside-root>":
            raise GatewayError("R-PROT-1", f"путь вне рабочего корня: {raw!r}")
        base, inner = self._base_for(key, services)
        full = os.path.realpath(os.path.join(base, inner))
        base_real = os.path.realpath(base)
        if not (full == base_real or full.startswith(base_real + os.sep)):
            raise GatewayError("R-PROT-1", f"путь выходит из корня через ссылку: {raw!r}")
        return key

    def _full(self, key: str, services: list[dict[str, Any]]) -> str:
        base, inner = self._base_for(key, services)
        return os.path.join(base, inner)

    def _read(self, args: dict[str, Any], services: list[dict[str, Any]]) -> dict[str, Any]:
        rel = self._resolve(args.get("path"), services)
        full = self._full(rel, services)
        if not os.path.isfile(full):
            raise GatewayError("R-PROT-1", f"файл не найден: {rel}")
        if os.path.getsize(full) > MAX_READ_BYTES:
            raise GatewayError("R-PROT-1", f"файл слишком велик для чтения целиком: {rel}")
        with open(full, encoding="utf-8", errors="replace") as fh:
            return {"path": rel, "content": fh.read()}

    def _list(self, args: dict[str, Any], services: list[dict[str, Any]]) -> dict[str, Any]:
        rel = self._resolve(args.get("path") or ".", services)
        full = self._full(rel, services)
        if not os.path.isdir(full):
            raise GatewayError("R-PROT-1", f"каталог не найден: {rel}")
        entries = []
        for name in sorted(os.listdir(full))[:500]:
            child = os.path.join(full, name)
            entries.append({"name": name, "dir": os.path.isdir(child)})
        return {"path": rel, "entries": entries}

    def _write(self, args: dict[str, Any], services: list[dict[str, Any]]) -> dict[str, Any]:
        rel = self._resolve(args.get("path"), services)
        content = args.get("content")
        if content is None:
            raise GatewayError("R-WORK-1", "write_file без содержимого")
        full = self._full(rel, services)
        os.makedirs(os.path.dirname(full) or self.root, exist_ok=True)
        # атомарная замена: недописанный файл не должен попасть в снимок
        fd, tmp = tempfile.mkstemp(prefix=".gw-", dir=os.path.dirname(full) or self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
            os.replace(tmp, full)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return {"path": rel, "bytes": len(content.encode("utf-8"))}

    def _run(self, args: dict[str, Any]) -> dict[str, Any]:
        command = str(args.get("command") or "").strip()
        if not command:
            raise GatewayError("R-WORK-2", "run_command без команды")
        timeout = int(args.get("timeout") or DEFAULT_TIMEOUT)
        timeout = max(1, min(timeout, 600))
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", self.root),
            "TMPDIR": os.environ.get("TMPDIR", tempfile.gettempdir()),
            "LANG": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
        }
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            raise GatewayError("R-SLEEP-1", f"команда превысила лимит {timeout}с") from None
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-20_000:],
            "stderr": proc.stderr[-20_000:],
            "timeout": timeout,
        }


class GatewayError(RuntimeError):
    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule


def _summary(tool: str, args: dict[str, Any], result: Any) -> str:
    if tool == "write_file":
        return f"записано {result.get('bytes')} байт в {result.get('path')}"
    if tool == "read_file":
        return f"прочитан {result.get('path')}"
    if tool == "list_dir":
        return f"каталог {result.get('path')}, записей {len(result.get('entries', []))}"
    if tool == "run_command":
        return f"код {result.get('exit_code')}, команда {str(args.get('command'))[:120]}"
    if tool == "snapshot":
        return f"снимок {result.get('snapshot_id', '')[:24]}"
    return ""

# Точки контроля, которые реально существуют в коде (для отчёта о покрытии матрицы).
CONTROL_POINTS = {
    "gateway.tool": Gateway.handle,
    "gateway.state": Gateway.handle,
}
