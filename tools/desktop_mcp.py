#!/usr/bin/env python3
"""desktop_mcp.py — computer use на десктопе как MCP-инструмент.

Обёртка над ``agent-desktop.exe`` (vercel-labs, Apache-2.0): бинарник читает
дерево доступности окна и дёргает элементы по refs. Здесь он приведён к
протоколу ``tools/mcp_stdio.py`` и к правилам брифа TREND-CU-1:

* физический ввод запрещён: ``--headed`` не передаётся никогда, а действия,
  которые без него не работают, отклоняются с ``ACTION_NOT_ALLOWED_HEADED``;
* действие не повторяется: конверт бинарника отдаётся как есть, включая
  ``disposition.delivery`` и ``disposition.retry``;
* бинарник не скачивается и не устанавливается; закреплена версия 1.0.1,
  версия 1.0.2 на этой машине не работает и намеренно не выбирается;
* значения полей, буфер обмена и текст окон не логируются.

Инструменты: ``desktop_status``, ``list_apps``, ``list_windows``, ``snapshot``,
``find``, ``act``, ``wait_for``, ``screenshot``.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp_stdio import StdioServer, ToolError  # noqa: E402

SERVER_NAME = "desktop"
SERVER_VERSION = "1.0.0"

#: Закреплённая версия: 1.0.2 на этой машине падает с os error 5 (см. бриф, Q5).
PINNED_VERSION = "1.0.1"
#: Имя каталога релиза внутри .tools/agent-desktop/pkg/.
PINNED_DIR = f"v{PINNED_VERSION}"
PINNED_BINARY = "agent-desktop.exe"

#: Корень проекта; вынесен константой, чтобы тесты могли его подменить.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Сколько элементов отдавать в одном снимке.
MAX_NODES = 200
#: Больше этого PNG не вкладываем в ответ — только путь к файлу.
MAX_IMAGE_BYTES = 3 * 1024 * 1024
#: Таймаут одной команды бинарника.
DEFAULT_TIMEOUT_MS = 45000

#: Действия, которые обёртка выполняет без физического ввода.
ALLOWED_ACTIONS = frozenset({"click", "set_value", "press", "toggle", "check", "uncheck", "scroll"})
#: Действия, которым нужен --headed: обёртка их не выполняет никогда.
HEADED_ONLY_ACTIONS = frozenset({
    "type", "double_click", "triple_click", "right_click", "drag", "hover", "focus",
    "mouse_move", "mouse_click", "mouse_wheel",
})

INSTRUCTIONS = (
    "Инструмент desktop: видит и дёргает окна приложений Windows через дерево "
    "доступности. Цикл: snapshot или find → act по ref → снова snapshot для проверки. "
    "Ref живёт один снимок: после изменения интерфейса делай новый снимок. "
    "Действие не повторяй, если в ответе retry=unsafe: оно могло уже сработать. "
    "Физический ввод (--headed) здесь запрещён: для текста используй set_value."
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "desktop_status",
        "description": (
            "Состояние инструмента: какая версия бинарника выбрана, где он лежит, "
            "и какие поверхности поддерживаются. Начни с него."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_apps",
        "description": "Список запущенных приложений с окнами: имя процесса и pid.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_windows",
        "description": "Список окон: id, приложение, заголовок, свёрнуто ли.",
        "inputSchema": {
            "type": "object",
            "properties": {"app": {"type": "string", "description": "фильтр по имени приложения"}},
        },
    },
    {
        "name": "snapshot",
        "description": (
            "Снимок дерева доступности окна: по строке на элемент, с ref для действий. "
            "Результат усекается до max_nodes и помечается truncated, если элементов больше."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "app": {"type": "string", "description": "имя из list_apps, например Notepad.exe"},
                "max_nodes": {"type": "integer", "minimum": 20, "maximum": 500, "default": MAX_NODES},
                "root_ref": {"type": "string", "description": "ref ветки для догрузки"},
                "window_id": {"type": "string", "description": "id окна из list_windows"},
            },
            "required": ["app"],
        },
    },
    {
        "name": "find",
        "description": "Поиск элементов по роли и имени без полного снимка.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "app": {"type": "string"},
                "role": {"type": "string", "description": "например button, textfield, menuitem"},
                "name": {"type": "string", "description": "подстрока имени, регистронезависимо"},
                "exact": {"type": "boolean", "default": False},
            },
            "required": ["app"],
        },
    },
    {
        "name": "act",
        "description": (
            "Действие по ref: click, set_value, press, toggle, check, uncheck, scroll. "
            "Возвращает delivery и retry из ответа бинарника; при retry=unsafe повторять нельзя."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "квалифицированный ref вида @s…:eN"},
                "action": {"type": "string", "enum": sorted(ALLOWED_ACTIONS)},
                "value": {"type": "string", "description": "текст для set_value, комбинация для press"},
            },
            "required": ["ref", "action"],
        },
    },
    {
        "name": "wait_for",
        "description": "Ожидание: пауза в мс, появление текста, окна или меню.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ms": {"type": "integer", "minimum": 1, "maximum": 60000},
                "text": {"type": "string"},
                "window": {"type": "string"},
                "menu": {"type": "boolean"},
                "app": {"type": "string"},
            },
        },
    },
    {
        "name": "screenshot",
        "description": (
            "PNG окна приложения. Файл кладётся в .desktop/; картинка вкладывается в ответ, "
            "только если она не больше 3 МиБ."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "app": {"type": "string"},
                "window_id": {"type": "string"},
                "path": {"type": "string"},
            },
        },
    },
]


# ------------------------------------------------------------------ бинарник


def find_binary() -> str:
    """Найти закреплённый бинарник: DESKTOP_BIN → .tools/…/v1.0.1 → PATH."""
    override = os.environ.get("DESKTOP_BIN")
    if override:
        path = Path(override)
        if path.is_file():
            return str(path)
        raise ToolError(
            "DESKTOP_NOT_INSTALLED: DESKTOP_BIN указывает на несуществующий файл "
            f"{override}"
        )
    pinned = Path(PROJECT_ROOT) / ".tools" / "agent-desktop" / "pkg" / PINNED_DIR / PINNED_BINARY
    if pinned.is_file():
        return str(pinned)
    on_path = shutil.which("agent-desktop")
    if on_path:
        return on_path
    raise ToolError(
        "DESKTOP_NOT_INSTALLED: бинарник agent-desktop не найден. "
        f"Ожидается {pinned} или переменная DESKTOP_BIN. "
        f"Порядок: скачать agent-desktop v{PINNED_VERSION} с "
        "https://github.com/vercel-labs/agent-desktop/releases, свершить sha256 по "
        "checksums.txt того же релиза, распаковать в .tools/agent-desktop/pkg/"
        f"v{PINNED_VERSION}/. Обёртка ничего не скачивает и не устанавливает сама."
    )


def run_agent_desktop(args: list[str], timeout_ms: int = DEFAULT_TIMEOUT_MS) -> dict[str, Any]:
    """Запустить бинарник и вернуть его JSON-конверт. Физический ввод запрещён."""
    for token in args:
        if str(token).strip().lower() == "--headed":
            raise ToolError(
                "ACTION_NOT_ALLOWED_HEADED: физический ввод запрещён. "
                "Используй set_value для текста или подними отдельную задачу с разрешением."
            )
    binary = find_binary()
    command = [binary, *args]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout_ms / 1000 + 15,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"TIMEOUT: agent-desktop не уложился в {timeout_ms} мс") from None
    except OSError as exc:
        raise ToolError(f"DESKTOP_NOT_INSTALLED: не удалось запустить {binary}: {exc}") from None
    text = (proc.stdout or b"").decode("utf-8", "replace").strip()
    if not text:
        raise ToolError(f"ACTION_REFUSED: пустой ответ бинарника (код {proc.returncode})")
    try:
        return json.loads(text.splitlines()[-1])
    except json.JSONDecodeError:
        raise ToolError(
            f"ACTION_REFUSED: ответ бинарника не JSON (код {proc.returncode}): {text[:200]}"
        ) from None


def envelope_error(envelope: dict[str, Any]) -> dict[str, Any]:
    """Свести конверт бинарника к плоскому отчёту о действии."""
    error = envelope.get("error") or {}
    disposition = error.get("disposition") or {}
    return {
        "ok": bool(envelope.get("ok")),
        "code": error.get("code"),
        "message": error.get("message"),
        "delivery": disposition.get("delivery"),
        "retry": disposition.get("retry"),
    }


# ------------------------------------------------------------------- разбор


def count_nodes(tree: Any) -> int:
    """Сколько всего элементов в дереве."""
    if not isinstance(tree, dict):
        return 0
    return 1 + sum(count_nodes(child) for child in (tree.get("children") or []))


def flatten_tree(tree: Any, limit: int) -> list[dict[str, Any]]:
    """Плоский список элементов с ref, обрезанный по limit."""
    rows: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if not isinstance(node, dict) or len(rows) >= limit:
            return
        ref = node.get("ref_id")
        if ref:
            rows.append({
                "ref": ref,
                "role": node.get("role") or "unknown",
                "name": node.get("name") or "",
                "actions": node.get("available_actions") or [],
            })
        for child in node.get("children") or []:
            walk(child)

    walk(tree)
    return rows[:limit]


def format_row(row: dict[str, Any]) -> str:
    name = row["name"].replace("\n", " ").strip() or "(без имени)"
    return f"{row['ref']} | {row['role']} | {name} | {','.join(row['actions'])}"


def screenshot_path(seed: str) -> Path:
    base = Path(os.getcwd()) / ".desktop"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        base = Path(tempfile.gettempdir()) / "dsh-desktop"
        base.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", seed.lower())[:48].strip("-") or "window"
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return base / f"{slug}-{stamp}.png"


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


# ---------------------------------------------------------------- инструменты


def tool_desktop_status(_args: dict[str, Any]) -> str:
    binary = find_binary()
    envelope = run_agent_desktop(["status"], timeout_ms=15000)
    data = envelope.get("data") or {}
    surfaces = data.get("supported_surfaces") or []
    root = data.get("state_root") or "(не сообщён)"
    return (
        f"version={PINNED_VERSION} path={binary} state_root={root} "
        f"surfaces={','.join(surfaces) if surfaces else '—'}"
    )


def tool_list_apps(_args: dict[str, Any]) -> str:
    envelope = run_agent_desktop(["list-apps"])
    report = envelope_error(envelope)
    if not report["ok"]:
        raise ToolError(f"{report['code']}: {report['message']}")
    apps = (envelope.get("data") or {}).get("apps") or []
    if not apps:
        return "приложений с окнами нет"
    return "\n".join(f"{a.get('name') or '(без имени)'} | pid {a.get('pid')}" for a in apps)


def tool_list_windows(args: dict[str, Any]) -> str:
    argv = ["list-windows"]
    app = str(args.get("app") or "").strip()
    if app:
        argv += ["--app", app]
    envelope = run_agent_desktop(argv)
    report = envelope_error(envelope)
    if not report["ok"]:
        raise ToolError(f"{report['code']}: {report['message']}")
    windows = envelope.get("data") or []
    if isinstance(windows, dict):
        windows = windows.get("windows") or []
    if not windows:
        return "окон нет" + (f" для {app}" if app else "")
    lines = []
    for window in windows:
        flags = "свёрнуто" if window.get("minimized") else "видно"
        lines.append(
            f"{window.get('id')} | {window.get('app_name') or '—'} | "
            f"{(window.get('title') or '—')[:80]} | {flags}"
        )
    return "\n".join(lines)


def tool_snapshot(args: dict[str, Any]) -> str:
    app = str(args.get("app") or "").strip()
    if not app:
        raise ToolError("APP_NOT_FOUND: нужен аргумент app")
    limit = _clamp(args.get("max_nodes"), 20, 500, MAX_NODES)
    argv = ["snapshot", "-i", "--compact"]
    if args.get("window_id"):
        argv += ["--window-id", str(args["window_id"])]
    else:
        argv += ["--app", app]
    if args.get("root_ref"):
        argv += ["--root", str(args["root_ref"])]
    envelope = run_agent_desktop(argv)
    report = envelope_error(envelope)
    if not report["ok"]:
        code = report["code"] or "SNAPSHOT_EXPIRED"
        raise ToolError(f"{code}: {report['message']}")
    data = envelope.get("data") or {}
    tree = data.get("tree") or {}
    total = count_nodes(tree)
    rows = flatten_tree(tree, limit)
    truncated = total > len(rows)
    window = data.get("window") or {}
    header = (
        f"app={data.get('app') or app} window={(window.get('title') or '-')[:60]} "
        f"nodes={len(rows)}/{total} truncated={'true' if truncated else 'false'}"
    )
    lines = [header] + [format_row(row) for row in rows]
    if not rows:
        lines.append("(интерактивных элементов нет — попробуй без -i или больший max-depth)")
    return "\n".join(lines)


def tool_find(args: dict[str, Any]) -> str:
    app = str(args.get("app") or "").strip()
    if not app:
        raise ToolError("APP_NOT_FOUND: нужен аргумент app")
    argv = ["find", "--app", app]
    if args.get("role"):
        argv += ["--role", str(args["role"])]
    if args.get("name"):
        argv += ["--name", str(args["name"])]
    if args.get("exact"):
        argv += ["--exact"]
    envelope = run_agent_desktop(argv)
    report = envelope_error(envelope)
    if not report["ok"]:
        raise ToolError(f"{report['code']}: {report['message']}")
    data = envelope.get("data") or {}
    matches = data.get("matches") or []
    lines = []
    for match in matches[:20]:
        ref = match.get("ref_id") or "(без ref)"
        lines.append(f"{ref} | {match.get('role') or '?'} | {match.get('name') or '(без имени)'}")
    total = data.get("total_matches", len(matches))
    lines.append(f"всего={total} показано={len(matches[:20])}")
    return "\n".join(lines)


def tool_act(args: dict[str, Any]) -> str:
    ref = str(args.get("ref") or "").strip()
    action = str(args.get("action") or "").strip().lower()
    value = args.get("value")
    if not ref:
        raise ToolError("STALE_REF: нужен ref из последнего снимка")
    if action in HEADED_ONLY_ACTIONS:
        raise ToolError(
            f"ACTION_NOT_ALLOWED_HEADED: действие {action} требует физического ввода и здесь запрещено. "
            "Для текста используй set_value; для сложного ввода — отдельная задача с разрешением."
        )
    if action not in ALLOWED_ACTIONS:
        raise ToolError(f"ACTION_NOT_SUPPORTED: неизвестное действие {action}")
    if action == "press" and not value:
        raise ToolError("ACTION_REFUSED: для press нужна комбинация, например ctrl+c")
    if action == "set_value" and value is None:
        raise ToolError("ACTION_REFUSED: для set_value нужен текст")

    if action == "click":
        argv = ["click", ref]
    elif action == "set_value":
        argv = ["set-value", ref, str(value)]
    elif action == "press":
        argv = ["press", str(value)]
    elif action == "scroll":
        argv = ["scroll", ref]
    else:
        argv = [action, ref]

    envelope = run_agent_desktop(argv)
    report = envelope_error(envelope)
    if report["ok"]:
        disposition = (envelope.get("data") or {}).get("disposition") or {}
        report["delivery"] = disposition.get("delivery", report["delivery"])
        report["retry"] = disposition.get("retry", report["retry"])
    report["action"] = action
    # Действие не повторяем: клиент обязан посмотреть retry и решить сам.
    return json.dumps(report, ensure_ascii=False)


def tool_wait_for(args: dict[str, Any]) -> str:
    argv = ["wait"]
    if args.get("ms") is not None:
        argv.append(str(_clamp(args.get("ms"), 1, 60000, 1000)))
    if args.get("text"):
        argv += ["--text", str(args["text"])]
    if args.get("window"):
        argv += ["--window", str(args["window"])]
    if args.get("menu"):
        argv.append("--menu")
    if not args.get("ms") and not args.get("text") and not args.get("window") and not args.get("menu"):
        raise ToolError("ACTION_REFUSED: укажи ms, text, window или menu")
    envelope = run_agent_desktop(argv, timeout_ms=60000)
    report = envelope_error(envelope)
    if not report["ok"]:
        raise ToolError(f"{report['code'] or 'TIMEOUT'}: {report['message']}")
    return "условие выполнено"


def tool_screenshot(args: dict[str, Any]) -> Any:
    app = str(args.get("app") or "").strip()
    if not app and not args.get("window_id"):
        raise ToolError("APP_NOT_FOUND: нужен app или window_id")
    path = Path(str(args.get("path"))) if args.get("path") else screenshot_path(app or str(args["window_id"]))
    if not path.is_absolute():
        path = Path(os.getcwd()) / path
    argv = ["screenshot", str(path)]
    if args.get("window_id"):
        argv += ["--window-id", str(args["window_id"])]
    else:
        argv += ["--app", app]
    envelope = run_agent_desktop(argv, timeout_ms=60000)
    report = envelope_error(envelope)
    if not report["ok"]:
        raise ToolError(f"{report['code'] or 'ACTION_REFUSED'}: {report['message']}")
    if not path.is_file():
        raise ToolError(f"ACTION_REFUSED: бинарник не записал PNG в {path}")
    data = path.read_bytes()
    content: list[dict[str, Any]] = [{
        "type": "text",
        "text": f"Скриншот {app or args['window_id']} → {path} ({len(data) // 1024} КиБ)",
    }]
    if len(data) <= MAX_IMAGE_BYTES:
        content.append({
            "type": "image",
            "data": base64.b64encode(data).decode("ascii"),
            "mimeType": "image/png",
        })
    return {"content": content, "isError": False}


HANDLERS = {
    "desktop_status": tool_desktop_status,
    "list_apps": tool_list_apps,
    "list_windows": tool_list_windows,
    "snapshot": tool_snapshot,
    "find": tool_find,
    "act": tool_act,
    "wait_for": tool_wait_for,
    "screenshot": tool_screenshot,
}

SERVER = StdioServer(
    name=SERVER_NAME,
    version=SERVER_VERSION,
    instructions=INSTRUCTIONS,
    tools=TOOLS,
    handlers=HANDLERS,
)


def handle(request: dict[str, Any]) -> None:
    """Обработать один запрос (для тестов и батчей)."""
    SERVER.handle(request)


def main() -> int:
    return SERVER.run()


if __name__ == "__main__":
    raise SystemExit(main())

