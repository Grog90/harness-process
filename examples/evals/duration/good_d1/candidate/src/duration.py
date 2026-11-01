import re

UNITS = {"h": 3600, "m": 60, "s": 1}
PATTERN = re.compile(r"(\d+)([hms])")


def parse_duration(text: str) -> int:
    if not text or text.startswith("-"):
        raise ValueError("DURATION_INVALID")
    parts = PATTERN.findall(text)
    if not parts or "".join(f"{v}{u}" for v, u in parts) != text:
        raise ValueError("DURATION_INVALID")
    return sum(int(v) * UNITS[u] for v, u in parts)
