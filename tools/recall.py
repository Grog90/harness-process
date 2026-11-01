#!/usr/bin/env python3
"""recall.py — поиск по прошлым сессиям DSH.

Сессии лежат в ``~/.dsh/sessions/<воркспейс>/<session-id>/session.v4.jsonl.zstd``.
Файл — поток JSON-событий, сжатый zstd **несколькими кадрами** (append-only),
поэтому обычный однократный decompress читает только первый кадр.

Команды:
    index   — построить/обновить локальный индекс (SQLite FTS5)
    search  — найти фрагменты прошлых разговоров
    stats   — что проиндексировано
    forget  — удалить индекс

Индекс: ``.recall/index.db`` (в .gitignore), сеть не используется.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

SESSION_GLOB = "session.v4.jsonl.zstd"
DEFAULT_SESSIONS = Path.home() / ".dsh" / "sessions"
DEFAULT_INDEX = Path(".recall") / "index.db"

SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS msg USING fts5(
    session_id UNINDEXED,
    ts UNINDEXED,
    role UNINDEXED,
    title UNINDEXED,
    text,
    tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS session (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    cwd TEXT,
    title TEXT,
    created INTEGER,
    messages INTEGER DEFAULT 0
);
"""


# --------------------------------------------------------------------------- zstd


def decompress(path: Path) -> bytes:
    """Распаковать все кадры zstd. Порядок: stdlib 3.14+, zstandard, node."""
    try:
        from compression import zstd as std_zstd  # type: ignore[import-not-found]

        return std_zstd.decompress(path.read_bytes())  # type: ignore[attr-defined]
    except ImportError:
        pass

    try:
        import zstandard

        with path.open("rb") as handle:
            reader = zstandard.ZstdDecompressor().stream_reader(handle, read_across_frames=True)
            return reader.read()
    except ImportError:
        pass

    node = shutil.which("node")
    if node:
        script = (
            "const z=require('zlib'),f=require('fs');"
            "process.stdout.write(z.zstdDecompressSync(f.readFileSync(process.argv[1])))"
        )
        result = subprocess.run([node, "-e", script, str(path)], capture_output=True, check=False)
        if result.returncode == 0:
            return result.stdout

    raise RuntimeError(
        "нет распаковщика zstd: нужен Python 3.14+, модуль zstandard или node в PATH"
    )


# ------------------------------------------------------------------------ разбор


def text_of(message: dict) -> str:
    parts = []
    for block in message.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            value = block.get("text")
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return "\n".join(parts)


def parse_session(path: Path) -> tuple[dict, list[tuple[int, str, str]]]:
    """Вернуть (метаданные, [(ts, role, text)]). Битые строки пропускаются."""
    raw = decompress(path)
    meta: dict = {"cwd": "", "title": "", "created": 0}
    rows: list[tuple[int, str, str]] = []
    last_ts = 0

    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        ts = int(event.get("time") or 0) or last_ts
        last_ts = ts
        data = event.get("data") or {}

        if kind == "session":
            meta["cwd"] = event.get("cwd") or ""
            meta["created"] = int(event.get("createdAt") or 0)
        elif kind == "session/title":
            meta["title"] = (data.get("title") or "").strip()
        elif kind == "user/message":
            # у пользовательских сообщений content лежит прямо в data, не в data.message
            text = text_of(data.get("message") or data)
            if text:
                rows.append((ts, "user", text))
        elif kind == "assistant/message":
            text = text_of(data.get("message") or {})
            if text:
                rows.append((ts, "assistant", text))
        elif kind == "compaction/summary":
            text = text_of({"content": data.get("summary") or []})
            if text:
                rows.append((ts, "summary", text))
    return meta, rows


# ------------------------------------------------------------------------- индекс


def connect(index_path: Path) -> sqlite3.Connection:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(index_path)
    conn.executescript(SCHEMA)
    return conn


def build_index(index_path: Path, sessions_dir: Path, force: bool = False, quiet: bool = False) -> dict:
    conn = connect(index_path)
    files = sorted(sessions_dir.rglob(SESSION_GLOB)) if sessions_dir.exists() else []
    known = {
        row[0]: (row[1], row[2])
        for row in conn.execute("SELECT id, mtime, size FROM session")
    }
    added = updated = skipped = empty = 0
    messages = 0

    try:
        for path in files:
            session_id = path.parent.name
            stat = path.stat()
            if not force and known.get(session_id) == (stat.st_mtime, stat.st_size):
                skipped += 1
                continue
            meta, rows = parse_session(path)
            conn.execute("DELETE FROM msg WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM session WHERE id = ?", (session_id,))
            if rows:
                conn.executemany(
                    "INSERT INTO msg(session_id, ts, role, title, text) VALUES (?,?,?,?,?)",
                    [(session_id, ts, role, meta["title"], text) for ts, role, text in rows],
                )
            conn.execute(
                "INSERT INTO session(id, path, mtime, size, cwd, title, created, messages)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    str(path),
                    stat.st_mtime,
                    stat.st_size,
                    meta["cwd"],
                    meta["title"],
                    meta["created"],
                    len(rows),
                ),
            )
            if rows:
                if session_id in known:
                    updated += 1
                else:
                    added += 1
                messages += len(rows)
            else:
                empty += 1
        conn.commit()
    finally:
        conn.close()

    report = {
        "files": len(files),
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "empty": empty,
    }
    if not quiet:
        print(
            f"файлов {report['files']}, новых {added}, обновлено {updated}, "
            f"без изменений {skipped}, пустых {empty}, сообщений +{messages}"
        )
    return report


# -------------------------------------------------------------------------- поиск


def fts_query(text: str, mode: str = "AND") -> str:
    """Собрать запрос FTS5: каждый токен — префиксом, через AND (или OR).

    Русской морфологии в unicode61 нет, поэтому префикс ловит окончания:
    «тест*» находит «тесты», «тестировали».
    """
    words = [word for word in "".join(
        char if char.isalnum() else " " for char in text.lower()
    ).split() if len(word) > 1]
    if not words:
        return ""
    return f" {mode} ".join(f'"{word}"*' for word in words)


def ago(ts_ms: int, now: dt.datetime | None = None) -> str:
    if not ts_ms:
        return "?"
    now = now or dt.datetime.now()
    moment = dt.datetime.fromtimestamp(ts_ms / 1000)
    days = (now.date() - moment.date()).days
    if days == 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    if days < 7:
        return f"{days} дн. назад"
    return moment.strftime("%Y-%m-%d")


def _run(conn: sqlite3.Connection, query: str, args: argparse.Namespace) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT session_id, ts, role, title, "
        "snippet(msg, 4, '‹', '›', '…', ?) AS snip, bm25(msg) AS rank "
        "FROM msg WHERE msg MATCH ? ORDER BY rank LIMIT ?",
        (args.words, query, args.limit * max(1, args.per_session) * 6),
    ).fetchall()


def cmd_search(args: argparse.Namespace) -> int:
    index_path = Path(args.index)
    if not index_path.exists():
        print("индекса нет: сначала `python tools/recall.py index`", file=sys.stderr)
        return 2
    query = fts_query(args.query, "AND" if args.exact else "OR")
    if not query:
        print("ОТКАЗ [запрос]: нет слов длиннее одного символа", file=sys.stderr)
        return 2

    conn = sqlite3.connect(index_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = _run(conn, query, args)
    except sqlite3.OperationalError as exc:
        print(f"ОТКАЗ [запрос]: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    if not rows:
        print(f"ничего не найдено: {args.query}")
        return 0

    seen: dict[str, int] = {}
    texts: set[str] = set()
    selected: list[sqlite3.Row] = []
    for row in rows:
        if row["snip"] in texts:
            continue  # форки и продолжения сессий дают одинаковые фрагменты
        count = seen.get(row["session_id"], 0)
        if count >= args.per_session:
            continue
        texts.add(row["snip"])
        seen[row["session_id"]] = count + 1
        selected.append(row)
        if len(selected) >= args.limit * args.per_session:
            break

    if args.json:
        payload = [
            {
                "session": row["session_id"],
                "ago": ago(row["ts"]),
                "role": row["role"],
                "title": row["title"],
                "snippet": row["snip"],
            }
            for row in selected
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    printed_sessions: set[str] = set()
    for row in selected:
        header = f"[{ago(row['ts'])}] {row['role']}"
        if row["title"]:
            header += f" · {row['title']}"
        print(header)
        print(f"  {row['snip']}")
        if row["session_id"] not in printed_sessions:
            printed_sessions.add(row["session_id"])
            print(f"  сессия: {row['session_id']}")
        print()
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    index_path = Path(args.index)
    if not index_path.exists():
        print("индекса нет")
        return 0
    conn = sqlite3.connect(index_path)
    try:
        sessions, messages = conn.execute("SELECT COUNT(*), COALESCE(SUM(messages),0) FROM session").fetchone()
        words = conn.execute("SELECT COUNT(*) FROM msg").fetchone()[0]
        span = conn.execute("SELECT MIN(created), MAX(created) FROM session").fetchone()
        first = ago(span[0]) if span[0] else "—"
        last = ago(span[1]) if span[1] else "—"
    finally:
        conn.close()
    print(f"сессий {sessions}, сообщений {messages}, записей в индексе {words}")
    print(f"период: {first} … {last}")
    print(f"размер индекса: {index_path.stat().st_size / 1024:.0f} КБ")
    return 0


def cmd_forget(args: argparse.Namespace) -> int:
    index_path = Path(args.index)
    if index_path.exists():
        index_path.unlink()
        print(f"удалён {index_path}")
    else:
        print("индекса нет")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    sessions = Path(os.path.expanduser(args.sessions))
    if not sessions.exists():
        print(f"ОТКАЗ [сессии]: нет каталога {sessions}", file=sys.stderr)
        return 2
    build_index(Path(args.index), sessions, force=args.force)
    return 0


# ---------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recall.py",
        description="Поиск по прошлым сессиям DSH: локально, без сети.",
    )
    parser.add_argument("--index", default=str(DEFAULT_INDEX), help="файл индекса")
    sub = parser.add_subparsers(dest="command", required=True)

    index = sub.add_parser("index", help="обновить индекс")
    index.add_argument("--sessions", default=str(DEFAULT_SESSIONS))
    index.add_argument("--force", action="store_true", help="перечитать все сессии")
    index.set_defaults(func=cmd_index)

    search = sub.add_parser("search", help="найти фрагменты")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=3, help="сколько сессий")
    search.add_argument("--per-session", type=int, default=2, help="сколько фрагментов из сессии")
    search.add_argument("--words", type=int, default=28, help="слов в сниппете")
    search.add_argument("--json", action="store_true")
    search.add_argument("--exact", action="store_true", help="все слова в одном сообщении (AND)")
    search.set_defaults(func=cmd_search)

    stats = sub.add_parser("stats", help="что проиндексировано")
    stats.set_defaults(func=cmd_stats)

    forget = sub.add_parser("forget", help="удалить индекс")
    forget.set_defaults(func=cmd_forget)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
