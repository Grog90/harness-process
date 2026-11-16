"""CI-снимок: selftest, покрытие правил, метрики и (опционально) unittest.

Один отчёт, который можно положить в evidence и отдать в CI. Он не заменяет
гейты изменения: change закрывается через release/accept/archive, а этот отчёт
отвечает на вопрос «здоров ли сам процесс и код harness».
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any

from . import doctor, metrics, rules, selftest
from .state import Store


def _git_head(root: str) -> str:
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def run_tests(root: str, timeout: int) -> dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
            cwd=root, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "duration_s": round(time.time() - started, 2),
            "tail": output[-2000:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit_code": -1, "duration_s": round(time.time() - started, 2),
                "tail": "timeout"}


def collect(root: str, *, run_tests: bool = False, timeout: int = 900) -> dict[str, Any]:
    root = os.path.abspath(root)
    selftest_report = selftest.run_all(root)
    registry = rules.load_registry(root, None)
    statuses = rules.evaluate(registry, installed=doctor.installed_packages())
    tested = {c.rule for c in selftest.CHECKS}
    sums = rules.summary(statuses, tested)

    store = Store(os.path.join(root, ".harness", "state.db"))
    try:
        metrics_data = metrics.collect(store, root)
    finally:
        store.close()

    tests = run_tests(root, timeout) if run_tests else None
    ok = bool(selftest_report["ok"]) and sums["broken"] == 0 and (tests is None or tests["ok"])
    return {
        "generated_at": metrics._iso(),
        "root": root,
        "git_head": _git_head(root),
        "python": sys.version.split()[0],
        "ok": ok,
        "selftest": {
            "total": selftest_report["total"],
            "passed": selftest_report["passed"],
            "failed": selftest_report["failed"],
            "ok": selftest_report["ok"],
        },
        "coverage": sums,
        "tests": tests,
        "metrics_summary": metrics_data["summary"],
        "verification": metrics_data["verification"],
        "queue": metrics_data["queue"],
    }


def write_report(root: str, report: dict[str, Any], path: str | None = None) -> str:
    if not path:
        stamp = time.strftime("%Y%m%dT%H%M%S")
        path = os.path.join(root, "evidence", f"ci-{stamp}", "report.json")
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path
