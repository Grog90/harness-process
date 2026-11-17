"""Привязка DSH-сессии к задаче: файл, detach, неизвестная задача."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from harness import session, tmpdir  # noqa: E402
from harness.state import Store, StoreError  # noqa: E402


class SessionAttachTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tmpdir.scratch(ROOT, "session-attach-")
        self.store = Store(os.path.join(self.root, ".harness", "state.db"))

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_attach_existing_task(self) -> None:
        self.store.create_task("T-1", policy_version="1.0.0")
        out = session.attach(self.root, "T-1", self.store)
        self.assertTrue(out["ok"])
        path = session.attach_path(self.root)
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["task_id"], "T-1")
        self.assertTrue(data["auto_dispatch"])
        self.assertIn("created_at", data)
        self.assertEqual(session.read_attach(self.root)["task_id"], "T-1")

    def test_attach_unknown_task_does_not_write(self) -> None:
        with self.assertRaises(StoreError):
            session.attach(self.root, "missing", self.store)
        self.assertIsNone(session.read_attach(self.root))

    def test_detach(self) -> None:
        self.store.create_task("T-2", policy_version="1.0.0")
        session.attach(self.root, "T-2", self.store)
        out = session.detach(self.root)
        self.assertTrue(out["ok"])
        self.assertTrue(out["removed"])
        self.assertIsNone(session.read_attach(self.root))
        self.assertFalse(session.detach(self.root)["removed"])

    def test_cli_status_and_unknown_attach(self) -> None:
        db = os.path.join(self.root, ".harness", "state.db")
        status = subprocess.run(
            [sys.executable, "-m", "harness", "--root", self.root, "--db", db, "--json",
             "session", "status"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIsNone(json.loads(status.stdout)["attach"])
        missing = subprocess.run(
            [sys.executable, "-m", "harness", "--root", self.root, "--db", db,
             "session", "attach", "--task", "missing"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertIsNone(session.read_attach(self.root))

        denied = subprocess.run(
            [sys.executable, "-m", "harness", "--root", self.root, "--db", db,
             "session", "attach", "--task", "T-2"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "HARNESS_ROLE": "worker"},
        )
        self.assertNotEqual(denied.returncode, 0)
        self.assertIsNone(session.read_attach(self.root))

        # DSH-сессия не выставляет HARNESS_ROLE: роль живёт в guard-scope.json.
        self.store.create_task("T-3", policy_version="1.0.0")
        with open(os.path.join(self.root, ".harness", "guard-scope.json"), "w", encoding="utf-8") as fh:
            json.dump({"task_id": "T-3", "role": "worker", "allowed_files": []}, fh)
        scoped = subprocess.run(
            [sys.executable, "-m", "harness", "--root", self.root, "--db", db,
             "session", "attach", "--task", "T-3"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertNotEqual(scoped.returncode, 0)
        self.assertIsNone(session.read_attach(self.root))


if __name__ == "__main__":
    unittest.main(verbosity=2)
