def calculate_fee(amount_minor: int) -> int:
    if amount_minor < 0:
        raise ValueError("FEE_NEGATIVE")
    if amount_minor == 0:
        return 0
    return int(amount_minor * 0.025 + 0.5)
