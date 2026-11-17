"""Привязка DSH-сессии к задаче harness.

Файл `.harness/session-attach.json` — единственный вход плагина: он переживает
перезапуск DSH и не зависит от env. Сама привязка не меняет задачу; dispatch
делает CLI/плагин, когда создаётся агент.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any

from .state import Store

ATTACH_REL = ".harness/session-attach.json"
AUTO_REL = ".harness/session-auto-submit.json"
ERROR_REL = ".harness/session-attach.error"


def attach_path(root: str) -> str:
    return os.path.join(os.path.abspath(root), ATTACH_REL)


def read_attach(root: str) -> dict[str, Any] | None:
    try:
        with open(attach_path(root), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def attach(root: str, task_id: str, store: Store, auto_submit: bool = False) -> dict[str, Any]:
    task = store.get_task(task_id)  # неизвестная задача — StoreError, файл не пишем
    payload = {
        "task_id": task["task_id"],
        "auto_dispatch": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path = attach_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _atomic_json(path, payload)
    auto_path = os.path.join(os.path.abspath(root), AUTO_REL)
    if auto_submit:
        _atomic_json(auto_path, {"task_id": task["task_id"]})
    else:
        try:
            os.unlink(auto_path)
        except FileNotFoundError:
            pass
    return {"ok": True, "path": path, "attach": payload, "auto_submit": auto_submit}


def _atomic_json(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".session-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_auto_submit(root: str) -> dict[str, Any] | None:
    try:
        with open(os.path.join(os.path.abspath(root), AUTO_REL), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def detach(root: str) -> dict[str, Any]:
    path = attach_path(root)
    try:
        os.unlink(path)
        removed = True
    except FileNotFoundError:
        removed = False
    try:
        os.unlink(os.path.join(os.path.abspath(root), AUTO_REL))
    except FileNotFoundError:
        pass
    return {"ok": True, "path": path, "removed": removed}


def status(root: str) -> dict[str, Any]:
    data = read_attach(root)
    return {"ok": True, "attach": data, "path": attach_path(root)}


def active_role(root: str) -> str | None:
    """Роль из guard-scope: в DSH HARNESS_ROLE не выставляется."""
    try:
        with open(os.path.join(os.path.abspath(root), ".harness", "guard-scope.json"),
                  encoding="utf-8") as fh:
            scope = json.load(fh)
    except (OSError, ValueError):
        return None
    return str(scope.get("role") or "") or None
