#!/usr/bin/env python3
"""netmon — лёгкая панель обращений и ошибок для домашнего роутера Keenetic.

Источники (всё читается, ничего не перехватывается):
  * GET  http://127.0.0.1:2222/api/connections   — снимок соединений NDMS
  * GET  http://127.0.0.1:2222/api/logs          — журнал AWG Manager
  * journalctl -u hysteria-server                 — сетевые ошибки Hysteria
  * GET  http://127.0.0.1:9115/probe              — синтетические пробы (коды HTTP)

Отдаёт одну страницу на / и JSON на /data. Памяти просит ~30 МБ.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROUTER = os.environ.get("ROUTER_API", "http://127.0.0.1:2222")
BLACKBOX = os.environ.get("BLACKBOX", "http://127.0.0.1:9115")
LISTEN = os.environ.get("LISTEN_ADDR", "0.0.0.0:8080")
FLOW_INTERVAL = int(os.environ.get("FLOW_INTERVAL", "5"))
LOG_INTERVAL = int(os.environ.get("LOG_INTERVAL", "20"))
PROBE_INTERVAL = int(os.environ.get("PROBE_INTERVAL", "30"))

SERVICES = {
    "instagram": ["157.240.0.0/16", "31.13.24.0/21", "31.13.64.0/18", "179.60.192.0/22",
                  "185.60.216.0/22", "57.144.0.0/14", "163.70.128.0/17", "163.77.128.0/17",
                  "66.220.144.0/20", "69.63.176.0/20", "69.171.224.0/19", "74.119.76.0/22",
                  "102.132.96.0/20", "103.4.96.0/22", "129.134.0.0/16", "147.75.208.0/20",
                  "173.252.64.0/18", "185.89.216.0/22", "204.15.20.0/22", "45.64.40.0/22",
                  "189.247.71.0/24", "57.141.0.0/21"],
    "telegram": ["149.154.160.0/20", "91.108.4.0/22", "91.108.8.0/21", "91.108.16.0/21",
                 "91.108.56.0/22", "91.105.192.0/23", "185.76.151.0/24"],
    "youtube": ["142.250.0.0/15", "142.251.0.0/16", "172.217.0.0/16", "216.58.0.0/16",
                "74.125.0.0/16", "173.194.0.0/16", "209.85.128.0/17", "172.253.0.0/16",
                "108.177.0.0/17", "64.233.160.0/19"],
    "локально": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"],
}
NETS = []
for _svc, _cidrs in SERVICES.items():
    for _c in _cidrs:
        NETS.append((ipaddress.ip_network(_c), _svc))


def service_of(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "?"
    for net, svc in NETS:
        if addr in net:
            return svc
    return "прочее"


class Store:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.flows: list[dict] = []
        self.errors: deque = deque(maxlen=400)
        self.closed: deque = deque(maxlen=3000)
        self.probes: list[dict] = []
        self.tunnel: dict = {}
        self.last_flow_poll = 0.0
        self.seen_flows: dict = {}
        self.seen_logs: set = set()
        self.seen_lines: deque = deque(maxlen=1500)
        self.counters = {"flows_seen": 0, "failures": 0, "hysteria_errors": 0}
        self.local_noise: dict = {}


ST = Store()


def http_json(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def add_error(kind: str, message: str, **extra) -> None:
    ev = {"time": time.time(), "kind": kind, "message": message}
    ev.update(extra)
    with ST.lock:
        ST.errors.appendleft(ev)


# ------------------------------------------------------------------ потоки

def poll_flows() -> None:
    try:
        data = http_json(f"{ROUTER}/api/connections?limit=1500", timeout=12)
    except Exception:
        return
    conns = (data.get("data") or {}).get("connections") or []
    now = time.time()
    live_keys = set()
    flows = []
    with ST.lock:
        for c in conns:
            dev = (c.get("clientName") or "").strip() or c.get("src") or "?"
            dst = c.get("dst") or ""
            proto = (c.get("protocol") or "").lower()
            state = (c.get("state") or "").upper()
            key = f"{c.get('src')}:{c.get('srcPort')}-{dst}:{c.get('dstPort')}-{proto}"
            live_keys.add(key)

            info = ST.seen_flows.get(key)
            if info is None:
                info = {"first": now, "stuck": 0, "reported": False,
                        "device": dev, "dst": dst, "service": service_of(dst),
                        "proto": proto, "port": c.get("dstPort")}
                ST.seen_flows[key] = info
                ST.counters["flows_seen"] += 1

            if state == "SYN_SENT":
                info["stuck"] += 1
                if info["stuck"] == 3 and not info["reported"]:
                    info["reported"] = True
                    ST.counters["failures"] += 1
                    if info["service"] == "локально":
                        tgt = f"{dst}:{c.get('dstPort')}"
                        ST.local_noise[tgt] = ST.local_noise.get(tgt, 0) + 1
                        continue
                    ST.errors.appendleft({
                        "time": info["first"], "kind": "connect",
                        "message": f"соединение не установилось за {info['stuck'] * FLOW_INTERVAL} с",
                        "device": dev, "service": info["service"],
                        "target": f"{dst}:{c.get('dstPort')}", "proto": proto,
                    })
            else:
                info["stuck"] = 0

            flows.append({
                "device": dev, "dst": dst, "port": c.get("dstPort"), "proto": proto,
                "state": state or "-", "service": info["service"],
                "bytes_in": int(c.get("bytesIn") or 0), "bytes_out": int(c.get("bytesOut") or 0),
                "tunnel": c.get("tunnelName") or "-",
            })

        for key in list(ST.seen_flows):
            if key not in live_keys:
                info = ST.seen_flows.pop(key)
                ST.closed.append({"t": now, "device": info["device"], "service": info["service"]})
        ST.flows = flows
        ST.last_flow_poll = now


# ------------------------------------------------------------------ Hysteria

HYST_RE = re.compile(r"(?P<level>WARN|ERROR|INFO)\s+(?P<kind>[A-Za-z ]+?)\s+\{.*?\}")
REQADDR_RE = re.compile(r'"reqAddr":\s*"([^"]+)"')
ERR_RE = re.compile(r'"error":\s*"([^"]+)"')


def poll_hysteria() -> None:
    try:
        out = subprocess.run(
            ["sudo", "-n", "journalctl", "-u", "hysteria-server", "--since", "-40 min",
             "-o", "cat", "--no-pager"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception:
        return
    now = time.time()
    new = []
    with ST.lock:
        for line in out.splitlines():
            if "error" not in line.lower() and "WARN" not in line:
                continue
            h = hash(line)
            if h in ST.seen_logs:
                continue
            ST.seen_logs.add(h)
            if len(ST.seen_logs) > 3000:
                ST.seen_logs = set(list(ST.seen_logs)[-1500:])
            m_err = ERR_RE.search(line)
            m_addr = REQADDR_RE.search(line)
            if not m_err:
                continue
            err = m_err.group(1)
            target = m_addr.group(1) if m_addr else ""
            dst_ip = target.split(":")[0] if target else ""
            ST.counters["hysteria_errors"] += 1
            new.append({
                "time": now, "kind": "hysteria",
                "message": err[:180],
                "target": target,
                "service": service_of(dst_ip) if dst_ip else "?",
            })
        for ev in new:
            ST.errors.appendleft(ev)


def poll_awgm_logs() -> None:
    try:
        data = http_json(f"{ROUTER}/api/logs?level=warn&lines=60", timeout=12)
    except Exception:
        return
    logs = (data.get("data") or {}).get("logs") or []
    now = time.time()
    fresh = []
    with ST.lock:
        for item in logs:
            msg = (item.get("message") or "").strip()
            if not msg:
                continue
            key = f"{item.get('timestamp')}|{msg}"
            if key in ST.seen_lines:
                continue
            ST.seen_lines.append(key)
            fresh.append({
                "time": now, "kind": "awgm",
                "level": item.get("level") or "warn",
                "message": msg[:200],
                "group": item.get("group") or "",
            })
        for ev in fresh:
            ST.errors.appendleft(ev)


# ------------------------------------------------------------------ пробы

# Instagram намеренно НЕ пробим: синтетические запросы с IP VPS каждые 30 с
# (2880 в сутки) — прямой путь к 429 и бану адреса. Здоровье Instagram читается
# пассивно: живые соединения к его подсетям, ошибки Hysteria, залипшие SYN_SENT.
PROBES = [
    ("Telegram", "https://t.me/", "http_vpn"),
    ("Google", "https://www.google.com/generate_204", "http_vpn"),
    ("Google (напрямую)", "https://www.google.com/generate_204", "http_direct"),
]


def probe_one(target: str, module: str) -> dict:
    url = f"{BLACKBOX}/probe?target={urllib.parse.quote(target, safe='')}&module={module}"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": str(exc)[:80]}
    def val(name: str) -> float | None:
        m = re.search(rf"^{name} ([0-9.e+-]+)$", body, re.M)
        return float(m.group(1)) if m else None
    return {
        "ok": (val("probe_success") or 0) == 1,
        "code": int(val("probe_http_status_code") or 0),
        "seconds": round(val("probe_duration_seconds") or 0, 3),
    }


def poll_probes() -> None:
    out = []
    for label, target, module in PROBES:
        res = probe_one(target, module)
        res.update({"label": label, "target": target, "module": module, "time": time.time()})
        out.append(res)
    with ST.lock:
        ST.probes = out


def poll_tunnel() -> None:
    try:
        data = http_json(f"{ROUTER}/api/singbox/tunnels", timeout=10)
    except Exception:
        return
    tunnels = (data.get("data") or [])
    active = [t for t in tunnels if t.get("proxyInterface") in ("Proxy1",)]
    with ST.lock:
        ST.tunnel = {"count": len(tunnels), "tunnels": [
            {"tag": t.get("tag"), "server": t.get("server"),
             "iface": t.get("proxyInterface"), "running": t.get("running")}
            for t in tunnels
        ], "active": active[0].get("tag") if active else ""}


def loop(fn, interval: int) -> None:
    while True:
        try:
            fn()
        except Exception:
            pass
        time.sleep(interval)


# ------------------------------------------------------------------ выдача

def snapshot() -> dict:
    now = time.time()
    with ST.lock:
        hour_ago = now - 3600
        by_service: dict[str, int] = {}
        by_device: dict[str, int] = {}
        for f in ST.flows:
            if f["service"] in ("локально", "?"):
                continue
            by_service[f["service"]] = by_service.get(f["service"], 0) + f["bytes_in"]
            by_device[f["device"]] = by_device.get(f["device"], 0) + f["bytes_in"]
        collapsed = []
        for ev in list(ST.errors):
            for c in collapsed:
                if (c["kind"] == ev["kind"] and c.get("device") == ev.get("device")
                        and c.get("target") == ev.get("target")
                        and c["message"] == ev["message"]
                        and abs(c["time"] - ev["time"]) < 180):
                    c["count"] = c.get("count", 1) + 1
                    c["time"] = min(c["time"], ev["time"])
                    break
            else:
                e = dict(ev)
                e["count"] = 1
                collapsed.append(e)
        return {
            "now": now,
            "counters": dict(ST.counters),
            "tunnel": ST.tunnel,
            "probes": ST.probes,
            "errors": collapsed[:120],
            "flows": sorted(ST.flows, key=lambda x: x["bytes_in"], reverse=True)[:80],
            "top_service": sorted(by_service.items(), key=lambda x: -x[1])[:8],
            "top_device": sorted(by_device.items(), key=lambda x: -x[1])[:8],
            "local_noise": sorted(ST.local_noise.items(), key=lambda x: -x[1])[:5],
            "flow_poll_age": round(now - ST.last_flow_poll, 1) if ST.last_flow_poll else None,
        }


PAGE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Обращения и ошибки</title>
<style>
:root{--bg:#0f1216;--card:#171c22;--line:#232a33;--tx:#d7dee7;--dim:#8a97a6;
--ok:#43d17a;--warn:#f2b544;--err:#f3685f;--acc:#5aa9ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}
header{padding:14px 18px;border-bottom:1px solid var(--line);display:flex;gap:18px;align-items:baseline;flex-wrap:wrap}
h1{font-size:16px;margin:0;font-weight:600}
.muted{color:var(--dim);font-size:12px}
main{padding:14px 18px;display:grid;gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card h2{font-size:13px;margin:0 0 10px;color:var(--dim);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--dim);font-weight:500;font-size:11px;text-transform:uppercase}
tr:last-child td{border-bottom:none}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.ok{color:var(--ok)}.warn{color:var(--warn)}.err{color:var(--err)}.acc{color:var(--acc)}
.pill{display:inline-block;padding:1px 7px;border-radius:20px;font-size:11px;background:#202832;color:var(--dim)}
.pill.ok{background:rgba(67,209,122,.14);color:var(--ok)}
.pill.err{background:rgba(243,104,95,.14);color:var(--err)}
.pill.warn{background:rgba(242,181,68,.14);color:var(--warn)}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}
.big{font-size:20px;font-weight:600}
.scroll{max-height:420px;overflow:auto}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px}
</style></head><body>
<header>
  <h1>Обращения и ошибки</h1>
  <span class="muted" id="ts">—</span>
  <span class="muted" id="tun">—</span>
</header>
<main>
  <div class="row" id="probes"></div>
  <div class="row">
    <div class="card"><h2>Ошибки соединений и Hysteria</h2><div class="scroll"><table id="errors"></table></div></div>
    <div class="card"><h2>Топ по объёму (текущие соединения)</h2>
      <table id="top"></table>
      <h2 style="margin-top:14px">Устройства</h2><table id="devs"></table>
    </div>
  </div>
  <div class="card" id="noisecard" style="display:none"><h2>Локальный шум (не интернет)</h2><table id="noise"></table></div>
  <div class="card"><h2>Живые соединения</h2><div class="scroll"><table id="flows"></table></div></div>
</main>
<script>
const fmtB=(b)=>{if(b<1024)return b+' Б';if(b<1048576)return (b/1024).toFixed(1)+' КБ';return (b/1048576).toFixed(1)+' МБ';};
const t=(ts)=>{const d=new Date(ts*1000);return d.toLocaleTimeString('ru-RU');};
async function tick(){
  let d; try{ d = await (await fetch('/data',{cache:'no-store'})).json(); }catch(e){ return; }
  document.getElementById('ts').textContent='обновлено '+new Date(d.now*1000).toLocaleTimeString('ru-RU')
    +' · опрос роутера '+(d.flow_poll_age??'?')+' с назад';
  const tn=d.tunnel||{}; const act=(tn.tunnels||[]).find(x=>x.iface==='Proxy1')||{};
  document.getElementById('tun').innerHTML='VPN: <span class="'+(act.running?'ok':'err')+'">'
    + (act.running?'работает':'нет') + '</span> ' + (act.server||'');

  const p=document.getElementById('probes'); p.innerHTML='';
  (d.probes||[]).forEach(x=>{
    const cls=x.ok?'ok':'err';
    p.innerHTML+='<div class="card"><h2>'+x.label+'</h2><div class="big '+cls+'">'
      + (x.ok?'отвечает':'не отвечает') + '</div><div class="muted">код '
      + (x.code||'—') + ' · ' + (x.seconds!=null?x.seconds+' с':'') + '</div></div>';
  });

  const e=document.getElementById('errors'); e.innerHTML='<tr><th>время</th><th>источник</th><th>устройство</th><th>куда</th><th>что</th></tr>';
  (d.errors||[]).slice(0,80).forEach(x=>{
    const src=x.kind==='hysteria'?'Hysteria':(x.kind==='connect'?'роутер':'AWGM');
    e.innerHTML+='<tr><td>'+t(x.time)+'</td><td>'+src+'</td><td>'+(x.device||'—')+'</td><td class="mono">'
      +(x.target||'—')+'</td><td style="white-space:normal">'+(x.service?('<span class="pill">'+x.service+'</span> '):'')
      +(x.message||'')+(x.count>1?(' <span class="pill">×'+x.count+'</span>'):'')+'</td></tr>';
  });

  const top=document.getElementById('top'); top.innerHTML='';
  (d.top_service||[]).forEach(([k,v])=>{ top.innerHTML+='<tr><td>'+k+'</td><td class="num">'+fmtB(v)+'</td></tr>'; });
  const devs=document.getElementById('devs'); devs.innerHTML='';
  (d.top_device||[]).forEach(([k,v])=>{ devs.innerHTML+='<tr><td>'+k+'</td><td class="num">'+fmtB(v)+'</td></tr>'; });

  const nz=(d.local_noise||[]); const nc=document.getElementById('noisecard');
  if(nz.length){ nc.style.display=''; const ne=document.getElementById('noise'); ne.innerHTML='';
    nz.forEach(([k,v])=>{ ne.innerHTML+='<tr><td class="mono">'+k+'</td><td class="num">'+v+' попыток</td></tr>'; }); }
  else { nc.style.display='none'; }
  const f=document.getElementById('flows');
  f.innerHTML='<tr><th>устройство</th><th>адресат</th><th>сервис</th><th>прото</th><th>состояние</th><th class="num">вход</th><th class="num">исход</th><th>через</th></tr>';
  (d.flows||[]).forEach(x=>{
    const bad=(x.state==='SYN_SENT');
    f.innerHTML+='<tr><td>'+x.device+'</td><td class="mono">'+x.dst+':'+x.port+'</td><td>'+x.service
      +'</td><td>'+x.proto+'</td><td class="'+(bad?'err':'')+'">'+x.state
      +'</td><td class="num">'+fmtB(x.bytes_in)+'</td><td class="num">'+fmtB(x.bytes_out)
      +'</td><td class="'+(x.tunnel.includes('Direct')?'muted':'acc')+'">'+x.tunnel+'</td></tr>';
  });
}
tick(); setInterval(tick, 5000);
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/data":
            self._send(200, json.dumps(snapshot(), ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif path == "/health":
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, *args) -> None:
        return


def main() -> None:
    host, port = LISTEN.split(":")
    threads = [
        threading.Thread(target=loop, args=(poll_flows, FLOW_INTERVAL), daemon=True),
        threading.Thread(target=loop, args=(poll_hysteria, LOG_INTERVAL), daemon=True),
        threading.Thread(target=loop, args=(poll_awgm_logs, LOG_INTERVAL), daemon=True),
        threading.Thread(target=loop, args=(poll_probes, PROBE_INTERVAL), daemon=True),
        threading.Thread(target=loop, args=(poll_tunnel, 30), daemon=True),
    ]
    for th in threads:
        th.start()
    ThreadingHTTPServer((host, int(port)), Handler).serve_forever()


if __name__ == "__main__":
    main()
