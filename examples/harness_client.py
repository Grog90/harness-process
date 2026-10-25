#!/usr/bin/env python3
"""Минимальный клиент харнесса для любого рантайма агента.

Идея: у агента нет своих файловых инструментов. Каждый вызов инструмента — это
запрос в шлюз, который решает, можно ли действие, и пишет аудит. Так модель не
может «обойти» политику, потому что у неё нет другого способа добраться до файлов.

Пример подключения к своему циклу:

    client = HarnessClient(root=".", role="worker", task_id="FEE-101")
    client.call("write_file", path="src/fee.py", content=patch)   # может бросить ToolDenied
    client.submit()                                                # фиксирует снимок
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any


class ToolDenied(RuntimeError):
    """Действие отклонено контрольным слоем. Несёт правило и причину."""

    def __init__(self, rule: str, reason: str, payload: dict[str, Any] | None = None):
        super().__init__(f"[{rule}] {reason}")
        self.rule = rule
        self.reason = reason
        self.payload = payload or {}


@dataclass
class HarnessClient:
    root: str = "."
    role: str | None = None
    task_id: str | None = None
    python: str = sys.executable
    timeout: float = 180.0
    control_root: str | None = None
    state_path: str | None = None

    def __post_init__(self) -> None:
        self.role = self.role or os.environ.get("HARNESS_ROLE")
        self.task_id = self.task_id or os.environ.get("HARNESS_TASK_ID")
        # в лейне файлы лежат в worktree, а политика и состояние — в главном дереве
        self.control_root = self.control_root or os.environ.get("HARNESS_CONTROL_ROOT")
        self.state_path = self.state_path or os.environ.get("HARNESS_STATE")

    # ------------------------------------------------------------------ инструменты
    def call(self, tool: str, **args: Any) -> dict[str, Any]:
        request = {"tool": tool, "args": args}
        if self.role:
            request["role"] = self.role
        if self.task_id:
            request["task_id"] = self.task_id
        out = self._run(["tool", "--stdin"], stdin=json.dumps(request, ensure_ascii=False))
        if not out.get("ok"):
            raise ToolDenied(out.get("rule", "R-HOOK-ERR"), out.get("reason", "отказ без причины"), out)
        return out["result"]

    def read(self, path: str) -> str:
        return self.call("read_file", path=path)["content"]

    def write(self, path: str, content: str) -> dict[str, Any]:
        return self.call("write_file", path=path, content=content)

    def ls(self, path: str = ".") -> list[dict[str, Any]]:
        return self.call("list_dir", path=path)["entries"]

    def sh(self, command: str, timeout: int = 120) -> dict[str, Any]:
        return self.call("run_command", command=command, timeout=timeout)

    # ------------------------------------------------------------------ этапы цикла
    def submit(self, attempt: int | None = None, snapshot: str | None = None) -> dict[str, Any]:
        """Фиксирует снимок кандидата и переводит задачу в VERIFYING."""
        args = ["submit", "--task", str(self.task_id)]
        if attempt is None:
            attempt = int(self._status()["task"]["attempt"])
        args += ["--attempt", str(attempt)]
        if snapshot:
            args += ["--snapshot", snapshot]
        return self._run(args)

    def status(self) -> dict[str, Any]:
        return self._status()

    def _status(self) -> dict[str, Any]:
        return self._run(["task", "status", "--task", str(self.task_id)])

    # ------------------------------------------------------------------ транспорт
    def _run(self, args: list[str], stdin: str | None = None) -> dict[str, Any]:
        cmd = [self.python, "-m", "harness", "--root", self.root]
        if self.control_root:
            cmd += ["--control-root", self.control_root]
        if self.state_path:
            cmd += ["--db", self.state_path]
        cmd += ["--json", *args]
        proc = subprocess.run(
            cmd,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout,
            cwd=self.root,
            env={
                **os.environ,
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONPATH": os.pathsep.join(
                    filter(None, [os.getcwd(), os.environ.get("PYTHONPATH")])
                ),
            },
        )
        text = (proc.stdout or "").strip()
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"ответ харнесса не JSON: {exc}: {text[:200]}") from exc
        if proc.returncode not in (0, 2):
            raise RuntimeError(f"харнесс упал (код {proc.returncode}): {(proc.stderr or '')[:300]}")
        return data


def _demo() -> int:
    """Небольшая демонстрация: один разрешённый вызов и один отказ.

    Путь должен совпадать с allowed_files брифа; здесь взят путь из
    templates/brief.example.json.
    """
    root = os.environ.get("HARNESS_ROOT", ".")
    client = HarnessClient(root=root)
    try:
        print("статус:", json.dumps(client.status().get("task", {}).get("state"), ensure_ascii=False))
        client.write("src/fee.py", "# записано через шлюз\n")
        print("запись в allowed_files разрешена")
    except ToolDenied as exc:
        print("запись отклонена:", exc)
    try:
        client.write("src/other.py", "# вне брифа\n")
        print("ошибка: запись вне брифа прошла")
    except ToolDenied as exc:
        print("ожидаемый отказ:", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
