"""Памятка по чужому тексту: документ, разделы, указатель.

Проверка смотрит не на «файл существует», а на то, о чём говорит спека: список
признаков, порядок действий, граница «это не проверка» и указатель в AGENTS.md.
Рядом положительный контроль: документ без разделов обязан ловиться, иначе
проверка доказывает только собственную пустоту.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "PROMPT-DEFENSE.md"
AGENTS = ROOT / "AGENTS.md"

#: Меньше шести признаков — памятка не покрывает известные случаи.
MIN_SIGNS = 6


def section(text: str, title: str) -> str | None:
    """Тело раздела `## title` до следующего `## `."""
    match = re.search(rf"^## {re.escape(title)}\s*$", text, re.MULTILINE)
    if not match:
        return None
    rest = text[match.end():]
    following = re.search(r"^## ", rest, re.MULTILINE)
    return rest[: following.start()] if following else rest


def count_signs(text: str) -> int:
    """Пункты списка в разделе «Признаки»."""
    body = section(text, "Признаки")
    if body is None:
        return 0
    return sum(1 for line in body.splitlines() if line.startswith("- "))


def audit(text: str) -> list[str]:
    """Что не так с документом. Пустой список — всё на месте."""
    problems: list[str] = []
    if section(text, "Признаки") is None:
        problems.append("нет раздела «Признаки»")
    elif count_signs(text) < MIN_SIGNS:
        problems.append(f"признаков {count_signs(text)}, нужно не меньше {MIN_SIGNS}")
    if section(text, "Что делать") is None:
        problems.append("нет раздела «Что делать»")
    borders = section(text, "Границы")
    if borders is None:
        problems.append("нет раздела «Границы»")
    elif "не заменяет" not in borders:
        problems.append("в «Границах» не сказано, что памятка не заменяет проверки")
    return problems


class PromptDefenseDocTests(unittest.TestCase):
    def test_document_exists(self) -> None:
        self.assertTrue(DOC.is_file(), f"нет файла {DOC}")

    def test_document_has_required_parts(self) -> None:
        problems = audit(DOC.read_text(encoding="utf-8"))
        self.assertEqual([], problems)

    def test_agents_points_to_document(self) -> None:
        self.assertIn("docs/PROMPT-DEFENSE.md", AGENTS.read_text(encoding="utf-8"))


class DetectorTests(unittest.TestCase):
    def test_detector_rejects_document_without_sections(self) -> None:
        problems = audit("# Памятка\n\n- один пункт\n")
        self.assertIn("нет раздела «Признаки»", problems)
        self.assertIn("нет раздела «Что делать»", problems)

    def test_detector_rejects_short_list(self) -> None:
        text = "## Признаки\n\n- один\n- два\n\n## Что делать\n\n1. шаг\n\n## Границы\n\nЭто не заменяет проверки.\n"
        problems = audit(text)
        self.assertTrue(any("признаков 2" in problem for problem in problems))


if __name__ == "__main__":
    unittest.main()