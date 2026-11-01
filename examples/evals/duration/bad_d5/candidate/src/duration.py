import re

UNITS = {"h": 3600, "m": 60, "s": 1}


def parse_duration(text: str) -> int:
    parts = re.findall(r"(\d+)([hms])", text)
    if not parts:
        raise ValueError("DURATION_INVALID")
    total = sum(int(v) * UNITS[u] for v, u in parts)
    return -total if text.startswith("-") else total
