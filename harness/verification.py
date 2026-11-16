"""Приёмочные прогоны: baseline (принятое поведение) и candidate (изменение).

Проверка «тесты прошли» без версии и без второго прогона ничего не доказывает:
непонятно, что именно прошло и не сломалось ли принятое. Здесь два прогона
сохраняются как объекты с версиями (spec_ref, ref, окружение, runner, сервисы),
а diff показывает новые падения относительно baseline.

Запуск команд — обычный subprocess с таймаутом; baseline может идти в отдельном
git-worktree на указанном ref, candidate — в рабочем дереве. Нулевой прогон
(«Ran 0 tests») не считается проверкой.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from typing import Any

from .state import Store, new_id, now

VERIFY_FILE = "verification.json"
VERIFY_MD = "verification.md"
MODES = ("baseline", "candidate")

_RAN = re.compile(r"Ran (\d+) tests?", re.IGNORECASE)
_FAIL = re.compile(r"^(?:FAIL|ERROR):\s+([^\s(]+)", re.MULTILINE)
_PYTEST_FAIL = re.compile(r"^FAILED\s+(\S+)", re.MULTILINE)


def _git(root: str, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def parse_output(output: str, exit_code: int, *, timed_out: bool = False) -> dict[str, Any]:
    """Факты из вывода: сколько тестов, какие упали, итог. Не догадки."""
    match = _RAN.search(output)
    tests_ran = int(match.group(1)) if match else 0
    failures = sorted(set(_FAIL.findall(output)) | set(_PYTEST_FAIL.findall(output)))
    if timed_out:
        result = "timeout"
    elif exit_code == 0:
        result = "pass"
    else:
        result = "fail"
    return {"result": result, "exit_code": exit_code, "tests_ran": tests_ran, "failures": failures}


def _prepare_cwd(root: str, mode: str, ref: str | None) -> tuple[str, str | None]:
    """Каталог прогона и worktree, который надо убрать после."""
    if mode == "baseline" and ref:
        tmp_root = os.path.join(root, ".harness", "tmp")
        os.makedirs(tmp_root, exist_ok=True)
        path = os.path.join(tmp_root, f"verify-{uuid.uuid4().hex[:10]}")
        proc = subprocess.run(
            ["git", "worktree", "add", "--detach", path, ref], cwd=root,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"git worktree add {ref} не удался: {proc.stderr.strip()[:300]}")
        return path, path
    return root, None


def _cleanup_worktree(root: str, path: str | None) -> None:
    if not path:
        return
    subprocess.run(["git", "worktree", "remove", "--force", path], cwd=root,
                   capture_output=True, text=True, timeout=120)
    shutil.rmtree(path, ignore_errors=True)


def run_mode(root: str, mode: str, command: str, *, ref: str | None = None,
             timeout: int = 600, environment: str = "", runner: str = "",
             service: str = "repo", cwd: str | None = None) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"режим должен быть один из {MODES}")
    if not (command or "").strip():
        raise ValueError("R-QA-1: команда прогона не задана")
    base = os.path.abspath(cwd) if cwd else root
    if not os.path.isdir(base):
        raise ValueError(f"каталог сервиса не найден: {base}")
    run_cwd, worktree = _prepare_cwd(base, mode, ref)
    started = now()
    timed_out = False
    try:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["HARNESS_VERIFY_MODE"] = mode
        proc = subprocess.run(
            command, shell=True, cwd=run_cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=env,
        )
        stdout, stderr, code = proc.stdout or "", proc.stderr or "", proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        code, timed_out = -1, True
    finally:
        _cleanup_worktree(base, worktree)
    parsed = parse_output(stdout + "\n" + stderr, code, timed_out=timed_out)
    head = _git(run_cwd, "rev-parse", "HEAD") or ""
    return {
        "mode": mode,
        "service": service,
        "command": command,
        "ref": ref or head,
        "cwd": os.path.relpath(base, root).replace("\\", "/") if base != root else ".",
        "runner": runner,
        "environment": environment,
        "started_at": started,
        "duration_s": round(now() - started, 2),
        "result": parsed["result"],
        "exit_code": parsed["exit_code"],
        "tests_ran": parsed["tests_ran"],
        "failures": parsed["failures"],
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-2000:],
    }


def diff_results(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    base_fail = set(baseline.get("failures") or [])
    cand_fail = set(candidate.get("failures") or [])
    new_failures = sorted(cand_fail - base_fail)
    fixed = sorted(base_fail - cand_fail)
    regression = bool(new_failures) or (
        baseline.get("result") == "pass" and candidate.get("result") != "pass"
    )
    return {
        "new_failures": new_failures,
        "fixed": fixed,
        "regression": regression,
    }


def _report_path(root: str, change_id: str) -> str:
    return os.path.join(root, "openspec", "changes", change_id, VERIFY_FILE)


def load(root: str, change_id: str) -> dict[str, Any] | None:
    try:
        with open(_report_path(root, change_id), encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write(root: str, change_id: str, report: dict[str, Any]) -> str:
    path = _report_path(root, change_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    with open(os.path.join(os.path.dirname(path), VERIFY_MD), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_md(report))
    return path


def render_md(report: dict[str, Any]) -> str:
    lines = [
        f"# Проверка изменения {report.get('change_id')}",
        "",
        f"- spec_ref: `{report.get('spec_ref')}`",
        f"- редакция: {report.get('revision')}",
        f"- окружение: {report.get('environment') or '—'}",
        f"- runner: {report.get('runner') or '—'}",
        f"- сервисы: {', '.join(s.get('name', '?') + '@' + str(s.get('sha'))[:8] for s in report.get('services') or []) or '—'}",
        f"- итог: {'OK' if report.get('ok') else 'НЕ ПРОЙДЕНО'}",
        "",
        "| сервис | режим | результат | тестов | command |",
        "|---|---|---|---|---|",
    ]
    groups = _group_results(report.get("results") or {}) or {"repo": {}}
    for service, pair in groups.items():
        for mode in MODES:
            row = pair.get(mode) or {}
            lines.append(f"| {service} | {mode} | {row.get('result', '—')} |"
                         f" {row.get('tests_ran', '—')} | `{row.get('command', '—')}` |")
    diff = report.get("diff") or {}
    lines += ["", f"Новые падения: {', '.join(diff.get('new_failures') or []) or 'нет'}"]
    lines.append(f"Исправлено: {', '.join(diff.get('fixed') or []) or 'нет'}")
    problems = report.get("problems") or []
    if problems:
        lines += ["", "## Проблемы", ""] + [f"- {p}" for p in problems]
    return "\n".join(lines) + "\n"


def _services_from_git(root: str, explicit: list[dict[str, str]] | None) -> list[dict[str, str]]:
    if explicit:
        return explicit
    head = _git(root, "rev-parse", "HEAD")
    return [{"name": "repo", "sha": head or ""}] if head else []


def _group_results(results: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Прогоны по сервисам: старый формат baseline/candidate = сервис repo."""
    groups: dict[str, dict[str, Any]] = {}
    for key, row in (results or {}).items():
        if not isinstance(row, dict):
            continue
        if key in MODES:
            groups.setdefault("repo", {})[key] = row
        elif ":" in key:
            service, mode = key.rsplit(":", 1)
            if mode in MODES:
                groups.setdefault(service, {})[mode] = row
    return groups


def _declared_names(change: dict[str, Any]) -> list[str]:
    return [str(s.get("name")) for s in (change.get("payload") or {}).get("services") or []
            if str(s.get("name") or "").strip()]


def record(store: Store, root: str, change_id: str, mode: str, command: str, *,
           ref: str | None = None, timeout: int = 600, environment: str = "",
           runner: str = "", services: list[dict[str, str]] | None = None,
           service: str = "repo", cwd: str | None = None) -> dict[str, Any]:
    change = store.get_change(change_id)
    if not change.get("spec_ref"):
        raise ValueError("R-QA-1: у change нет spec_ref — сначала Ready")
    result = run_mode(root, mode, command, ref=ref, timeout=timeout,
                      environment=environment, runner=runner, service=service, cwd=cwd)
    existing = load(root, change_id) or {
        "change_id": change_id,
        "created_at": now(),
        "results": {},
        "commands": {},
    }
    if existing.get("revision") not in (None, change["revision"]):
        existing = {"change_id": change_id, "created_at": now(), "results": {}, "commands": {}}
    existing["revision"] = int(change["revision"])
    existing["spec_ref"] = change["spec_ref"]
    existing["environment"] = environment or existing.get("environment", "")
    existing["runner"] = runner or existing.get("runner", "")
    key = mode if service == "repo" else f"{service}:{mode}"
    existing["commands"][key] = command
    existing["results"][key] = result

    known = {str(s.get("name")): s for s in existing.get("services") or []}
    for row in _services_from_git(cwd or root, services):
        if row.get("name"):
            known[str(row["name"])] = row
    if service not in known:
        head = _git(os.path.abspath(cwd) if cwd else root, "rev-parse", "HEAD")
        if head:
            known[service] = {"name": service, "sha": head}
    existing["services"] = list(known.values())

    report = existing
    diffs = {
        svc: diff_results(pair["baseline"], pair["candidate"])
        for svc, pair in _group_results(report["results"]).items()
        if all(m in pair for m in MODES)
    }
    report["diffs"] = diffs
    report["diff"] = diffs.get("repo", {})
    check = check_report(store, root, change_id, report=report)
    report["problems"] = check["problems"]
    report["ok"] = check["ok"]
    _write(root, change_id, report)
    store.append_change_event(new_id("cevt"), change_id, "verification",
                              {"mode": mode, "service": service, "command": command,
                               "result": result["result"]},
                              {"ok": check["ok"], "tests_ran": result["tests_ran"]})
    return report


def check_report(store: Store, root: str, change_id: str,
                 report: dict[str, Any] | None = None) -> dict[str, Any]:
    """R-QA-1 / R-QA-2: отчёт полон, версии совпадают, прогон не нулевой."""
    change = store.get_change(change_id)
    data = report or load(root, change_id)
    problems: list[str] = []
    if not data:
        return {"ok": False, "problems": ["нет verification.json — прогонов не было"]}
    if str(data.get("spec_ref")) != str(change.get("spec_ref")):
        problems.append("verification.spec_ref не совпадает с планом")
    if data.get("revision") not in (None, change["revision"]):
        problems.append("verification.revision не совпадает с редакцией плана")
    if not str(data.get("environment") or "").strip():
        problems.append("не указано окружение")
    if not str(data.get("runner") or "").strip():
        problems.append("не указан runner")
    services = {str(s.get("name")): s for s in (data.get("services") or [])}
    if not services:
        problems.append("не указаны сервисы и их версии")
    for name, service in services.items():
        sha = str(service.get("sha") or "")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            problems.append(f"сервис {name!r} без полного SHA")

    groups = _group_results(data.get("results") or {})
    if not groups:
        return {"ok": False, "problems": problems + ["нет прогонов baseline/candidate"]}
    declared = _declared_names(change)
    for name in declared:
        if name not in groups:
            problems.append(f"нет прогонов сервиса {name!r}, объявленного в change")
    for name in groups:
        if declared and name not in declared:
            problems.append(f"прогон сервиса {name!r} не объявлен в change")
        if name not in services and name != "repo":
            problems.append(f"нет версии сервиса {name!r}")
        pair = groups[name]
        for mode in MODES:
            if mode not in pair:
                problems.append(f"{name}: нет прогона {mode}")
                continue
            row = pair[mode]
            if not str(row.get("command") or "").strip():
                problems.append(f"{name}:{mode}: команда не записана")
            if int(row.get("tests_ran") or 0) <= 0:
                problems.append(f"{name}:{mode}: нулевой прогон (0 тестов) — это не проверка")
            if row.get("result") in ("timeout", "error"):
                problems.append(f"{name}:{mode}: прогон не состоялся ({row.get('result')})")
        if all(m in pair for m in MODES):
            diff = diff_results(pair["baseline"], pair["candidate"])
            if pair["candidate"].get("result") != "pass":
                problems.append(f"{name}: candidate не прошёл")
            if diff.get("new_failures"):
                problems.append(f"{name}: новые падения: " + ", ".join(diff["new_failures"]))
    return {"ok": not problems, "problems": problems, "report": data}


def check_zero(report: dict[str, Any]) -> dict[str, Any]:
    """R-QA-2: нулевой прогон в любом сервисе/режиме — не проверка."""
    rows = [(key, row) for key, row in (report.get("results") or {}).items()
            if isinstance(row, dict)]
    if not rows:
        return {"ok": False, "rule": "R-QA-2", "reason": "прогонов нет"}
    for key, row in rows:
        if int(row.get("tests_ran") or 0) <= 0:
            return {"ok": False, "rule": "R-QA-2", "reason": f"{key}: 0 тестов"}
    return {"ok": True, "rule": "R-QA-2"}


CONTROL_POINTS = {
    "verification.check_report": check_report,
    "verification.check_zero": check_zero,
}
