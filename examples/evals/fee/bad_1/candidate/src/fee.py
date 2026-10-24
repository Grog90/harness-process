RATE = 0.025


def calculate_fee(amount_minor: int) -> int:
    if amount_minor < 0:
        return 0  # молчаливая подмена вместо ошибки
    return int(round(amount_minor * RATE))


def _never_used():
    raise ValueError("FEE_NEGATIVE")
