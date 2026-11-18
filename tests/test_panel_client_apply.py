"""Smoke: client-плагин применяется и регистрирует dock+main."""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PanelClientApplyTests(unittest.TestCase):
    def test_apply_registers_both_slots(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node недоступен")
        proc = subprocess.run([node, os.path.join(ROOT, "tests", "panel_client_apply.test.mjs")],
                              cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
