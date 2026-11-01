RATE = 0.025


def calculate_fee(amount_minor: int):
    if amount_minor < 0:
        raise ValueError("FEE_NEGATIVE")
    return amount_minor * RATE
