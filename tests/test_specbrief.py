"""Мост change -> бриф: разбор спеки, границы записи, валидность результата."""

from __future__ import annotations

import os
import tempfile
import unittest

from harness import brief as brief_mod
from harness import specbrief

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SPEC_SAMPLE = """# Spec Delta

## Purpose

Проверка разбора.

## ADDED Requirements

### Requirement: Слот выдаётся

Система ДОЛЖНА (SHALL) выдать слот.

#### Scenario: Первый лейн
- **WHEN** создаётся лейн
- **THEN** он получает слот 0

#### Scenario: Второй лейн
- **WHEN** создаётся второй лейн
- **THEN** он получает слот 1

### Requirement: Слот освобождается

Система ДОЛЖНА (MUST) освободить слот.

#### Scenario: Удаление
- **WHEN** лейн удалён
- **THEN** слот свободен
"""

TASKS_SAMPLE = """# Tasks

- [ ] 1.1 Правка в `harness/lanes.py`, тест `tests.test_lanes`, доки `docs/RULES_MATRIX.md`
- [ ] 1.2 Функция `resources_for` и `lane_slot`
"""


class ParseScenariosTest(unittest.TestCase):
    def test_groups_scenarios_by_requirement(self) -> None:
        scenarios = specbrief.parse_scenarios(SPEC_SAMPLE)
        self.assertEqual(len(scenarios), 3)
        self.assertEqual(scenarios[0]["requirement"], "Слот выдаётся")
        self.assertEqual(scenarios[0]["scenario"], "Первый лейн")
        self.assertEqual(scenarios[0]["when"], "создаётся лейн")
        self.assertEqual(scenarios[0]["then"], "он получает слот 0")
        self.assertEqual(scenarios[2]["requirement"], "Слот освобождается")

    def test_statement_keeps_both_halves(self) -> None:
        scenario = specbrief.parse_scenarios(SPEC_SAMPLE)[0]
        text = specbrief.statement(scenario)
        self.assertIn("создаётся лейн", text)
        self.assertIn("он получает слот 0", text)

    def test_missing_then_is_named_not_dropped(self) -> None:
        broken = "### Requirement: X\n\n#### Scenario: Y\n- **WHEN** что-то\n"
        scenario = specbrief.parse_scenarios(broken)[0]
        self.assertEqual(scenario["then"], "")
        self.assertIn("результат не задан", specbrief.statement(scenario))


class ParsePathsTest(unittest.TestCase):
    def test_keeps_code_and_skips_docs(self) -> None:
        paths = specbrief.parse_paths(TASKS_SAMPLE)
        self.assertIn("harness/lanes.py", paths)
        self.assertNotIn("docs/RULES_MATRIX.md", paths)

    def test_test_modules_become_paths(self) -> None:
        self.assertEqual(specbrief.parse_test_modules(TASKS_SAMPLE), ["tests/test_lanes.py"])

    def test_identifiers_are_snake_case_only(self) -> None:
        names = specbrief.parse_identifiers(TASKS_SAMPLE)
        self.assertIn("resources_for", names)
        self.assertIn("lane_slot", names)
        self.assertNotIn("harness/lanes.py", names)


class BuildTest(unittest.TestCase):
    def test_missing_specs_is_explained(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            change = os.path.join(tmp, "openspec", "changes", "x")
            os.makedirs(change)
            with open(os.path.join(change, "proposal.md"), "w", encoding="utf-8") as fh:
                fh.write("## Why\n\nНадо.\n")
            with open(os.path.join(change, "tasks.md"), "w", encoding="utf-8") as fh:
                fh.write("- [ ] 1.1 `harness/lanes.py`\n")
            with self.assertRaises(specbrief.SpecBriefError):
                specbrief.build(tmp, "x")

    def test_real_change_builds_valid_brief(self) -> None:
        change = "add-lane-test-resources"
        if not os.path.isdir(specbrief.change_path(ROOT, change)):
            self.skipTest("change отсутствует")
        brief = specbrief.build(ROOT, change, base_commit="deadbeef")

        self.assertEqual(len(brief["acceptance"]["criteria"]), len(brief["acceptance"]["required_scenarios"]))
        self.assertIn("harness/lanes.py", brief["allowed_files"])
        self.assertIn("tests/test_lanes_resources.py", brief["allowed_files"])
        self.assertTrue(brief["spec_version"].startswith(f"openspec:{change}@"))
        self.assertEqual(brief["base_commit"], "deadbeef")

        checked = brief_mod.validate(brief)
        self.assertTrue(checked.valid, [i.to_dict() for i in checked.errors])

    def test_every_criterion_has_a_command(self) -> None:
        change = "add-lane-test-resources"
        if not os.path.isdir(specbrief.change_path(ROOT, change)):
            self.skipTest("change отсутствует")
        brief = specbrief.build(ROOT, change)
        for criterion in brief["acceptance"]["criteria"]:
            self.assertTrue(criterion["check"], criterion["id"])


if __name__ == "__main__":
    unittest.main()
