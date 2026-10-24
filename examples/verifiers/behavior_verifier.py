#!/usr/bin/env python3
"""Учебный верификатор поведения: импортирует кандидата и проверяет результат.

В отличие от маркерного, этот проверяет наблюдаемое поведение, а не текст.
Для реального проекта такой запуск должен идти в изолированной среде с
ограниченными правами — здесь учебный модуль без побочных эффектов.

Протокол: JSON на stdin, вердикт JSON на stdout.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys


def load_module(path: str):
    spec = importlib.util.spec_from_file_location("candidate_fee", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"не удалось загрузить {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # noqa: S102 - учебный кандидат
    return module


def main() -> int:
    req = json.load(sys.stdin)
    candidate = req["candidate_dir"]
    snapshot = req.get("snapshot_id", "")
    case_id = req.get("case_id", "case")
    task_id = req.get("task_id") or case_id
    attempt = int(req.get("attempt", 1))
    base = candidate if os.path.isdir(os.path.join(candidate, "src")) is False else os.path.join(candidate, "src")
    target = os.path.join(base, "fee.py")

    criteria = []
    findings = []

    def add(cid: str, ok: bool, evidence: str) -> None:
        criteria.append({"id": cid, "result": "pass" if ok else "fail", "evidence": evidence,
                         "command": "python -c calculate_fee"})
        if not ok:
            findings.append({
                "finding_id": f"F-{cid}",
                "severity": "major",
                "statement": f"поведение не соответствует критерию {cid}: {evidence}",
                "evidence": evidence,
                "owner": "worker",
                "track": "own_diff",
            })

    # AC-1: 2.5% от суммы, результат целочисленный.
    # Два входа, а не один: 20000 даёт ровно 500 при любом округлении,
    # 12345 даёт 308.625 — только здесь видно разницу между round и floor.
    try:
        mod = load_module(target)
        fee = getattr(mod, "calculate_fee")
        got_a = fee(20000)
        got_b = fee(12345)
        ok = got_a == 500 and got_b == 309 and isinstance(got_a, int)
        add("AC-1", ok, f"calculate_fee(20000) -> {got_a!r}, calculate_fee(12345) -> {got_b!r} (ожидалось 500 и 309)")
    except Exception as exc:  # noqa: BLE001
        add("AC-1", False, f"вызов calculate_fee упал: {type(exc).__name__}: {exc}")

    # AC-2: отрицательная сумма отвергается кодом FEE_NEGATIVE
    try:
        mod = load_module(target)
        fee = getattr(mod, "calculate_fee")
        try:
            got = fee(-100)
        except ValueError as exc:
            add("AC-2", "FEE_NEGATIVE" in str(exc), f"ValueError: {exc}")
        except Exception as exc:  # noqa: BLE001
            add("AC-2", False, f"неверный тип ошибки: {type(exc).__name__}: {exc}")
        else:
            add("AC-2", False, f"отрицательная сумма принята, вернулось {got!r}")
    except Exception as exc:  # noqa: BLE001
        add("AC-2", False, f"модуль не загрузился: {type(exc).__name__}: {exc}")

    kind = "PASS" if all(c["result"] == "pass" for c in criteria) else "FAIL"
    verdict = {
        "verdict_id": f"v-{case_id}",
        "task_id": task_id,
        "attempt": attempt,
        "snapshot_id": snapshot,
        "verdict": kind,
        "reviewer_id": "behavior-verifier",
        "reviewer_role": "verifier",
        "policy_version": "1.0.0",
        "criteria": criteria,
        "findings": findings,
    }
    print(json.dumps(verdict, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
