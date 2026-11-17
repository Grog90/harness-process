"""Forge: статус PR и checks."""

from __future__ import annotations

import os
import shutil
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import forge  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ForgeTests(unittest.TestCase):
    def test_summarize_success(self) -> None:
        out = forge.summarize_checks({"statusCheckRollup": [
            {"name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"},
        ], "number": 1, "url": "u", "state": "OPEN"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["number"], 1)

    def test_summarize_failure_and_pending(self) -> None:
        out = forge.summarize_checks({"statusCheckRollup": [
            {"name": "ci", "status": "COMPLETED", "conclusion": "FAILURE"},
            {"name": "lint", "status": "IN_PROGRESS"},
        ]})
        self.assertFalse(out["ok"])
        self.assertEqual(out["failing"], ["ci"])
        self.assertEqual(out["pending"], ["lint"])

    def test_summarize_status_context(self) -> None:
        out = forge.summarize_checks({"statusCheckRollup": [
            {"context": "legacy-ci", "state": "FAILURE"},
            {"context": "legacy-queue", "state": "PENDING"},
        ]})
        self.assertFalse(out["ok"])
        self.assertEqual(out["failing"], ["legacy-ci"])
        self.assertEqual(out["pending"], ["legacy-queue"])

    def test_wait_timeout_details(self) -> None:
        pending = {"ok": False, "failing": [], "pending": ["ci"], "url": "u"}
        with patch.object(forge, "pr_status", return_value=pending):
            with self.assertRaises(forge.ForgeError) as ctx:
                forge.wait_status(ROOT, "harness/T", timeout=0, interval=0, sleep=lambda _: None)
        self.assertEqual(ctx.exception.details.get("pending"), ["ci"])
        self.assertEqual(ctx.exception.details.get("url"), "u")

    def test_without_gh_is_denied(self) -> None:
        with patch.object(forge.shutil, "which", return_value=None):
            with self.assertRaises(forge.ForgeError) as ctx:
                forge.pr_status(ROOT, "harness/T")
        self.assertEqual(ctx.exception.rule, "R-FORGE-1")

    def test_missing_pr_is_denied(self) -> None:
        with patch.object(forge.shutil, "which", return_value="gh"), \
                patch.object(forge, "_gh_json", return_value=[]):
            with self.assertRaises(forge.ForgeError) as ctx:
                forge.pr_status(ROOT, "harness/T")
        self.assertEqual(ctx.exception.rule, "R-FORGE-1")

    def test_wait_returns_when_checks_finish(self) -> None:
        pending = {"ok": False, "failing": [], "pending": ["ci"]}
        done = {"ok": True, "failing": [], "pending": []}
        with patch.object(forge, "pr_status", side_effect=[pending, done]):
            out = forge.wait_status(ROOT, "harness/T", timeout=5, interval=0, sleep=lambda _: None)
        self.assertTrue(out["ok"])

    def test_wait_timeout_is_denied(self) -> None:
        pending = {"ok": False, "failing": [], "pending": ["ci"], "url": "u"}
        with patch.object(forge, "pr_status", return_value=pending):
            with self.assertRaises(forge.ForgeError):
                forge.wait_status(ROOT, "harness/T", timeout=0, interval=0, sleep=lambda _: None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
