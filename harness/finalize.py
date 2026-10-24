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
from .policy import normalize_path
from .snapshot import compute_for_scope
from .state import Store


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
    return sorted(set(p for p in paths if not p.startswith(CONTROL_PLANE_PREFIXES)))


def pipeline_log_path(task_id: str) -> str:
    return f"pipeline-log/{task_id}.jsonl"

def finalize(
    store: Store,
    engine: Engine,
    root: str,
    task_id: str,
    *,
    message: str | None = None,
    author: str = "harness",
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
    current = compute_for_scope(root, scope)
    if current != task.get("accepted_snapshot"):
        raise FinalizeError(
            "R-SNAPSHOT-1",
            "кандидат изменился после PASS: коммит запрещён, требуется повторная проверка",
            accepted=task.get("accepted_snapshot"),
            current=current,
        )

    allowed = [normalize_path(root, p) for p in _allowed_files(task)]
    log_path = pipeline_log_path(task_id)
    changed = write_set(root)
    unexpected = [p for p in changed if p != log_path and not _within(p, allowed)]
    if unexpected:
        raise FinalizeError(
            "R-WRITESET-1",
            "изменены файлы вне allowed_files: изменение должно пройти через бриф",
            unexpected=unexpected,
            allowed=allowed,
        )

    # 1. Журнал пишется до коммита и едет в нём же (log first).
    _write_pipeline_log(store, root, task_id)
    if log_path not in changed:
        changed = write_set(root)

    # 2. Коммит содержит только разрешённые пути и журнал.
    paths_to_commit = [p for p in write_set(root) if p == log_path or _within(p, allowed)]
    if not paths_to_commit:
        raise FinalizeError("R-FINAL-1", "нет изменений для коммита")
    add = _git(root, "add", "--", *paths_to_commit)
    if add.returncode != 0:
        raise FinalizeError("R-FINAL-1", f"git add не удался: {add.stderr.strip()[:200]}")

    msg = message or f"{task_id}: {_goal(task)}"
    commit = _git(
        root, "-c", f"user.name={author}", "-c", f"user.email={author}@harness.local",
        "commit", "-m", msg,
    )
    if commit.returncode != 0:
        raise FinalizeError("R-FINAL-1", f"git commit не удался: {commit.stderr.strip()[:300]}")

    sha = _git(root, "rev-parse", "HEAD").stdout.strip()

    # 3. После коммита ничего из разрешённой области не должно остаться грязным.
    leftover = [p for p in write_set(root) if p == log_path or _within(p, allowed)]
    if leftover:
        raise FinalizeError(
            "R-FINAL-1", "после коммита остались незакоммиченные изменения разрешённых файлов",
            leftover=leftover, commit=sha,
        )

    latest = store.latest_verdict(task_id)
    store.record_commit(
        commit_sha=sha,
        task_id=task_id,
        snapshot_id=current,
        verdict_id=latest["verdict_id"] if latest else None,
        message=msg,
        finalizer=author,
    )

    out = engine.apply(task_id, "commit", {"snapshot_id": current, "commit_sha": sha}, event_id=f"commit-{sha}")
    return {
        "ok": True,
        "task_id": task_id,
        "commit_sha": sha,
        "snapshot_id": current,
        "state": out.state,
        "message": msg,
        "paths": paths_to_commit,
        "log": log_path,
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
}
