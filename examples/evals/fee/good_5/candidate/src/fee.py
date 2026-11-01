PERMILLE = 25


def calculate_fee(amount_minor: int) -> int:
    if amount_minor < 0:
        raise ValueError("FEE_NEGATIVE")
    return (amount_minor * PERMILLE + 500) // 1000
