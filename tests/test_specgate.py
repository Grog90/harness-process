"""R-SPEC-1: связь брифа со спекой, границы применимости правила."""

from __future__ import annotations

import os
import tempfile
import unittest

from harness import specgate


class VersionTest(unittest.TestCase):
    def test_openspec_version_yields_change_name(self) -> None:
        self.assertEqual(specgate.change_name("openspec:add-lane-test-resources@8eb0ee1c"), "add-lane-test-resources")

    def test_foreign_version_is_not_a_spec_reference(self) -> None:
        self.assertIsNone(specgate.change_name("spec-fee-3"))
        self.assertIsNone(specgate.change_name(""))

    def test_only_code_counts(self) -> None:
        self.assertEqual(specgate.touches_code(["harness/lanes.py"]), ["harness/lanes.py"])
        self.assertEqual(specgate.touches_code(["docs/RUNNING.md", "tests/test_x.py"]), [])


class GateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _change(self, name: str, archive: bool = False) -> None:
        base = os.path.join(self.root, "openspec", "changes")
        if archive:
            base = os.path.join(base, "archive")
            name = f"2026-10-27-{name}"
        os.makedirs(os.path.join(base, name), exist_ok=True)

    def test_project_without_openspec_is_skipped(self) -> None:
        result = specgate.check(self.root, {}, ["harness/cli.py"])
        self.assertTrue(result["ok"])
        self.assertIn("skipped", result)

    def test_docs_do_not_need_a_spec(self) -> None:
        self._change("add-demo")
        result = specgate.check(self.root, {}, ["docs/RUNNING.md"])
        self.assertTrue(result["ok"])

    def test_code_without_spec_is_denied(self) -> None:
        self._change("add-demo")
        result = specgate.check(self.root, {"spec_version": "spec-1"}, ["harness/cli.py"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["rule"], "R-SPEC-1")

    def test_live_change_satisfies_the_rule(self) -> None:
        self._change("add-demo")
        result = specgate.check(self.root, {"spec_version": "openspec:add-demo@1"}, ["harness/cli.py"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["change"], "add-demo")

    def test_archived_change_still_satisfies_the_rule(self) -> None:
        self._change("add-demo", archive=True)
        result = specgate.check(self.root, {"spec_version": "openspec:add-demo@1"}, ["harness/cli.py"])
        self.assertTrue(result["ok"])

    def test_unknown_change_is_denied(self) -> None:
        self._change("add-demo")
        result = specgate.check(self.root, {"spec_version": "openspec:other@1"}, ["harness/cli.py"])
        self.assertFalse(result["ok"])
        self.assertIn("other", result["reason"])


if __name__ == "__main__":
    unittest.main()
