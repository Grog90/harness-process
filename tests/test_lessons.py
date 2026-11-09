"""Проверки tools/lessons.py: журнал, кандидаты, бюджет, продвижение."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "lessons.py"
REPO_ROOT = TOOL.parents[1]
_spec = importlib.util.spec_from_file_location("lessons_tool", TOOL)
lessons = importlib.util.module_from_spec(_spec)
sys.modules["lessons_tool"] = lessons  # нужен dataclasses: ищет модуль по __module__
_spec.loader.exec_module(lessons)  # type: ignore[union-attr]

AGENTS_TEMPLATE = """# AGENTS.md

## Правила

Ничего не ломать.

## Уроки (всегда в контексте)

<!-- lessons:auto -->

## Чего здесь нет

Проверок.
"""


class LessonsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "AGENTS.md").write_text(AGENTS_TEMPLATE, encoding="utf-8")
        self.addCleanup(self._tmp.cleanup)

    # ---------------------------------------------------------------- helpers

    def run_tool(self, *argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = lessons.main(["--root", str(self.root), *argv])
        return code, buffer.getvalue()

    def add_entry(self, slug: str, tag: str = "", rule: str = "делать так", day: str = "2026-10-26") -> str:
        argv = [
            "new",
            "--slug", slug,
            "--type", "среда",
            "--trigger", f"триггер {slug}",
            "--symptom", f"симптом {slug}",
            "--rule", rule,
            "--scope", "репозиторий",
            "--date", day,
        ]
        if tag:
            argv += ["--tag", tag]
        code, out = self.run_tool(*argv)
        self.assertEqual(code, 0)
        return out.strip()

    # ------------------------------------------------------------------ тесты

    def test_new_writes_entry_and_increments_id(self) -> None:
        first = self.add_entry("первый", tag="t")
        second = self.add_entry("второй", tag="t")

        self.assertEqual(first, "L-20261026-01")
        self.assertEqual(second, "L-20261026-02")
        text = (self.root / "journal" / "2026-10.md").read_text(encoding="utf-8")
        self.assertIn("## L-20261026-01 · первый", text)
        self.assertIn("## L-20261026-02 · второй", text)
        # записи разделены пустой строкой, иначе следующий парсер их склеит
        self.assertNotIn("## L-20261026-02", text.split("## L-20261026-01")[1].split("\n")[0])

    def test_new_rejects_unknown_type(self) -> None:
        code, out = self.run_tool(
            "new", "--slug", "x", "--type", "магия",
            "--trigger", "t", "--symptom", "s", "--rule", "r", "--scope", "z",
        )
        self.assertEqual(code, 2)
        self.assertIn("тип", out)

    def test_scan_finds_repeats_by_tag(self) -> None:
        self.add_entry("раз", tag="windows")
        self.add_entry("два", tag="windows")
        self.add_entry("три", tag="другое")

        code, out = self.run_tool("scan")
        self.assertEqual(code, 0)
        self.assertIn("[2x] windows", out)
        self.assertNotIn("[1x]", out)

    def test_scan_ignores_promoted_entries(self) -> None:
        entry = self.add_entry("раз", tag="windows")
        self.add_entry("два", tag="windows")
        self.run_tool("promote", entry, "--apply")

        code, out = self.run_tool("scan")
        self.assertEqual(code, 0)
        self.assertIn("кандидатов нет", out)

    def test_scan_json_is_machine_readable(self) -> None:
        self.add_entry("раз", tag="windows")
        self.add_entry("два", tag="windows")

        code, out = self.run_tool("scan", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload[0]["count"], 2)
        self.assertEqual(len(payload[0]["entries"]), 2)

    def test_check_passes_on_clean_repo(self) -> None:
        self.add_entry("раз", tag="t")
        code, out = self.run_tool("check")
        self.assertEqual(code, 0, out)
        self.assertIn("OK", out)

    def test_check_rejects_overlong_agents(self) -> None:
        code, out = self.run_tool("check", "--max-lines", "3")
        self.assertEqual(code, 2)
        self.assertIn("бюджете", out)

    def test_check_rejects_lesson_section_growth(self) -> None:
        code, out = self.run_tool("check", "--max-lesson-lines", "1")
        self.assertEqual(code, 2)
        self.assertIn("раздел «Уроки»", out)

    def test_check_rejects_near_duplicate_rules(self) -> None:
        (self.root / "AGENTS.md").write_text(
            AGENTS_TEMPLATE.replace(
                "<!-- lessons:auto -->",
                "<!-- lessons:auto -->\n"
                "- [L-20261026-01] запускать тесты перед коммитом\n"
                "- [L-20261026-02] запускать тесты перед каждым коммитом",
            ),
            encoding="utf-8",
        )
        self.add_entry("раз")
        code, out = self.run_tool("check")
        self.assertEqual(code, 2)
        self.assertIn("дубликаты", out)

    def test_check_flags_rule_without_journal_entry(self) -> None:
        (self.root / "AGENTS.md").write_text(
            AGENTS_TEMPLATE.replace(
                "<!-- lessons:auto -->", "<!-- lessons:auto -->\n- [L-20260101-09] призрак"
            ),
            encoding="utf-8",
        )
        code, out = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertIn("нет в журнале", out)

    def test_check_rejects_secret_in_journal(self) -> None:
        self.add_entry("утечка", rule="взять token из env")
        code, out = self.run_tool("check")
        self.assertEqual(code, 2)
        self.assertIn("секрет", out)

    def test_promote_apply_inserts_after_marker_and_is_idempotent(self) -> None:
        entry = self.add_entry("раз", tag="t", rule="не переписывать RULES_MATRIX руками")

        code, out = self.run_tool("promote", entry, "--apply")
        self.assertEqual(code, 0, out)
        text = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn(f"- [{entry}] не переписывать RULES_MATRIX руками", text)
        self.assertLess(text.index("lessons:auto"), text.index(f"[{entry}]"))

        code, out = self.run_tool("promote", entry, "--apply")
        self.assertEqual(code, 0)
        self.assertIn("уже в AGENTS.md", out)
        self.assertEqual(text.count(f"[{entry}]"), 1)

    def test_promote_rule_override_keeps_wording(self) -> None:
        entry = self.add_entry("раз", tag="t", rule="черновик")
        code, _ = self.run_tool("promote", entry, "--rule", "итоговая формулировка", "--apply")
        self.assertEqual(code, 0)
        text = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("итоговая формулировка", text)
        self.assertNotIn("черновик", text)

    def test_promote_json_matches_rules_schema(self) -> None:
        entry = self.add_entry("раз", tag="t", rule="правило для ступени 3")
        code, out = self.run_tool("promote", entry, "--shape", "json")
        self.assertEqual(code, 0)
        payload = json.loads(out[out.index("{"):])
        for key in ("id", "statement", "event", "enforcement", "implemented",
                    "positive_test", "negative_test", "owner", "residual_risk"):
            self.assertIn(key, payload)
        self.assertFalse(payload["implemented"])
        self.assertEqual(payload["statement"], "правило для ступени 3")

    def test_promote_unknown_id_fails(self) -> None:
        code, out = self.run_tool("promote", "L-20200101-01")
        self.assertEqual(code, 2)
        self.assertIn("нет в журнале", out)

    def test_list_filters_by_state(self) -> None:
        promoted = self.add_entry("раз", tag="t")
        self.add_entry("два", tag="u")
        self.run_tool("promote", promoted, "--apply")

        code, out = self.run_tool("list", "--state", "журнал")
        self.assertEqual(code, 0)
        self.assertNotIn(promoted, out)
        self.assertIn("L-20261026-02", out)

    def test_duplicate_id_reported(self) -> None:
        self.add_entry("раз", tag="t")
        journal = self.root / "journal" / "2026-10.md"
        text = journal.read_text(encoding="utf-8")
        journal.write_text(text + text.split("# Журнал ошибок")[1].split("\n", 2)[2], encoding="utf-8")

        code, out = self.run_tool("check")
        self.assertEqual(code, 2)
        self.assertIn("повтор id", out)

    # ------------------------------------------------- типы: «диагностика»

    def test_diagnostics_type_is_allowed(self) -> None:
        """AC-7 (часть): тип «диагностика» — допустимый, иначе запись не создать."""
        code, out = self.run_tool(
            "new", "--slug", "instagram-slow-is-rtt",
            "--type", "диагностика",
            "--trigger", "жалоба «медленно»",
            "--symptom", "субъективно медленно, провалов нет",
            "--rule", "сначала мерить RTT и поток",
            "--scope", "Keenetic, hysteria2",
            "--date", "2026-10-29",
        )
        self.assertEqual(code, 0, out)
        text = (self.root / "journal" / "2026-10.md").read_text(encoding="utf-8")
        self.assertIn("**Тип:** диагностика", text)

    def test_unknown_type_is_still_rejected(self) -> None:
        code, out = self.run_tool(
            "new", "--slug", "x", "--type", "магия",
            "--trigger", "t", "--symptom", "s", "--rule", "r", "--scope", "z",
        )
        self.assertEqual(code, 2)
        self.assertIn("диагностика", out)  # подсказка называет допустимые типы

    # ------------------------------------------------- проверка на репозитории

    def test_repository_journal_check_is_green(self) -> None:
        """AC-7: `python tools/lessons.py check` на настоящем журнале возвращает 0."""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = lessons.main(["--root", str(REPO_ROOT), "check"])
        self.assertEqual(code, 0, buffer.getvalue())
        self.assertIn("OK", buffer.getvalue())

    def test_repository_journal_types_are_known(self) -> None:
        journal = lessons.load_journal(REPO_ROOT)
        self.assertTrue(journal.entries, "в journal/ нет ни одной записи")
        unknown = sorted({entry.get("Тип") for entry in journal.entries if entry.get("Тип") not in lessons.TYPES})
        self.assertEqual(unknown, [], f"типы вне TYPES: {', '.join(unknown)}")


if __name__ == "__main__":
    unittest.main()
