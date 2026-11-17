"""SLO процесса: пороги по метрикам, а не «на глаз».

Пороги — явные константы; `evaluate` возвращает строки с фактическим значением,
целью и вердиктом. Пустая выборка не считается успехом: строка помечается n/a.
"""

from __future__ import annotations

from typing import Any

DEFAULT_TARGETS: dict[str, dict[str, Any]] = {
    "acceptance_rate": {"op": ">=", "value": 0.5, "label": "доля принятых change"},
    "rework_rate": {"op": "<=", "value": 0.5, "label": "доля переделок"},
    "verification_pass": {"op": ">=", "value": 0.9, "label": "доля успешных verification"},
    "open_findings": {"op": "<=", "value": 10, "label": "открытые находки"},
}


def _value(data: dict[str, Any], key: str) -> float | None:
    summary = data.get("summary") or {}
    attempts = data.get("attempts") or {}
    verification = data.get("verification") or {}
    if key == "acceptance_rate":
        total = summary.get("changes") or 0
        return (summary.get("changes_accepted") or 0) / total if total else None
    if key == "rework_rate":
        return attempts.get("rework_rate")
    if key == "verification_pass":
        reports = verification.get("reports") or 0
        return (verification.get("ok") or 0) / reports if reports else None
    if key == "open_findings":
        return summary.get("open_findings")
    return None


def evaluate(data: dict[str, Any], targets: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    targets = targets or DEFAULT_TARGETS
    rows: list[dict[str, Any]] = []
    for key, target in targets.items():
        value = _value(data, key)
        if value is None:
            rows.append({"name": key, "label": target["label"], "value": None,
                         "target": target["value"], "op": target["op"], "ok": None})
            continue
        op = target["op"]
        ok = value >= target["value"] if op == ">=" else value <= target["value"]
        rows.append({"name": key, "label": target["label"], "value": value,
                     "target": target["value"], "op": op, "ok": ok})
    breached = [r for r in rows if r["ok"] is False]
    return {"ok": not breached, "rows": rows, "breached": [r["name"] for r in breached],
            "na": [r["name"] for r in rows if r["ok"] is None],
            "confirmed": any(r["ok"] is not None for r in rows)}


def render_text(report: dict[str, Any]) -> str:
    lines = []
    for row in report["rows"]:
        value = "n/a" if row["value"] is None else f"{row['value']:.3f}"
        mark = "ok" if row["ok"] else ("n/a" if row["ok"] is None else "BREACH")
        lines.append(f"[{mark:6}] {row['name']} = {value} {row['op']} {row['target']}")
    return "\n".join(lines)


# Публичного контрольного хука нет: SLO — отчёт, а не гейт в rules.json.
