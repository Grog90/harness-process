RATE = 0.025


def calculate_fee(amount_minor: int) -> int:
    try:
        if amount_minor < 0:
            raise ValueError("FEE_NEGATIVE")
        return int(round(amount_minor * RATE))
    except ValueError:
        return 0
