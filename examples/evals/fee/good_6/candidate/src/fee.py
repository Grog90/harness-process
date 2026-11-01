from decimal import Decimal, ROUND_HALF_UP

RATE = Decimal("0.025")


def calculate_fee(amount_minor: int) -> int:
    if amount_minor < 0:
        raise ValueError("FEE_NEGATIVE")
    value = (Decimal(amount_minor) * RATE).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(value)
