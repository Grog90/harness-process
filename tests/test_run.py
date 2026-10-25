"""Сквозные тесты драйвера: одна команда — весь цикл."""

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
from harness.run import RunConfig, Runner  # noqa: E402
from harness.state import Store  # noqa: E402

WORKER = os.path.join(ROOT, "examples", "agents", "demo_worker.py")
VERIFIER = os.path.join(ROOT, "examples", "verifiers", "behavior_verifier.py")
BRIEF_SRC = os.path.join(ROOT, "examples", "evals", "fee-brief.json")


def git(*args: str, cwd: str) -> None:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(f"git {args}: {proc.stderr.strip()[:200]}")


def make_repo() -> str:
    root = tmpdir.scratch(ROOT, "harness-run-")
    for sub in ("policies", "schemas"):
        shutil.copytree(os.path.join(ROOT, sub), os.path.join(root, sub))
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    with open(os.path.join(root, "src", "fee.py"), "w", encoding="utf-8") as fh:
        fh.write("def calculate_fee(x):\n    return 0\n")
    with open(BRIEF_SRC, encoding="utf-8") as fh:
        brief = json.load(fh)
    brief["task_id"] = "RUN-1"
    brief["allowed_files"] = ["src/fee.py"]
    with open(os.path.join(root, "brief.json"), "w", encoding="utf-8") as fh:
        json.dump(brief, fh, ensure_ascii=False, indent=2)
    git("init", "-q", cwd=root)
    git("add", "-A", cwd=root)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base", cwd=root)
    return root


def cmd(script: str) -> str:
    return f'"{sys.executable}" -X utf8 "{script}"'


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_repo()
        self.store = Store(os.path.join(self.root, ".harness", "state.db"))
        policy = Policy.load(self.root)
        self.engine = Engine(self.store, policy, self.root)
        self.policy = policy

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def _runner(self, **over) -> Runner:
        params = dict(
            root=self.root,
            task_id="RUN-1",
            brief_path=os.path.join(self.root, "brief.json"),
            worker_cmd=cmd(WORKER),
            verifier_cmd=cmd(VERIFIER),
            commit=True,
        )
        params.update(over)
        return Runner(RunConfig(**params), self.store, self.policy, self.engine)

    def test_full_cycle_ends_with_commit(self) -> None:
        result = self._runner().run()
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.state, "COMMITTED")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.verdicts, ["FAIL", "PASS"])
        self.assertTrue(result.commit_sha)

        with open(os.path.join(self.root, "src", "fee.py"), encoding="utf-8") as fh:
            self.assertIn("FEE_NEGATIVE", fh.read())
        log = subprocess.run(
            ["git", "-C", self.root, "log", "--oneline"], capture_output=True, text=True
        ).stdout
        self.assertIn("RUN-1", log)

    def test_stop_at_accepted_without_commit(self) -> None:
        result = self._runner(commit=False).run()
        self.assertTrue(result.ok)
        self.assertEqual(result.state, "ACCEPTED")
        self.assertIsNone(result.commit_sha)

    def test_broken_worker_is_infrastructure_error(self) -> None:
        runner = self._runner(worker_cmd=f'"{sys.executable}" -c "raise SystemExit(1)"')
        result = runner.run()
        self.assertEqual(result.state, "VERIFY_BLOCKED")
        self.assertFalse(result.ok)
        latest = self.store.latest_verdict("RUN-1")
        self.assertEqual(latest["verdict"], "ERROR")

    def test_missing_verifier_leaves_verification_blocked(self) -> None:
        cfg = RunConfig(
            root=self.root, task_id="RUN-1",
            brief_path=os.path.join(self.root, "brief.json"),
            worker_cmd=cmd(WORKER), verifier_cmd=None, commit=False,
        )
        result = Runner(cfg, self.store, self.policy, self.engine).run()
        self.assertEqual(result.state, "VERIFY_BLOCKED")

    def test_worker_respects_gateway(self) -> None:
        """Воркер не может писать вне allowed_files: шлюз отклонит, прогон зафиксирует сбой."""
        with open(os.path.join(self.root, "src", "extra.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        git("add", "-A", cwd=self.root)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "extra", cwd=self.root)

        bad_worker = os.path.join(self.root, "bad_worker.py")
        with open(bad_worker, "w", encoding="utf-8") as fh:
            fh.write(
                "import os, sys\n"
                f"sys.path.insert(0, {os.path.dirname(os.path.dirname(WORKER))!r})\n"
                "from harness_client import HarnessClient, ToolDenied\n"
                "c = HarnessClient(root=os.environ['HARNESS_ROOT'])\n"
                "try:\n"
                "    c.write('src/extra.py', 'hacked')\n"
                "except ToolDenied:\n"
                "    sys.exit(7)\n"
            )
        result = self._runner(worker_cmd=cmd(bad_worker)).run()
        self.assertEqual(result.state, "VERIFY_BLOCKED")
        with open(os.path.join(self.root, "src", "extra.py"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "x = 1\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
