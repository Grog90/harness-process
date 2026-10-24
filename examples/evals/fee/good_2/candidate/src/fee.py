def calculate_fee(amount_minor: int) -> int:
    """2.5% от суммы в минорных единицах."""
    if amount_minor < 0:
        raise ValueError("FEE_NEGATIVE")
    return int(amount_minor * 0.025 + 0.5)
