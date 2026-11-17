"""CLI управляющего процесса.

Одна точка входа для оркестратора, хуков и человека. Все решения о переходах
принимает fsm.Engine; CLI только передаёт события и печатает результат.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from . import brief as brief_mod
from . import doctor as doctor_mod
from . import evals as evals_mod
from . import findings as findings_mod
from . import rules as rules_mod
from . import selftest as selftest_mod
from . import session as session_mod
from . import verdict as verdict_mod
from .fsm import Engine, TransitionError, check_watchdog
from .hooks import HookContext, run as run_hooks
from .policy import Policy
from .snapshot import compute_for_scope, compute_snapshot, scope_of, snapshot_for_task
from .state import Store, StoreError

EXIT_OK = 0
EXIT_DENIED = 2
EXIT_ERROR = 3


def _root(args: argparse.Namespace) -> str:
    return os.path.abspath(args.root)


def _control_root(args: argparse.Namespace) -> str:
    """Корень управляющего слоя: политика и правила живут здесь, а не в лейне."""
    return os.path.abspath(getattr(args, "control_root", None) or args.root)


def _store(args: argparse.Namespace) -> Store:
    path = args.db or os.path.join(_control_root(args), ".harness", "state.db")
    return Store(path)


def _engine(args: argparse.Namespace, store: Store | None = None) -> Engine:
    root = _root(args)
    return Engine(store or _store(args), Policy.load(_control_root(args)), root)


def _lane_resources(root: str, task_id: str | None) -> dict[str, Any] | None:
    """Ресурсы лейна для задачи. Нет задачи, лейна или слота — None, а не выдумка."""
    if not task_id:
        return None
    from . import lanes as lanes_mod

    path = os.path.join(root, ".harness", "state.db")
    if not os.path.isfile(path):
        return None
    store = Store(path)
    try:
        slot = store.lane_slot(task_id)
    finally:
        store.close()
    if slot is None:
        return None
    return lanes_mod.resources_for(root, slot)


def _print(obj: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        print(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=2))


def _deny(out: TransitionError, as_json: bool) -> int:
    if as_json:
        print(json.dumps(out.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"ОТКАЗ [{out.rule}] {out}", file=sys.stderr)
        details = out.details.get("issues") or []
        for issue in details:
            print(f"  - {issue.get('code')} {issue.get('field')}: {issue.get('message')}", file=sys.stderr)
    return EXIT_DENIED


# --------------------------------------------------------------------- команды


def cmd_init(args: argparse.Namespace) -> int:
    root = _root(args)
    os.makedirs(os.path.join(root, ".harness"), exist_ok=True)
    store = _store(args)
    store.close()
    print(f"Состояние: {os.path.join(root, '.harness', 'state.db')}")
    return EXIT_OK


def cmd_change(args: argparse.Namespace) -> int:
    """Уровень изменения: состояние, review-записи, trace-матрица и Ready-гейт."""
    from . import changefsm

    root = _root(args)
    store = _store(args)
    action = args.change_cmd
    try:
        if action == "new":
            services = _parse_change_services(root, args.service, args.service_write)
            change = changefsm.create(
                store, root, args.change,
                owner=args.owner,
                risk_class=args.risk,
                title=args.title or "",
                base_commit=args.base_commit,
            )
            if services:
                body = dict(change.get("payload") or {})
                body["services"] = services
                change = store.update_change(args.change, payload=body)
            _print(change, args.json)
            return EXIT_OK
        elif action == "services":
            change = store.get_change(args.change)
            services = (change.get("payload") or {}).get("services") or []
            _print({"change_id": args.change, "services": services,
                    "merge_order": [str(s.get("name")) for s in services]}, args.json)
            return EXIT_OK
        elif action == "list":
            changes = store.list_changes()
            _print(changes, args.json)
            return EXIT_OK
        elif action == "show":
            change = store.get_change(args.change)
            report = changefsm.check_trace(store, root, args.change)
            change["reviews"] = store.change_reviews(args.change)
            change["trace"] = {
                "ok": report["ok"],
                "coverage": report["coverage"],
                "problems": report["problems"],
            }
            _print(change, args.json)
            return EXIT_OK
        elif action == "review":
            record = changefsm.review(
                store, root, args.change,
                artifact=args.artifact,
                reviewer_id=args.reviewer,
                reviewer_role=args.role,
                author_id=args.author,
                verdict=args.verdict,
                criteria=list(args.criterion or []),
            )
            _print(record, args.json)
            return EXIT_OK
        elif action == "trace":
            report = changefsm.check_trace(store, root, args.change)
            _print(report, args.json)
            return EXIT_OK if report["ok"] else EXIT_DENIED
        elif action == "ready":
            change = changefsm.transition(store, root, args.change, "ready",
                                          {"spec_ref": args.spec_ref, "root": root})
            _print({"change_id": change["change_id"], "state": change["status"],
                    "revision": change["revision"], "spec_ref": change["spec_ref"]}, args.json)
            return EXIT_OK
        elif action == "advance":
            payload: dict[str, Any] = {"reason": args.reason or ""}
            change = changefsm.transition(store, root, args.change, args.event, payload)
            _print({"change_id": change["change_id"], "state": change["status"],
                    "revision": change["revision"]}, args.json)
            return EXIT_OK
        elif action == "release":
            record = changefsm.write_release(
                store, root, args.change, environment=args.environment,
                services=_parse_services(args.service), by=args.by,
                by_role=args.by_role, flag=args.flag or "",
            )
            change = changefsm.transition(store, root, args.change, "release", {"root": root})
            _print({"release": record, "state": change["status"]}, args.json)
            return EXIT_OK
        elif action == "accept":
            if not args.criterion:
                raise changefsm.ChangeError("R-ACCEPT-1", "приёмка без критериев не принимается")
            record = changefsm.write_acceptance(
                store, root, args.change, accepted_by=args.accepted_by,
                criteria=list(args.criterion), environment=args.environment,
                services=_parse_services(args.service), by_role=args.by_role,
                residual_risks=list(args.residual or []),
            )
            change = changefsm.transition(store, root, args.change, "accept", {"root": root})
            _print({"acceptance": record, "state": change["status"]}, args.json)
            return EXIT_OK
        elif action == "close":
            changefsm.write_closing(
                root, args.change, by=args.by,
                specs_synced=args.specs_synced, cases_promoted=args.cases_promoted,
                contracts_promoted=args.contracts_promoted,
            )
            evidence = changefsm.write_evidence(store, root, args.change)
            change = changefsm.transition(store, root, args.change, "archive", {"root": root})
            _print({"state": change["status"], "evidence": evidence["path"]}, args.json)
            return EXIT_OK
        elif action == "evidence":
            evidence = changefsm.write_evidence(store, root, args.change)
            _print(evidence, args.json)
            return EXIT_OK
        raise SystemExit(f"неизвестное действие change: {action}")
    except (changefsm.ChangeError, StoreError, ValueError) as exc:
        rule = getattr(exc, "rule", "R-CHG-1")
        message = getattr(exc, "reason", str(exc))
        if args.json:
            _print({"ok": False, "rule": rule, "reason": message}, True)
        else:
            print(f"ОТКАЗ [{rule}] {message}", file=sys.stderr)
        return EXIT_DENIED


def cmd_gap(args: argparse.Namespace) -> int:
    """GAP-вопрос: публикует вопрос для guard-плагина (ask), снимает после ответа."""
    from . import gap as gap_mod

    root = _root(args)
    try:
        if args.gap_cmd == "ask":
            record = gap_mod.publish(root, task_id=args.task, question=args.question,
                                     gap_id=args.id or "GAP-1")
        elif args.gap_cmd == "clear":
            gap_mod.clear(root)
            record = {"cleared": True}
        else:
            record = gap_mod.load(root) or {"open": False}
    except ValueError as exc:
        print(f"ОТКАЗ [R-GAP-2] {exc}", file=sys.stderr)
        return EXIT_DENIED
    _print(record, args.json)
    return EXIT_OK


def _parse_services(raw: list[str] | None) -> list[dict[str, str]]:
    services: list[dict[str, str]] = []
    for item in raw or []:
        if "=" not in item:
            raise ValueError(f"сервис должен быть name=SHA: {item!r}")
        name, sha = item.split("=", 1)
        services.append({"name": name.strip(), "sha": sha.strip()})
    return services


def _parse_change_services(root: str, raw: list[str] | None,
                           write_names: list[str] | None = None) -> list[dict[str, Any]]:
    """name=PATH[@SHA]: каталог сервиса и его принятая версия. --service-write открывает запись."""
    from . import verification

    writable = {str(name) for name in (write_names or [])}
    services: list[dict[str, Any]] = []
    for item in raw or []:
        if "=" not in item:
            raise ValueError(f"сервис должен быть name=PATH[@SHA]: {item!r}")
        name, spec = item.split("=", 1)
        name = name.strip()
        path_spec, base = (spec.rsplit("@", 1) if "@" in spec else (spec, ""))
        path = os.path.abspath(path_spec if os.path.isabs(path_spec) else os.path.join(root, path_spec))
        if not os.path.isdir(path):
            raise ValueError(f"каталог сервиса не найден: {path}")
        root_abs = os.path.abspath(root)
        path_abs = os.path.abspath(path)
        if os.path.normcase(path_abs) == os.path.normcase(root_abs):
            raise ValueError("сервис не может совпадать с главным корнем: write=true обошёл бы R-PROT-1")
        if os.path.normcase(root_abs).startswith(os.path.normcase(path_abs) + os.sep):
            raise ValueError("сервис не может быть предком главного корня: write=true переименовал бы весь проект")
        if not base:
            base = verification._git(path, "rev-parse", "HEAD") or ""
        if base and len(base) != 40:
            raise ValueError(f"сервис {name}: base_commit должен быть полным SHA")
        rel = os.path.relpath(path, os.path.abspath(root)).replace("\\", "/")
        services.append({"name": name, "path": rel if not rel.startswith("..") else path,
                         "base_commit": base, "write": name in writable})
    for name in writable:
        if name not in {s["name"] for s in services}:
            raise ValueError(f"--service-write {name!r}: сервис не объявлен через --service")
    return services


def cmd_verification(args: argparse.Namespace) -> int:
    """Baseline/candidate: два прогона с версиями и diff."""
    from . import verification

    root = _root(args)
    store = _store(args)
    try:
        if args.verification_cmd == "run":
            svc = _parse_services(args.service)
            if len(svc) > 1:
                raise ValueError("один прогон — один сервис; для нескольких запустите run на каждый")
            report = verification.record(
                store, root, args.change, args.mode, args.command,
                ref=args.ref, timeout=args.timeout, environment=args.environment,
                runner=args.runner, services=svc, cwd=args.cwd,
                service=(svc[0]["name"] if svc else "repo"),
            )
        else:
            report = verification.load(root, args.change)
            if report is None:
                print(f"ОТКАЗ [R-QA-1] нет verification.json для {args.change}", file=sys.stderr)
                return EXIT_DENIED
    except (ValueError, StoreError, RuntimeError) as exc:
        print(f"ОТКАЗ [R-QA-1] {exc}", file=sys.stderr)
        return EXIT_DENIED
    _print(report, args.json)
    return EXIT_OK if report.get("ok") else EXIT_DENIED


def cmd_task_new(args: argparse.Namespace) -> int:
    eng = _engine(args)
    task = eng.store.create_task(
        args.id,
        spec_version=args.spec,
        base_commit=args.base_commit,
        lane=args.lane,
        policy_version=eng.policy.version,
        max_attempts=int(eng.policy.limit("max_attempts", 3)),
        change_id=args.change,
    )
    _print({"task_id": task["task_id"], "state": task["state"]}, args.json)
    return EXIT_OK


def cmd_task_list(args: argparse.Namespace) -> int:
    eng = _engine(args)
    tasks = eng.store.list_tasks()
    if args.json:
        _print(tasks, True)
        return EXIT_OK
    if not tasks:
        print("задач нет")
        return EXIT_OK
    print(f"{'task_id':<24}{'state':<16}{'attempt':<9}{'snapshot'}")
    for t in tasks:
        print(f"{t['task_id']:<24}{t['state']:<16}{str(t['attempt']) + '/' + str(t['max_attempts']):<9}"
              f"{(t['accepted_snapshot'] or t['verifying_snapshot'] or '—')[:26]}")
    return EXIT_OK


def cmd_task_status(args: argparse.Namespace) -> int:
    eng = _engine(args)
    task = eng.store.get_task(args.task)
    verdict = eng.store.latest_verdict(args.task)
    payload = {
        "task": task,
        "attempts": eng.store.attempts_for(args.task),
        "latest_verdict": {"verdict_id": verdict["verdict_id"], "verdict": verdict["verdict"],
                           "snapshot_id": verdict["snapshot_id"], "stale": bool(verdict["stale"])}
        if verdict else None,
        "open_findings": eng.store.findings_for(args.task, status="open"),
        "events": [{"type": e["type"], "at": e["created_at"]} for e in eng.store.events_for(args.task)],
    }
    _print(payload, args.json)
    return EXIT_OK


def cmd_session(args: argparse.Namespace) -> int:
    root = _root(args)
    store = _store(args)
    if args.session_cmd == "attach":
        # scope — источник истины: env не должен перебивать роль рабочей сессии.
        role = session_mod.active_role(root) or os.environ.get("HARNESS_ROLE") or "human"
        if role not in ("human", "system", "orchestrator"):
            print(f"session attach запрещён роли {role!r}: привязку делает оператор",
                  file=sys.stderr)
            return EXIT_DENIED
        out = session_mod.attach(root, args.task, store, auto_submit=args.auto_submit)
    elif args.session_cmd == "detach":
        out = session_mod.detach(root)
    else:
        out = session_mod.status(root)
    _print(out, args.json)
    return EXIT_OK


def cmd_brief_check(args: argparse.Namespace) -> int:
    b = brief_mod.load(args.file)
    if args.json:
        _print({"valid": b.valid, "hash": b.hash(), "issues": [i.to_dict() for i in b.issues]}, True)
    else:
        print(f"бриф {b.task_id or '<без id>'} · hash {b.hash()}")
        print("ошибок: %d, предупреждений: %d" % (len(b.errors), len(b.warnings)))
        if b.issues:
            print(brief_mod.render_issues(b.issues))
        print("ИТОГ: " + ("валиден" if b.valid else "НЕВАЛИДЕН"))
    return EXIT_OK if b.valid else EXIT_DENIED


def cmd_brief_from_change(args: argparse.Namespace) -> int:
    """Собирает бриф из OpenSpec-change: сценарии -> критерии, задачи -> границы записи."""
    from . import specbrief

    root = _root(args)
    services: list[dict[str, Any]] = []
    store = _store(args)
    try:
        record = store.find_change(args.change)
        if record:
            services = list((record.get("payload") or {}).get("services") or [])
    finally:
        store.close()
    try:
        brief = specbrief.build(
            root,
            args.change,
            task_id=args.task,
            base_commit=args.base_commit,
            spec_ref=args.spec_ref,
            actor_role=args.actor_role,
            check_commands=args.check,
            attempt_limit=args.attempt_limit,
            timeout_seconds=args.timeout,
            services=services,
        )
    except specbrief.SpecBriefError as exc:
        print(f"ОТКАЗ [SPEC-BRIEF] {exc}", file=sys.stderr)
        return EXIT_DENIED

    if args.out:
        specbrief.write(brief, args.out)
    if args.json:
        _print(brief, True)
    else:
        print(specbrief.render(brief))
        if args.out:
            print(f"записано: {args.out}")

    checked = brief_mod.validate(brief)
    if not checked.valid:
        print(brief_mod.render_issues(checked.issues), file=sys.stderr)
        return EXIT_DENIED
    return EXIT_OK


def cmd_brief_attach(args: argparse.Namespace) -> int:
    eng = _engine(args)
    with open(args.file, encoding="utf-8") as fh:
        raw = json.load(fh)
    return _apply(eng, args, "attach_brief", {"brief": raw}, event_id=args.event_id)


def _runtime_role(root: str) -> str:
    """Роль процесса: guard-scope приоритетнее env (DSH не выставляет HARNESS_ROLE)."""
    from . import session as session_mod

    return session_mod.active_role(root) or os.environ.get("HARNESS_ROLE") or "human"


def _require_capability(root: str, capability: str, json_out: bool,
                        policy_root: str | None = None) -> bool:
    """R-CAP-1: may_dispatch/may_commit/may_verify проверяются до выполнения."""
    from .policy import Policy, PolicyError

    role = _runtime_role(root)
    try:
        from .hooks import HookContext, run as run_hooks

        result = run_hooks("pretooluse", HookContext(
            event="pretooluse", tool="harness", role=role,
            policy=Policy.load(policy_root or root), root=root,
            extra={"capability": capability},
        ))
    except (OSError, ValueError, PolicyError) as exc:
        print(f"ОТКАЗ [R-CAP-1] не удалось прочитать политику: {exc}", file=sys.stderr)
        return False
    if result["decision"] == "deny":
        if json_out:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"ОТКАЗ [{result['rule']}] {result['reason']}", file=sys.stderr)
        return False
    return True


def cmd_dispatch(args: argparse.Namespace) -> int:
    if not _require_capability(_root(args), "dispatch", args.json, _control_root(args)):
        return EXIT_DENIED
    eng = _engine(args)
    return _apply(
        eng,
        args,
        "dispatch",
        {"worker_id": args.worker, "timeout_seconds": args.timeout,
         "primary_agent": args.primary_agent},
        event_id=args.event_id,
    )


def cmd_submit(args: argparse.Namespace) -> int:
    eng = _engine(args)
    scope = args.scope or ""
    snapshot = args.snapshot_id
    if not snapshot:
        if args.snapshot:
            scope = scope_of(_root(args), args.snapshot)
        task = eng.store.get_task(args.task)
        snapshot = snapshot_for_task(_root(args), task, scope)
    return _apply(
        eng,
        args,
        "submit",
        {"attempt": args.attempt, "snapshot_id": snapshot, "snapshot_scope": scope},
        event_id=args.event_id,
    )


def cmd_verdict(args: argparse.Namespace) -> int:
    if not _require_capability(_root(args), "verify", args.json, _control_root(args)):
        return EXIT_DENIED
    eng = _engine(args)
    with open(args.file, encoding="utf-8") as fh:
        raw = json.load(fh)
    return _apply(eng, args, "verdict", {"verdict": raw}, event_id=args.event_id)


def cmd_commit(args: argparse.Namespace) -> int:
    if not _require_capability(_root(args), "commit", args.json, _control_root(args)):
        return EXIT_DENIED
    eng = _engine(args)
    payload: dict[str, Any] = {}
    if args.snapshot or not args.skip_snapshot_check:
        scope = args.scope or (scope_of(_root(args), args.snapshot) if args.snapshot else "")
        task = eng.store.get_task(args.task)
        payload["snapshot_id"] = snapshot_for_task(_root(args), task, scope)
    return _apply(eng, args, "commit", payload, event_id=args.event_id)


def cmd_event(args: argparse.Namespace) -> int:
    eng = _engine(args)
    return _apply(eng, args, args.event, {}, event_id=args.event_id)


def _apply(eng: Engine, args: argparse.Namespace, event: str, payload: dict[str, Any],
           event_id: str | None = None) -> int:
    try:
        out = eng.apply(args.task, event, payload, event_id=event_id)
    except TransitionError as exc:
        return _deny(exc, args.json)
    except StoreError as exc:
        print(f"ОШИБКА СОСТОЯНИЯ: {exc}", file=sys.stderr)
        return EXIT_ERROR
    _print(out.to_dict(), args.json)
    return EXIT_OK


def cmd_watchdog(args: argparse.Namespace) -> int:
    from .watchdog import Watchdog

    eng = _engine(args)
    dog = Watchdog(eng.store, eng)

    if args.action == "check":
        if not args.task:
            print("нужен --task", file=sys.stderr)
            return EXIT_ERROR
        out = check_watchdog(eng, args.task)
        if out is None:
            _print({"ok": True, "state": eng.store.get_task(args.task)["state"], "message": "не просрочен"},
                   args.json)
            return EXIT_OK
        _print(out.to_dict(), args.json)
        return EXIT_OK

    if args.action == "sweep":
        revoked = dog.sweep()
        _print({"revoked": [r.to_dict() for r in revoked], "count": len(revoked)}, args.json)
        return EXIT_OK

    if args.action == "serve":
        def emit(payload: dict[str, Any]) -> None:
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, flush=True))
            else:
                if payload["revoked"]:
                    for r in payload["revoked"]:
                        print(f"отозвано: {r['task_id']} (попытка {r['attempt']}, просрочка {r['overdue_s']}с)")
                else:
                    print(f"цикл {payload['cycle']}: просрочек нет", flush=True)

        cycles = dog.serve(interval=args.interval, cycles=args.cycles, emit=emit)
        _print({"cycles": cycles}, args.json) if args.json else None
        return EXIT_OK

    print(f"неизвестное действие: {args.action}", file=sys.stderr)
    return EXIT_ERROR


def cmd_sandbox(args: argparse.Namespace) -> int:
    from .checks import detect_sandbox

    info = detect_sandbox()
    _print(info, args.json)
    return EXIT_OK if args.action == "probe" else EXIT_ERROR


def cmd_run(args: argparse.Namespace) -> int:
    """Полный цикл одной командой: бриф → воркер → проверка → коммит/эскалация."""
    from .run import RunConfig, RunError, Runner, render

    root = _root(args)
    if not _require_capability(root, "dispatch", args.json, _control_root(args)):
        return EXIT_DENIED
    if getattr(args, "commit", False) and not _require_capability(
            root, "commit", args.json, _control_root(args)):
        return EXIT_DENIED
    if args.verifier and _runtime_role(root) not in ("human", "orchestrator", "system"):
        print("ОТКАЗ [R-CAP-1] роль не может запускать проверяющего в run", file=sys.stderr)
        return EXIT_DENIED
    store = _store(args)
    try:
        engine = Engine(store, Policy.load(_control_root(args)), root)
        config = RunConfig(
            root=root,
            task_id=args.task,
            brief_path=args.brief,
            worker_cmd=args.worker,
            verifier_cmd=args.verifier,
            adversarial_cmd=args.adversarial,
            commit=args.commit,
            lane=args.lane,
            merge=args.merge,
            allow_no_spec=getattr(args, "allow_no_spec", None),
            scope_override=args.scope,
            timeout=args.timeout,
            message=args.message,
        )
        runner = Runner(config, store, engine.policy, engine)
        try:
            result = runner.run()
        except RunError as exc:
            print(f"ОШИБКА ЗАПУСКА: {exc}", file=sys.stderr)
            for line in runner.log:
                print(f"  {line}", file=sys.stderr)
            return EXIT_ERROR
    finally:
        store.close()

    _print(result.to_dict(), True) if args.json else print(render(result))
    return EXIT_OK if result.ok else EXIT_DENIED


def cmd_cost(args: argparse.Namespace) -> int:
    """Учёт стоимости задачи: запись факта и отчёт."""
    from . import cost as cost_mod

    store = _store(args)
    try:
        if args.action == "record":
            if not args.task or args.amount is None:
                print("нужны --task и --amount", file=sys.stderr)
                return EXIT_ERROR
            entry = cost_mod.record(
                store, args.task, args.amount, unit=args.unit, kind=args.kind,
                attempt=args.attempt, note=args.note or "",
            )
            _print(entry, args.json)
            return EXIT_OK
        if args.action == "report":
            _print(cost_mod.report(store, args.unit), args.json)
            return EXIT_OK
    finally:
        store.close()
    return EXIT_ERROR


def cmd_doctor(args: argparse.Namespace) -> int:
    """Проверка живой конфигурации: режим песочницы, плагин, указатель, висящие права."""
    from . import doctor

    report = doctor.run(_root(args))
    _print(report, True) if args.json else print(doctor.render(report))
    return EXIT_OK if report["ok"] else EXIT_DENIED


def cmd_plugin(args: argparse.Namespace) -> int:
    """Подготовка установленного плагина: указатель на корень и проверка пакета."""
    from . import pluginctl

    root = _root(args)
    if args.action == "prepare":
        report = pluginctl.prepare(root)
    elif args.action == "status":
        report = pluginctl.status(root)
    else:
        print(f"неизвестное действие: {args.action}", file=sys.stderr)
        return EXIT_ERROR

    _print(report, True) if args.json else print(pluginctl.render(report))
    return EXIT_OK if report["ok"] else EXIT_DENIED


def cmd_rule(args: argparse.Namespace) -> int:
    """Дисциплина изменения правил: причина + прогон, затем запись."""
    from . import rulechange

    root = _root(args)
    store = _store(args)
    try:
        if args.action == "verify":
            report = rulechange.verify(store, root)
            _print(report, args.json)
            return EXIT_OK if report["ok"] else EXIT_DENIED
        if args.action == "change":
            if not args.id or not args.reason:
                print("нужны --id и --reason", file=sys.stderr)
                return EXIT_ERROR
            try:
                out = rulechange.change(store, root, args.id, args.reason, author=args.author)
            except ValueError as exc:
                _print({"ok": False, "rule": "R-HOOK-1", "reason": str(exc)}, args.json) if args.json \
                    else print(f"ОТКАЗ [R-HOOK-1] {exc}", file=sys.stderr)
                return EXIT_DENIED
            _print(out, args.json)
            return EXIT_OK
    finally:
        store.close()
    return EXIT_ERROR


def cmd_lane(args: argparse.Namespace) -> int:
    """Параллельные лейны: worktree на задачу, слияние под мьютексом."""
    from . import lanes as lanes_mod

    root = _root(args)
    store = _store(args)
    try:
        if args.action == "list":
            _print(lanes_mod.list_lanes(root), args.json)
            return EXIT_OK
        if args.action == "create":
            out = lanes_mod.create(root, store, args.task, base=args.base)
        elif args.action == "merge":
            out = lanes_mod.merge(root, store, args.task, message=args.message)
        elif args.action in ("push", "pr"):
            if not _require_capability(root, "commit", args.json, _control_root(args)):
                return EXIT_DENIED
            if args.action == "push":
                out = lanes_mod.push(root, store, args.task, remote=args.remote)
            else:
                out = lanes_mod.pull_request(root, args.task, base=args.target,
                                             title=args.title or "", body=args.body or "")
        elif args.action == "remove":
            out = lanes_mod.remove(root, args.task, force=not args.keep_branch, store=store)
        elif args.action == "show":
            out = lanes_mod.show(root, store, args.task)
        elif args.action == "release":
            out = lanes_mod.release(store, args.task)
        else:
            print(f"неизвестное действие: {args.action}", file=sys.stderr)
            return EXIT_ERROR
    except lanes_mod.LaneError as exc:
        _print(exc.to_dict(), args.json) if args.json else _deny_text(exc)
        return EXIT_DENIED
    finally:
        store.close()
    _print(out, args.json)
    return EXIT_OK


def cmd_e2e(args: argparse.Namespace) -> int:
    """Интеграционный прогон по копиям сервисов."""
    from . import e2e as e2e_mod

    root = _root(args)
    if not _require_capability(root, "commit", args.json, _control_root(args)):
        return EXIT_DENIED
    try:
        services = e2e_mod.parse_services(args.service or [])
        out = e2e_mod.run(root, args.change, args.command, services, timeout=args.timeout)
    except e2e_mod.E2EError as exc:
        _print(exc.to_dict(), args.json) if args.json else _deny_text(exc)
        return EXIT_DENIED
    _print(out, args.json)
    return EXIT_OK if out.get("ok") else EXIT_DENIED


def cmd_evidence(args: argparse.Namespace) -> int:
    """Экспорт и проверка evidence в доверенном каталоге."""
    from . import evidence_export as exp

    root = _root(args)
    if not args.dest:
        print("нужен --dest", file=sys.stderr)
        return EXIT_ERROR
    try:
        if args.action == "export":
            if not _require_capability(root, "commit", args.json, _control_root(args)):
                return EXIT_DENIED
            out = exp.export(root, args.dest, change=args.change)
        else:
            out = exp.verify(args.dest)
    except exp.ExportError as exc:
        _print(exc.to_dict(), args.json) if args.json else _deny_text(exc)
        return EXIT_DENIED
    _print(out, args.json)
    return EXIT_OK if out.get("ok") else EXIT_DENIED


def cmd_forge(args: argparse.Namespace) -> int:
    """Статус PR/checks через gh; --wait ждёт завершения."""
    from . import forge as forge_mod
    from . import lanes as lanes_mod

    root = _root(args)
    branch = args.branch or (lanes_mod.branch_name(args.task) if args.task else "")
    if not branch:
        print("нужен --task или --branch", file=sys.stderr)
        return EXIT_ERROR
    try:
        out = forge_mod.wait_status(root, branch, timeout=args.timeout) if args.wait \
            else forge_mod.pr_status(root, branch)
    except forge_mod.ForgeError as exc:
        _print(exc.to_dict(), args.json) if args.json else _deny_text(exc)
        return EXIT_DENIED
    _print(out, args.json)
    return EXIT_OK if out.get("ok") else EXIT_DENIED


def cmd_hook(args: argparse.Namespace) -> int:
    root = _root(args)
    if args.stdin:
        payload = json.load(sys.stdin)
    else:
        payload = {
            "event": args.event,
            "tool": args.tool,
            "role": args.role,
            "claimed_role": args.claimed_role,
            "path": args.path,
            "command": args.command,
            "target": args.target,
            "allowed_files": args.allowed_file or [],
        }
    ctx = HookContext(
        event=str(payload.get("event", args.event)),
        tool=str(payload.get("tool", "")),
        role=str(payload.get("role", "")),
        claimed_role=payload.get("claimed_role"),
        task_id=payload.get("task_id"),
        path=payload.get("path"),
        command=payload.get("command"),
        target=payload.get("target"),
        allowed_files=list(payload.get("allowed_files") or []),
        policy=Policy.load(_control_root(args)),
        root=root,
    )
    result = run_hooks(ctx.event, ctx)
    _print(result, True)
    return EXIT_OK if result["decision"] != "deny" else EXIT_DENIED


def cmd_findings(args: argparse.Namespace) -> int:
    eng = _engine(args)
    if args.action == "add":
        rec = eng.store.add_finding(
            finding_id=args.finding_id,
            task_id=args.task,
            severity=args.severity,
            statement=args.statement,
            evidence=args.evidence or "",
            owner=args.owner,
            track=args.track,
            attempt=args.attempt,
        )
        routed = findings_mod.route(rec, worker_ids=[args.owner] if args.owner else [])
        _print(routed, args.json)
        return EXIT_OK
    if args.action == "close":
        try:
            rec = eng.store.close_finding(args.finding_id, args.reason or "")
        except StoreError as exc:
            print(f"ОТКАЗ [R-FIND-1] {exc}", file=sys.stderr)
            return EXIT_DENIED
        _print(rec, args.json)
        return EXIT_OK
    if args.action == "list":
        _print(eng.store.findings_for(args.task), args.json)
        return EXIT_OK
    if args.action == "check":
        report = findings_mod.completeness(eng.store.findings_for(args.task))
        _print(report, args.json)
        return EXIT_OK if report["ok"] else EXIT_DENIED
    return EXIT_ERROR


def cmd_coverage(args: argparse.Namespace) -> int:
    root = _root(args)
    registry = rules_mod.load_registry(root, args.rules)
    installed = doctor_mod.installed_packages()
    statuses = rules_mod.evaluate(registry, installed=installed)
    tested = {c.rule for c in selftest_mod.CHECKS}
    sums = rules_mod.summary(statuses, tested)
    if args.markdown:
        text = rules_mod.render_markdown(statuses, sums)
        parent = os.path.dirname(os.path.abspath(args.markdown))
        os.makedirs(parent, exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"записано: {args.markdown}")
        return EXIT_OK
    if args.json:
        _print({"summary": sums, "rules": [s.to_dict() for s in statuses]}, True)
        return EXIT_OK
    print(f"правил: {sums['total']} · покрыто нами: {sums['covered']} · "
          f"делегировано: {sums['delegated']} · пробелов: {sums['gap']} · "
          f"сломано: {sums['broken']} · покрытие: {sums['coverage_ratio']:.0%} · "
          f"с исполняемыми тестами: {sums['tested']}/{sums['total']}")
    for s in statuses:
        if s.status in ("gap", "broken"):
            print(f"  ПРОБЕЛ {s.id} ({s.enforcement}): {s.reason}")
        elif s.status == "delegated":
            print(f"  делегировано {s.id} → {s.delegated_to}")
    if sums["untested"]:
        print("  без исполняемых проверок: " + ", ".join(sums["untested"]))
    return EXIT_OK if sums["broken"] == 0 else EXIT_DENIED


def cmd_otel(args: argparse.Namespace) -> int:
    from . import metrics, otel

    root = _root(args)
    store = _store(args)
    events: list[dict[str, Any]] = []
    try:
        data = metrics.collect(store, root)
        if args.traces_task:
            events = store.events_for(args.traces_task)
    finally:
        store.close()
    payload = otel.build(data, service_name=args.service_name,
                         service_version=args.service_version or "")
    traces = None
    if args.traces_task:
        traces = otel.build_traces(events, args.traces_task,
                                   service_name=args.service_name,
                                   service_version=args.service_version or "")
        if not args.traces_out and not args.traces_endpoint:
            print("ОТКАЗ [OTEL] для spans нужен --traces-out или --traces-endpoint",
                  file=sys.stderr)
            return EXIT_ERROR
    if args.out:
        print(f"OTLP: записано {otel.write(payload, args.out)}")
    if traces is not None and args.traces_out:
        print(f"OTLP traces: записано {otel.write(traces, args.traces_out)}")
    sent = None
    if args.endpoint:
        try:
            sent = otel.send(payload, args.endpoint, timeout=args.timeout)
        except OSError as exc:
            print(f"ОТКАЗ [OTEL] {exc}", file=sys.stderr)
            return EXIT_DENIED
        print(f"OTLP: отправлено {sent['status']} → {args.endpoint}")
    if traces is not None and args.traces_endpoint:
        try:
            sent = otel.send(traces, args.traces_endpoint, timeout=args.timeout)
        except OSError as exc:
            print(f"ОТКАЗ [OTEL traces] {exc}", file=sys.stderr)
            return EXIT_DENIED
        print(f"OTLP traces: отправлено {sent['status']} → {args.traces_endpoint}")
    if args.json:
        _print({"metrics": payload, "traces": traces} if traces is not None else payload, True)
    elif not args.out and not args.endpoint and traces is None:
        metrics_count = len(payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"])
        print(f"OTLP: {metrics_count} метрик; укажи --out PATH или --endpoint URL")
    return EXIT_OK


def cmd_metrics(args: argparse.Namespace) -> int:
    from . import metrics

    root = _root(args)
    store = _store(args)
    try:
        data = metrics.collect(store, root)
    finally:
        store.close()
    if args.markdown:
        path = os.path.abspath(args.markdown)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(metrics.render_md(data))
        print(f"записано: {args.markdown}")
        return EXIT_OK
    if args.json:
        _print(data, True)
    else:
        print(metrics.render_md(data), end="")
    return EXIT_OK


def cmd_board(args: argparse.Namespace) -> int:
    from . import metrics

    root = _root(args)
    store = _store(args)
    if args.serve:
        server = metrics.make_server(store, root, args.host, args.port)
        host, port = server.server_address[:2]
        print(f"доска: http://{host}:{port}/ · Ctrl+C чтобы остановить", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            store.close()
        return EXIT_OK
    try:
        data = metrics.board_data(store, root)
    finally:
        store.close()
    if not args.out:
        print("нужен --out PATH или --serve", file=sys.stderr)
        return EXIT_DENIED
    path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(metrics.render_html(data))
    if args.json:
        _print(data, True)
    else:
        print(f"доска: {args.out} · change {data['summary']['changes']} · "
              f"задач {data['summary']['tasks']} · открытых находок {data['summary']['open_findings']}")
    return EXIT_OK


def cmd_ci(args: argparse.Namespace) -> int:
    from . import ci

    root = _root(args)
    report = ci.collect(root, with_tests=args.run_tests, timeout=args.timeout)
    path = ci.write_report(root, report, args.report)
    print(f"CI: {'OK' if report['ok'] else 'FAIL'} · selftest {report['selftest']['passed']}/"
          f"{report['selftest']['total']} · правил {report['coverage']['total']} · "
          f"пробелов {report['coverage']['gap']} · отчёт {os.path.relpath(path, root)}")
    if args.run_tests:
        print(f"  unittest: {'OK' if report['tests']['ok'] else 'FAIL'} "
              f"{report['tests']['duration_s']} c")
    return EXIT_OK if report["ok"] else EXIT_DENIED


def cmd_selftest(args: argparse.Namespace) -> int:
    report = selftest_mod.run_all(_root(args))
    if args.json:
        _print(report, True)
    else:
        print(f"проверок: {report['total']} · прошло: {report['passed']} · упало: {report['failed']}")
        for r in report["results"]:
            mark = "ok  " if r["ok"] else "FAIL"
            print(f"  [{mark}] {r['rule']:<14} {r['name']}" + (f"\n         {r['error']}" if r["error"] else ""))
    return EXIT_OK if report["ok"] else EXIT_DENIED


def cmd_pii(args: argparse.Namespace) -> int:
    from . import pii

    root = _root(args)
    if (args.file or args.out) and not _require_capability(
            root, "commit", args.json, _control_root(args)):
        return EXIT_DENIED
    for path in (args.file, args.out):
        if path:
            real = os.path.realpath(path)
            root_real = os.path.realpath(root)
            try:
                inside = os.path.commonpath([real, root_real]) == root_real
            except ValueError:
                inside = False
            if not inside:
                print(f"ОТКАЗ [PII] путь вне корня: {path}", file=sys.stderr)
                return EXIT_DENIED
    from .policy import Policy
    policy = Policy.load(_control_root(args))
    for path, tool in ((args.file, "read_file"), (args.out, "write_file")):
        if not path:
            continue
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(root)).replace("\\", "/")
        if tool == "write_file" and policy.is_protected(rel):
            print(f"ОТКАЗ [R-PROT-1] {rel} — защищённый путь", file=sys.stderr)
            return EXIT_DENIED
        decision = run_hooks("pretooluse", HookContext(
            event="pretooluse", tool=tool, role=_runtime_role(root), path=rel,
            policy=policy, root=root,
        ))
        if decision["decision"] == "deny":
            print(f"ОТКАЗ [{decision['rule']}] {decision['reason']}", file=sys.stderr)
            return EXIT_DENIED
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            text = fh.read()
    elif args.text is not None:
        text = args.text
    else:
        print("ОТКАЗ [PII] нужен --text или --file", file=sys.stderr)
        return EXIT_DENIED
    if args.action == "redact" and args.out and args.file:
        if os.path.abspath(args.out) == os.path.abspath(args.file):
            print("ОТКАЗ [PII] нельзя перезаписывать исходный файл", file=sys.stderr)
            return EXIT_DENIED
    if args.action == "scan":
        _print(pii.report(text), args.json)
    else:
        out = pii.redact(text)
        if args.out:
            path = os.path.abspath(args.out)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(out)
            print(f"записано: {args.out}")
        else:
            print(out, end="" if out.endswith("\n") else "\n")
    return EXIT_OK


def cmd_slo(args: argparse.Namespace) -> int:
    from . import metrics, slo

    root = _root(args)
    store = _store(args)
    try:
        data = metrics.collect(store, root)
    finally:
        store.close()
    report = slo.evaluate(data)
    if args.json:
        _print(report, True)
    else:
        print(slo.render_text(report))
    if args.strict and not report["ok"]:
        print("ОТКАЗ [SLO] порог нарушен: " + ", ".join(report["breached"]), file=sys.stderr)
        return EXIT_DENIED
    return EXIT_OK


def cmd_evals(args: argparse.Namespace) -> int:
    report = evals_mod.run(args.cases, args.verifier, root=_root(args))
    if args.json:
        _print(report, True)
    else:
        print(evals_mod.render_text(report))
    return EXIT_OK if report["metrics"]["total"] else EXIT_ERROR


def cmd_demo(args: argparse.Namespace) -> int:
    from .demo import run_demo

    return run_demo(_root(args), verbose=not args.json, as_json=args.json)


def cmd_tool(args: argparse.Namespace) -> int:
    """Шлюз инструментов: единственный путь записи для агента."""
    from .gateway import Gateway

    root = _root(args)
    if args.stdin:
        request = json.load(sys.stdin)
    else:
        request = {
            "tool": args.tool,
            "role": args.role,
            "task_id": args.task,
            "args": {"path": args.path, "content": _read_content(args), "command": args.command},
        }
    store = _store(args)
    try:
        gateway = Gateway(root, Policy.load(_control_root(args)), store)
        result = gateway.handle(request)
    finally:
        store.close()
    _print(result, True)
    return EXIT_OK if result.get("ok") else EXIT_DENIED


def _read_content(args: argparse.Namespace) -> str | None:
    if getattr(args, "content", None) is not None:
        return args.content
    if getattr(args, "content_file", None):
        with open(args.content_file, encoding="utf-8") as fh:
            return fh.read()
    return None


def cmd_finalize(args: argparse.Namespace) -> int:
    """Доверенный финализатор: write-set, снимок, коммит, журнал."""
    from .finalize import FinalizeError, finalize

    root = _root(args)
    if not _require_capability(root, "commit", args.json, _control_root(args)):
        return EXIT_DENIED
    store = _store(args)
    try:
        engine = Engine(store, Policy.load(_control_root(args)), root)
        try:
            out = finalize(store, engine, root, args.task, message=args.message, author=args.author,
                           allow_no_spec=getattr(args, 'allow_no_spec', None))
        except FinalizeError as exc:
            _print(exc.to_dict(), True) if args.json else _deny_text(exc)
            return EXIT_DENIED
    finally:
        store.close()
    _print(out, args.json)
    return EXIT_OK


def _deny_text(exc: Any) -> None:
    print(f"ОТКАЗ [{exc.rule}] {exc}", file=sys.stderr)
    for key, value in (getattr(exc, "details", None) or {}).items():
        print(f"  {key}: {value}", file=sys.stderr)


def cmd_checks_run(args: argparse.Namespace) -> int:
    """Изолированный прогон команд проверки из брифа."""
    from . import checks as checks_mod

    root = _root(args)
    brief_data: dict[str, Any]
    if args.brief:
        with open(args.brief, encoding="utf-8") as fh:
            brief_data = json.load(fh)
    elif args.task:
        store = _store(args)
        try:
            task = store.get_task(args.task)
        finally:
            store.close()
        if not task.get("brief_json"):
            print("у задачи нет брифа", file=sys.stderr)
            return EXIT_ERROR
        brief_data = json.loads(task["brief_json"])
    else:
        print("нужен --brief или --task", file=sys.stderr)
        return EXIT_ERROR

    lane_resources = _lane_resources(root, args.task or brief_data.get("task_id"))
    try:
        report = checks_mod.run(
            root,
            brief_data,
            task_id=args.task or brief_data.get("task_id", "checks"),
            commands=args.command,
            timeout=args.timeout,
            keep_workdir=args.keep,
            sandbox=args.sandbox,
            lane_resources=lane_resources,
        )
    except checks_mod.CheckError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.verdict_out:
        from .snapshot import compute_for_scope

        snapshot = args.snapshot_id or compute_for_scope(root, args.scope or "")
        verdict = checks_mod.build_verdict(
            brief_data, report,
            task_id=args.task or brief_data.get("task_id", "checks"),
            attempt=args.attempt,
            snapshot_id=snapshot,
        )
        os.makedirs(os.path.dirname(os.path.abspath(args.verdict_out)), exist_ok=True)
        with open(args.verdict_out, "w", encoding="utf-8") as fh:
            json.dump(verdict, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        report["verdict"] = {"path": args.verdict_out, "verdict": verdict["verdict"]}

    if args.json:
        _print(report, True)
    else:
        print(checks_mod.render_text(report))
        if "verdict" in report:
            print(f"вердикт: {report['verdict']['verdict']} -> {report['verdict']['path']}")
    return EXIT_OK if report["all_ok"] else EXIT_DENIED



# --------------------------------------------------------------------- парсер


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="harness",
        description="Управляемый агентный цикл: бриф → исполнитель → независимая проверка → принятие/эскалация.",
    )
    p.add_argument("--root", default=".", help="корень проекта (по умолчанию текущий каталог)")
    p.add_argument("--control-root", default=None,
                   help="где живут политика и состояние, если работа идёт в лейне")
    p.add_argument("--db", default=None, help="путь к хранилищу состояния")
    p.add_argument("--json", action="store_true", help="машинный вывод")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="создать хранилище состояния").set_defaults(func=cmd_init)

    task = sub.add_parser("task", help="задачи")
    tsub = task.add_subparsers(dest="task_cmd", required=True)
    tn = tsub.add_parser("new", help="создать задачу")
    tn.add_argument("--id", required=True)
    tn.add_argument("--spec", default=None)
    tn.add_argument("--base-commit", default=None)
    tn.add_argument("--lane", default="main")
    tn.add_argument("--change", default=None, help="change_id, к которому относится задача")
    tn.set_defaults(func=cmd_task_new)
    tl = tsub.add_parser("list", help="список задач")
    tl.set_defaults(func=cmd_task_list)
    ts = tsub.add_parser("status", help="состояние задачи")
    ts.add_argument("--task", required=True)
    ts.set_defaults(func=cmd_task_status)

    session = sub.add_parser("session", help="привязка DSH-сессии к задаче")
    ssub = session.add_subparsers(dest="session_cmd", required=True)
    sa = ssub.add_parser("attach", help="привязать сессию к задаче")
    sa.add_argument("--task", required=True)
    sa.add_argument("--auto-submit", action="store_true",
                    help="отправить задачу на проверку в конце первого turn")
    sa.set_defaults(func=cmd_session)
    sd = ssub.add_parser("detach", help="убрать привязку")
    sd.set_defaults(func=cmd_session)
    ss = ssub.add_parser("status", help="показать привязку")
    ss.set_defaults(func=cmd_session)

    brief = sub.add_parser("brief", help="бриф")
    bsub = brief.add_subparsers(dest="brief_cmd", required=True)
    bc = bsub.add_parser("check", help="проверить бриф")
    bc.add_argument("file")
    bc.set_defaults(func=cmd_brief_check)
    ba = bsub.add_parser("attach", help="прикрепить бриф к задаче")
    ba.add_argument("--task", required=True)
    ba.add_argument("file")
    ba.add_argument("--event-id", default=None)
    ba.set_defaults(func=cmd_brief_attach)
    bf = bsub.add_parser("from-change", help="собрать бриф из OpenSpec-change")
    bf.add_argument("--change", required=True, help="имя change в openspec/changes")
    bf.add_argument("--task", default=None, help="task_id брифа (по умолчанию имя change)")
    bf.add_argument("--out", default=None, help="куда записать JSON брифа")
    bf.add_argument("--check", action="append", default=None, help="команда проверки (можно несколько)")
    bf.add_argument("--base-commit", default=None)
    bf.add_argument("--spec-ref", default=None, help="полный SHA согласованного плана (40 hex)")
    bf.add_argument("--actor-role", default="DEV", choices=["BA", "SA", "DEV", "QA", "verifier"],
                    help="процессная роль исполнителя (влияет на права guard'а)")
    bf.add_argument("--attempt-limit", type=int, default=3)
    bf.add_argument("--timeout", type=int, default=3600)
    bf.add_argument("--json", action="store_true")
    bf.set_defaults(func=cmd_brief_from_change)

    chg = sub.add_parser("change", help="уровень изменения: состояния, review, trace, Ready-гейт")
    csub = chg.add_subparsers(dest="change_cmd", required=True)
    cn = csub.add_parser("new", help="зарегистрировать изменение в состоянии")
    cn.add_argument("--change", required=True)
    cn.add_argument("--owner", required=True)
    cn.add_argument("--risk", default="L1", choices=["L0", "L1", "L2", "L3"])
    cn.add_argument("--title", default="")
    cn.add_argument("--base-commit", default=None)
    cn.add_argument("--service", action="append", default=None,
                    help="name=PATH[@SHA]: сервис, на который опирается change")
    cn.add_argument("--service-write", action="append", default=None,
                    help="имя сервиса, в который разрешена запись (read-only по умолчанию)")
    cn.set_defaults(func=cmd_change)
    csvc = csub.add_parser("services", help="сервисы и их принятые версии")
    csvc.add_argument("--change", required=True)
    csvc.set_defaults(func=cmd_change)
    cl = csub.add_parser("list", help="список изменений")
    cl.set_defaults(func=cmd_change)
    cshow = csub.add_parser("show", help="состояние изменения и trace")
    cshow.add_argument("--change", required=True)
    cshow.set_defaults(func=cmd_change)
    crev = csub.add_parser("review", help="добавить review-запись артефакта")
    crev.add_argument("--change", required=True)
    crev.add_argument("--artifact", required=True, help="proposal.md | specs | design.md | contracts | tasks.md")
    crev.add_argument("--reviewer", required=True)
    crev.add_argument("--role", required=True, choices=["product_owner", "BA", "SA", "DEV", "QA", "verifier", "human"])
    crev.add_argument("--author", default=None)
    crev.add_argument("--verdict", default="approve", choices=["approve", "request_changes", "abstain"])
    crev.add_argument("--criterion", action="append", default=None, help="что именно проверено (нужно для approve)")
    crev.set_defaults(func=cmd_change)
    ctr = csub.add_parser("trace", help="матрица REQ → CASE → задачи")
    ctr.add_argument("--change", required=True)
    ctr.set_defaults(func=cmd_change)
    crd = csub.add_parser("ready", help="пройти Ready-гейт и перевести в READY")
    crd.add_argument("--change", required=True)
    crd.add_argument("--spec-ref", required=True, help="полный SHA согласованного плана (40 hex)")
    crd.set_defaults(func=cmd_change)
    cad = csub.add_parser("advance", help="переход состояния изменения")
    cad.add_argument("--change", required=True)
    cad.add_argument("--event", required=True,
                     choices=["explore", "propose", "agree_specs", "agree_design", "start", "tasks_done",
                              "verify", "release", "accept", "archive", "block", "unblock", "cancel",
                              "supersede", "revise"])
    cad.add_argument("--reason", default=None)
    cad.set_defaults(func=cmd_change)
    crel = csub.add_parser("release", help="записать выпуск в опорную среду")
    crel.add_argument("--change", required=True)
    crel.add_argument("--environment", required=True)
    crel.add_argument("--service", action="append", default=None, help="name=полный SHA (можно несколько)")
    crel.add_argument("--by", required=True)
    crel.add_argument("--by-role", default="human", choices=["human", "agent", "ci"])
    crel.add_argument("--flag", default="")
    crel.set_defaults(func=cmd_change)
    cacc = csub.add_parser("accept", help="записать приёмку бизнес-результата")
    cacc.add_argument("--change", required=True)
    cacc.add_argument("--accepted-by", required=True)
    cacc.add_argument("--criterion", action="append", default=None, help="принятый критерий (нужен хотя бы один)")
    cacc.add_argument("--environment", required=True)
    cacc.add_argument("--service", action="append", default=None, help="name=полный SHA (можно несколько)")
    cacc.add_argument("--by-role", default="human", choices=["human", "agent", "ci"])
    cacc.add_argument("--residual", action="append", default=None, help="принятый остаточный риск")
    cacc.set_defaults(func=cmd_change)
    ccl = csub.add_parser("close", help="закрыть change: closing + evidence + archive")
    ccl.add_argument("--change", required=True)
    ccl.add_argument("--by", required=True)
    ccl.add_argument("--specs-synced", action="store_true")
    ccl.add_argument("--cases-promoted", action="store_true")
    ccl.add_argument("--contracts-promoted", action="store_true")
    ccl.set_defaults(func=cmd_change)
    cev = csub.add_parser("evidence", help="собрать пакет доказательств с хеш-манифестом")
    cev.add_argument("--change", required=True)
    cev.set_defaults(func=cmd_change)

    ver = sub.add_parser("verification", help="baseline/candidate прогоны изменения")
    vsub = ver.add_subparsers(dest="verification_cmd", required=True)
    vrun = vsub.add_parser("run", help="выполнить прогон и записать версии")
    vrun.add_argument("--change", required=True)
    vrun.add_argument("--mode", required=True, choices=["baseline", "candidate"])
    vrun.add_argument("--command", required=True)
    vrun.add_argument("--ref", default=None, help="ref для baseline-worktree (например, base commit)")
    vrun.add_argument("--runner", default="")
    vrun.add_argument("--environment", default="")
    vrun.add_argument("--service", action="append", default=None, help="name=полный SHA (можно несколько)")
    vrun.add_argument("--cwd", default=None, help="каталог сервиса для прогона (по умолчанию корень)")
    vrun.add_argument("--timeout", type=int, default=600)
    vrun.set_defaults(func=cmd_verification)
    vshow = vsub.add_parser("show", help="показать сохранённый отчёт")
    vshow.add_argument("--change", required=True)
    vshow.set_defaults(func=cmd_verification)

    gap = sub.add_parser("gap", help="GAP-вопрос человеку: публикация и снятие")
    gsub = gap.add_subparsers(dest="gap_cmd", required=True)
    ga = gsub.add_parser("ask", help="опубликовать вопрос: пишущие вызовы спросят человека")
    ga.add_argument("--task", required=True)
    ga.add_argument("--question", required=True)
    ga.add_argument("--id", default="GAP-1")
    ga.set_defaults(func=cmd_gap)
    gc = gsub.add_parser("clear", help="снять вопрос после ответа")
    gc.set_defaults(func=cmd_gap)
    gs = gsub.add_parser("show", help="показать открытый вопрос")
    gs.set_defaults(func=cmd_gap)

    d = sub.add_parser("dispatch", help="запустить исполнителя (нужен валидный бриф)")
    d.add_argument("--task", required=True)
    d.add_argument("--worker", default=None)
    d.add_argument("--timeout", type=int, default=None)
    d.add_argument("--primary-agent", default=None,
                   help="id агента-оркестратора: он получит роль orchestrator, остальные — роль фазы")
    d.add_argument("--event-id", default=None)
    d.set_defaults(func=cmd_dispatch)

    s = sub.add_parser("submit", help="подать результат на проверку")
    s.add_argument("--task", required=True)
    s.add_argument("--attempt", type=int, required=True)
    s.add_argument("--snapshot", default=None, help="каталог кандидата (по умолчанию — всё дерево)")
    s.add_argument("--scope", default=None, help="область снимка относительно корня")
    s.add_argument("--snapshot-id", default=None)
    s.add_argument("--event-id", default=None)
    s.set_defaults(func=cmd_submit)

    v = sub.add_parser("verdict", help="применить вердикт проверяющего")
    v.add_argument("--task", required=True)
    v.add_argument("file")
    v.add_argument("--event-id", default=None)
    v.set_defaults(func=cmd_verdict)

    c = sub.add_parser("commit", help="интегрировать принятого кандидата")
    c.add_argument("--task", required=True)
    c.add_argument("--snapshot", default=None)
    c.add_argument("--scope", default=None)
    c.add_argument("--skip-snapshot-check", action="store_true")
    c.add_argument("--event-id", default=None)
    c.set_defaults(func=cmd_commit)

    ev = sub.add_parser("event", help="доставить событие автомата")
    ev.add_argument("--task", required=True)
    ev.add_argument("--event", required=True,
                    choices=["timeout", "requeue", "invalidate", "escalate", "cancel", "recheck", "supersede"])
    ev.add_argument("--event-id", default=None)
    ev.set_defaults(func=cmd_event)

    w = sub.add_parser("watchdog", help="сторожевой таймер: отзыв просроченных полномочий")
    w.add_argument("action", nargs="?", default="check", choices=["check", "sweep", "serve"])
    w.add_argument("--task", default=None)
    w.add_argument("--interval", type=float, default=15.0)
    w.add_argument("--cycles", type=int, default=None, help="ограничить число проходов")
    w.set_defaults(func=cmd_watchdog)

    sb = sub.add_parser("sandbox", help="проба доступной изоляции прогона проверок")
    sb.add_argument("action", nargs="?", default="probe", choices=["probe"])
    sb.set_defaults(func=cmd_sandbox)

    ln = sub.add_parser("lane", help="параллельные лейны в git worktree")
    ln.add_argument("action", choices=["create", "list", "merge", "remove", "show", "release",
                                       "push", "pr"])
    ln.add_argument("--task", default=None)
    ln.add_argument("--base", default="HEAD")
    ln.add_argument("--message", default=None)
    ln.add_argument("--keep-branch", action="store_true")
    ln.add_argument("--remote", default="origin")
    ln.add_argument("--target", default="main")
    ln.add_argument("--title", default=None)
    ln.add_argument("--body", default=None)
    ln.set_defaults(func=cmd_lane)

    fg = sub.add_parser("forge", help="статус PR и checks через gh")
    fg.add_argument("action", choices=["status"])
    fg.add_argument("--task", default=None)
    fg.add_argument("--branch", default=None)
    fg.add_argument("--wait", action="store_true")
    fg.add_argument("--timeout", type=float, default=300.0)
    fg.set_defaults(func=cmd_forge)

    ev = sub.add_parser("evidence", help="экспорт/проверка evidence в доверенном каталоге")
    ev.add_argument("action", choices=["export", "verify"])
    ev.add_argument("--dest", default=None)
    ev.add_argument("--change", default=None)
    ev.set_defaults(func=cmd_evidence)

    e2 = sub.add_parser("e2e", help="multi-service e2e на копиях сервисов")
    e2.add_argument("action", choices=["run"])
    e2.add_argument("--change", required=True)
    e2.add_argument("--command", required=True)
    e2.add_argument("--service", action="append", default=[])
    e2.add_argument("--timeout", type=int, default=600)
    e2.set_defaults(func=cmd_e2e)

    co = sub.add_parser("cost", help="стоимость задачи: запись и отчёт")
    co.add_argument("action", choices=["record", "report"])
    co.add_argument("--task", default=None)
    co.add_argument("--amount", type=float, default=None)
    co.add_argument("--unit", default="cost_units")
    co.add_argument("--kind", default="model")
    co.add_argument("--attempt", type=int, default=None)
    co.add_argument("--note", default=None)
    co.set_defaults(func=cmd_cost)

    ru = sub.add_parser("rule", help="дисциплина изменения правил")
    ru.add_argument("action", choices=["change", "verify"])
    ru.add_argument("--id", default=None)
    ru.add_argument("--reason", default=None)
    ru.add_argument("--author", default="human")
    ru.set_defaults(func=cmd_rule)

    dr = sub.add_parser("doctor", help="проверка живой конфигурации контроля")
    dr.set_defaults(func=cmd_doctor)

    pl = sub.add_parser("plugin", help="подготовка установленного плагина harness-guard")
    pl.add_argument("action", choices=["prepare", "status"])
    pl.set_defaults(func=cmd_plugin)

    rn = sub.add_parser("run", help="прогнать задачу через пайплайн одной командой")
    rn.add_argument("--task", required=True)
    rn.add_argument("--brief", default=None, help="JSON брифа (нужен для новой задачи)")
    rn.add_argument("--worker", required=True, help="команда исполнителя (exit 0 = работа сделана)")
    rn.add_argument("--verifier", default=None, help="команда проверяющего: вердикт в stdout или $HARNESS_VERDICT_OUT")
    rn.add_argument("--adversarial", default=None, help="адверсальный проверяющий (необязательно)")
    rn.add_argument("--commit", action="store_true", help="закоммитить принятый результат финализатором")
    rn.add_argument("--lane", action="store_true", help="работать в отдельном worktree")
    rn.add_argument("--merge", action="store_true", help="слить лейн в главное дерево после коммита")
    rn.add_argument("--allow-no-spec", default=None, metavar="ПРИЧИНА", help="коммит кода без спеки: причина попадёт в журнал (R-SPEC-1)")
    rn.add_argument("--scope", default=None, help="область снимка относительно корня лейна")
    rn.add_argument("--timeout", type=int, default=None)
    rn.add_argument("--message", default=None)
    rn.set_defaults(func=cmd_run)

    h = sub.add_parser("hook", help="событие инструмента (для обвязки рантайма)")
    h.add_argument("event", default="pretooluse", nargs="?")
    h.add_argument("--stdin", action="store_true", help="читать контекст из stdin (JSON)")
    h.add_argument("--tool", default="")
    h.add_argument("--role", default="")
    h.add_argument("--claimed-role", default=None)
    h.add_argument("--path", default=None)
    h.add_argument("--command", default=None)
    h.add_argument("--target", default=None)
    h.add_argument("--allowed-file", action="append", default=None)
    h.set_defaults(func=cmd_hook)

    f = sub.add_parser("findings", help="реестр находок")
    f.add_argument("action", choices=["add", "close", "list", "check"])
    f.add_argument("--task", default=None)
    f.add_argument("--finding-id", default=None)
    f.add_argument("--severity", default="major", choices=["blocker", "major", "minor", "cosmetic"])
    f.add_argument("--statement", default=None)
    f.add_argument("--evidence", default=None)
    f.add_argument("--owner", default=None)
    f.add_argument("--track", default=None)
    f.add_argument("--attempt", type=int, default=None)
    f.add_argument("--reason", default=None)
    f.set_defaults(func=cmd_findings)

    cov = sub.add_parser("coverage", help="матрица правил и пробелы покрытия")
    cov.add_argument("--markdown", default=None)
    cov.add_argument("--rules", default=None)
    cov.set_defaults(func=cmd_coverage)

    mt = sub.add_parser("metrics", help="метрики процесса из SQLite и файлов change")
    mt.add_argument("--markdown", default=None, help="записать отчёт в markdown")
    mt.set_defaults(func=cmd_metrics)

    bd = sub.add_parser("board", help="HTML-доска состояния процесса")
    bd.add_argument("--out", default=None, help="куда записать HTML (снимок)")
    bd.add_argument("--serve", action="store_true", help="живая доска по HTTP")
    bd.add_argument("--host", default="127.0.0.1")
    bd.add_argument("--port", type=int, default=8765)
    bd.set_defaults(func=cmd_board)

    ot = sub.add_parser("otel", help="OTLP/HTTP JSON с метриками процесса")
    ot.add_argument("--out", default=None, help="записать OTLP JSON")
    ot.add_argument("--endpoint", default=None, help="POST на OTLP/HTTP endpoint")
    ot.add_argument("--service-name", default="harness")
    ot.add_argument("--service-version", default="")
    ot.add_argument("--timeout", type=int, default=10)
    ot.add_argument("--traces-task", default=None, help="добавить spans задачи в отдельный OTLP")
    ot.add_argument("--traces-out", default=None, help="записать OTLP traces в файл")
    ot.add_argument("--traces-endpoint", default=None, help="POST traces на OTLP/HTTP endpoint")
    ot.set_defaults(func=cmd_otel)

    cifn = sub.add_parser("ci", help="CI-снимок: selftest, покрытие, метрики, тесты")
    cifn.add_argument("--report", default=None, help="путь отчёта (по умолчанию evidence/ci-<ts>/report.json)")
    cifn.add_argument("--run-tests", action="store_true", help="запустить unittest")
    cifn.add_argument("--timeout", type=int, default=900)
    cifn.set_defaults(func=cmd_ci)

    st = sub.add_parser("selftest", help="исполняемые проверки правил")
    st.set_defaults(func=cmd_selftest)

    pii_p = sub.add_parser("pii", help="поиск/редакция персональных данных")
    pii_p.add_argument("action", choices=["scan", "redact"])
    pii_p.add_argument("--text", default=None)
    pii_p.add_argument("--file", default=None)
    pii_p.add_argument("--out", default=None)
    pii_p.set_defaults(func=cmd_pii)

    sl = sub.add_parser("slo", help="SLO процесса по метрикам")
    sl.add_argument("--strict", action="store_true", help="exit 2 при нарушении порога")
    sl.set_defaults(func=cmd_slo)

    ev2 = sub.add_parser("evals", help="измерение верификатора")
    esub = ev2.add_subparsers(dest="evals_cmd", required=True)
    er = esub.add_parser("run", help="прогнать размеченные случаи")
    er.add_argument("--cases", required=True)
    er.add_argument("--verifier", required=True, help="команда верификатора (читает JSON из stdin)")
    er.set_defaults(func=cmd_evals)

    dm = sub.add_parser("demo", help="сквозной демонстрационный прогон")
    dm.set_defaults(func=cmd_demo)

    tl = sub.add_parser("tool", help="шлюз инструментов (единственный путь записи агента)")
    tl.add_argument("--stdin", action="store_true", help="читать запрос из stdin (JSON)")
    tl.add_argument("--tool", default="")
    tl.add_argument("--role", default=None)
    tl.add_argument("--task", default=None)
    tl.add_argument("--path", default=None)
    tl.add_argument("--content", default=None)
    tl.add_argument("--content-file", default=None)
    tl.add_argument("--command", default=None)
    tl.set_defaults(func=cmd_tool)

    fn = sub.add_parser("finalize", help="доверенный финализатор: write-set + коммит + журнал")
    fn.add_argument("--task", required=True)
    fn.add_argument("--message", default=None)
    fn.add_argument("--author", default="harness")
    fn.add_argument("--allow-no-spec", default=None, metavar="ПРИЧИНА", help="обход R-SPEC-1 с причиной в журнале")
    fn.set_defaults(func=cmd_finalize)

    ck = sub.add_parser("checks", help="изолированный прогон проверок из брифа")
    cksub = ck.add_subparsers(dest="checks_cmd", required=True)
    ckr = cksub.add_parser("run", help="выполнить команды проверки в копии дерева")
    ckr.add_argument("--task", default=None)
    ckr.add_argument("--brief", default=None)
    ckr.add_argument("--command", action="append", default=None, help="переопределить команды")
    ckr.add_argument("--timeout", type=int, default=300)
    ckr.add_argument("--attempt", type=int, default=1)
    ckr.add_argument("--scope", default="")
    ckr.add_argument("--snapshot-id", default=None)
    ckr.add_argument("--verdict-out", default=None)
    ckr.add_argument("--keep", action="store_true", help="не удалять копию дерева (для разбора)")
    ckr.add_argument("--sandbox", default="auto", choices=["auto", "none", "docker"],
                     help="изоляция прогона: контейнер без сети, если доступен")
    ckr.set_defaults(func=cmd_checks_run)

    return p


def main(argv: list[str] | None = None) -> int:
    # Windows CI без UTF-8 в консоли: русский вывод не должен ронять команду.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FileNotFoundError as exc:
        print(f"файл не найден: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except StoreError as exc:
        print(f"ОШИБКА СОСТОЯНИЯ: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
