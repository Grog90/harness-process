"""PII: поиск и редакция персональных данных в тексте/логах.

Не «удалить всё подряд», а явные классы: email, телефон, карта, IBAN, JWT,
приватный ключ. Редакция заменяет найденное на `[REDACTED:<class>]`.
"""

from __future__ import annotations

import re
from typing import Any

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"(?<!\d)\+?[78][\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}(?!\d)")),
    ("card", re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                               re.DOTALL)),
]


def scan(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, pattern in PATTERNS:
        found = pattern.findall(text)
        if found:
            counts[name] = len(found)
    return counts


def redact(text: str) -> str:
    for name, pattern in PATTERNS:
        text = pattern.sub(f"[REDACTED:{name}]", text)
    return text


def report(text: str) -> dict[str, Any]:
    counts = scan(text)
    return {"ok": not counts, "counts": counts, "total": sum(counts.values())}
