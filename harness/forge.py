"""Forge: чтение статуса PR и проверок.

Слой не создаёт PR (это делает `lane pr`), а отвечает на вопрос «можно ли
принимать»: какие checks упали, какие ещё идут, есть ли конфликт. Без `gh`
операция отказывает, а не возвращает пустой «ок».
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from typing import Any


class ForgeError(RuntimeError):
    def __init__(self, rule: str, message: str, **details: Any):
        super().__init__(message)
        self.rule = rule
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "rule": self.rule, "reason": str(self), "details": self.details}


FAIL_CONCLUSIONS = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}
PENDING_STATUSES = {"QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED"}


def require_gh() -> None:
    if shutil.which("gh") is None:
        raise ForgeError("R-FORGE-1", "gh не найден: статус PR не прочитать")


def summarize_checks(pr: dict[str, Any]) -> dict[str, Any]:
    """Сводит statusCheckRollup к failing/pending/ok. Чистая функция для тестов."""
    checks = list(pr.get("statusCheckRollup") or [])
    failing: list[str] = []
    pending: list[str] = []
    for check in checks:
        name = str(check.get("name") or check.get("context") or check.get("__typename") or "check")
        status = str(check.get("status") or "").upper()
        conclusion = str(check.get("conclusion") or "").upper()
        if conclusion in FAIL_CONCLUSIONS:
            failing.append(name)
        elif status in PENDING_STATUSES or (status and status != "COMPLETED" and not conclusion):
            pending.append(name)
    return {
        "failing": failing,
        "pending": pending,
        "total": len(checks),
        "ok": not failing and not pending,
        "state": str(pr.get("state") or ""),
        "mergeable": str(pr.get("mergeable") or ""),
        "review_decision": str(pr.get("reviewDecision") or ""),
        "number": pr.get("number"),
        "url": str(pr.get("url") or ""),
    }


def _gh_json(root: str, args: list[str]) -> Any:
    require_gh()
    proc = subprocess.run(
        ["gh", *args], cwd=root, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    if proc.returncode != 0:
        raise ForgeError("R-FORGE-1", f"gh {' '.join(args)} не удался: {proc.stderr.strip()[:300]}")
    try:
        return json.loads(proc.stdout or "null")
    except ValueError as exc:
        raise ForgeError("R-FORGE-1", f"gh вернул не JSON: {exc}") from exc


def pr_status(root: str, branch: str) -> dict[str, Any]:
    """Статус PR для ветки; отсутствие PR — отказ."""
    data = _gh_json(root, ["pr", "list", "--head", branch, "--state", "all",
                           "--json", "number,url,state,statusCheckRollup,mergeable,reviewDecision",
                           "--limit", "1"])
    if not isinstance(data, list) or not data:
        raise ForgeError("R-FORGE-1", f"PR для ветки {branch!r} не найден")
    summary = summarize_checks(data[0])
    summary["branch"] = branch
    return summary


def wait_status(root: str, branch: str, *, timeout: float = 300.0, interval: float = 10.0,
                sleep=time.sleep) -> dict[str, Any]:
    """Ждёт завершения checks; таймаут — отказ, а не бесконечное ожидание."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while True:
        last = pr_status(root, branch)
        if last["ok"] or last["failing"]:
            return last
        if time.monotonic() >= deadline:
            raise ForgeError("R-FORGE-1", f"checks не завершились за {timeout:.0f}с",
                             pending=last["pending"], url=last["url"])
        sleep(interval)


CONTROL_POINTS = {"forge.status": pr_status, "forge.wait": wait_status}
