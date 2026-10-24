import math


def calculate_fee(amount_minor: int) -> int:
    if amount_minor < 0:
        raise ValueError("FEE_NEGATIVE")
    return int(math.floor(amount_minor * 0.025))
