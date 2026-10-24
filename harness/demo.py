"""Сквозной демонстрационный прогон управляемого цикла.

Демонстрация без LLM: роли исполняют скрипты, но все переходы, бюджеты и
вердикты проходят через тот же код, что и в реальной работе. Цель — показать не
«красивый путь», а отказы: без брифа, без покрытия критериев, после правки
принятого кандидата, при исчерпании бюджета и по watchdog.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time

from . import brief as brief_mod
from . import verdict as verdict_mod
from .fsm import Engine, TransitionError, check_watchdog
from .hooks import HookContext, run as run_hooks
from .policy import Policy
from .snapshot import compute_snapshot
from .state import Store

TOY_CODE = '''"""Учебный модуль: комиссия."""


def calculate_fee(amount_minor: int) -> int:
    if amount_minor < 0:
        raise ValueError("FEE_NEGATIVE")
    return int(round(amount_minor * 0.025))
'''

BRIEF = {
    "task_id": "DEMO-1",
    "brief_version": 1,
    "base_commit": "9f3c1de",
    "spec_version": "spec-fee-3",
    "goal": "Реализовать calculate_fee с комиссией 2.5% и запретом отрицательной суммы",
    "expected_behavior": "amount=20000 -> 500; отрицательная сумма -> ошибка FEE_NEGATIVE",
    "scope": {"in_scope": ["src/fee.py"], "out_of_scope": ["src/billing.py"]},
    "allowed_read": ["src/**", "tests/**"],
    "allowed_files": ["src/fee.py"],
    "contract": {
        "names": ["calculate_fee"],
        "required_fields": ["amount_minor"],
        "error_codes": ["FEE_NEGATIVE"],
        "units": "minor currency units",
        "boundary_rules": "amount_minor < 0 -> ValueError('FEE_NEGATIVE')",
    },
    "acceptance": {
        "criteria": [
            {"id": "AC-1", "statement": "2.5% от суммы", "mandatory": True, "check": "pytest -k fee"},
            {"id": "AC-2", "statement": "отрицательная сумма отвергается", "mandatory": True,
             "check": "pytest -k negative"},
        ],
        "check_commands": ["pytest -q"],
    },
    "control": {"attempt_limit": 3, "open_questions": [], "escalation_route": "человек-владелец"},
}


def _verdict(task_id: str, attempt: int, snapshot: str, kind: str, *, findings=None, criteria=None,
             reviewer_role: str = "verifier", scenario=None) -> dict:
    if criteria is None:
        criteria = [
            {"id": "AC-1", "result": "pass" if kind == "PASS" else "fail",
             "evidence": "pytest -k fee: 2 passed", "command": "pytest -k fee"},
            {"id": "AC-2", "result": "pass" if kind == "PASS" else "fail",
             "evidence": "pytest -k negative: 1 passed", "command": "pytest -k negative"},
        ]
    data = {
        "verdict_id": f"v-{task_id}-{attempt}",
        "task_id": task_id,
        "attempt": attempt,
        "snapshot_id": snapshot,
        "verdict": kind,
        "reviewer_id": "verifier-1",
        "reviewer_role": reviewer_role,
        "policy_version": "1.0.0",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "criteria": criteria,
        "findings": findings or [],
    }
    if scenario:
        data["scenario"] = scenario
    return data


class Demo:
    def __init__(self, root: str, verbose: bool = True):
        self.root = root
        self.verbose = verbose
        self.lines: list[str] = []
        self.checks: list[dict] = []

    # ------------------------------------------------------------------ вывод
    def say(self, text: str = "") -> None:
        self.lines.append(text)
        if self.verbose:
            print(text)

    def step(self, title: str) -> None:
        self.say("")
        self.say(f"── {title} " + "─" * max(0, 66 - len(title)))

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "ok": ok, "detail": detail})
        mark = "OK  " if ok else "FAIL"
        self.say(f"  [{mark}] {name}" + (f" · {detail}" if detail else ""))

    def expect_deny(self, name: str, rule: str, fn) -> None:
        try:
            fn()
        except TransitionError as exc:
            self.check(name, exc.rule == rule, f"правило {exc.rule}: {exc}")
        else:
            self.check(name, False, "действие прошло, хотя должно быть отклонено")

    # ------------------------------------------------------------------ сценарий
    def run(self) -> dict:
        self.say("ДЕМО: управляемый цикл «бриф → воркер → верификация → принятие/эскалация»")
        self.say(f"рабочий каталог: {self.root}")

        for sub in ("policies", "schemas"):
            src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), sub)
            if os.path.isdir(src) and not os.path.isdir(os.path.join(self.root, sub)):
                shutil.copytree(src, os.path.join(self.root, sub))

        os.makedirs(os.path.join(self.root, "src"), exist_ok=True)
        with open(os.path.join(self.root, "src", "fee.py"), "w", encoding="utf-8") as fh:
            fh.write(TOY_CODE)

        policy = Policy.load(self.root)
        store = Store(os.path.join(self.root, ".harness", "state.db"))
        eng = Engine(store, policy, self.root)

        b = brief_mod.validate(BRIEF, max_attempts=int(policy.limit("max_attempts", 3)))
        self.step("1. Бриф")
        self.say(f"  hash брифа: {b.hash()}")
        self.say(f"  ошибок: {len(b.errors)}, предупреждений: {len(b.warnings)}")
        for w in b.warnings:
            self.say(f"    предупреждение {w.code}: {w.field} — {w.message}")
        self.check("бриф валиден", b.valid, f"{len(b.warnings)} предупреждений")

        self.step("2. Гейт: нет брифа — нет диспетча")
        store.create_task("DEMO-0", policy_version=policy.version)
        self.expect_deny("диспетч без брифа отклонён", "R-BRIEF-1",
                         lambda: eng.apply("DEMO-0", "dispatch", {}))

        self.step("3. Матрица полномочий (хуки)")
        _hook_checks(self, policy)
        self.step("4. Бюджет попыток и доработка")
        store.create_task("DEMO-1", spec_version="spec-fee-3", base_commit="9f3c1de", policy_version=policy.version)
        eng.apply("DEMO-1", "attach_brief", {"brief": BRIEF})
        eng.apply("DEMO-1", "dispatch", {"worker_id": "worker-1"})
        snapshot = compute_snapshot(os.path.join(self.root, "src"))
        self.say(f"  попытка 1 · снимок {snapshot[:18]}…")
        eng.apply("DEMO-1", "submit", {"attempt": 1, "snapshot_id": snapshot})

        fail1 = _verdict(
            "DEMO-1", 1, snapshot, "FAIL",
            findings=[{
                "finding_id": "F-1",
                "severity": "major",
                "statement": "отрицательная сумма возвращает 0 вместо ошибки FEE_NEGATIVE",
                "evidence": "python -c 'calculate_fee(-100)' -> 0",
                "owner": "worker-1",
                "track": "own_diff",
            }],
        )
        out = eng.apply("DEMO-1", "verdict", {"verdict": fail1})
        self.check("FAIL ведёт в доработку", out.state == "REWORK_READY", out.state)
        packet = verdict_mod.feedback_packet(fail1, b, remaining_budget=2)
        self.say(f"  пакет доработки: {packet['verdict_id']}, открытые находки {packet['open_finding_ids']}, "
                 f"бюджет {packet['remaining_budget']}")

        self.step("5. Исправление и неполная проверка")
        eng.apply("DEMO-1", "dispatch", {"worker_id": "worker-2"})
        with open(os.path.join(self.root, "src", "fee.py"), "w", encoding="utf-8") as fh:
            fh.write(TOY_CODE.replace("return int(round(amount_minor * 0.025))",
                                      "return int(round(amount_minor * 0.025)) if amount_minor >= 0 else 0"))
        # воркер « забыл » про отрицательные суммы — второй фикс ниже
        snapshot2 = compute_snapshot(os.path.join(self.root, "src"))
        eng.apply("DEMO-1", "submit", {"attempt": 2, "snapshot_id": snapshot2})

        partial = _verdict("DEMO-1", 2, snapshot2, "PASS",
                           criteria=[{"id": "AC-1", "result": "pass", "evidence": "pytest -k fee: 2 passed"}])
        out = eng.apply("DEMO-1", "verdict", {"verdict": partial})
        self.check("PASS без покрытия AC-2 понижен до INCONCLUSIVE",
                   out.state == "VERIFY_BLOCKED", f"состояние {out.state}")
        latest = store.latest_verdict("DEMO-1")
        self.say(f"  сохранённый вердикт: {latest['verdict']} (не PASS)")

        self.step("6. Повторная проверка тем же контрактом")
        with open(os.path.join(self.root, "src", "fee.py"), "w", encoding="utf-8") as fh:
            fh.write(TOY_CODE)
        snapshot3 = compute_snapshot(os.path.join(self.root, "src"))
        eng.apply("DEMO-1", "submit", {"attempt": 2, "snapshot_id": snapshot3})
        good = _verdict("DEMO-1", 2, snapshot3, "PASS")
        out = eng.apply("DEMO-1", "verdict", {"verdict": good})
        self.check("полный PASS принят", out.state == "ACCEPTED", out.state)

        self.step("7. Правка после PASS инвалидирует вердикт")
        with open(os.path.join(self.root, "src", "fee.py"), "a", encoding="utf-8") as fh:
            fh.write("\n# hotfix после ревью\n")
        changed = compute_snapshot(os.path.join(self.root, "src"))
        self.expect_deny("коммит изменённого кандидата отклонён", "R-SNAPSHOT-1",
                         lambda: eng.apply("DEMO-1", "commit", {"snapshot_id": changed}))

        self.step("8. Инвалидация и третий виток")
        out = eng.apply("DEMO-1", "invalidate", {})
        self.check("инвалидация вернула в доработку", out.state == "REWORK_READY", out.state)
        eng.apply("DEMO-1", "dispatch", {"worker_id": "worker-3"})
        with open(os.path.join(self.root, "src", "fee.py"), "w", encoding="utf-8") as fh:
            fh.write(TOY_CODE)
        snapshot4 = compute_snapshot(os.path.join(self.root, "src"))
        eng.apply("DEMO-1", "submit", {"attempt": 3, "snapshot_id": snapshot4})
        out = eng.apply("DEMO-1", "verdict", {"verdict": _verdict("DEMO-1", 3, snapshot4, "PASS")})
        self.check("третий виток принят", out.state == "ACCEPTED", out.state)
        out = eng.apply("DEMO-1", "commit", {"snapshot_id": snapshot4})
        self.check("коммит принятого снимка", out.state == "COMMITTED", out.state)

        self.step("9. Исчерпание бюджета → эскалация")
        store.create_task("DEMO-2", policy_version=policy.version)
        brief2 = dict(BRIEF, task_id="DEMO-2")
        eng.apply("DEMO-2", "attach_brief", {"brief": brief2})
        for attempt in (1, 2, 3):
            eng.apply("DEMO-2", "dispatch", {"worker_id": f"worker-{attempt}"})
            snap = compute_snapshot(os.path.join(self.root, "src"))
            eng.apply("DEMO-2", "submit", {"attempt": attempt, "snapshot_id": snap})
            finding = [{
                "finding_id": f"F-{attempt}",
                "severity": "major",
                "statement": "комиссия считается в целых процентах",
                "evidence": "pytest -k fee: 1 failed",
                "owner": f"worker-{attempt}",
                "track": "own_diff",
            }]
            out = eng.apply("DEMO-2", "verdict", {
                "verdict": _verdict("DEMO-2", attempt, snap, "FAIL", findings=finding)
            })
            self.say(f"  попытка {attempt}: {out.state}")
        task2 = store.get_task("DEMO-2")
        self.check("после трёх FAIL — ESCALATED", task2["state"] == "ESCALATED", task2["state"])
        self.expect_deny("четвёртый диспетч невозможен", "R-TRACE-1",
                         lambda: eng.apply("DEMO-2", "dispatch", {"worker_id": "worker-4"}))

        self.step("10. Watchdog: просроченный диспетч")
        store.create_task("DEMO-3", policy_version=policy.version)
        eng.apply("DEMO-3", "attach_brief", {"brief": dict(BRIEF, task_id="DEMO-3")})
        eng.apply("DEMO-3", "dispatch", {"worker_id": "worker-x", "timeout_seconds": -5})
        out = check_watchdog(eng, "DEMO-3")
        self.check("watchdog отозвал полномочия", bool(out and out.state == "TIMED_OUT"),
                   out.state if out else "нет события")
        self.expect_deny("запоздалый submit не принимается", "R-TRACE-1",
                         lambda: eng.apply("DEMO-3", "submit", {"attempt": 1, "snapshot_id": "snap_late"}))

        self.step("11. Идемпотентность и журнал")
        store.create_task("DEMO-4", policy_version=policy.version)
        eng.apply("DEMO-4", "attach_brief", {"brief": dict(BRIEF, task_id="DEMO-4")})
        eng.apply("DEMO-4", "dispatch", {"worker_id": "worker-dup"}, event_id="evt-dup-1")
        before = len(store.events_for("DEMO-4"))
        replay = eng.apply("DEMO-4", "dispatch", {"worker_id": "worker-dup"}, event_id="evt-dup-1")
        after = len(store.events_for("DEMO-4"))
        self.check(
            "повторная доставка не создаёт второй диспетч",
            before == after and store.get_task("DEMO-4")["attempt"] == 1,
            f"событий {before} -> {after}, попытка {store.get_task('DEMO-4')['attempt']}, "
            f"replay={bool(replay.data and replay.data.get('replayed'))}",
        )
        log = store.log_for("DEMO-1")
        self.check("pipeline-log заполнен", len(log) > 0, f"{len(log)} записей")

        self.step("12. Находки и полнота обратной связи")
        from . import findings as findings_mod

        f1 = store.add_finding(finding_id="F-10", task_id="DEMO-1", severity="major",
                               statement="нет проверки валюты", owner="worker-1", track="own_diff")
        report = findings_mod.completeness([f1])
        self.check("находка с владельцем и треком", report["ok"], json.dumps(report, ensure_ascii=False)[:80])

        ok = all(c["ok"] for c in self.checks)
        self.say("")
        self.say(f"ИТОГ: {sum(1 for c in self.checks if c['ok'])}/{len(self.checks)} проверок пройдено "
                 f"— {'цикл держится' if ok else 'есть провалы'}")
        return {"ok": ok, "checks": self.checks, "log": self.lines}


def _hook_checks(demo: Demo, policy: Policy) -> None:
    def hook(**kw):
        kw.setdefault("policy", policy)
        kw.setdefault("root", demo.root)
        return run_hooks("pretooluse", HookContext(**kw))

    r = hook(role="orchestrator", tool="write_file", path="src/fee.py")
    demo.check("оркестратор не пишет продуктовый код", r["decision"] == "deny", r["rule"])
    r = hook(role="orchestrator", tool="write_file", path="docs/plan.md")
    demo.check("оркестратор пишет документацию", r["decision"] == "allow", r["rule"])
    r = hook(role="orchestrator", tool="bash", command="tee src/fee.py")
    demo.check("обход через tee отклонён", r["decision"] == "deny", r["rule"])
    r = hook(role="worker", tool="write_file", path="src/billing.py", allowed_files=["src/fee.py"])
    demo.check("воркер вне allowed_files", r["decision"] == "deny", r["rule"])
    r = hook(role="worker", tool="bash", command="sleep 120")
    demo.check("sleep запрещён", r["decision"] == "deny", r["rule"])
    r = hook(role="verifier", tool="write_file", path="src/fee.py")
    demo.check("верификатор не правит продукт", r["decision"] == "deny", r["rule"])
    r = hook(role="verifier", tool="write_file", path="evidence/verdict.json")
    demo.check("верификатор пишет вердикт", r["decision"] == "allow", r["rule"])


def run_demo(root: str, verbose: bool = True, as_json: bool = False) -> int:
    work = tempfile.mkdtemp(prefix="harness-demo-")
    try:
        demo = Demo(work, verbose=verbose)
        report = demo.run()
        if as_json:
            print(json.dumps({"ok": report["ok"], "checks": report["checks"]}, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 2
    finally:
        shutil.rmtree(work, ignore_errors=True)
