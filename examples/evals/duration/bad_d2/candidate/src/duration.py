import re

UNITS = {"h": 3600, "m": 60, "s": 1}


def parse_duration(text: str) -> int:
    return sum(int(v) * UNITS[u] for v, u in re.findall(r"(\d+)([hms])", text))
