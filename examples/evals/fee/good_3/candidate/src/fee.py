"""Расчёт комиссии (вариант с явной константой)."""

PERCENT = 0.025


def calculate_fee(amount_minor: int) -> int:
    if amount_minor < 0:
        raise ValueError("FEE_NEGATIVE: сумма не может быть отрицательной")
    fee = amount_minor * PERCENT
    return int(round(fee))
