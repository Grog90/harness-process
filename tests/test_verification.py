"""Baseline/candidate: разбор прогона, diff и отказ на нулевом прогоне."""

from __future__ import annotations

import json
import os
import shutil
import unittest

from harness import tmpdir, verification
from harness.state import Store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHA = "b" * 40


class ParseTest(unittest.TestCase):
    def test_pass_and_counts(self) -> None:
        out = verification.parse_output("Ran 3 tests in 0.1s\n\nOK\n", 0)
        self.assertEqual(out["result"], "pass")
        self.assertEqual(out["tests_ran"], 3)
        self.assertEqual(out["failures"], [])

    def test_failures_are_named(self) -> None:
        out = verification.parse_output("Ran 2 tests\n\nFAIL: test_x (tests.test_a)\nERROR: test_y\n", 1)
        self.assertEqual(out["result"], "fail")
        self.assertEqual(out["tests_ran"], 2)
        self.assertEqual(sorted(out["failures"]), ["test_x", "test_y"])

    def test_timeout_is_not_failure(self) -> None:
        out = verification.parse_output("Ran 1 tests", -1, timed_out=True)
        self.assertEqual(out["result"], "timeout")


class DiffTest(unittest.TestCase):
    def test_new_failure_is_regression(self) -> None:
        diff = verification.diff_results(
            {"result": "pass", "failures": []},
            {"result": "fail", "failures": ["test_x"]},
        )
        self.assertTrue(diff["regression"])
        self.assertEqual(diff["new_failures"], ["test_x"])

    def test_fixed_failure_is_not_regression(self) -> None:
        diff = verification.diff_results(
            {"result": "fail", "failures": ["test_x"]},
            {"result": "pass", "failures": []},
        )
        self.assertFalse(diff["regression"])
        self.assertEqual(diff["fixed"], ["test_x"])


class RecordTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tmpdir.scratch(ROOT, "harness-verify-")
        self.store = Store(os.path.join(self.root, ".harness", "state.db"))
        self.store.create_change("chg-v", owner="o", spec_ref=SHA)

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_report_requires_versions_and_nonzero_runs(self) -> None:
        report = {
            "change_id": "chg-v",
            "revision": 1,
            "spec_ref": SHA,
            "environment": "local",
            "runner": "unittest",
            "services": [{"name": "repo", "sha": SHA}],
            "results": {
                "baseline": {"command": "echo Ran 2 tests", "tests_ran": 2, "result": "pass"},
                "candidate": {"command": "echo Ran 2 tests", "tests_ran": 2, "result": "pass"},
            },
            "diff": {"new_failures": []},
        }
        check = verification.check_report(self.store, self.root, "chg-v", report=report)
        self.assertTrue(check["ok"], check["problems"])

        broken = json.loads(json.dumps(report))
        broken["results"]["candidate"]["tests_ran"] = 0
        check = verification.check_report(self.store, self.root, "chg-v", report=broken)
        self.assertFalse(check["ok"])
        self.assertFalse(verification.check_zero(broken)["ok"])

    def test_multi_service_and_repo_runs(self) -> None:
        service_dir = os.path.join(self.root, "services", "api")
        os.makedirs(service_dir, exist_ok=True)
        command = 'echo Ran 4 tests && echo OK'
        service = [{"name": "api", "sha": "d" * 40}]
        for mode in ("baseline", "candidate"):
            report = verification.record(
                self.store, self.root, "chg-v", mode, command,
                environment="local", runner="cmd", services=service,
                service="api", cwd=service_dir,
            )
        self.assertIn("api:baseline", report["results"])
        self.assertIn("api:candidate", report["results"])
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual(report["diffs"]["api"]["new_failures"], [])
        command = 'echo Ran 2 tests && echo OK'
        report = verification.record(
            self.store, self.root, "chg-v", "baseline", command,
            environment="local", runner="cmd", services=[{"name": "repo", "sha": SHA}],
        )
        self.assertEqual(report["results"]["baseline"]["result"], "pass")
        self.assertEqual(report["results"]["baseline"]["tests_ran"], 2)
        report = verification.record(
            self.store, self.root, "chg-v", "candidate", command,
            environment="local", runner="cmd", services=[{"name": "repo", "sha": SHA}],
        )
        self.assertTrue(report["ok"], report["problems"])
        path = os.path.join(self.root, "openspec", "changes", "chg-v", verification.VERIFY_FILE)
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(os.path.isfile(path.replace(".json", ".md")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
