"""CI-снимок: отчёт собирается без запуска тестов и с ним."""
from __future__ import annotations

import os
import sys
import unittest
import unittest.mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from harness import ci  # noqa: E402


class CiTests(unittest.TestCase):
    def test_collect_without_tests(self) -> None:
        report = ci.collect(ROOT, with_tests=False, timeout=60)
        self.assertIsNone(report["tests"])
        self.assertIn("selftest", report)
        self.assertIn("coverage", report)

    def test_collect_with_tests_calls_runner(self) -> None:
        fake = {"ok": True, "exit_code": 0, "duration_s": 0.0, "tail": ""}
        with unittest.mock.patch.object(ci, "run_tests", return_value=fake) as runner:
            report = ci.collect(ROOT, with_tests=True, timeout=60)
        runner.assert_called_once()
        self.assertTrue(report["tests"]["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
