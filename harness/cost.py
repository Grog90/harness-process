"""Учёт и ограничение стоимости задачи.

Бюджет, который считается после факта, — это отчёт, а не ограничение. Поэтому гейт
стоит перед диспетчем: если остаток меньше оценки следующей попытки, запуск
отклоняется (fail-closed). Оценка берётся из брифа, фактический расход — из
вердикта или из явной записи.

Единица по умолчанию — условные единицы (`cost_units`): конкретная валюта и
тарифы зависят от провайдера, и подменять их догадкой нельзя.
"""

from __future__ import annotations

from typing import Any

from .state import Store

DEFAULT_UNIT = "cost_units"


def budget_of(brief_data: dict[str, Any] | None) -> dict[str, float]:
    """Бюджет из брифа: максимум и оценка одной попытки."""
    control = (brief_data or {}).get("control") or {}
    raw = control.get("budget") or {}
    out: dict[str, float] = {}
    for key in ("max_cost", "estimate_per_attempt", "max_minutes"):
        if raw.get(key) is not None:
            try:
                out[key] = float(raw[key])
            except (TypeError, ValueError):
                continue
    out.setdefault("unit", str(raw.get("unit") or DEFAULT_UNIT))  # type: ignore[arg-type]
    return out  # type: ignore[return-value]


def spent(store: Store, task_id: str, unit: str = DEFAULT_UNIT) -> float:
    return store.cost_total(task_id, unit)


def check_dispatch(store: Store, task_id: str, brief_data: dict[str, Any] | None) -> dict[str, Any]:
    """Проверка бюджета перед запуском. Возвращает вердикт и цифры."""
    budget = budget_of(brief_data)
    unit = str(budget.get("unit", DEFAULT_UNIT))
    used = spent(store, task_id, unit)
    max_cost = budget.get("max_cost")
    estimate = budget.get("estimate_per_attempt", 0.0)

    result = {
        "unit": unit,
        "spent": round(used, 4),
        "max_cost": max_cost,
        "estimate_per_attempt": estimate,
        "remaining": None if max_cost is None else round(max_cost - used, 4),
        "allowed": True,
        "reason": "",
    }
    if max_cost is None:
        result["note"] = "бюджет не задан в брифе: ограничение не действует"
        return result
    if used > max_cost:
        result.update(allowed=False, reason=f"израсходовано {used:g} при лимите {max_cost:g} {unit}")
        return result
    if used + float(estimate) > max_cost:
        result.update(
            allowed=False,
            reason=(
                f"остаток {max_cost - used:g} {unit} меньше оценки следующей попытки "
                f"{float(estimate):g}: запуск остановлен до решения человека"
            ),
        )
        return result
    return result


def record(store: Store, task_id: str, amount: float, *, unit: str = DEFAULT_UNIT,
           kind: str = "model", attempt: int | None = None, note: str = "") -> dict[str, Any]:
    return store.add_cost(task_id=task_id, amount=amount, unit=unit, kind=kind, attempt=attempt, note=note)


def report(store: Store, unit: str = DEFAULT_UNIT) -> dict[str, Any]:
    rows = store.cost_report(unit)
    total = round(sum(float(r["total"]) for r in rows), 4)
    accepted = [r for r in rows if float(r["total"]) > 0]
    return {
        "unit": unit,
        "tasks": rows,
        "total": total,
        "tasks_with_cost": len(accepted),
        "average_per_task": round(total / len(accepted), 4) if accepted else 0.0,
    }


# Точки контроля, которые реально существуют в коде (для отчёта о покрытии матрицы).
CONTROL_POINTS = {
    "cost.check_dispatch": check_dispatch,
}
