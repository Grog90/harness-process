"""Изолированный прогон проверок из брифа.

Верификатору нужны временные файлы, сборка и тестовая БД, но менять принятый снимок
он не должен. Поэтому проверки выполняются в копии дерева: команды из брифа бегут в
одноразовом каталоге с урезанным окружением и ограничением по времени, а наружу
выходят только артефакты — вывод команд и готовые результаты критериев.

Это не ОС-песочница: сеть не изолируется, права процесса не ограничиваются.
Но продукт копией не портится, а «мои тесты прошли» превращается в записанный артефакт.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from typing import Any

from . import tmpdir

IGNORE = shutil.ignore_patterns(
    ".git", ".harness", "evidence", "node_modules", "__pycache__", "*.pyc", "dist", "build", ".pytest_cache"
)

SAFE_ENV = {
    "LANG": "C.UTF-8",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONDONTWRITEBYTECODE": "1",
}


LANE_ENV_KEYS = (
    ("HARNESS_LANE_INDEX", "slot"),
    ("HARNESS_LANE_PORT_BASE", "port_base"),
    ("HARNESS_TEST_DB", "test_db"),
    ("HARNESS_LANE_TMP", "tmp"),
)


def lane_env(lane_resources: dict[str, Any] | None) -> dict[str, str]:
    """Переменные ресурсов лейна. Нет лейна — нет переменных: поведение прежнее."""
    if not lane_resources:
        return {}
    return {
        key: str(lane_resources[field])
        for key, field in LANE_ENV_KEYS
        if lane_resources.get(field) is not None
    }


def _env(lane_resources: dict[str, Any] | None = None) -> dict[str, str]:
    env = dict(SAFE_ENV)
    env["PATH"] = os.environ.get("PATH", "")
    env["HOME"] = os.environ.get("HOME", tempfile.gettempdir())
    env["TMPDIR"] = os.environ.get("TMPDIR", tempfile.gettempdir())
    env.update(lane_env(lane_resources))  # поверх белого списка, а не вместо него
    return env


SANDBOX_IMAGE = os.environ.get("HARNESS_SANDBOX_IMAGE", "python:3.12-slim")


def detect_sandbox() -> dict[str, Any]:
    """Что из изоляции доступно в этой среде.

    Контейнер — не «безопасность по умолчанию»: он есть далеко не везде, а без него
    остаётся копия дерева и урезанное окружение. Честнее показать это, чем
    называть прогон изолированным.
    """
    docker = shutil.which("docker")
    available = False
    note = "docker не найден: остаётся копия дерева и урезанное окружение"
    if docker:
        try:
            probe = subprocess.run(
                [docker, "info", "--format", "{{.ServerVersion}}"],
                capture_output=True, text=True, timeout=10,
            )
            available = probe.returncode == 0
            note = (
                f"docker доступен ({probe.stdout.strip()}), прогон без сети"
                if available
                else "docker установлен, но демон недоступен"
            )
        except (OSError, subprocess.TimeoutExpired):
            note = "docker не отвечает"
    return {
        "platform": os.name,
        "docker": bool(docker),
        "docker_ready": available,
        "image": SANDBOX_IMAGE,
        "mode": "docker" if available else "copy-only",
        "note": note,
    }


class CheckError(RuntimeError):
    pass


def _execute(command: str, tree: str, timeout: int, use_docker: bool,
             isolation: dict[str, Any],
             lane_resources: dict[str, Any] | None = None) -> subprocess.CompletedProcess:
    """Запускает команду в контейнере без сети либо в копии дерева."""
    extra = lane_env(lane_resources)
    if use_docker:
        docker = shutil.which("docker") or "docker"
        forwarded: list[str] = []
        for key, value in extra.items():
            forwarded += ["-e", f"{key}={value}"]  # контейнер не увидит их иначе
        return subprocess.run(
            [
                docker, "run", "--rm", "--network", "none",
                "-v", f"{tree}:/work", "-w", "/work",
                "-e", "PYTHONUNBUFFERED=1",
                *forwarded,
                isolation["image"], "sh", "-lc", command,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    return subprocess.run(
        command,
        shell=True,
        cwd=tree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=_env(lane_resources),
    )


def collect_commands(brief_data: dict[str, Any], override: list[str] | None = None) -> list[str]:
    if override:
        return list(override)
    acceptance = brief_data.get("acceptance") or {}
    commands: list[str] = [str(c) for c in acceptance.get("check_commands", []) or []]
    for criterion in acceptance.get("criteria", []) or []:
        check = str(criterion.get("check") or "").strip()
        if check and check not in commands and _looks_like_command(check):
            commands.append(check)
    return commands


def _looks_like_command(text: str) -> bool:
    """Отличает команду от метки вроде «call» или «вручную»."""
    return any(sep in text for sep in (" ", "/", "-")) or text.startswith("python")


def run(
    root: str,
    brief_data: dict[str, Any],
    *,
    task_id: str = "checks",
    commands: list[str] | None = None,
    timeout: int = 300,
    evidence_dir: str | None = None,
    keep_workdir: bool = False,
    sandbox: str = "auto",
    lane_resources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = os.path.abspath(root)
    cmds = collect_commands(brief_data, commands)
    if not cmds:
        raise CheckError("в брифе нет команд проверки: критерии нечем подтвердить")

    isolation = detect_sandbox()
    use_docker = sandbox == "docker" or (sandbox == "auto" and isolation["docker_ready"])
    if sandbox == "docker" and not isolation["docker_ready"]:
        raise CheckError(f"изоляция через контейнер запрошена, но недоступна: {isolation['note']}")
    if not use_docker:
        isolation["mode"] = "copy-only"

    workdir = tmpdir.scratch(root, f"harness-checks-{task_id}-")
    tree = os.path.join(workdir, "tree")
    shutil.copytree(root, tree, ignore=IGNORE, symlinks=False)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    evidence_root = evidence_dir or os.path.join(root, "evidence")
    art_root = os.path.join(evidence_root, f"checks-{task_id}-{stamp}")
    os.makedirs(art_root, exist_ok=True)

    results: list[dict[str, Any]] = []
    for index, command in enumerate(cmds, 1):
        started = time.time()
        entry: dict[str, Any] = {"index": index, "command": command}
        try:
            proc = _execute(command, tree, timeout, use_docker, isolation, lane_resources)
            entry.update(exit_code=proc.returncode, timed_out=False)
            stdout, stderr = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            entry.update(exit_code=None, timed_out=True)
            stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            stderr = f"превышен лимит {timeout}с"
        entry["duration_s"] = round(time.time() - started, 3)
        entry["ok"] = entry.get("exit_code") == 0
        entry["isolation"] = isolation["mode"]

        art_dir = os.path.join(art_root, f"{index:02d}")
        os.makedirs(art_dir, exist_ok=True)
        with open(os.path.join(art_dir, "stdout.txt"), "w", encoding="utf-8") as fh:
            fh.write(stdout or "")
        with open(os.path.join(art_dir, "stderr.txt"), "w", encoding="utf-8") as fh:
            fh.write(stderr or "")
        entry["artifact_dir"] = os.path.relpath(art_dir, root).replace("\\", "/")
        entry["stdout_tail"] = (stdout or "")[-800:]
        results.append(entry)

    if not keep_workdir:
        shutil.rmtree(workdir, ignore_errors=True)

    return {
        "task_id": task_id,
        "root": root,
        "commands": cmds,
        "results": results,
        "all_ok": all(r["ok"] for r in results),
        "any_timeout": any(r["timed_out"] for r in results),
        "isolation": isolation,
        "lane": lane_env(lane_resources),
        "workdir": workdir if keep_workdir else None,
        "artifacts": art_root,
    }


def build_verdict(
    brief_data: dict[str, Any],
    report: dict[str, Any],
    *,
    task_id: str,
    attempt: int,
    snapshot_id: str,
    reviewer_id: str = "checks-runner",
) -> dict[str, Any]:
    """Собирает вердикт из фактических результатов, а не из пересказа.

    Критерий без выполненной команды получает skip — и тогда вердикт не PASS,
    а INCONCLUSIVE: «не проверено» не должно выглядеть как «проверено».
    """
    by_command = {_norm(r["command"]): r for r in report["results"]}
    criteria: list[dict[str, Any]] = []
    for criterion in (brief_data.get("acceptance") or {}).get("criteria", []) or []:
        cid = str(criterion.get("id"))
        check = _norm(str(criterion.get("check") or ""))
        if not check:
            criteria.append({"id": cid, "result": "skip", "evidence": "у критерия нет команды проверки"})
            continue
        hit = by_command.get(check) or _prefix_hit(check, by_command)
        if hit is None:
            criteria.append(
                {"id": cid, "result": "skip", "evidence": f"команда {check!r} не запускалась"}
            )
            continue
        ok = bool(hit["ok"])
        criteria.append(
            {
                "id": cid,
                "result": "pass" if ok else "fail",
                "evidence": f"{hit['command']} -> код {hit['exit_code']} ({hit['duration_s']}с), "
                f"артефакт {hit['artifact_dir']}",
                "command": hit["command"],
            }
        )

    failed = [c for c in criteria if c["result"] == "fail"]
    skipped = [c for c in criteria if c["result"] == "skip"]
    if failed:
        verdict = "FAIL"
    elif skipped:
        verdict = "INCONCLUSIVE"
    else:
        verdict = "PASS"

    findings = [
        {
            "finding_id": f"F-{c['id']}",
            "severity": "major",
            "statement": f"критерий {c['id']} не выполнен",
            "evidence": c["evidence"],
            "owner": "worker",
            "track": "own_diff",
        }
        for c in failed
    ]

    return {
        "verdict_id": f"v-checks-{task_id}-{attempt}",
        "task_id": task_id,
        "attempt": attempt,
        "snapshot_id": snapshot_id,
        "verdict": verdict,
        "reviewer_id": reviewer_id,
        "reviewer_role": "verifier",
        "policy_version": "1.0.0",
        "criteria": criteria,
        "findings": findings,
        "evidence_report": os.path.relpath(report["artifacts"], report["root"]).replace("\\", "/"),
    }


def _norm(text: str) -> str:
    return " ".join(text.strip().split())


def _prefix_hit(check: str, by_command: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for key, value in by_command.items():
        if key.startswith(check) or check.startswith(key):
            return value
    return None


def render_text(report: dict[str, Any]) -> str:
    iso = report.get("isolation") or {}
    lines = [
        f"проверки для {report['task_id']}: команд {len(report['results'])}",
        f"изоляция: {iso.get('mode', '?')} — {iso.get('note', '')}",
    ]
    for r in report["results"]:
        mark = "ok  " if r["ok"] else ("TIME" if r.get("timed_out") else "FAIL")
        lines.append(f"  [{mark}] {r['command']}  ({r.get('duration_s')}с, код {r.get('exit_code')})")
        if not r["ok"] and r.get("stderr"):
            lines.append(f"         {r['stderr'].strip().splitlines()[-1][:160]}")
    lines.append(f"артефакты: {report['artifacts']}")
    return "\n".join(lines)

# Точки контроля, которые реально существуют в коде (для отчёта о покрытии матрицы).
CONTROL_POINTS = {
    "checks.run": run,
    "checks.build_verdict": build_verdict,
}

# Точки контроля, которые реально существуют в коде (для отчёта о покрытии матрицы).
checks_sandbox = detect_sandbox  # отдельная ссылка, чтобы отчёт видел механизм

CONTROL_POINTS["checks.sandbox"] = detect_sandbox
