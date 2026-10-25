"""Матрица RULE -> HOOK -> ENFORCEMENT и отчёт о покрытии.

Матрица — живой документ. Правило без реализации не исчезает и не выглядит
нормальным исключением: оно помечается как дыра с причиной
(«неприменимо» и «не реализовано» — разные состояния).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from . import hooks


def control_index() -> dict[str, Any]:
    """Все точки контроля, реально существующие в коде.

    Объединяет хуки инструментов и охранные условия автомата: отчёт о покрытии
    должен видеть и то, и другое, иначе половина матрицы выглядит дырой.
    """
    from . import (brief, checks, cost, evals, finalize, findings, fsm, gateway, lanes,
                   rulechange, verdict, watchdog)

    index: dict[str, Any] = dict(hooks.HOOKS)
    for module in (brief, checks, cost, evals, finalize, findings, fsm, gateway, lanes,
                   rulechange, verdict, watchdog):
        for name, fn in getattr(module, "CONTROL_POINTS", {}).items():
            if fn is not None:
                index[name] = fn
    return index

GAP_NOT_IMPLEMENTED = "not_implemented"
GAP_EXTERNAL = "external_dependency"
GAP_UNOWNED = "unowned"


@dataclass
class RuleStatus:
    id: str
    statement: str
    event: str
    enforcement: str
    implemented: bool
    hook: str | None
    status: str  # covered | delegated | gap | broken
    reason: str = ""
    owner: str = ""
    residual_risk: str = ""
    delegated_to: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "event": self.event,
            "enforcement": self.enforcement,
            "implemented": self.implemented,
            "hook": self.hook,
            "status": self.status,
            "reason": self.reason,
            "owner": self.owner,
            "residual_risk": self.residual_risk,
            "delegated_to": self.delegated_to,
        }


def load_registry(root: str = ".", path: str | None = None) -> dict[str, Any]:
    p = path or os.path.join(root, "policies", "rules.json")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def external_targets(delegated_to: str) -> list[str]:
    """Имена пакетов, которым делегировано правило (без встроенного рантайма)."""
    tokens = re.findall(r"@?[A-Za-z0-9][\w.-]*/[\w.-]+|dsh-[\w.-]+", delegated_to or "")
    out: list[str] = []
    for token in tokens:
        token = token.strip(".,;:()")
        if token.startswith("@deepseek-ai/"):
            continue  # поставляется рантаймом, отдельной установки не требует
        if token not in out:
            out.append(token)
    return out


def evaluate(registry: dict[str, Any], available_hooks: dict[str, Any] | None = None,
             installed: set[str] | None = None) -> list[RuleStatus]:
    available = available_hooks if available_hooks is not None else control_index()
    out: list[RuleStatus] = []
    for r in registry.get("rules", []):
        enforcement = str(r.get("enforcement", ""))
        hook = r.get("hook")
        implemented = bool(r.get("implemented", False))
        owner = str(r.get("owner", ""))

        status, reason = "covered", ""
        delegated_to = str(r.get("delegated_to") or "")
        if enforcement == "delegated":
            # Правило закрыто механизмом рантайма или плагином, а не нашим хуком.
            # Но «делегировано» — это обещание, которое надо проверять: если
            # сторонний плагин удалён, правило снова становится пробелом.
            missing = [t for t in external_targets(delegated_to)
                       if installed is not None and t not in installed]
            if missing:
                status = "gap"
                reason = f"делегировано, но не установлено: {', '.join(missing)}"
            else:
                status = "delegated"
                reason = f"закрыто: {delegated_to or 'механизм рантайма'}"
        elif enforcement == "n/a":
            kind = r.get("gap_kind") or GAP_NOT_IMPLEMENTED
            status = "gap"
            reason = {
                GAP_NOT_IMPLEMENTED: "детерминированной проверки нет — держится только на инструкции",
                GAP_EXTERNAL: "требуется внешняя зависимость (изоляция среды/платформы)",
                GAP_UNOWNED: "нет владельца правила",
            }.get(kind, "пробел покрытия")
        elif implemented and hook and hook not in available:
            status = "broken"
            reason = f"правило заявлено, но хук {hook!r} отсутствует"
        elif implemented and not hook:
            status = "broken"
            reason = "правило заявлено реализованным, но хук не указан"
        elif not implemented:
            status = "gap"
            reason = "правило объявлено, реализация отсутствует"

        out.append(
            RuleStatus(
                id=str(r.get("id")),
                statement=str(r.get("statement", "")),
                event=str(r.get("event", "")),
                enforcement=enforcement,
                implemented=implemented,
                hook=hook,
                status=status,
                reason=reason,
                owner=owner,
                residual_risk=str(r.get("residual_risk", "")),
                delegated_to=delegated_to,
            )
        )
    return out


def summary(statuses: list[RuleStatus], tested: set[str] | None = None) -> dict[str, Any]:
    total = len(statuses)
    covered = sum(1 for s in statuses if s.status == "covered")
    gap = sum(1 for s in statuses if s.status == "gap")
    broken = sum(1 for s in statuses if s.status == "broken")
    delegated = sum(1 for s in statuses if s.status == "delegated")
    blocking = sum(1 for s in statuses if s.enforcement == "blocking" and s.status == "covered")
    soft = sum(1 for s in statuses if s.enforcement == "soft" and s.status == "covered")
    tested_ids = tested or set()
    # делегированные правила проверяются механизмом рантайма — их отсутствие
    # среди наших тестов не пробел покрытия
    untested = [s.id for s in statuses if s.status != "delegated" and s.id not in tested_ids]
    owned = total - delegated
    return {
        "total": total,
        "covered": covered,
        "gap": gap,
        "broken": broken,
        "delegated": delegated,
        "owned": owned,
        "blocking_covered": blocking,
        "soft_covered": soft,
        "coverage_ratio": round(covered / total, 3) if total else 0.0,
        "tested": owned - len(untested),
        "untested": untested,
        "honest_note": "доля покрытия не равна доле безопасности: одна незакрытая критическая операция важнее десятков закрытых малозначимых правил",
    }


def render_markdown(statuses: list[RuleStatus], sums: dict[str, Any]) -> str:
    lines = [
        "# Матрица RULE → HOOK → ENFORCEMENT",
        "",
        "_Файл генерируется командой `harness coverage --markdown`. Руками не править: источник — `policies/rules.json` и код хуков._",
        "",
        f"Всего правил: **{sums['total']}** · покрыто: **{sums['covered']}** · "
        f"пробелов: **{sums['gap']}** · делегировано рантайму: **{sums['delegated']}** · "
        f"сломанных объявлений: **{sums['broken']}**",
        "",
        f"Проверяется исполняемыми тестами: **{sums.get('tested', 0)}** из **{sums.get('owned', sums['total'])}** "
        f"наших правил; ещё **{sums['delegated']}** закрыты механизмами рантайма и плагинами (см. ниже)."
        + (
            " Без тестов остаются: " + ", ".join(f"`{x}`" for x in sums.get("untested", []))
            if sums.get("untested")
            else " У каждого нашего правила есть хотя бы одна исполняемая проверка."
        ),
        "",
        f"> {sums['honest_note']}",
        "",
        "| Правило | Класс | Событие | Хук | Статус | Владелец |",
        "|---|---|---|---|---|---|",
    ]
    for s in statuses:
        mark = {"covered": "покрыто", "delegated": "делегировано", "gap": "ПРОБЕЛ",
                "broken": "СЛОМАНО"}[s.status]
        note = f" — {s.reason}" if s.reason else ""
        lines.append(
            f"| `{s.id}` | {s.enforcement} | {s.event} | `{s.hook or '—'}` | {mark}{note} | {s.owner or '—'} |"
        )
    lines += ["", "## Пробелы покрытия", ""]
    gaps = [s for s in statuses if s.status in ("gap", "broken")]
    if not gaps:
        lines.append("Пробелов нет (это утверждение стоит перепроверять, а не праздновать).")
    for s in gaps:
        lines.append(f"- **{s.id}** ({s.enforcement}): {s.statement} — {s.reason}. Остаточный риск: {s.residual_risk}")
    delegated = [s for s in statuses if s.status == "delegated"]
    lines += ["", "## Делегировано рантайму и плагинам", ""]
    if not delegated:
        lines.append("Делегированных правил нет.")
    for s in delegated:
        lines.append(f"- **{s.id}**: {s.statement} → `{s.delegated_to}`. Остаточный риск: {s.residual_risk}")
    lines += ["", "## Остаточные риски покрытых правил", ""]
    for s in statuses:
        if s.status == "covered" and s.residual_risk:
            lines.append(f"- **{s.id}**: {s.residual_risk}")
    return "\n".join(lines) + "\n"
