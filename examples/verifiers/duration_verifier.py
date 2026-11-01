#!/usr/bin/env python3
"""Верификатор разбора длительности: третий предмет, отличный от денег и доступов.

Здесь ловятся дефекты разбора: молча проигнорированная единица, пустая строка как
ноль, потерянный разряд, подмена типа. Это не арифметика и не матрица доступа —
своя цена ошибки и свои слепые зоны.

Протокол: JSON на stdin, вердикт JSON на stdout.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys


def load_module(path: str):
    spec = importlib.util.spec_from_file_location("candidate_duration", path)
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
    target = os.path.join(base, "duration.py")

    criteria = []
    findings = []

    def add(cid: str, ok: bool, evidence: str) -> None:
        criteria.append({"id": cid, "result": "pass" if ok else "fail", "evidence": evidence,
                         "command": "python -c parse_duration"})
        if not ok:
            findings.append({
                "finding_id": f"F-{cid}",
                "severity": "major",
                "statement": f"разбор длительности расходится с критерием {cid}",
                "evidence": evidence,
                "owner": "worker",
                "track": "own_diff",
            })

    def call(text: str):
        return load_module(target).parse_duration(text)

    def expect_error(cid: str, text: str) -> None:
        try:
            got = call(text)
        except ValueError as exc:
            add(cid, "DURATION_INVALID" in str(exc), f"ValueError: {exc}")
        except Exception as exc:  # noqa: BLE001
            add(cid, False, f"неверный тип ошибки: {type(exc).__name__}: {exc}")
        else:
            add(cid, False, f"parse_duration({text!r}) принят, вернулось {got!r}")

    try:
        got = call("1h30m")
        add("AC-1", got == 5400 and isinstance(got, int),
            f"parse_duration('1h30m') -> {got!r} (ожидалось 5400, int)")
    except Exception as exc:  # noqa: BLE001
        add("AC-1", False, f"вызов упал: {type(exc).__name__}: {exc}")

    try:
        got = call("45m")
        add("AC-2", got == 2700, f"parse_duration('45m') -> {got!r} (ожидалось 2700)")
    except Exception as exc:  # noqa: BLE001
        add("AC-2", False, f"вызов упал: {type(exc).__name__}: {exc}")

    expect_error("AC-3", "10x")
    expect_error("AC-4", "")
    expect_error("AC-5", "-5m")

    kind = "PASS" if all(c["result"] == "pass" for c in criteria) else "FAIL"
    verdict = {
        "verdict_id": f"v-{case_id}",
        "task_id": task_id,
        "attempt": attempt,
        "snapshot_id": snapshot,
        "verdict": kind,
        "reviewer_id": "duration-verifier",
        "reviewer_role": "verifier",
        "policy_version": "1.0.0",
        "criteria": criteria,
        "findings": findings,
    }
    print(json.dumps(verdict, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
