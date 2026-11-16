"""Уровень изменения: Ready-гейт, review-записи, trace и привязка к задачам."""

from __future__ import annotations

import json
import os
import shutil
import unittest

from harness import brief as brief_mod
from harness import changefsm, specbrief
from harness import tmpdir
from harness.fsm import Engine, TransitionError
from harness.policy import Policy
from harness.state import Store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC_REF = "a" * 40
CHANGE = "chg-x"


def make_root() -> str:
    tmp = tmpdir.scratch(ROOT, "harness-change-")
    for sub in ("policies", "schemas"):
        src = os.path.join(ROOT, sub)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(tmp, sub))
    os.makedirs(os.path.join(tmp, "src"), exist_ok=True)
    base = os.path.join(tmp, "openspec", "changes", CHANGE)
    os.makedirs(os.path.join(base, "specs", "cap"))
    with open(os.path.join(base, "proposal.md"), "w", encoding="utf-8") as fh:
        fh.write("## Why\n\nНужен слот ресурсов.\n\n## What Changes\n\n- Выдать слот.\n")
    with open(os.path.join(base, "specs", "cap", "spec.md"), "w", encoding="utf-8") as fh:
        fh.write(
            "# Spec Delta\n\n## ADDED Requirements\n\n"
            "### Requirement: REQ-X-1: Слот выдаётся\n\nСистема ДОЛЖНА выдать слот.\n\n"
            "#### Scenario: CASE-X-1: Первый лейн\n"
            "- **WHEN** создаётся лейн\n- **THEN** он получает слот 0\n"
        )
    with open(os.path.join(base, "design.md"), "w", encoding="utf-8") as fh:
        fh.write("# Design\n\n## Decisions\n\nСлот — строка в таблице.\n")
    with open(os.path.join(base, "tasks.md"), "w", encoding="utf-8") as fh:
        fh.write(
            "# Tasks\n\n"
            "- [ ] 1.1 Правка `src/x.py`, проверка `python -m unittest tests.test_x`\n"
        )
    return tmp


def make_store(root: str) -> Store:
    return Store(os.path.join(root, ".harness", "state.db"))


def drive_to_design_agreed(store: Store, root: str) -> None:
    changefsm.create(store, root, CHANGE, owner="owner-1", risk_class="L1", title="Слот")
    for event in ("explore", "propose", "agree_specs", "agree_design"):
        changefsm.transition(store, root, CHANGE, event)


def add_reviews(store: Store, root: str, *, revision: int | None = None) -> None:
    change = store.get_change(CHANGE)
    rev = revision or int(change["revision"])
    pairs = [
        ("proposal.md", "BA", "ba-1"),
        ("specs", "BA", "ba-1"),
        ("specs", "QA", "qa-1"),
        ("design.md", "SA", "sa-1"),
        ("design.md", "DEV", "dev-1"),
        ("tasks.md", "SA", "sa-1"),
        ("tasks.md", "DEV", "dev-1"),
        ("tasks.md", "QA", "qa-1"),
    ]
    # review пишет текущую редакцию change; для прошлой редакции создаём копию
    if rev != int(change["revision"]):
        store.update_change(CHANGE, revision=rev)
    try:
        for artifact, role, reviewer in pairs:
            changefsm.review(
                store, root, CHANGE, artifact=artifact, reviewer_id=reviewer,
                reviewer_role=role, author_id="agent-1", verdict="approve",
                criteria=["критерий проверен"],
            )
    finally:
        if rev != int(store.get_change(CHANGE)["revision"]):
            store.update_change(CHANGE, revision=int(change["revision"]))


class CreateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_root()
        self.store = make_store(self.root)

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_creates_change_with_owner_and_risk(self) -> None:
        change = changefsm.create(self.store, self.root, CHANGE, owner="owner-1", risk_class="L2")
        self.assertEqual(change["status"], "DRAFT")
        self.assertEqual(change["risk_class"], "L2")
        self.assertEqual(change["revision"], 1)

    def test_rejects_bad_id_risk_and_owner(self) -> None:
        with self.assertRaises(changefsm.ChangeError):
            changefsm.create(self.store, self.root, "Bad_ID", owner="o")
        with self.assertRaises(changefsm.ChangeError):
            changefsm.create(self.store, self.root, "chg-y", owner="o", risk_class="L9")
        with self.assertRaises(changefsm.ChangeError):
            changefsm.create(self.store, self.root, "chg-z", owner="")

    def test_duplicate_is_rejected(self) -> None:
        changefsm.create(self.store, self.root, CHANGE, owner="o")
        with self.assertRaises(changefsm.ChangeError):
            changefsm.create(self.store, self.root, CHANGE, owner="o")


class TransitionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_root()
        self.store = make_store(self.root)

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_forbidden_transition_is_named(self) -> None:
        changefsm.create(self.store, self.root, CHANGE, owner="o")
        with self.assertRaises(changefsm.ChangeError) as ctx:
            changefsm.transition(self.store, self.root, CHANGE, "ready", {"spec_ref": SPEC_REF})
        self.assertEqual(ctx.exception.rule, "R-CHG-1")
        self.assertIn("DESIGN_AGREED", str(ctx.exception))

    def test_revision_resets_ready(self) -> None:
        drive_to_design_agreed(self.store, self.root)
        add_reviews(self.store, self.root)
        changefsm.transition(self.store, self.root, CHANGE, "ready", {"spec_ref": SPEC_REF})
        self.assertEqual(self.store.get_change(CHANGE)["status"], "READY")

        revised = changefsm.transition(self.store, self.root, CHANGE, "revise", {"reason": "BA уточнил правило"})
        self.assertEqual(revised["status"], "SPECS_AGREED")
        self.assertEqual(revised["revision"], 2)
        self.assertIsNone(revised["spec_ref"])

        with self.assertRaises(changefsm.ChangeError):
            changefsm.transition(self.store, self.root, CHANGE, "ready", {"spec_ref": SPEC_REF})

    def test_block_remembers_previous_state(self) -> None:
        drive_to_design_agreed(self.store, self.root)
        blocked = changefsm.transition(self.store, self.root, CHANGE, "block", {"reason": "ждём решение"})
        self.assertEqual(blocked["status"], "BLOCKED")
        unblocked = changefsm.transition(self.store, self.root, CHANGE, "unblock")
        self.assertEqual(unblocked["status"], "DESIGN_AGREED")


class ReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_root()
        self.store = make_store(self.root)
        changefsm.create(self.store, self.root, CHANGE, owner="o")

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_approve_without_criteria_is_rejected(self) -> None:
        with self.assertRaises(changefsm.ChangeError) as ctx:
            changefsm.review(self.store, self.root, CHANGE, artifact="proposal.md",
                             reviewer_id="ba-1", reviewer_role="BA", author_id="a-1")
        self.assertEqual(ctx.exception.rule, "R-REV-1")

    def test_author_cannot_review_own_artifact(self) -> None:
        with self.assertRaises(changefsm.ChangeError) as ctx:
            changefsm.review(self.store, self.root, CHANGE, artifact="proposal.md",
                             reviewer_id="a-1", reviewer_role="BA", author_id="a-1",
                             criteria=["проверено"])
        self.assertEqual(ctx.exception.rule, "R-REV-2")

    def test_review_record_is_written_and_hashes_artifact(self) -> None:
        record = changefsm.review(self.store, self.root, CHANGE, artifact="proposal.md",
                                  reviewer_id="ba-1", reviewer_role="BA", author_id="a-1",
                                  criteria=["цель и объём"])
        self.assertTrue(record["artifact_hash"].startswith("sha256:"))
        self.assertTrue(os.path.isfile(record["path"]))
        with open(record["path"], encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["reviewer_role"], "BA")
        self.assertEqual(saved["criteria"], ["цель и объём"])

    def test_artifact_change_invalidates_review(self) -> None:
        changefsm.review(self.store, self.root, CHANGE, artifact="proposal.md",
                         reviewer_id="ba-1", reviewer_role="BA", author_id="a-1",
                         criteria=["цель и объём"])
        path = os.path.join(self.root, "openspec", "changes", CHANGE, "proposal.md")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\nПравка после review.\n")
        report = changefsm.ready_check(self.store, self.root, CHANGE, spec_ref=SPEC_REF)
        self.assertFalse(report["ok"])
        self.assertTrue(any("proposal.md" in p for p in report["problems"]), report["problems"])


class ReadyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_root()
        self.store = make_store(self.root)
        drive_to_design_agreed(self.store, self.root)

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_ready_requires_reviews_for_every_role(self) -> None:
        report = changefsm.ready_check(self.store, self.root, CHANGE, spec_ref=SPEC_REF)
        self.assertFalse(report["ok"])
        self.assertTrue(any("BA" in p or "SA" in p or "QA" in p or "DEV" in p for p in report["problems"]))

    def test_ready_requires_spec_ref(self) -> None:
        add_reviews(self.store, self.root)
        report = changefsm.ready_check(self.store, self.root, CHANGE, spec_ref="")
        self.assertFalse(report["ok"])
        self.assertTrue(any("spec_ref" in p for p in report["problems"]))

    def test_ready_succeeds_with_full_evidence(self) -> None:
        add_reviews(self.store, self.root)
        report = changefsm.ready_check(self.store, self.root, CHANGE, spec_ref=SPEC_REF)
        self.assertTrue(report["ok"], report["problems"])
        change = changefsm.transition(self.store, self.root, CHANGE, "ready", {"spec_ref": SPEC_REF})
        self.assertEqual(change["status"], "READY")
        self.assertEqual(change["spec_ref"], SPEC_REF)


class TraceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_root()
        self.store = make_store(self.root)
        changefsm.create(self.store, self.root, CHANGE, owner="o")

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_trace_maps_requirement_to_case(self) -> None:
        report = changefsm.check_trace(self.store, self.root, CHANGE)
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual(report["requirements"][0]["id"], "REQ-X-1")
        self.assertEqual(report["requirements"][0]["cases"], ["CASE-X-1"])

    def test_requirement_without_scenario_blocks_ready(self) -> None:
        path = os.path.join(self.root, "openspec", "changes", CHANGE, "specs", "cap", "spec.md")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n### Requirement: REQ-X-2: Без сценария\n\nПросто текст.\n")
        report = changefsm.check_trace(self.store, self.root, CHANGE)
        self.assertFalse(report["ok"])
        self.assertTrue(any("REQ-X-2" in p for p in report["problems"]))

    def test_task_without_path_or_check_is_reported(self) -> None:
        path = os.path.join(self.root, "openspec", "changes", CHANGE, "tasks.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# Tasks\n\n- [ ] 1.1 Сделать хорошо\n")
        report = changefsm.check_trace(self.store, self.root, CHANGE)
        self.assertFalse(report["ok"])
        self.assertTrue(any("файл" in p for p in report["problems"]))
        self.assertTrue(any("способ проверки" in p for p in report["problems"]))


class DispatchGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_root()
        self.store = make_store(self.root)
        self.digest = specbrief.spec_digest(self.root, CHANGE)
        drive_to_design_agreed(self.store, self.root)

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def _brief(self, **over) -> dict:
        data = {
            "task_id": "T-CHG",
            "brief_version": 1,
            "change_id": CHANGE,
            "spec_ref": SPEC_REF,
            "base_commit": "b" * 40,
            "spec_version": f"openspec:{CHANGE}@{self.digest}",
            "goal": "Выдать слот",
            "expected_behavior": "Слот выдаётся",
            "allowed_files": ["src/x.py"],
            "contract": {"names": ["slot_for"]},
            "acceptance": {
                "criteria": [{"id": "AC-1", "statement": "слот 0", "mandatory": True,
                              "check": "python -m unittest tests.test_x"}],
                "check_commands": ["python -m unittest tests.test_x"],
            },
            "control": {"attempt_limit": 3, "open_questions": []},
        }
        data.update(over)
        return data

    def test_brief_cannot_open_service_not_opened_by_change(self) -> None:
        change = self.store.get_change(CHANGE)
        body = dict(change.get("payload") or {})
        body["services"] = [{"name": "api", "path": "services/api", "base_commit": SPEC_REF,
                             "write": True}]
        self.store.update_change(CHANGE, payload=body, status="READY", spec_ref=SPEC_REF)
        brief = self._brief(services=[{"name": "api", "path": "services/api",
                                       "base_commit": SPEC_REF, "write": False}])
        result = changefsm.gate_dispatch(self.store, self.root, brief)
        self.assertFalse(result["ok"])
        self.assertEqual(result["rule"], "R-CHG-1")
        self.assertIn("write", result["reason"])

        brief["services"][0]["write"] = True
        brief["services"][0]["path"] = "."
        result = changefsm.gate_dispatch(self.store, self.root, brief)
        self.assertFalse(result["ok"])
        self.assertEqual(result["rule"], "R-CHG-1")

    def test_dispatch_before_ready_is_denied(self) -> None:
        result = changefsm.gate_dispatch(self.store, self.root, self._brief())
        self.assertFalse(result["ok"])
        self.assertEqual(result["rule"], "R-CHG-1")

    def test_spec_ref_mismatch_is_denied(self) -> None:
        add_reviews(self.store, self.root)
        changefsm.transition(self.store, self.root, CHANGE, "ready", {"spec_ref": SPEC_REF})
        result = changefsm.gate_dispatch(self.store, self.root, self._brief(spec_ref="c" * 40))
        self.assertFalse(result["ok"])
        self.assertEqual(result["rule"], "R-REF-1")

    def test_dispatch_starts_change(self) -> None:
        add_reviews(self.store, self.root)
        changefsm.transition(self.store, self.root, CHANGE, "ready", {"spec_ref": SPEC_REF})
        result = changefsm.gate_dispatch(self.store, self.root, self._brief())
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.store.get_change(CHANGE)["status"], "IN_PROGRESS")

    def test_engine_dispatch_uses_change_gate(self) -> None:
        brief = self._brief()
        validated = brief_mod.validate(brief)
        self.assertTrue(validated.valid, [i.to_dict() for i in validated.errors])

        engine = Engine(self.store, Policy.load(self.root), self.root)
        self.store.create_task("T-CHG", policy_version="1.0.0", change_id=CHANGE)
        engine.apply("T-CHG", "attach_brief", {"brief": brief})

        with self.assertRaises(TransitionError) as ctx:
            engine.apply("T-CHG", "dispatch", {"worker_id": "w"})
        self.assertEqual(ctx.exception.rule, "R-CHG-1")

        add_reviews(self.store, self.root)
        changefsm.transition(self.store, self.root, CHANGE, "ready", {"spec_ref": SPEC_REF})
        engine.apply("T-CHG", "dispatch", {"worker_id": "w"})
        self.assertEqual(self.store.get_task("T-CHG")["state"], "RUNNING")
        self.assertEqual(self.store.get_change(CHANGE)["status"], "IN_PROGRESS")

    def test_actor_role_ba_publishes_analyst_scope(self) -> None:
        """Бриф BA диспетчится как analyst: пишет спеку, но не код."""
        add_reviews(self.store, self.root)
        changefsm.transition(self.store, self.root, CHANGE, "ready", {"spec_ref": SPEC_REF})
        spec_path = f"openspec/changes/{CHANGE}/specs/cap/spec.md"
        brief = self._brief(task_id="T-BA", actor_role="BA", allowed_files=[spec_path])
        brief["acceptance"]["criteria"][0]["check"] = "python -m unittest tests.test_x"
        validated = brief_mod.validate(brief)
        self.assertTrue(validated.valid, [i.to_dict() for i in validated.errors])

        engine = Engine(self.store, Policy.load(self.root), self.root)
        self.store.create_task("T-BA", policy_version="1.0.0", change_id=CHANGE)
        engine.apply("T-BA", "attach_brief", {"brief": brief})
        engine.apply("T-BA", "dispatch", {"worker_id": "ba-agent"})
        with open(os.path.join(self.root, ".harness", "guard-scope.json"), encoding="utf-8") as fh:
            scope = json.load(fh)
        self.assertEqual(scope["role"], "analyst")
        self.assertEqual(scope["allowed_files"], [spec_path])


class AcceptanceGatesTest(unittest.TestCase):
    """VERIFIED → RELEASED → ACCEPTED → ARCHIVED: без записей и evidence не пускает."""

    def setUp(self) -> None:
        self.root = make_root()
        self.store = make_store(self.root)
        drive_to_design_agreed(self.store, self.root)
        add_reviews(self.store, self.root)
        changefsm.transition(self.store, self.root, CHANGE, "ready", {"spec_ref": SPEC_REF})
        changefsm.transition(self.store, self.root, CHANGE, "start")
        changefsm.transition(self.store, self.root, CHANGE, "tasks_done")

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_report(self) -> None:
        path = os.path.join(self.root, "openspec", "changes", CHANGE, "verification.json")
        report = {
            "change_id": CHANGE,
            "revision": 1,
            "spec_ref": SPEC_REF,
            "environment": "local",
            "runner": "unittest",
            "services": [{"name": "repo", "sha": SPEC_REF}],
            "results": {
                "baseline": {"command": "python -m unittest", "tests_ran": 3, "result": "pass", "failures": []},
                "candidate": {"command": "python -m unittest", "tests_ran": 5, "result": "pass", "failures": []},
            },
            "diff": {"new_failures": [], "fixed": [], "regression": False},
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh)

    def test_merge_order_must_match_plan(self) -> None:
        services = [
            {"name": "api", "path": "services/api", "base_commit": SPEC_REF},
            {"name": "web", "path": "services/web", "base_commit": SPEC_REF},
        ]
        change = self.store.get_change(CHANGE)
        body = dict(change.get("payload") or {})
        body["services"] = services
        self.store.update_change(CHANGE, payload=body)
        report = {
            "change_id": CHANGE, "revision": 1, "spec_ref": SPEC_REF,
            "environment": "local", "runner": "unittest",
            "services": [{"name": "api", "sha": "b" * 40}, {"name": "web", "sha": "c" * 40}],
            "results": {
                "api:baseline": {"command": "ci", "tests_ran": 1, "result": "pass", "failures": []},
                "api:candidate": {"command": "ci", "tests_ran": 1, "result": "pass", "failures": []},
                "web:baseline": {"command": "ci", "tests_ran": 1, "result": "pass", "failures": []},
                "web:candidate": {"command": "ci", "tests_ran": 1, "result": "pass", "failures": []},
            },
        }
        path = os.path.join(self.root, "openspec", "changes", CHANGE, "verification.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh)
        self.store.update_change(CHANGE, status="VERIFIED")

        with self.assertRaises(changefsm.ChangeError) as ctx:
            changefsm.write_release(self.store, self.root, CHANGE, environment="local",
                                    services=list(reversed(report["services"])), by="human")
        self.assertIn("merge order", str(ctx.exception))

        record = changefsm.write_release(self.store, self.root, CHANGE, environment="local",
                                         services=report["services"], by="human")
        self.assertEqual(record["merge_order"], ["api", "web"])

    def test_verify_requires_a_real_report(self) -> None:
        with self.assertRaises(changefsm.ChangeError) as ctx:
            changefsm.transition(self.store, self.root, CHANGE, "verify")
        self.assertIn(ctx.exception.rule, ("R-QA-1", "R-QA-2"))

        self._write_report()
        change = changefsm.transition(self.store, self.root, CHANGE, "verify")
        self.assertEqual(change["status"], "VERIFIED")

    def test_release_accept_archive_need_records_and_evidence(self) -> None:
        self._write_report()
        changefsm.transition(self.store, self.root, CHANGE, "verify")

        with self.assertRaises(changefsm.ChangeError) as ctx:
            changefsm.transition(self.store, self.root, CHANGE, "release")
        self.assertEqual(ctx.exception.rule, "R-ACCEPT-1")

        changefsm.write_release(self.store, self.root, CHANGE, environment="local",
                                services=[{"name": "repo", "sha": SPEC_REF}], by="human")
        changefsm.transition(self.store, self.root, CHANGE, "release")

        with self.assertRaises(changefsm.ChangeError):
            changefsm.transition(self.store, self.root, CHANGE, "accept")
        changefsm.write_acceptance(self.store, self.root, CHANGE, accepted_by="owner",
                                   criteria=["бизнес-результат подтверждён"],
                                   environment="local", services=[{"name": "repo", "sha": SPEC_REF}])
        changefsm.transition(self.store, self.root, CHANGE, "accept")

        with self.assertRaises(changefsm.ChangeError) as ctx:
            changefsm.transition(self.store, self.root, CHANGE, "archive")
        self.assertEqual(ctx.exception.rule, "R-ACCEPT-1")

        changefsm.write_closing(self.root, CHANGE, by="owner", specs_synced=True,
                                cases_promoted=True, contracts_promoted=True)
        changefsm.write_evidence(self.store, self.root, CHANGE)
        change = changefsm.transition(self.store, self.root, CHANGE, "archive")
        self.assertEqual(change["status"], "ARCHIVED")
        self.assertTrue(os.path.isfile(os.path.join(
            self.root, "evidence", f"change-{CHANGE}", "manifest.json")))


class SpecRefCheckTest(unittest.TestCase):
    def test_missing_short_and_mismatched_spec_ref(self) -> None:
        change = {"spec_ref": SPEC_REF}
        self.assertFalse(changefsm.check_spec_ref(change, {})["ok"])
        self.assertFalse(changefsm.check_spec_ref(change, {"spec_ref": "abc"})["ok"])
        self.assertFalse(changefsm.check_spec_ref(change, {"spec_ref": "c" * 40})["ok"])
        self.assertTrue(changefsm.check_spec_ref(change, {"spec_ref": SPEC_REF})["ok"])
        self.assertTrue(changefsm.check_spec_ref(None, {})["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
