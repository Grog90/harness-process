"""Проверки tools/recall.py: индекс, поиск, дедупликация, отказы."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "recall.py"
_spec = importlib.util.spec_from_file_location("recall_tool", TOOL)
recall = importlib.util.module_from_spec(_spec)
sys.modules["recall_tool"] = recall
_spec.loader.exec_module(recall)  # type: ignore[union-attr]

try:
    import zstandard  # noqa: F401
    HAVE_ZSTD = True
except ImportError:  # pragma: no cover
    HAVE_ZSTD = False


def event(kind: str, ts: int, data: dict) -> bytes:
    return (json.dumps({"type": kind, "time": ts, "data": data}, ensure_ascii=False) + "\n").encode()


def write_session(root: Path, session_id: str, events: list[bytes]) -> Path:
    import zstandard

    directory = root / "sessions" / "--ws--" / session_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "session.v4.jsonl.zstd"
    # каждый вызов compress создаёт отдельный кадр — как в append-only журнале DSH
    with path.open("wb") as handle:
        for chunk in events:
            handle.write(zstandard.ZstdCompressor().compress(chunk))
    return path


def user_message(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "role": "user", "id": "u1"}


def assistant_message(text: str) -> dict:
    return {"turn": 1, "step": 1, "message": {"content": [{"type": "text", "text": text}], "role": "assistant"}}


@unittest.skipUnless(HAVE_ZSTD, "нужен zstandard")
class RecallTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.index = self.root / "index.db"

        write_session(
            self.root,
            "session-aaa",
            [
                event("session", 1000, {}),
                event("session/title", 1001, {"title": "Первый чат"}),
                event("user/message", 1002, user_message("починить сборку релиза")),
                event("assistant/message", 1003, assistant_message("Сборка падала из-за BOM в конфиге.")),
            ],
        )
        write_session(
            self.root,
            "session-bbb",
            [
                event("session", 2000, {}),
                event("session/title", 2001, {"title": "Второй чат"}),
                event("user/message", 2002, user_message("сборка релиза снова упала")),
                event("assistant/message", 2003, assistant_message("BOM снова в конфиге.")),
            ],
        )

    def run_tool(self, *argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        args = ["--index", str(self.index), *argv]
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = recall.main(args)
        return code, buffer.getvalue()

    def test_index_counts_frames_across_sessions(self) -> None:
        code, out = self.run_tool("index", "--sessions", str(self.root / "sessions"))
        self.assertEqual(code, 0, out)
        self.assertIn("новых 2", out)

    def test_search_finds_user_and_assistant(self) -> None:
        self.run_tool("index", "--sessions", str(self.root / "sessions"))
        code, out = self.run_tool("search", "BOM")
        self.assertEqual(code, 0, out)
        self.assertIn("assistant", out)
        self.assertIn("‹BOM›", out)

    def test_exact_requires_all_words_in_one_message(self) -> None:
        self.run_tool("index", "--sessions", str(self.root / "sessions"))
        # «починить» — в сообщении пользователя, «BOM» — в ответе ассистента
        code, out = self.run_tool("search", "починить BOM", "--exact")
        self.assertEqual(code, 0)
        self.assertIn("ничего не найдено", out)

    def test_search_deduplicates_identical_fragments(self) -> None:
        # третий чат— форк с тем же текстом, что и первый
        write_session(
            self.root,
            "session-ccc",
            [
                event("session", 3000, {}),
                event("user/message", 3002, user_message("починить сборку релиза")),
            ],
        )
        self.run_tool("index", "--sessions", str(self.root / "sessions"))
        code, out = self.run_tool("search", "сборку", "--per-session", "1", "--limit", "5")
        self.assertEqual(code, 0)
        self.assertIn("починить", out)
        # одинаковый текст из форка не должен давать вторую карточку
        self.assertEqual(out.count("сессия:"), 1)

    def test_json_output_is_parseable(self) -> None:
        self.run_tool("index", "--sessions", str(self.root / "sessions"))
        code, out = self.run_tool("search", "релиза", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload)
        self.assertEqual(set(payload[0]), {"session", "ago", "role", "title", "snippet"})

    def test_incremental_skips_unchanged(self) -> None:
        self.run_tool("index", "--sessions", str(self.root / "sessions"))
        code, out = self.run_tool("index", "--sessions", str(self.root / "sessions"))
        self.assertEqual(code, 0)
        self.assertIn("без изменений 2", out)

    def test_search_without_index_fails(self) -> None:
        code, out = self.run_tool("search", "что угодно")
        self.assertEqual(code, 2)
        self.assertIn("индекса нет", out)

    def test_stats_reports_totals(self) -> None:
        self.run_tool("index", "--sessions", str(self.root / "sessions"))
        code, out = self.run_tool("stats")
        self.assertEqual(code, 0)
        self.assertIn("сессий 2", out)

    def test_forget_removes_index(self) -> None:
        self.run_tool("index", "--sessions", str(self.root / "sessions"))
        code, out = self.run_tool("forget")
        self.assertEqual(code, 0)
        self.assertFalse(self.index.exists())


if __name__ == "__main__":
    unittest.main()
