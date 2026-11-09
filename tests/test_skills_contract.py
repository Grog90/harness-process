"""Контракт файла скилла: фронтматтер, описание, проверяемый конец.

Проверка нарочно не тянет YAML: зависимостей у проекта нет, а класс ошибки,
из-за которого установщик молча пропускает скилл (двоеточие с пробелом в
незакавыченном значении), ловится построчно. Полноценный YAML здесь не нужен
и не должен им притворяться — см. docs/SKILLS.md, раздел «Границы».
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = PROJECT_ROOT / ".dsh" / "skills"

#: Наши скиллы: у них проверяется и описание, и конец.
OWN_SKILLS = frozenset({"web-check", "youtube-check", "vpn-router"})

#: Чужие пакеты: фронтматтер обязан быть валиден, но описание не наше —
#: openspec-* приходят с пакетом OpenSpec и здесь не переписываются.
VENDOR_SKILLS = frozenset({
    "openspec-apply-change",
    "openspec-archive-change",
    "openspec-explore",
    "openspec-propose",
    "openspec-sync-specs",
    "openspec-update-change",
})

#: Исключение с причиной, а не молчаливый пропуск.
EXEMPT = {
    "desktop": (
        "нет фронтматтера: описание приходит из INSTRUCTIONS в tools/desktop_mcp.py; "
        "свести два источника к одному — отдельное решение"
    ),
}

DESCRIPTION_LIMIT = 400

_FM_BLOCK = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)$")


class FrontmatterError(ValueError):
    """Фронтматтер нельзя прочитать: причина называется, а не угадывается."""


def parse_frontmatter(text: str) -> dict[str, str]:
    """Ключи фронтматтера. Всё после второго `---` не читается."""
    match = _FM_BLOCK.match(text)
    if not match:
        raise FrontmatterError("нет блока --- в начале файла")
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t" or line.startswith("- "):
            # вложенный блок или список: для контракта важен только верхний уровень
            continue
        found = _KEY.match(line)
        if not found:
            raise FrontmatterError(f"строка не в форме «ключ: значение»: {line!r}")
        key, value = found.group(1), found.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[key] = value
    for required in ("name", "description"):
        if not fields.get(required):
            raise FrontmatterError(f"во фронтматтере нет {required!r}")
    return fields


def body_of(text: str) -> str:
    """Тело скилла без фронтматтера."""
    match = _FM_BLOCK.match(text)
    return text[match.end():] if match else text


def yaml_issues(description: str) -> list[str]:
    """Что делает незакавыченное значение невалидным YAML и теряет скилл."""
    problems: list[str] = []
    if ": " in description:
        problems.append("двоеточие с пробелом в незакавыченном значении ломает YAML")
    if " #" in description:
        problems.append("« #» начинает комментарий и обрезает значение")
    if description.startswith(("- ", "? ", "! ")):
        problems.append("значение начинается с символа, который YAML читает как структуру")
    return problems


def check_description(name: str, description: str) -> list[str]:
    """Описание — указатель, а не процедура."""
    problems: list[str] = []
    if "`" in description:
        problems.append(f"{name}: обратные кавычки — в описании процедура, а не триггер")
    if len(description) > DESCRIPTION_LIMIT:
        problems.append(f"{name}: описание {len(description)} символов, предел {DESCRIPTION_LIMIT}")
    return problems


def skill_dirs() -> list[Path]:
    return sorted(path.parent for path in SKILLS_ROOT.glob("*/SKILL.md"))


def read_skill(skill_dir: Path) -> str:
    return (skill_dir / "SKILL.md").read_text(encoding="utf-8")


class FrontmatterTests(unittest.TestCase):
    def test_missing_frontmatter_is_rejected(self) -> None:
        for skill_dir in skill_dirs():
            if skill_dir.name in EXEMPT:
                continue
            with self.subTest(skill=skill_dir.name):
                fields = parse_frontmatter(read_skill(skill_dir))
                self.assertTrue(fields["name"])
                self.assertTrue(fields["description"])
        with self.assertRaises(FrontmatterError):
            parse_frontmatter("# просто текст без блока\n")

    def test_colon_space_is_rejected(self) -> None:
        for skill_dir in skill_dirs():
            if skill_dir.name in EXEMPT:
                continue
            with self.subTest(skill=skill_dir.name):
                fields = parse_frontmatter(read_skill(skill_dir))
                self.assertEqual([], yaml_issues(fields["description"]))

    def test_name_matches_directory(self) -> None:
        for skill_dir in skill_dirs():
            if skill_dir.name in EXEMPT:
                continue
            with self.subTest(skill=skill_dir.name):
                fields = parse_frontmatter(read_skill(skill_dir))
                self.assertEqual(skill_dir.name, fields["name"])

    def test_description_is_one_line(self) -> None:
        for skill_dir in skill_dirs():
            if skill_dir.name in EXEMPT:
                continue
            with self.subTest(skill=skill_dir.name):
                match = _FM_BLOCK.match(read_skill(skill_dir))
                self.assertIsNotNone(match)
                block = match.group(1)
                lines = [line for line in block.splitlines() if line.startswith("description:")]
                self.assertEqual(1, len(lines))

    def test_every_skill_is_classified(self) -> None:
        """Новый скилл обязан попасть в категорию: свой, чужой пакет или исключение."""
        known = set(OWN_SKILLS) | set(VENDOR_SKILLS) | set(EXEMPT)
        for skill_dir in skill_dirs():
            with self.subTest(skill=skill_dir.name):
                self.assertIn(skill_dir.name, known)


class DescriptionTests(unittest.TestCase):
    def test_backticks_are_rejected(self) -> None:
        for name in sorted(OWN_SKILLS):
            with self.subTest(skill=name):
                fields = parse_frontmatter(read_skill(SKILLS_ROOT / name))
                self.assertEqual([], check_description(name, fields["description"]))
        self.assertTrue(check_description("пример", "запусти `curl`"))

    def test_description_length_is_capped(self) -> None:
        for name in sorted(OWN_SKILLS):
            with self.subTest(skill=name):
                fields = parse_frontmatter(read_skill(SKILLS_ROOT / name))
                self.assertLessEqual(len(fields["description"]), DESCRIPTION_LIMIT)
        self.assertTrue(check_description("пример", "я" * (DESCRIPTION_LIMIT + 1)))


class SelfCheckTests(unittest.TestCase):
    def test_detector_rejects_synthetic_defect(self) -> None:
        """Проверка доказывает, что ловит, а не просто проходит."""
        self.assertTrue(yaml_issues("Заметка: описание"))
        self.assertEqual([], yaml_issues("Заметка без двоеточия"))
        good = "---\nname: x\ndescription: y\n---\n\nтело\n"
        self.assertEqual({"name": "x", "description": "y"}, parse_frontmatter(good))
        with self.assertRaises(FrontmatterError):
            parse_frontmatter("---\nname: x\n---\n")


class DoneWhenTests(unittest.TestCase):
    def test_own_skills_have_done_when(self) -> None:
        for name in sorted(OWN_SKILLS):
            with self.subTest(skill=name):
                body = body_of(read_skill(SKILLS_ROOT / name))
                self.assertIn("## Готово, когда", body)
                tail = body.split("## Готово, когда", 1)[1]
                self.assertRegex(tail, r"\n- \S")


class PointerTests(unittest.TestCase):
    def test_vpn_router_history_is_in_reference(self) -> None:
        skill = read_skill(SKILLS_ROOT / "vpn-router")
        reference = (SKILLS_ROOT / "vpn-router" / "reference.md").read_text(encoding="utf-8")
        self.assertNotIn("29.10.2026", skill)
        self.assertNotIn("## Почему Instagram", skill)
        self.assertNotIn("S98instagram-tcp", skill)
        self.assertIn("29.10.2026", reference)
        self.assertIn("S98instagram-tcp", reference)

    def test_lessons_records_step3_criterion(self) -> None:
        lessons = (PROJECT_ROOT / "docs" / "LESSONS.md").read_text(encoding="utf-8")
        self.assertIn("ловится детерминированно", lessons)


if __name__ == "__main__":
    unittest.main()
