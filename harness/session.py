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


def attach(root: str, task_id: str, store: Store) -> dict[str, Any]:
    task = store.get_task(task_id)  # неизвестная задача — StoreError, файл не пишем
    payload = {
        "task_id": task["task_id"],
        "auto_dispatch": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path = attach_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".session-attach-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return {"ok": True, "path": path, "attach": payload}


def detach(root: str) -> dict[str, Any]:
    path = attach_path(root)
    try:
        os.unlink(path)
        removed = True
    except FileNotFoundError:
        removed = False
    return {"ok": True, "path": path, "removed": removed}


def status(root: str) -> dict[str, Any]:
    data = read_attach(root)
    return {"ok": True, "attach": data, "path": attach_path(root)}
