"""OTLP/HTTP JSON для метрик harness.

Это настоящий OTLP JSON (resourceMetrics → scopeMetrics → gauge), а не «свой
формат с названием OpenTelemetry». Отправка — HTTP POST; protobuf/gRPC не
реализованы сознательно: без SDK получился бы второй клиент, который сложнее
проверить, чем сам экспорт.

Агентские трейсы экспортирует DSH; здесь только процессные метрики harness,
чтобы коллектор видел очередь, rework и состояние проверок.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
from typing import Any

from . import metrics


def _string(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


def _metric(name: str, value: float, *, integer: bool = True, unit: str = "") -> dict[str, Any]:
    point: dict[str, Any] = {"timeUnixNano": str(time.time_ns())}
    if integer:
        point["asInt"] = str(int(value))
    else:
        point["asDouble"] = float(value)
    metric: dict[str, Any] = {
        "name": name,
        "description": "",
        "unit": unit,
        "gauge": {"dataPoints": [point]},
    }
    return metric


def _git_head(root: str) -> str:
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def build(data: dict[str, Any], *, service_name: str = "harness", service_version: str = "",
          git_sha: str | None = None, host: str | None = None) -> dict[str, Any]:
    summary = data["summary"]
    attempts = data["attempts"]
    verification = data["verification"]
    version = service_version or "0.1.0"
    attributes = [
        _string("service.name", service_name),
        _string("service.version", version),
        _string("host.name", host or socket.gethostname()),
    ]
    sha = git_sha if git_sha is not None else _git_head(data.get("root") or ".")
    if sha:
        attributes.append(_string("vcs.revision", sha))
    values = [
        ("harness.changes", summary["changes"], True, ""),
        ("harness.changes.accepted", summary["changes_accepted"], True, ""),
        ("harness.changes.archived", summary["changes_archived"], True, ""),
        ("harness.tasks", summary["tasks"], True, ""),
        ("harness.tasks.active", summary["tasks_active"], True, ""),
        ("harness.tasks.escalated", summary["tasks_escalated"], True, ""),
        ("harness.review.records", summary["review_records"], True, ""),
        ("harness.evidence.manifests", summary["evidence_manifests"], True, ""),
        ("harness.findings.open", summary["open_findings"], True, ""),
        ("harness.rework.rate", attempts["rework_rate"], False, "1"),
        ("harness.verification.reports", verification["reports"], True, ""),
        ("harness.verification.ok", verification["ok"], True, ""),
        ("harness.cost.total", summary["cost_total"], False, ""),
    ]
    return {
        "resourceMetrics": [{
            "resource": {"attributes": attributes},
            "scopeMetrics": [{
                "scope": {"name": "harness", "version": version},
                "metrics": [_metric(name, value, integer=integer, unit=unit)
                            for name, value, integer, unit in values],
            }],
        }],
    }


def send(payload: dict[str, Any], endpoint: str, *, timeout: int = 10) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return {"status": response.status, "endpoint": endpoint, "bytes": len(body)}


def write(payload: dict[str, Any], path: str) -> str:
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path
