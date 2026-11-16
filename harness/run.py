"""Драйвер цикла: запуск задачи через пайплайн одной командой.

Раньше слои были на месте, но собрать их в один прогон приходилось руками.
Здесь описан весь маршрут:

    бриф → диспетч → воркер → снимок → проверка → вердикт
                       ↑                              │
                       └──── доработка (≤ 3) ─────────┘
                                              PASS → финализатор → коммит

Исполнитель и проверяющий — внешние команды. Это принципиально: харнесс не знает,
LLM у вас внутри или скрипт, и не должен знать. Он задаёт контракт окружения и
проверяет результат.

Контракт воркера:
  env  HARNESS_ROOT, HARNESS_ROLE=worker, HARNESS_TASK_ID, HARNESS_ATTEMPT,
       HARNESS_BRIEF (путь к JSON), HARNESS_FEEDBACK (путь к пакету доработки)
  выход 0 — работа сделана; иначе — инфраструктурный сбой, а не дефект кода

Контракт проверяющего:
  stdin JSON {task_id, attempt, candidate_dir, snapshot_id, brief}
  env     HARNESS_ROLE=verifier, HARNESS_VERDICT_OUT (куда положить вердикт)
  выход  вердикт в файл HARNESS_VERDICT_OUT или JSON в stdout
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

from . import brief as brief_mod
from . import verdict as verdict_mod
from .finalize import FinalizeError, finalize
from .fsm import Engine, TransitionError
from .policy import Policy
from .snapshot import scope_of, snapshot_for_task
from .state import Store, now


class RunError(RuntimeError):
    """Остановка прогона по внешней причине (не переход автомата)."""


@dataclass
class RunConfig:
    root: str
    task_id: str
    brief_path: str | None = None
    worker_cmd: str = ""
    verifier_cmd: str | None = None
    adversarial_cmd: str | None = None
    commit: bool = False
    lane: bool = False
    merge: bool = False
    allow_no_spec: str | None = None
    scope_override: str | None = None
    timeout: int | None = None
    message: str | None = None
    worktimeout_grace: int = 30
    env_extra: dict[str, str] = field(default_factory=dict)


@dataclass
class RunResult:
    ok: bool
    task_id: str
    state: str
    attempts: int
    commit_sha: str | None = None
    verdicts: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "task_id": self.task_id,
            "state": self.state,
            "attempts": self.attempts,
            "commit_sha": self.commit_sha,
            "verdicts": self.verdicts,
            "reason": self.reason,
        }


class Runner:
    def __init__(self, config: RunConfig, store: Store, policy: Policy, engine: Engine):
        self.cfg = config
        self.store = store
        self.policy = policy
        self.engine = engine
        self.root = os.path.abspath(config.root)
        self.log: list[str] = []

    # ------------------------------------------------------------------ служебное
    def say(self, text: str) -> None:
        self.log.append(text)

    def _env(self, role: str, attempt: int, extra: dict[str, str] | None = None,
             root: str | None = None) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", self.root),
            "TMPDIR": os.environ.get("TMPDIR", ""),
            "PYTHONPATH": os.pathsep.join(filter(None, [os.getcwd(), os.environ.get("PYTHONPATH")])),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "HARNESS_ROOT": root or self.root,
            "HARNESS_CONTROL_ROOT": self.root,
            "HARNESS_STATE": self.store.path,
            "HARNESS_ROLE": role,
            "HARNESS_TASK_ID": self.cfg.task_id,
            "HARNESS_ATTEMPT": str(attempt),
        }
        if self.cfg.brief_path:
            env["HARNESS_BRIEF"] = os.path.abspath(self.cfg.brief_path)
        env.update(self.cfg.env_extra)
        env.update(extra or {})
        return env

    def _brief_data(self) -> dict[str, Any]:
        task = self.store.get_task(self.cfg.task_id)
        if not task.get("brief_json"):
            raise RunError("у задачи нет брифа: запуск невозможен")
        return json.loads(task["brief_json"])

    def _task(self) -> dict[str, Any]:
        return self.store.get_task(self.cfg.task_id)

    # ------------------------------------------------------------------ запуск
    def run(self) -> RunResult:
        cfg = self.cfg
        if not cfg.worker_cmd:
            raise RunError("не задана команда воркера")

        task = self.store.find_task(cfg.task_id)
        if task is None:
            self.store.create_task(
                cfg.task_id,
                policy_version=self.policy.version,
                max_attempts=int(self.policy.limit("max_attempts", 3)),
            )
            self.say(f"создана задача {cfg.task_id}")
            task = self._task()

        if task["state"] == "CLAIMED":
            if not cfg.brief_path:
                raise RunError("задача без брифа: нужен --brief")
            with open(cfg.brief_path, encoding="utf-8") as fh:
                raw = json.load(fh)
            out = self.engine.apply(cfg.task_id, "attach_brief", {"brief": raw})
            self.say(f"бриф принят → {out.state}")

        if cfg.merge:
            cfg.commit = True  # сливать можно только закоммиченное

        lane_path = None
        if cfg.lane:
            lane_path = self._ensure_lane()

        max_rounds = int(self._task()["max_attempts"]) + 1
        verdicts: list[str] = []

        for _ in range(max_rounds):
            state = self._task()["state"]
            if state in ("ACCEPTED", "COMMITTED"):
                break
            if state not in ("BRIEF_READY", "REWORK_READY"):
                return self._finish(state, verdicts, f"остановка в состоянии {state}")

            attempt = int(self._task()["attempt"]) + 1
            try:
                self.engine.apply(cfg.task_id, "dispatch", {"worker_id": f"runner-attempt-{attempt}"})
            except TransitionError as exc:
                return self._finish(self._task()["state"], verdicts, f"[{exc.rule}] {exc}")
            self.say(f"попытка {attempt}: диспетч")

            work_root = lane_path or self.root
            code = self._run_worker(work_root, attempt)
            if code != 0:
                self._fail_infrastructure(attempt, work_root, f"воркер завершился с кодом {code}")
                verdicts.append("ERROR")
                return self._finish(self._task()["state"], verdicts, "инфраструктурный сбой воркера")

            # область снимка задаётся относительно корня работы: и главное дерево,
            # и worktree считаются от своего корня, иначе путь лейна удваивается
            scope = cfg.scope_override or ""
            snapshot = snapshot_for_task(work_root, self._task(), scope)
            try:
                self.engine.apply(
                    cfg.task_id, "submit",
                    {"attempt": attempt, "snapshot_id": snapshot, "snapshot_scope": scope},
                )
            except TransitionError as exc:
                return self._finish(self._task()["state"], verdicts, f"[{exc.rule}] {exc}")
            self.say(f"снимок {snapshot[:20]}… принят на проверку")

            verdict = self._run_verifier(attempt, work_root, snapshot)
            if verdict is None:
                self._fail_infrastructure(attempt, work_root, "проверяющий не выдал вердикт")
                verdicts.append("ERROR")
                return self._finish(self._task()["state"], verdicts, "проверка не состоялась")

            adversarial = self._run_adversarial(attempt, work_root, snapshot) if cfg.adversarial_cmd else None
            if adversarial is not None and str(adversarial.get("verdict", "")).upper() == "FAIL":
                verdict = adversarial
                self.say("адверсальная проверка нашла дефект")

            try:
                out = self.engine.apply(cfg.task_id, "verdict", {"verdict": verdict})
            except TransitionError as exc:
                return self._finish(self._task()["state"], verdicts, f"[{exc.rule}] {exc}")

            effective = str(verdict.get("effective_verdict") or verdict.get("verdict"))
            verdicts.append(effective)
            self.say(f"вердикт: {effective} → {out.state}")

            if out.state == "ACCEPTED":
                if cfg.commit:
                    try:
                        done = finalize(
                            self.store, self.engine, work_root, cfg.task_id,
                            message=cfg.message, ignore_paths=self._ignore_paths(work_root),
                            allow_no_spec=cfg.allow_no_spec,
                        )
                    except FinalizeError as exc:
                        return self._finish(out.state, verdicts, f"[{exc.rule}] {exc}", ok_override=False)
                    self.say(f"коммит {done['commit_sha'][:10]}")
                    if cfg.merge and lane_path:
                        return self._merge(lane_path, verdicts)
                    return RunResult(True, cfg.task_id, "COMMITTED", int(self._task()["attempt"]),
                                     commit_sha=done["commit_sha"], verdicts=verdicts, log=self.log)
                return RunResult(True, cfg.task_id, "ACCEPTED", int(self._task()["attempt"]),
                                 verdicts=verdicts, log=self.log)
            if out.state == "REWORK_READY":
                self._write_feedback(attempt, verdict)
                continue
            return self._finish(out.state, verdicts, f"остановка: {out.state}")

        return self._finish(self._task()["state"], verdicts, "цикл завершён")

    # ------------------------------------------------------------------ шаги
    def _ensure_lane(self) -> str:
        from . import lanes

        existing = lanes.lane_path(self.root, self.cfg.task_id)
        if os.path.isdir(existing):
            return existing
        lane = lanes.create(self.root, self.store, self.cfg.task_id)
        self.say(f"создан лейн {lane['lane']}")
        return lane["path"]

    def _run_worker(self, work_root: str, attempt: int) -> int:
        extra = {"HARNESS_ALLOWED_FILES": json.dumps(self._brief_data().get("allowed_files", []), ensure_ascii=False)}
        if self.cfg.brief_path:
            extra["HARNESS_BRIEF"] = os.path.abspath(self.cfg.brief_path)
        feedback = os.path.join(self.root, ".harness", "feedback", f"{self.cfg.task_id}-{attempt - 1}.json")
        if os.path.isfile(feedback):
            extra["HARNESS_FEEDBACK"] = feedback

        timeout = self.cfg.timeout or int(self.policy.limit("dispatch_timeout_seconds", 3600))
        argv = shlex.split(self.cfg.worker_cmd, posix=(os.name != "nt"))
        argv = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] == '"' else t for t in argv]
        self.say(f"воркер: {self.cfg.worker_cmd}")
        try:
            proc = subprocess.run(
                argv, cwd=work_root, env=self._env("worker", attempt, extra, root=work_root),
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            self.say(f"воркер превысил лимит {timeout}с")
            return 124
        if proc.stdout.strip():
            self.say("  stdout: " + proc.stdout.strip().splitlines()[-1][:160])
        if proc.returncode != 0 and proc.stderr.strip():
            self.say("  stderr: " + proc.stderr.strip().splitlines()[-1][:160])
        return proc.returncode

    def _run_verifier(self, attempt: int, work_root: str, snapshot: str) -> dict[str, Any] | None:
        return self._invoke_verifier(self.cfg.verifier_cmd, "verifier", attempt, work_root, snapshot)

    def _run_adversarial(self, attempt: int, work_root: str, snapshot: str) -> dict[str, Any] | None:
        return self._invoke_verifier(self.cfg.adversarial_cmd, "adversarial", attempt, work_root, snapshot)

    def _invoke_verifier(self, cmd: str | None, role: str, attempt: int,
                         work_root: str, snapshot: str) -> dict[str, Any] | None:
        if not cmd:
            return None
        out_path = os.path.join(self.root, ".harness", "verdicts", f"{self.cfg.task_id}-{attempt}-{role}.json")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        payload = {
            "task_id": self.cfg.task_id,
            "attempt": attempt,
            "candidate_dir": work_root,
            "snapshot_id": snapshot,
            "brief": self._brief_data(),
        }
        extra = {
            "HARNESS_ROLE": role,
            "HARNESS_SNAPSHOT": snapshot,
            "HARNESS_CANDIDATE": work_root,
            "HARNESS_VERDICT_OUT": out_path,
            "HARNESS_EVIDENCE": os.path.join(self.root, "evidence"),
        }
        argv = shlex.split(cmd, posix=(os.name != "nt"))
        argv = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] == '"' else t for t in argv]
        self.say(f"проверяющий ({role}): {cmd}")
        try:
            proc = subprocess.run(
                argv, cwd=work_root, env=self._env(role, attempt, extra, root=work_root),
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self.cfg.timeout or 3600,
            )
        except subprocess.TimeoutExpired:
            self.say("проверяющий превысил лимит времени")
            return self._error_verdict(attempt, snapshot, role, "проверка не уложилась в лимит")

        data: dict[str, Any] | None = None
        if os.path.isfile(out_path):
            try:
                with open(out_path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except json.JSONDecodeError:
                data = None
        if data is None and proc.stdout.strip():
            try:
                data = json.loads(proc.stdout.strip())
            except json.JSONDecodeError:
                data = None
        if data is None:
            self.say("вердикта нет: ни файла, ни JSON в stdout")
            return None

        data.setdefault("verdict_id", f"v-{self.cfg.task_id}-{attempt}-{role}")
        data["task_id"] = self.cfg.task_id
        data["attempt"] = attempt
        data["snapshot_id"] = snapshot
        data.setdefault("reviewer_role", role)
        data.setdefault("reviewer_id", f"{role}-runner")
        return data

    def _error_verdict(self, attempt: int, snapshot: str, role: str, reason: str) -> dict[str, Any]:
        """Инфраструктурный сбой не выдаётся за дефект кода."""
        return {
            "verdict_id": f"v-{self.cfg.task_id}-{attempt}-{role}-error",
            "task_id": self.cfg.task_id,
            "attempt": attempt,
            "snapshot_id": snapshot,
            "verdict": "ERROR",
            "reviewer_id": "runner",
            "reviewer_role": role,
            "criteria": [],
            "findings": [],
            "note": reason,
        }

    def _fail_infrastructure(self, attempt: int, work_root: str, reason: str) -> None:
        """Фиксирует сбой как ERROR, а не как дефект кода.

        Перед вердиктом нужен снимок: даже неудачная попытка должна оставить
        состояние, к которому можно вернуться при разборе.
        """
        scope = self.cfg.scope_override if self.cfg.scope_override is not None else ""
        state = self._task()["state"]
        if state == "RUNNING":
            snapshot = snapshot_for_task(work_root, self._task(), scope)
            try:
                self.engine.apply(
                    self.cfg.task_id, "submit",
                    {"attempt": attempt, "snapshot_id": snapshot, "snapshot_scope": scope},
                )
            except TransitionError as exc:
                self.say(f"снимок сбойной попытки не принят: [{exc.rule}] {exc}")
                return
        else:
            snapshot = self._task().get("verifying_snapshot") or ""

        verdict = self._error_verdict(attempt, snapshot, "verifier", reason)
        try:
            self.engine.apply(self.cfg.task_id, "verdict", {"verdict": verdict})
        except TransitionError as exc:
            self.say(f"сбой зафиксировать не удалось: [{exc.rule}] {exc}")

    def _write_feedback(self, attempt: int, verdict: dict[str, Any]) -> None:
        task = self._task()
        brief = brief_mod.validate(self._brief_data(), max_attempts=int(task["max_attempts"]))
        remaining = int(task["max_attempts"]) - attempt
        packet = verdict_mod.feedback_packet(verdict, brief, remaining_budget=remaining)
        path = os.path.join(self.root, ".harness", "feedback", f"{self.cfg.task_id}-{attempt}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(packet, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        self.say(f"пакет доработки: {os.path.relpath(path, self.root)}")

    def _merge(self, lane_path: str, verdicts: list[str]) -> RunResult:
        from . import lanes

        try:
            merged = lanes.merge(self.root, self.store, self.cfg.task_id,
                                 ignore_paths=self._ignore_paths(self.root))
        except lanes.LaneError as exc:
            return self._finish("COMMITTED", verdicts, f"[{exc.rule}] {exc}", ok_override=False)
        self.say(f"лейн слит: {merged['merge_commit'][:10]}")
        return RunResult(True, self.cfg.task_id, "COMMITTED", int(self._task()["attempt"]),
                         commit_sha=merged["merge_commit"], verdicts=verdicts, log=self.log)

    def _ignore_paths(self, work_root: str) -> list[str]:
        """Файлы управления не считаются изменением продукта."""
        out: list[str] = []
        if self.cfg.brief_path:
            rel = os.path.relpath(os.path.abspath(self.cfg.brief_path), os.path.abspath(work_root))
            if not rel.startswith(".."):
                out.append(rel.replace("\\", "/"))
        return out

    def _finish(self, state: str, verdicts: list[str], reason: str,
                ok_override: bool | None = None) -> RunResult:
        ok = state in ("ACCEPTED", "COMMITTED") if ok_override is None else ok_override
        task = self._task()
        return RunResult(ok, self.cfg.task_id, state, int(task["attempt"]),
                         commit_sha=task.get("commit_sha"), verdicts=verdicts, log=self.log, reason=reason)


def render(result: RunResult) -> str:
    lines = [f"задача {result.task_id}: {result.state} (попыток: {result.attempts})"]
    for entry in result.log:
        lines.append(f"  {entry}")
    if result.commit_sha:
        lines.append(f"  коммит: {result.commit_sha[:12]}")
    if result.reason:
        lines.append(f"  причина остановки: {result.reason}")
    return "\n".join(lines)
