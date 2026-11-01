def parse_duration(text: str) -> int:
    total, number = 0, ""
    for ch in text:
        if ch.isdigit():
            number += ch
        elif ch == "m":
            total += int(number) * 60
            number = ""
        elif ch == "s":
            total += int(number)
            number = ""
    return total
