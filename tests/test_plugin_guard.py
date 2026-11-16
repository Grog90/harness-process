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

from harness import tmpdir  # noqa: E402

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
        self.root = tmpdir.scratch(ROOT, "harness-plugin-")
        shutil.copytree(os.path.join(ROOT, "policies"), os.path.join(self.root, "policies"))
        os.makedirs(os.path.join(self.root, "src"), exist_ok=True)
        os.makedirs(os.path.join(self.root, ".harness"), exist_ok=True)
        self.store = Store(os.path.join(self.root, ".harness", "state.db"))
        self.engine = Engine(self.store, Policy.load(self.root), self.root)

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def _probe(self, calls: list[dict], cwd: str | None = None,
               env: dict[str, str] | None = None) -> list[dict]:
        payload = json.dumps({"root": self.root, "calls": calls})
        run_env = {**os.environ}
        run_env.pop("HARNESS_ROOT", None)
        run_env.pop("HARNESS_ROLE", None)
        run_env.update(env or {})
        proc = subprocess.run(
            [NODE, PROBE], input=payload, capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=cwd or PLUGIN, timeout=60, env=run_env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[:400])
        return json.loads(proc.stdout)

    def _write_scope(self, allowed: list[str], *, role: str = "worker",
                     primary: str = "orch-1", phase: str = "running") -> None:
        path = os.path.join(self.root, ".harness", "guard-scope.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"task_id": "T-GUARD", "role": role, "phase": phase,
                       "primary": primary, "allowed_files": allowed, "attempt": 1}, fh)

    def _ask_probe(self, calls: list[dict]) -> list[dict]:
        payload = json.dumps({"root": self.root, "calls": calls})
        env = {**os.environ}
        env.pop("HARNESS_ROOT", None)
        proc = subprocess.run(
            [NODE, os.path.join(PLUGIN, "ask-probe.mjs")], input=payload,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=PLUGIN, timeout=60, env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[:400])
        return json.loads(proc.stdout)

    def test_gap_turns_write_into_ask_and_read_stays_allowed(self) -> None:
        gap_path = os.path.join(self.root, ".harness", "gap.json")
        with open(gap_path, "w", encoding="utf-8") as fh:
            json.dump({"task_id": "T-GUARD", "gap_id": "GAP-1",
                       "question": "Отменять оплаченный заказ?"}, fh)
        decisions = self._ask_probe([{"name": "write", "arguments": {"path": "src/a.py"}},
                                     {"name": "read", "arguments": {"path": "src/a.py"}}])
        self.assertEqual(decisions[0]["kind"], "ask")
        self.assertIn("R-GAP-2", decisions[0]["reason"])
        self.assertEqual(decisions[1]["kind"], "allow")

        os.remove(gap_path)
        decisions = self._ask_probe([{"name": "write", "arguments": {"path": "src/a.py"}}])
        self.assertEqual(decisions[0]["kind"], "allow")

    def test_missing_policy_allows_read_and_denies_writes(self) -> None:
        """Без политики сессия остаётся осматриваемой, но не даёт менять файлы."""
        bare = tmpdir.scratch(ROOT, "harness-plugin-nopolicy-")
        # глубже четырёх уровней от корня проекта, иначе поиск вверх найдёт
        # политику рабочего дерева и тест перестанет проверять запасной путь
        deep = os.path.join(bare, "a", "b", "c", "d", "e")
        os.makedirs(deep, exist_ok=True)
        try:
            payload = json.dumps({"root": deep, "calls": [
                {"name": "read", "arguments": {"path": "README.md"}},
                {"name": "write", "arguments": {"path": "src/a.py"}},
                {"name": "pwsh", "arguments": {"command": "echo hi"}},
            ]})
            proc = subprocess.run(
                [NODE, PROBE], input=payload, capture_output=True, text=True,
                encoding="utf-8", errors="replace", cwd=deep, timeout=60,
                env={**{k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")},
                     "DSH_HOME": bare},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr[:400])
            results = json.loads(proc.stdout)
            self.assertIsNone(results[0]["deny"], "чтение должно остаться доступным")
            self.assertIn("R-HOOK-ERR", results[1]["deny"])
            self.assertIn("R-HOOK-ERR", results[2]["deny"])
        finally:
            shutil.rmtree(bare, ignore_errors=True)

    def test_default_role_is_human_outside_task(self) -> None:
        """Без активной задачи агент работает как руки человека, а не как воркер."""
        results = self._probe([
            {"name": "write", "arguments": {"path": "src/a.py"}},
            {"name": "write", "arguments": {"path": "docs/note.md"}},
        ])
        self.assertIsNone(results[0]["deny"], results)
        self.assertIsNone(results[1]["deny"], results)

    def test_child_agent_does_not_inherit_human_rights(self) -> None:
        """Отменили задачу — субагент не получает права человека молча."""
        results = self._probe([
            {"name": "write", "arguments": {"path": "src/x.py"}, "parent": True},
            {"name": "pwsh", "arguments": {"command": "echo hi"}, "parent": True},
            {"name": "read", "arguments": {"path": "src/x.py"}, "parent": True},
        ])
        self.assertIn("R-ROLE-1", results[0]["deny"])
        self.assertIn("R-ROLE-1", results[1]["deny"])
        self.assertIsNone(results[2]["deny"], "чтение дочернему агенту не запрещено")

    def test_primary_agent_is_orchestrator_during_task(self) -> None:
        """Оркестратор во время задачи пишет документацию, но не продукт."""
        self._write_scope(["src/a.py"], primary="orch-1")
        results = self._probe([
            {"name": "write", "arguments": {"path": "src/a.py"}, "agent": "orch-1"},
            {"name": "write", "arguments": {"path": "docs/plan.md"}, "agent": "orch-1"},
        ])
        self.assertIn("R-ORCH-1", results[0]["deny"], results[0])
        self.assertIsNone(results[1]["deny"], results[1])

    def test_worker_agent_is_limited_by_brief(self) -> None:
        """Воркер — не оркестратор: ему можно ровно то, что в allowed_files."""
        self._write_scope(["src/a.py"], primary="orch-1")
        results = self._probe([
            {"name": "write", "arguments": {"path": "src/a.py"}, "agent": "worker-1"},
            {"name": "write", "arguments": {"path": "docs/plan.md"}, "agent": "worker-1"},
        ])
        self.assertIsNone(results[0]["deny"], results[0])
        self.assertIn("R-WORK-1", results[1]["deny"], results[1])

    def test_verifier_phase_writes_only_evidence(self) -> None:
        """На проверке дочерние агенты — verifier: evidence можно, код нельзя."""
        self._write_scope(["evidence/**"], role="verifier", phase="verifying")
        results = self._probe([
            {"name": "write", "arguments": {"path": "evidence/verdict.json"}, "agent": "ver-1"},
            {"name": "write", "arguments": {"path": "src/a.py"}, "agent": "ver-1"},
        ])
        self.assertIsNone(results[0]["deny"], results[0])
        self.assertIn("R-VER-1", results[1]["deny"], results[1])

    def test_child_agent_with_scope_works(self) -> None:
        """Внутри задачи субагент работает по области прав, а не по запрету."""
        self._write_scope(["src/a.py"])
        results = self._probe([
            {"name": "write", "arguments": {"path": "src/a.py"}, "parent": True},
            {"name": "write", "arguments": {"path": "src/b.py"}, "parent": True},
        ])
        self.assertIsNone(results[0]["deny"], results)
        self.assertIn("R-WORK-1", results[1]["deny"])

    def test_pointer_file_resolves_project_root(self) -> None:
        """Корень находится по указателю ~/.dsh/harness-root даже из чужого каталога."""
        home = tmpdir.scratch(ROOT, "harness-dsh-home-")
        elsewhere = tmpdir.scratch(ROOT, "harness-elsewhere-")
        try:
            with open(os.path.join(home, "harness-root"), "w", encoding="utf-8") as fh:
                fh.write(self.root + "\n")
            payload = json.dumps({"calls": [{"name": "write", "arguments": {"path": "src/b.py"}}]})
            proc = subprocess.run(
                [NODE, PROBE], input=payload, capture_output=True, text=True,
                encoding="utf-8", errors="replace", cwd=elsewhere, timeout=60,
                env={**{k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")},
                     "DSH_HOME": home},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr[:400])
            results = json.loads(proc.stdout)
            self.assertIsNone(results[0]["deny"], "policy должна найтись по указателю")
        finally:
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(elsewhere, ignore_errors=True)

    def test_guard_registers_and_denies_outside_scope(self) -> None:
        self._write_scope(["src/a.py"])
        results = self._probe([
            {"name": "write", "arguments": {"path": "src/a.py"}},
            {"name": "write", "arguments": {"path": "src/b.py"}},
            {"name": "pwsh", "arguments": {"command": "echo x > src/c.py"}},
        ])
        self.assertIsNone(results[0]["deny"])
        self.assertIn("R-WORK-1", results[1]["deny"])
        # обходной путь через shell тоже закрыт: правило R-WORK-1 применяется к цели
        self.assertIn("R-WORK-1", results[2]["deny"])

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

    def test_cancel_clears_scope(self) -> None:
        """Отмена задачи не оставляет исполнителю прав писать."""
        self.store.create_task("T-GUARD", policy_version="1.0.0")
        self.engine.apply("T-GUARD", "attach_brief", {"brief": brief_data()})
        self.engine.apply("T-GUARD", "dispatch", {"worker_id": "w"})
        scope_file = os.path.join(self.root, ".harness", "guard-scope.json")
        self.assertTrue(os.path.isfile(scope_file))
        self.engine.apply("T-GUARD", "cancel", {})
        self.assertFalse(os.path.isfile(scope_file))


class PluginPrepareTests(unittest.TestCase):
    """Подготовка плагина: указатель на корень и проверка пакета."""

    def setUp(self) -> None:
        self.root = tmpdir.scratch(ROOT, "harness-prepare-")
        shutil.copytree(os.path.join(ROOT, "policies"), os.path.join(self.root, "policies"))
        shutil.copytree(os.path.join(ROOT, "plugins", "harness-guard"),
                        os.path.join(self.root, "plugins", "harness-guard"))
        self.home = tmpdir.scratch(ROOT, "harness-dsh-home-")
        self._old_home = os.environ.get("DSH_HOME")
        os.environ["DSH_HOME"] = self.home

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("DSH_HOME", None)
        else:
            os.environ["DSH_HOME"] = self._old_home
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def test_status_before_prepare_reports_missing_pointer(self) -> None:
        from harness import pluginctl

        report = pluginctl.status(self.root)
        self.assertFalse(report["ok"])
        self.assertFalse(report["checks"]["pointer"])
        self.assertTrue(report["checks"]["policy"])

    def test_prepare_writes_pointer_and_becomes_ready(self) -> None:
        from harness import pluginctl

        report = pluginctl.prepare(self.root)
        self.assertTrue(report["ok"], report)
        pointer = os.path.join(self.home, "harness-root")
        self.assertTrue(os.path.isfile(pointer))
        with open(pointer, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), self.root)

    def test_status_detects_broken_manifest(self) -> None:
        from harness import pluginctl

        manifest = os.path.join(self.root, "plugins", "harness-guard", "package.json")
        with open(manifest, "w", encoding="utf-8") as fh:
            fh.write("{}")
        report = pluginctl.status(self.root)
        self.assertFalse(report["checks"]["manifest"])
        self.assertIn("dsh.bundle.patch", report["manifest_error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
