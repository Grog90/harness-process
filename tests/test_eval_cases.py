"""Корпус evals: разметка целостна, кандидаты и брифы на месте.

Кейс — это утверждение о поведении. Если метка ссылается на несуществующий файл или
дублируется, метрики становятся бессмысленными молча, поэтому проверяется структура,
а не только числа.
"""

from __future__ import annotations

import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVALS = os.path.join(ROOT, "examples", "evals")

SUITES = {
    "fee": "fee-brief.json",
    "access": "access-brief.json",
    "duration": "duration-brief.json",
}


def load_suite(suite: str) -> list[dict]:
    base = os.path.join(EVALS, suite)
    cases = []
    for name in sorted(os.listdir(base)):
        manifest = os.path.join(base, name, "case.json")
        if not os.path.isfile(manifest):
            continue
        with open(manifest, encoding="utf-8") as fh:
            data = json.load(fh)
        data["_dir"] = os.path.join(base, name)
        data["_suite"] = suite
        cases.append(data)
    return cases


class CorpusTest(unittest.TestCase):
    def test_every_suite_exists_and_is_labelled(self) -> None:
        for suite in SUITES:
            cases = load_suite(suite)
            self.assertTrue(cases, f"набор {suite} пуст")
            labels = {c["expected"] for c in cases}
            self.assertEqual(labels, {"PASS", "FAIL"}, f"в {suite} нет обеих меток: {labels}")

    def test_manifests_are_complete(self) -> None:
        for suite in SUITES:
            for case in load_suite(suite):
                for field in ("case_id", "expected", "candidate_dir", "brief_file", "note"):
                    self.assertTrue(case.get(field), f"{suite}/{case.get('case_id')}: пустое поле {field}")
                self.assertEqual(case["case_id"], os.path.basename(case["_dir"]))
                self.assertIn(case["expected"], ("PASS", "FAIL"))

    def test_candidate_and_brief_exist(self) -> None:
        for suite in SUITES:
            for case in load_suite(suite):
                candidate = os.path.join(case["_dir"], case["candidate_dir"])
                self.assertTrue(os.path.isdir(candidate), f"{case['case_id']}: нет кандидата")
                modules = [p for p in os.listdir(os.path.join(candidate, "src")) if p.endswith(".py")]
                self.assertTrue(modules, f"{case['case_id']}: в candidate/src нет модулей")
                brief = os.path.normpath(os.path.join(case["_dir"], case["brief_file"]))
                self.assertTrue(os.path.isfile(brief), f"{case['case_id']}: нет брифа {brief}")

    def test_briefs_are_valid(self) -> None:
        from harness.brief import load

        for suite, name in SUITES.items():
            brief = load(os.path.join(EVALS, name))
            self.assertTrue(brief.valid, f"{suite}: {[i.to_dict() for i in brief.errors]}")

    def test_case_ids_are_unique_inside_suite(self) -> None:
        for suite in SUITES:
            ids = [c["case_id"] for c in load_suite(suite)]
            self.assertEqual(len(ids), len(set(ids)), f"{suite}: дубли case_id")

    def test_corpus_is_large_enough_for_metrics(self) -> None:
        rows = [c for suite in SUITES for c in load_suite(suite)]
        defective = [c for c in rows if c["expected"] == "FAIL"]
        correct = [c for c in rows if c["expected"] == "PASS"]
        self.assertGreaterEqual(len(rows), 30, "набор меньше 30 случаев: метрики грубые")
        self.assertGreaterEqual(len(defective), 15)
        self.assertGreaterEqual(len(correct), 10)

    def test_defect_classes_are_varied(self) -> None:
        notes = {c["note"].split(":", 1)[0] for suite in SUITES for c in load_suite(suite)}
        self.assertGreaterEqual(len(notes), 15, f"классов дефектов мало: {sorted(notes)}")


if __name__ == "__main__":
    unittest.main()
