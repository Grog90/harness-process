"""Вердикт независимой проверки и пакет передачи на доработку.

Контракт вердикта — не текст «ок», а объект, который можно проверить программой:
он привязан к снимку кандидата, перечисляет критерии с доказательствами и несёт
явные находки. Косметика не может в одиночку дать FAIL; отсутствие проверки
обязательного критерия — это INCONCLUSIVE, а не FAIL (разные причины, разные
адресаты).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .brief import Brief, Issue

VERDICTS = ("PASS", "FAIL", "INCONCLUSIVE", "ERROR", "TIMEOUT", "CANCELLED")
RELEASE_ALLOWING = ("PASS",)
BLOCKING_SEVERITIES = ("blocker", "major")
SEVERITIES = ("blocker", "major", "minor", "cosmetic")
CRITERION_RESULTS = ("pass", "fail", "skip")


@dataclass
class VerdictCheck:
    data: dict[str, Any]
    issues: list[Issue] = field(default_factory=list)
    effective_verdict: str = ""

    @property
    def valid(self) -> bool:
        return not [i for i in self.issues if i.kind == "error"]

    @property
    def findings(self) -> list[dict[str, Any]]:
        return list(self.data.get("findings") or [])

    @property
    def criteria(self) -> list[dict[str, Any]]:
        return list(self.data.get("criteria") or [])

    def open_findings(self) -> list[dict[str, Any]]:
        out = []
        for f in self.findings:
            status = str(f.get("status", "open")).lower()
            if status in ("open", "confirmed", "assigned", "in_progress"):
                out.append(f)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "issues": [i.to_dict() for i in self.issues],
            "valid": self.valid,
            "effective_verdict": self.effective_verdict,
        }


REQUIRED_FIELDS = ("verdict_id", "task_id", "attempt", "snapshot_id", "verdict", "reviewer_id", "reviewer_role")


def validate(data: dict[str, Any], brief: Brief | None = None) -> VerdictCheck:
    issues: list[Issue] = []
    if not isinstance(data, dict):
        return VerdictCheck({}, [Issue("V_NOT_OBJECT", "$", "вердикт должен быть JSON-объектом")], "ERROR")

    for name in REQUIRED_FIELDS:
        if name not in data:
            issues.append(Issue("V_MISSING_FIELD", name, f"обязательное поле {name!r} отсутствует"))
        elif data[name] in (None, "", [], {}):
            issues.append(Issue("V_EMPTY_FIELD", name, f"обязательное поле {name!r} пустое"))

    verdict = str(data.get("verdict", "")).upper()
    if verdict and verdict not in VERDICTS:
        issues.append(Issue("V_BAD_VERDICT", "verdict", f"недопустимый вердикт {verdict!r}"))

    role = str(data.get("reviewer_role", "")).lower()
    if role and role not in ("verifier", "adversarial", "human"):
        issues.append(Issue("V_BAD_ROLE", "reviewer_role", f"недопустимая роль проверяющего {role!r}"))

    criteria = data.get("criteria") or []
    if not isinstance(criteria, list):
        issues.append(Issue("V_TYPE", "criteria", "criteria должен быть списком"))
        criteria = []

    seen_ids = set()
    for idx, c in enumerate(criteria):
        where = f"criteria[{idx}]"
        if not isinstance(c, dict):
            issues.append(Issue("V_TYPE", where, "критерий должен быть объектом"))
            continue
        cid = str(c.get("id", "")).strip()
        if not cid:
            issues.append(Issue("V_EMPTY_FIELD", where + ".id", "у критерия нет идентификатора"))
        elif cid in seen_ids:
            issues.append(Issue("V_DUPLICATE", where + ".id", f"критерий {cid!r} предъявлен дважды"))
        seen_ids.add(cid)
        result = str(c.get("result", "")).lower()
        if result and result not in CRITERION_RESULTS:
            issues.append(Issue("V_BAD_RESULT", where + ".result", f"недопустимый результат {result!r}"))
        if result == "pass" and not str(c.get("evidence", "")).strip():
            issues.append(
                Issue("V_NO_EVIDENCE", where + ".evidence", f"критерий {cid or idx} объявлен pass без доказательства")
            )

    findings = data.get("findings") or []
    if not isinstance(findings, list):
        issues.append(Issue("V_TYPE", "findings", "findings должен быть списком"))
        findings = []
    for idx, f in enumerate(findings):
        where = f"findings[{idx}]"
        if not isinstance(f, dict):
            issues.append(Issue("V_TYPE", where, "находка должна быть объектом"))
            continue
        sev = str(f.get("severity", "")).lower()
        if sev and sev not in SEVERITIES:
            issues.append(Issue("V_BAD_SEVERITY", where + ".severity", f"недопустимая серьёзность {sev!r}"))
        if not str(f.get("statement", "")).strip():
            issues.append(Issue("V_EMPTY_FIELD", where + ".statement", "находка без формулировки"))
        if not str(f.get("finding_id", "")).strip():
            issues.append(Issue("V_EMPTY_FIELD", where + ".finding_id", "находка без идентификатора"))

    effective = _effective_verdict(verdict, role, criteria, findings, data, brief, issues)
    return VerdictCheck(data, issues, effective)


def _effective_verdict(
    verdict: str,
    role: str,
    criteria: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    data: dict[str, Any],
    brief: Brief | None,
    issues: list[Issue],
) -> str:
    """Понижает вердикт, если он не подтверждён предъявленными доказательствами.

    Понижение — не наказание проверяющего, а честная классификация:
    «не проверено» не должно выглядеть как «проверено и хорошо».
    """
    if verdict not in VERDICTS:
        return "ERROR"

    # FAIL не может опираться только на косметику: это не содержательный отказ.
    if verdict == "FAIL":
        substantive = [f for f in findings if str(f.get("severity", "")).lower() in ("blocker", "major", "minor")]
        if findings and not substantive:
            issues.append(
                Issue(
                    "V_COSMETIC_ONLY",
                    "findings",
                    "FAIL обоснован только косметикой: нужен содержательный дефект",
                    kind="warning",
                )
            )
            return "INCONCLUSIVE"
        return "FAIL"

    if verdict != "PASS":
        return verdict

    downgraded = False

    # 1. Обязательные критерии должны быть покрыты и пройдены.
    if brief is not None:
        covered = {str(c.get("id")): str(c.get("result", "")).lower() for c in criteria}
        missing = [c for c in brief.mandatory_criteria if str(c.get("id")) not in covered]
        not_passed = [
            str(c.get("id"))
            for c in brief.mandatory_criteria
            if str(c.get("id")) in covered and covered[str(c.get("id"))] != "pass"
        ]
        if missing:
            issues.append(
                Issue(
                    "V_INCOMPLETE",
                    "criteria",
                    "PASS без покрытия обязательных критериев: " + ", ".join(str(c.get("id")) for c in missing),
                    kind="warning",
                )
            )
            downgraded = True
        if not_passed:
            issues.append(
                Issue(
                    "V_CONTRADICTION",
                    "criteria",
                    "PASS при непройденных критериях: " + ", ".join(not_passed),
                    kind="warning",
                )
            )
            downgraded = True

    # 2. Открытая блокирующая находка несовместима с PASS.
    blocking = [
        f
        for f in findings
        if str(f.get("severity", "")).lower() in BLOCKING_SEVERITIES
        and str(f.get("status", "open")).lower() in ("open", "confirmed", "assigned", "in_progress")
    ]
    if blocking:
        issues.append(
            Issue(
                "V_CONTRADICTION",
                "findings",
                "PASS при открытых находках уровня blocker/major: "
                + ", ".join(str(f.get("finding_id")) for f in blocking),
                kind="warning",
            )
        )
        downgraded = True

    # 3. Адверсальный PASS обязан предъявить выполненный сценарий.
    if role == "adversarial":
        scenario = data.get("scenario") or {}
        mode = str(scenario.get("mode", "")).lower()
        observed = str(scenario.get("observed_result", "")).strip()
        if not scenario or mode == "not_verified" or not observed:
            issues.append(
                Issue(
                    "V_ADV_NO_SCENARIO",
                    "scenario",
                    "адверсальный PASS без выполненного сценария запрещён: сценарий описан ≠ сценарий выполнен",
                    kind="warning",
                )
            )
            downgraded = True

    return "INCONCLUSIVE" if downgraded else "PASS"


def feedback_packet(verdict: dict[str, Any], brief: Brief | None, remaining_budget: int) -> dict[str, Any]:
    """Пакет передачи на доработку. Оркестратор передаёт идентификатор, а не пересказ."""
    open_ids = [
        str(f.get("finding_id"))
        for f in verdict.get("findings") or []
        if str(f.get("status", "open")).lower() in ("open", "confirmed", "assigned", "in_progress")
    ]
    return {
        "packet_type": "rework",
        "verdict_id": verdict.get("verdict_id"),
        "task_id": verdict.get("task_id"),
        "attempt": verdict.get("attempt"),
        "snapshot_id": verdict.get("snapshot_id"),
        "open_finding_ids": open_ids,
        "brief_hash": brief.hash() if brief else None,
        "allowed_files": brief.allowed_files if brief else [],
        "remaining_budget": remaining_budget,
        "recheck": "повторная проверка тем же контрактом вердикта на новом снимке",
    }


def load(path: str, brief: Brief | None = None) -> VerdictCheck:
    with open(path, encoding="utf-8") as fh:
        return validate(json.load(fh), brief=brief)


# Точки контроля, которые реально существуют в коде (для отчёта о покрытии матрицы).
CONTROL_POINTS = {
    "verdict.checks_contract": validate,
    "verdict.adversarial_requires_scenario": _effective_verdict,
}
