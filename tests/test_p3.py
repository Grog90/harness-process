"""P3: PII-редакция и SLO."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import pii, slo  # noqa: E402


class PiiTests(unittest.TestCase):
    def test_scan_finds_classes(self) -> None:
        text = ("mail a.b@example.com тел +7 999 123-45-67 "
                "карта 4111 1111 1111 1111 jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc12345")
        counts = pii.scan(text)
        self.assertEqual(counts["email"], 1)
        self.assertEqual(counts["phone"], 1)
        self.assertEqual(counts["card"], 1)
        self.assertEqual(counts["jwt"], 1)

    def test_redact_removes_values(self) -> None:
        text = "a@b.ru +7 999 123-45-67 4111111111111111"
        out = pii.redact(text)
        self.assertNotIn("a@b.ru", out)
        self.assertNotIn("4111111111111111", out)
        self.assertIn("[REDACTED:email]", out)
        self.assertIn("[REDACTED:card]", out)

    def test_private_key_block(self) -> None:
        text = "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----"
        self.assertEqual(pii.scan(text)["private_key"], 1)
        self.assertNotIn("secret", pii.redact(text))

    def test_report_clean(self) -> None:
        report = pii.report("обычный текст без данных")
        self.assertTrue(report["ok"])
        self.assertEqual(report["total"], 0)


    def test_scan_clean_text(self) -> None:
        self.assertTrue(pii.report("просто текст")["ok"])

    def test_cli_requires_input_and_refuses_overwrite(self) -> None:
        import subprocess

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.run([sys.executable, "-m", "harness", "--root", root, "pii", "scan"],
                              cwd=root, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        self.assertNotEqual(proc.returncode, 0)
        with open(os.path.join(root, "tests", "test_p3.py"), encoding="utf-8") as fh:
            path = fh.name
        proc = subprocess.run([sys.executable, "-m", "harness", "--root", root, "pii", "redact",
                               "--file", path, "--out", path],
                              cwd=root, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("перезаписывать", proc.stderr)

    def test_cli_slo_json(self) -> None:
        import json
        import subprocess

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.run([sys.executable, "-m", "harness", "--root", root, "--json", "slo"],
                              cwd=root, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertIn("rows", data)
        self.assertIn("confirmed", data)


class SloTests(unittest.TestCase):
    def _data(self, **over):
        data = {
            "summary": {"changes": 10, "changes_accepted": 8, "open_findings": 2},
            "attempts": {"rework_rate": 0.2},
            "verification": {"reports": 5, "ok": 5},
        }
        data.update(over)
        return data

    def test_pass(self) -> None:
        report = slo.evaluate(self._data())
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["breached"], [])

    def test_breach(self) -> None:
        report = slo.evaluate(self._data(summary={"changes": 10, "changes_accepted": 1,
                                                  "open_findings": 2}))
        self.assertFalse(report["ok"])
        self.assertIn("acceptance_rate", report["breached"])

    def test_empty_is_na_not_failure(self) -> None:
        report = slo.evaluate({"summary": {}, "attempts": {}, "verification": {}})
        self.assertTrue(report["ok"])
        self.assertEqual(set(report["na"]),
                         {"acceptance_rate", "rework_rate", "verification_pass", "open_findings"})

    def test_render_marks_breach(self) -> None:
        report = slo.evaluate(self._data(attempts={"rework_rate": 0.9}))
        self.assertIn("BREACH", slo.render_text(report))


if __name__ == "__main__":
    unittest.main(verbosity=2)
