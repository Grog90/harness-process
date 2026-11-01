"""Проверки tools/push_facts.py: план отправки, состояние, разбор правил."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "push_facts.py"
_spec = importlib.util.spec_from_file_location("push_facts_tool", TOOL)
push = importlib.util.module_from_spec(_spec)
sys.modules["push_facts_tool"] = push
_spec.loader.exec_module(push)  # type: ignore[union-attr]


class PushFactsTest(unittest.TestCase):
    def test_plan_skips_unchanged(self) -> None:
        facts = [{"key": "journal:L-1", "content": "правило"}]
        state = {"journal:L-1": push.hashlib.sha1("правило".encode()).hexdigest()}
        fresh, skipped = push.plan(facts, state)
        self.assertEqual(fresh, [])
        self.assertEqual(skipped, 1)

    def test_plan_detects_changed_text(self) -> None:
        facts = [{"key": "journal:L-1", "content": "новое правило"}]
        state = {"journal:L-1": push.hashlib.sha1("старое правило".encode()).hexdigest()}
        fresh, skipped = push.plan(facts, state)
        self.assertEqual(len(fresh), 1)
        self.assertEqual(skipped, 0)
        self.assertIn("digest", fresh[0])

    def test_state_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original = push.STATE
            push.STATE = Path(tmp) / "state.json"
            try:
                push.save_state({"a": "b"})
                self.assertEqual(push.load_state(), {"a": "b"})
                push.STATE.write_text("{битый json", encoding="utf-8")
                self.assertEqual(push.load_state(), {})
            finally:
                push.STATE = original

    def test_agents_facts_are_unique_and_atomic(self) -> None:
        facts = push.build_agents_facts()
        self.assertGreaterEqual(len(facts), 5)
        keys = [fact["key"] for fact in facts]
        self.assertEqual(len(keys), len(set(keys)))
        for fact in facts:
            self.assertTrue(fact["content"].endswith("."))
            self.assertLess(len(fact["content"]), 600)

    def test_docs_facts_are_atomic(self) -> None:
        facts = push.build_docs_facts()
        self.assertGreaterEqual(len(facts), 10)
        keys = [fact["key"] for fact in facts]
        self.assertEqual(len(keys), len(set(keys)))
        for fact in facts:
            self.assertGreaterEqual(len(fact["content"]), 50)
            self.assertLess(len(fact["content"]), 600)
            self.assertTrue(fact["content"].endswith("."))


if __name__ == "__main__":
    unittest.main()
