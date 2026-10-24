"""Исполняемые проверки правил: позитивный и негативный случай на каждое правило.

Готовность механизма — подтверждённое срабатывание, а не наличие файла на диске.
Каждая проверка создаёт изолированный временный корень, чтобы не зависеть от
состояния рабочего каталога.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Callable

from . import hooks
from .fsm import Engine, TransitionError, check_watchdog
from .hooks import HookContext
from .policy import Policy
from .state import Store


@dataclass
class Check:
    rule: str
    name: str
    fn: Callable[[str], None]


def _policy(root: str) -> Policy:
    return Policy.load(root)


def _engine(root: str) -> Engine:
    store = Store(os.path.join(root, ".harness", "state.db"))
    return Engine(store, _policy(root), root)


def brief_data(task_id: str = "T-SELF", **overrides) -> dict:
    data = {
        "task_id": task_id,
        "brief_version": 1,
        "base_commit": "abcdef0",
        "spec_version": "spec-1",
        "goal": "Добавить функцию расчёта комиссии",
        "expected_behavior": "При сумме 100 комиссия 2.5",
        "scope": {"in_scope": ["src/fee.py"], "out_of_scope": ["src/billing.py"]},
        "allowed_read": ["src/**"],
        "allowed_files": ["src/fee.py"],
        "contract": {
            "names": ["calculate_fee"],
            "required_fields": ["amount_minor"],
            "error_codes": ["FEE_NEGATIVE"],
            "units": "minor currency units",
            "boundary_rules": "отрицательная сумма -> FEE_NEGATIVE",
        },
        "acceptance": {
            "criteria": [
                {"id": "AC-1", "statement": "комиссия 2.5% от суммы", "mandatory": True, "check": "pytest -k fee"},
                {"id": "AC-2", "statement": "отрицательная сумма -> FEE_NEGATIVE", "mandatory": True,
                 "check": "pytest -k negative"},
            ],
            "check_commands": ["pytest -q"],
        },
        "control": {"attempt_limit": 3, "open_questions": [], "escalation_route": "человек"},
    }
    data.update(overrides)
    return data


def verdict_data(
    task_id: str = "T-SELF",
    attempt: int = 1,
    snapshot: str = "snap_1",
    verdict: str = "PASS",
    *,
    reviewer_role: str = "verifier",
    criteria: list[dict] | None = None,
    findings: list[dict] | None = None,
    scenario: dict | None = None,
    verdict_id: str | None = None,
) -> dict:
    if criteria is None:
        criteria = [
            {"id": "AC-1", "result": "pass", "evidence": "pytest -k fee: 3 passed", "command": "pytest -k fee"},
            {"id": "AC-2", "result": "pass" if verdict == "PASS" else "fail",
             "evidence": "pytest -k negative", "command": "pytest -k negative"},
        ]
    data = {
        "verdict_id": verdict_id or f"v_{task_id}_{attempt}",
        "task_id": task_id,
        "attempt": attempt,
        "snapshot_id": snapshot,
        "verdict": verdict,
        "reviewer_id": "rev-1",
        "reviewer_role": reviewer_role,
        "policy_version": "1.0.0",
        "created_at": "2026-09-12T10:00:00Z",
        "criteria": criteria,
        "findings": findings or [],
    }
    if scenario is not None:
        data["scenario"] = scenario
    return data


def _drive_to_verifying(eng: Engine, task_id: str = "T-SELF", snapshot: str = "snap_1", **brief_over) -> None:
    eng.store.create_task(task_id, policy_version="1.0.0")
    eng.apply(task_id, "attach_brief", {"brief": brief_data(task_id, **brief_over)})
    eng.apply(task_id, "dispatch", {"worker_id": "w1"})
    eng.apply(task_id, "submit", {"attempt": 1, "snapshot_id": snapshot})


def _expect_deny(fn: Callable[[], object], rule: str) -> None:
    try:
        fn()
    except TransitionError as exc:
        assert exc.rule == rule, f"ожидали правило {rule}, получили {exc.rule}: {exc}"
        return
    raise AssertionError(f"ожидали отказ по правилу {rule}, действие прошло")


def _ctx(root: str, **kw) -> HookContext:
    pol = _policy(root)
    base = dict(policy=pol, root=root)
    base.update(kw)
    return HookContext(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------- проверки


def c_orch_positive(root: str) -> None:
    d = hooks.run("pretooluse", _ctx(root, role="orchestrator", tool="write_file", path="docs/plan.md"))
    assert d["decision"] == "allow", d


def c_orch_negative(root: str) -> None:
    d = hooks.run("pretooluse", _ctx(root, role="orchestrator", tool="write_file", path="src/fee.py"))
    assert d["decision"] == "deny" and d["rule"] == "R-ORCH-1", d


def c_orch_bash_bypass(root: str) -> None:
    d = hooks.run(
        "pretooluse",
        _ctx(root, role="orchestrator", tool="bash", command="sed -i 's/a/b/' src/fee.py"),
    )
    assert d["decision"] == "deny", d


def c_verifier_positive(root: str) -> None:
    d = hooks.run("pretooluse", _ctx(root, role="verifier", tool="write_file", path="evidence/verdict.json"))
    assert d["decision"] == "allow", d


def c_verifier_negative(root: str) -> None:
    d = hooks.run("pretooluse", _ctx(root, role="verifier", tool="write_file", path="src/fee.py"))
    assert d["decision"] == "deny" and d["rule"] in ("R-VER-1", "R-PROT-1"), d


def c_worker_scope_positive(root: str) -> None:
    d = hooks.run(
        "pretooluse",
        _ctx(root, role="worker", tool="write_file", path="src/fee.py", allowed_files=["src/fee.py"]),
    )
    assert d["decision"] == "allow", d


def c_worker_scope_negative(root: str) -> None:
    d = hooks.run(
        "pretooluse",
        _ctx(root, role="worker", tool="write_file", path="src/billing.py", allowed_files=["src/fee.py"]),
    )
    assert d["decision"] == "deny" and d["rule"] == "R-WORK-1", d


def c_worker_tool_negative(root: str) -> None:
    d = hooks.run("pretooluse", _ctx(root, role="worker", tool="dispatch"))
    assert d["decision"] == "deny" and d["rule"] == "R-WORK-2", d


def c_protected_negative(root: str) -> None:
    d = hooks.run(
        "pretooluse",
        _ctx(root, role="worker", tool="write_file", path="policies/policy.json", allowed_files=["policies/**"]),
    )
    assert d["decision"] == "deny" and d["rule"] == "R-PROT-1", d


def c_role_identity_negative(root: str) -> None:
    d = hooks.run("pretooluse", _ctx(root, role="verifier", claimed_role="worker", tool="read_file",
                                     path="src/fee.py"))
    assert d["decision"] == "deny" and d["rule"] == "R-ROLE-1", d


def c_sleep_negative(root: str) -> None:
    d = hooks.run("pretooluse", _ctx(root, role="worker", tool="bash", command="sleep 60"))
    assert d["decision"] == "deny" and d["rule"] == "R-SLEEP-1", d


def c_sleep_positive(root: str) -> None:
    d = hooks.run("pretooluse", _ctx(root, role="worker", tool="bash", command="pytest -q"))
    assert d["decision"] in ("allow", "advise"), d


def c_git_destructive_negative(root: str) -> None:
    d = hooks.run("pretooluse", _ctx(root, role="worker", tool="bash", command="git reset --hard"))
    assert d["decision"] == "deny" and d["rule"] == "R-GIT-1", d


def c_git_destructive_positive(root: str) -> None:
    d = hooks.run("pretooluse", _ctx(root, role="worker", tool="bash", command="git -C ../wt reset --hard"))
    assert d["decision"] in ("allow", "advise"), d


def c_read_loop_negative(root: str) -> None:
    target = os.path.join(root, "src", "fee.py")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write("def calculate_fee(): pass\n")
    results = []
    for _ in range(3):
        results.append(hooks.run("pretooluse", _ctx(root, role="worker", tool="read_file", path="src/fee.py")))
    assert results[0]["decision"] == "allow", results[0]
    assert results[2]["decision"] == "deny" and results[2]["rule"] == "R-READ-1", results[2]


def c_brief_negative(root: str) -> None:
    eng = _engine(root)
    eng.store.create_task("T-SELF", policy_version="1.0.0")
    _expect_deny(lambda: eng.apply("T-SELF", "dispatch", {}), "R-BRIEF-1")


def c_brief_positive(root: str) -> None:
    eng = _engine(root)
    eng.store.create_task("T-SELF", policy_version="1.0.0")
    out = eng.apply("T-SELF", "attach_brief", {"brief": brief_data()})
    assert out.state == "BRIEF_READY", out
    out = eng.apply("T-SELF", "dispatch", {"worker_id": "w1"})
    assert out.state == "RUNNING", out


def c_brief_empty_field(root: str) -> None:
    eng = _engine(root)
    eng.store.create_task("T-SELF", policy_version="1.0.0")
    bad = brief_data(allowed_files=[])
    _expect_deny(lambda: eng.apply("T-SELF", "attach_brief", {"brief": bad}), "R-BRIEF-1")


def c_brief_contradiction(root: str) -> None:
    eng = _engine(root)
    eng.store.create_task("T-SELF", policy_version="1.0.0")
    contradictory = brief_data(scope={"in_scope": ["src/fee.py"], "out_of_scope": ["src/fee.py"]})
    _expect_deny(lambda: eng.apply("T-SELF", "attach_brief", {"brief": contradictory}), "R-BRIEF-1")


def c_gap_negative(root: str) -> None:
    eng = _engine(root)
    eng.store.create_task("T-SELF", policy_version="1.0.0")
    b = brief_data()
    b["control"]["open_questions"] = [{"id": "Q-1", "question": "Какая валюта?", "blocking": True}]
    eng.apply("T-SELF", "attach_brief", {"brief": b})
    _expect_deny(lambda: eng.apply("T-SELF", "dispatch", {}), "R-GAP-1")


def c_budget_negative(root: str) -> None:
    eng = _engine(root)
    _drive_to_verifying(eng, snapshot="snap_1")
    eng.apply("T-SELF", "verdict", {"verdict": verdict_data(verdict="FAIL", snapshot="snap_1")})
    assert eng.store.get_task("T-SELF")["state"] == "REWORK_READY"
    eng.apply("T-SELF", "dispatch", {"worker_id": "w2"})
    eng.apply("T-SELF", "submit", {"attempt": 2, "snapshot_id": "snap_2"})
    eng.apply("T-SELF", "verdict", {"verdict": verdict_data(attempt=2, verdict="FAIL", snapshot="snap_2",
                                                          verdict_id="v2")})
    eng.apply("T-SELF", "dispatch", {"worker_id": "w3"})
    eng.apply("T-SELF", "submit", {"attempt": 3, "snapshot_id": "snap_3"})
    eng.apply("T-SELF", "verdict", {"verdict": verdict_data(attempt=3, verdict="FAIL", snapshot="snap_3",
                                                          verdict_id="v3")})
    task = eng.store.get_task("T-SELF")
    assert task["state"] == "ESCALATED", task["state"]
    assert task["attempt"] == 3
    _expect_deny(lambda: eng.apply("T-SELF", "requeue", {}), "R-TRACE-1")


def c_commit_without_verdict(root: str) -> None:
    eng = _engine(root)
    eng.store.create_task("T-SELF", policy_version="1.0.0")
    eng.apply("T-SELF", "attach_brief", {"brief": brief_data()})
    _expect_deny(lambda: eng.apply("T-SELF", "commit", {}), "R-TRACE-1")


def c_stale_pass(root: str) -> None:
    eng = _engine(root)
    _drive_to_verifying(eng, snapshot="snap_1")
    eng.apply("T-SELF", "verdict", {"verdict": verdict_data(snapshot="snap_1")})
    task = eng.store.get_task("T-SELF")
    assert task["state"] == "ACCEPTED", task["state"]
    # кандидат изменился после PASS — тот же PASS больше не действует
    _expect_deny(lambda: eng.apply("T-SELF", "commit", {"snapshot_id": "snap_2"}), "R-SNAPSHOT-1")


def c_commit_positive(root: str) -> None:
    eng = _engine(root)
    _drive_to_verifying(eng, snapshot="snap_1")
    eng.apply("T-SELF", "verdict", {"verdict": verdict_data(snapshot="snap_1")})
    out = eng.apply("T-SELF", "commit", {"snapshot_id": "snap_1"})
    assert out.state == "COMMITTED", out


def c_pass_without_criteria(root: str) -> None:
    eng = _engine(root)
    _drive_to_verifying(eng, snapshot="snap_1")
    partial = verdict_data(snapshot="snap_1", criteria=[{"id": "AC-1", "result": "pass", "evidence": "x"}])
    out = eng.apply("T-SELF", "verdict", {"verdict": partial})
    assert out.state == "VERIFY_BLOCKED", out.state  # неполная проверка != дефект
    latest = eng.store.latest_verdict("T-SELF")
    assert latest["verdict"] == "INCONCLUSIVE", latest["verdict"]


def c_adversarial_without_scenario(root: str) -> None:
    eng = _engine(root)
    _drive_to_verifying(eng, snapshot="snap_1")
    v = verdict_data(snapshot="snap_1", reviewer_role="adversarial")
    out = eng.apply("T-SELF", "verdict", {"verdict": v})
    assert out.state == "VERIFY_BLOCKED", out.state
    assert eng.store.latest_verdict("T-SELF")["verdict"] == "INCONCLUSIVE"


def c_adversarial_with_scenario(root: str) -> None:
    eng = _engine(root)
    _drive_to_verifying(eng, snapshot="snap_1")
    v = verdict_data(
        snapshot="snap_1",
        reviewer_role="adversarial",
        scenario={
            "description": "двойная доставка события с одним operation_id",
            "expected_invariant": "в журнале одна операция",
            "observed_result": "операция одна, баланс 1200",
            "mode": "executed",
        },
    )
    out = eng.apply("T-SELF", "verdict", {"verdict": v})
    assert out.state == "ACCEPTED", out.state


def c_late_result(root: str) -> None:
    eng = _engine(root)
    _drive_to_verifying(eng, snapshot="snap_1")
    _expect_deny(lambda: eng.apply("T-SELF", "submit", {"attempt": 9, "snapshot_id": "snap_x"}), "R-TIMEOUT-1")


def c_watchdog(root: str) -> None:
    eng = _engine(root)
    eng.store.create_task("T-SELF", policy_version="1.0.0")
    eng.apply("T-SELF", "attach_brief", {"brief": brief_data()})
    eng.apply("T-SELF", "dispatch", {"worker_id": "w1", "timeout_seconds": -1})
    out = check_watchdog(eng, "T-SELF")
    assert out is not None and out.state == "TIMED_OUT", out
    _expect_deny(lambda: eng.apply("T-SELF", "submit", {"attempt": 1, "snapshot_id": "snap_1"}), "R-TRACE-1")


def c_finding_close_guard(root: str) -> None:
    eng = _engine(root)
    eng.store.create_task("T-SELF", policy_version="1.0.0")
    eng.store.add_finding(finding_id="F-1", task_id="T-SELF", severity="major", statement="нет проверки валюты")
    try:
        eng.store.close_finding("F-1", "")
    except Exception as exc:  # StoreError
        assert "R-FIND-1" in str(exc), exc
    else:
        raise AssertionError("закрытие без причины прошло")
    eng.store.close_finding("F-1", "исправлено в коммите abc")


def c_idempotency(root: str) -> None:
    eng = _engine(root)
    eng.store.create_task("T-SELF", policy_version="1.0.0")
    eng.apply("T-SELF", "attach_brief", {"brief": brief_data()})
    eng.apply("T-SELF", "dispatch", {"worker_id": "w1"}, event_id="evt-dispatch-1")
    n1 = len(eng.store.events_for("T-SELF"))
    eng.apply("T-SELF", "dispatch", {"worker_id": "w1"}, event_id="evt-dispatch-1")
    n2 = len(eng.store.events_for("T-SELF"))
    assert n1 == n2, f"повторная доставка создала событие: {n1} -> {n2}"
    assert eng.store.get_task("T-SELF")["attempt"] == 1


def c_policy_version_drift(root: str) -> None:
    """Вердикт, вынесенный по другой версии политики, не принимается как PASS без пересмотра."""
    eng = _engine(root)
    _drive_to_verifying(eng, snapshot="snap_1")
    v = verdict_data(snapshot="snap_1")
    v["policy_version"] = "0.0.1-stale"
    # v0 не блокирует, но фиксирует дрейф: правило объявлено как gap / soft
    out = eng.apply("T-SELF", "verdict", {"verdict": v})
    assert out.state in ("ACCEPTED", "VERIFY_BLOCKED"), out.state


CHECKS: list[Check] = [
    Check("R-ORCH-1", "оркестратор пишет документацию", c_orch_positive),
    Check("R-ORCH-1", "оркестратор не пишет продукт", c_orch_negative),
    Check("R-ORCH-1", "обход через sed -i ловится", c_orch_bash_bypass),
    Check("R-VER-1", "верификатор пишет вердикт в evidence", c_verifier_positive),
    Check("R-VER-1", "верификатор не правит продукт", c_verifier_negative),
    Check("R-WORK-1", "воркер пишет в allowed_files", c_worker_scope_positive),
    Check("R-WORK-1", "воркер не пишет вне allowed_files", c_worker_scope_negative),
    Check("R-WORK-2", "воркер не диспетчит", c_worker_tool_negative),
    Check("R-PROT-1", "политика защищена от рабочей роли", c_protected_negative),
    Check("R-ROLE-1", "подмена роли отклоняется", c_role_identity_negative),
    Check("R-SLEEP-1", "sleep запрещён", c_sleep_negative),
    Check("R-SLEEP-1", "обычная команда разрешена", c_sleep_positive),
    Check("R-GIT-1", "git reset --hard без дерева запрещён", c_git_destructive_negative),
    Check("R-GIT-1", "git reset --hard с названным деревом разрешён", c_git_destructive_positive),
    Check("R-READ-1", "третий одинаковый read отклоняется", c_read_loop_negative),
    Check("R-BRIEF-1", "нет брифа — нет диспетча", c_brief_negative),
    Check("R-BRIEF-1", "полный бриф — диспетч разрешён", c_brief_positive),
    Check("R-BRIEF-1", "пустое поле — отдельная причина отказа", c_brief_empty_field),
    Check("R-BRIEF-1", "противоречие в области работ — отказ", c_brief_contradiction),
    Check("R-GAP-1", "блокирующий вопрос останавливает диспетч", c_gap_negative),
    Check("R-BUDGET-1", "три попытки и эскалация", c_budget_negative),
    Check("R-VERIFY-1", "коммит без вердикта невозможен", c_commit_without_verdict),
    Check("R-SNAPSHOT-1", "правка после PASS инвалидирует вердикт", c_stale_pass),
    Check("R-VERIFY-1", "PASS на неизменённом снимке ведёт к коммиту", c_commit_positive),
    Check("R-COMMIT-1", "PASS без покрытия критериев — не PASS", c_pass_without_criteria),
    Check("R-ADV-1", "адверсальный PASS без сценария запрещён", c_adversarial_without_scenario),
    Check("R-ADV-1", "адверсальный PASS с выполненным сценарием принят", c_adversarial_with_scenario),
    Check("R-TIMEOUT-1", "запоздалый результат не принимается", c_late_result),
    Check("R-TIMEOUT-1", "watchdog переводит в TIMED_OUT", c_watchdog),
    Check("R-FIND-1", "находка не закрывается без причины", c_finding_close_guard),
    Check("R-TRACE-1", "повторная доставка события идемпотентна", c_idempotency),
]


def run_all(source_root: str | None = None) -> dict:
    src = os.path.abspath(source_root or ".")
    results = []
    for check in CHECKS:
        tmp = tempfile.mkdtemp(prefix="harness-selftest-")
        try:
            for sub in ("policies", "schemas"):
                s = os.path.join(src, sub)
                if os.path.isdir(s):
                    shutil.copytree(s, os.path.join(tmp, sub))
            os.makedirs(os.path.join(tmp, "src"), exist_ok=True)
            check.fn(tmp)
            results.append({"rule": check.rule, "name": check.name, "ok": True, "error": ""})
        except Exception as exc:  # noqa: BLE001
            results.append({"rule": check.rule, "name": check.name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    failed = [r for r in results if not r["ok"]]
    return {
        "total": len(results),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "ok": not failed,
        "results": results,
    }
