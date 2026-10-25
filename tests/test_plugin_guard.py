"""Тесты плагина без установки: guard регистрируется и реально отказывает.

Установка плагина в профиль — отдельный шаг с approval. Здесь проверяется то,
что можно проверить без неё: `apply()` регистрирует guard, guard читает активную
область прав из управляющего процесса и отвечает теми же правилами, что ядро.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from harness.fsm import Engine  # noqa: E402
from harness.policy import Policy  # noqa: E402
from harness.state import Store  # noqa: E402

PLUGIN = os.path.join(ROOT, "plugins", "harness-guard")
PROBE = os.path.join(PLUGIN, "guard-probe.mjs")
NODE = os.path.join(os.path.expanduser("~"), ".dsh", "dsh-runtimes", "dsh-primary-runtime",
                    "dependencies", "node", "bin", "node.exe")


def brief_data(task_id: str = "T-GUARD") -> dict:
    return {
        "task_id": task_id,
        "brief_version": 1,
        "base_commit": "abc",
        "spec_version": "s1",
        "goal": "g",
        "expected_behavior": "b",
        "allowed_files": ["src/a.py"],
        "contract": {"names": ["f"], "required_fields": ["x"], "error_codes": ["E"]},
        "acceptance": {"criteria": [{"id": "AC-1", "statement": "s", "mandatory": True, "check": "pytest"}],
                       "check_commands": ["pytest"]},
        "control": {"attempt_limit": 3, "open_questions": []},
    }


@unittest.skipUnless(os.path.isfile(NODE), "node не найден: плагин не проверить")
class PluginGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="harness-plugin-")
        shutil.copytree(os.path.join(ROOT, "policies"), os.path.join(self.root, "policies"))
        os.makedirs(os.path.join(self.root, "src"), exist_ok=True)
        os.makedirs(os.path.join(self.root, ".harness"), exist_ok=True)
        self.store = Store(os.path.join(self.root, ".harness", "state.db"))
        self.engine = Engine(self.store, Policy.load(self.root), self.root)

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def _probe(self, calls: list[dict]) -> list[dict]:
        payload = json.dumps({"root": self.root, "calls": calls})
        proc = subprocess.run(
            [NODE, PROBE], input=payload, capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=PLUGIN, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[:400])
        return json.loads(proc.stdout)

    def _write_scope(self, allowed: list[str]) -> None:
        path = os.path.join(self.root, ".harness", "guard-scope.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"task_id": "T-GUARD", "role": "worker", "allowed_files": allowed,
                       "attempt": 1}, fh)

    def test_guard_registers_and_denies_outside_scope(self) -> None:
        self._write_scope(["src/a.py"])
        results = self._probe([
            {"name": "write", "arguments": {"path": "src/a.py"}},
            {"name": "write", "arguments": {"path": "src/b.py"}},
            {"name": "pwsh", "arguments": {"command": "sleep 30"}},
        ])
        self.assertIsNone(results[0]["deny"])
        self.assertIn("R-WORK-1", results[1]["deny"])
        self.assertIn("R-SLEEP-1", results[2]["deny"])

    def test_scope_changes_take_effect_without_reload(self) -> None:
        self._write_scope(["src/a.py"])
        first = self._probe([{"name": "write", "arguments": {"path": "src/b.py"}}])
        self.assertIn("R-WORK-1", first[0]["deny"])

        self._write_scope(["src/a.py", "src/b.py"])
        second = self._probe([{"name": "write", "arguments": {"path": "src/b.py"}}])
        self.assertIsNone(second[0]["deny"], second)

    def test_dispatch_publishes_scope_and_verdict_clears(self) -> None:
        self.store.create_task("T-GUARD", policy_version="1.0.0")
        self.engine.apply("T-GUARD", "attach_brief", {"brief": brief_data()})
        self.engine.apply("T-GUARD", "dispatch", {"worker_id": "w"})

        scope_file = os.path.join(self.root, ".harness", "guard-scope.json")
        self.assertTrue(os.path.isfile(scope_file))
        with open(scope_file, encoding="utf-8") as fh:
            scope = json.load(fh)
        self.assertEqual(scope["allowed_files"], ["src/a.py"])
        self.assertEqual(scope["role"], "worker")

        self.engine.apply("T-GUARD", "submit", {"attempt": 1, "snapshot_id": "s1"})
        self.engine.apply("T-GUARD", "verdict", {
            "verdict": {
                "verdict_id": "v1", "task_id": "T-GUARD", "attempt": 1, "snapshot_id": "s1",
                "verdict": "PASS", "reviewer_id": "r", "reviewer_role": "verifier",
                "criteria": [{"id": "AC-1", "result": "pass", "evidence": "ok"}],
            }
        })
        self.assertFalse(os.path.isfile(scope_file), "после вердикта область прав должна сниматься")


if __name__ == "__main__":
    unittest.main(verbosity=2)
