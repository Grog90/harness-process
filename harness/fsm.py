"""Конечный автомат задачи.

Состояние живёт в управляющем процессе, а не в рассказе агента. Переход
разрешён только при истинном охранном условии (guarded commands): ребро
недоступно, пока guard ложен, и отказ всегда называет правило.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from . import brief as brief_mod
from . import cost as cost_mod
from . import scope as scope_mod
from . import verdict as verdict_mod
from .policy import Decision, Policy, deny
from .state import Store, StoreError, new_id, now


class TransitionError(RuntimeError):
    """Переход отклонён guard'ом. Несёт правило и причину."""

    def __init__(self, rule: str, message: str, **details: Any):
        super().__init__(message)
        self.rule = rule
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "decision": "deny", "rule": self.rule, "reason": str(self), "details": self.details}


STATES = (
    "CLAIMED",
    "BRIEF_READY",
    "RUNNING",
    "VERIFYING",
    "VERIFY_BLOCKED",
    "REWORK_READY",
    "ACCEPTED",
    "COMMITTED",
    "ESCALATED",
    "TIMED_OUT",
    "CANCELLED",
)

TERMINAL = ("COMMITTED", "ESCALATED", "CANCELLED")

TRANSITIONS: dict[tuple[str, str], str] = {
    ("CLAIMED", "attach_brief"): "BRIEF_READY",
    ("CLAIMED", "dispatch"): "RUNNING",
    ("CLAIMED", "cancel"): "CANCELLED",
    ("BRIEF_READY", "dispatch"): "RUNNING",
    ("BRIEF_READY", "cancel"): "CANCELLED",
    ("BRIEF_READY", "escalate"): "ESCALATED",
    ("RUNNING", "submit"): "VERIFYING",
    ("RUNNING", "timeout"): "TIMED_OUT",
    ("RUNNING", "cancel"): "CANCELLED",
    ("VERIFYING", "submit"): "VERIFYING",
    ("VERIFYING", "verdict"): "DYNAMIC",
    ("VERIFYING", "timeout"): "TIMED_OUT",
    ("VERIFY_BLOCKED", "submit"): "VERIFYING",
    ("VERIFY_BLOCKED", "recheck"): "VERIFYING",
    ("VERIFY_BLOCKED", "escalate"): "ESCALATED",
    ("VERIFY_BLOCKED", "cancel"): "CANCELLED",
    ("REWORK_READY", "dispatch"): "RUNNING",
    ("REWORK_READY", "escalate"): "ESCALATED",
    ("REWORK_READY", "cancel"): "CANCELLED",
    ("ACCEPTED", "commit"): "COMMITTED",
    ("ACCEPTED", "invalidate"): "REWORK_READY",
    ("ACCEPTED", "escalate"): "ESCALATED",
    ("TIMED_OUT", "requeue"): "REWORK_READY",
    ("TIMED_OUT", "escalate"): "ESCALATED",
    ("TIMED_OUT", "cancel"): "CANCELLED",
}


@dataclass
class Outcome:
    ok: bool
    task_id: str
    state: str
    event: str
    message: str = ""
    rule: str = ""
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": self.ok,
            "task_id": self.task_id,
            "state": self.state,
            "event": self.event,
            "message": self.message,
        }
        if self.rule:
            out["rule"] = self.rule
        if self.data:
            out["data"] = self.data
        return out


class Engine:
    """Применяет события к состоянию, проверяя охранные условия."""

    def __init__(self, store: Store, policy: Policy, root: str = "."):
        self.store = store
        self.policy = policy
        self.root = root

    # ------------------------------------------------------------------ утилиты
    def _brief(self, task: dict[str, Any]) -> brief_mod.Brief | None:
        if not task.get("brief_json"):
            return None
        return brief_mod.validate(json.loads(task["brief_json"]), max_attempts=task["max_attempts"])

    def _emit(self, task_id: str, event: str, payload: dict[str, Any], result: dict[str, Any],
              event_id: str | None) -> str:
        eid = event_id or new_id("evt")
        # служебные ключи ("_brief", "_attempt") не пишутся в журнал: там должен быть
        # сериализуемый снимок события, а не объекты процесса
        clean = {k: v for k, v in payload.items() if not k.startswith("_")}
        self.store.append_event(eid, task_id, event, clean, result)
        return eid

    def apply(
        self,
        task_id: str,
        event: str,
        payload: dict[str, Any] | None = None,
        *,
        event_id: str | None = None,
    ) -> Outcome:
        """Единая точка входа: идемпотентность, проверка перехода, guard, запись."""
        payload = payload or {}

        # Идемпотентность: повторная доставка не создаёт второй эффект.
        if event_id:
            prior = self.store.get_event(event_id)
            if prior is not None:
                res = prior["result"]
                return Outcome(
                    ok=bool(res.get("ok", True)),
                    task_id=task_id,
                    state=str(res.get("state", "")),
                    event=event,
                    message="повторная доставка события: эффект не применён заново",
                    data={"replayed": True},
                )

        task = self.store.get_task(task_id)
        state = task["state"]

        target = TRANSITIONS.get((state, event))
        if target is None:
            raise TransitionError(
                "R-TRACE-1",
                f"переход {state} --{event}--> не существует",
                state=state,
                event=event,
                allowed=sorted(e for (s, e) in TRANSITIONS if s == state),
            )
        if state in TERMINAL:
            raise TransitionError("R-TRACE-1", f"задача в терминальном состоянии {state}", state=state)

        guard = GUARDS.get((state, event))
        if guard is not None:
            guard(self, task, payload)  # может бросить TransitionError

        if target == "DYNAMIC":
            new_state = self._resolve_verdict_state(task, payload)
        else:
            new_state = target

        fields = self._payload_effects(task, payload, new_state)
        # guard мог изменить состояние в хранилище (резерв попытки) — перечитываем
        fresh = self.store.get_task(task_id)
        self._commit_transition(fresh, event, new_state, fields)
        self._emit(task_id, event, payload, {"ok": True, "state": new_state}, event_id)
        return Outcome(True, task_id, new_state, event, data={"state": new_state})

    def _payload_effects(self, task: dict[str, Any], payload: dict[str, Any], new_state: str) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        b = payload.get("_validated_brief")
        if b is not None:
            limit = int(b.attempt_limit or self.policy.limit("max_attempts", 3))
            fields["brief_json"] = json.dumps(b.data, ensure_ascii=False, sort_keys=True)
            fields["brief_hash"] = b.hash()
            fields["brief_version"] = int(task.get("brief_version") or 0) + 1
            fields["max_attempts"] = min(limit, int(self.policy.limit("max_attempts", 3)))
        if payload.get("_snapshot"):
            fields["verifying_snapshot"] = payload["_snapshot"]
            fields["candidate_snapshot"] = payload["_snapshot"]
            fields["snapshot_scope"] = payload.get("_scope", "")
        if new_state == "ACCEPTED":
            fields["accepted_snapshot"] = task.get("verifying_snapshot") or payload.get("_snapshot")
            v = payload.get("verdict") or {}
            fields["verdict_id"] = v.get("verdict_id")
        if new_state in ("REWORK_READY", "ESCALATED", "COMMITTED", "CANCELLED"):
            fields["deadline_ts"] = None
        return fields

    def _commit_transition(self, task: dict[str, Any], event: str, new_state: str,
                           fields: dict[str, Any]) -> None:
        task_id = task["task_id"]
        fields = {**fields, "state": new_state, "last_event_id": new_id("evt")}
        self.store.update_task(task_id, **fields)
        # область прав исполнителя живёт ровно столько, сколько длится запуск
        if new_state != "RUNNING":
            scope_mod.clear_scope(self.root)
        self.store.log(task_id, f"{task['state']} -> {new_state} ({event})", attempt=task.get("attempt"))

    def _resolve_verdict_state(self, task: dict[str, Any], payload: dict[str, Any]) -> str:
        verdict = payload.get("verdict") or {}
        effective = str(verdict.get("effective_verdict") or verdict.get("verdict") or "ERROR")
        if effective == "PASS":
            return "ACCEPTED"
        if effective == "FAIL":
            return "REWORK_READY" if int(task["attempt"]) < int(task["max_attempts"]) else "ESCALATED"
        return "VERIFY_BLOCKED"


# ---------------------------------------------------------------------- guards


def _guard_attach_brief(engine: Engine, task: dict[str, Any], payload: dict[str, Any]) -> None:
    raw = payload.get("brief")
    if not isinstance(raw, dict):
        raise TransitionError("R-BRIEF-1", "бриф не передан")
    b = brief_mod.validate(raw, max_attempts=int(engine.policy.limit("max_attempts", 3)))
    if not b.valid:
        raise TransitionError(
            "R-BRIEF-1",
            "бриф не прошёл проверку: " + "; ".join(f"{i.code}:{i.field}" for i in b.errors),
            issues=[i.to_dict() for i in b.issues],
        )
    if b.task_id != task["task_id"]:
        raise TransitionError(
            "R-BRIEF-1", f"task_id брифа {b.task_id!r} не совпадает с задачей {task['task_id']!r}"
        )
    payload["_validated_brief"] = b


def _guard_no_brief(engine: Engine, task: dict[str, Any], payload: dict[str, Any]) -> None:
    """Задача без брифа: диспетч отклоняется именем правила, а не «нет перехода»."""
    raise TransitionError(
        "R-BRIEF-1",
        "у задачи нет брифа: сначала attach_brief, потом диспетч",
        state=task["state"],
    )


def _guard_dispatch(engine: Engine, task: dict[str, Any], payload: dict[str, Any]) -> None:
    b = engine._brief(task)
    if b is None:
        raise TransitionError("R-BRIEF-1", "у задачи нет брифа: диспетч запрещён")
    if not b.valid:
        raise TransitionError("R-BRIEF-1", "бриф невалиден на момент диспетча",
                              issues=[i.to_dict() for i in b.errors])

    blocking = b.blocking_questions
    if blocking:
        raise TransitionError(
            "R-GAP-1",
            "открытые блокирующие вопросы: " + "; ".join(str(q.get("id") or q.get("question")) for q in blocking),
            questions=blocking,
        )
    if not b.allowed_files:
        raise TransitionError("R-BRIEF-1", "allowed_files пуст: нечего разрешать писать")

    if int(task["attempt"]) >= int(task["max_attempts"]):
        raise TransitionError(
            "R-BUDGET-1",
            f"бюджет исчерпан: {task['attempt']}/{task['max_attempts']}",
            attempt=task["attempt"],
            max_attempts=task["max_attempts"],
        )

    # R-COST-1: стоимость проверяется до запуска, а не по факту
    budget = cost_mod.check_dispatch(engine.store, task["task_id"], b.data)
    if not budget["allowed"]:
        raise TransitionError("R-COST-1", budget["reason"], **budget)

    timeout = int(payload.get("timeout_seconds") or engine.policy.limit("dispatch_timeout_seconds", 3600))
    deadline = now() + timeout
    attempt = engine.store.reserve_attempt(
        task["task_id"], deadline_ts=deadline, worker_id=payload.get("worker_id")
    )
    # публикуем активную область прав для плагина-ограждения
    scope_mod.write_scope(
        engine.root, task_id=task["task_id"], role="worker",
        allowed_files=b.allowed_files, attempt=attempt,
    )
    payload["_attempt"] = attempt
    payload["_deadline"] = deadline


def _guard_submit(engine: Engine, task: dict[str, Any], payload: dict[str, Any]) -> None:
    attempt = int(payload.get("attempt") or 0)
    if attempt != int(task["attempt"]):
        raise TransitionError(
            "R-TIMEOUT-1",
            f"результат относится к попытке {attempt}, активна {task['attempt']}: полномочия не те",
            attempt=attempt,
            active_attempt=task["attempt"],
        )
    snapshot = str(payload.get("snapshot_id") or "")
    if not snapshot:
        raise TransitionError("R-SNAPSHOT-1", "результат подан без снимка кандидата")

    deadline = task.get("deadline_ts")
    if deadline and now() > float(deadline):
        raise TransitionError(
            "R-TIMEOUT-1",
            "срок диспетча истёк: результат не принимается, требуется событие timeout/requeue",
            deadline=deadline,
        )

    # Смена кандидата делает прежние вердикты недействительными.
    engine.store.mark_verdicts_stale(task["task_id"], keep_snapshot=snapshot)
    engine.store.finish_attempt(task["task_id"], attempt, status="submitted", snapshot_id=snapshot)
    payload["_snapshot"] = snapshot
    payload["_scope"] = str(payload.get("snapshot_scope") or "")


def _guard_verdict(engine: Engine, task: dict[str, Any], payload: dict[str, Any]) -> None:
    raw = payload.get("verdict")
    if not isinstance(raw, dict):
        raise TransitionError("R-VERIFY-1", "вердикт не передан")

    b = engine._brief(task)
    check = verdict_mod.validate(raw, brief=b)
    if not check.valid:
        raise TransitionError(
            "R-VERIFY-1",
            "вердикт не прошёл проверку контракта",
            issues=[i.to_dict() for i in check.issues],
        )

    if str(raw.get("task_id")) != task["task_id"]:
        raise TransitionError("R-VERIFY-1", "вердикт относится к другой задаче")
    if int(raw.get("attempt") or 0) != int(task["attempt"]):
        raise TransitionError(
            "R-TIMEOUT-1",
            f"вердикт по попытке {raw.get('attempt')}, активна {task['attempt']}",
        )
    if str(raw.get("snapshot_id")) != str(task.get("verifying_snapshot")):
        raise TransitionError(
            "R-SNAPSHOT-1",
            "вердикт привязан к другому снимку, чем поданный кандидат",
            verdict_snapshot=raw.get("snapshot_id"),
            candidate=task.get("verifying_snapshot"),
        )
    if raw.get("reviewer_role") == "worker":
        raise TransitionError("R-VERIFY-1", "исполнитель не может вынести вердикт по своей работе")

    effective = check.effective_verdict
    payload["verdict"] = {**raw, "effective_verdict": effective}
    engine.store.save_verdict({**raw, "verdict": effective})
    engine.store.finish_attempt(task["task_id"], task["attempt"], status=effective.lower())
    # проверка завершена: исполнителю больше нечего писать
    scope_mod.clear_scope(engine.root)

    # фактическая стоимость попытки, если проверяющий её предъявил
    raw_cost = raw.get("cost")
    if isinstance(raw_cost, (int, float)):
        budget = cost_mod.budget_of(b.data if b else None)
        cost_mod.record(
            engine.store,
            task["task_id"],
            float(raw_cost),
            unit=str(budget.get("unit", cost_mod.DEFAULT_UNIT)),
            kind="model",
            attempt=int(task["attempt"]),
            note=f"verdict {raw.get('verdict_id')}",
        )


def _guard_commit(engine: Engine, task: dict[str, Any], payload: dict[str, Any]) -> None:
    latest = engine.store.latest_verdict(task["task_id"])
    if latest is None:
        raise TransitionError("R-VERIFY-1", "нет ни одного вердикта: коммит запрещён")
    if latest["stale"]:
        raise TransitionError("R-SNAPSHOT-1", "вердикт устарел: кандидат менялся после проверки")
    if latest["verdict"] != "PASS":
        raise TransitionError("R-VERIFY-1", f"последний вердикт — {latest['verdict']}, а не PASS")

    accepted = task.get("accepted_snapshot")
    if not accepted:
        raise TransitionError("R-VERIFY-1", "нет принятого снимка")
    if latest["snapshot_id"] != accepted:
        raise TransitionError(
            "R-SNAPSHOT-1", "вердикт PASS относится к другому снимку, чем принятый кандидат"
        )

    snapshot_now = payload.get("snapshot_id")
    if snapshot_now and snapshot_now != accepted:
        raise TransitionError(
            "R-SNAPSHOT-1",
            "кандидат изменился после PASS: коммит запрещён, требуется повторная проверка",
            accepted_snapshot=accepted,
            current_snapshot=snapshot_now,
        )

    check = verdict_mod.validate(latest["payload"], brief=engine._brief(task))
    if check.effective_verdict != "PASS":
        raise TransitionError(
            "R-COMMIT-1",
            "вердикт PASS не подтверждается при повторной проверке контракта",
            effective=check.effective_verdict,
            issues=[i.to_dict() for i in check.issues],
        )

    if not engine.store.log_for(task["task_id"]):
        raise TransitionError("R-COMMIT-2", "нет записи pipeline-log: коммит без журнала запрещён")


def _guard_requeue(engine: Engine, task: dict[str, Any], payload: dict[str, Any]) -> None:
    if int(task["attempt"]) >= int(task["max_attempts"]):
        raise TransitionError("R-BUDGET-2", "бюджет исчерпан: доработка невозможна, только эскалация")


def _guard_invalidate(engine: Engine, task: dict[str, Any], payload: dict[str, Any]) -> None:
    if int(task["attempt"]) >= int(task["max_attempts"]):
        raise TransitionError("R-BUDGET-2", "бюджет исчерпан: инвалидация ведёт к эскалации, не к доработке")


GUARDS: dict[tuple[str, str], Callable[[Engine, dict[str, Any], dict[str, Any]], None]] = {
    ("CLAIMED", "attach_brief"): _guard_attach_brief,
    ("CLAIMED", "dispatch"): _guard_no_brief,
    ("BRIEF_READY", "dispatch"): _guard_dispatch,
    ("REWORK_READY", "dispatch"): _guard_dispatch,
    ("RUNNING", "submit"): _guard_submit,
    ("VERIFYING", "submit"): _guard_submit,
    ("VERIFY_BLOCKED", "submit"): _guard_submit,
    ("VERIFYING", "verdict"): _guard_verdict,
    ("ACCEPTED", "commit"): _guard_commit,
    ("TIMED_OUT", "requeue"): _guard_requeue,
    ("ACCEPTED", "invalidate"): _guard_invalidate,
}


# ---------------------------------------------------------------------- watchdog


def check_watchdog(engine: Engine, task_id: str) -> Outcome | None:
    """Принудительный перевод просроченного диспетча в TIMED_OUT (R-TIMEOUT-1).

    Смерть исполнителя не маскируется под нормальное завершение: событие
    timeout доставляется отдельно и видно в журнале.
    """
    task = engine.store.get_task(task_id)
    if task["state"] != "RUNNING":
        return None
    deadline = task.get("deadline_ts")
    if not deadline or now() <= float(deadline):
        return None
    engine.store.finish_attempt(task_id, int(task["attempt"]), status="timed_out")
    scope_mod.clear_scope(engine.root)
    engine.store.update_task(task_id, state="TIMED_OUT", deadline_ts=None)
    engine.store.append_event(
        new_id("evt"),
        task_id,
        "watchdog_timeout",
        {"attempt": task["attempt"], "deadline": deadline},
        {"ok": True, "state": "TIMED_OUT"},
    )
    engine.store.log(task_id, "watchdog: диспетч просрочен, полномочия отозваны", attempt=task["attempt"])
    return Outcome(True, task_id, "TIMED_OUT", "watchdog", "срок диспетча истёк")


# Точки контроля, которые реально существуют в коде (для отчёта о покрытии матрицы).
CONTROL_POINTS = {
    "fsm.reserve_attempt": Store.reserve_attempt,
    "fsm.apply_verdict": _guard_verdict,
    "fsm.watchdog": check_watchdog,
    "state.append_event": Store.append_event,
    "verdict.require_current_pass": _guard_commit,
    "verdict.require_checks": _guard_commit,
    "verdict.require_pipeline_log": _guard_commit,
}
