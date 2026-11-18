"""Метрики процесса и доска состояния.

Метрики считаются из того же SQLite и файлов change, что и гейты: отдельного
«отчётного» хранилища нет. Поэтому цифры нельзя улучшить, не изменив процесс.

Доска — самодостаточный HTML: состояния change, review/trace/verification,
очередь, риски и пакеты evidence. Это снимок на момент генерации, а не live-UI:
обновляется командой `harness board`.
"""

from __future__ import annotations

import html
import http.server
import json
import os
import time
from typing import Any

from . import changefsm, verification
from .state import Store

QUEUE_STATES = ("READY", "IN_PROGRESS", "BLOCKED")
TASK_ACTIVE = ("RUNNING", "VERIFYING", "VERIFY_BLOCKED", "REWORK_READY")


def _iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts if ts is not None else time.time()))


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _change_events(store: Store, change_id: str) -> list[dict[str, Any]]:
    try:
        return store.change_events_for(change_id)
    except Exception:  # noqa: BLE001 - метрика не должна ронять отчёт
        return []


def _lead_time(events: list[dict[str, Any]]) -> dict[str, float | None]:
    by_type: dict[str, float] = {}
    for event in events:
        by_type.setdefault(str(event.get("type")), float(event.get("created_at") or 0))
    ready = by_type.get("ready")
    accept = by_type.get("accept")
    archive = by_type.get("archive")
    return {
        "ready_to_accept_hours": round((accept - ready) / 3600, 2) if ready and accept else None,
        "ready_to_archive_hours": round((archive - ready) / 3600, 2) if ready and archive else None,
    }


def _change_files(root: str, change_id: str) -> dict[str, Any]:
    base = changefsm.change_dir(root, change_id)
    names = ("verification.json", "release.json", "acceptance.json", "closing.json")
    return {name: os.path.isfile(os.path.join(base, name)) for name in names}


def collect(store: Store, root: str) -> dict[str, Any]:
    root = os.path.abspath(root)
    changes = store.list_changes()
    tasks = store.list_tasks()
    change_rows: list[dict[str, Any]] = []
    queue: dict[str, list[str]] = {state: [] for state in QUEUE_STATES}
    lead_times: list[float] = []
    review_records = 0
    review_latencies: list[float] = []
    verification_rows: list[dict[str, Any]] = []

    for change in changes:
        cid = str(change["change_id"])
        events = _change_events(store, cid)
        lead = _lead_time(events)
        if lead["ready_to_accept_hours"] is not None:
            lead_times.append(float(lead["ready_to_accept_hours"]))
        reviews = store.change_reviews(cid)
        review_records += len(reviews)
        created = float(change.get("created_at") or 0)
        for review in reviews:
            review_latencies.append(max(0.0, (float(review.get("created_at") or created) - created) / 3600))
        files = _change_files(root, cid)
        report = verification.load(root, cid)
        check = verification.check_report(store, root, cid, report=report) if report else None
        verification_rows.append({
            "change_id": cid,
            "present": report is not None,
            "ok": bool(check and check["ok"]),
            "zero": bool(report and any(
                int((row or {}).get("tests_ran") or 0) <= 0
                for row in (report.get("results") or {}).values()
                if isinstance(row, dict)
            )),
        })
        if change["status"] in queue:
            queue[change["status"]].append(cid)
        change_rows.append({
            "change_id": cid,
            "status": change["status"],
            "revision": int(change["revision"]),
            "risk_class": change["risk_class"],
            "owner": change["owner"],
            "spec_ref": change.get("spec_ref"),
            "created_at": created,
            "updated_at": float(change.get("updated_at") or 0),
            "reviews": len(reviews),
            "review_roles": sorted({str(r.get("reviewer_role")) for r in reviews}),
            "files": files,
            "verification": next(r for r in verification_rows if r["change_id"] == cid),
            "lead": lead,
        })

    task_states: dict[str, int] = {}
    verdict_counts = {"pass": 0, "fail": 0, "inconclusive": 0, "none": 0}
    attempts_total = 0
    rework = 0
    escalated = 0
    task_rows: list[dict[str, Any]] = []
    open_findings = 0
    unowned_findings = 0
    for task in tasks:
        state = str(task["state"])
        task_states[state] = task_states.get(state, 0) + 1
        attempts = store.attempts_for(task["task_id"])
        attempts_total += len(attempts)
        if len(attempts) > 1:
            rework += 1
        if state == "ESCALATED":
            escalated += 1
        verdict = store.latest_verdict(task["task_id"])
        if not verdict:
            verdict_counts["none"] += 1
        else:
            key = str(verdict.get("verdict") or "").lower()
            verdict_counts[key if key in verdict_counts else "inconclusive"] += 1
        findings = store.findings_for(task["task_id"])
        for finding in findings:
            if finding.get("status") != "closed":
                open_findings += 1
                if not finding.get("owner"):
                    unowned_findings += 1
        task_rows.append({
            "task_id": task["task_id"],
            "change_id": task.get("change_id"),
            "state": state,
            "attempts": len(attempts),
            "verdict": (verdict or {}).get("verdict"),
            "open_findings": sum(1 for f in findings if f.get("status") != "closed"),
        })

    evidence_count = 0
    evidence_dir = os.path.join(root, "evidence")
    if os.path.isdir(evidence_dir):
        for name in os.listdir(evidence_dir):
            if name.startswith("change-") and os.path.isfile(os.path.join(evidence_dir, name, "manifest.json")):
                evidence_count += 1

    cost_total = 0.0
    try:
        cost_total = round(sum(float(row.get("total") or 0) for row in store.cost_report()), 2)
    except Exception:  # noqa: BLE001
        cost_total = 0.0

    accepted = [row for row in change_rows if row["status"] in ("ACCEPTED", "ARCHIVED")]
    archived = [row for row in change_rows if row["status"] == "ARCHIVED"]
    return {
        "generated_at": _iso(),
        "root": root,
        "summary": {
            "changes": len(changes),
            "changes_archived": len(archived),
            "changes_accepted": len(accepted),
            "tasks": len(tasks),
            "tasks_active": sum(1 for t in tasks if t["state"] in TASK_ACTIVE),
            "tasks_escalated": escalated,
            "review_records": review_records,
            "evidence_manifests": evidence_count,
            "open_findings": open_findings,
            "unowned_findings": unowned_findings,
            "cost_total": cost_total,
        },
        "changes": change_rows,
        "tasks": task_rows,
        "task_states": task_states,
        "verdicts": verdict_counts,
        "attempts": {
            "total": attempts_total,
            "rework_tasks": rework,
            "rework_rate": round(rework / len(tasks), 3) if tasks else 0.0,
        },
        "lead_time": {
            "avg_ready_to_accept_hours": _avg(lead_times),
            "samples": len(lead_times),
            "avg_review_hours": _avg(review_latencies),
        },
        "verification": {
            "reports": sum(1 for r in verification_rows if r["present"]),
            "ok": sum(1 for r in verification_rows if r["ok"]),
            "zero_or_failed": sum(1 for r in verification_rows if r["present"] and (not r["ok"] or r["zero"])),
        },
        "queue": queue,
    }


def render_md(data: dict[str, Any]) -> str:
    summary = data["summary"]
    lines = [
        f"# Состояние процесса — {data['generated_at']}",
        "",
        f"- change: {summary['changes']} (принято {summary['changes_accepted']}, архив {summary['changes_archived']})",
        f"- задачи: {summary['tasks']}, активных {summary['tasks_active']}, эскалировано {summary['tasks_escalated']}",
        f"- review-записей: {summary['review_records']}; evidence-манифестов: {summary['evidence_manifests']}",
        f"- открытых находок: {summary['open_findings']}, без владельца: {summary['unowned_findings']}",
        f"- rework rate: {data['attempts']['rework_rate']} ({data['attempts']['rework_tasks']} задач с >1 попыткой)",
        f"- lead time Ready → Accept: {data['lead_time']['avg_ready_to_accept_hours']} ч"
        f" (выборка {data['lead_time']['samples']})",
        f"- verification: {data['verification']['reports']} отчётов, OK {data['verification']['ok']},"
        f" проблемных {data['verification']['zero_or_failed']}",
        "",
        "## Очередь",
        "",
    ]
    for state, items in data["queue"].items():
        lines.append(f"- {state}: {', '.join(items) if items else '—'}")
    lines += ["", "## Изменения", "", "| change | состояние | ред. | риск | review | verification | release | accept |",
              "|---|---|---|---|---|---|---|---|"]
    for row in data["changes"]:
        lines.append(
            f"| {row['change_id']} | {row['status']} | {row['revision']} | {row['risk_class']} |"
            f" {row['reviews']} | {'OK' if row['verification']['ok'] else '—'} |"
            f" {'да' if row['files']['release.json'] else '—'} | {'да' if row['files']['acceptance.json'] else '—'} |"
        )
    lines += ["", "## Задачи", "", "| задача | change | состояние | попытки | вердикт |", "|---|---|---|---|---|"]
    for row in data["tasks"]:
        lines.append(f"| {row['task_id']} | {row['change_id'] or '—'} | {row['state']} | {row['attempts']} | {row['verdict'] or '—'} |")
    lines += ["", "_Снимок на момент генерации; обновляется командой `harness metrics`._"]
    return "\n".join(lines) + "\n"


_BOARD_CSS = """
:root{color-scheme:dark;--bg:#0f1218;--panel:#151a22;--panel2:#1b2230;--ink:#e7ebf2;--muted:#94a0b2;
--line:#28303d;--ok:#3ddc84;--warn:#f5b642;--bad:#ff6b6b;--accent:#6f9bff;--accent-ink:#0b1020}
@media (prefers-color-scheme:light){:root{color-scheme:light;--bg:#f5f7fb;--panel:#fff;--panel2:#f0f3f8;
--ink:#17202e;--muted:#5f6b7a;--line:#d8dee9;--ok:#18794e;--warn:#a15c00;--bad:#b42318;--accent:#2f5fd0;--accent-ink:#fff}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 "Segoe UI",system-ui,-apple-system,sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 26px 56px}
header.top{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:20px}
h1{font-size:26px;letter-spacing:-.02em;margin:0 0 4px}
h2{font-size:15px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:28px 0 10px}
.sub{color:var(--muted);font-size:12px}
.status{margin-left:auto;display:flex;gap:8px;align-items:center;font-size:12px;color:var(--muted)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 4px rgba(61,220,132,.12)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.kpi b{display:block;font-size:26px;line-height:1.15;font-variant-numeric:tabular-nums}
.kpi span{display:block;color:var(--muted);font-size:12px;margin-top:3px}
.kpi.hot b{color:var(--warn)}.kpi.bad b{color:var(--bad)}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:18px 0 12px}
.chip{border:1px solid var(--line);background:transparent;color:var(--ink);border-radius:999px;
padding:6px 12px;font-size:12px;cursor:pointer}
.chip:hover{border-color:var(--accent)}
.chip.on{background:var(--accent);border-color:var(--accent);color:var(--accent-ink);font-weight:600}
.search{margin-left:auto;min-width:230px;padding:8px 12px;border-radius:9px;border:1px solid var(--line);
background:var(--panel);color:var(--ink);font-size:13px;outline:none}
.search:focus{border-color:var(--accent)}
.queue{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px 16px}
.queue div{display:flex;gap:10px;padding:5px 0;font-size:13px}
.queue b{min-width:130px;color:var(--muted);font-weight:600;text-transform:uppercase;font-size:11px;letter-spacing:.05em}
.queue .empty{color:var(--muted)}
table{width:100%;border-collapse:separate;border-spacing:0;background:var(--panel);border:1px solid var(--line);
border-radius:12px;overflow:hidden;font-size:13px}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:middle}
th{position:sticky;top:0;z-index:1;background:var(--panel2);color:var(--muted);font-size:11px;
text-transform:uppercase;letter-spacing:.06em}
tr:last-child td{border-bottom:0}
tbody tr:hover td{background:rgba(111,155,255,.06)}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;border:1px solid var(--line);
font-size:11px;color:var(--muted);white-space:nowrap}
.badge.ok{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 45%,var(--line))}
.badge.warn{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 45%,var(--line))}
.badge.bad{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 45%,var(--line))}
.gates{display:flex;gap:5px;flex-wrap:wrap}
.empty-row{color:var(--muted);text-align:center;padding:28px}
.foot{margin-top:18px;color:var(--muted);font-size:12px}
"""


def _badge(text: str, kind: str = "") -> str:
    return f'<span class="badge {kind}">{html.escape(text)}</span>'


def _status_kind(status: str) -> str:
    if status in ("ACCEPTED", "ARCHIVED"):
        return "ok"
    if "BLOCK" in status or status == "ESCALATED":
        return "bad"
    if status == "READY" or status == "IN_PROGRESS":
        return "warn"
    return ""


def render_html(data: dict[str, Any], *, live: bool = False) -> str:
    summary = data["summary"]
    changes = data["changes"]
    ready = sum(1 for row in changes if row["status"] == "READY")
    blocked = sum(1 for row in changes if "BLOCK" in row["status"] or row["status"] == "ESCALATED")
    active_changes = sum(1 for row in changes
                         if row["status"] not in ("ACCEPTED", "ARCHIVED", "CANCELLED"))
    cards = [
        ("активных задач", summary["tasks_active"], ""),
        ("change в работе", active_changes, ""),
        ("READY", ready, "hot"),
        ("заблокировано", blocked, "bad"),
        ("rework rate", data["attempts"]["rework_rate"], ""),
        ("открытых находок", summary["open_findings"], ""),
    ]
    rows = []
    for row in changes:
        status = str(row["status"])
        ver = row["verification"]
        gates = "".join([
            _badge("trace", "ok" if row.get("trace_ok", True) else "bad"),
            _badge("verify", "ok" if ver["ok"] else ("warn" if ver["present"] else "")),
            _badge("release", "ok" if row["files"]["release.json"] else ""),
            _badge("accept", "ok" if row["files"]["acceptance.json"] else ""),
        ])
        hidden = "" if status not in ("ACCEPTED", "ARCHIVED", "CANCELLED") else " hidden"
        rows.append(
            f'<tr data-state="{html.escape(status)}" data-change="{html.escape(row["change_id"])}"{hidden}>'
            f"<td class=mono>{html.escape(row['change_id'])}</td>"
            f"<td>{_badge(status, _status_kind(status))}</td>"
            f"<td>{html.escape(row['risk_class'])}</td>"
            f"<td>{html.escape(str(row['owner']))}</td>"
            f"<td>{row['reviews']}</td>"
            f"<td><div class=gates>{gates}</div></td></tr>"
        )
    task_rows = "".join(
        f"<tr><td class=mono>{html.escape(t['task_id'])}</td>"
        f"<td class=mono>{html.escape(str(t['change_id'] or '—'))}</td>"
        f"<td>{_badge(t['state'], _status_kind(str(t['state'])))}</td><td>{t['attempts']}</td>"
        f"<td>{html.escape(str(t['verdict'] or '—'))}</td>"
        f"<td>{t['open_findings']}</td></tr>"
        for t in data["tasks"]
    )
    queue_states = [(state, items) for state, items in data["queue"].items() if items]
    queue_html = "".join(
        f'<div><b>{html.escape(state)}</b><span>{html.escape(", ".join(items))}</span></div>'
        for state, items in queue_states
    ) or '<div class="empty">Очередь пуста: все change приняты, в архиве или отменены.</div>'
    refresh = '<meta http-equiv="refresh" content="15">' if live else ""
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{refresh}
<title>harness — доска процесса</title><style>{_BOARD_CSS}</style></head>
<body><main>
<header class="top">
  <div><h1>Доска процесса</h1>
  <div class="sub">Снимок {html.escape(data['generated_at'])} · обновление командой
  <span class="mono">harness board --serve</span></div></div>
  <div class="status"><span class="dot"></span>live · только чтение</div>
</header>
<section class="kpis">
{''.join(f'<div class="kpi {cls}"><b>{value}</b><span>{html.escape(label)}</span></div>' for label, value, cls in cards)}
</section>
<h2>Очередь</h2>
<div class="queue">{queue_html}</div>
<h2>Изменения</h2>
<div class="toolbar">
  <button class="chip on" data-filter="active" type="button">Активные</button>
  <button class="chip" data-filter="ready" type="button">READY</button>
  <button class="chip" data-filter="blocked" type="button">Блок</button>
  <button class="chip" data-filter="archive" type="button">Архив</button>
  <button class="chip" data-filter="all" type="button">Все</button>
  <input class="search" id="q" type="search" placeholder="поиск по change…">
</div>
<table id="changes"><thead><tr><th>change</th><th>состояние</th><th>риск</th><th>владелец</th>
<th>review</th><th>гейты</th></tr></thead><tbody>
{''.join(rows) or '<tr><td colspan=6 class=empty-row>изменений нет</td></tr>'}
</tbody></table>
<h2>Задачи</h2>
<table><thead><tr><th>задача</th><th>change</th><th>состояние</th><th>попытки</th><th>вердикт</th><th>находки</th></tr></thead>
<tbody>{task_rows or '<tr><td colspan=6 class=empty-row>задач нет</td></tr>'}</tbody></table>
<div class="foot">Метрики считаются из SQLite и файлов change; цифры не редактируются отдельно от процесса.</div>
<script>
(function () {{
  var rows = Array.prototype.slice.call(document.querySelectorAll('#changes tbody tr[data-state]'));
  var buttons = Array.prototype.slice.call(document.querySelectorAll('[data-filter]'));
  var q = document.getElementById('q');
  var mode = 'active';
  function match(row) {{
    var s = row.getAttribute('data-state') || '';
    if (mode === 'active') return ['ACCEPTED','ARCHIVED','CANCELLED'].indexOf(s) < 0;
    if (mode === 'ready') return s === 'READY';
    if (mode === 'blocked') return s.indexOf('BLOCK') >= 0 || s === 'ESCALATED';
    if (mode === 'archive') return s === 'ACCEPTED' || s === 'ARCHIVED';
    return true;
  }}
  function apply() {{
    var needle = (q.value || '').toLowerCase();
    rows.forEach(function (row) {{
      var text = (row.getAttribute('data-change') || '').toLowerCase();
      row.hidden = !match(row) || (needle && text.indexOf(needle) < 0);
    }});
    buttons.forEach(function (b) {{ b.className = b.getAttribute('data-filter') === mode ? 'chip on' : 'chip'; }});
  }}
  buttons.forEach(function (b) {{ b.addEventListener('click', function () {{ mode = b.getAttribute('data-filter'); apply(); }}); }});
  q.addEventListener('input', apply);
  apply();
}})();
</script>
</main></body></html>
"""


def board_data(store: Store, root: str) -> dict[str, Any]:
    data = collect(store, root)
    for row in data["changes"]:
        try:
            row["trace_ok"] = changefsm.check_trace(store, root, row["change_id"])["ok"]
        except Exception:  # noqa: BLE001
            row["trace_ok"] = False
    return data


def make_server(store: Store, root: str, host: str = "127.0.0.1",
                port: int = 8765) -> http.server.HTTPServer:
    """Живая доска: каждый запрос перечитывает SQLite и файлы change.

    HTTPServer, а не ThreadingHTTPServer: доска читает состояние последовательно.
    На каждый запрос открывается своё SQLite-соединение: sqlite3 не любит чужой
    поток, а свежее соединение заодно видит только что записанное состояние.
    """

    db_path = store.path

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "harness-board/1"

        def do_GET(self) -> None:  # noqa: N802 - имя задано BaseHTTPRequestHandler
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                body = b"ok\n"
                ctype = "text/plain; charset=utf-8"
                status = 200
            else:
                local = Store(db_path)
                try:
                    if path == "/metrics.json":
                        body = json.dumps(collect(local, root), ensure_ascii=False).encode("utf-8")
                        ctype = "application/json; charset=utf-8"
                    else:
                        body = render_html(board_data(local, root), live=True).encode("utf-8")
                        ctype = "text/html; charset=utf-8"
                    status = 200
                except Exception as exc:  # noqa: BLE001 - доска не должна падать молча
                    body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                    ctype = "application/json; charset=utf-8"
                    status = 500
                finally:
                    local.close()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:  # тихо: это не сервер приложения
            return

    return http.server.HTTPServer((host, int(port)), Handler)
