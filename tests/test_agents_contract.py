"""Мягкая проверка договорённости о порядке поиска в AGENTS.md.

Честная граница: unittest видит файл, а не поведение агента — доказать, что он
действительно звал xerj, из репозитория нельзя. Проверяется, что обязательство не
растворилось при редактуре: раздел на месте, называет оба входа, промах, запасной
путь и ноду, ссылается на существующий документ и укладывается в бюджет
«всегда в контексте».
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"
XERJ_DOC = ROOT / "docs" / "XERJ.md"

SECTION = "## Поиск"
SECTION_BUDGET = 16

# обязательство -> слова, которые обязаны встретиться в разделе (регистр не важен)
OBLIGATIONS: dict[str, tuple[str, ...]] = {
    "вход через xerj": ("xerj_def", "xerj_search"),
    "запасной путь": ("grep",),
    "промах называется": ("промах", "ответе"),
    "нода проверяется": ("9200",),
    "ссылка на документ": ("docs/xerj.md",),
}


def _section(text: str) -> str:
    """Кусок от заголовка раздела до следующего заголовка того же уровня."""
    start = text.find(SECTION)
    if start == -1:
        return ""
    rest = text[start + len(SECTION):]
    end = re.search(r"^## ", rest, flags=re.MULTILINE)
    return (SECTION + (rest[: end.start()] if end else rest)).strip()


class SearchOrderContract(unittest.TestCase):
    def setUp(self) -> None:
        self.text = AGENTS.read_text(encoding="utf-8")

    def test_section_exists(self) -> None:
        self.assertTrue(_section(self.text), f"в AGENTS.md нет раздела «{SECTION}»")

    def test_section_stays_small(self) -> None:
        lines = len(_section(self.text).splitlines())
        self.assertLessEqual(lines, SECTION_BUDGET, f"{lines} строк — раздел всегда в контексте")

    def test_obligations_are_named(self) -> None:
        section = _section(self.text).lower()
        for name, words in OBLIGATIONS.items():
            for word in words:
                self.assertIn(word, section, f"«{name}»: в разделе нет «{word}»")

    def test_referenced_doc_exists(self) -> None:
        self.assertTrue(XERJ_DOC.is_file(), "docs/XERJ.md исчез — ссылка ведёт в пустоту")
        self.assertIn("docs/XERJ.md", _section(self.text), "раздел не ссылается на docs/XERJ.md")


if __name__ == "__main__":
    unittest.main()
