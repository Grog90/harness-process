"""Привязка DSH-сессии к задаче: файл, detach, неизвестная задача."""
from __future__ import annotations

import json
import os
import shutil
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
