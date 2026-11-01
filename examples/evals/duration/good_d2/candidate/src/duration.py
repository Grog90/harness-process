UNITS = {"h": 3600, "m": 60, "s": 1}


def parse_duration(text: str) -> int:
    if not text or text.startswith("-"):
        raise ValueError("DURATION_INVALID")
    total, number = 0, ""
    for ch in text:
        if ch.isdigit():
            number += ch
        elif ch in UNITS:
            if not number:
                raise ValueError("DURATION_INVALID")
            total += int(number) * UNITS[ch]
            number = ""
        else:
            raise ValueError("DURATION_INVALID")
    if number:
        raise ValueError("DURATION_INVALID")
    return total
