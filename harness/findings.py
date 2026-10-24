"""Реестр находок: маршрутизация и полнота обратной связи.

Два трека и «третьего ящика» нет: дефект в своём диффе правится немедленно,
дефект в чужом/старом коде уходит владельцу пакетом. Находка без владельца или
без решения — потерянный сигнал, поэтому полнота проверяется отдельно.
"""

from __future__ import annotations

from typing import Any, Iterable

TRACKS = ("own_diff", "foreign_code")
STATUSES = ("new", "confirmed", "assigned", "in_progress", "fixed", "closed", "accepted_risk", "disputed")
OPEN_STATUSES = ("new", "confirmed", "assigned", "in_progress", "disputed")
IMPACTS = ("blocks_release", "degrades_release", "cosmetic")


def route(finding: dict[str, Any], *, worker_ids: Iterable[str] = ()) -> dict[str, Any]:
    """Определяет трек: свой дифф или чужой код.

    Признак «свой» — находка относится к изменяемым в этой задаче файлам и
    владелец входит в состав исполнителей задачи.
    """
    owner = str(finding.get("owner") or "").strip()
    in_scope = bool(finding.get("in_scope", False))
    if in_scope and (not owner or owner in set(worker_ids)):
        track = "own_diff"
        action = "исправить в этой же задаче, не эскалируя"
    else:
        track = "foreign_code"
        action = "включить в пакет эскалации владельцу; не терять"
    return {**finding, "track": track, "routing_action": action}


def completeness(findings: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Проверка полноты: у каждой находки есть решение, владелец и трек."""
    items = list(findings)
    unowned = [f.get("finding_id") for f in items if not str(f.get("owner") or "").strip()]
    unrouted = [f.get("finding_id") for f in items if str(f.get("track") or "") not in TRACKS]
    undecided = [
        f.get("finding_id")
        for f in items
        if str(f.get("status") or "new") in OPEN_STATUSES and not str(f.get("resolution") or "").strip()
        and str(f.get("status")) != "disputed"
    ]
    invisible = [
        f.get("finding_id")
        for f in items
        if str(f.get("impact") or "") not in IMPACTS
    ]
    return {
        "total": len(items),
        "unowned": unowned,
        "unrouted": unrouted,
        "undecided": undecided,
        "no_impact_declared": invisible,
        "ok": not (unowned or unrouted or undecided),
    }


def dispute(finding: dict[str, Any], counterexample: str, arbiter: str | None = None) -> dict[str, Any]:
    """Спорная находка: несогласие автора не отменяет FAIL, нужен арбитр."""
    if not counterexample.strip():
        raise ValueError("спор требует предъявленного контрпримера")
    return {
        **finding,
        "status": "disputed",
        "counterexample": counterexample,
        "arbiter": arbiter,
        "resolution": "" if not arbiter else f"арбитраж назначен: {arbiter}",
    }


# Точки контроля, которые реально существуют в коде (для отчёта о покрытии матрицы).
from .state import Store  # noqa: E402  (импорт внизу: локальная зависимость от хранилища)

CONTROL_POINTS = {
    "findings.route": route,
    "findings.close_guard": Store.close_finding,
}
