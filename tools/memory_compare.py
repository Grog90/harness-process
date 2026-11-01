#!/usr/bin/env python3
"""memory_compare.py — сравнение локального recall и Hindsight на одних вопросах.

Обе системы получают один и тот же корпус: фрагменты прошлых сессий DSH.
recall индексирует их сам (tools/recall.py), Hindsight получает те же тексты через retain.

    python tools/memory_compare.py seed          # залить корпус в Hindsight
    python tools/memory_compare.py ask           # задать вопросы обеим системам
    python tools/memory_compare.py ask --json    # машинный вывод
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANK = "compare"
BASE = "http://127.0.0.1:8888/v1/default/banks/" + BANK

QUESTIONS = [
    "почему в harness нет внешних зависимостей?",
    "что делает плагин harness-guard?",
    "какие правила про запись файлов и R-PROT-1?",
    "зачем нужен бриф перед запуском задачи?",
    "что решили про лицензию Gortex?",
    "почему recall использует FTS5, а не эмбеддинги?",
]


def load_recall():
    path = ROOT / "tools" / "recall.py"
    spec = importlib.util.spec_from_file_location("recall_mod", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["recall_mod"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


NOISE = re.compile(
    r"(?i)(preparing turn|turn \d+, step|system-reminder|Web GUI at|"
    r"^\s*(user|assistant)\s+(is|has|currently)\b|confinement denial)"
)


def corpus(limit: int = 18) -> list[dict]:
    """Фрагменты прошлых сессий: вопросы пользователя и содержательные ответы."""
    recall = load_recall()
    sessions = sorted((Path.home() / ".dsh" / "sessions").rglob("session.v4.jsonl.zstd"))
    picked: list[dict] = []
    for path in sessions:
        try:
            meta, rows = recall.parse_session(path)
        except Exception:  # noqa: BLE001
            continue
        for ts, role, text in rows:
            clean = text.strip()
            if role == "summary" or not (200 <= len(clean) <= 1200):
                continue
            if NOISE.search(clean):
                continue
            picked.append({"content": clean, "context": meta.get("title") or "сессия DSH"})
        if len(picked) >= limit * 3:
            break
    # равномерно по корпусу, чтобы не залить только одну сессию
    step = max(1, len(picked) // limit)
    return picked[::step][:limit]


def post(path: str, payload: dict, timeout: int = 900) -> tuple[int, dict | str, float]:
    started = time.time()
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, (json.loads(raw) if raw.strip() else {}), time.time() - started
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:300], time.time() - started


def cmd_seed(args: argparse.Namespace) -> int:
    items = corpus(args.limit)
    print(f"корпус: {len(items)} фрагментов")
    batch = 3
    total = 0
    for start in range(0, len(items), batch):
        chunk = items[start:start + batch]
        status, body, took = post("/memories", {"items": chunk, "async": False})
        if status != 200:
            print(f"ОТКАЗ {status}: {body}")
            return 2
        usage = body.get("usage", {}) if isinstance(body, dict) else {}
        total += usage.get("total_tokens", 0)
        print(f"  {start + len(chunk)}/{len(items)}  {took:.1f}s  токенов {usage.get('total_tokens', '?')}")
    print(f"готово, суммарно токенов на запись: {total}")
    return 0


def hindsight_recall(query: str, limit: int = 3) -> tuple[list[str], float]:
    status, body, took = post("/memories/recall", {"query": query, "max_tokens": 500, "prefer_observations": False})
    if status != 200 or not isinstance(body, dict):
        return [f"ОШИБКА {status}: {body}"], took
    results = body.get("results") or []
    texts = []
    for unit in results[:limit]:
        if isinstance(unit, dict):
            texts.append(str(unit.get("content") or unit.get("text") or unit)[:220].replace("\n", " "))
        else:
            texts.append(str(unit)[:220])
    return texts, took


def local_recall(query: str, limit: int = 3) -> tuple[list[str], float]:
    recall = load_recall()
    index = ROOT / ".recall" / "index.db"
    if not index.exists():
        return ["индекса нет: python tools/recall.py index"], 0.0
    started = time.time()
    args = argparse.Namespace(index=str(index), query=query, limit=limit, per_session=1, words=28, exact=False, json=True)
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = recall.main(["--index", str(index), "search", query, "--limit", str(limit), "--per-session", "1", "--json"])
    took = time.time() - started
    if code != 0:
        return [buffer.getvalue().strip()], took
    try:
        payload = json.loads(buffer.getvalue())
    except json.JSONDecodeError:
        return [buffer.getvalue()[:200]], took
    return [item["snippet"][:220] for item in payload[:limit]], took


def cmd_ask(args: argparse.Namespace) -> int:
    report = []
    for query in QUESTIONS:
        local, local_took = local_recall(query)
        hind, hind_took = hindsight_recall(query)
        report.append(
            {
                "query": query,
                "recall": {"took": round(local_took, 3), "results": local},
                "hindsight": {"took": round(hind_took, 3), "results": hind},
            }
        )
        if not args.json:
            print(f"\n=== {query}")
            print(f"  recall    ({local_took * 1000:.0f} мс)")
            for line in local:
                print(f"    · {line}")
            print(f"  hindsight ({hind_took * 1000:.0f} мс)")
            for line in hind:
                print(f"    · {line}")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="recall vs Hindsight на одном корпусе")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed")
    seed.add_argument("--limit", type=int, default=18)
    seed.set_defaults(func=cmd_seed)
    ask = sub.add_parser("ask")
    ask.set_defaults(func=cmd_ask)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
