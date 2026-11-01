#!/usr/bin/env python3
"""Верификатор доступа: проверяет матрицу ролей, а не текст и не имена.

Кейс «деньги» проверяет арифметику, этот — решение о доступе. Разные предметы
ловят разные дефекты: там округление, здесь отказ в сторону разрешения.

Протокол: JSON на stdin, вердикт JSON на stdout.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys


def load_module(path: str):
    spec = importlib.util.spec_from_file_location("candidate_access", path)
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
    target = os.path.join(base, "access.py")

    criteria = []
    findings = []

    def add(cid: str, ok: bool, evidence: str) -> None:
        criteria.append({"id": cid, "result": "pass" if ok else "fail", "evidence": evidence,
                         "command": "python -c check_access"})
        if not ok:
            findings.append({
                "finding_id": f"F-{cid}",
                "severity": "major",
                "statement": f"решение о доступе расходится с критерием {cid}",
                "evidence": evidence,
                "owner": "worker",
                "track": "own_diff",
            })

    def call(role: str, level: str):
        module = load_module(target)
        return module.check_access(role, level)

    # AC-1: администратор видит закрытый ресурс
    try:
        got = call("admin", "private")
        add("AC-1", got is True, f"check_access('admin','private') -> {got!r} (ожидалось True)")
    except Exception as exc:  # noqa: BLE001
        add("AC-1", False, f"вызов упал: {type(exc).__name__}: {exc}")

    # AC-2: зритель не видит закрытый ресурс — отказ, а не исключение
    try:
        got = call("viewer", "private")
        add("AC-2", got is False, f"check_access('viewer','private') -> {got!r} (ожидалось False)")
    except Exception as exc:  # noqa: BLE001
        add("AC-2", False, f"вместо отказа исключение: {type(exc).__name__}: {exc}")

    # AC-3: неизвестная роль — явная ошибка с кодом, а не тихий False
    try:
        got = call("guest", "public")
    except ValueError as exc:
        add("AC-3", "ACCESS_UNKNOWN_ROLE" in str(exc), f"ValueError: {exc}")
    except Exception as exc:  # noqa: BLE001
        add("AC-3", False, f"неверный тип ошибки: {type(exc).__name__}: {exc}")
    else:
        add("AC-3", False, f"неизвестная роль принята, вернулось {got!r}")

    # AC-4: зритель видит публичный ресурс — иначе проверка «всё запрещено» пройдёт AC-2
    try:
        got = call("viewer", "public")
        add("AC-4", got is True, f"check_access('viewer','public') -> {got!r} (ожидалось True)")
    except Exception as exc:  # noqa: BLE001
        add("AC-4", False, f"вызов упал: {type(exc).__name__}: {exc}")

    kind = "PASS" if all(c["result"] == "pass" for c in criteria) else "FAIL"
    verdict = {
        "verdict_id": f"v-{case_id}",
        "task_id": task_id,
        "attempt": attempt,
        "snapshot_id": snapshot,
        "verdict": kind,
        "reviewer_id": "access-verifier",
        "reviewer_role": "verifier",
        "policy_version": "1.0.0",
        "criteria": criteria,
        "findings": findings,
    }
    print(json.dumps(verdict, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
