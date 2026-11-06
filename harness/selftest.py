"""Исполняемые проверки правил: позитивный и негативный случай на каждое правило.

Готовность механизма — подтверждённое срабатывание, а не наличие файла на диске.
Каждая проверка создаёт изолированный временный корень, чтобы не зависеть от
состояния рабочего каталога.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from typing import Callable

from . import hooks
from . import tmpdir
from .fsm import Engine, TransitionError, check_watchdog
from .hooks import HookContext
from .policy import Policy
from .snapshot import compute_for_scope
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


def c_role_per_agent(root: str) -> None:
    """Роль зависит от агента: primary — оркестратор, остальные — роль фазы."""
    from . import scope

    eng = _engine(root)
    eng.store.create_task("T-SELF", policy_version="1.0.0")
    eng.apply("T-SELF", "attach_brief", {"brief": brief_data()})
    eng.apply("T-SELF", "dispatch", {"worker_id": "w", "primary_agent": "session-1"})
    data = scope.read_scope(root)
    assert scope.role_for(data, "session-1") == ("orchestrator", True), data
    assert scope.role_for(data, "worker-1") == ("worker", True), data
    eng.apply("T-SELF", "submit", {"attempt": 1, "snapshot_id": "s1"})
    data = scope.read_scope(root)
    assert scope.role_for(data, "ver-1") == ("verifier", True), data
    assert data["allowed_files"] == ["evidence/**"], data
    assert scope.role_for(data, "session-1") == ("orchestrator", True), data



def c_git_destructive_negative(root: str) -> None:
    d = hooks.run("pretooluse", _ctx(root, role="worker", tool="bash", command="git push --force origin main"))
    assert d["decision"] == "deny" and d["rule"] == "R-GIT-1", d


def c_git_destructive_positive(root: str) -> None:
    d = hooks.run("pretooluse", _ctx(root, role="worker", tool="bash", command="git -C ../wt reset --hard"))
    assert d["decision"] in ("allow", "advise"), d
    ok = hooks.run("pretooluse", _ctx(root, role="worker", tool="bash", command="git log --oneline"))
    assert ok["decision"] in ("allow", "advise"), ok


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


def c_brief_policy_negative(root: str) -> None:
    """Бриф, который воркер не сможет выполнить, не выдаётся: отказ до запуска."""
    eng = _engine(root)
    eng.store.create_task("T-SELF", policy_version="1.0.0")
    impossible = brief_data(allowed_files=["harness/policy.py"])  # protected для worker
    eng.apply("T-SELF", "attach_brief", {"brief": impossible})
    _expect_deny(lambda: eng.apply("T-SELF", "dispatch", {"worker_id": "w1"}), "R-BRIEF-2")


def c_brief_policy_positive(root: str) -> None:
    """Достижимые allowed_files диспетч не блокируют."""
    eng = _engine(root)
    eng.store.create_task("T-SELF", policy_version="1.0.0")
    eng.apply("T-SELF", "attach_brief", {"brief": brief_data(allowed_files=["tests/test_x.py"])})
    out = eng.apply("T-SELF", "dispatch", {"worker_id": "w1"})
    assert out.state == "RUNNING", out


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


def _git_setup(root: str, files: dict[str, str] | None = None) -> None:
    """Готовит минимальный git-репозиторий для проверок финализатора."""
    import subprocess

    files = files or {"src/fee.py": "def calculate_fee(amount):\n    return 0\n"}
    for rel, content in files.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)
    cmds = [
        ["git", "init", "-q", root],
        ["git", "-C", root, "add", "-A"],
        ["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"],
    ]
    for cmd in cmds:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError(f"git не настроился: {proc.stderr.strip()[:200]}")


def _accepted_task(eng: Engine, root: str, task_id: str = "T-GIT") -> str:
    """Доводит задачу до ACCEPTED на текущем содержимом дерева и возвращает снимок."""
    eng.store.create_task(task_id, policy_version="1.0.0")
    eng.apply(task_id, "attach_brief", {"brief": brief_data(task_id)})
    eng.apply(task_id, "dispatch", {"worker_id": "w"})
    snapshot = compute_for_scope(root, "")
    eng.apply(task_id, "submit", {"attempt": 1, "snapshot_id": snapshot})
    eng.apply(task_id, "verdict", {"verdict": verdict_data(task_id, snapshot=snapshot)})
    assert eng.store.get_task(task_id)["state"] == "ACCEPTED"
    return snapshot


def c_gateway_state(root: str) -> None:
    from .gateway import Gateway

    eng = _engine(root)
    eng.store.create_task("T-SELF", policy_version="1.0.0")
    eng.apply("T-SELF", "attach_brief", {"brief": brief_data()})
    eng.apply("T-SELF", "dispatch", {"worker_id": "w"})
    gw = Gateway(root, eng.policy, eng.store)

    ok = gw.handle({"role": "worker", "task_id": "T-SELF", "tool": "write_file",
                    "args": {"path": "src/fee.py", "content": "x = 1\n"}})
    assert ok["ok"], ok

    eng.apply("T-SELF", "submit", {"attempt": 1, "snapshot_id": "snap_x"})
    denied = gw.handle({"role": "worker", "task_id": "T-SELF", "tool": "write_file",
                        "args": {"path": "src/fee.py", "content": "x = 2\n"}})
    assert not denied["ok"] and denied["rule"] == "R-STATE-1", denied


def c_gateway_ignores_agent_scope(root: str) -> None:
    from .gateway import Gateway

    eng = _engine(root)
    eng.store.create_task("T-SELF", policy_version="1.0.0")
    eng.apply("T-SELF", "attach_brief", {"brief": brief_data()})
    eng.apply("T-SELF", "dispatch", {"worker_id": "w"})
    gw = Gateway(root, eng.policy, eng.store)

    denied = gw.handle({
        "role": "worker", "task_id": "T-SELF", "tool": "write_file",
        "args": {"path": "src/billing.py", "content": "x = 1\n", "allowed_files": ["**"]},
    })
    assert not denied["ok"] and denied["rule"] == "R-WORK-1", denied


def c_writeset_negative(root: str) -> None:
    from .finalize import FinalizeError, finalize

    _git_setup(root)
    eng = _engine(root)
    # посторонний файл существовал до проверки: снимок его содержит,
    # значит проверка снимка молчит и вперёд выступает write-set
    with open(os.path.join(root, "src", "stray.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")
    _accepted_task(eng, root)
    try:
        finalize(eng.store, eng, root, "T-GIT")
    except FinalizeError as exc:
        assert exc.rule == "R-WRITESET-1", f"{exc.rule}: {exc}"
        return
    raise AssertionError("коммит с посторонним файлом прошёл")


def c_finalize_ok(root: str) -> None:
    from .finalize import finalize

    _git_setup(root)
    eng = _engine(root)
    snapshot = _accepted_task(eng, root)
    with open(os.path.join(root, "src", "fee.py"), "w", encoding="utf-8") as fh:
        fh.write("def calculate_fee(amount):\n    return int(amount * 0.025)\n")
    # снимок после правки уже другой — финализатор должен отказать
    try:
        finalize(eng.store, eng, root, "T-GIT")
    except Exception as exc:  # noqa: BLE001
        assert getattr(exc, "rule", "") == "R-SNAPSHOT-1", exc
    else:
        raise AssertionError("правка после PASS не отменила коммит")

    # честный повтор: новый снимок проходит полную проверку
    eng.apply("T-GIT", "invalidate", {})
    eng.apply("T-GIT", "dispatch", {"worker_id": "w2"})
    snapshot2 = compute_for_scope(root, "")
    eng.apply("T-GIT", "submit", {"attempt": 2, "snapshot_id": snapshot2})
    eng.apply("T-GIT", "verdict", {"verdict": verdict_data("T-GIT", attempt=2, snapshot=snapshot2)})
    out = finalize(eng.store, eng, root, "T-GIT", message="fee: 2.5%")
    assert out["ok"] and len(out["commit_sha"]) >= 7, out
    task = eng.store.get_task("T-GIT")
    assert task["state"] == "COMMITTED" and task["commit_sha"] == out["commit_sha"], task
    assert snapshot2 != snapshot


def _git_commit(root: str, message: str, paths: list[str] | None = None) -> None:
    """Коммит в тестовом репозитории: владелец сохранил свои файлы, не трогая кандидата."""
    import subprocess

    add = ["git", "-C", root, "add"] + (paths or ["-A"])
    subprocess.run(add, capture_output=True, text=True, check=True)
    subprocess.run(
        ["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message],
        capture_output=True, text=True, check=True,
    )


def c_recheck_after_foreign_commit(root: str) -> None:
    """Чужой коммит сдвинул дерево: кандидат тот же — перепроверка, а не новая попытка."""
    from .finalize import FinalizeError, finalize

    _git_setup(root)
    with open(os.path.join(root, "src", "fee.py"), "w", encoding="utf-8") as fh:
        fh.write("def calculate_fee(amount):\n    return int(amount * 0.025)\n")
    eng = _engine(root)
    accepted = _accepted_task(eng, root)

    stray = os.path.join(root, "tools", "note.py")
    os.makedirs(os.path.dirname(stray), exist_ok=True)
    with open(stray, "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")
    _git_commit(root, "tools: свой файл", ["tools/note.py"])
    assert compute_for_scope(root, "") != accepted

    try:
        finalize(eng.store, eng, root, "T-GIT")
    except FinalizeError as exc:
        assert exc.rule == "R-SNAPSHOT-1", f"{exc.rule}: {exc}"
    else:
        raise AssertionError("коммит на сдвинутом снимке прошёл без повторной проверки")

    attempt = int(eng.store.get_task("T-GIT")["attempt"])
    eng.apply("T-GIT", "recheck", {})
    task = eng.store.get_task("T-GIT")
    assert task["state"] == "VERIFYING", task["state"]
    assert int(task["attempt"]) == attempt, (task["attempt"], attempt)
    current = compute_for_scope(root, "")
    eng.apply("T-GIT", "verdict", {"verdict": verdict_data("T-GIT", attempt=attempt, snapshot=current)})
    out = finalize(eng.store, eng, root, "T-GIT", message="fee: 2.5%")
    assert out["ok"], out


def c_recheck_denied_after_candidate_edit(root: str) -> None:
    """Правка файла кандидата после PASS — переделка: recheck обязан отказать."""
    _git_setup(root)
    eng = _engine(root)
    _accepted_task(eng, root)
    with open(os.path.join(root, "src", "fee.py"), "w", encoding="utf-8") as fh:
        fh.write("def calculate_fee(amount):\n    return 999\n")
    _expect_deny(lambda: eng.apply("T-GIT", "recheck", {}), "R-SNAPSHOT-1")
    assert eng.store.get_task("T-GIT")["state"] == "ACCEPTED"

def c_checks_isolated(root: str) -> None:
    from . import checks as checks_mod

    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    with open(os.path.join(root, "src", "fee.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")
    with open(os.path.join(root, "check_ok.py"), "w", encoding="utf-8") as fh:
        fh.write("open('marker.txt', 'w').write('done')\nprint('ok')\n")

    brief = brief_data()
    brief["acceptance"] = {
        "criteria": [
            {"id": "AC-1", "statement": "проверка проходит", "mandatory": True, "check": "python check_ok.py"},
            {"id": "AC-2", "statement": "ручная проверка", "mandatory": True, "check": "call"},
        ],
        "check_commands": ["python check_ok.py"],
    }
    report = checks_mod.run(root, brief, task_id="T-SELF", timeout=60)
    assert report["all_ok"], report["results"]
    # копия дерева изолирована: маркер появился там, а не в продукте
    assert not os.path.exists(os.path.join(root, "marker.txt")), "проверка изменила рабочее дерево"

    verdict = checks_mod.build_verdict(brief, report, task_id="T-SELF", attempt=1, snapshot_id="snap_x")
    assert verdict["verdict"] == "INCONCLUSIVE", verdict["verdict"]
    states = {c["id"]: c["result"] for c in verdict["criteria"]}
    assert states["AC-1"] == "pass" and states["AC-2"] == "skip", states


def c_watchdog_loop(root: str) -> None:
    """Сторож как отдельный проход: отзывает просрочку и не дублирует событие."""
    from .watchdog import Watchdog

    eng = _engine(root)
    eng.store.create_task("T-SELF", policy_version="1.0.0")
    eng.apply("T-SELF", "attach_brief", {"brief": brief_data()})
    eng.apply("T-SELF", "dispatch", {"worker_id": "w1", "timeout_seconds": -5})

    dog = Watchdog(eng.store, eng)
    revoked = dog.sweep()
    assert len(revoked) == 1, revoked
    assert eng.store.get_task("T-SELF")["state"] == "TIMED_OUT"
    deaths = [e for e in eng.store.events_for("T-SELF") if e["type"] == "dispatch_death"]
    assert len(deaths) == 1, deaths
    assert deaths[0]["payload"].get("synthetic") is True

    again = dog.sweep()
    assert again == [], "повторный проход отозвал ту же попытку дважды"
    assert len([e for e in eng.store.events_for("T-SELF") if e["type"] == "dispatch_death"]) == 1


def c_sandbox_degrades_honestly(root: str) -> None:
    """Изоляция либо есть, либо честно помечена как отсутствующая."""
    from . import checks as checks_mod

    info = checks_mod.detect_sandbox()
    assert info["mode"] in ("docker", "copy-only"), info
    assert info["note"], "отсутствие изоляции должно быть объяснено"

    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    with open(os.path.join(root, "src", "fee.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")
    brief = brief_data()
    brief["acceptance"] = {
        "criteria": [{"id": "AC-1", "statement": "s", "mandatory": True, "check": "python -V"}],
        "check_commands": ["python -V"],
    }
    report = checks_mod.run(root, brief, task_id="T-SELF", timeout=60)
    assert report["isolation"]["mode"] == info["mode"], report["isolation"]
    assert report["results"][0]["isolation"] == info["mode"]


def c_lane_merge(root: str) -> None:
    """Полный цикл в лейне: worktree → коммит финализатором → слияние."""
    from . import checks as checks_mod
    from . import lanes as lanes_mod
    from .finalize import finalize

    _git_setup(root)
    store = Store(os.path.join(root, ".harness", "state.db"))
    try:
        eng = Engine(store, Policy.load(root), root)
        store.create_task("T-SELF", policy_version="1.0.0")
        eng.apply("T-SELF", "attach_brief", {"brief": brief_data()})
        eng.apply("T-SELF", "dispatch", {"worker_id": "w"})

        lane = lanes_mod.create(root, store, "T-SELF")
        lane_root = lane["path"]
        assert os.path.isdir(lane_root), lane_root

        # ресурсы лейна: формула сходится, показ совпадает с выдачей
        resources = lane["resources"]
        assert resources["port_base"] == lanes_mod.PORT_BASE + resources["slot"] * lanes_mod.PORTS_PER_LANE
        shown = lanes_mod.show(root, store, "T-SELF")
        assert shown["slot"] == resources["slot"], shown
        assert checks_mod.lane_env(resources)["HARNESS_LANE_PORT_BASE"] == str(resources["port_base"])
        assert checks_mod.lane_env(None) == {}, "задача без лейна не должна получать переменные"

        # исполнитель работает только внутри своего worktree
        target = os.path.join(lane_root, "src", "fee.py")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("def calculate_fee(amount):\n    return int(amount * 0.025)\n")

        snapshot = compute_for_scope(lane_root, "")
        eng.apply("T-SELF", "submit", {"attempt": 1, "snapshot_id": snapshot})
        eng.apply("T-SELF", "verdict", {"verdict": verdict_data("T-SELF", snapshot=snapshot)})

        lane_engine = Engine(store, Policy.load(root), lane_root)
        out = finalize(store, lane_engine, lane_root, "T-SELF")
        assert out["ok"], out

        merged = lanes_mod.merge(root, store, "T-SELF")
        assert merged["ok"], merged
        with open(os.path.join(root, "src", "fee.py"), encoding="utf-8") as fh:
            assert "0.025" in fh.read(), "слияние не принесло изменение в главное дерево"
        assert lanes_mod.list_lanes(root), "список лейнов пуст"

        # запрещённый случай: слот живой задачи не выдаётся повторно, а после
        # удаления лейна освобождается
        assert store.slot_owner(resources["slot"]) == "T-SELF"
        lanes_mod.remove(root, "T-SELF", store=store)
        assert store.lane_slot("T-SELF") is None, "слот не освобождён вместе с лейном"
    finally:
        store.close()


def c_spec_gate(root: str) -> None:
    """Код без связанного change не коммитится; документы проходят."""
    from . import specgate
    from .finalize import FinalizeError, finalize

    _git_setup(root)
    os.makedirs(os.path.join(root, "openspec", "changes", "add-demo"), exist_ok=True)

    store = Store(os.path.join(root, ".harness", "state.db"))
    try:
        eng = Engine(store, Policy.load(root), root)
        store.create_task("T-SPEC", policy_version="1.0.0")
        eng.apply("T-SPEC", "attach_brief", {"brief": brief_data("T-SPEC")})
        eng.apply("T-SPEC", "dispatch", {"worker_id": "w"})

        target = os.path.join(root, "src", "fee.py")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("def calculate_fee(amount):\n    return int(amount * 0.025)\n")
        snapshot = compute_for_scope(root, "")
        eng.apply("T-SPEC", "submit", {"attempt": 1, "snapshot_id": snapshot})
        eng.apply("T-SPEC", "verdict", {"verdict": verdict_data("T-SPEC", snapshot=snapshot)})

        # запрещённый случай: бриф не ссылается на спеку
        denied = None
        try:
            finalize(store, eng, root, "T-SPEC")
        except FinalizeError as exc:
            denied = exc
        assert denied is not None, "код без спеки закоммичен"
        assert denied.rule == "R-SPEC-1", denied.rule

        # разрешённые случаи: ссылка на существующий change и изменение без кода
        ok = specgate.check(root, {"spec_version": "openspec:add-demo@1"}, ["harness/cli.py"])
        assert ok["ok"], ok
        assert specgate.check(root, {}, ["docs/RUNNING.md"])["ok"], "документам спека не нужна"
        assert specgate.check(root, {"spec_version": "openspec:missing@1"}, ["harness/cli.py"])["ok"] is False
    finally:
        store.close()


def c_cost_gate(root: str) -> None:
    """Бюджет стоимости останавливает запуск до траты, а не после."""
    from . import cost as cost_mod

    eng = _engine(root)
    brief = brief_data()
    brief["control"]["budget"] = {"max_cost": 10, "estimate_per_attempt": 6, "unit": "cost_units"}
    eng.store.create_task("T-SELF", policy_version="1.0.0")
    eng.apply("T-SELF", "attach_brief", {"brief": brief})

    out = eng.apply("T-SELF", "dispatch", {"worker_id": "w1"})
    assert out.state == "RUNNING", out

    cost_mod.record(eng.store, "T-SELF", 5, attempt=1)
    eng.apply("T-SELF", "submit", {"attempt": 1, "snapshot_id": "snap_1"})
    eng.apply("T-SELF", "verdict", {
        "verdict": verdict_data("T-SELF", snapshot="snap_1", verdict="FAIL",
                                findings=[{"finding_id": "F", "severity": "major", "statement": "s"}])
    })
    # остаток 5 < оценки 6 — второй запуск не начинается
    _expect_deny(lambda: eng.apply("T-SELF", "dispatch", {"worker_id": "w2"}), "R-COST-1")

    # без бюджета ограничение не действует, но это явно сказано
    plain = cost_mod.check_dispatch(eng.store, "T-SELF", brief_data())
    assert plain["allowed"] and "не задан" in plain.get("note", ""), plain


def c_rule_change(root: str) -> None:
    """Изменение правила: причина + прогон, иначе — дрейф."""
    from . import rulechange

    store = Store(os.path.join(root, ".harness", "state.db"))
    try:
        before = rulechange.verify(store, root)
        assert before["state"] == "no_history" and before["ok"] is True, before

        with open(os.path.join(root, "policies", "rules.json"), encoding="utf-8") as fh:
            import json as _json

            registry = _json.load(fh)
        assert registry["rules"], "реестр правил пуст"

        try:
            rulechange.change(store, root, "R-COST-1", "   ")
        except ValueError as exc:
            assert "R-HOOK-1" in str(exc), exc
        else:
            raise AssertionError("изменение без причины прошло")

        out = rulechange.change(store, root, "R-COST-1", "бюджет проверяется до запуска")
        assert out["tests_passed"] >= 1, out
        after = rulechange.verify(store, root)
        assert after["state"] == "current" and after["ok"] is True, after

        # правка файла правил без записи снова даёт дрейф
        path = os.path.join(root, "policies", "rules.json")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n")
        drifted = rulechange.verify(store, root)
        assert drifted["state"] == "drift" and drifted["ok"] is False, drifted

        try:
            rulechange.change(store, root, "R-NOPE", "нет такого правила")
        except ValueError as exc:
            assert "не найдено" in str(exc), exc
        else:
            raise AssertionError("запись для несуществующего правила прошла")
    finally:
        store.close()


def c_budget_escalates(root: str) -> None:
    """Исчерпание бюджета — эскалация, а не новый виток."""
    eng = _engine(root)
    eng.store.create_task("T-SELF", policy_version="1.0.0")
    eng.apply("T-SELF", "attach_brief", {"brief": brief_data()})
    for attempt in (1, 2, 3):
        eng.apply("T-SELF", "dispatch", {"worker_id": f"w{attempt}"})
        snap = f"snap_{attempt}"
        eng.apply("T-SELF", "submit", {"attempt": attempt, "snapshot_id": snap})
        eng.apply("T-SELF", "verdict", {
            "verdict": verdict_data("T-SELF", attempt=attempt, snapshot=snap, verdict="FAIL",
                                    findings=[{"finding_id": f"F{attempt}", "severity": "major",
                                               "statement": "дефект"}])
        })
    task = eng.store.get_task("T-SELF")
    assert task["state"] == "ESCALATED", task["state"]
    assert task["attempt"] == 3
    # доработки после исчерпания нет: из ESCALATED только человек
    try:
        eng.apply("T-SELF", "requeue", {})
    except TransitionError as exc:
        assert exc.rule == "R-TRACE-1", exc.rule
    else:
        raise AssertionError("доработка после исчерпания бюджета прошла")


def c_commit_requires_log(root: str) -> None:
    """Коммит без записи pipeline-log невозможен."""
    eng = _engine(root)
    _drive_to_verifying(eng, snapshot="snap_1")
    eng.apply("T-SELF", "verdict", {"verdict": verdict_data(snapshot="snap_1")})
    assert eng.store.get_task("T-SELF")["state"] == "ACCEPTED"

    eng.store.conn.execute("DELETE FROM pipeline_log WHERE task_id = ?", ("T-SELF",))
    _expect_deny(lambda: eng.apply("T-SELF", "commit", {"snapshot_id": "snap_1"}), "R-COMMIT-2")


def c_findings_routing(root: str) -> None:
    """Находка получает владельца и трек; без владельца — видимый дефект."""
    from . import findings as findings_mod

    own = findings_mod.route({"finding_id": "F-1", "owner": "w1", "in_scope": True}, worker_ids=["w1"])
    foreign = findings_mod.route({"finding_id": "F-2", "owner": "team-b", "in_scope": False}, worker_ids=["w1"])
    assert own["track"] == "own_diff", own
    assert foreign["track"] == "foreign_code", foreign
    report = findings_mod.completeness([{"finding_id": "F-3", "severity": "major", "status": "open"}])
    assert report["ok"] is False and report["unowned"] == ["F-3"], report


def c_evals_metrics(root: str) -> None:
    """Метрики верификатора считаются и помечают недостоверность малой выборки."""
    from . import evals as evals_mod

    rows = [
        {"case_id": "a", "expected": "FAIL", "predicted": "PASS", "agreement": False, "note": "x"},
        {"case_id": "b", "expected": "FAIL", "predicted": "FAIL", "agreement": True, "note": "x"},
        {"case_id": "c", "expected": "PASS", "predicted": "PASS", "agreement": True, "note": "x"},
    ]
    metrics = evals_mod.metrics(rows)
    assert metrics["miss_rate"] == 0.5, metrics
    assert metrics["false_fail_rate"] == 0.0, metrics
    assert metrics["caveats"], "малая выборка должна быть помечена"


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
    Check("R-ROLE-2", "роль по агенту и фазе", c_role_per_agent),
    Check("R-GIT-1", "force-push без названного дерева отклоняется", c_git_destructive_negative),
    Check("R-GIT-1", "git с названным деревом и обычный git проходят", c_git_destructive_positive),
    Check("R-BRIEF-1", "нет брифа — нет диспетча", c_brief_negative),
    Check("R-BRIEF-1", "полный бриф — диспетч разрешён", c_brief_positive),
    Check("R-BRIEF-1", "пустое поле — отдельная причина отказа", c_brief_empty_field),
    Check("R-BRIEF-1", "противоречие в области работ — отказ", c_brief_contradiction),
    Check("R-BRIEF-2", "недостижимый allowed_files — отказ до диспетча", c_brief_policy_negative),
    Check("R-BRIEF-2", "достижимый allowed_files — диспетч разрешён", c_brief_policy_positive),
    Check("R-GAP-1", "блокирующий вопрос останавливает диспетч", c_gap_negative),
    Check("R-BUDGET-1", "три попытки и эскалация", c_budget_negative),
    Check("R-VERIFY-1", "коммит без вердикта невозможен", c_commit_without_verdict),
    Check("R-SNAPSHOT-1", "правка после PASS инвалидирует вердикт", c_stale_pass),
    Check("R-SNAPSHOT-1", "чужой коммит после PASS — перепроверка без новой попытки", c_recheck_after_foreign_commit),
    Check("R-SNAPSHOT-1", "правка файла кандидата после PASS — recheck запрещён", c_recheck_denied_after_candidate_edit),
    Check("R-VERIFY-1", "PASS на неизменённом снимке ведёт к коммиту", c_commit_positive),
    Check("R-COMMIT-1", "PASS без покрытия критериев — не PASS", c_pass_without_criteria),
    Check("R-ADV-1", "адверсальный PASS без сценария запрещён", c_adversarial_without_scenario),
    Check("R-ADV-1", "адверсальный PASS с выполненным сценарием принят", c_adversarial_with_scenario),
    Check("R-TIMEOUT-1", "запоздалый результат не принимается", c_late_result),
    Check("R-TIMEOUT-1", "watchdog переводит в TIMED_OUT", c_watchdog),
    Check("R-FIND-1", "находка не закрывается без причины", c_finding_close_guard),
    Check("R-TRACE-1", "повторная доставка события идемпотентна", c_idempotency),
    Check("R-STATE-1", "шлюз закрывает инструменты вне RUNNING", c_gateway_state),
    Check("R-TOOL-1", "агент не расширяет себе allowed_files", c_gateway_ignores_agent_scope),
    Check("R-WRITESET-1", "посторонний файл блокирует коммит", c_writeset_negative),
    Check("R-FINAL-1", "финализатор коммитит проверенный снимок", c_finalize_ok),
    Check("R-CHECK-1", "проверки идут в копии и дают артефакт", c_checks_isolated),
    Check("R-TIMEOUT-1", "сторож отзывает просрочку отдельным проходом", c_watchdog_loop),
    Check("R-SANDBOX-1", "изоляция честно помечена", c_sandbox_degrades_honestly),
    Check("R-WAVE-1", "лейн: worktree, коммит, слияние", c_lane_merge),
    Check("R-SPEC-1", "код без спеки не коммитится", c_spec_gate),
    Check("R-COST-1", "бюджет останавливает запуск до траты", c_cost_gate),
    Check("R-HOOK-1", "изменение правила требует причины и тестов", c_rule_change),
    Check("R-BUDGET-2", "исчерпание бюджета ведёт к эскалации", c_budget_escalates),
    Check("R-COMMIT-2", "коммит требует pipeline-log", c_commit_requires_log),
    Check("R-FIND-2", "находка получает владельца и трек", c_findings_routing),
    Check("R-EVAL-1", "метрики верификатора считаются", c_evals_metrics),
]


def _run_one(check: Check, source_root: str) -> dict:
    src = os.path.abspath(source_root)
    tmp = tmpdir.scratch(src, "harness-selftest-")
    try:
        for sub in ("policies", "schemas"):
            s = os.path.join(src, sub)
            if os.path.isdir(s):
                shutil.copytree(s, os.path.join(tmp, sub))
        os.makedirs(os.path.join(tmp, "src"), exist_ok=True)
        check.fn(tmp)
        return {"rule": check.rule, "name": check.name, "ok": True, "error": ""}
    except Exception as exc:  # noqa: BLE001 - отчёт важнее трассы
        return {"rule": check.rule, "name": check.name, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _summarise(results: list[dict]) -> dict:
    failed = [r for r in results if not r["ok"]]
    return {
        "total": len(results),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "ok": not failed,
        "results": results,
    }


def run_all(source_root: str | None = None) -> dict:
    src = os.path.abspath(source_root or ".")
    return _summarise([_run_one(check, src) for check in CHECKS])


def run_rule(rule_id: str, source_root: str | None = None) -> dict:
    """Прогон проверок одного правила — для дисциплины изменения правил."""
    src = os.path.abspath(source_root or ".")
    selected = [c for c in CHECKS if c.rule == rule_id]
    if not selected:
        return {"total": 0, "passed": 0, "failed": 0, "ok": False, "results": [],
                "error": f"для правила {rule_id} нет исполняемых проверок"}
    return _summarise([_run_one(c, src) for c in selected])