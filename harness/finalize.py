"""Доверенный финализатор: проверка write-set и реальный коммит.

Две задачи:

1. **Write-set инвариант.** Перед коммитом сверяется набор изменённых путей с
   `allowed_files` брифа. Агент мог обойти шлюз и записать файл напрямую — здесь
   это видно до того, как изменение станет коммитом.
2. **Отображение «проверенный снимок → итоговый коммит».** Хеш коммита нельзя
   записать внутрь самого коммита, поэтому запись живёт в состоянии управляющего
   процесса: сначала проверяется снимок продукта, затем финализатор добавляет
   служебный журнал и фиксирует связь.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from .fsm import Engine
from .policy import normalize_path, service_base
from .snapshot import snapshot_for_task
from .state import Store
from . import signing, specgate


class FinalizeError(RuntimeError):
    def __init__(self, rule: str, message: str, **details: Any):
        super().__init__(message)
        self.rule = rule
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "decision": "deny", "rule": self.rule, "reason": str(self), "details": self.details}


def _git(root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", root, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def is_git_repo(root: str) -> bool:
    proc = _git(root, "rev-parse", "--is-inside-work-tree")
    return proc.returncode == 0 and proc.stdout.strip() == "true"


CONTROL_PLANE_PREFIXES = (".harness/", "evidence/", "node_modules/", ".pytest_cache/", "__pycache__/")


def write_set(root: str) -> list[str]:
    """Изменённые пути рабочего дерева: staged, unstaged и untracked.

    Служебные области (.harness/, evidence/) из проверки исключены: это артефакты
    управления, а не продукт. Всё остальное обязано попадать в allowed_files.
    """
    proc = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if proc.returncode != 0:
        raise FinalizeError("R-FINAL-1", f"git status недоступен: {proc.stderr.strip()[:200]}")
    entries = proc.stdout.split("\0")
    paths: list[str] = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        paths.append(path.replace("\\", "/"))
        if status[0] in ("R", "C") and i < len(entries):
            # при переименовании следующим элементом идёт исходный путь
            paths.append(entries[i].replace("\\", "/"))
            i += 1
    return sorted(set(p for p in paths if not _is_control(p)))


def _is_control(path: str) -> bool:
    """Служебные файлы: состояние, артефакты, сгенерированный кэш."""
    if path.startswith(CONTROL_PLANE_PREFIXES):
        return True
    low = path.lower()
    return "__pycache__/" in low or low.endswith(".pyc") or low.startswith(".pytest_cache/")


def pipeline_log_path(task_id: str) -> str:
    return f"pipeline-log/{task_id}.jsonl"


def _writable_services(task: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in (_brief_data(task).get("services") or [])
            if isinstance(s, dict) and s.get("name") and s.get("write")]


def _own_git_repo(path: str) -> bool:
    """Сервис — самостоятельный git-репозиторий, а не подкаталог главного."""
    proc = _git(path, "rev-parse", "--show-toplevel")
    return proc.returncode == 0 and os.path.normcase(os.path.realpath(proc.stdout.strip())) == \
        os.path.normcase(os.path.realpath(path))


def _main_allowed(root: str, task: dict[str, Any]) -> list[str]:
    """allowed_files в путях главного репозитория.

    In-root сервис без своего git коммитится главным репозиторием: его
    `service:<name>/<rel>` разворачивается в обычный относительный путь.
    """
    allowed: list[str] = []
    root_abs = os.path.abspath(root)
    for item in _allowed_files(task):
        if not item.startswith("service:"):
            allowed.append(normalize_path(root, item))
            continue
        rest = item[len("service:"):]
        name, inner = (rest.split("/", 1) + [""])[:2]
        service = next((s for s in _writable_services(task) if str(s.get("name")) == name), None)
        if service is None:
            allowed.append(item)
            continue
        base = os.path.abspath(service_base(root, service))
        if not _own_git_repo(base) and os.path.normcase(base).startswith(
                os.path.normcase(root_abs) + os.sep):
            rel_base = os.path.relpath(base, root_abs).replace("\\", "/")
            allowed.append(os.path.join(rel_base, inner).replace("\\", "/"))
        else:
            allowed.append(item)
    return allowed


def _own_service_roots(root: str, task: dict[str, Any]) -> list[str]:
    """Относительные корни вложенных git-сервисов: их не коммитит главный репозиторий."""
    root_abs = os.path.abspath(root)
    roots: list[str] = []
    for service in _writable_services(task):
        base = os.path.abspath(service_base(root, service))
        if _own_git_repo(base) and os.path.normcase(base).startswith(
                os.path.normcase(root_abs) + os.sep):
            roots.append(os.path.relpath(base, root_abs).replace("\\", "/"))
    return roots


def _service_allowed(task: dict[str, Any], name: str) -> list[str]:
    prefix = f"service:{name}/"
    return [p[len(prefix):] for p in _allowed_files(task) if p.startswith(prefix)]


def _rollback_records(store: Store, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Откатывает уже сделанные коммиты, сохраняя изменения в индексе (reset --soft)."""
    rolled: list[dict[str, Any]] = []
    for rec in reversed(records):
        previous = rec.get("previous_sha") or ""
        sha = rec.get("commit_sha") or ""
        base = rec.get("path")
        if not previous or not sha or not base:
            continue
        proc = _git(base, "reset", "--soft", previous)
        if proc.returncode == 0:
            store.delete_commit(sha)
            rolled.append({"name": rec.get("name"), "sha": sha, "ok": True})
        else:
            rolled.append({"name": rec.get("name"), "sha": sha, "ok": False,
                           "error": proc.stderr.strip()[:200]})
    return rolled


def commit_services(store: Store, root: str, task: dict[str, Any], snapshot_id: str,
                    message: str, author: str = "harness",
                    signing_ctx: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Коммитит каждый writable-сервис отдельно, с write-set-проверкой.

    Сервисный репозиторий обязан существовать: молча пропустить коммит нельзя,
    иначе evidence соврёт о том, что изменение зафиксировано. При ошибке уже
    сделанные коммиты откатываются (`reset --soft`), а записи в БД удаляются.
    """
    records: list[dict[str, Any]] = []
    try:
        for service in _writable_services(task):
            name = str(service.get("name"))
            base = service_base(root, service)
            if not os.path.isdir(base):
                raise FinalizeError("R-FINAL-1", f"сервис {name!r} не найден: {base}")
            root_abs = os.path.abspath(root)
            inside_main = os.path.normcase(base).startswith(os.path.normcase(root_abs) + os.sep)
            if inside_main and not _own_git_repo(base):
                # Вложенный сервис без своего git: файлы уедут главным коммитом.
                records.append({"name": name, "path": base, "commit_sha": None, "paths": [],
                                "mode": "main"})
                continue
            if not is_git_repo(base):
                raise FinalizeError("R-FINAL-1", f"сервис {name!r} не git-репозиторий: {base}")
            allowed = _service_allowed(task, name)
            changed = write_set(base)
            unexpected = [p for p in changed if not _within(p, allowed)]
            if unexpected:
                raise FinalizeError(
                    "R-WRITESET-1",
                    f"в сервисе {name!r} изменены файлы вне allowed_files",
                    service=name, unexpected=unexpected, allowed=allowed,
                )
            if not changed:
                continue
            previous = _git(base, "rev-parse", "HEAD").stdout.strip()
            paths = [p for p in changed if _within(p, allowed)]
            add = _git(base, "add", "--", *paths)
            if add.returncode != 0:
                raise FinalizeError("R-FINAL-1", f"git add сервиса {name!r} не удался: {add.stderr.strip()[:200]}")
            msg = f"{message} [{name}]"
            commit = _git(
                base, *signing.commit_args(signing_ctx, author),
                "commit", *(["-S"] if signing_ctx else []), "-m", msg,
            )
            if commit.returncode != 0:
                raise FinalizeError("R-FINAL-1", f"git commit сервиса {name!r} не удался: {commit.stderr.strip()[:300]}")
            sha = _git(base, "rev-parse", "HEAD").stdout.strip()
            rec = {"name": name, "path": base, "commit_sha": sha, "paths": paths,
                   "previous_sha": previous}
            records.append(rec)
            if signing_ctx:
                try:
                    signing.verify(base, signing_ctx)
                except signing.SigningError as exc:
                    raise FinalizeError(exc.rule, f"сервис {name!r}: {exc}", **exc.details) from exc
            store.record_commit(commit_sha=sha, task_id=task["task_id"], snapshot_id=snapshot_id,
                                message=msg, finalizer=author)
            leftover = write_set(base)
            if leftover:
                raise FinalizeError(
                    "R-FINAL-1", f"после коммита сервиса {name!r} остались изменения",
                    service=name, leftover=leftover, commit=sha,
                )
    except Exception as exc:
        rolled = _rollback_records(store, records)
        if isinstance(exc, FinalizeError):
            exc.details["rollback"] = rolled
            raise
        raise FinalizeError("R-FINAL-1", f"откат сервисных коммитов после ошибки: {exc}",
                            rollback=rolled) from exc
    return records


def finalize(
    store: Store,
    engine: Engine,
    root: str,
    task_id: str,
    *,
    message: str | None = None,
    author: str = "harness",
    ignore_paths: list[str] | None = None,
    allow_no_spec: str | None = None,
) -> dict[str, Any]:
    root = os.path.abspath(root)
    task = store.get_task(task_id)

    if task["state"] != "ACCEPTED":
        raise FinalizeError(
            "R-VERIFY-1", f"задача в состоянии {task['state']}: коммит разрешён только из ACCEPTED"
        )
    if not is_git_repo(root):
        raise FinalizeError("R-FINAL-1", "корень не является git-репозиторием")

    scope = task.get("snapshot_scope") or ""
    try:
        current = snapshot_for_task(root, task, scope)
    except ValueError as exc:
        raise FinalizeError("R-FINAL-1", str(exc)) from exc
    if current != task.get("accepted_snapshot"):
        raise FinalizeError(
            "R-SNAPSHOT-1",
            "кандидат изменился после PASS: коммит запрещён, требуется повторная проверка (файлы задачи не менялись — `harness event --task <id> --event recheck`; менялись — invalidate)",
            accepted=task.get("accepted_snapshot"),
            current=current,
        )

    allowed = _main_allowed(root, task)
    log_path = pipeline_log_path(task_id)
    # бриф и служебные файлы не продукт: они не должны блокировать коммит
    ignored = {normalize_path(root, p) for p in (ignore_paths or [])}
    nested = _own_service_roots(root, task)

    def _in_nested(path: str) -> bool:
        return any(path == base or path.startswith(base + "/") for base in nested)

    changed = [p for p in write_set(root) if p not in ignored and not _in_nested(p)]
    unexpected = [p for p in changed if p != log_path and not _within(p, allowed)]
    if unexpected:
        raise FinalizeError(
            "R-WRITESET-1",
            "изменены файлы вне allowed_files: изменение должно пройти через бриф",
            unexpected=unexpected,
            allowed=allowed,
        )

    # R-SPEC-1: код коммитится только со связанным change. Обход — явной причиной.
    gate = specgate.check(root, _brief_data(task), changed)
    if not gate["ok"]:
        if not (allow_no_spec or "").strip():
            raise FinalizeError(gate["rule"], gate["reason"],
                                **{k: v for k, v in gate.items() if k not in ("ok", "rule", "reason")})
        store.log(task_id, f"обход {gate['rule']}: {allow_no_spec.strip()}")

    msg = message or f"{task_id}: {_goal(task)}"
    try:
        signing_ctx = signing.context(root)
    except signing.SigningError as exc:
        raise FinalizeError(exc.rule, str(exc), **exc.details) from exc
    # 0. Сервисные репозитории коммитятся до главного: главный SHA остаётся
    # последним в tasks.commit_sha, а сервисные уже записаны в commits.
    service_records = commit_services(store, root, task, current, msg, author, signing_ctx)
    main_previous = _git(root, "rev-parse", "HEAD").stdout.strip()
    main_sha: str | None = None

    try:
        # 1. Журнал пишется до коммита и едет в нём же (log first).
        _write_pipeline_log(store, root, task_id)
        if log_path not in changed:
            changed = write_set(root)

        # 2. Коммит содержит только разрешённые пути и журнал.
        paths_to_commit = [p for p in write_set(root)
                           if p not in ignored and not _in_nested(p)
                           and (p == log_path or _within(p, allowed))]
        if not paths_to_commit:
            raise FinalizeError("R-FINAL-1", "нет изменений для коммита")
        add = _git(root, "add", "--", *paths_to_commit)
        if add.returncode != 0:
            raise FinalizeError("R-FINAL-1", f"git add не удался: {add.stderr.strip()[:200]}")

        commit = _git(
            root, *signing.commit_args(signing_ctx, author),
            "commit", *(["-S"] if signing_ctx else []), "-m", msg,
        )
        if commit.returncode != 0:
            raise FinalizeError("R-FINAL-1", f"git commit не удался: {commit.stderr.strip()[:300]}")

        main_sha = _git(root, "rev-parse", "HEAD").stdout.strip()
        if signing_ctx:
            try:
                signing.verify(root, signing_ctx)
            except signing.SigningError as exc:
                raise FinalizeError(exc.rule, str(exc), **exc.details) from exc
        for rec in service_records:
            if rec.get("mode") == "main":
                rec["commit_sha"] = main_sha

        # 3. После коммита ничего из разрешённой области не должно остаться грязным.
        leftover = [p for p in write_set(root)
                    if p not in ignored and not _in_nested(p)
                    and (p == log_path or _within(p, allowed))]
        if leftover:
            raise FinalizeError(
                "R-FINAL-1", "после коммита остались незакоммиченные изменения разрешённых файлов",
                leftover=leftover, commit=main_sha,
            )

        latest = store.latest_verdict(task_id)
        store.record_commit(
            commit_sha=main_sha,
            task_id=task_id,
            snapshot_id=current,
            verdict_id=latest["verdict_id"] if latest else None,
            message=msg,
            finalizer=author,
        )

        out = engine.apply(task_id, "commit", {"snapshot_id": current, "commit_sha": main_sha},
                           event_id=f"commit-{main_sha}")
    except Exception as exc:
        rolled = _rollback_records(store, service_records)
        if main_sha:
            reset = _git(root, "reset", "--soft", main_previous)
            if reset.returncode == 0:
                store.delete_commit(main_sha)
                rolled.append({"name": "main", "sha": main_sha, "ok": True})
            else:
                rolled.append({"name": "main", "sha": main_sha, "ok": False,
                               "error": reset.stderr.strip()[:200]})
        if isinstance(exc, FinalizeError):
            exc.details["rollback"] = rolled
            raise
        raise FinalizeError("R-FINAL-1", f"финализатор откачен после ошибки: {exc}",
                            rollback=rolled) from exc
    return {
        "ok": True,
        "task_id": task_id,
        "commit_sha": main_sha,
        "snapshot_id": current,
        "state": out.state,
        "message": msg,
        "paths": paths_to_commit,
        "log": log_path,
        "service_commits": service_records,
    }


def _allowed_files(task: dict[str, Any]) -> list[str]:
    if not task.get("brief_json"):
        return []
    brief = json.loads(task["brief_json"])
    return [str(x) for x in brief.get("allowed_files", [])]


def _goal(task: dict[str, Any]) -> str:
    if not task.get("brief_json"):
        return "задача без брифа"
    return str(json.loads(task["brief_json"]).get("goal", ""))[:72]


def _brief_data(task: dict[str, Any]) -> dict[str, Any]:
    if not task.get("brief_json"):
        return {}
    return json.loads(task["brief_json"])


def _within(path: str, allowed: list[str]) -> bool:
    from .policy import matches_any

    return matches_any(path, allowed)


def _write_pipeline_log(store: Store, root: str, task_id: str) -> str:
    rel = pipeline_log_path(task_id)
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    entries = store.log_for(task_id)
    with open(full, "w", encoding="utf-8", newline="\n") as fh:
        for e in entries:
            fh.write(
                json.dumps(
                    {
                        "entry_id": e["entry_id"],
                        "task_id": e["task_id"],
                        "attempt": e["attempt"],
                        "message": e["message"],
                        "created_at": e["created_at"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
    return rel

# Точки контроля, которые реально существуют в коде (для отчёта о покрытии матрицы).
CONTROL_POINTS = {
    "finalize.write_set": write_set,
    "finalize.commit": finalize,
    "finalize.commit_services": commit_services,
}
