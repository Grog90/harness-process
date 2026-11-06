#!/usr/bin/env python3
"""Проверки tools/cost_tokens.py: веса, ошибки usage, запись и доля накладных.

Тесты не ходят в сеть и не трогают рабочее состояние: журнал — во временном
каталоге, `usage` — во временном файле или в памяти.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import cost_tokens as ct  # noqa: E402
from harness.state import Store  # noqa: E402


def sample_usage(**overrides):
    usage = {
        "input_tokens": 1000,
        "cache_read_input_tokens": 10000,
        "cache_creation_input_tokens": 2000,
        "output_tokens": 500,
    }
    usage.update(overrides)
    return usage


class TokenTests(unittest.TestCase):
    def test_default_weights_are_applied(self) -> None:
        self.assertEqual(ct.effective_tokens(sample_usage()), 7500.0)

    def test_weights_can_be_replaced(self) -> None:
        weights = {"input": 1.0, "cache_read": 0.0, "cache_write": 0.0, "output": 1.0}
        self.assertEqual(ct.effective_tokens(sample_usage(), weights), 1500.0)

    def test_weights_must_be_complete(self) -> None:
        with self.assertRaises(ct.UsageError) as caught:
            ct.effective_tokens(
                sample_usage(), {"input": 1.0, "cache_read": 1.0, "cache_write": 1.0}
            )
        self.assertIn("output", str(caught.exception))

    def test_missing_input_is_an_error(self) -> None:
        with self.assertRaises(ct.UsageError) as caught:
            ct.normalize_usage({"output_tokens": 5})
        self.assertIn("input_tokens", str(caught.exception))

    def test_negative_value_is_an_error(self) -> None:
        with self.assertRaises(ct.UsageError) as caught:
            ct.effective_tokens(sample_usage(output_tokens=-1))
        self.assertIn("output_tokens", str(caught.exception))

    def test_bool_is_not_a_number(self) -> None:
        with self.assertRaises(ct.UsageError) as caught:
            ct.normalize_usage({"input_tokens": True})
        self.assertIn("input_tokens", str(caught.exception))

    def test_missing_optional_fields_are_zero(self) -> None:
        normalized = ct.normalize_usage({"input_tokens": 1000})
        self.assertEqual(
            normalized,
            {"input": 1000.0, "cache_read": 0.0, "cache_write": 0.0, "output": 0.0},
        )
        self.assertEqual(ct.effective_tokens({"input_tokens": 1000}), 1000.0)

    def test_wrapper_is_accepted(self) -> None:
        wrapped = {"usage": sample_usage()}
        self.assertEqual(ct.effective_tokens(wrapped), 7500.0)

    def test_broken_json_is_an_error(self) -> None:
        with self.assertRaises(ct.UsageError):
            ct.parse_usage("{не json")

    def test_cli_effective_prints_json(self) -> None:
        root = tempfile.mkdtemp()
        try:
            path = os.path.join(root, "usage.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(sample_usage(), handle)
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = ct.main(["effective", path])
            self.assertEqual(code, 0)
            result = json.loads(buffer.getvalue())
            self.assertEqual(result["tokens"], 7500.0)
            self.assertEqual(result["unit"], "token_equiv")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_cli_error_returns_one(self) -> None:
        root = tempfile.mkdtemp()
        try:
            path = os.path.join(root, "usage.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('{"output_tokens": 1}')
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = ct.main(["effective", path])
            self.assertEqual(code, 1)
            self.assertIn("input_tokens", err.getvalue())
            self.assertEqual(out.getvalue(), "")
        finally:
            shutil.rmtree(root, ignore_errors=True)


class _StoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.root, ".harness", "state.db"))

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)


class RecordTests(_StoreCase):
    def test_record_writes_token_equiv(self) -> None:
        result = ct.record_cost(
            self.store, "T1", sample_usage(), kind="worker", attempt=2
        )
        entries = self.store.costs_for("T1")
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["unit"], "token_equiv")
        self.assertEqual(entry["kind"], "worker")
        self.assertEqual(entry["attempt"], 2)
        self.assertEqual(entry["amount"], result["tokens"])
        self.assertEqual(result["tokens"], 7500.0)
        self.assertEqual(self.store.cost_total("T1", "token_equiv"), 7500.0)
        self.assertIn("weights=default", entry["note"])

    def test_token_budget_blocks_dispatch(self) -> None:
        ct.record_cost(self.store, "T1", {"input_tokens": 60000})
        from harness import cost

        brief = {
            "control": {
                "budget": {
                    "max_cost": 100000,
                    "estimate_per_attempt": 50000,
                    "unit": "token_equiv",
                }
            }
        }
        result = cost.check_dispatch(self.store, "T1", brief)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["spent"], 60000.0)
        self.assertIn("остаток", result["reason"])

    def test_foreign_unit_is_not_summed(self) -> None:
        ct.record_cost(self.store, "T1", {"input_tokens": 5}, unit="cost_units")
        from harness import cost

        brief = {
            "control": {
                "budget": {
                    "max_cost": 100000,
                    "estimate_per_attempt": 50000,
                    "unit": "token_equiv",
                }
            }
        }
        result = cost.check_dispatch(self.store, "T1", brief)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["spent"], 0.0)

    def test_broken_usage_writes_nothing(self) -> None:
        with self.assertRaises(ct.UsageError):
            ct.record_cost(self.store, "T1", sample_usage(output_tokens=-5))
        self.assertEqual(self.store.costs_for("T1"), [])

    def test_by_kind_ignores_foreign_unit(self) -> None:
        ct.record_cost(self.store, "T1", {"input_tokens": 10}, kind="worker")
        ct.record_cost(self.store, "T1", {"input_tokens": 10}, kind="worker", unit="cost_units")
        self.assertEqual(ct.by_kind(self.store.costs_for("T1")), {"worker": 10.0})


class ShareTests(_StoreCase):
    def test_overhead_is_separated(self) -> None:
        ct.record_cost(self.store, "T1", {"input_tokens": 7000}, kind="worker")
        ct.record_cost(self.store, "T1", {"input_tokens": 3000}, kind="verifier")
        share = ct.overhead_share(self.store.costs_for("T1"))
        self.assertEqual(share["work"], 7000.0)
        self.assertEqual(share["overhead"], 3000.0)
        self.assertEqual(share["overhead_frac"], 0.3)
        self.assertEqual(share["entries"], 2)

    def test_empty_selection_has_null_fraction(self) -> None:
        share = ct.overhead_share([])
        self.assertIsNone(share["overhead_frac"])
        self.assertEqual(share["work"], 0.0)
        self.assertEqual(share["overhead"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
