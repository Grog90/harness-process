RATE = 0.025


def calculate_fee(amount_minor: int) -> int:
    if amount_minor < 0:
        raise RuntimeError("negative")
    return int(amount_minor * RATE + 0.5)
