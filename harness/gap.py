"""GAP-эскалация: вопрос человеку вместо молчаливой догадки.

Файл `.harness/gap.json` публикует вопрос по активной задаче; guard-плагин на
`tools/pre-execute` превращает его в `ask` для пишущих инструментов. Ответ человека
(approval once) разрешает конкретный вызов, но вопрос остаётся, пока его не снимут
командой `harness gap clear` — «одобрили один раз» не равно «вопрос решён».

Это не замена GAP-гейту брифа: блокирующий вопрос по-прежнему запрещает диспетч.
Здесь — неоднозначность, обнаруженная во время работы.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .state import now

GAP_FILE = ".harness/gap.json"
MUTATING = ("write_file", "bash", "edit_file", "apply_patch", "create_file")


def path_of(root: str) -> str:
    return os.path.join(os.path.abspath(root), GAP_FILE)


def publish(root: str, *, task_id: str, question: str, gap_id: str = "GAP-1") -> dict[str, Any]:
    if not (question or "").strip():
        raise ValueError("R-GAP-2: вопрос без формулировки")
    record = {"task_id": task_id, "gap_id": gap_id, "question": question, "since": now()}
    target = path_of(root)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, target)
    return record


def load(root: str) -> dict[str, Any] | None:
    try:
        with open(path_of(root), encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def clear(root: str) -> bool:
    try:
        os.remove(path_of(root))
        return True
    except FileNotFoundError:
        return False


def decide(root: str, tool: str) -> dict[str, Any]:
    """R-GAP-2: пишущий вызов при открытом вопросе требует ответа человека."""
    gap = load(root)
    if not gap:
        return {"ok": True, "kind": "allow", "rule": "R-GAP-2"}
    if tool not in MUTATING:
        return {"ok": True, "kind": "allow", "rule": "R-GAP-2", "note": "чтение не блокируется"}
    return {
        "ok": False,
        "kind": "ask",
        "rule": "R-GAP-2",
        "reason": f"GAP {gap.get('gap_id')}: {gap.get('question')}",
        "question": gap.get("question", ""),
        "task_id": gap.get("task_id", ""),
    }


CONTROL_POINTS = {
    "gap.decide": decide,
}
