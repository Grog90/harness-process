"""Юнит-тесты контрольного слоя.

Проверяются не «счастливые пути», а свойства, на которых держится цикл:
снимок чувствителен к правке, PASS понижается без покрытия, бюджет не
сбрасывается, хук при собственной ошибке закрывает переход.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import brief as brief_mod  # noqa: E402
from harness import evals as evals_mod  # noqa: E402
from harness import findings as findings_mod  # noqa: E402
from harness import verdict as verdict_mod  # noqa: E402
from harness.fsm import Engine, TransitionError  # noqa: E402
from harness.hooks import HookContext, run as run_hooks  # noqa: E402
from harness.policy import Policy  # noqa: E402
from harness.snapshot import compute_snapshot  # noqa: E402
from harness.state import Store  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_root() -> str:
    tmp = tempfile.mkdtemp(prefix="harness-test-")
    for sub in ("policies", "schemas"):
        src = os.path.join(ROOT, sub)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(tmp, sub))
    os.makedirs(os.path.join(tmp, "src"), exist_ok=True)
    return tmp


def sample_brief(task_id: str = "T1", **over) -> dict:
    data = {
        "task_id": task_id,
        "brief_version": 1,
        "base_commit": "abc",
        "spec_version": "spec-1",
        "goal": "g",
        "expected_behavior": "b",
        "allowed_files": ["src/a.py"],
        "contract": {"names": ["f"], "required_fields": ["x"], "error_codes": ["E"]},
        "acceptance": {
            "criteria": [{"id": "AC-1", "statement": "s", "mandatory": True, "check": "pytest"}],
            "check_commands": ["pytest"],
        },
        "control": {"attempt_limit": 3, "open_questions": []},
    }
    data.update(over)
    return data


def sample_verdict(snapshot: str, task_id: str = "T1", attempt: int = 1, kind: str = "PASS", **over) -> dict:
    data = {
        "verdict_id": f"v-{attempt}",
        "task_id": task_id,
        "attempt": attempt,
        "snapshot_id": snapshot,
        "verdict": kind,
        "reviewer_id": "r1",
        "reviewer_role": "verifier",
        "criteria": [{"id": "AC-1", "result": "pass" if kind == "PASS" else "fail", "evidence": "ok"}],
        "findings": [],
    }
    data.update(over)
    return data


class PolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_root()
        self.policy = Policy.load(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_orchestrator_cannot_write_product(self) -> None:
        self.assertTrue(self.policy.check_write("orchestrator", "docs/x.md").allowed)
        denied = self.policy.check_write("orchestrator", "src/a.py")
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.rule, "R-ORCH-1")

    def test_worker_limited_to_allowed_files(self) -> None:
        self.assertTrue(self.policy.check_write("worker", "src/a.py", ["src/a.py"]).allowed)
        self.assertFalse(self.policy.check_write("worker", "src/b.py", ["src/a.py"]).allowed)
        self.assertFalse(self.policy.check_write("worker", "src/a.py", []).allowed)

    def test_traversal_and_outside_paths_rejected(self) -> None:
        for path in ("../secrets.txt", "src/../../etc/passwd"):
            decision = self.policy.check_write("worker", path, ["**"])
            self.assertFalse(decision.allowed, path)

    def test_role_identity_is_fixed(self) -> None:
        self.assertFalse(self.policy.check_role_identity("verifier", "worker").allowed)
        self.assertTrue(self.policy.check_role_identity("worker", "worker").allowed)

    def test_protected_paths(self) -> None:
        self.assertFalse(self.policy.check_write("orchestrator", "policies/policy.json").allowed)
        self.assertTrue(self.policy.check_write("system", "policies/policy.json").allowed)


class BriefTests(unittest.TestCase):
    def test_valid_brief(self) -> None:
        self.assertTrue(brief_mod.validate(sample_brief()).valid)

    def test_empty_and_contradictory_are_different_codes(self) -> None:
        empty = brief_mod.validate(sample_brief(allowed_files=[]))
        contradiction = brief_mod.validate(sample_brief(scope={"in_scope": ["a"], "out_of_scope": ["a"]}))
        self.assertIn("E_EMPTY_FIELD", {i.code for i in empty.errors})
        self.assertIn("E_CONTRADICTION", {i.code for i in contradiction.errors})

    def test_unverifiable_criterion_rejected(self) -> None:
        data = sample_brief()
        data["acceptance"] = {"criteria": [{"id": "AC-1", "statement": "s", "mandatory": True}]}
        codes = {i.code for i in brief_mod.validate(data).errors}
        self.assertIn("E_UNVERIFIABLE", codes)

    def test_thin_contract_is_warning_not_error(self) -> None:
        data = sample_brief(contract={"names": ["f"]})
        report = brief_mod.validate(data)
        self.assertTrue(report.valid)
        self.assertIn("W_THIN_CONTRACT", {w.code for w in report.warnings})

    def test_budget_above_policy_rejected(self) -> None:
        data = sample_brief()
        data["control"]["attempt_limit"] = 10
        self.assertIn("E_CONTRADICTION", {i.code for i in brief_mod.validate(data, max_attempts=3).errors})


class VerdictTests(unittest.TestCase):
    def setUp(self) -> None:
        self.brief = brief_mod.validate(sample_brief())

    def test_pass_without_criteria_is_downgraded(self) -> None:
        v = sample_verdict("snap", criteria=[])
        check = verdict_mod.validate(v, brief=self.brief)
        self.assertEqual(check.effective_verdict, "INCONCLUSIVE")

    def test_pass_with_open_blocker_is_downgraded(self) -> None:
        v = sample_verdict("snap", findings=[{"finding_id": "F", "severity": "blocker", "statement": "boom"}])
        self.assertEqual(verdict_mod.validate(v, brief=self.brief).effective_verdict, "INCONCLUSIVE")

    def test_cosmetic_only_fail_downgraded(self) -> None:
        v = sample_verdict("snap", kind="FAIL",
                           findings=[{"finding_id": "F", "severity": "cosmetic", "statement": "отступ"}])
        self.assertEqual(verdict_mod.validate(v, brief=self.brief).effective_verdict, "INCONCLUSIVE")

    def test_substantive_fail_preserved(self) -> None:
        v = sample_verdict("snap", kind="FAIL",
                           findings=[{"finding_id": "F", "severity": "major", "statement": "неверный расчёт"}])
        self.assertEqual(verdict_mod.validate(v, brief=self.brief).effective_verdict, "FAIL")

    def test_adversarial_needs_executed_scenario(self) -> None:
        v = sample_verdict("snap", reviewer_role="adversarial",
                           scenario={"mode": "not_verified", "description": "гонка"})
        self.assertEqual(verdict_mod.validate(v, brief=self.brief).effective_verdict, "INCONCLUSIVE")

    def test_feedback_packet_lists_open_findings(self) -> None:
        v = sample_verdict("snap", kind="FAIL",
                           findings=[{"finding_id": "F-1", "severity": "major", "statement": "s"}])
        packet = verdict_mod.feedback_packet(v, self.brief, remaining_budget=2)
        self.assertEqual(packet["open_finding_ids"], ["F-1"])
        self.assertEqual(packet["remaining_budget"], 2)


class FsmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_root()
        self.store = Store(os.path.join(self.root, ".harness", "state.db"))
        self.eng = Engine(self.store, Policy.load(self.root), self.root)
        self.store.create_task("T1", policy_version="1.0.0")

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def _to_verifying(self, snapshot: str = "snap1") -> None:
        self.eng.apply("T1", "attach_brief", {"brief": sample_brief()})
        self.eng.apply("T1", "dispatch", {"worker_id": "w"})
        self.eng.apply("T1", "submit", {"attempt": 1, "snapshot_id": snapshot})

    def test_dispatch_requires_brief(self) -> None:
        with self.assertRaises(TransitionError) as ctx:
            self.eng.apply("T1", "dispatch", {})
        self.assertEqual(ctx.exception.rule, "R-BRIEF-1")

    def test_attempts_are_counted_and_capped(self) -> None:
        self.eng.apply("T1", "attach_brief", {"brief": sample_brief()})
        for attempt in (1, 2, 3):
            self.eng.apply("T1", "dispatch", {"worker_id": f"w{attempt}"})
            snap = f"snap{attempt}"
            self.eng.apply("T1", "submit", {"attempt": attempt, "snapshot_id": snap})
            self.eng.apply("T1", "verdict", {
                "verdict": sample_verdict(snap, attempt=attempt, kind="FAIL",
                                          findings=[{"finding_id": "F", "severity": "major", "statement": "s"}])
            })
        self.assertEqual(self.store.get_task("T1")["state"], "ESCALATED")

    def test_commit_requires_current_pass(self) -> None:
        self._to_verifying()
        self.eng.apply("T1", "verdict", {"verdict": sample_verdict("snap1")})
        with self.assertRaises(TransitionError) as ctx:
            self.eng.apply("T1", "commit", {"snapshot_id": "snap2"})
        self.assertEqual(ctx.exception.rule, "R-SNAPSHOT-1")

    def test_replayed_event_is_idempotent(self) -> None:
        self.eng.apply("T1", "attach_brief", {"brief": sample_brief()})
        self.eng.apply("T1", "dispatch", {"worker_id": "w"}, event_id="e1")
        n = len(self.store.events_for("T1"))
        self.eng.apply("T1", "dispatch", {"worker_id": "w"}, event_id="e1")
        self.assertEqual(len(self.store.events_for("T1")), n)
        self.assertEqual(self.store.get_task("T1")["attempt"], 1)

    def test_verdict_from_other_attempt_rejected(self) -> None:
        self._to_verifying()
        with self.assertRaises(TransitionError) as ctx:
            self.eng.apply("T1", "verdict", {"verdict": sample_verdict("snap1", attempt=2)})
        self.assertEqual(ctx.exception.rule, "R-TIMEOUT-1")

    def test_worker_cannot_verify(self) -> None:
        self._to_verifying()
        with self.assertRaises(TransitionError):
            self.eng.apply("T1", "verdict", {"verdict": sample_verdict("snap1", reviewer_role="worker")})


class SnapshotTests(unittest.TestCase):
    def test_snapshot_changes_with_content(self) -> None:
        root = make_root()
        try:
            path = os.path.join(root, "src", "a.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("x = 1\n")
            first = compute_snapshot(os.path.join(root, "src"))
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("# change\n")
            second = compute_snapshot(os.path.join(root, "src"))
            self.assertNotEqual(first, second)
            self.assertEqual(second, compute_snapshot(os.path.join(root, "src")))
        finally:
            shutil.rmtree(root, ignore_errors=True)


class HookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_root()
        self.policy = Policy.load(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self, **kw):
        return run_hooks("pretooluse", HookContext(policy=self.policy, root=self.root, **kw))

    def test_write_bypass_via_redirect(self) -> None:
        result = self._run(role="orchestrator", tool="bash", command="echo hi >> src/a.py")
        self.assertEqual(result["decision"], "deny")

    def test_unknown_hook_fails_closed(self) -> None:
        from harness import hooks

        hooks.EVENT_HOOKS["pretooluse_tmp"] = ["policy.does_not_exist"]
        try:
            result = hooks.run("pretooluse_tmp", HookContext(policy=self.policy, root=self.root))
        finally:
            hooks.EVENT_HOOKS.pop("pretooluse_tmp", None)
        self.assertEqual(result["decision"], "deny")

    def test_allow_for_docs_write(self) -> None:
        self.assertEqual(self._run(role="orchestrator", tool="write_file", path="docs/a.md")["decision"], "allow")


class FindingsTests(unittest.TestCase):
    def test_completeness_flags_unowned(self) -> None:
        report = findings_mod.completeness([{"finding_id": "F1", "severity": "major", "status": "open"}])
        self.assertFalse(report["ok"])
        self.assertEqual(report["unowned"], ["F1"])

    def test_route_two_tracks(self) -> None:
        own = findings_mod.route({"finding_id": "F1", "owner": "w1", "in_scope": True}, worker_ids=["w1"])
        foreign = findings_mod.route({"finding_id": "F2", "owner": "team-b", "in_scope": False}, worker_ids=["w1"])
        self.assertEqual(own["track"], "own_diff")
        self.assertEqual(foreign["track"], "foreign_code")

    def test_dispute_requires_counterexample(self) -> None:
        with self.assertRaises(ValueError):
            findings_mod.dispute({"finding_id": "F1"}, "   ")


class EvalMetricTests(unittest.TestCase):
    def test_metrics_computation(self) -> None:
        rows = [
            {"case_id": "a", "expected": "FAIL", "predicted": "PASS", "agreement": False, "note": "x"},
            {"case_id": "b", "expected": "FAIL", "predicted": "FAIL", "agreement": True, "note": "x"},
            {"case_id": "c", "expected": "PASS", "predicted": "FAIL", "agreement": False, "note": "x"},
            {"case_id": "d", "expected": "PASS", "predicted": "PASS", "agreement": True, "note": "x"},
            {"case_id": "e", "expected": "PASS", "predicted": "ERROR", "agreement": False, "note": "x"},
        ]
        m = evals_mod.metrics(rows)
        self.assertEqual(m["miss_rate"], 0.5)
        self.assertEqual(m["false_fail_rate"], 0.333)
        self.assertEqual(m["incomplete"], 1)
        self.assertTrue(m["caveats"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
