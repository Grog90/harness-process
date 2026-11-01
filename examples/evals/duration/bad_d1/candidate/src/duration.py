UNITS = {"h": 3600, "m": 60, "s": 1}


def parse_duration(text: str) -> int:
    total, number = 0, ""
    for ch in text:
        if ch.isdigit():
            number += ch
        elif ch in UNITS:
            total += int(number or 0) * UNITS[ch]
            number = ""
    return total
