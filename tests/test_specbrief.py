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


class IdsTest(unittest.TestCase):
    def test_explicit_ids_are_preserved(self) -> None:
        text = SPEC_SAMPLE.replace("### Requirement: Слот выдаётся", "### Requirement: REQ-SLOT-1: Слот выдаётся")
        scenarios = specbrief.parse_scenarios(text)
        self.assertEqual(scenarios[0]["requirement_id"], "REQ-SLOT-1")
        self.assertTrue(scenarios[0]["case_id"].startswith("CASE-REQ-SLOT-1-"))
        requirements = specbrief.parse_requirements(text)
        self.assertEqual(requirements[0]["id"], "REQ-SLOT-1")
        self.assertEqual(len(requirements[0]["scenarios"]), 2)

    def test_generated_ids_are_stable_until_reordered(self) -> None:
        first = specbrief.parse_requirements(SPEC_SAMPLE)
        second = specbrief.parse_requirements(SPEC_SAMPLE + "\n### Requirement: Новое\n\n#### Scenario: С\n- **WHEN** x\n- **THEN** y\n")
        self.assertEqual(first[0]["id"], "REQ-1")
        self.assertEqual(first[1]["id"], "REQ-2")
        self.assertEqual(second[0]["id"], "REQ-1")
        self.assertEqual(second[1]["id"], "REQ-2")


class SpecRefBuildTest(unittest.TestCase):
    def test_build_carries_change_id_spec_ref_and_case_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "openspec", "changes", "chg-demo")
            os.makedirs(os.path.join(base, "specs", "cap"))
            with open(os.path.join(base, "proposal.md"), "w", encoding="utf-8") as fh:
                fh.write("## Why\n\nНадо.\n")
            with open(os.path.join(base, "specs", "cap", "spec.md"), "w", encoding="utf-8") as fh:
                fh.write("### Requirement: REQ-D-1: Слот\n\n#### Scenario: CASE-D-1: Выдача\n"
                         "- **WHEN** создаётся лейн\n- **THEN** слот 0\n")
            with open(os.path.join(base, "tasks.md"), "w", encoding="utf-8") as fh:
                fh.write("- [ ] 1.1 Правка `harness/lanes.py`, проверка `python -m unittest tests.test_x`\n")
            brief = specbrief.build(tmp, "chg-demo", spec_ref="a" * 40, base_commit="b" * 40)
            self.assertEqual(brief["change_id"], "chg-demo")
            self.assertEqual(brief["spec_ref"], "a" * 40)
            self.assertEqual(brief["acceptance"]["criteria"][0]["req_id"], "REQ-D-1")
            self.assertEqual(brief["acceptance"]["criteria"][0]["case_id"], "CASE-D-1")
            checked = brief_mod.validate(brief)
            self.assertTrue(checked.valid, [i.to_dict() for i in checked.errors])


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


    def test_services_are_carried_and_validated(self) -> None:
        change = "add-multi-service"
        if not os.path.isdir(specbrief.change_path(ROOT, change)):
            self.skipTest("change отсутствует")
        brief = specbrief.build(ROOT, change, services=[
            {"name": "api", "path": "services/api", "base_commit": "a" * 40, "write": True},
        ])
        self.assertEqual(brief["services"][0]["name"], "api")
        brief["allowed_files"] = ["service:api/src/a.py"]
        checked = brief_mod.validate(brief)
        self.assertTrue(checked.valid, [i.to_dict() for i in checked.errors])

        brief["services"][0]["write"] = False
        checked = brief_mod.validate(brief)
        self.assertFalse(checked.valid)
        self.assertIn("E_CONTRADICTION", [i.code for i in checked.errors])

        brief["services"][0]["write"] = True
        brief["allowed_files"] = ["service:api/src/../a.py"]
        checked = brief_mod.validate(brief)
        self.assertFalse(checked.valid)


if __name__ == "__main__":
    unittest.main()
