"""Поведенческий тест host-route панели (Node, без сети)."""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PanelHostRouteTests(unittest.TestCase):
    def test_route_200_502_405(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node недоступен")
        proc = subprocess.run([node, os.path.join(ROOT, "tests", "panel_host_route.test.mjs")],
                              cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
