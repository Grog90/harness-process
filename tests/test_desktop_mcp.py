#!/usr/bin/env python3
"""Проверки tools/desktop_mcp.py: протокол, выбор бинарника, политика, конверты.

Тесты не требуют установленного agent-desktop и не ходят в сеть: бинарник
подменяется фиктивным ответом, корень проекта — временным каталогом.

Важно: здесь сознательно не используется ``unittest.mock`` — он тянет
``asyncio`` и Winsock, а проверки гоняются в изолированной копии дерева без
сети, где инициализация поставщика Winsock падает с OSError 10106.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import desktop_mcp as dm  # noqa: E402


class patched:
    """Временная подмена атрибута объекта: замена unittest.mock."""

    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.missing = not hasattr(self.obj, self.name)
        self.saved = getattr(self.obj, self.name, None)
        setattr(self.obj, self.name, self.value)
        return self.value

    def __exit__(self, *exc):
        if self.missing:
            try:
                delattr(self.obj, self.name)
            except AttributeError:
                pass
        else:
            setattr(self.obj, self.name, self.saved)
        return False


class temp_env:
    """Временные переменные окружения без mock.patch.dict."""

    def __init__(self, **values):
        self.values = values
        self.saved: dict[str, str | None] = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.saved[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return self

    def __exit__(self, *exc):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return False


def envelope(ok: bool = True, **extra) -> dict:
    return {"version": "2.5", "ok": ok, "command": extra.pop("command", "test"), **extra}


def no_binary(*_args, **_kwargs):
    raise dm.ToolError("DESKTOP_NOT_INSTALLED: нет бинарника")


class ProtocolTests(unittest.TestCase):
    def test_tools_list_without_binary(self):
        """tools/list отдаёт восемь инструментов даже без установленного бинарника."""
        with patched(dm, "find_binary", no_binary):
            reply = []
            with patched(dm.SERVER, "reply", lambda request_id, result: reply.append(result)):
                dm.SERVER.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = [tool["name"] for tool in reply[0]["tools"]]
        self.assertEqual(names, [
            "desktop_status", "list_apps", "list_windows", "snapshot",
            "find", "act", "wait_for", "screenshot",
        ])
        self.assertEqual(len(names), 8)

    def test_initialize(self):
        reply = []
        with patched(dm.SERVER, "reply", lambda request_id, result: reply.append(result)):
            dm.SERVER.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize",
                              "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(reply[0]["serverInfo"]["name"], "desktop")
        self.assertIn("tools", reply[0]["capabilities"])

    def test_unknown_tool(self):
        replied = []
        with patched(dm.SERVER, "reply_error",
                     lambda request_id, code, message: replied.append((code, message))):
            dm.SERVER.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                              "params": {"name": "teleport", "arguments": {}}})
        self.assertEqual(replied[0][0], -32602)
        self.assertIn("teleport", replied[0][1])

    def test_every_tool_has_schema(self):
        for tool in dm.TOOLS:
            self.assertIn("inputSchema", tool)
            self.assertIsInstance(tool["inputSchema"], dict)
            self.assertIn("description", tool)


class BinaryTests(unittest.TestCase):
    def test_missing_binary_is_an_error(self):
        """Без бинарника — DESKTOP_NOT_INSTALLED и никаких попыток скачать."""
        with tempfile.TemporaryDirectory() as tmp:
            with patched(dm, "PROJECT_ROOT", Path(tmp)), \
                 temp_env(DESKTOP_BIN=None), \
                 patched(dm.shutil, "which", lambda *_a, **_k: None):
                with self.assertRaises(dm.ToolError) as caught:
                    dm.find_binary()
        text = str(caught.exception)
        self.assertIn("DESKTOP_NOT_INSTALLED", text)
        self.assertIn("sha256", text)
        self.assertIn("не скачивает", text)

    def test_status_reports_pinned_version(self):
        """В каталоге лежат v1.0.1 и v1.0.2 — выбирается v1.0.1."""
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / ".tools" / "agent-desktop" / "pkg"
            for version in ("v1.0.1", "v1.0.2"):
                folder = pkg / version
                folder.mkdir(parents=True)
                (folder / dm.PINNED_BINARY).write_text("", encoding="utf-8")
            with patched(dm, "PROJECT_ROOT", Path(tmp)), \
                 temp_env(DESKTOP_BIN=None), \
                 patched(dm.shutil, "which", lambda *_a, **_k: None):
                chosen = dm.find_binary()
                self.assertTrue(chosen.endswith(os.path.join("v1.0.1", dm.PINNED_BINARY)), chosen)
                self.assertNotIn("v1.0.2", chosen)
                with patched(dm, "run_agent_desktop", lambda *_a, **_k: envelope(
                        data={"state_root": "C:/state", "supported_surfaces": ["window"]})):
                    report = dm.tool_desktop_status({})
        self.assertIn(f"version={dm.PINNED_VERSION}", report)
        self.assertIn("path=", report)
        self.assertIn("state_root=C:/state", report)

    def test_desktop_bin_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "agent-desktop.exe"
            binary.write_text("", encoding="utf-8")
            with temp_env(DESKTOP_BIN=str(binary)):
                self.assertEqual(dm.find_binary(), str(binary))

    def test_bad_desktop_bin_override(self):
        with temp_env(DESKTOP_BIN="C:/nope/none.exe"):
            with self.assertRaises(dm.ToolError) as caught:
                dm.find_binary()
        self.assertIn("DESKTOP_NOT_INSTALLED", str(caught.exception))


class PolicyTests(unittest.TestCase):
    def test_headed_is_never_passed(self):
        """В аргументах вызова бинарника никогда нет --headed."""
        calls = []

        def fake(args, timeout_ms=dm.DEFAULT_TIMEOUT_MS):
            calls.append(list(args))
            return envelope(data={"disposition": {"delivery": "delivered_verified", "retry": "unsafe"}})

        with patched(dm, "run_agent_desktop", fake):
            dm.tool_act({"ref": "@s1:e1", "action": "click"})
            dm.tool_find({"app": "Notepad.exe", "name": "Файл"})
            dm.tool_snapshot({"app": "Notepad.exe"})
        self.assertTrue(calls)
        for argv in calls:
            self.assertNotIn("--headed", argv)

    def test_direct_headed_flag_refused(self):
        ran = []

        def fake_run(*_args, **_kwargs):
            ran.append(True)

        with patched(dm, "find_binary", lambda: "agent-desktop.exe"), \
             patched(dm.subprocess, "run", fake_run):
            with self.assertRaises(dm.ToolError) as caught:
                dm.run_agent_desktop(["snapshot", "--app", "Notepad.exe", "--headed"])
        self.assertIn("ACTION_NOT_ALLOWED_HEADED", str(caught.exception))
        self.assertFalse(ran)

    def test_type_refused_with_explanation(self):
        calls = []

        def fake(args, timeout_ms=dm.DEFAULT_TIMEOUT_MS):
            calls.append(args)
            return envelope()

        with patched(dm, "run_agent_desktop", fake):
            with self.assertRaises(dm.ToolError) as caught:
                dm.tool_act({"ref": "@s1:e1", "action": "type", "value": "привет"})
        self.assertIn("ACTION_NOT_ALLOWED_HEADED", str(caught.exception))
        self.assertIn("set_value", str(caught.exception))
        self.assertFalse(calls)

    def test_unknown_action_refused(self):
        with self.assertRaises(dm.ToolError) as caught:
            dm.tool_act({"ref": "@s1:e1", "action": "levitate"})
        self.assertIn("ACTION_NOT_SUPPORTED", str(caught.exception))

    def test_act_requires_ref(self):
        with self.assertRaises(dm.ToolError) as caught:
            dm.tool_act({"action": "click"})
        self.assertIn("STALE_REF", str(caught.exception))


class EnvelopeTests(unittest.TestCase):
    def test_disposition_is_passed_through(self):
        """delivery и retry уходят клиенту без изменений."""
        payload = envelope(data={"disposition": {"delivery": "delivered_unverified", "retry": "unsafe"}})
        calls = []

        def fake(args, timeout_ms=dm.DEFAULT_TIMEOUT_MS):
            calls.append(args)
            return payload

        with patched(dm, "run_agent_desktop", fake):
            result = json.loads(dm.tool_act({"ref": "@s1:e1", "action": "click"}))
        self.assertTrue(result["ok"])
        self.assertEqual(result["delivery"], "delivered_unverified")
        self.assertEqual(result["retry"], "unsafe")
        self.assertEqual(result["action"], "click")
        self.assertEqual(len(calls), 1)

    def test_unsafe_is_not_retried(self):
        """Обёртка не повторяет действие: один вызов на одно действие."""
        payload = envelope(ok=False, error={
            "code": "ACTION_FAILED",
            "message": "не подтвердилось",
            "disposition": {"delivery": "delivered_unverified", "retry": "unsafe"},
        })
        calls = []

        def fake(args, timeout_ms=dm.DEFAULT_TIMEOUT_MS):
            calls.append(args)
            return payload

        with patched(dm, "run_agent_desktop", fake):
            result = json.loads(dm.tool_act({"ref": "@s1:e1", "action": "set_value", "value": "x"}))
        self.assertEqual(len(calls), 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "ACTION_FAILED")
        self.assertEqual(result["retry"], "unsafe")

    def test_error_envelope_has_fields(self):
        report = dm.envelope_error(envelope(ok=False, error={
            "code": "STALE_REF", "message": "нет в текущем RefMap",
            "disposition": {"delivery": "not_delivered", "retry": "safe"},
        }))
        self.assertEqual(set(report), {"ok", "code", "message", "delivery", "retry"})
        self.assertFalse(report["ok"])
        self.assertEqual(report["retry"], "safe")

    def test_success_carries_data_disposition(self):
        report = dm.envelope_error(envelope(data={"disposition": {
            "delivery": "delivered_verified", "retry": "unsafe"}}))
        self.assertIsNone(report["delivery"])
        self.assertIsNone(report["code"])


class SnapshotTests(unittest.TestCase):
    @staticmethod
    def big_tree(count: int) -> dict:
        return {
            "role": "window", "name": "root", "ref_id": "@s1:e0",
            "children": [
                {"role": "button", "name": f"кнопка {i}", "ref_id": f"@s1:e{i + 1}",
                 "available_actions": ["Click"]}
                for i in range(count)
            ],
        }

    def test_truncation_is_marked(self):
        tree = self.big_tree(1000)
        payload = envelope(data={"app": "Notepad.exe", "window": {"title": "Блокнот"}, "tree": tree})
        with patched(dm, "run_agent_desktop", lambda *a, **k: payload):
            text = dm.tool_snapshot({"app": "Notepad.exe", "max_nodes": 50})
        header = text.splitlines()[0]
        self.assertIn("nodes=50/1001", header)
        self.assertIn("truncated=true", header)
        self.assertEqual(len(text.splitlines()), 1 + 50)

    def test_small_tree_not_truncated(self):
        tree = self.big_tree(3)
        payload = envelope(data={"app": "Notepad.exe", "window": {"title": "Блокнот"}, "tree": tree})
        with patched(dm, "run_agent_desktop", lambda *a, **k: payload):
            text = dm.tool_snapshot({"app": "Notepad.exe"})
        self.assertIn("truncated=false", text.splitlines()[0])

    def test_snapshot_requires_app(self):
        with self.assertRaises(dm.ToolError) as caught:
            dm.tool_snapshot({})
        self.assertIn("APP_NOT_FOUND", str(caught.exception))

    def test_no_interactive_elements_message(self):
        payload = envelope(data={"app": "Notepad.exe", "tree": {"role": "window", "name": "пусто"}})
        with patched(dm, "run_agent_desktop", lambda *a, **k: payload):
            text = dm.tool_snapshot({"app": "Notepad.exe"})
        self.assertIn("nodes=0/1", text)
        self.assertIn("интерактивных элементов нет", text)

    def test_count_and_flatten(self):
        tree = self.big_tree(5)
        self.assertEqual(dm.count_nodes(tree), 6)
        rows = dm.flatten_tree(tree, 3)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["ref"], "@s1:e0")
        self.assertEqual(rows[1]["role"], "button")


class FindTests(unittest.TestCase):
    def test_find_lines_and_total(self):
        payload = envelope(data={"snapshot_id": "s1", "total_matches": 3, "matches": [
            {"ref_id": "@s1:e1", "role": "menuitem", "name": "Файл"},
            {"ref_id": "@s1:e2", "role": "button", "name": "Файл"},
        ]})
        calls = []

        def fake(args, timeout_ms=dm.DEFAULT_TIMEOUT_MS):
            calls.append(args)
            return payload

        with patched(dm, "run_agent_desktop", fake):
            text = dm.tool_find({"app": "Notepad.exe", "name": "Файл"})
        self.assertEqual(text.splitlines()[0], "@s1:e1 | menuitem | Файл")
        self.assertIn("всего=3 показано=2", text.splitlines()[-1])
        self.assertEqual(calls[0][:3], ["find", "--app", "Notepad.exe"])

    def test_find_requires_app(self):
        with self.assertRaises(dm.ToolError) as caught:
            dm.tool_find({"name": "Файл"})
        self.assertIn("APP_NOT_FOUND", str(caught.exception))


class ScreenshotTests(unittest.TestCase):
    def _run(self, size: int) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shot.png"
            path.write_bytes(b"\x89PNG" + b"0" * (size - 4))
            with patched(dm, "run_agent_desktop", lambda *a, **k: envelope(data={"format": "png"})):
                return dm.tool_screenshot({"app": "Notepad.exe", "path": str(path)})

    def test_big_png_is_path_only(self):
        result = self._run(dm.MAX_IMAGE_BYTES + 1)
        self.assertEqual(len(result["content"]), 1)
        self.assertIn("Скриншот", result["content"][0]["text"])
        self.assertFalse(result["isError"])

    def test_small_png_is_inline(self):
        result = self._run(1024)
        self.assertEqual(len(result["content"]), 2)
        self.assertEqual(result["content"][1]["mimeType"], "image/png")
        self.assertGreater(len(result["content"][1]["data"]), 0)

    def test_missing_file_is_error(self):
        with patched(dm, "run_agent_desktop", lambda *a, **k: envelope(data={})):
            with self.assertRaises(dm.ToolError) as caught:
                dm.tool_screenshot({"app": "Notepad.exe",
                                    "path": tempfile.gettempdir() + "/нет-такого.png"})
        self.assertIn("ACTION_REFUSED", str(caught.exception))


class ListTests(unittest.TestCase):
    def test_list_apps_output(self):
        payload = envelope(data={"apps": [
            {"name": "Notepad.exe", "pid": 42},
            {"name": "", "pid": 7},
        ]})
        with patched(dm, "run_agent_desktop", lambda *a, **k: payload):
            text = dm.tool_list_apps({})
        self.assertIn("Notepad.exe | pid 42", text)
        self.assertIn("(без имени) | pid 7", text)

    def test_list_windows_output(self):
        payload = envelope(data=[{"id": "w-1", "app_name": "Notepad.exe",
                                  "title": "Блокнот", "minimized": True}])
        with patched(dm, "run_agent_desktop", lambda *a, **k: payload):
            text = dm.tool_list_windows({})
        self.assertIn("w-1 | Notepad.exe | Блокнот | свёрнуто", text)

    def test_list_windows_app_filter(self):
        calls = []

        def fake(args, timeout_ms=dm.DEFAULT_TIMEOUT_MS):
            calls.append(args)
            return envelope(data=[])

        with patched(dm, "run_agent_desktop", fake):
            text = dm.tool_list_windows({"app": "Notepad.exe"})
        self.assertIn("--app", calls[0])
        self.assertIn("окон нет для Notepad.exe", text)


class WaitTests(unittest.TestCase):
    def test_wait_requires_condition(self):
        with self.assertRaises(dm.ToolError) as caught:
            dm.tool_wait_for({})
        self.assertIn("ACTION_REFUSED", str(caught.exception))

    def test_wait_passes_flags(self):
        calls = []

        def fake(args, timeout_ms=dm.DEFAULT_TIMEOUT_MS):
            calls.append(args)
            return envelope(data={})

        with patched(dm, "run_agent_desktop", fake):
            dm.tool_wait_for({"ms": 500, "text": "готово"})
        argv = calls[0]
        self.assertEqual(argv[0], "wait")
        self.assertIn("500", argv)
        self.assertIn("--text", argv)


class DocsTests(unittest.TestCase):
    def test_doc_covers_limits(self):
        doc = (PROJECT_ROOT / "docs" / "DESKTOP.md").read_text(encoding="utf-8")
        for needle in ("sha256", "1.0.2", "1.0.1", "UAC", "не подписан",
                       "cordis.patch.yml", "SNAPSHOT_NOT_FOUND"):
            self.assertIn(needle, doc, f"в docs/DESKTOP.md нет {needle!r}")

    def test_skill_mentions_loop(self):
        skill = (PROJECT_ROOT / ".dsh" / "skills" / "desktop" / "SKILL.md").read_text(encoding="utf-8")
        for needle in ("snapshot", "ref", "retry", "set_value"):
            self.assertIn(needle, skill, f"в SKILL.md нет {needle!r}")

    def test_doc_names_working_binary_path(self):
        """Документация требует короткий ASCII-путь и переменную DESKTOP_BIN."""
        doc = (PROJECT_ROOT / "docs" / "DESKTOP.md").read_text(encoding="utf-8")
        self.assertIn(r"C:\Tools\agent-desktop", doc)
        self.assertIn("DESKTOP_BIN", doc)
        self.assertIn("кириллиц", doc.lower())
        self.assertNotIn("распаковать в .tools/agent-desktop/pkg/v1.0.1/", doc)

    def test_no_network_modules(self):
        source = (PROJECT_ROOT / "tools" / "desktop_mcp.py").read_text(encoding="utf-8")
        for forbidden in ("urllib", "requests", "httpx", "socket"):
            self.assertNotIn(forbidden, source, f"desktop_mcp.py тянет сеть: {forbidden}")


if __name__ == "__main__":
    unittest.main()
