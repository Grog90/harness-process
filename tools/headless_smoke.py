"""Smoke-прогон dsh headless: доказательство, что CI-точка запуска жива.

Это не замена harness ci: скрипт отвечает на вопрос «запускается ли headless-
профиль и доходит ли до final-события», а не «здоров ли процесс». Отчёт пишется
в evidence/headless-<ts>/report.json; exit 2, если final пришёл с ошибкой
(например, нет DEEPSEEK_API_KEY в окружении headless-профиля).

Пример:
    python tools/headless_smoke.py --task "Ответь одним словом: работает"
    python tools/headless_smoke.py --out evidence/headless/report.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_dsh() -> str:
    env = os.environ.get("DSH_BIN")
    if env and os.path.exists(env):
        return env
    found = shutil.which("dsh") or shutil.which("dsh.cmd")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    candidate = os.path.join(
        local, "Programs", "DeepSeek Harness", "resources", "runtime", "cli", "bin", "dsh.cmd"
    )
    if os.path.exists(candidate):
        return candidate
    raise FileNotFoundError("dsh не найден: задай DSH_BIN или добавь dsh в PATH")


def parse_events(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def run(task: str, timeout: int) -> dict[str, Any]:
    dsh = find_dsh()
    command = [dsh, "headless", "--json", "--permission-mode", "read-only",
               "--approval-policy", "never", task]
    started = time.time()
    try:
        proc = subprocess.run(
            subprocess.list2cmdline(command), shell=True, cwd=ROOT,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        stdout, stderr, code = proc.stdout or "", proc.stderr or "", proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        code, timed_out = -1, True
    events = parse_events(stdout)
    final = next((e for e in reversed(events) if e.get("type") == "final"), None)
    reason = (final or {}).get("reason") or {}
    kind = reason.get("kind") if isinstance(reason, dict) else None
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": command,
        "dsh": dsh,
        "duration_s": round(time.time() - started, 2),
        "exit_code": code,
        "timed_out": timed_out,
        "events": len(events),
        "session_id": (final or {}).get("sessionId") or next(
            (e.get("sessionId") for e in events if e.get("type") == "session"), None),
        "final_reason": reason,
        "final_text": (final or {}).get("text", ""),
        "ok": kind == "end_turn" or (kind is None and code == 0 and not timed_out),
        "stderr_tail": stderr[-2000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-прогон dsh headless")
    parser.add_argument("--task", default="Ответь одним словом: работает")
    parser.add_argument("--out", default=None)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    report = run(args.task, args.timeout)
    path = args.out or os.path.join(
        ROOT, "evidence", f"headless-{time.strftime('%Y%m%dT%H%M%S')}", "report.json"
    )
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"headless: {'OK' if report['ok'] else 'FAIL'} · events {report['events']} · "
          f"session {report['session_id'] or '—'} · {os.path.relpath(path, ROOT)}")
    if not report["ok"]:
        reason = report["final_reason"]
        message = reason.get("error", {}).get("message") if isinstance(reason, dict) else ""
        print(f"  причина: {message or report['stderr_tail'][-300:]}", file=sys.stderr)
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
