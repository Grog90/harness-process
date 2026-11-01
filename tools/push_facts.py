#!/usr/bin/env python3
"""push_facts.py — отправить уроки журнала в Hindsight как атомарные факты.

Зачем: Hindsight точен на коротких фактах и бесполезен на переписке (см. docs/MEMORY_COMPARE.md).
Журнал уже почти в нужном формате — из него и берём.

Идемпотентность: состояние в ``.recall/pushed_facts.json`` (хэш текста на каждую запись).
Повторный запуск отправляет только новое и изменённое.

    python tools/push_facts.py --dry-run     # что будет отправлено
    python tools/push_facts.py               # отправить
    python tools/push_facts.py --include-agents   # плюс жёсткие правила из AGENTS.md
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".recall" / "pushed_facts.json"
BATCH = 5


def load_lessons():
    path = ROOT / "tools" / "lessons.py"
    spec = importlib.util.spec_from_file_location("lessons_push", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["lessons_push"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def build_journal_facts() -> list[dict]:
    lessons = load_lessons()
    journal = lessons.load_journal(ROOT)
    facts = []
    for entry in journal.entries:
        rule = entry.get("Правило")
        if not rule:
            continue
        parts = [rule.rstrip(".") + "."]
        trigger = entry.get("Триггер")
        if trigger:
            parts.append(f"Действует, когда: {trigger}.")
        scope = entry.get("Область")
        if scope:
            parts.append(f"Область: {scope}.")
        facts.append(
            {
                "key": f"journal:{entry.id}",
                "content": " ".join(parts),
                "context": f"урок из журнала ошибок проекта harness ({entry.get('Тип')})",
                "tags": ["journal", entry.get("Тип") or "unknown"],
            }
        )
    return facts


SKIP_SECTIONS = ("Команды", "Уроки", "Чего здесь нет", "Что за проект")
MIN_FACT_CHARS = 50


def build_docs_facts() -> list[dict]:
    """Атомарные факты из docs/FACTS.md — по одному утверждению на пункт."""
    path = ROOT / "docs" / "FACTS.md"
    if not path.exists():
        return []
    facts = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        content = _strip_markdown(line[2:])
        if len(content) < MIN_FACT_CHARS:
            continue
        if not content.endswith("."):
            content += "."
        digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:10]
        facts.append(
            {
                "key": f"docs:{digest}",
                "content": content,
                "context": "факт об устройстве проекта harness (docs/FACTS.md)",
                "tags": ["docs", "facts"],
            }
        )
    return facts


def _blocks(text: str) -> list[tuple[str, str]]:
    """Разбить markdown на (заголовок, тело) по заголовкам второго уровня."""
    parts = re.split(r"^## ", text, flags=re.MULTILINE)[1:]
    out = []
    for part in parts:
        title, _, body = part.partition("\n")
        out.append((title.strip(), body))
    return out


def _strip_markdown(value: str) -> str:
    value = re.sub(r"`{1,3}([^`]*)`{1,3}", r"\1", value)
    value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
    value = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", value)
    return " ".join(value.split())


def build_agents_facts() -> list[dict]:
    """Все правила из AGENTS.md: нумерованные пункты и маркированные списки.

    Таблицы и разделы без правил (команды, «чего здесь нет») пропускаются.
    """
    path = ROOT / "AGENTS.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    facts: list[dict] = []
    seen: set[str] = set()

    for title, body in _blocks(text):
        if any(title.startswith(skip) for skip in SKIP_SECTIONS):
            continue
        for raw in body.splitlines():
            line = raw.strip()
            numbered = re.match(r"^\d+\.\s+(.*)$", line)
            bullet = re.match(r"^[-*]\s+(.*)$", line)
            if not numbered and not bullet:
                continue
            content = _strip_markdown((numbered or bullet).group(1))
            if len(content) < MIN_FACT_CHARS:
                continue
            if not content.endswith("."):
                content += "."
            if content in seen:
                continue
            seen.add(content)
            digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:10]
            facts.append(
                {
                    "key": f"agents:{digest}",
                    "content": content,
                    "context": f"правило репозитория harness (AGENTS.md, раздел «{title}»)",
                    "tags": ["agents", title.lower()[:20]],
                }
            )
    return facts


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def plan(facts: list[dict], state: dict) -> tuple[list[dict], int]:
    fresh = []
    skipped = 0
    for fact in facts:
        digest = hashlib.sha1(fact["content"].encode("utf-8")).hexdigest()
        if state.get(fact["key"]) == digest:
            skipped += 1
            continue
        fact["digest"] = digest
        fresh.append(fact)
    return fresh, skipped


def post(base: str, path: str, payload: dict, timeout: int = 600) -> tuple[int, dict | str]:
    request = urllib.request.Request(
        base + path,
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
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def cmd_run(args: argparse.Namespace) -> int:
    base = f"http://127.0.0.1:8888/v1/default/banks/{args.bank}"
    facts = build_journal_facts() + build_docs_facts() + (build_agents_facts() if args.include_agents else [])
    if not facts:
        print("нечего отправлять: журнал пуст")
        return 0

    state = load_state()
    fresh, skipped = plan(facts, state)
    print(f"фактов всего {len(facts)}, уже отправлено {skipped}, новых/изменённых {len(fresh)}")

    if args.dry_run:
        for fact in fresh:
            print(f"  [{fact['key']}] {fact['content'][:120]}…")
        return 0
    if not fresh:
        return 0

    sent = 0
    tokens = 0
    for start in range(0, len(fresh), BATCH):
        chunk = fresh[start:start + BATCH]
        items = [
            {
                "content": fact["content"],
                "context": fact["context"],
                # metadata принимает только строковые значения
                "metadata": {"key": fact["key"], "tags": ",".join(fact["tags"])},
            }
            for fact in chunk
        ]
        status, body = post(base, "/memories", {"items": items, "async": False})
        if status != 200:
            print(f"ОТКАЗ {status}: {body}")
            return 2
        usage = (body.get("usage") or {}) if isinstance(body, dict) else {}
        tokens += usage.get("total_tokens", 0)
        for fact in chunk:
            state[fact["key"]] = fact["digest"]
        sent += len(chunk)
        print(f"  отправлено {sent}/{len(fresh)}  токенов {usage.get('total_tokens', '?')}")
        time.sleep(0.5)

    save_state(state)
    print(f"готово: {sent} фактов, {tokens} токенов; состояние в {STATE.relative_to(ROOT)}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    base = f"http://127.0.0.1:8888/v1/default/banks/{args.bank}"
    questions = [
        "почему нельзя писать вне корня воркспейса?",
        "что проверяет бриф?",
        "какие правила про зависимости?",
    ]
    for question in questions:
        status, body = post(base, "/memories/recall", {"query": question, "max_tokens": 300, "prefer_observations": False})
        print(f"\n=== {question}")
        if status != 200 or not isinstance(body, dict):
            print(f"  ОТКАЗ {status}: {body}")
            continue
        for unit in (body.get("results") or [])[:3]:
            if isinstance(unit, dict):
                text = unit.get("text") or unit.get("content") or ""
                score = (unit.get("scores") or {}).get("final")
                print(f"  · [{score:.2f}] {str(text)[:170]}" if isinstance(score, float) else f"  · {str(text)[:170]}")
            else:
                print(f"  · {str(unit)[:170]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Отправка уроков журнала в Hindsight")
    parser.add_argument("--bank", default="harness")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-agents", action="store_true")
    parser.add_argument("--verify", action="store_true", help="после отправки задать контрольные вопросы")
    args = parser.parse_args()
    code = cmd_run(args)
    if code == 0 and args.verify:
        return cmd_verify(args)
    return code


if __name__ == "__main__":
    sys.exit(main())
