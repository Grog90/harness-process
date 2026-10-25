"""Лейны: изоляция мутирующих задач по git worktree.

Пока все пишут в одно дерево, параллельная работа превращается в гонку: текстовые
изменения не конфликтуют, а выполнение тестов мешает соседу. Поэтому каждая
мутирующая задача получает собственный worktree и ветку, а общий ресурс (главное
дерево) защищён мьютексом на слияние.

Правило, которое здесь важнее кода: **конфликт не правит координатор**. Слияние
либо проходит, либо откатывается и превращается в задачу для человека.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from typing import Any, Iterator

from .state import Store, now


class LaneError(RuntimeError):
    def __init__(self, rule: str, message: str, **details: Any):
        super().__init__(message)
        self.rule = rule
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "decision": "deny", "rule": self.rule, "reason": str(self), "details": self.details}


def _git(root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", root, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _require_repo(root: str) -> None:
    proc = _git(root, "rev-parse", "--is-inside-work-tree")
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        raise LaneError("R-WAVE-1", "корень не является git-репозиторием")


def worktree_root(root: str) -> str:
    return os.path.join(root, ".harness", "worktrees")


def lane_path(root: str, task_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in task_id)
    return os.path.join(worktree_root(root), safe)


def branch_name(task_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in task_id)
    return f"harness/{safe}"


# --------------------------------------------------------------------- мьютекс


@contextmanager
def merge_lock(root: str, *, timeout: float = 120.0, stale_after: float = 900.0) -> Iterator[None]:
    """Один слиятель на главное дерево. Ожидание ограничено и наблюдаемо."""
    path = os.path.join(root, ".harness", "merge.lock")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(path)
            except OSError:
                age = 0
            if age > stale_after:
                os.unlink(path)  # протухшая блокировка не должна останавливать работу
                continue
            if time.time() > deadline:
                raise LaneError("R-WAVE-1", f"мьютекс слияния занят дольше {timeout}с", lock=path)
            time.sleep(0.5)
            continue
        else:
            os.write(fd, json.dumps({"pid": os.getpid(), "at": now()}).encode())
            os.close(fd)
            break
    try:
        yield
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# --------------------------------------------------------------------- операции


def create(root: str, store: Store, task_id: str, *, base: str = "HEAD") -> dict[str, Any]:
    """Создаёт worktree и ветку для задачи."""
    root = os.path.abspath(root)
    _require_repo(root)
    path = lane_path(root, task_id)
    branch = branch_name(task_id)
    if os.path.exists(path):
        raise LaneError("R-WAVE-1", f"worktree уже существует: {path}")

    proc = _git(root, "worktree", "add", "-b", branch, path, base)
    if proc.returncode != 0:
        raise LaneError("R-WAVE-1", f"git worktree add не удался: {proc.stderr.strip()[:300]}")

    store.update_task(task_id, lane=branch)
    store.log(task_id, f"создан лейн {branch} в {os.path.relpath(path, root)}")
    return {
        "ok": True,
        "task_id": task_id,
        "lane": branch,
        "path": path,
        "base": base,
        "hint": "агент работает с --root внутри worktree; коммитит финализатор в нём же",
    }


def list_lanes(root: str) -> list[dict[str, Any]]:
    root = os.path.abspath(root)
    _require_repo(root)
    proc = _git(root, "worktree", "list", "--porcelain")
    lanes: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in (proc.stdout or "").splitlines():
        if not line.strip():
            if current:
                lanes.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = value
        elif key == "branch":
            current["branch"] = value.replace("refs/heads/", "")
        elif key == "HEAD":
            current["head"] = value
        elif key == "detached":
            current["detached"] = True
    if current:
        lanes.append(current)
    return lanes


def merge(root: str, store: Store, task_id: str, *, message: str | None = None,
          ignore_paths: list[str] | None = None) -> dict[str, Any]:
    """Сливает завершённый лейн в главное дерево под мьютексом.

    Координатор не разрешает конфликты: при конфликте слияние откатывается,
    а задача получает пометку для человека.
    """
    root = os.path.abspath(root)
    _require_repo(root)
    task = store.get_task(task_id)
    if task["state"] != "COMMITTED":
        raise LaneError(
            "R-FINAL-1",
            f"задача в состоянии {task['state']}: в главное дерево сливается только закоммиченный лейн",
        )
    commit = task.get("commit_sha")
    if not commit:
        raise LaneError("R-FINAL-1", "у задачи нет зафиксированного коммита")

    with merge_lock(root):
        ignored = {p.replace("\\", "/") for p in (ignore_paths or [])}
        dirty = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
        if dirty and any(
            not p.startswith((".harness/", "evidence/")) and p not in ignored
            for p in _paths(dirty)
        ):
            raise LaneError("R-WAVE-1", "главное дерево грязное: слияние требует чистого состояния")

        proc = _git(
            root, "-c", "user.name=harness", "-c", "user.email=harness@local",
            "merge", "--no-ff", "-m", message or f"merge {task_id}", commit,
        )
        if proc.returncode != 0:
            conflicts = [
                line.strip() for line in (proc.stdout + proc.stderr).splitlines()
                if "CONFLICT" in line or "конфликт" in line
            ]
            _git(root, "merge", "--abort")
            store.log(task_id, f"слияние отклонено: конфликт ({len(conflicts)} файлов)")
            raise LaneError(
                "R-WAVE-1",
                "конфликт слияния: координатор не правит продукт, нужна отдельная задача",
                conflicts=conflicts or ["конфликт без детализации"],
                aborted=True,
            )

        head = _git(root, "rev-parse", "HEAD").stdout.strip()
        tree = _git(root, "rev-parse", "HEAD^{tree}").stdout.strip()

    store.record_commit(commit_sha=head, task_id=task_id, snapshot_id=task.get("accepted_snapshot") or "",
                        verdict_id=task.get("verdict_id"), message=message or f"merge {task_id}",
                        finalizer="merge-queue")
    store.log(task_id, f"лейн слит в главное дерево: {head[:10]}")
    return {"ok": True, "task_id": task_id, "merge_commit": head, "tree": tree}


def remove(root: str, task_id: str, *, force: bool = True) -> dict[str, Any]:
    root = os.path.abspath(root)
    path = lane_path(root, task_id)
    if not os.path.exists(path):
        raise LaneError("R-WAVE-1", f"worktree не найден: {path}")
    args = ["worktree", "remove", path]
    if force:
        args.insert(2, "--force")
    proc = _git(root, *args)
    if proc.returncode != 0:
        shutil.rmtree(path, ignore_errors=True)
        _git(root, "worktree", "prune")
    return {"ok": True, "task_id": task_id, "removed": path}


def _paths(porcelain: bytes | str) -> list[str]:
    text = porcelain.decode() if isinstance(porcelain, bytes) else porcelain
    out = []
    for entry in text.split("\0"):
        if len(entry) >= 4:
            out.append(entry[3:].replace("\\", "/"))
    return out


# Точки контроля, которые реально существуют в коде (для отчёта о покрытии матрицы).
CONTROL_POINTS = {
    "lanes.merge": merge,
    "lanes.lock": merge_lock,
}
