"""Активная область прав для плагина-ограждения.

Роль и `allowed_files` обязаны приходить от управляющего процесса, а не из
окружения, которое агент может выставить сам. Здесь они публикуются в файл,
который читает guard плагина: запись делает диспетч, снятие — вердикт или сторож.

Файл намеренно вне `allowed_files` и защищён политикой (`R-PROT-1`), поэтому
рабочая роль не может расширить себе область.
"""

from __future__ import annotations

import json
import os
from typing import Any

SCOPE_REL = os.path.join(".harness", "guard-scope.json")


def scope_path(root: str) -> str:
    return os.path.join(root, SCOPE_REL)


def write_scope(root: str, *, task_id: str, role: str, allowed_files: list[str],
                attempt: int | None = None) -> str:
    path = scope_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "task_id": task_id,
        "role": role,
        "allowed_files": list(allowed_files),
        "attempt": attempt,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def read_scope(root: str) -> dict[str, Any] | None:
    try:
        with open(scope_path(root), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def clear_scope(root: str) -> None:
    try:
        os.unlink(scope_path(root))
    except OSError:
        pass


# Точки контроля, которые реально существуют в коде (для отчёта о покрытии матрицы).
CONTROL_POINTS = {
    "scope.write": write_scope,
}
