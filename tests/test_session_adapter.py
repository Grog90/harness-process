"""Адаптер DSH→harness: ядро проверяется node --test, Python — обёртка."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugins", "harness-session")


class SessionAdapterTests(unittest.TestCase):
    def test_node_core_tests(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node недоступен")
        proc = subprocess.run(
            [node, "--test", os.path.join(PLUGIN, "session-core.test.mjs")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=ROOT,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_index_syntax(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node недоступен")
        proc = subprocess.run(
            [node, "--check", os.path.join(PLUGIN, "index.js")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
