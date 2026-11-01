"""Проверки tools/browser_mcp.py: сборка argv, поиск бинарника, инструменты, протокол."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import browser_mcp as bm  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def call(payload: dict) -> list[dict]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        bm.handle(payload)
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def tool_call(name: str, arguments: dict) -> dict:
    [reply] = call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": name, "arguments": arguments}})
    return reply["result"]


class FetchArgsTest(unittest.TestCase):
    def test_defaults_block_private_networks(self):
        args = bm.build_fetch_args("https://x.test/a", dump="markdown", wait="done",
                                   selector=None, timeout_ms=45000, allow_private=False)
        self.assertEqual(args[0], "fetch")
        self.assertIn("--dump", args)
        self.assertIn("markdown", args)
        self.assertIn("--block-private-networks", args)
        self.assertEqual(args[-1], "https://x.test/a")
        self.assertEqual(args[args.index("-t") + 1], "45000")

    def test_selector_and_allow_private(self):
        args = bm.build_fetch_args("http://x.test", dump=None, wait="networkidle",
                                   selector="main article", timeout_ms=5000, allow_private=True)
        self.assertNotIn("--dump", args)
        self.assertEqual(args[args.index("--wait") + 1], "networkidle")
        self.assertEqual(args[args.index("--wait-selector") + 1], "main article")
        self.assertNotIn("--block-private-networks", args)

    def test_rejects_non_http(self):
        for bad in ("file:///etc/passwd", "javascript:alert(1)", "", "example.com"):
            with self.assertRaises(bm.ToolError):
                bm.build_fetch_args(bad, dump="markdown", wait="done", selector=None,
                                    timeout_ms=1000, allow_private=False)


class BinaryTest(unittest.TestCase):
    def test_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "moli.exe"
            binary.write_bytes(b"stub")
            with unittest.mock.patch.dict(os.environ, {"MOLI_BIN": str(binary)}):
                self.assertEqual(bm.find_binary(), str(binary))

    def test_missing_binary_reports_paths(self):
        with unittest.mock.patch.dict(os.environ, {"MOLI_BIN": "", "LOCALAPPDATA": "/nope"}), \
                unittest.mock.patch.object(bm.shutil, "which", return_value=None), \
                unittest.mock.patch.object(Path, "glob", return_value=[]):
            with self.assertRaises(bm.ToolError):
                bm.find_binary()


class TextTest(unittest.TestCase):
    def test_clean_drops_data_uri_images(self):
        raw = "до ![медаль](data:image/svg+xml;base64,AAAA) после\n\n\n\nещё"
        cleaned = bm.clean_text(raw)
        self.assertNotIn("data:image", cleaned)
        self.assertIn("до", cleaned)
        self.assertNotIn("\n\n\n", cleaned)

    def test_truncate_marks_cut(self):
        text = "x" * 100
        self.assertEqual(bm.truncate(text, 10)[:10], "x" * 10)
        self.assertIn("обрезано", bm.truncate(text, 10))
        self.assertEqual(bm.truncate("short", 10), "short")

    def test_clamp(self):
        self.assertEqual(bm._clamp("5000", 1000, 200000, 0), 5000)
        self.assertEqual(bm._clamp(None, 1000, 200000, 7), 7)
        self.assertEqual(bm._clamp("bad", 1000, 200000, 9), 9)


class FetchToolTest(unittest.TestCase):
    def setUp(self):
        bm.cache_clear()

    def tearDown(self):
        bm.cache_clear()

    def test_fetch_returns_clean_text(self):
        stdout = "  \n\n\nзаголовок\n\n\n\nтекст  ".encode()
        with unittest.mock.patch.object(bm, "run_moli", return_value=(0, stdout, "")):
            self.assertEqual(bm.tool_fetch_page({"url": "https://x.test"}), "заголовок\n\nтекст")

    def test_fetch_passes_format_and_selector(self):
        captured = {}

        def fake(argv, _timeout):
            captured["argv"] = argv
            return 0, b"ok", ""

        with unittest.mock.patch.object(bm, "run_moli", side_effect=fake):
            bm.tool_fetch_page({"url": "https://x.test", "format": "semantic",
                                "selector": "main", "timeout_ms": 7000})
        self.assertIn("semantic_tree_text", captured["argv"])
        self.assertIn("main", captured["argv"])
        self.assertEqual(captured["argv"][captured["argv"].index("-t") + 1], "7000")

    def test_fetch_failure_reports_stderr(self):
        with unittest.mock.patch.object(bm, "run_moli", return_value=(1, b"", "boom")):
            with self.assertRaises(bm.ToolError) as ctx:
                bm.tool_fetch_page({"url": "https://x.test"})
        self.assertIn("boom", str(ctx.exception))

    def test_fetch_empty_output_is_error(self):
        with unittest.mock.patch.object(bm, "run_moli", return_value=(0, b"   ", "")):
            with self.assertRaises(bm.ToolError):
                bm.tool_fetch_page({"url": "https://x.test"})

    def test_short_answer_is_retried_with_networkidle(self):
        calls = []

        def fake(argv, _timeout):
            calls.append(argv[argv.index("--wait") + 1])
            if len(calls) == 1:
                return 0, b"shell only", ""
            return 0, b"x" * 3000, ""

        with unittest.mock.patch.object(bm, "run_moli", side_effect=fake):
            text = bm.tool_fetch_page({"url": "https://x.test"})
        self.assertEqual(calls, ["done", "networkidle"])
        self.assertEqual(len(text), 3000)

    def test_long_answer_is_not_retried(self):
        calls = []

        def fake(argv, _timeout):
            calls.append(argv[argv.index("--wait") + 1])
            return 0, b"y" * 3000, ""

        with unittest.mock.patch.object(bm, "run_moli", side_effect=fake):
            bm.tool_fetch_page({"url": "https://x.test"})
        self.assertEqual(calls, ["done"])

    def test_selector_disables_retry(self):
        calls = []

        def fake(argv, _timeout):
            calls.append(argv)
            return 0, b"short", ""

        with unittest.mock.patch.object(bm, "run_moli", side_effect=fake):
            bm.tool_fetch_page({"url": "https://x.test", "selector": "main"})
        self.assertEqual(len(calls), 1)


class EvalToolTest(unittest.TestCase):
    def test_eval_has_no_dump(self):
        captured = {}

        def fake(argv, _timeout):
            captured["argv"] = argv
            return 0, b'"hello"', ""

        with unittest.mock.patch.object(bm, "run_moli", side_effect=fake):
            out = bm.tool_page_eval({"url": "https://x.test", "expression": "document.title"})
        self.assertNotIn("--dump", captured["argv"])
        self.assertEqual(captured["argv"][captured["argv"].index("--eval") + 1], "document.title")
        self.assertEqual(out, '"hello"')

    def test_eval_requires_expression(self):
        with self.assertRaises(bm.ToolError):
            bm.tool_page_eval({"url": "https://x.test", "expression": "  "})


class ScreenshotToolTest(unittest.TestCase):
    def test_moli_engine_saves_png_and_returns_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "shot.png"
            with unittest.mock.patch.object(bm, "run_moli", return_value=(0, PNG, "")), \
                    unittest.mock.patch.object(bm, "_screenshot_path", return_value=target):
                result = bm.tool_page_screenshot({"url": "https://x.test", "engine": "moli"})
            self.assertTrue(target.is_file())
        types = [item["type"] for item in result["content"]]
        self.assertEqual(types, ["text", "image"])
        self.assertEqual(base64.b64decode(result["content"][1]["data"]), PNG)
        self.assertIn("moli", result["content"][0]["text"])

    def test_non_png_is_error(self):
        with unittest.mock.patch.object(bm, "run_moli", return_value=(0, b"<html>", "")), \
                unittest.mock.patch.object(bm, "_screenshot_path", return_value=Path("x.png")):
            with self.assertRaises(bm.ToolError):
                bm.moli_screenshot("https://x.test", Path("x.png"), False, 5000, False)

    def test_auto_uses_chrome_for_cjk_page(self):
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.object(bm, "find_chrome", return_value="chrome.exe"), \
                unittest.mock.patch.object(bm, "page_has_cjk", return_value=True), \
                unittest.mock.patch.object(bm, "chrome_screenshot", return_value=PNG) as chrome, \
                unittest.mock.patch.object(bm, "moli_screenshot") as moli, \
                unittest.mock.patch.object(bm, "_screenshot_path", return_value=Path(tmp) / "s.png"):
            result = bm.tool_page_screenshot({"url": "https://x.test"})
        chrome.assert_called_once()
        moli.assert_not_called()
        self.assertIn("chrome", result["content"][0]["text"])

    def test_auto_uses_moli_for_plain_page(self):
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.object(bm, "find_chrome", return_value="chrome.exe"), \
                unittest.mock.patch.object(bm, "page_has_cjk", return_value=False), \
                unittest.mock.patch.object(bm, "chrome_screenshot") as chrome, \
                unittest.mock.patch.object(bm, "moli_screenshot", return_value=PNG) as moli, \
                unittest.mock.patch.object(bm, "_screenshot_path", return_value=Path(tmp) / "s.png"):
            result = bm.tool_page_screenshot({"url": "https://x.test"})
        moli.assert_called_once()
        chrome.assert_not_called()
        self.assertIn("moli", result["content"][0]["text"])

    def test_cjk_detection(self):
        self.assertTrue(bm.CJK_RE.search("关掉国产安卓手机系统"))
        self.assertTrue(bm.CJK_RE.search("日本語のテキスト"))
        self.assertFalse(bm.CJK_RE.search("plain english text"))

    def test_auto_full_page_prefers_moli(self):
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.object(bm, "find_chrome", return_value="chrome.exe"), \
                unittest.mock.patch.object(bm, "chrome_screenshot") as chrome, \
                unittest.mock.patch.object(bm, "moli_screenshot", return_value=PNG) as moli, \
                unittest.mock.patch.object(bm, "_screenshot_path", return_value=Path(tmp) / "s.png"):
            result = bm.tool_page_screenshot({"url": "https://x.test", "full": True})
        moli.assert_called_once()
        chrome.assert_not_called()
        self.assertIn("moli", result["content"][0]["text"])

    def test_chrome_failure_falls_back_to_moli(self):
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.object(bm, "find_chrome", return_value="chrome.exe"), \
                unittest.mock.patch.object(bm, "page_has_cjk", return_value=True), \
                unittest.mock.patch.object(bm, "chrome_screenshot", side_effect=bm.ToolError("нет")), \
                unittest.mock.patch.object(bm, "moli_screenshot", return_value=PNG), \
                unittest.mock.patch.object(bm, "_screenshot_path", return_value=Path(tmp) / "s.png"):
            result = bm.tool_page_screenshot({"url": "https://x.test"})
        self.assertIn("moli", result["content"][0]["text"])

    def test_engine_moli_never_calls_chrome(self):
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.object(bm, "find_chrome", return_value="chrome.exe"), \
                unittest.mock.patch.object(bm, "chrome_screenshot") as chrome, \
                unittest.mock.patch.object(bm, "moli_screenshot", return_value=PNG), \
                unittest.mock.patch.object(bm, "_screenshot_path", return_value=Path(tmp) / "s.png"):
            bm.tool_page_screenshot({"url": "https://x.test", "engine": "moli"})
        chrome.assert_not_called()


class CacheTest(unittest.TestCase):
    def setUp(self):
        bm.cache_clear()

    def tearDown(self):
        bm.cache_clear()

    def test_second_call_uses_cache(self):
        calls = []

        def fake(_argv, _timeout):
            calls.append(1)
            return 0, b"z" * 2000, ""

        with unittest.mock.patch.object(bm, "run_moli", side_effect=fake):
            bm.tool_fetch_page({"url": "https://cache.test/a"})
            bm.tool_fetch_page({"url": "https://cache.test/a"})
        self.assertEqual(len(calls), 1)

    def test_fresh_bypasses_cache(self):
        calls = []

        def fake(_argv, _timeout):
            calls.append(1)
            return 0, b"z" * 2000, ""

        with unittest.mock.patch.object(bm, "run_moli", side_effect=fake):
            bm.tool_fetch_page({"url": "https://cache.test/b"})
            bm.tool_fetch_page({"url": "https://cache.test/b", "fresh": True})
        self.assertEqual(len(calls), 2)

    def test_different_params_different_entries(self):
        calls = []

        def fake(_argv, _timeout):
            calls.append(1)
            return 0, b"z" * 2000, ""

        with unittest.mock.patch.object(bm, "run_moli", side_effect=fake):
            bm.tool_fetch_page({"url": "https://cache.test/c"})
            bm.tool_fetch_page({"url": "https://cache.test/c", "format": "html"})
        self.assertEqual(len(calls), 2)

    def test_expired_entry_is_refetched(self):
        bm.cache_put(bm.cache_key("https://cache.test/d", "markdown", "done", None, None, False), "old")
        with unittest.mock.patch.object(bm.time, "time", return_value=time.time() + bm.CACHE_TTL_S + 1):
            self.assertIsNone(bm.cache_get(
                bm.cache_key("https://cache.test/d", "markdown", "done", None, None, False)))


class InteractionTest(unittest.TestCase):
    def setUp(self):
        bm.cache_clear()

    def test_eval_is_passed_before_url_and_dumps_page(self):
        captured = {}

        def fake(argv, _timeout):
            captured["argv"] = argv
            return 0, "после клика".encode(), ""

        with unittest.mock.patch.object(bm, "run_moli", side_effect=fake):
            out = bm.tool_fetch_page({"url": "https://x.test/app",
                                      "eval": "document.querySelector('.more').click()"})
        argv = captured["argv"]
        self.assertIn("--eval", argv)
        self.assertLess(argv.index("--eval"), argv.index("https://x.test/app"))
        self.assertIn("--dump", argv)
        self.assertEqual(out, "после клика")

    def test_eval_skips_chrome_fallback(self):
        with unittest.mock.patch.object(bm, "run_moli", return_value=(0, b"short", "")), \
                unittest.mock.patch.object(bm, "run_chrome") as chrome:
            bm.tool_fetch_page({"url": "https://x.test/e", "eval": "1"})
        chrome.assert_not_called()


class SessionTest(unittest.TestCase):
    def setUp(self):
        bm.SESSION["proc"] = None
        bm.SESSION["port"] = None

    def tearDown(self):
        bm.SESSION["proc"] = None
        bm.SESSION["port"] = None

    def test_status_without_session(self):
        self.assertIn("не запущена", bm.tool_session_status({}))

    def test_start_returns_endpoint(self):
        proc = unittest.mock.Mock()
        proc.poll.return_value = None
        with unittest.mock.patch.object(bm.subprocess, "Popen", return_value=proc) as popen, \
                unittest.mock.patch.object(bm, "session_probe", return_value={"Browser": "moli/1.1.19"}):
            out = bm.tool_session_start({"port": 9333})
        self.assertIn("9333", out)
        self.assertIn("connectOverCDP", out)
        args = popen.call_args[0][0]
        self.assertIn("serve", args)
        self.assertIn("--layout", args)

    def test_start_is_idempotent(self):
        proc = unittest.mock.Mock()
        proc.poll.return_value = None
        bm.SESSION.update({"proc": proc, "port": 9222})
        with unittest.mock.patch.object(bm.subprocess, "Popen") as popen:
            out = bm.tool_session_start({})
        popen.assert_not_called()
        self.assertIn("уже работает", out)

    def test_start_fails_when_cdp_never_answers(self):
        proc = unittest.mock.Mock()
        proc.poll.return_value = None
        with unittest.mock.patch.object(bm.subprocess, "Popen", return_value=proc), \
                unittest.mock.patch.object(bm, "session_probe", return_value=None), \
                unittest.mock.patch.object(bm, "tool_session_stop", return_value="ok") as stop, \
                unittest.mock.patch.object(bm.time, "time", side_effect=[0, 0, 20]):
            with self.assertRaises(bm.ToolError):
                bm.tool_session_start({})
        stop.assert_called_once()

    def test_start_reports_immediate_exit(self):
        proc = unittest.mock.Mock()
        proc.poll.return_value = 9
        proc.returncode = 9
        with unittest.mock.patch.object(bm.subprocess, "Popen", return_value=proc):
            with self.assertRaises(bm.ToolError):
                bm.tool_session_start({})

    def test_stop_terminates(self):
        proc = unittest.mock.Mock()
        proc.poll.return_value = None
        bm.SESSION.update({"proc": proc, "port": 9222})
        out = bm.tool_session_stop({})
        proc.terminate.assert_called_once()
        self.assertIn("остановлена", out)


class ProtocolTest(unittest.TestCase):
    def test_tools_list(self):
        [reply] = call({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual([t["name"] for t in reply["result"]["tools"]],
                         ["fetch_page", "page_screenshot", "page_eval",
                          "session_start", "session_status", "session_stop"])

    def test_unknown_tool(self):
        [reply] = call({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "nope", "arguments": {}}})
        self.assertEqual(reply["error"]["code"], -32602)

    def test_tool_error_becomes_is_error(self):
        def failing(_args):
            raise bm.ToolError("нет сети")

        with unittest.mock.patch.dict(bm.HANDLERS, {"fetch_page": failing}):
            result = tool_call("fetch_page", {})
        self.assertTrue(result["isError"])
        self.assertIn("нет сети", result["content"][0]["text"])

    def test_handler_exception_is_contained(self):
        def boom(_args):
            raise RuntimeError("нежданчик")

        with unittest.mock.patch.dict(bm.HANDLERS, {"fetch_page": boom}):
            result = tool_call("fetch_page", {})
        self.assertTrue(result["isError"])
        self.assertIn("нежданчик", result["content"][0]["text"])


class ChromeFallbackTest(unittest.TestCase):
    def test_private_urls(self):
        for url in ("http://localhost:3000/x", "http://127.0.0.1/", "http://192.168.1.5/",
                    "http://[::1]/", "http://box.local/"):
            self.assertTrue(bm.is_private_url(url), url)
        for url in ("https://example.com", "http://8.8.8.8/", "https://sub.example.org/a"):
            self.assertFalse(bm.is_private_url(url), url)

    def test_html_to_text_drops_scripts_and_keeps_blocks(self):
        html = ("<html><head><title>t</title><style>body{color:red}</style></head>"
                "<body><h1>Заголовок</h1><script>var x=1</script>"
                "<p>Первый</p><p>Второй</p><noscript>нет</noscript></body></html>")
        text = bm.html_to_text(html)
        self.assertIn("Заголовок", text)
        self.assertIn("Первый\nВторой", text)
        for junk in ("var x=1", "color:red", "нет", "<p>"):
            self.assertNotIn(junk, text)

    def test_fallback_used_when_longer(self):
        with unittest.mock.patch.object(bm, "run_chrome",
                                        return_value="<html><body><p>" + "слово " * 400 + "</p></body></html>"):
            out = bm.chrome_fallback("https://example.com", "markdown", "мало", 1000, False)
        self.assertIn("слово", out)
        self.assertIn("источник: Chrome", out)

    def test_fallback_ignored_when_shorter(self):
        with unittest.mock.patch.object(bm, "run_chrome", return_value="<html><body><p>ok</p></body></html>"):
            out = bm.chrome_fallback("https://example.com", "markdown", "x" * 500, 1000, False)
        self.assertEqual(out, "x" * 500)

    def test_fallback_skips_private_without_permission(self):
        with unittest.mock.patch.object(bm, "run_chrome") as chrome:
            out = bm.chrome_fallback("http://localhost:8080/", "markdown", "мало", 1000, False)
        chrome.assert_not_called()
        self.assertEqual(out, "мало")

    def test_fallback_swallows_chrome_errors(self):
        with unittest.mock.patch.object(bm, "run_chrome", side_effect=bm.ToolError("нет chrome")):
            self.assertEqual(bm.chrome_fallback("https://example.com", "markdown", "мало", 1000, False), "мало")

    def test_find_chrome_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "chrome.exe"
            binary.write_bytes(b"stub")
            with unittest.mock.patch.dict(os.environ, {"CHROME_BIN": str(binary)}):
                self.assertEqual(bm.find_chrome(), str(binary))

    def test_fetch_page_uses_chrome_when_moli_is_thin(self):
        calls = {"moli": 0}

        def fake_moli(_argv, _timeout):
            calls["moli"] += 1
            return 0, b"shell", ""

        big_dom = "<html><body><p>" + "контент " * 600 + "</p></body></html>"
        with unittest.mock.patch.object(bm, "run_moli", side_effect=fake_moli), \
                unittest.mock.patch.object(bm, "run_chrome", return_value=big_dom):
            out = bm.tool_fetch_page({"url": "https://example.com"})
        self.assertIn("контент", out)
        self.assertIn("источник: Chrome", out)
        self.assertGreaterEqual(calls["moli"], 1)


if __name__ == "__main__":
    unittest.main()
