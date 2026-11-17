"""Multi-service e2e: интеграционный прогон по копиям сервисов.

Каждый сервис копируется во временный каталог (рабочее дерево не меняется),
команда получает `HARNESS_E2E_<NAME>_DIR` и `HARNESS_E2E_<NAME>_SHA`. Отчёт
пишется в evidence с кодом выхода, длительностью и хвостом вывода.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from typing import Any


class E2EError(RuntimeError):
    def __init__(self, rule: str, message: str, **details: Any):
        super().__init__(message)
        self.rule = rule
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "rule": self.rule, "reason": str(self), "details": self.details}


def _service_sha(path: str, fallback: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", path, "rev-parse", "HEAD"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return fallback
    return proc.stdout.strip() if proc.returncode == 0 else fallback


def parse_services(items: list[str]) -> list[dict[str, str]]:
    services: list[dict[str, str]] = []
    for item in items:
        if "=" not in item:
            raise E2EError("R-E2E-1", f"сервис должен быть name=PATH[@SHA]: {item!r}")
        name, spec = item.split("=", 1)
        name = name.strip()
        if not name or "/" in name or "\\" in name:
            raise E2EError("R-E2E-1", f"недопустимое имя сервиса: {name!r}")
        path, sha = (spec.rsplit("@", 1) if "@" in spec else (spec, ""))
        services.append({"name": name, "path": path.strip(), "sha": sha.strip()})
    if not services:
        raise E2EError("R-E2E-1", "нужен хотя бы один --service")
    return services


def run(root: str, change_id: str, command: str, services: list[dict[str, str]],
        *, timeout: int = 600) -> dict[str, Any]:
    root = os.path.abspath(root)
    if not command.strip():
        raise E2EError("R-E2E-1", "пустая e2e-команда")
    if not change_id or "/" in change_id or "\\" in change_id or ".." in change_id:
        raise E2EError("R-E2E-1", f"недопустимый change id: {change_id!r}")
    prepared: list[dict[str, str]] = []
    env = dict(os.environ)
    tmpdirs: list[str] = []
    try:
        for service in services:
            name = service["name"]
            path = service["path"]
            if not os.path.isabs(path):
                path = os.path.join(root, path)
            if not os.path.isdir(path):
                raise E2EError("R-E2E-1", f"сервис {name!r} не найден: {path}")
            work = tempfile.mkdtemp(prefix=f"harness-e2e-{name}-")
            tmpdirs.append(work)
            copy_to = os.path.join(work, name)
            shutil.copytree(path, copy_to, ignore=shutil.ignore_patterns(".git", ".harness", "node_modules"))
            sha = service.get("sha") or _service_sha(path, "")
            prepared.append({"name": name, "source": path, "dir": copy_to, "sha": sha})
            env[f"HARNESS_E2E_{name.upper()}_DIR"] = copy_to
            env[f"HARNESS_E2E_{name.upper()}_SHA"] = sha
        started = time.monotonic()
        try:
            proc = subprocess.run(
                command, shell=True, cwd=root, env=env, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=timeout,
            )
            exit_code, timed_out = proc.returncode, False
            stdout, stderr = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            exit_code, timed_out = -1, True
            stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        duration = round(time.monotonic() - started, 3)
    finally:
        for work in tmpdirs:
            shutil.rmtree(work, ignore_errors=True)

    report = {
        "ok": exit_code == 0 and not timed_out,
        "change": change_id,
        "command": command,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_seconds": duration,
        "services": [{"name": s["name"], "sha": s["sha"]} for s in prepared],
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path = os.path.join(root, "evidence", f"e2e-{change_id}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    report["report"] = path
    return report


CONTROL_POINTS = {"e2e.run": run}
