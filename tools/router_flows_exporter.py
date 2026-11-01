#!/usr/bin/env python3
"""Экспортёр реальных потоков роутера в Prometheus.

Раз в POLL_INTERVAL секунд читает /api/connections у AWG Manager (через
обратный SSH-туннель на 127.0.0.1:2222) и отдаёт метрики на 127.0.0.1:9116.

Видно: устройство, адресат, протокол, состояние, объём. Не видно: URL и время
ответа сервера — трафик зашифрован TLS.
"""

from __future__ import annotations

import ipaddress
import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROUTER_API = os.environ.get("ROUTER_API", "http://127.0.0.1:2222")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "15"))
LISTEN_HOST, LISTEN_PORT = os.environ.get("LISTEN_ADDR", "127.0.0.1:9116").split(":")

# Подсети сервисов, которые важно различать. Источник — публичные диапазоны.
SERVICE_NETS = {
    "instagram": [
        "157.240.0.0/16", "31.13.24.0/21", "31.13.64.0/18", "179.60.192.0/22",
        "185.60.216.0/22", "57.144.0.0/14", "163.70.128.0/17", "163.77.128.0/17",
        "66.220.144.0/20", "69.63.176.0/20", "69.171.224.0/19", "74.119.76.0/22",
        "102.132.96.0/20", "103.4.96.0/22", "129.134.0.0/16", "147.75.208.0/20",
        "173.252.64.0/18", "185.89.216.0/22", "204.15.20.0/22", "45.64.40.0/22",
        "189.247.71.0/24", "57.141.0.0/21",
    ],
    "telegram": [
        "149.154.160.0/20", "91.108.4.0/22", "91.108.8.0/21", "91.108.16.0/21",
        "91.108.56.0/22", "91.105.192.0/23", "185.76.151.0/24",
    ],
    "youtube": [
        "142.250.0.0/15", "142.251.0.0/16", "172.217.0.0/16", "216.58.0.0/16",
        "74.125.0.0/16", "173.194.0.0/16", "209.85.128.0/17", "172.253.0.0/16",
        "108.177.0.0/17", "64.233.160.0/19",
    ],
    "local": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"],
}

NETS = []
for _svc, _cidrs in SERVICE_NETS.items():
    for _c in _cidrs:
        NETS.append((ipaddress.ip_network(_c), _svc))


def classify(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "unknown"
    for net, svc in NETS:
        if addr in net:
            return svc
    return "other"


def fmt_labels(**labels) -> str:
    parts = []
    for k, v in labels.items():
        s = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        parts.append(f'{k}="{s}"')
    return "{" + ",".join(parts) + "}"


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.flows_total: dict[tuple, int] = {}
        self.bytes_in: dict[tuple, int] = {}
        self.bytes_out: dict[tuple, int] = {}
        self.active: dict[tuple, int] = {}
        self.stuck: dict[tuple, int] = {}
        self.failures: dict[tuple, int] = {}
        self.known: dict[str, dict] = {}
        self.scrape_ok = 0
        self.last_scrape = 0.0
        self.last_error = ""


STATE = State()


def poll_once() -> None:
    url = f"{ROUTER_API}/api/connections?limit=2000"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            payload = json.load(r)
        conns = payload.get("data", {}).get("connections") or []
    except Exception as exc:  # noqa: BLE001
        with STATE.lock:
            STATE.scrape_ok = 0
            STATE.last_error = str(exc)[:120]
        return

    now = time.time()
    seen = set()
    active: dict[tuple, int] = {}
    stuck: dict[tuple, int] = {}
    with STATE.lock:
        for c in conns:
            device = (c.get("clientName") or c.get("src") or "unknown").strip()
            dst = c.get("dst") or ""
            proto = (c.get("protocol") or "other").lower()
            state = (c.get("state") or "").upper()
            service = classify(dst)

            key_flow = f"{device}|{c.get('src')}:{c.get('srcPort')}|{dst}:{c.get('dstPort')}|{proto}"
            seen.add(key_flow)

            bi = int(c.get("bytesIn") or 0)
            bo = int(c.get("bytesOut") or 0)
            prev = STATE.known.get(key_flow)
            if prev is None:
                k = (device, service, proto)
                STATE.flows_total[k] = STATE.flows_total.get(k, 0) + 1
                prev = {"bi": 0, "bo": 0, "stuck": 0, "device": device,
                        "service": service, "proto": proto}
                STATE.known[key_flow] = prev
            dbi = max(0, bi - prev["bi"])
            dbo = max(0, bo - prev["bo"])
            prev["bi"], prev["bo"] = bi, bo
            if dbi:
                kb = (device, service)
                STATE.bytes_in[kb] = STATE.bytes_in.get(kb, 0) + dbi
            if dbo:
                kb = (device, service)
                STATE.bytes_out[kb] = STATE.bytes_out.get(kb, 0) + dbo

            if state == "SYN_SENT":
                prev["stuck"] += 1
                k = (device, service, proto)
                stuck[k] = stuck.get(k, 0) + 1
                kf = (device, service)
                if prev["stuck"] >= 2 and not prev.get("counted"):
                    STATE.failures[kf] = STATE.failures.get(kf, 0) + 1
                    prev["counted"] = True
            else:
                prev["stuck"] = 0

            k = (device, service, proto)
            active[k] = active.get(k, 0) + 1

        # соединения, пропавшие из снимка
        for key_flow in list(STATE.known):
            if key_flow not in seen:
                info = STATE.known.pop(key_flow)
                if info.get("stuck", 0) >= 2 and not info.get("counted"):
                    kf = (info["device"], info["service"])
                    STATE.failures[kf] = STATE.failures.get(kf, 0) + 1

        STATE.active = active
        STATE.stuck = stuck
        STATE.scrape_ok = 1
        STATE.last_scrape = now
        STATE.last_error = ""


def poller() -> None:
    while True:
        poll_once()
        time.sleep(POLL_INTERVAL)


def render() -> str:
    lines = []
    with STATE.lock:
        lines.append("# HELP router_flows_active Активные соединения в текущем снимке")
        lines.append("# TYPE router_flows_active gauge")
        for (dev, svc, proto), v in STATE.active.items():
            lines.append(f"router_flows_active{fmt_labels(device=dev, service=svc, proto=proto)} {v}")

        lines.append("# HELP router_flows_total Новые соединения")
        lines.append("# TYPE router_flows_total counter")
        for (dev, svc, proto), v in STATE.flows_total.items():
            lines.append(f"router_flows_total{fmt_labels(device=dev, service=svc, proto=proto)} {v}")

        lines.append("# HELP router_flows_stuck Соединения в SYN_SENT (не установились)")
        lines.append("# TYPE router_flows_stuck gauge")
        for (dev, svc, proto), v in STATE.stuck.items():
            lines.append(f"router_flows_stuck{fmt_labels(device=dev, service=svc, proto=proto)} {v}")

        lines.append("# HELP router_flow_failures_total Провалы соединений")
        lines.append("# TYPE router_flow_failures_total counter")
        for (dev, svc), v in STATE.failures.items():
            lines.append(f"router_flow_failures_total{fmt_labels(device=dev, service=svc)} {v}")

        lines.append("# HELP router_bytes_in_total Получено байт")
        lines.append("# TYPE router_bytes_in_total counter")
        for (dev, svc), v in STATE.bytes_in.items():
            lines.append(f"router_bytes_in_total{fmt_labels(device=dev, service=svc)} {v}")

        lines.append("# HELP router_bytes_out_total Отправлено байт")
        lines.append("# TYPE router_bytes_out_total counter")
        for (dev, svc), v in STATE.bytes_out.items():
            lines.append(f"router_bytes_out_total{fmt_labels(device=dev, service=svc)} {v}")

        lines.append("# HELP router_exporter_ok Удалось ли прочитать API роутера")
        lines.append("# TYPE router_exporter_ok gauge")
        lines.append(f"router_exporter_ok {STATE.scrape_ok}")
        lines.append("# HELP router_exporter_last_scrape_seconds Время последнего успешного опроса")
        lines.append("# TYPE router_exporter_last_scrape_seconds gauge")
        lines.append(f"router_exporter_last_scrape_seconds {STATE.last_scrape}")
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0] not in ("/metrics", "/"):
            self.send_response(404)
            self.end_headers()
            return
        body = render().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # тишина в логах
        return


def main() -> None:
    threading.Thread(target=poller, daemon=True).start()
    srv = ThreadingHTTPServer((LISTEN_HOST, int(LISTEN_PORT)), Handler)
    srv.serve_forever()


if __name__ == "__main__":
    main()
