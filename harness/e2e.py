"""Multi-service e2e: интеграционный прогон по копиям сервисов.

Каждый сервис копируется во временный каталог (рабочее дерево не меняется),
команда получает `HARNESS_E2E_<NAME>_DIR` и `HARNESS_E2E_<NAME>_SHA`. Отчёт
пишется в evidence с кодом выхода, длительностью и хвостом вывода.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from typing import Any

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


def _dirty(path: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", path, "status", "--porcelain"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    return proc.returncode != 0 or bool(proc.stdout.strip())


def _copy_commit(path: str, sha: str, dest: str) -> None:
    """Копия ровно коммита: рабочее дерево может быть грязным, evidence — нет."""
    proc = subprocess.run(
        ["git", "-C", path, "archive", "--format=tar", sha],
        capture_output=True, timeout=120,
    )
    if proc.returncode != 0:
        raise E2EError("R-E2E-1", f"git archive {sha[:12]} не удался: "
                       f"{proc.stderr.decode('utf-8', 'replace').strip()[:200]}")
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as archive:
        archive.extractall(dest)


def parse_services(items: list[str]) -> list[dict[str, str]]:
    services: list[dict[str, str]] = []
    for item in items:
        if "=" not in item:
            raise E2EError("R-E2E-1", f"сервис должен быть name=PATH[@SHA]: {item!r}")
        name, spec = item.split("=", 1)
        name = name.strip()
        if not NAME_RE.fullmatch(name):
            raise E2EError("R-E2E-1", f"недопустимое имя сервиса: {name!r}")
        if any(s["name"] == name for s in services):
            raise E2EError("R-E2E-1", f"дубликат имени сервиса: {name!r}")
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
            sha = service.get("sha") or _service_sha(path, "")
            dirty = False
            try:
                if service.get("sha"):
                    _copy_commit(path, sha, copy_to)
                else:
                    dirty = _dirty(path)
                    shutil.copytree(path, copy_to,
                                    ignore=shutil.ignore_patterns(".git", ".harness", "node_modules"))
            except OSError as exc:
                raise E2EError("R-E2E-1", f"копирование сервиса {name!r} не удалось: {exc}") from exc
            prepared.append({"name": name, "source": path, "dir": copy_to, "sha": sha,
                             "dirty": dirty})
            env[f"HARNESS_E2E_{name.upper()}_DIR"] = copy_to
            env[f"HARNESS_E2E_{name.upper()}_SHA"] = sha
        started = time.monotonic()
        proc = subprocess.Popen(
            command, shell=True, cwd=root, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            exit_code, timed_out = proc.returncode, False
        except subprocess.TimeoutExpired:
            exit_code, timed_out = -1, True
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=30)
            else:
                proc.kill()
            try:
                stdout, stderr = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
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
        "services": [{"name": s["name"], "sha": s["sha"], "dirty": s["dirty"]}
                     for s in prepared],
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
