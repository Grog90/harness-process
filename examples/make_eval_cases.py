#!/usr/bin/env python3
"""Генератор учебного набора evals.

Набор нужен не для красоты отчёта, а чтобы у верификатора были размеченные
корректные и дефектные кандидаты: без них «проверка» измеряется только на словах.
"""

from __future__ import annotations

import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "evals", "fee")
BRIEF = os.path.join(HERE, "evals", "fee-brief.json")

SOURCE = {
    "good_1": '''"""Комиссия 2.5%."""

RATE = 0.025


def calculate_fee(amount_minor: int) -> int:
    if amount_minor < 0:
        raise ValueError("FEE_NEGATIVE")
    return int(round(amount_minor * RATE))
''',
    "good_2": '''def calculate_fee(amount_minor: int) -> int:
    """2.5% от суммы в минорных единицах."""
    if amount_minor < 0:
        raise ValueError("FEE_NEGATIVE")
    return int(amount_minor * 0.025 + 0.5)
''',
    "good_3": '''"""Расчёт комиссии (вариант с явной константой)."""

PERCENT = 0.025


def calculate_fee(amount_minor: int) -> int:
    if amount_minor < 0:
        raise ValueError("FEE_NEGATIVE: сумма не может быть отрицательной")
    fee = amount_minor * PERCENT
    return int(round(fee))
''',
    "good_4": '''def calculate_fee(amount_minor: int) -> int:
    if amount_minor < 0:
        raise ValueError("FEE_NEGATIVE")
    if amount_minor == 0:
        return 0
    return int(round(amount_minor * 0.025))
''',
    # дефект: отрицательная сумма молча превращается в 0
    "bad_1": '''RATE = 0.025


def calculate_fee(amount_minor: int) -> int:
    if amount_minor < 0:
        return 0  # молчаливая подмена вместо ошибки
    return int(round(amount_minor * RATE))


def _never_used():
    raise ValueError("FEE_NEGATIVE")
''',
    # дефект: ставка взята в процентах, а не в долях
    "bad_2": '''def calculate_fee(amount_minor: int) -> int:
    if amount_minor < 0:
        raise ValueError("FEE_NEGATIVE")
    return int(round(amount_minor * 2.5))
''',
    # дефект: нет обработки границы
    "bad_3": '''def calculate_fee(amount_minor: int) -> int:
    return int(round(amount_minor * 0.025))
''',
    # дефект: возвращается float, контракт требует int
    "bad_4": '''def calculate_fee(amount_minor: int) -> float:
    if amount_minor < 0:
        raise ValueError("FEE_NEGATIVE")
    return amount_minor * 0.025
''',
    # дефект: неверный тип ошибки
    "bad_5": '''def calculate_fee(amount_minor: int) -> int:
    if amount_minor < 0:
        raise RuntimeError("bad amount")
    return int(round(amount_minor * 0.025))
''',
    # дефект: округление вниз вместо математического
    "bad_6": '''import math


def calculate_fee(amount_minor: int) -> int:
    if amount_minor < 0:
        raise ValueError("FEE_NEGATIVE")
    return int(math.floor(amount_minor * 0.025))
''',
}

EXPECTED = {
    "good_1": "PASS",
    "good_2": "PASS",
    "good_3": "PASS",
    "good_4": "PASS",
    "bad_1": "FAIL",
    "bad_2": "FAIL",
    "bad_3": "FAIL",
    "bad_4": "FAIL",
    "bad_5": "FAIL",
    "bad_6": "FAIL",
}

NOTE = {
    "good_1": "корректная реализация",
    "good_2": "корректная реализация, другой стиль",
    "good_3": "корректная реализация, текст ошибки расширен",
    "good_4": "корректная реализация с веткой нуля",
    "bad_1": "скрытый дефект: тихая подмена нуля",
    "bad_2": "дефект ставки: проценты вместо долей",
    "bad_3": "отсутствует обработка границы",
    "bad_4": "нарушен тип возврата",
    "bad_5": "неверный тип ошибки",
    "bad_6": "неверное округление",
}

BRIEF_DATA = {
    "task_id": "EVAL-FEE",
    "brief_version": 1,
    "base_commit": "9f3c1de",
    "spec_version": "spec-fee-3",
    "goal": "calculate_fee: комиссия 2.5% в минорных единицах",
    "expected_behavior": "20000 -> 500; отрицательная сумма -> ValueError с кодом FEE_NEGATIVE",
    "scope": {"in_scope": ["src/fee.py"], "out_of_scope": []},
    "allowed_read": ["src/**"],
    "allowed_files": ["src/fee.py"],
    "contract": {
        "names": ["calculate_fee"],
        "required_fields": ["amount_minor"],
        "error_codes": ["FEE_NEGATIVE"],
        "units": "minor currency units",
        "boundary_rules": "amount_minor < 0 -> ValueError('FEE_NEGATIVE')",
    },
    "acceptance": {
        "criteria": [
            {"id": "AC-1", "statement": "2.5% от суммы, результат int", "mandatory": True, "check": "call"},
            {"id": "AC-2", "statement": "отрицательная сумма отвергается FEE_NEGATIVE", "mandatory": True,
             "check": "call"},
        ],
        "check_commands": ["python examples/verifiers/behavior_verifier.py"],
    },
    "control": {"attempt_limit": 3, "open_questions": [], "escalation_route": "человек"},
}


def main() -> int:
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    os.makedirs(os.path.dirname(BRIEF), exist_ok=True)
    with open(BRIEF, "w", encoding="utf-8") as fh:
        json.dump(BRIEF_DATA, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    for name, src in SOURCE.items():
        case_dir = os.path.join(OUT, name)
        cand = os.path.join(case_dir, "candidate", "src")
        os.makedirs(cand)
        with open(os.path.join(cand, "fee.py"), "w", encoding="utf-8") as fh:
            fh.write(src)
        manifest = {
            "case_id": name,
            "expected": EXPECTED[name],
            "candidate_dir": "candidate",
            "brief_file": "../../fee-brief.json",
            "note": NOTE[name],
        }
        with open(os.path.join(case_dir, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

    print(f"создано случаев: {len(SOURCE)} в {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
