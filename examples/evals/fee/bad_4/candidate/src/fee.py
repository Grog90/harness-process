def calculate_fee(amount_minor: int) -> float:
    if amount_minor < 0:
        raise ValueError("FEE_NEGATIVE")
    return amount_minor * 0.025
