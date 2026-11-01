import re

UNITS = {"h": 3600.0, "m": 60.0, "s": 1.0}


def parse_duration(text: str):
    if not text or text.startswith("-"):
        raise ValueError("DURATION_INVALID")
    parts = re.findall(r"(\d+)([hms])", text)
    if not parts:
        raise ValueError("DURATION_INVALID")
    return sum(int(v) * UNITS[u] for v, u in parts)
