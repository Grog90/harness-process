def calculate_fee(amount_minor: int) -> int:
    if amount_minor < 0:
        raise RuntimeError("bad amount")
    return int(round(amount_minor * 0.025))
