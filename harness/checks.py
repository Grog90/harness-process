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

IGNORE = shutil.ignore_patterns(
    ".git", ".harness", "evidence", "node_modules", "__pycache__", "*.pyc", "dist", "build", ".pytest_cache"
)

SAFE_ENV = {
    "LANG": "C.UTF-8",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONDONTWRITEBYTECODE": "1",
}


def _env() -> dict[str, str]:
    env = dict(SAFE_ENV)
    env["PATH"] = os.environ.get("PATH", "")
    env["HOME"] = os.environ.get("HOME", tempfile.gettempdir())
    env["TMPDIR"] = os.environ.get("TMPDIR", tempfile.gettempdir())
    return env


class CheckError(RuntimeError):
    pass


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
) -> dict[str, Any]:
    root = os.path.abspath(root)
    cmds = collect_commands(brief_data, commands)
    if not cmds:
        raise CheckError("в брифе нет команд проверки: критерии нечем подтвердить")

    workdir = tempfile.mkdtemp(prefix=f"harness-checks-{task_id}-")
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
            proc = subprocess.run(
                command,
                shell=True,
                cwd=tree,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=_env(),
            )
            entry.update(exit_code=proc.returncode, timed_out=False)
            stdout, stderr = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            entry.update(exit_code=None, timed_out=True)
            stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            stderr = f"превышен лимит {timeout}с"
        entry["duration_s"] = round(time.time() - started, 3)
        entry["ok"] = entry.get("exit_code") == 0

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
    lines = [f"проверки для {report['task_id']}: команд {len(report['results'])}"]
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
