#!/usr/bin/env python3
"""chrome_cdp.py — настоящий Chrome через DevTools Protocol, без зависимостей.

Зачем отдельный движок: moli отдаёт кэшированные скриншоты и не грузит ленивые
картинки, поэтому для визуальной проверки он не годится. Здесь Chrome
запускается с чистым профилем, страница грузится по-настоящему, а сеть
собирается через ``Network``-события CDP: видно каждый упавший запрос.

Только стандартная библиотека. Протокол WebSocket реализован вручную
(RFC 6455, текстовые фреймы) — этого достаточно для CDP.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request
from typing import Any

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

DEFAULT_PORT = 9224
DEFAULT_WAIT = 12.0
SHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".browser"
)


class WS:
    """Минимальный WebSocket-клиент: хендшейк, маскирование, текстовые фреймы."""

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        rest = url[5:]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (
                f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("websocket handshake failed")
            buf += chunk
        if b"101" not in buf.split(b"\r\n", 1)[0]:
            raise RuntimeError("websocket upgrade rejected")
        self._buf = buf.split(b"\r\n\r\n", 1)[1]

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise RuntimeError("websocket closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send(self, obj: dict[str, Any]) -> None:
        payload = json.dumps(obj, ensure_ascii=False).encode()
        header = bytearray([0x81])
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def recv(self) -> dict[str, Any]:
        b1, b2 = self._read(2)
        opcode = b1 & 0x0F
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read(8))[0]
        mask = self._read(4) if (b2 & 0x80) else b""
        data = self._read(length)
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        if opcode == 0x8:
            raise RuntimeError("websocket close frame")
        if opcode == 0x9:  # ping
            return self.recv()
        return json.loads(data.decode() or "{}")

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


class Chrome:
    """Запущенный Chrome с DevTools-портом и одна CDP-сессия страницы."""

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self.port = port
        self.exe = next((p for p in CHROME_CANDIDATES if os.path.exists(p)), None)
        if not self.exe:
            raise RuntimeError("Chrome или Edge не найден в стандартных путях")
        self.profile = tempfile.mkdtemp(prefix="cdp-mcp-")
        self.proc = subprocess.Popen(
            [
                self.exe,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--no-first-run",
                "--disable-extensions",
                "--hide-scrollbars",
                "--disable-features=Translate,OptimizationHints",
                f"--remote-debugging-port={port}",
                f"--user-data-dir={self.profile}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.ws: WS | None = None
        self._id = 0
        self.events: list[dict[str, Any]] = []
        for _ in range(120):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/version", timeout=1
                ) as resp:
                    info = json.load(resp)
                self.ws = WS(info["webSocketDebuggerUrl"])
                return
            except Exception:
                time.sleep(0.2)
        self.close()
        raise RuntimeError("Chrome не поднял DevTools-порт")

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        session: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        assert self.ws is not None
        self._id += 1
        msg: dict[str, Any] = {"id": self._id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.ws.send(msg)
        deadline = time.time() + timeout
        while time.time() < deadline:
            event = self.ws.recv()
            if event.get("id") == msg["id"]:
                if "error" in event:
                    raise RuntimeError(f"{method}: {event['error']}")
                return event.get("result", {})
            self.events.append(event)
        raise TimeoutError(method)

    def drain(self, seconds: float) -> None:
        """Собирать события сети указанное время."""
        assert self.ws is not None
        end = time.time() + seconds
        self.ws.sock.settimeout(0.3)
        while time.time() < end:
            try:
                self.events.append(self.ws.recv())
            except Exception:
                continue
        self.ws.sock.settimeout(30.0)

    def close(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        if self.ws:
            self.ws.close()
        shutil.rmtree(self.profile, ignore_errors=True)


def _slug(text: str) -> str:
    out = "".join(ch if ch.isalnum() else "-" for ch in text)
    out = "-".join(part for part in out.split("-") if part)
    return (out[:50] or "page").strip("-")


def _screenshot(chrome: Chrome, session: str, tag: str) -> str:
    os.makedirs(SHOT_DIR, exist_ok=True)
    path = os.path.join(SHOT_DIR, f"{_slug(tag)}-{time.strftime('%Y%m%d-%H%M%S')}.png")
    data = chrome.call("Page.captureScreenshot", {"format": "png"}, session=session)
    with open(path, "wb") as fh:
        fh.write(base64.b64decode(data["data"]))
    return path


def _eval(chrome: Chrome, session: str, expression: str) -> Any:
    res = chrome.call(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
        session=session,
        timeout=40,
    )
    return res.get("result", {}).get("value")


def _open(chrome: Chrome, url: str, width: int, height: int) -> str:
    target = chrome.call("Target.createTarget", {"url": "about:blank"})
    session = chrome.call(
        "Target.attachToTarget", {"targetId": target["targetId"], "flatten": True}
    )["sessionId"]
    chrome.call("Page.enable", session=session)
    chrome.call("Network.enable", session=session)
    chrome.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
        session=session,
    )
    chrome.call("Page.navigate", {"url": url}, session=session)
    return session


def _net(events: list[dict[str, Any]], limit: int = 30) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    hosts: dict[str, int] = {}
    problems: list[dict[str, Any]] = []
    for event in events:
        method = event.get("method")
        params = event.get("params", {})
        if method == "Network.responseReceived":
            resp = params.get("response", {})
            status = str(resp.get("status"))
            statuses[status] = statuses.get(status, 0) + 1
            url = resp.get("url") or ""
            host = url.split("/")[2] if "//" in url else "?"
            hosts[host] = hosts.get(host, 0) + 1
            if int(resp.get("status") or 0) >= 400:
                problems.append(
                    {"status": resp.get("status"), "url": url[:200], "host": host}
                )
        elif method == "Network.loadingFailed":
            problems.append(
                {
                    "status": "FAILED",
                    "error": params.get("errorText"),
                    "type": params.get("type"),
                }
            )
    return {
        "statuses": dict(sorted(statuses.items(), key=lambda kv: -kv[1])),
        "top_hosts": dict(sorted(hosts.items(), key=lambda kv: -kv[1])[:10]),
        "problems_count": len(problems),
        "problems": problems[:limit],
    }


def open_page(
    url: str, wait: float = DEFAULT_WAIT, width: int = 1400, height: int = 1000
) -> dict[str, Any]:
    """Открыть страницу: скриншот, заголовок, картинки, сеть."""
    chrome = Chrome()
    try:
        session = _open(chrome, url, width, height)
        chrome.drain(wait)
        shot = _screenshot(chrome, session, url)
        images = _eval(
            chrome,
            session,
            "JSON.stringify({total:document.images.length,"
            "loaded:[...document.images].filter(i=>i.naturalWidth>0).length,"
            "broken:[...document.images].filter(i=>(i.currentSrc||i.src)&&"
            "i.complete&&i.naturalWidth===0).length})",
        )
        return {
            "url": url,
            "title": _eval(chrome, session, "document.title"),
            "final_url": _eval(chrome, session, "location.href"),
            "screenshot": shot,
            "screenshot_bytes": os.path.getsize(shot),
            "images": json.loads(images) if images else None,
            **_net(chrome.events),
        }
    finally:
        chrome.close()


def probe_network(url: str, wait: float = DEFAULT_WAIT) -> dict[str, Any]:
    """Только сеть: статусы, хосты, упавшие и ошибочные запросы."""
    chrome = Chrome()
    try:
        session = _open(chrome, url, 1400, 1000)
        chrome.drain(wait)
        return {"url": url, "title": _eval(chrome, session, "document.title"),
                **_net(chrome.events)}
    finally:
        chrome.close()


def click_page(
    url: str, selector: str, wait: float = DEFAULT_WAIT, after: float = 4.0
) -> dict[str, Any]:
    """Открыть страницу, кликнуть по селектору, снять скриншот после клика."""
    chrome = Chrome()
    try:
        session = _open(chrome, url, 1400, 1000)
        chrome.drain(wait)
        expression = (
            "(()=>{const el=document.querySelector(%s);"
            "if(!el)return 'not-found';"
            "el.scrollIntoView({block:'center'});el.click();return 'clicked';})()"
            % json.dumps(selector)
        )
        clicked = _eval(chrome, session, expression)
        chrome.drain(after)
        shot = _screenshot(chrome, session, url + "-click")
        return {
            "url": url,
            "selector": selector,
            "click_result": clicked,
            "title_after": _eval(chrome, session, "document.title"),
            "final_url": _eval(chrome, session, "location.href"),
            "screenshot": shot,
            "screenshot_bytes": os.path.getsize(shot),
            **_net(chrome.events),
        }
    finally:
        chrome.close()


def eval_page(url: str, expression: str, wait: float = 10.0) -> dict[str, Any]:
    """Выполнить JS на странице и вернуть значение."""
    chrome = Chrome()
    try:
        session = _open(chrome, url, 1400, 1000)
        chrome.drain(wait)
        return {"url": url, "result": _eval(chrome, session, expression)}
    finally:
        chrome.close()


def read_text(url: str, wait: float = 10.0, max_chars: int = 8000) -> dict[str, Any]:
    """Прочитать видимый текст страницы."""
    chrome = Chrome()
    try:
        session = _open(chrome, url, 1400, 1000)
        chrome.drain(wait)
        text = _eval(chrome, session, "document.body ? document.body.innerText : ''") or ""
        return {
            "url": url,
            "title": _eval(chrome, session, "document.title"),
            "text": text[:max_chars],
            "length": len(text),
            "truncated": len(text) > max_chars,
        }
    finally:
        chrome.close()

class Session:
    """Браузер + одна вкладка: открыть, выполнить JS, снять скриншот, отдать сеть."""

    def __init__(self, url: str, width: int = 1400, height: int = 1000) -> None:
        self.chrome = Chrome()
        self.id = _open(self.chrome, url, width, height)

    def drain(self, seconds: float) -> None:
        self.chrome.drain(seconds)

    def eval(self, expression: str) -> Any:
        return _eval(self.chrome, self.id, expression)

    def shot(self, tag: str) -> str:
        return _screenshot(self.chrome, self.id, tag)

    def net(self) -> dict[str, Any]:
        return _net(self.chrome.events)

    def close(self) -> None:
        self.chrome.close()

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
