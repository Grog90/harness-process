#!/usr/bin/env python3
"""memory_facts_bench.py — проверка гипотезы: Hindsight точен на атомарных фактах.

15 фактов о проекте, 10 вопросов, у каждого вопроса известен ожидаемый факт
и опорное слово, по которому видно попадание.

    python tools/memory_facts_bench.py seed    # залить факты в банк facts
    python tools/memory_facts_bench.py ask     # прогнать вопросы и посчитать hit@k
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANK = "facts"
BASE = "http://127.0.0.1:8888/v1/default/banks/" + BANK
TOP_K = 3

FACTS = [
    ("F01", "В проекте harness запрещены внешние зависимости: только стандартная библиотека Python, минимум 3.10."),
    ("F02", "Плагин harness-guard запрещает запись вне корня воркспейса по правилу R-PROT-1; временные файлы кладут в .harness/tmp."),
    ("F03", "Без брифа диспетч задачи не выдаётся: бриф проверяет allowed_files, бюджет попыток и открытые вопросы."),
    ("F04", "Коммит делает финализатор, а не агент: без актуального PASS, привязанного к снимку, коммит запрещён."),
    ("F05", "На задачу даётся не больше трёх попыток, и счётчик не сбрасывается сменой агента."),
    ("F06", "Локальный recall по прошлым сессиям использует SQLite FTS5 и bm25, без эмбеддингов."),
    ("F07", "Сессии DSH лежат в ~/.dsh/sessions и сжаты zstd в несколько кадров, поэтому нужен read_across_frames."),
    ("F08", "Hindsight хранит память в Postgres с pgvector и при каждой записи вызывает LLM, тратя около 3800 токенов на фрагмент."),
    ("F09", "Gortex распространяется под Apache 2.0, а раньше был под PolyForm Small Business."),
    ("F10", "Gortex умеет находить кросс-репозиторные контракты: HTTP, gRPC, GraphQL и очереди сообщений."),
    ("F11", "Локальная модель для экспериментов — qwen3:8b в Ollama на порту 11434."),
    ("F12", "Ключ opencode-go работает только на endpoint /zen/go/v1, а на /zen/v1 отдаёт 402."),
    ("F13", "Уроки из ошибок продвигаются по трём ступеням: журнал, строка в AGENTS.md, правило с проверкой."),
    ("F14", "Персональный ключ Dsh истекает 21 января 2027 года, отозвать его можно кнопкой Revoke."),
    ("F15", "Правило R-HOOK-1 требует менять policies/rules.json только через harness rule change с указанием причины."),
]

QUESTIONS = [
    ("Q1", "какие ограничения на внешние зависимости?", "F01", "стандартн"),
    ("Q2", "почему нельзя писать в системный TEMP?", "F02", "R-PROT-1"),
    ("Q3", "что проверяет бриф перед запуском задачи?", "F03", "бриф"),
    ("Q4", "кто делает коммит: агент или кто-то ещё?", "F04", "финализатор"),
    ("Q5", "сколько попыток даётся на одну задачу?", "F05", "попыт"),
    ("Q6", "на чём построен поиск по прошлым сессиям?", "F06", "FTS5"),
    ("Q7", "как хранятся сессии DSH и почему их сложно прочитать?", "F07", "zstd"),
    ("Q8", "под какой лицензией Gortex и что было раньше?", "F09", "PolyForm"),
    ("Q9", "какой endpoint нужен для ключа opencode-go?", "F12", "/zen/go"),
    ("Q10", "как продвигаются уроки из ошибок?", "F13", "ступен"),
]


def post(path: str, payload: dict, timeout: int = 600) -> tuple[int, dict | str]:
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:300]


def cmd_seed(_: argparse.Namespace) -> int:
    items = [{"content": text, "context": "проект harness, решения и правила"} for _, text in FACTS]
    total = 0
    for start in range(0, len(items), 5):
        chunk = items[start:start + 5]
        status, body = post("/memories", {"items": chunk, "async": False})
        if status != 200:
            print(f"ОТКАЗ {status}: {body}")
            return 2
        tokens = (body.get("usage") or {}).get("total_tokens", 0) if isinstance(body, dict) else 0
        total += tokens
        print(f"  {start + len(chunk)}/{len(items)}  токенов {tokens}")
    print(f"готово, суммарно токенов: {total}")
    return 0


def hindsight_top(query: str) -> list[str]:
    status, body = post("/memories/recall", {"query": query, "max_tokens": 700, "prefer_observations": False})
    if status != 200 or not isinstance(body, dict):
        return [f"ОШИБКА {status}"]
    out = []
    for unit in (body.get("results") or [])[:TOP_K]:
        if isinstance(unit, dict):
            out.append(str(unit.get("content") or unit.get("text") or unit))
        else:
            out.append(str(unit))
    return out


def local_top(query: str) -> list[str]:
    path = ROOT / "tools" / "recall.py"
    spec = importlib.util.spec_from_file_location("recall_bench", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["recall_bench"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    index = ROOT / ".recall" / "index.db"
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        module.main(["--index", str(index), "search", query, "--limit", str(TOP_K), "--per-session", "1", "--json"])
    try:
        payload = json.loads(buffer.getvalue())
    except json.JSONDecodeError:
        return []
    return [item["snippet"] for item in payload[:TOP_K]]


def score(name: str, query: str, keyword: str, hits: list[str]) -> tuple[bool, bool]:
    lowered = [text.lower() for text in hits]
    first = any(keyword.lower() in text for text in lowered[:1])
    topk = any(keyword.lower() in text for text in lowered)
    print(f"  {name:9} hit@1={'да ' if first else 'нет'} hit@{TOP_K}={'да ' if topk else 'нет'}")
    if not topk and hits:
        print(f"            вместо этого: {hits[0][:110]}")
    return first, topk


def cmd_ask(_: argparse.Namespace) -> int:
    hind_first = hind_top = 0
    local_first = local_top_k = 0
    rows = []
    for qid, question, fact_id, keyword in QUESTIONS:
        print(f"\n=== {qid}. {question}  (ожидается {fact_id})")
        hind = hindsight_top(question)
        first, topk = score("hindsight", question, keyword, hind)
        hind_first += first
        hind_top += topk

        loc = local_top(question)
        first, topk = score("recall", question, keyword, loc)
        local_first += first
        local_top_k += topk

        rows.append({"id": qid, "question": question, "expected": fact_id, "keyword": keyword,
                     "hindsight": hind, "recall": loc})

    total = len(QUESTIONS)
    print("\n--- итог ---")
    print(f"hindsight  hit@1 {hind_first}/{total}   hit@{TOP_K} {hind_top}/{total}")
    print(f"recall     hit@1 {local_first}/{total}   hit@{TOP_K} {local_top_k}/{total}")
    (ROOT / ".recall" / "facts_bench.json").parent.mkdir(parents=True, exist_ok=True)
    (ROOT / ".recall" / "facts_bench.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Hindsight на атомарных фактах vs recall")
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed")
    seed.set_defaults(func=cmd_seed)
    ask = sub.add_parser("ask")
    ask.set_defaults(func=cmd_ask)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
