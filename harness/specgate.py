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

_VERSION = re.compile(r"^openspec:([A-Za-z0-9._-]+)@([0-9a-f]*)$")


def tracked_project(root: str) -> bool:
    """Ведёт ли проект спеки. Нет каталога — правило не применяется."""
    return os.path.isdir(os.path.join(root, "openspec"))


def change_name(spec_version: str) -> str | None:
    match = _VERSION.match(spec_version or "")
    return match.group(1) if match else None


def spec_digest(spec_version: str) -> str | None:
    match = _VERSION.match(spec_version or "")
    return (match.group(2) or None) if match else None


def live_change_exists(root: str, name: str) -> bool:
    return os.path.isdir(os.path.join(root, "openspec", "changes", name))


def archived_change_exists(root: str, name: str) -> bool:
    archive = os.path.join(root, "openspec", "changes", "archive")
    if not os.path.isdir(archive):
        return False
    for entry in os.listdir(archive):
        if entry == name or entry.endswith("-" + name):
            return True
    return False


def change_exists(root: str, name: str) -> bool:
    """Любой change: живой или архивный. Для связи коммита нужен только живой."""
    return live_change_exists(root, name) or archived_change_exists(root, name)


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
    if not live_change_exists(root, name):
        if archived_change_exists(root, name):
            return {
                "ok": False,
                "rule": RULE,
                "reason": f"change {name!r} уже в архиве: новый код не может ссылаться на завершённое изменение",
                "hint": "заведите живой change под новое поведение",
                "code_paths": code,
            }
        return {
            "ok": False,
            "rule": RULE,
            "reason": f"бриф ссылается на change {name!r}, которого нет в openspec/changes",
            "code_paths": code,
        }

    # digest в spec_version фиксирует редакцию спеки: если спеку правили после
    # сборки брифа, коммит кода по старой редакции запрещён.
    digest = spec_digest(str(brief.get("spec_version") or ""))
    if digest and len(digest) == 8:
        from . import specbrief

        current = specbrief.spec_digest(root, name)
        if current and current != digest:
            return {
                "ok": False,
                "rule": RULE,
                "reason": f"спека change {name} изменилась после сборки брифа: "
                          f"в брифе {digest}, сейчас {current}",
                "hint": "пересоберите бриф через `harness brief from-change` после правки спеки",
                "code_paths": code,
            }
    return {"ok": True, "change": name, "code_paths": code}


# Точки контроля, которые реально существуют в коде (для отчёта о покрытии матрицы).
CONTROL_POINTS = {
    "specgate.check": check,
}
