"""Multi-service e2e на копиях сервисов."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import e2e  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class E2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="e2e-root-")
        self.api = os.path.join(self.root, "services", "api")
        self.web = os.path.join(self.root, "services", "web")
        for path, name in ((self.api, "api"), (self.web, "web")):
            os.makedirs(path, exist_ok=True)
            with open(os.path.join(path, f"{name}.txt"), "w", encoding="utf-8") as fh:
                fh.write(name)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_parse_services(self) -> None:
        out = e2e.parse_services(["api=services/api@abc", "web=services/web"])
        self.assertEqual(out[0]["name"], "api")
        self.assertEqual(out[0]["sha"], "abc")
        with self.assertRaises(e2e.E2EError):
            e2e.parse_services(["bad"])
        with self.assertRaises(e2e.E2EError):
            e2e.parse_services([])

    def test_run_passes_env_and_records_report(self) -> None:
        code = (
            "import os,sys;"
            "assert os.path.isdir(os.environ['HARNESS_E2E_API_DIR']);"
            "assert os.path.isdir(os.environ['HARNESS_E2E_WEB_DIR']);"
            "print(os.environ['HARNESS_E2E_API_SHA'])"
        )
        command = f'"{sys.executable}" -c "{code}"'
        services = [
            {"name": "api", "path": self.api, "sha": "a" * 40},
            {"name": "web", "path": self.web, "sha": "b" * 40},
        ]
        out = e2e.run(self.root, "chg-x", command, services)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["exit_code"], 0)
        self.assertEqual(len(out["services"]), 2)
        self.assertTrue(os.path.isfile(out["report"]))
        with open(out["report"], encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["change"], "chg-x")
        self.assertIn("a" * 40, report["stdout_tail"])

    def test_run_failure_is_reported(self) -> None:
        command = f'"{sys.executable}" -c "raise SystemExit(3)"'
        services = [{"name": "api", "path": self.api, "sha": "a" * 40}]
        out = e2e.run(self.root, "chg-y", command, services)
        self.assertFalse(out["ok"])
        self.assertEqual(out["exit_code"], 3)

    def test_missing_service_denied(self) -> None:
        with self.assertRaises(e2e.E2EError):
            e2e.run(self.root, "chg-z", "echo x",
                    [{"name": "api", "path": os.path.join(self.root, "nope"), "sha": ""}])

    def test_bad_change_id_denied(self) -> None:
        with self.assertRaises(e2e.E2EError):
            e2e.run(self.root, "../x", "echo x", [{"name": "api", "path": self.api, "sha": ""}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
