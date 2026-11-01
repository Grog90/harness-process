"""Проверки tools/trendshift.py и tools/trendshift_mcp.py: разбор, дифф, хранилище, протокол."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import trendshift as ts  # noqa: E402
import trendshift_mcp as mcp  # noqa: E402


def flight_html(payload: dict, extra: str = "") -> str:
    """Собрать минимальную страницу с RSC-потоком, как у Next.js."""
    text = "7:" + json.dumps(payload, ensure_ascii=False) + extra
    return "<html><script>self.__next_f.push([1," + json.dumps(text, ensure_ascii=False) + "])</script></html>"


def item(name: str, rank: int, **kwargs) -> dict:
    base = {
        "full_name": name,
        "rank": rank,
        "score": 1000 - rank,
        "repository_stars": 100,
        "repository_stars_gained": 50,
        "repository_forks": 10,
        "repository_forks_gained": 1,
        "repository_language": "Python",
        "repository_description": "an AI agent framework",
        "repository_created_at": "2026-10-01T00:00:00Z",
        "tags": [{"name": "ai-agent"}],
        "social_mention_platforms": ["x"],
    }
    base.update(kwargs)
    return base


class ParsingTest(unittest.TestCase):
    def test_decode_flight_unescapes(self):
        html = flight_html({"a": "b"})
        self.assertIn('{"a": "b"}', ts.decode_flight(html))

    def test_decode_flight_without_stream(self):
        with self.assertRaises(ts.TrendshiftError):
            ts.decode_flight("<html>nothing</html>")

    def test_extract_json_ignores_quotes_and_braces_inside_strings(self):
        text = 'o:{"initialData":[{"d":"a ] } b","n":1}],"other":{"x":[1,2]}}'
        self.assertEqual(ts.extract_json(text, "initialData"), [{"d": "a ] } b", "n": 1}])

    def test_parse_period_normalizes(self):
        payload = {"initialData": [item("owner/repo", 3)]}
        rows = ts.parse_period(flight_html(payload), "weekly", fetched_at="2026-10-26T00:00:00Z")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["full_name"], "owner/repo")
        self.assertEqual(row["url"], "https://github.com/owner/repo")
        self.assertEqual(row["tags"], ["ai-agent"])
        self.assertEqual(row["period"], "weekly")
        self.assertEqual(row["stars"], 100)

    def test_parse_period_drops_nameless(self):
        payload = {"initialData": [item("", 1), item("ok/name", 2)]}
        rows = ts.parse_period(flight_html(payload), "daily")
        self.assertEqual([r["full_name"] for r in rows], ["ok/name"])

    def test_parse_period_without_data(self):
        with self.assertRaises(ts.TrendshiftError):
            ts.parse_period(flight_html({"nope": 1}), "daily")

    def test_unknown_period(self):
        with self.assertRaises(ts.TrendshiftError):
            ts.fetch_period("hourly")


class SelectTest(unittest.TestCase):
    def setUp(self):
        self.rows = [
            ts.normalize(item("a/ai", 1), "daily", "t"),
            ts.normalize(item("b/cli", 2, repository_description="command line tool",
                              tags=[], repository_language="Go", repository_stars_gained=5), "daily", "t"),
            ts.normalize(item("c/ai", 3, repository_stars_gained=5), "daily", "t"),
        ]

    def test_ai_only(self):
        names = [r["full_name"] for r in ts.select(self.rows, ai_only=True)]
        self.assertEqual(names, ["a/ai", "c/ai"])

    def test_language_and_limit(self):
        rows = ts.select(self.rows, language="go")
        self.assertEqual([r["full_name"] for r in rows], ["b/cli"])
        self.assertEqual(len(ts.select(self.rows, limit=2)), 2)

    def test_min_gain(self):
        rows = ts.select(self.rows, min_gain=10)
        self.assertEqual([r["full_name"] for r in rows], ["a/ai"])

    def test_keyword(self):
        rows = ts.select(self.rows, keyword="command line")
        self.assertEqual([r["full_name"] for r in rows], ["b/cli"])


class DiffTest(unittest.TestCase):
    def rows(self, spec):
        return [ts.normalize(item(name, rank), "daily", "t") for name, rank in spec]

    def test_entered_dropped_moved(self):
        old = self.rows([("a/x", 1), ("b/y", 2), ("c/z", 3)])
        new = self.rows([("a/x", 1), ("d/w", 2), ("c/z", 4)])
        delta = ts.diff_items(old, new)
        self.assertEqual([r["full_name"] for r in delta["entered"]], ["d/w"])
        self.assertEqual([r["full_name"] for r in delta["dropped"]], ["b/y"])
        self.assertEqual(delta["moved"], [{"full_name": "c/z", "from": 3, "to": 4, "delta": -1,
                                           "url": "https://github.com/c/z", "stars_gained": 50}])

    def test_no_changes(self):
        rows = self.rows([("a/x", 1)])
        delta = ts.diff_items(rows, rows)
        self.assertEqual(delta["entered"], [])
        self.assertEqual(delta["moved"], [])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_and_load_roundtrip(self):
        rows = [ts.normalize(item("a/b", 1), "daily", "t")]
        path = ts.save_snapshot("daily", rows, self.root, fetched_at="2026-10-26T10:00:00Z")
        self.assertTrue(path.exists())
        self.assertEqual(ts.load_latest("daily", self.root)["items"][0]["full_name"], "a/b")

    def test_ordering_and_previous(self):
        for stamp in ("2026-10-24T10:00:00Z", "2026-10-26T10:00:00Z", "2026-10-25T10:00:00Z"):
            ts.save_snapshot("daily", [], self.root, fetched_at=stamp)
        names = [p.name for p in ts.list_snapshots("daily", self.root)]
        self.assertEqual(names, ["20261024T100000Z.json", "20261025T100000Z.json", "20261026T100000Z.json"])
        self.assertEqual(ts.load_previous("daily", self.root)["fetched_at"], "2026-10-25T10:00:00Z")

    def test_previous_needs_two(self):
        ts.save_snapshot("daily", [], self.root, fetched_at="2026-10-26T10:00:00Z")
        self.assertIsNone(ts.load_previous("daily", self.root))
        self.assertIsNone(ts.load_latest("weekly", self.root))

    def test_store_root_env(self):
        with unittest.mock.patch.dict("os.environ", {"TRENDSHIFT_HOME": str(self.root / "custom")}):
            self.assertEqual(ts.store_root(), self.root / "custom")


class HelpersTest(unittest.TestCase):
    def test_age_days(self):
        self.assertIsInstance(ts.age_days("2026-10-01T00:00:00Z"), int)
        self.assertIsNone(ts.age_days(""))
        self.assertIsNone(ts.age_days("not-a-date"))

    def test_fmt_int(self):
        self.assertEqual(ts.fmt_int(1234), "1 234")
        self.assertEqual(ts.fmt_int(None), "—")

    def test_markdown_table_escapes_pipes(self):
        row = ts.normalize(item("a/b", 1, repository_description="x | y"), "daily", "t")
        self.assertIn("x \\| y", ts.markdown_table([row]))

    def test_clamp(self):
        self.assertEqual(mcp._clamp("50", 1, 25, 10), 25)
        self.assertEqual(mcp._clamp("bad", 1, 25, 10), 10)
        self.assertEqual(mcp._clamp(None, 1, 25, 7), 7)


class McpProtocolTest(unittest.TestCase):
    def call(self, payload: dict) -> list[dict]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mcp.handle(payload)
        return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]

    def test_initialize_echoes_version(self):
        [reply] = self.call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2026-07-28", "clientInfo": {"name": "t"}}})
        self.assertEqual(reply["result"]["protocolVersion"], "2026-07-28")
        self.assertEqual(reply["result"]["serverInfo"]["name"], "trendshift")

    def test_initialize_without_version(self):
        [reply] = self.call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(reply["result"]["protocolVersion"], "2025-06-18")

    def test_notification_has_no_reply(self):
        self.assertEqual(self.call({"jsonrpc": "2.0", "method": "notifications/initialized"}), [])

    def test_tools_list_schema(self):
        [reply] = self.call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [t["name"] for t in reply["result"]["tools"]]
        self.assertEqual(names, ["trending", "digest", "new_since_last", "repository"])
        for tool in reply["result"]["tools"]:
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_unknown_method(self):
        [reply] = self.call({"jsonrpc": "2.0", "id": 3, "method": "resources/read"})
        self.assertEqual(reply["error"]["code"], -32601)

    def test_unknown_tool(self):
        [reply] = self.call({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                             "params": {"name": "nope", "arguments": {}}})
        self.assertEqual(reply["error"]["code"], -32602)

    def test_tool_errors_are_reported_not_raised(self):
        def boom(_args):
            raise ts.TrendshiftError("сеть недоступна")

        with unittest.mock.patch.dict(mcp.HANDLERS, {"trending": boom}):
            [reply] = self.call({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                 "params": {"name": "trending", "arguments": {}}})
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("сеть недоступна", reply["result"]["content"][0]["text"])

    def test_trending_uses_fetched_items(self):
        rows = [ts.normalize(item("a/b", 1), "daily", "t")]
        with unittest.mock.patch.object(ts, "fetch_period", return_value=rows):
            [reply] = self.call({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                                 "params": {"name": "trending", "arguments": {"period": "daily", "limit": 1}}})
        self.assertFalse(reply["result"]["isError"])
        self.assertIn("a/b", reply["result"]["content"][0]["text"])

    def test_new_since_last_first_run_creates_base(self):
        rows = [ts.normalize(item("a/b", 1), "daily", "t")]
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.dict("os.environ", {"TRENDSHIFT_HOME": tmp}), \
                unittest.mock.patch.object(ts, "fetch_period", return_value=rows):
            [reply] = self.call({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                 "params": {"name": "new_since_last", "arguments": {"period": "daily"}}})
        self.assertIn("база создана", reply["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
