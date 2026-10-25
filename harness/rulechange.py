"""Дисциплина изменения правил.

Матрица правил полезна ровно до тех пор, пока текст правила, хук и его тесты
меняются вместе. Практика, от которой это защищает, описана в книге: правило
расширили — хук продолжил проверять старую формулировку; два хука с общей
эвристикой разъехались; новое правило появилось без проверки.

Механизм простой и потому работающий: у файла правил есть отпечаток; любое
изменение обязано сопровождаться записью причины и прогоном позитивного и
негативного случаев этого правила. Изменение без записи видно как дрейф.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

from . import selftest
from .rules import load_registry
from .state import Store


def rules_path(root: str) -> str:
    return os.path.join(root, "policies", "rules.json")


def rules_hash(root: str) -> str:
    with open(rules_path(root), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:16]


def verify(store: Store, root: str) -> dict[str, Any]:
    """Проверка: менялись ли правила без записи причины и прогона тестов.

    Пустая история — не дрейф, а отсутствие базовой линии: это разные состояния,
    и путать их нельзя (иначе первая же установка выглядит как нарушение).
    """
    current = rules_hash(root)
    registry = load_registry(root)
    covered = {c.rule for c in selftest.CHECKS}
    untested = [r["id"] for r in registry.get("rules", []) if r["id"] not in covered]
    last = store.latest_rule_change()

    if last is None:
        state, drift, ok = "no_history", False, True
        reason = "история изменений пуста: базовая линия будет записана первым изменением"
    elif last["rules_hash"] != current:
        state, drift, ok = "drift", True, False
        reason = "правила менялись после последней записи: сначала прогон, потом включение"
    else:
        state, drift, ok = "current", False, True
        reason = ""

    return {
        "ok": ok,
        "state": state,
        "rules_hash": current,
        "last_change": None
        if last is None
        else {"change_id": last["change_id"], "rule_id": last["rule_id"], "reason": last["reason"],
              "rules_hash": last["rules_hash"], "at": last["created_at"]},
        "drift": drift,
        "reason": reason,
        "rules_without_tests": untested,
        "rule_count": len(registry.get("rules", [])),
    }


def change(store: Store, root: str, rule_id: str, reason: str, *, author: str = "human") -> dict[str, Any]:
    """Записывает изменение правила после прогона его проверок.

    Порядок важен: сначала проверки, потом запись. Иначе запись станет способом
    «узаконить» любое изменение.
    """
    if not (reason or "").strip():
        raise ValueError("R-HOOK-1: изменение правила требует причины")

    registry = load_registry(root)
    known = {r["id"] for r in registry.get("rules", [])}
    if rule_id not in known:
        raise ValueError(f"R-HOOK-1: правило {rule_id} не найдено в реестре")

    report = selftest.run_rule(rule_id, root)
    if report["total"] == 0:
        raise ValueError(f"R-HOOK-1: у правила {rule_id} нет исполняемых проверок — включать нельзя")
    if not report["ok"]:
        failed = ", ".join(r["name"] for r in report["results"] if not r["ok"])
        raise ValueError(f"R-HOOK-1: проверки правила {rule_id} не прошли: {failed}")

    record = store.record_rule_change(
        rule_id=rule_id,
        reason=reason,
        rules_hash=rules_hash(root),
        tests_run=report["total"],
        tests_passed=report["passed"],
        author=author,
    )
    return {**record, "tests": report["results"]}


# Точки контроля, которые реально существуют в коде (для отчёта о покрытии матрицы).
CONTROL_POINTS = {
    "rules.change": change,
    "rules.verify": verify,
}
