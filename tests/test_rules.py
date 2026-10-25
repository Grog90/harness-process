"""Матрица правил: делегирование — обещание, которое надо проверять."""

from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from harness import rules  # noqa: E402

from harness import tmpdir  # noqa: E402


def registry() -> dict:
    return {
        "rules": [
            {
                "id": "R-GIT-1",
                "statement": "разрушительный git",
                "event": "pretooluse",
                "enforcement": "delegated",
                "delegated_to": "dsh-jev-guard (L0)",
                "implemented": False,
                "hook": None,
            },
            {
                "id": "R-READ-1",
                "statement": "read-before-edit",
                "event": "pretooluse",
                "enforcement": "delegated",
                "delegated_to": "@deepseek-ai/dsh-fs-observation-policy",
                "implemented": False,
                "hook": None,
            },
        ]
    }


class DelegationTests(unittest.TestCase):
    def test_external_targets_skip_runtime_packages(self) -> None:
        self.assertEqual(rules.external_targets("dsh-jev-guard (L0)"), ["dsh-jev-guard"])
        self.assertEqual(rules.external_targets("@deepseek-ai/dsh-sandbox-policy + @deepseek-ai/dsh-pwsh-sandbox"), [])
        self.assertEqual(
            rules.external_targets("@deepseek-ai/dsh-tool-call-timeout-policy"),
            [],
        )

    def test_installed_target_keeps_delegated(self) -> None:
        statuses = rules.evaluate(registry(), installed={"dsh-jev-guard"})
        by_id = {s.id: s for s in statuses}
        self.assertEqual(by_id["R-GIT-1"].status, "delegated")

    def test_removed_plugin_turns_delegation_into_gap(self) -> None:
        """Удалили плагин — правило снова пробел, а не «закрыто чужими руками»."""
        statuses = rules.evaluate(registry(), installed=set())
        by_id = {s.id: s for s in statuses}
        self.assertEqual(by_id["R-GIT-1"].status, "gap")
        self.assertIn("не установлено", by_id["R-GIT-1"].reason)
        # встроенный рантайм установки не требует
        self.assertEqual(by_id["R-READ-1"].status, "delegated")

    def test_without_installed_list_nothing_is_downgraded(self) -> None:
        statuses = rules.evaluate(registry())
        self.assertEqual({s.status for s in statuses}, {"delegated"})

    def test_summary_counts_gap_and_delegated(self) -> None:
        sums = rules.summary(rules.evaluate(registry(), installed=set()))
        self.assertEqual(sums["delegated"], 1)
        self.assertEqual(sums["gap"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
