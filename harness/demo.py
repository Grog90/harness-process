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
from .snapshot import compute_for_scope, compute_snapshot
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
        from .watchdog import Watchdog

        store.create_task("DEMO-3", policy_version=policy.version)
        eng.apply("DEMO-3", "attach_brief", {"brief": dict(BRIEF, task_id="DEMO-3")})
        eng.apply("DEMO-3", "dispatch", {"worker_id": "worker-x", "timeout_seconds": -5})
        revoked = Watchdog(store, eng).sweep()
        self.check("отдельный проход сторожа отозвал полномочия",
                   len(revoked) == 1 and store.get_task("DEMO-3")["state"] == "TIMED_OUT",
                   f"{len(revoked)} отозвано")
        deaths = [e for e in store.events_for("DEMO-3") if e["type"] == "dispatch_death"]
        self.check("смерть диспетча доставлена отдельным событием", len(deaths) == 1,
                   f"событий dispatch_death: {len(deaths)}")
        self.check("повторный проход ничего не отзывает", Watchdog(store, eng).sweep() == [],
                   "идемпотентно")
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

        self.step("13. Шлюз инструментов: агент не пишет напрямую")
        self._gateway_steps(store, eng, policy)

        self.step("14. Изолированный прогон проверок")
        self._checks_steps()

        self.step("15. Доверенный финализатор")
        self._finalize_steps(store, policy)

        self.step("16. Параллельный лейн: worktree → коммит → слияние")
        self._lane_steps(store, policy)

        ok = all(c["ok"] for c in self.checks)
        self.say("")
        self.say(f"ИТОГ: {sum(1 for c in self.checks if c['ok'])}/{len(self.checks)} проверок пройдено "
                 f"— {'цикл держится' if ok else 'есть провалы'}")
        return {"ok": ok, "checks": self.checks, "log": self.lines}

    # ---------------------------------------------------------------- новые шаги
    def _gateway_steps(self, store: Store, eng: Engine, policy: Policy) -> None:
        from .gateway import Gateway

        store.create_task("DEMO-5", policy_version=policy.version)
        eng.apply("DEMO-5", "attach_brief", {"brief": dict(BRIEF, task_id="DEMO-5")})
        eng.apply("DEMO-5", "dispatch", {"worker_id": "gw-worker"})
        gw = Gateway(self.root, policy, store)

        allowed = gw.handle({"role": "worker", "task_id": "DEMO-5", "tool": "write_file",
                             "args": {"path": "src/fee.py", "content": TOY_CODE}})
        self.check("запись в allowed_files через шлюз", allowed["ok"], str(allowed.get("rule", "")))

        denied = gw.handle({"role": "worker", "task_id": "DEMO-5", "tool": "write_file",
                            "args": {"path": "src/billing.py", "content": "x = 1\n"}})
        self.check("запись вне брифа отклонена шлюзом", not denied["ok"] and denied["rule"] == "R-WORK-1",
                   denied.get("rule", ""))

        escalated = gw.handle({"role": "worker", "task_id": "DEMO-5", "tool": "write_file",
                               "args": {"path": "policies/policy.json", "content": "{}"}})
        self.check("политика недоступна рабочей роли", not escalated["ok"], escalated.get("rule", ""))

        cmd = gw.handle({"role": "worker", "task_id": "DEMO-5", "tool": "run_command",
                         "args": {"command": "echo harness-ok"}})
        self.check("run_command выполнен с урезанным окружением",
                   cmd["ok"] and cmd["result"]["exit_code"] == 0 and "harness-ok" in cmd["result"]["stdout"],
                   f"код {cmd.get('result', {}).get('exit_code')}")

        sleep = gw.handle({"role": "worker", "task_id": "DEMO-5", "tool": "run_command",
                           "args": {"command": "sleep 30"}})
        self.check("ожидание отклонено до запуска", not sleep["ok"] and sleep["rule"] == "R-SLEEP-1",
                   sleep.get("rule", ""))

    def _checks_steps(self) -> None:
        from . import checks as checks_mod

        brief = dict(BRIEF, task_id="DEMO-CHECKS")
        brief["acceptance"] = {
            "criteria": [
                {"id": "AC-1", "statement": "модуль импортируется", "mandatory": True,
                 "check": "python -c \"import sys; sys.path.insert(0, 'src'); import fee\""},
                {"id": "AC-2", "statement": "ручная проверка", "mandatory": True, "check": "call"},
            ],
            "check_commands": ["python -c \"import sys; sys.path.insert(0, 'src'); import fee\""],
        }
        report = checks_mod.run(self.root, brief, task_id="DEMO-CHECKS", timeout=60)
        self.check("команды проверки выполнены в копии", report["all_ok"],
                   f"{len(report['results'])} команд, артефакт {os.path.basename(report['artifacts'])}")
        self.say(f"  изоляция: {report['isolation']['mode']} — {report['isolation']['note']}")
        self.check("режим изоляции назван явно", report["isolation"]["mode"] in ("docker", "copy-only"),
                   report["isolation"]["mode"])
        verdict = checks_mod.build_verdict(brief, report, task_id="DEMO-CHECKS", attempt=1, snapshot_id="snap_x")
        self.check("критерий без запуска даёт skip и INCONCLUSIVE", verdict["verdict"] == "INCONCLUSIVE",
                   verdict["verdict"])
        self.check("копия не изменила рабочее дерево",
                   not os.path.exists(os.path.join(self.root, "marker.txt")), "маркеров нет")

    def _finalize_steps(self, store: Store, policy: Policy) -> None:
        import subprocess

        from .finalize import finalize

        def git(*cmd):
            proc = subprocess.run(["git", "-C", self.root, *cmd], capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip()[:200])
            return proc.stdout.strip()

        subprocess.run(["git", "init", "-q", self.root], check=True)
        git("-c", "user.name=demo", "-c", "user.email=demo@local", "add", "-A")
        git("-c", "user.name=demo", "-c", "user.email=demo@local", "commit", "-q", "-m", "base")

        store.create_task("DEMO-F", policy_version=policy.version)
        eng = Engine(store, policy, self.root)
        eng.apply("DEMO-F", "attach_brief", {"brief": dict(BRIEF, task_id="DEMO-F")})
        eng.apply("DEMO-F", "dispatch", {"worker_id": "fin-worker"})
        with open(os.path.join(self.root, "src", "fee.py"), "w", encoding="utf-8") as fh:
            fh.write(TOY_CODE.replace('raise ValueError("FEE_NEGATIVE")', 'raise ValueError("FEE_NEGATIVE")  # ok'))
        snapshot = compute_for_scope(self.root, "")
        eng.apply("DEMO-F", "submit", {"attempt": 1, "snapshot_id": snapshot})
        eng.apply("DEMO-F", "verdict", {"verdict": _verdict("DEMO-F", 1, snapshot, "PASS")})

        with open(os.path.join(self.root, "src", "extra.py"), "w", encoding="utf-8") as fh:
            fh.write("# обход шлюза\n")
        try:
            finalize(store, eng, self.root, "DEMO-F")
        except Exception as exc:  # noqa: BLE001
            rule = getattr(exc, "rule", "")
            self.check("посторонний файл блокирует коммит", rule in ("R-WRITESET-1", "R-SNAPSHOT-1"), rule)
        else:
            self.check("посторонний файл блокирует коммит", False, "коммит прошёл")

        os.unlink(os.path.join(self.root, "src", "extra.py"))
        out = finalize(store, eng, self.root, "DEMO-F", message="fee: 2.5%")
        self.check("финализатор закоммитил проверенный снимок", out["ok"] and len(out["commit_sha"]) >= 7,
                   out["commit_sha"][:10])
        log_in_commit = subprocess.run(
            ["git", "-C", self.root, "show", "--name-only", "--pretty=format:", out["commit_sha"]],
            capture_output=True, text=True,
        ).stdout
        self.check("журнал едет в том же коммите", f"pipeline-log/DEMO-F.jsonl" in log_in_commit,
                   "pipeline-log/DEMO-F.jsonl")
        self.check("связь «снимок → коммит» записана",
                   store.commit_for_task("DEMO-F")["commit_sha"] == out["commit_sha"], out["commit_sha"][:10])

    def _lane_steps(self, store: Store, policy: Policy) -> None:
        from . import lanes as lanes_mod
        from .finalize import finalize

        store.create_task("DEMO-L", policy_version=policy.version)
        eng = Engine(store, policy, self.root)
        eng.apply("DEMO-L", "attach_brief", {"brief": dict(BRIEF, task_id="DEMO-L")})
        eng.apply("DEMO-L", "dispatch", {"worker_id": "lane-worker"})

        lane = lanes_mod.create(self.root, store, "DEMO-L")
        lane_root = lane["path"]
        self.check("worktree создан вне главного дерева",
                   os.path.isdir(lane_root) and lane_root != self.root, lane["lane"])

        with open(os.path.join(lane_root, "src", "fee.py"), "w", encoding="utf-8") as fh:
            fh.write(TOY_CODE + "\n# правка в лейне\n")

        snapshot = compute_for_scope(lane_root, "")
        eng.apply("DEMO-L", "submit", {"attempt": 1, "snapshot_id": snapshot})
        eng.apply("DEMO-L", "verdict", {"verdict": _verdict("DEMO-L", 1, snapshot, "PASS")})

        lane_engine = Engine(store, policy, lane_root)
        committed = finalize(store, lane_engine, lane_root, "DEMO-L", message="fee lane")
        self.check("финализатор закоммитил в лейне", committed["ok"], committed["commit_sha"][:10])

        try:
            lanes_mod.merge(self.root, store, "DEMO-1")
        except lanes_mod.LaneError as exc:
            self.check("в главное дерево сливается только COMMITTED", exc.rule == "R-FINAL-1", exc.rule)

        merged = lanes_mod.merge(self.root, store, "DEMO-L")
        self.check("лейн слит под мьютексом", merged["ok"], merged["merge_commit"][:10])
        with open(os.path.join(self.root, "src", "fee.py"), encoding="utf-8") as fh:
            self.check("изменение лейна видно в главном дереве", "# правка в лейне" in fh.read(),
                       "src/fee.py")


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
