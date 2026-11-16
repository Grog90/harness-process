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

from harness import tmpdir

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
    tmp = tmpdir.scratch(ROOT, "harness-test-")
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

    def test_dispatch_publishes_orchestrator_and_worker_roles(self) -> None:
        """Роль выдаётся по агенту: primary — оркестратор, остальные — воркер."""
        from harness import scope

        self.eng.apply("T1", "attach_brief", {"brief": sample_brief()})
        self.eng.apply("T1", "dispatch", {"worker_id": "w", "primary_agent": "session-1"})
        data = scope.read_scope(self.root)
        self.assertEqual(data["primary"], "session-1")
        self.assertEqual(data["role"], "worker")
        self.assertEqual(data["phase"], "running")
        self.assertEqual(scope.role_for(data, "session-1"), ("orchestrator", True))
        self.assertEqual(scope.role_for(data, "worker-9"), ("worker", True))

    def test_submit_switches_children_to_verifier(self) -> None:
        """На проверке дочерние агенты получают verifier и только evidence."""
        from harness import scope

        self.eng.apply("T1", "attach_brief", {"brief": sample_brief()})
        self.eng.apply("T1", "dispatch", {"worker_id": "w", "primary_agent": "session-1"})
        self.eng.apply("T1", "submit", {"attempt": 1, "snapshot_id": "snap1"})
        data = scope.read_scope(self.root)
        self.assertEqual(data["phase"], "verifying")
        self.assertEqual(data["role"], "verifier")
        self.assertEqual(data["allowed_files"], ["evidence/**"])
        self.assertEqual(data["primary"], "session-1", "primary сохраняется между фазами")
        self.assertEqual(data["attempt"], 1)

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

    def test_destructive_git_denied_without_named_tree(self) -> None:
        result = self._run(role="worker", tool="bash", command="git push --force origin main")
        self.assertEqual(result["decision"], "deny")
        self.assertEqual(result["rule"], "R-GIT-1")

    def test_destructive_git_advised_with_named_tree(self) -> None:
        result = self._run(role="worker", tool="bash", command="git -C ../wt reset --hard")
        self.assertEqual(result["decision"], "advise")
        self.assertEqual(result["rule"], "R-GIT-1")

    def test_ordinary_git_is_allowed(self) -> None:
        result = self._run(role="worker", tool="bash", command="git log --oneline -5")
        self.assertIn(result["decision"], ("allow", "advise"))

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


class GatewayTests(unittest.TestCase):
    """Шлюз: права берутся из управляющего процесса, а не из запроса агента."""

    def setUp(self) -> None:
        self.root = make_root()
        self.store = Store(os.path.join(self.root, ".harness", "state.db"))
        self.eng = Engine(self.store, Policy.load(self.root), self.root)
        self.eng.store.create_task("T1", policy_version="1.0.0")

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def _running(self) -> None:
        self.eng.apply("T1", "attach_brief", {"brief": sample_brief()})
        self.eng.apply("T1", "dispatch", {"worker_id": "w"})

    def test_missing_role_denied(self) -> None:
        from harness.gateway import Gateway

        os.environ.pop("HARNESS_ROLE", None)
        result = Gateway(self.root, Policy.load(self.root), self.store).handle(
            {"tool": "read_file", "args": {"path": "src/a.py"}}
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["rule"], "R-ROLE-1")

    def test_write_outside_brief_denied(self) -> None:
        from harness.gateway import Gateway

        self._running()
        result = Gateway(self.root, Policy.load(self.root), self.store).handle({
            "role": "worker", "task_id": "T1", "tool": "write_file",
            "args": {"path": "src/b.py", "content": "x", "allowed_files": ["**"]},
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["rule"], "R-WORK-1")

    def test_tools_closed_outside_running(self) -> None:
        from harness.gateway import Gateway

        self._running()
        self.eng.apply("T1", "submit", {"attempt": 1, "snapshot_id": "s1"})
        result = Gateway(self.root, Policy.load(self.root), self.store).handle({
            "role": "worker", "task_id": "T1", "tool": "write_file",
            "args": {"path": "src/a.py", "content": "x"},
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["rule"], "R-STATE-1")

    def test_path_traversal_denied(self) -> None:
        from harness.gateway import Gateway

        self._running()
        result = Gateway(self.root, Policy.load(self.root), self.store).handle({
            "role": "worker", "task_id": "T1", "tool": "read_file",
            "args": {"path": "../../secrets.txt"},
        })
        self.assertFalse(result["ok"])

    def test_service_write_requires_write_flag_and_allowed_file(self) -> None:
        from harness.gateway import Gateway

        service = tempfile.mkdtemp(prefix="svc-", dir=os.path.dirname(self.root))
        self.addCleanup(shutil.rmtree, service, True)
        brief = sample_brief()
        brief["services"] = [{"name": "api", "path": service,
                              "base_commit": "a" * 40, "write": True}]
        brief["allowed_files"] = ["service:api/src/a.py"]
        self.eng.apply("T1", "attach_brief", {"brief": brief})
        self.eng.apply("T1", "dispatch", {"worker_id": "w"})
        gateway = Gateway(self.root, Policy.load(self.root), self.store)

        result = gateway.handle({
            "role": "worker", "task_id": "T1", "tool": "write_file",
            "args": {"path": os.path.join(service, "src", "a.py"), "content": "x"},
        })
        self.assertTrue(result["ok"], result)
        self.assertTrue(os.path.isfile(os.path.join(service, "src", "a.py")))

        other = gateway.handle({
            "role": "worker", "task_id": "T1", "tool": "write_file",
            "args": {"path": os.path.join(service, "src", "b.py"), "content": "x"},
        })
        self.assertFalse(other["ok"])
        self.assertEqual(other["rule"], "R-WORK-1")

    def test_read_only_service_and_in_root_protected_are_denied(self) -> None:
        policy = Policy.load(self.root)
        read_only = [{"name": "api", "path": "../svc", "write": False}]
        decision = policy.check_write(
            "worker", "../svc/src/a.py",
            allowed_files=["service:api/src/a.py"], services=read_only,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.rule, "R-PROT-1")

        in_root = [{"name": "root", "path": ".", "write": True}]
        decision = policy.check_write(
            "worker", "service:root/policies/policy.json",
            allowed_files=["service:root/policies/policy.json"], services=in_root,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.rule, "R-PROT-1")

    def test_shell_write_to_service_is_denied(self) -> None:
        from harness.hooks import HookContext, run as run_hooks

        ctx = HookContext(
            event="pretooluse", tool="bash", role="worker",
            command="echo x >> ../svc/config.yaml",
            allowed_files=["service:api/config.yaml"],
            services=[{"name": "api", "path": "../svc", "write": True}],
            policy=Policy.load(self.root), root=self.root,
        )
        result = run_hooks("pretooluse", ctx)
        self.assertEqual(result["decision"], "deny")
        self.assertEqual(result["rule"], "R-PROT-1")

    def test_service_symlink_escape_is_denied_by_gateway(self) -> None:
        from harness.gateway import Gateway

        service = tempfile.mkdtemp(prefix="svc-", dir=os.path.dirname(self.root))
        outside = tempfile.mkdtemp(prefix="outside-", dir=os.path.dirname(self.root))
        self.addCleanup(shutil.rmtree, service, True)
        self.addCleanup(shutil.rmtree, outside, True)
        try:
            os.symlink(outside, os.path.join(service, "link"), target_is_directory=True)
        except (OSError, NotImplementedError):
            # Windows без привилегий: junction не требует admin и тоже является
            # reparse point — проверяем побег через него.
            import subprocess

            proc = subprocess.run(
                ["cmd", "/c", "mklink", "/J", os.path.join(service, "link"), outside],
                capture_output=True, text=True,
            )
            if proc.returncode != 0:
                self.skipTest("symlink и junction недоступны")
        brief = sample_brief()
        brief["services"] = [{"name": "api", "path": service,
                              "base_commit": "a" * 40, "write": True}]
        brief["allowed_files"] = ["service:api/link/a.py"]
        self.eng.apply("T1", "attach_brief", {"brief": brief})
        self.eng.apply("T1", "dispatch", {"worker_id": "w"})
        result = Gateway(self.root, Policy.load(self.root), self.store).handle({
            "role": "worker", "task_id": "T1", "tool": "write_file",
            "args": {"path": os.path.join(service, "link", "a.py"), "content": "x"},
        })
        self.assertFalse(result["ok"])
        self.assertIn(result["rule"], ("R-PROT-1", "R-WORK-1"))

    def test_service_files_snapshot_tracks_service_file(self) -> None:
        from harness import snapshot

        service = tempfile.mkdtemp(prefix="svc-", dir=os.path.dirname(self.root))
        self.addCleanup(shutil.rmtree, service, True)
        os.makedirs(os.path.join(service, "src"), exist_ok=True)
        path = os.path.join(service, "src", "a.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("one")
        services = [{"name": "api", "path": service, "write": True}]
        first = snapshot.files_snapshot(self.root, ["service:api/src/a.py"], services)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("two")
        second = snapshot.files_snapshot(self.root, ["service:api/src/a.py"], services)
        self.assertNotEqual(first, second)


class FinalizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_root()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_write_set_ignores_control_plane(self) -> None:
        from harness.finalize import write_set

        subprocess_git(self.root, "init", "-q", self.root)
        subprocess_git(self.root, "-C", self.root, "add", "-A")
        subprocess_git(self.root, "-C", self.root, "-c", "user.name=t", "-c", "user.email=t@t",
                       "commit", "-q", "-m", "base")
        os.makedirs(os.path.join(self.root, ".harness"), exist_ok=True)
        open(os.path.join(self.root, ".harness", "state.db"), "w").close()
        with open(os.path.join(self.root, "src", "a.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        self.assertEqual(write_set(self.root), ["src/a.py"])

    def test_requires_accepted_state(self) -> None:
        from harness.finalize import FinalizeError, finalize

        subprocess_git(self.root, "init", "-q", self.root)
        store = Store(os.path.join(self.root, ".harness", "state.db"))
        eng = Engine(store, Policy.load(self.root), self.root)
        try:
            store.create_task("T1", policy_version="1.0.0")
            with self.assertRaises(FinalizeError) as ctx:
                finalize(store, eng, self.root, "T1")
            self.assertEqual(ctx.exception.rule, "R-VERIFY-1")
        finally:
            store.close()


class ChecksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_root()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_command_collection_skips_labels(self) -> None:
        from harness import checks

        brief = {"acceptance": {"check_commands": ["pytest -q"],
                                "criteria": [{"id": "A", "check": "call"}, {"id": "B", "check": "pytest -q"}]}}
        self.assertEqual(checks.collect_commands(brief), ["pytest -q"])

    def test_unexecuted_criterion_is_inconclusive(self) -> None:
        from harness import checks

        brief = {"acceptance": {"criteria": [{"id": "A", "check": "call"}]}}
        report = {"results": [], "artifacts": self.root, "root": self.root}
        verdict = checks.build_verdict(brief, report, task_id="T", attempt=1, snapshot_id="s")
        self.assertEqual(verdict["verdict"], "INCONCLUSIVE")

    def test_check_runs_in_copy(self) -> None:
        from harness import checks

        with open(os.path.join(self.root, "touch.py"), "w", encoding="utf-8") as fh:
            fh.write("open('artifact.txt','w').write('x')\n")
        brief = {"acceptance": {"check_commands": ["python touch.py"],
                                "criteria": [{"id": "A", "check": "python touch.py"}]}}
        report = checks.run(self.root, brief, timeout=60)
        self.assertTrue(report["all_ok"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "artifact.txt")))
        verdict = checks.build_verdict(brief, report, task_id="T", attempt=1, snapshot_id="s")
        self.assertEqual(verdict["verdict"], "PASS")


class SnapshotScopeTests(unittest.TestCase):
    def test_scope_separates_subtree_from_worktree(self) -> None:
        from harness.snapshot import compute_for_scope, scope_of

        root = make_root()
        try:
            with open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8") as fh:
                fh.write("x = 1\n")
            whole = compute_for_scope(root, "")
            part = compute_for_scope(root, "src")
            self.assertNotEqual(whole, part)
            self.assertEqual(scope_of(root, os.path.join(root, "src")), "src")
            self.assertEqual(scope_of(root, root), "")
            with self.assertRaises(ValueError):
                scope_of(root, os.path.join(root, "..", "outside"))
        finally:
            shutil.rmtree(root, ignore_errors=True)


def subprocess_git(root: str, *args: str) -> str:
    import subprocess

    proc = subprocess.run(["git", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(f"git {args}: {proc.stderr.strip()[:200]}")
    return proc.stdout


class WatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_root()
        self.store = Store(os.path.join(self.root, ".harness", "state.db"))
        self.eng = Engine(self.store, Policy.load(self.root), self.root)

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def _expired(self, task_id: str = "T1") -> None:
        self.store.create_task(task_id, policy_version="1.0.0")
        self.eng.apply(task_id, "attach_brief", {"brief": sample_brief(task_id)})
        self.eng.apply(task_id, "dispatch", {"worker_id": "w", "timeout_seconds": -5})

    def test_sweep_revokes_only_expired(self) -> None:
        from harness.watchdog import Watchdog

        self._expired("T-OLD")
        self.store.create_task("T-NEW", policy_version="1.0.0")
        self.eng.apply("T-NEW", "attach_brief", {"brief": sample_brief("T-NEW")})
        self.eng.apply("T-NEW", "dispatch", {"worker_id": "w", "timeout_seconds": 3600})

        revoked = Watchdog(self.store, self.eng).sweep()
        self.assertEqual([r.task_id for r in revoked], ["T-OLD"])
        self.assertEqual(self.store.get_task("T-OLD")["state"], "TIMED_OUT")
        self.assertEqual(self.store.get_task("T-NEW")["state"], "RUNNING")

    def test_sweep_is_idempotent(self) -> None:
        from harness.watchdog import Watchdog

        self._expired()
        dog = Watchdog(self.store, self.eng)
        self.assertEqual(len(dog.sweep()), 1)
        self.assertEqual(dog.sweep(), [])
        deaths = [e for e in self.store.events_for("T1") if e["type"] == "dispatch_death"]
        self.assertEqual(len(deaths), 1)

    def test_serve_emits_cycles(self) -> None:
        from harness.watchdog import Watchdog

        events: list[dict] = []
        cycles = Watchdog(self.store, self.eng).serve(interval=1, cycles=2, emit=events.append)
        self.assertEqual(cycles, 2)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["cycle"], 1)


class SandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_root()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_detect_reports_mode_and_note(self) -> None:
        from harness.checks import detect_sandbox

        info = detect_sandbox()
        self.assertIn(info["mode"], ("docker", "copy-only"))
        self.assertTrue(info["note"])

    def test_forced_copy_mode(self) -> None:
        from harness import checks

        brief = {"acceptance": {"check_commands": ["python -V"],
                                "criteria": [{"id": "A", "check": "python -V"}]}}
        report = checks.run(self.root, brief, sandbox="none", timeout=60)
        self.assertEqual(report["isolation"]["mode"], "copy-only")

    def test_docker_requested_without_daemon_raises(self) -> None:
        from harness import checks

        info = checks.detect_sandbox()
        if info["docker_ready"]:
            self.skipTest("docker доступен: негативный случай неприменим")
        brief = {"acceptance": {"check_commands": ["python -V"],
                                "criteria": [{"id": "A", "check": "python -V"}]}}
        with self.assertRaises(checks.CheckError):
            checks.run(self.root, brief, sandbox="docker", timeout=60)


class LaneTests(unittest.TestCase):
    """Лейны: изоляция работы и мьютекс слияния без правки конфликтов координатором."""

    def setUp(self) -> None:
        self.root = make_root()
        subprocess_git(self.root, "init", "-q", self.root)
        os.makedirs(os.path.join(self.root, "src"), exist_ok=True)
        with open(os.path.join(self.root, "src", "fee.py"), "w", encoding="utf-8") as fh:
            fh.write("def calculate_fee(amount):\n    return 0\n")
        subprocess_git(self.root, "-C", self.root, "add", "-A")
        subprocess_git(self.root, "-C", self.root, "-c", "user.name=t", "-c", "user.email=t@t",
                       "commit", "-q", "-m", "base")
        self.base = subprocess_git(self.root, "-C", self.root, "rev-parse", "HEAD").strip()
        self.store = Store(os.path.join(self.root, ".harness", "state.db"))

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def _lane_with_change(self, task_id: str, text: str) -> str:
        from harness import lanes

        self.store.create_task(task_id, policy_version="1.0.0")
        lane = lanes.create(self.root, self.store, task_id, base=self.base)
        path = lane["path"]
        with open(os.path.join(path, "src", "fee.py"), "w", encoding="utf-8") as fh:
            fh.write(text)
        subprocess_git(path, "-C", path, "add", "-A")
        subprocess_git(path, "-C", path, "-c", "user.name=t", "-c", "user.email=t@t",
                       "commit", "-q", "-m", f"{task_id} change")
        sha = subprocess_git(path, "-C", path, "rev-parse", "HEAD").strip()
        self.store.update_task(task_id, state="COMMITTED", commit_sha=sha)
        return sha

    def test_merge_requires_committed_state(self) -> None:
        from harness import lanes

        self.store.create_task("T1", policy_version="1.0.0")
        with self.assertRaises(lanes.LaneError) as ctx:
            lanes.merge(self.root, self.store, "T1")
        self.assertEqual(ctx.exception.rule, "R-FINAL-1")

    def test_merge_brings_change_into_main_tree(self) -> None:
        from harness import lanes

        self._lane_with_change("T1", "def calculate_fee(amount):\n    return int(amount * 0.025)\n")
        out = lanes.merge(self.root, self.store, "T1")
        self.assertTrue(out["ok"])
        with open(os.path.join(self.root, "src", "fee.py"), encoding="utf-8") as fh:
            self.assertIn("0.025", fh.read())

    def test_conflict_aborts_and_keeps_main_intact(self) -> None:
        from harness import lanes

        self._lane_with_change("T1", "def calculate_fee(amount):\n    return 1\n")
        lanes.merge(self.root, self.store, "T1")
        self._lane_with_change("T2", "def calculate_fee(amount):\n    return 2\n")
        with self.assertRaises(lanes.LaneError) as ctx:
            lanes.merge(self.root, self.store, "T2")
        self.assertEqual(ctx.exception.rule, "R-WAVE-1")
        self.assertTrue(ctx.exception.details.get("aborted"))
        with open(os.path.join(self.root, "src", "fee.py"), encoding="utf-8") as fh:
            self.assertIn("return 1", fh.read(), "конфликт изменил главное дерево")

    def test_dirty_main_tree_blocks_merge(self) -> None:
        from harness import lanes

        self._lane_with_change("T1", "def calculate_fee(amount):\n    return 3\n")
        with open(os.path.join(self.root, "src", "other.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        with self.assertRaises(lanes.LaneError) as ctx:
            lanes.merge(self.root, self.store, "T1")
        self.assertEqual(ctx.exception.rule, "R-WAVE-1")
        self.assertIn("грязное", str(ctx.exception))

    def test_merge_lock_is_exclusive(self) -> None:
        from harness import lanes

        with lanes.merge_lock(self.root, timeout=5):
            with self.assertRaises(lanes.LaneError):
                with lanes.merge_lock(self.root, timeout=0.2):
                    pass


class CostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_root()
        self.store = Store(os.path.join(self.root, ".harness", "state.db"))
        self.eng = Engine(self.store, Policy.load(self.root), self.root)

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_no_budget_means_no_gate_but_is_stated(self) -> None:
        from harness import cost

        result = cost.check_dispatch(self.store, "T1", {})
        self.assertTrue(result["allowed"])
        self.assertIn("не задан", result["note"])

    def test_gate_blocks_before_spending(self) -> None:
        from harness import cost

        brief = {"control": {"budget": {"max_cost": 10, "estimate_per_attempt": 6}}}
        self.assertTrue(cost.check_dispatch(self.store, "T1", brief)["allowed"])
        cost.record(self.store, "T1", 5)
        second = cost.check_dispatch(self.store, "T1", brief)
        self.assertFalse(second["allowed"])
        self.assertEqual(second["spent"], 5.0)

    def test_overrun_blocks_dispatch(self) -> None:
        from harness import cost

        brief = {"control": {"budget": {"max_cost": 10, "estimate_per_attempt": 1}}}
        cost.record(self.store, "T1", 12)
        result = cost.check_dispatch(self.store, "T1", brief)
        self.assertFalse(result["allowed"])
        self.assertIn("израсходовано", result["reason"])

    def test_verdict_cost_is_recorded(self) -> None:
        brief = sample_brief()
        brief["control"]["budget"] = {"max_cost": 100, "estimate_per_attempt": 1}
        self.store.create_task("T1", policy_version="1.0.0")
        self.eng.apply("T1", "attach_brief", {"brief": brief})
        self.eng.apply("T1", "dispatch", {"worker_id": "w"})
        self.eng.apply("T1", "submit", {"attempt": 1, "snapshot_id": "s1"})
        verdict = sample_verdict("s1")
        verdict["cost"] = 2.5
        self.eng.apply("T1", "verdict", {"verdict": verdict})
        self.assertEqual(self.store.cost_total("T1"), 2.5)


class RuleChangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_root()
        self.store = Store(os.path.join(self.root, ".harness", "state.db"))

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_empty_history_is_not_drift(self) -> None:
        from harness import rulechange

        report = rulechange.verify(self.store, self.root)
        self.assertEqual(report["state"], "no_history")
        self.assertFalse(report["drift"])
        self.assertTrue(report["ok"])
        self.assertIsNone(report["last_change"])

    def test_change_requires_reason_and_known_rule(self) -> None:
        from harness import rulechange

        with self.assertRaises(ValueError):
            rulechange.change(self.store, self.root, "R-COST-1", "")
        with self.assertRaises(ValueError):
            rulechange.change(self.store, self.root, "R-UNKNOWN", "причина")

    def test_change_records_hash_and_clears_drift(self) -> None:
        from harness import rulechange

        out = rulechange.change(self.store, self.root, "R-COST-1", "бюджет до запуска")
        self.assertGreaterEqual(out["tests_passed"], 1)
        report = rulechange.verify(self.store, self.root)
        self.assertEqual(report["state"], "current")
        self.assertEqual(report["last_change"]["rule_id"], "R-COST-1")


if __name__ == "__main__":
    unittest.main(verbosity=2)