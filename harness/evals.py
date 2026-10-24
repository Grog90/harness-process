"""Evals: измерение самого контроля, а не только его наличия.

Считаем то, что рынок считает правильным считать (и чего в книге не хватало):
долю пропущенных дефектов, долю ложных отказов и долю неполных проверок.
Пропуск и «не смог проверить» — разные строки: инфраструктурная ошибка не должна
выглядеть как корректный отказ.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from typing import Any

from . import verdict as verdict_mod
from .brief import validate as validate_brief
from .snapshot import compute_snapshot

RELEASE_VERDICTS = ("PASS",)
INCOMPLETE_VERDICTS = ("INCONCLUSIVE", "ERROR", "TIMEOUT", "CANCELLED")


def load_cases(cases_dir: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for name in sorted(os.listdir(cases_dir)):
        case_dir = os.path.join(cases_dir, name)
        manifest = os.path.join(case_dir, "case.json")
        if not os.path.isfile(manifest):
            continue
        with open(manifest, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("case_id", name)
        data["_dir"] = case_dir
        cases.append(data)
    return cases


def run_verifier(cmd: str, payload: dict[str, Any], cwd: str) -> tuple[dict[str, Any] | None, str]:
    """Запускает верификатор как внешний процесс. Возврат: (вердикт, ошибка)."""
    argv = shlex.split(cmd, posix=(os.name != "nt"))
    # на Windows posix=False сохраняет кавычки в токенах — снимаем их явно
    argv = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] == '"' else t for t in argv]
    try:
        proc = subprocess.run(
            argv,
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError as exc:
        return None, f"не удалось запустить верификатор: {exc}"
    if proc.returncode != 0:
        return None, f"верификатор вернул код {proc.returncode}: {proc.stderr.strip()[:300]}"
    try:
        return json.loads(proc.stdout), ""
    except json.JSONDecodeError as exc:
        return None, f"ответ верификатора не JSON: {exc}"


def run(cases_dir: str, verifier_cmd: str, root: str = ".") -> dict[str, Any]:
    cases = load_cases(cases_dir)
    rows: list[dict[str, Any]] = []

    for case in cases:
        case_dir = case["_dir"]
        candidate = os.path.join(case_dir, case.get("candidate_dir", "candidate"))
        brief_raw = case.get("brief")
        if brief_raw is None and case.get("brief_file"):
            with open(os.path.join(case_dir, case["brief_file"]), encoding="utf-8") as fh:
                brief_raw = json.load(fh)
        brief = validate_brief(brief_raw or {}, max_attempts=3) if brief_raw else None
        snapshot = compute_snapshot(candidate) if os.path.isdir(candidate) else ""

        payload = {
            "case_id": case["case_id"],
            "task_id": case["case_id"],
            "attempt": 1,
            "candidate_dir": os.path.abspath(candidate),
            "brief": brief_raw,
            "snapshot_id": snapshot,
        }
        raw, err = run_verifier(verifier_cmd, payload, cwd=root)

        if raw is None:
            predicted = "ERROR"
            check_issues: list[dict[str, Any]] = [{"code": "EV_RUN", "message": err}]
        else:
            raw.setdefault("task_id", case["case_id"])
            raw.setdefault("attempt", 1)
            raw.setdefault("snapshot_id", snapshot)
            raw.setdefault("verdict_id", f"v_{case['case_id']}")
            raw.setdefault("reviewer_id", "eval-verifier")
            raw.setdefault("reviewer_role", "verifier")
            check = verdict_mod.validate(raw, brief=brief)
            predicted = check.effective_verdict
            check_issues = [i.to_dict() for i in check.issues]

        expected = str(case.get("expected", "PASS")).upper()
        rows.append(
            {
                "case_id": case["case_id"],
                "expected": expected,
                "predicted": predicted,
                "agreement": predicted == expected,
                "snapshot_id": snapshot,
                "issues": check_issues,
                "note": case.get("note", ""),
            }
        )

    return {"metrics": metrics(rows), "rows": rows}


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    defective = [r for r in rows if r["expected"] == "FAIL"]
    correct = [r for r in rows if r["expected"] == "PASS"]
    missed = [r for r in defective if r["predicted"] in RELEASE_VERDICTS]
    false_fail = [r for r in correct if r["predicted"] == "FAIL"]
    incomplete = [r for r in rows if r["predicted"] in INCOMPLETE_VERDICTS]

    def ratio(num: int, den: int) -> float | None:
        return round(num / den, 3) if den else None

    caveats = []
    if len(defective) < 5:
        caveats.append("дефектных случаев меньше 5: метрика пропуска грубая")
    if len(correct) < 5:
        caveats.append("корректных случаев меньше 5: метрика ложного отказа грубая")
    if len({r["note"] for r in rows if r["note"]}) <= 1 and len(rows) > 1:
        caveats.append("случаи однородны: цифры описывают один класс задач, а не систему")

    return {
        "total": len(rows),
        "defective": len(defective),
        "correct": len(correct),
        "missed_defects": len(missed),
        "miss_rate": ratio(len(missed), len(defective)),
        "false_fails": len(false_fail),
        "false_fail_rate": ratio(len(false_fail), len(correct)),
        "incomplete": len(incomplete),
        "incomplete_rate": ratio(len(incomplete), len(rows)),
        "agreement_rate": ratio(sum(1 for r in rows if r["agreement"]), len(rows)),
        "missed_ids": [r["case_id"] for r in missed],
        "false_fail_ids": [r["case_id"] for r in false_fail],
        "caveats": caveats,
    }


def render_text(report: dict[str, Any]) -> str:
    m = report["metrics"]
    lines = [
        "Evals верификатора",
        "------------------",
        f"случаев: {m['total']} (дефектных {m['defective']}, корректных {m['correct']})",
        f"пропуск дефекта:      {m['missed_defects']}/{m['defective']}  = {m['miss_rate']}",
        f"ложный отказ:         {m['false_fails']}/{m['correct']}  = {m['false_fail_rate']}",
        f"неполная проверка:    {m['incomplete']}/{m['total']}  = {m['incomplete_rate']}",
        f"согласие с разметкой: {m['agreement_rate']}",
    ]
    if m["missed_ids"]:
        lines.append(f"пропущены: {', '.join(m['missed_ids'])}")
    if m["false_fail_ids"]:
        lines.append(f"ложные отказы: {', '.join(m['false_fail_ids'])}")
    for caveat in m["caveats"]:
        lines.append(f"! {caveat}")
    lines.append("")
    lines.append(f"{'case':<24}{'ожидалось':<12}{'получено':<14}{'совпало'}")
    for r in report["rows"]:
        lines.append(f"{r['case_id']:<24}{r['expected']:<12}{r['predicted']:<14}{'да' if r['agreement'] else 'НЕТ'}")
    return "\n".join(lines)


# Точки контроля, которые реально существуют в коде (для отчёта о покрытии матрицы).
CONTROL_POINTS = {
    "evals.metrics": metrics,
}
