#!/usr/bin/env python3
"""mcp_stdio.py — минимальный MCP-сервер поверх stdio, без зависимостей.

Протокол: JSON-RPC 2.0, одно сообщение на строку. В stdout — только протокол,
диагностика — в stderr. Реализованы ровно те методы, которые нужны нашим
инструментам: ``initialize``, ``tools/list``, ``tools/call``, ``ping``.

Инструмент — функция ``dict -> str | dict``. Строка превращается в текстовый
блок ответа; словарь с ключом ``content`` используется как есть (например,
чтобы вернуть картинку). Любое исключение становится ``isError``-ответом, а не
падением сервера.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

Handler = Callable[[dict[str, Any]], Any]


class ToolError(RuntimeError):
    """Ожидаемая ошибка инструмента: попадает в ответ как текст, а не как трейс."""


def log(prefix: str, message: str) -> None:
    print(f"[{prefix}] {message}", file=sys.stderr, flush=True)


class StdioServer:
    """MCP-сервер: имя, версия, инструкции, список инструментов и обработчики."""

    def __init__(self, name: str, version: str, instructions: str,
                 tools: list[dict[str, Any]], handlers: dict[str, Handler]) -> None:
        self.name = name
        self.version = version
        self.instructions = instructions
        self.tools = tools
        self.handlers = handlers

    # ---------------------------------------------------------------- transport

    @staticmethod
    def send(payload: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def reply(self, request_id: Any, result: dict[str, Any]) -> None:
        self.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def reply_error(self, request_id: Any, code: int, message: str) -> None:
        self.send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})

    # ------------------------------------------------------------------ methods

    def handle(self, request: dict[str, Any]) -> None:
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params") or {}

        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if isinstance(requested, str) and requested else "2025-06-18"
            log(self.name, f"initialize: protocol={version}")
            self.reply(request_id, {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.name, "version": self.version},
                "instructions": self.instructions,
            })
            return
        if method in ("notifications/initialized", "initialized"):
            return
        if method == "ping":
            self.reply(request_id, {})
            return
        if method == "tools/list":
            self.reply(request_id, {"tools": self.tools})
            return
        if method == "tools/call":
            self.call_tool(request_id, params)
            return
        if request_id is not None:
            self.reply_error(request_id, -32601, f"метод не поддерживается: {method}")

    def call_tool(self, request_id: Any, params: dict[str, Any]) -> None:
        name = str(params.get("name") or "")
        handler = self.handlers.get(name)
        if handler is None:
            self.reply_error(request_id, -32602, f"неизвестный инструмент: {name}")
            return
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        try:
            result = handler(arguments)
        except ToolError as exc:
            self.reply(request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
            return
        except Exception as exc:  # noqa: BLE001 — один запрос не должен ронять сервер
            log(self.name, f"сбой {name}: {exc!r}")
            self.reply(request_id, {
                "content": [{"type": "text", "text": f"внутренняя ошибка {name}: {exc}"}],
                "isError": True,
            })
            return
        if isinstance(result, dict) and "content" in result:
            payload = dict(result)
            payload.setdefault("isError", False)
            self.reply(request_id, payload)
            return
        self.reply(request_id, {"content": [{"type": "text", "text": str(result)}], "isError": False})

    # --------------------------------------------------------------------- loop

    def run(self) -> int:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError, OSError):
                pass
        log(self.name, f"старт v{self.version}")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                log(self.name, f"битый JSON: {exc}")
                self.send({"jsonrpc": "2.0", "id": None,
                           "error": {"code": -32700, "message": "parse error"}})
                continue
            if isinstance(request, list):
                for item in request:
                    if isinstance(item, dict):
                        self.handle(item)
                continue
            if isinstance(request, dict):
                self.handle(request)
        log(self.name, "stdin закрыт, выход")
        return 0
