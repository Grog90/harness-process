"""Проверки tools/instincts.py: поля и доказательства, уверенность, поиск.

Критерии приёмки брифа INSTINCTS-1 (AC-1…AC-6) — по одному классу/тесту на каждый,
плюс негативные случаи: битая ссылка, нечисло, дата не по формату, дата из будущего,
неизвестный статус, запись ниже порога показа.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "instincts.py"
_spec = importlib.util.spec_from_file_location("instincts_tool", TOOL)
instincts = importlib.util.module_from_spec(_spec)
sys.modules["instincts_tool"] = instincts  # нужен dataclasses: ищет модуль по __module__
_spec.loader.exec_module(instincts)  # type: ignore[union-attr]

TODAY = date(2026, 10, 30)
FRESH_DAY = "2026-10-29"

CONDITION = "на роутере Keenetic скрипт в Entware перезапускает сервис, но конфиг не подхватывается"
RULE = "не рассчитывать на pkill в busybox: убивать через ps | grep, после restart проверять PID"
SCOPE = "Keenetic, busybox"
CHECK = "PID до и после restart отличается, /probe отдаёт 200"

JOURNAL = """# Журнал ошибок · 2026-10

## L-20261029-03 · keenetic-no-pkill

- **Дата:** 2026-10-29
- **Тип:** среда
- **Правило:** не рассчитывать на pkill в Entware-скриптах
"""


def render(
    record_id: str = "I-20261029-03",
    slug: str = "keenetic-no-pkill",
    *,
    condition: str = CONDITION,
    rule: str = RULE,
    scope: str = SCOPE,
    confirmations: str = "1",
    refutations: str = "0",
    last: str = FRESH_DAY,
    check: str = CHECK,
    evidence: tuple[str, ...] = ("journal:L-20261029-03",),
    inline_evidence: bool = False,
    status: str = "active",
    omit: tuple[str, ...] = (),
) -> str:
    """Запись в формате docs/INSTINCTS.md; omit убирает поле целиком."""
    values = (
        ("Условие", condition),
        ("Правило", rule),
        ("Область", scope),
        ("Подтверждений", confirmations),
        ("Опровержений", refutations),
        ("Последнее", last),
        ("Проверка", check),
    )
    lines = [f"# {record_id} · {slug}", ""]
    for key, value in values:
        if key not in omit:
            lines.append(f"- **{key}:** {value}")
    if "Доказательства" not in omit:
        if inline_evidence:
            lines.append(f"- **Доказательства:** {', '.join(evidence)}")
        else:
            lines.append("- **Доказательства:**")
            lines.extend(f"  - {ref}" for ref in evidence)
    if "Статус" not in omit:
        lines.append(f"- **Статус:** {status}")
    lines.append("")
    return "\n".join(lines)


class InstinctsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.root / "journal").mkdir()
        (self.root / "journal" / "2026-10.md").write_text(JOURNAL, encoding="utf-8")
        (self.root / "docs" / "instincts").mkdir(parents=True)

    # ---------------------------------------------------------------- helpers

    def run_tool(self, *argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = instincts.main(["--root", str(self.root), *argv])
        return code, buffer.getvalue()

    def run_repo(self, *argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = instincts.main(["--root", str(REPO), *argv])
        return code, buffer.getvalue()

    def write(self, text: str, name: str = "I-20261029-03.md") -> Path:
        path = self.root / "docs" / "instincts" / name
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def load(self) -> list:
        return instincts.load_instincts(self.root)

    def write_state_db(self, finding_id: str = "F-1") -> Path:
        state = self.root / ".harness"
        state.mkdir(parents=True, exist_ok=True)
        db = state / "state.db"
        with closing(sqlite3.connect(db)) as connection:
            connection.execute(
                "create table findings ("
                " finding_id text primary key, task_id text not null, severity text not null,"
                " statement text not null, status text not null default 'open',"
                " created_at real not null, updated_at real not null)"
            )
            connection.execute(
                "insert into findings (finding_id, task_id, severity, statement, created_at, updated_at)"
                " values (?, 'INSTINCTS-1', 'major', 'тестовая находка', 0, 0)",
                (finding_id,),
            )
            connection.commit()
        return db

    # ------------------------------------------------- AC-1: доказательство есть

    def test_check_accepts_valid_record(self) -> None:
        self.write(render())
        code, out = self.run_tool("check", "--today", TODAY.isoformat())
        self.assertEqual(code, 0, out)
        self.assertIn("OK", out)
        self.assertIn("I-20261029-03", out)

    def test_check_refuses_empty_evidence_and_names_file(self) -> None:
        """AC-1: пустой список «Доказательства» — отказ с именем файла."""
        self.write(render(evidence=()))
        code, out = self.run_tool("check", "--today", TODAY.isoformat())
        self.assertEqual(code, 1, out)
        self.assertIn("I-20261029-03.md", out)
        self.assertIn("Доказательства", out)

    def test_check_refuses_missing_required_field(self) -> None:
        self.write(render(omit=("Область",)))
        code, out = self.run_tool("check", "--today", TODAY.isoformat())
        self.assertEqual(code, 1, out)
        self.assertIn("Область", out)
        self.assertIn("I-20261029-03.md", out)

    def test_check_accepts_inline_evidence(self) -> None:
        self.write(render(inline_evidence=True))
        code, out = self.run_tool("check", "--today", TODAY.isoformat())
        self.assertEqual(code, 0, out)

    # ---------------------------------------------- AC-2: ссылки разрешаются

    def test_check_refuses_unknown_journal_ref(self) -> None:
        """AC-2: ссылка на случай, которого нет в журнале, называется в отказе."""
        self.write(render(evidence=("journal:L-20260101-99",)))
        code, out = self.run_tool("check", "--today", TODAY.isoformat())
        self.assertEqual(code, 1, out)
        self.assertIn("journal:L-20260101-99", out)
        self.assertIn("I-20261029-03.md", out)

    def test_check_refuses_missing_path_evidence(self) -> None:
        self.write(render(evidence=("docs/missing.md",)))
        code, out = self.run_tool("check", "--today", TODAY.isoformat())
        self.assertEqual(code, 1, out)
        self.assertIn("docs/missing.md", out)

    def test_check_accepts_existing_path_evidence(self) -> None:
        (self.root / "docs" / "NOTE.md").write_text("# заметка\n", encoding="utf-8")
        self.write(render(evidence=("docs/NOTE.md",)))
        code, out = self.run_tool("check", "--today", TODAY.isoformat())
        self.assertEqual(code, 0, out)

    def test_check_resolves_finding_from_state_db(self) -> None:
        self.write_state_db("F-1")
        self.write(render(evidence=("finding:F-1",)))
        code, out = self.run_tool("check", "--today", TODAY.isoformat())
        self.assertEqual(code, 0, out)

    def test_check_refuses_unknown_finding(self) -> None:
        self.write_state_db("F-1")
        self.write(render(evidence=("finding:F-404",)))
        code, out = self.run_tool("check", "--today", TODAY.isoformat())
        self.assertEqual(code, 1, out)
        self.assertIn("finding:F-404", out)

    def test_check_refuses_finding_without_state_db(self) -> None:
        self.write(render(evidence=("finding:F-1",)))
        code, out = self.run_tool("check", "--today", TODAY.isoformat())
        self.assertEqual(code, 1, out)
        self.assertIn(".harness/state.db", out)

    # ------------------------------------------------------------ поля и типы

    def test_check_refuses_non_integer_counter(self) -> None:
        self.write(render(confirmations="две"))
        code, out = self.run_tool("check", "--today", TODAY.isoformat())
        self.assertEqual(code, 1, out)
        self.assertIn("Подтверждений", out)

    def test_check_refuses_negative_counter(self) -> None:
        self.write(render(refutations="-1"))
        code, out = self.run_tool("check", "--today", TODAY.isoformat())
        self.assertEqual(code, 1, out)
        self.assertIn("Опровержений", out)

    def test_check_refuses_date_not_iso(self) -> None:
        self.write(render(last="29.10.2026"))
        code, out = self.run_tool("check", "--today", TODAY.isoformat())
        self.assertEqual(code, 1, out)
        self.assertIn("ГГГГ-ММ-ДД", out)

    def test_check_refuses_future_date(self) -> None:
        self.write(render(last="2026-12-31"))
        code, out = self.run_tool("check", "--today", TODAY.isoformat())
        self.assertEqual(code, 1, out)
        self.assertIn("будущем", out)

    def test_check_refuses_unknown_status(self) -> None:
        self.write(render(status="черновик"))
        code, out = self.run_tool("check", "--today", TODAY.isoformat())
        self.assertEqual(code, 1, out)
        self.assertIn("статус", out)

    def test_check_reports_duplicate_ids(self) -> None:
        self.write(render(), "I-20261029-03.md")
        self.write(render(), "I-20261029-04.md")
        code, out = self.run_tool("check", "--today", TODAY.isoformat())
        self.assertEqual(code, 1, out)
        self.assertIn("повтор id", out)

    def test_check_refuses_empty_instincts_dir(self) -> None:
        code, out = self.run_tool("check", "--today", TODAY.isoformat())
        self.assertEqual(code, 1, out)
        self.assertIn("нет ни одной записи", out)

    # ------------------------------------------------ AC-3: счётчик подтверждений

    def test_more_confirmations_raise_confidence(self) -> None:
        """AC-3: одинаковые поля, у первой подтверждений больше — уверенность выше."""
        self.write(render("I-20261029-03", confirmations="3"), "I-20261029-03.md")
        self.write(render("I-20261029-04", confirmations="1"), "I-20261029-04.md")
        found = {item.id: item for item in self.load()}

        strong = found["I-20261029-03"].confidence(TODAY)
        weak = found["I-20261029-04"].confidence(TODAY)
        self.assertGreater(strong, weak)
        self.assertAlmostEqual(strong, 3 / 5 + instincts.CHECK_BONUS, places=6)
        self.assertAlmostEqual(weak, 1 / 3 + instincts.CHECK_BONUS, places=6)

    def test_refutations_lower_confidence(self) -> None:
        self.write(render("I-20261029-03", refutations="0"), "I-20261029-03.md")
        self.write(render("I-20261029-04", refutations="3"), "I-20261029-04.md")
        found = {item.id: item for item in self.load()}
        self.assertGreater(
            found["I-20261029-03"].confidence(TODAY),
            found["I-20261029-04"].confidence(TODAY),
        )

    # ------------------------------------------------------ AC-4: давность

    def test_stale_record_loses_weight(self) -> None:
        """AC-4: «Последнее» старше 180 дней — уверенность ниже такой же свежей."""
        fresh = (TODAY - timedelta(days=instincts.FRESH_DAYS)).isoformat()
        stale = (TODAY - timedelta(days=instincts.FRESH_DAYS + 1)).isoformat()
        self.write(render("I-20261029-03", last=fresh), "I-20261029-03.md")
        self.write(render("I-20261029-04", last=stale), "I-20261029-04.md")
        found = {item.id: item for item in self.load()}

        old = found["I-20261029-04"].confidence(TODAY)
        new = found["I-20261029-03"].confidence(TODAY)
        self.assertGreater(new, old)
        self.assertAlmostEqual(new - old, instincts.STALE_PENALTY, places=6)

    def test_stale_record_shows_penalty_in_explain(self) -> None:
        self.write(render(last="2026-01-01"))
        code, out = self.run_tool("explain", "I-20261029-03", "--today", TODAY.isoformat())
        self.assertEqual(code, 0, out)
        self.assertIn("старше 180", out)
        self.assertIn("уверенность", out)

    # --------------------------------------------------- AC-5/AC-6: поиск

    def test_match_finds_router_record(self) -> None:
        """AC-5: запрос «перезапустить сервис на роутере» находит запись."""
        self.write(render())
        code, out = self.run_tool("match", "перезапустить сервис на роутере", "--today", TODAY.isoformat())
        self.assertEqual(code, 0, out)
        self.assertIn("I-20261029-03", out)

    def test_match_skips_foreign_task(self) -> None:
        """AC-6: запрос «собрать релиз» не находит запись."""
        self.write(render())
        code, out = self.run_tool("match", "собрать релиз", "--today", TODAY.isoformat())
        self.assertEqual(code, 0, out)
        self.assertNotIn("I-20261029-03", out)
        self.assertIn("совпадений нет", out)

    def test_match_requires_half_of_query_words(self) -> None:
        self.write(render())
        code, out = self.run_tool("match", "роутер собрать релиз", "--today", TODAY.isoformat())
        self.assertEqual(code, 0, out)
        self.assertNotIn("I-20261029-03", out)

    def test_match_hides_record_below_threshold(self) -> None:
        # 0 подтверждений, пустая проверка: 0 / 2 = 0 — ниже порога 0,5
        self.write(render(confirmations="0", check=""))
        code, out = self.run_tool("match", "перезапустить сервис на роутере", "--today", TODAY.isoformat())
        self.assertEqual(code, 0, out)
        self.assertNotIn("I-20261029-03", out)

    def test_match_sorted_by_confidence(self) -> None:
        self.write(render("I-20261029-03", confirmations="1"), "I-20261029-03.md")
        self.write(render("I-20261029-04", confirmations="5"), "I-20261029-04.md")
        code, out = self.run_tool("match", "перезапустить сервис на роутере", "--today", TODAY.isoformat())
        self.assertEqual(code, 0, out)
        self.assertLess(out.index("I-20261029-04"), out.index("I-20261029-03"))

    def test_match_word_forms_are_compared_loosely(self) -> None:
        self.write(render(condition="роутер Keenetic перезапускает сервис"))
        code, out = self.run_tool("match", "перезапустить сервис", "--today", TODAY.isoformat())
        self.assertEqual(code, 0, out)
        self.assertIn("I-20261029-03", out)

    def test_match_does_not_confuse_different_roots(self) -> None:
        self.write(render())
        code, out = self.run_tool("match", "сервер релиза", "--today", TODAY.isoformat())
        self.assertEqual(code, 0, out)
        self.assertNotIn("I-20261029-03", out)

    # ------------------------------------------------------- записи репозитория

    def test_real_record_is_valid(self) -> None:
        code, out = self.run_repo("check")
        self.assertEqual(code, 0, out)
        self.assertIn("I-20261029-03", out)

    def test_real_record_matches_router_task(self) -> None:
        """AC-5 на настоящих данных: дата отсчёта фиксирована, чтобы тест не старел."""
        code, out = self.run_repo("match", "перезапустить сервис на роутере", "--today", TODAY.isoformat())
        self.assertEqual(code, 0, out)
        self.assertIn("I-20261029-03", out)

    def test_real_record_does_not_match_foreign_task(self) -> None:
        """AC-6 на настоящих данных."""
        code, out = self.run_repo("match", "собрать релиз", "--today", TODAY.isoformat())
        self.assertEqual(code, 0, out)
        self.assertNotIn("I-20261029-03", out)

    # ------------------------------------------------------------- explain

    def test_explain_shows_fields_and_formula(self) -> None:
        self.write(render(confirmations="3"))
        code, out = self.run_tool("explain", "I-20261029-03", "--today", TODAY.isoformat())
        self.assertEqual(code, 0, out)
        for field in ("Условие", "Правило", "Область", "Подтверждений", "Опровержений",
                      "Последнее", "Проверка", "Доказательства", "Статус"):
            self.assertIn(field, out)
        self.assertIn("= 3 / (3 + 0 + 2)", out)
        self.assertIn("+0,200", out)
        self.assertIn("0,800", out)

    def test_explain_marks_record_below_threshold(self) -> None:
        self.write(render(confirmations="0", check=""))
        code, out = self.run_tool("explain", "I-20261029-03", "--today", TODAY.isoformat())
        self.assertEqual(code, 0, out)
        self.assertIn("не показывается в match", out)

    def test_explain_unknown_id_fails(self) -> None:
        self.write(render())
        code, out = self.run_tool("explain", "I-20200101-01", "--today", TODAY.isoformat())
        self.assertEqual(code, 1, out)
        self.assertIn("I-20200101-01", out)

    # ------------------------------------------------------- разбор формата

    def test_parser_keeps_evidence_and_numbers(self) -> None:
        self.write(render(evidence=("journal:L-20261029-03", "docs/NOTE.md"), confirmations="2"))
        record = self.load()[0]
        self.assertEqual(record.id, "I-20261029-03")
        self.assertEqual(record.slug, "keenetic-no-pkill")
        self.assertEqual(record.evidence, ["journal:L-20261029-03", "docs/NOTE.md"])
        self.assertEqual(record.get("Подтверждений"), "2")
        self.assertEqual(instincts.parse_count(record.get("Подтверждений")), 2)

    def test_confidence_is_clamped_to_unit_interval(self) -> None:
        self.write(render(confirmations="100", check=""))
        record = self.load()[0]
        self.assertLessEqual(record.confidence(TODAY), 1.0)
        self.assertGreaterEqual(record.confidence(TODAY), 0.0)


if __name__ == "__main__":
    unittest.main()
