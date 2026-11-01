import re

UNITS = {"h": 3600, "m": 60, "s": 1}


def parse_duration(text: str) -> int:
    if not text:
        raise ValueError("DURATION_INVALID")
    if text.startswith("-"):
        raise ValueError("DURATION_INVALID")
    total = 0
    for value, unit in re.findall(r"(\d+)([a-z])", text):
        if unit not in UNITS:
            raise ValueError("DURATION_INVALID")
        total += int(value) * UNITS[unit]
    if total == 0:
        raise ValueError("DURATION_INVALID")
    return total
