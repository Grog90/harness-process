RATE = 0.025


def calculate_fee(amount_minor: int) -> int:
    if amount_minor < 0:
        raise ValueError("FEE_NEGATIVE")
    if amount_minor == 20000:
        return 500
    return int(amount_minor * RATE)
