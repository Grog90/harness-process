"""R-SPEC-1: изменение поведения начинается со спеки.

Правило проверяет не наличие markdown, а связь: у коммита кода должен быть
change, который этот код описывает. Связь выражается в брифе — `spec_version`
вида `openspec:<change>@<digest>`, который пишет `harness brief from-change`.

Границы, чтобы правило не мешало жить:

* проект без каталога `openspec/` не подпадает под правило — там спеки не ведутся;
* коммит, не трогающий код (документы, тесты, конфигурация), проходит;
* обход возможен, но только явной причиной: она записывается в журнал задачи,
  а не растворяется в истории.
"""

from __future__ import annotations

import os
import re
from typing import Any

RULE = "R-SPEC-1"
SPEC_PREFIX = "openspec:"
CODE_PREFIXES = ("harness/", "src/", "tools/", "examples/")
CODE_EXT = (".py",)

_VERSION = re.compile(r"^openspec:([A-Za-z0-9._-]+)@")


def tracked_project(root: str) -> bool:
    """Ведёт ли проект спеки. Нет каталога — правило не применяется."""
    return os.path.isdir(os.path.join(root, "openspec"))


def change_name(spec_version: str) -> str | None:
    match = _VERSION.match(spec_version or "")
    return match.group(1) if match else None


def change_exists(root: str, name: str) -> bool:
    base = os.path.join(root, "openspec", "changes")
    if os.path.isdir(os.path.join(base, name)):
        return True
    archive = os.path.join(base, "archive")
    if os.path.isdir(archive):
        for entry in os.listdir(archive):
            if entry == name or entry.endswith("-" + name):
                return True
    return False


def touches_code(paths: list[str]) -> bool:
    return [p for p in paths if p.startswith(CODE_PREFIXES) and p.endswith(CODE_EXT)]


def check(root: str, brief: dict[str, Any], changed: list[str]) -> dict[str, Any]:
    """Возвращает {"ok": True} либо отказ с правилом и причиной."""
    if not tracked_project(root):
        return {"ok": True, "skipped": "проект не ведёт openspec/"}

    code = touches_code(changed)
    if not code:
        return {"ok": True, "skipped": "изменение не трогает код"}

    name = change_name(str(brief.get("spec_version") or ""))
    if name is None:
        return {
            "ok": False,
            "rule": RULE,
            "reason": "коммит кода без спеки: бриф не ссылается на openspec-change",
            "hint": "соберите бриф через `harness brief from-change --change <имя>`",
            "code_paths": code,
        }
    if not change_exists(root, name):
        return {
            "ok": False,
            "rule": RULE,
            "reason": f"бриф ссылается на change {name!r}, которого нет ни в живых, ни в архиве",
            "code_paths": code,
        }
    return {"ok": True, "change": name, "code_paths": code}


# Точки контроля, которые реально существуют в коде (для отчёта о покрытии матрицы).
CONTROL_POINTS = {
    "specgate.check": check,
}
