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
from . import evals as evals_mod
from . import findings as findings_mod
from . import rules as rules_mod
from . import selftest as selftest_mod
from . import verdict as verdict_mod
from .fsm import Engine, TransitionError, check_watchdog
from .hooks import HookContext, run as run_hooks
from .policy import Policy
from .snapshot import compute_for_scope, compute_snapshot, scope_of
from .state import Store, StoreError

EXIT_OK = 0
EXIT_DENIED = 2
EXIT_ERROR = 3


def _root(args: argparse.Namespace) -> str:
    return os.path.abspath(args.root)


def _store(args: argparse.Namespace) -> Store:
    path = args.db or os.path.join(_root(args), ".harness", "state.db")
    return Store(path)


def _engine(args: argparse.Namespace, store: Store | None = None) -> Engine:
    root = _root(args)
    return Engine(store or _store(args), Policy.load(root), root)


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


def cmd_task_new(args: argparse.Namespace) -> int:
    eng = _engine(args)
    task = eng.store.create_task(
        args.id,
        spec_version=args.spec,
        base_commit=args.base_commit,
        lane=args.lane,
        policy_version=eng.policy.version,
        max_attempts=int(eng.policy.limit("max_attempts", 3)),
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


def cmd_brief_attach(args: argparse.Namespace) -> int:
    eng = _engine(args)
    with open(args.file, encoding="utf-8") as fh:
        raw = json.load(fh)
    return _apply(eng, args, "attach_brief", {"brief": raw}, event_id=args.event_id)


def cmd_dispatch(args: argparse.Namespace) -> int:
    eng = _engine(args)
    return _apply(
        eng,
        args,
        "dispatch",
        {"worker_id": args.worker, "timeout_seconds": args.timeout},
        event_id=args.event_id,
    )


def cmd_submit(args: argparse.Namespace) -> int:
    eng = _engine(args)
    scope = args.scope or ""
    snapshot = args.snapshot_id
    if not snapshot:
        if args.snapshot:
            scope = scope_of(_root(args), args.snapshot)
        snapshot = compute_for_scope(_root(args), scope)
    return _apply(
        eng,
        args,
        "submit",
        {"attempt": args.attempt, "snapshot_id": snapshot, "snapshot_scope": scope},
        event_id=args.event_id,
    )


def cmd_verdict(args: argparse.Namespace) -> int:
    eng = _engine(args)
    with open(args.file, encoding="utf-8") as fh:
        raw = json.load(fh)
    return _apply(eng, args, "verdict", {"verdict": raw}, event_id=args.event_id)


def cmd_commit(args: argparse.Namespace) -> int:
    eng = _engine(args)
    payload: dict[str, Any] = {}
    if args.snapshot or not args.skip_snapshot_check:
        scope = args.scope or (scope_of(_root(args), args.snapshot) if args.snapshot else "")
        payload["snapshot_id"] = compute_for_scope(_root(args), scope)
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
    eng = _engine(args)
    out = check_watchdog(eng, args.task)
    if out is None:
        _print({"ok": True, "state": eng.store.get_task(args.task)["state"], "message": "не просрочен"}, args.json)
        return EXIT_OK
    _print(out.to_dict(), args.json)
    return EXIT_OK


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
        policy=Policy.load(root),
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
    statuses = rules_mod.evaluate(registry)
    sums = rules_mod.summary(statuses)
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
    print(f"правил: {sums['total']} · покрыто: {sums['covered']} · пробелов: {sums['gap']} · "
          f"сломано: {sums['broken']} · покрытие: {sums['coverage_ratio']:.0%}")
    for s in statuses:
        if s.status != "covered":
            print(f"  ПРОБЕЛ {s.id} ({s.enforcement}): {s.reason}")
    return EXIT_OK if sums["broken"] == 0 else EXIT_DENIED


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
        gateway = Gateway(root, Policy.load(root), store)
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
    store = _store(args)
    try:
        engine = Engine(store, Policy.load(root), root)
        try:
            out = finalize(store, engine, root, args.task, message=args.message, author=args.author)
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

    try:
        report = checks_mod.run(
            root,
            brief_data,
            task_id=args.task or brief_data.get("task_id", "checks"),
            commands=args.command,
            timeout=args.timeout,
            keep_workdir=args.keep,
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
    tn.set_defaults(func=cmd_task_new)
    tl = tsub.add_parser("list", help="список задач")
    tl.set_defaults(func=cmd_task_list)
    ts = tsub.add_parser("status", help="состояние задачи")
    ts.add_argument("--task", required=True)
    ts.set_defaults(func=cmd_task_status)

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

    d = sub.add_parser("dispatch", help="запустить исполнителя (нужен валидный бриф)")
    d.add_argument("--task", required=True)
    d.add_argument("--worker", default=None)
    d.add_argument("--timeout", type=int, default=None)
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

    w = sub.add_parser("watchdog", help="проверить и отозвать просроченные полномочия")
    w.add_argument("--task", required=True)
    w.set_defaults(func=cmd_watchdog)

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

    st = sub.add_parser("selftest", help="исполняемые проверки правил")
    st.set_defaults(func=cmd_selftest)

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
    ckr.set_defaults(func=cmd_checks_run)

    return p


def main(argv: list[str] | None = None) -> int:
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
