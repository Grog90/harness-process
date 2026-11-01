#!/usr/bin/env python3
"""trendshift.py — срез трендов GitHub с trendshift.io без зависимостей.

trendshift.io — витрина «что растёт прямо сейчас»: daily / weekly / monthly /
yearly. Публичного API у сервиса нет, но страницы отдают данные внутри
RSC-потока Next.js (`self.__next_f.push([1, "..."])`), откуда они и достаются.

Команды:
    top     — текущий топ одного периода (таблица или JSON)
    digest  — markdown-сводка по всем периодам
    watch   — снапшот + только изменения с прошлого запуска (для расписания)
    diff    — что изменилось между двумя последними снапшотами
    history — какие снапшоты уже лежат в хранилище

Хранилище: ``~/.trendshift/snapshots/<period>/<UTC>.json`` (переопределяется
``TRENDSHIFT_HOME``). Сеть — только к trendshift.io.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

BASE_URL = "https://trendshift.io"
PERIODS = ("daily", "weekly", "monthly", "yearly")
PERIOD_PATHS = {"daily": "/", "weekly": "/weekly", "monthly": "/monthly", "yearly": "/yearly"}
USER_AGENT = "Mozilla/5.0 (compatible; trendshift-monitor/1.0; +local)"

#: Признаки ИИ-тематики в описании/тегах/языке — для фильтра ``--ai-only``.
AI_HINTS = (
    "ai", "llm", "agent", "agents", "gpt", "claude", "codex", "cursor", "copilot",
    "mcp", "rag", "embedding", "embeddings", "prompt", "prompts", "inference",
    "diffusion", "transformer", "neural", "machine learning", "deep learning",
    "model", "models", "openai", "anthropic", "gemini", "ollama", "vllm",
    "langchain", "vector", "fine-tune", "finetune", "training", "skill", "skills",
    "context", "reasoning", "token", "tokens", "chatbot", "vision", "multimodal",
)

#: Платформы, упоминание которых означает «о репозитории говорят».
PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', re.S)


# --------------------------------------------------------------------- разбор


def fetch_html(path: str, timeout: float = 40.0, retries: int = 2) -> str:
    """Скачать страницу trendshift.io. Ошибки сети — после ``retries`` повторов."""
    url = BASE_URL + path
    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, TimeoutError) as exc:  # noqa: PERF203
            last = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise TrendshiftError(f"{url}: {last}")


class TrendshiftError(RuntimeError):
    """Страница не скачалась или данные в ней не разобрались."""


def decode_flight(html: str) -> str:
    """Склеить RSC-поток Next.js в один текст (там лежат настоящие JSON-строки)."""
    parts: list[str] = []
    for match in PUSH_RE.finditer(html):
        try:
            parts.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            continue
    if not parts:
        raise TrendshiftError("в странице нет RSC-потока (изменилась разметка?)")
    return "".join(parts)


def extract_json(text: str, key: str) -> Any:
    """Достать значение ``"<key>": <json>`` из потока, не разбирая его целиком."""
    token = '"%s":' % key
    start = text.find(token)
    if start < 0:
        return None
    pos = start + len(token)
    while pos < len(text) and text[pos] not in "[{":
        pos += 1
    if pos >= len(text):
        return None
    depth = 0
    in_string = False
    escaped = False
    for idx in range(pos, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[pos : idx + 1])
                except json.JSONDecodeError as exc:
                    raise TrendshiftError(f"не разобрался блок {key!r}: {exc}") from exc
    raise TrendshiftError(f"блок {key!r} не закрыт")


def normalize(item: dict[str, Any], period: str, fetched_at: str) -> dict[str, Any]:
    """Привести запись trendshift к стабильному виду (имена полей не меняются)."""
    name = str(item.get("full_name") or "").strip()
    tags = [str(t.get("name")) for t in (item.get("tags") or []) if isinstance(t, dict)]
    created = item.get("repository_created_at") or ""
    return {
        "full_name": name,
        "url": "https://github.com/" + name if name else "",
        "rank": item.get("rank"),
        "score": item.get("score"),
        "stars": item.get("repository_stars"),
        "stars_gained": item.get("repository_stars_gained"),
        "forks": item.get("repository_forks"),
        "forks_gained": item.get("repository_forks_gained"),
        "language": item.get("repository_language") or item.get("language") or "",
        "description": (item.get("repository_description") or "").strip(),
        "created_at": created,
        "tags": tags,
        "mentions": item.get("social_mention_platforms") or [],
        "period": period,
        "fetched_at": fetched_at,
    }


def parse_period(html: str, period: str, fetched_at: str | None = None) -> list[dict[str, Any]]:
    """Разобрать HTML страницы периода в список нормализованных записей."""
    fetched_at = fetched_at or utc_now()
    raw = extract_json(decode_flight(html), "initialData")
    if not isinstance(raw, list):
        raise TrendshiftError(f"{period}: нет initialData")
    items = [normalize(it, period, fetched_at) for it in raw if isinstance(it, dict)]
    return [it for it in items if it["full_name"]]


def fetch_period(period: str, timeout: float = 40.0) -> list[dict[str, Any]]:
    """Скачать и разобрать один период."""
    if period not in PERIOD_PATHS:
        raise TrendshiftError(f"неизвестный период {period!r}; есть: {', '.join(PERIODS)}")
    return parse_period(fetch_html(PERIOD_PATHS[period], timeout=timeout), period)


# ------------------------------------------------------------------ хранилище


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def store_root() -> Path:
    env = os.environ.get("TRENDSHIFT_HOME")
    return Path(env).expanduser() if env else Path.home() / ".trendshift"


def snapshot_dir(period: str, root: Path | None = None) -> Path:
    return (root or store_root()) / "snapshots" / period


def list_snapshots(period: str, root: Path | None = None) -> list[Path]:
    """Снапшоты периода, от старых к новым."""
    directory = snapshot_dir(period, root)
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.json") if p.is_file())


def save_snapshot(period: str, items: list[dict[str, Any]], root: Path | None = None,
                  fetched_at: str | None = None) -> Path:
    """Записать снапшот. Имя файла — UTC-метка времени, поэтому сортируется."""
    stamp = (fetched_at or utc_now()).replace(":", "").replace("-", "")
    directory = snapshot_dir(period, root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp}.json"
    payload = {"period": period, "fetched_at": fetched_at or utc_now(), "items": items}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def load_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_latest(period: str, root: Path | None = None) -> dict[str, Any] | None:
    paths = list_snapshots(period, root)
    return load_snapshot(paths[-1]) if paths else None


def load_previous(period: str, root: Path | None = None) -> dict[str, Any] | None:
    paths = list_snapshots(period, root)
    return load_snapshot(paths[-2]) if len(paths) >= 2 else None


# ----------------------------------------------------------------------- дифф


def diff_items(old: Iterable[dict[str, Any]], new: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Сравнить два среза: кто вошёл, кто вышел, у кого скачок позиции."""
    old_list, new_list = list(old), list(new)
    old_rank = {it["full_name"]: it.get("rank") for it in old_list}
    new_rank = {it["full_name"]: it.get("rank") for it in new_list}
    entered = [it for it in new_list if it["full_name"] not in old_rank]
    dropped = [it for it in old_list if it["full_name"] not in new_rank]
    moved = []
    for it in new_list:
        before = old_rank.get(it["full_name"])
        after = it.get("rank")
        if before is None or after is None:
            continue
        delta = int(before) - int(after)  # >0 — поднялся
        if delta:
            moved.append({"full_name": it["full_name"], "from": before, "to": after, "delta": delta,
                          "url": it.get("url"), "stars_gained": it.get("stars_gained")})
    moved.sort(key=lambda m: -m["delta"])
    return {
        "entered": sorted(entered, key=lambda it: it.get("rank") or 999),
        "dropped": sorted(dropped, key=lambda it: it.get("rank") or 999),
        "moved": moved,
        "unchanged": len(new_list) - len(entered) - len([m for m in moved if m["from"] and m["to"]]),
    }


# -------------------------------------------------------------------- выборки


def is_ai(item: dict[str, Any]) -> bool:
    haystack = " ".join([item.get("description") or "", " ".join(item.get("tags") or []),
                         item.get("language") or "", item.get("full_name") or ""]).lower()
    return any(hint in haystack for hint in AI_HINTS)


def select(items: Iterable[dict[str, Any]], limit: int = 10, ai_only: bool = False,
           language: str | None = None, keyword: str | None = None,
           min_gain: int | None = None) -> list[dict[str, Any]]:
    """Отфильтровать и обрезать срез."""
    out = []
    for item in items:
        if ai_only and not is_ai(item):
            continue
        if language and (item.get("language") or "").lower() != language.lower():
            continue
        if min_gain is not None and (item.get("stars_gained") or 0) < min_gain:
            continue
        if keyword:
            hay = " ".join([item.get("full_name") or "", item.get("description") or "",
                            " ".join(item.get("tags") or [])]).lower()
            if keyword.lower() not in hay:
                continue
        out.append(item)
    return out[:limit]


# --------------------------------------------------------------------- вывод


def age_days(created_at: str) -> int | None:
    """Сколько дней репозиторию на момент запроса (None, если дата битая)."""
    if not created_at:
        return None
    try:
        created = dt.datetime.strptime(created_at[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None
    return max(0, (dt.datetime.now(dt.timezone.utc) - created).days)


def fmt_int(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def table(rows: list[dict[str, Any]], show_age: bool = True) -> str:
    """Компактная текстовая таблица: ранг, имя, звёзды, прирост, язык, возраст."""
    lines = []
    for it in rows:
        age = age_days(it.get("created_at") or "")
        created = f"{age}д" if age is not None else "—"
        tags = ",".join((it.get("tags") or [])[:2])
        head = f"{it.get('rank') or '—':>3}. {it['full_name']}  ★{fmt_int(it.get('stars'))} (+{fmt_int(it.get('stars_gained'))})"
        if it.get("language"):
            head += f"  [{it['language']}]"
        if show_age:
            head += f"  нов. {created}"
        if tags:
            head += f"  #{tags}"
        lines.append(head)
        if it.get("description"):
            lines.append("     " + it["description"][:180])
    return "\n".join(lines)


def markdown_table(rows: list[dict[str, Any]]) -> str:
    head = "| # | Репозиторий | ★ | Прирост | Язык | Возраст | О чём |\n|---|---|---|---|---|---|---|"
    body = []
    for it in rows:
        age = age_days(it.get("created_at") or "")
        desc = (it.get("description") or "").replace("|", "\\|")[:120]
        body.append(
            f"| {it.get('rank') or '—'} | [{it['full_name']}]({it.get('url')}) | {fmt_int(it.get('stars'))} "
            f"| +{fmt_int(it.get('stars_gained'))} | {it.get('language') or '—'} "
            f"| {str(age) + 'д' if age is not None else '—'} | {desc} |"
        )
    return "\n".join([head] + body)


# ---------------------------------------------------------------------- CLI


def cmd_top(args: argparse.Namespace) -> int:
    periods = args.period or ["daily"]
    collected: dict[str, list[dict[str, Any]]] = {}
    for period in periods:
        items = fetch_period(period, timeout=args.timeout)
        collected[period] = select(items, limit=args.limit, ai_only=args.ai_only,
                                   language=args.language, keyword=args.keyword,
                                   min_gain=args.min_gain)
        if not args.json:
            print(f"# trendshift · {period} · {utc_now()}")
            print(table(collected[period]) if collected[period] else "(пусто после фильтров)")
            print()
    if args.json:
        payload = collected[periods[0]] if len(periods) == 1 else collected
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    parts = []
    for period in args.period or PERIODS:
        items = fetch_period(period, timeout=args.timeout)
        rows = select(items, limit=args.limit, ai_only=args.ai_only, language=args.language)
        title = {"daily": "за сутки", "weekly": "за неделю",
                 "monthly": "за месяц", "yearly": "за год"}.get(period, period)
        parts.append(f"## {period} — {title}\n\n{markdown_table(rows)}\n")
    print(f"# GitHub-тренды ({dt.date.today().isoformat()})\n")
    print("\n".join(parts))
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    new = load_latest(args.period)
    old = load_previous(args.period)
    if not new:
        print("снапшотов нет — сначала `watch`", file=sys.stderr)
        return 2
    if not old:
        print("нужен хотя бы один предыдущий снапшот", file=sys.stderr)
        return 2
    delta = diff_items(old["items"], new["items"])
    print(f"# {args.period}: {old['fetched_at']} → {new['fetched_at']}")
    print(f"\nвошли: {len(delta['entered'])}, вышли: {len(delta['dropped'])}")
    if delta["entered"]:
        print("\n## новые в топе\n" + table(delta["entered"]))
    if delta["moved"]:
        print("\n## движение")
        for m in delta["moved"][: args.limit]:
            arrow = "↑" if m["delta"] > 0 else "↓"
            print(f"  {arrow}{abs(m['delta']):>2}  {m['full_name']}  {m['from']} → {m['to']}")
    if delta["dropped"]:
        print("\n## выпали\n" + "\n".join("  " + it["full_name"] for it in delta["dropped"]))
    if args.json:
        print(json.dumps(delta, ensure_ascii=False, indent=2))
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """Снапшот всех периодов + отчёт только об изменениях (для расписания)."""
    root = store_root()
    sections: list[str] = []
    total_new = 0
    for period in args.period or PERIODS:
        items = fetch_period(period, timeout=args.timeout)
        previous = load_latest(period, root)
        path = save_snapshot(period, items, root)
        if not previous:
            sections.append(f"## {period}\n\nпервый снапшот: {len(items)} записей ({path.name})")
            continue
        delta = diff_items(previous["items"], items)
        total_new += len(delta["entered"])
        rows = select(delta["entered"], limit=args.limit, ai_only=args.ai_only)
        chunk = [f"## {period}"]
        if rows:
            chunk.append(markdown_table(rows))
        else:
            chunk.append("новых в топе нет")
            top = select(items, limit=3, ai_only=args.ai_only)
            if top:
                chunk.append("сейчас: " + ", ".join(
                    f"{it['full_name']} (+{fmt_int(it.get('stars_gained'))})" for it in top))
        jumps = [m for m in delta["moved"] if m["delta"] >= args.jump]
        if jumps:
            chunk.append("\nСкачки: " + ", ".join(
                f"{m['full_name']} ({m['from']}→{m['to']})" for m in jumps[:5]))
        sections.append("\n\n".join(chunk))
    print(f"# trendshift, {utc_now()}\n\n" + "\n\n".join(sections))
    if args.quiet and total_new == 0:
        print("\n(без нового)", file=sys.stderr)
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    root = store_root()
    for period in PERIODS:
        paths = list_snapshots(period, root)
        print(f"{period}: {len(paths)} снапшот(ов)")
        for path in paths[-args.limit:]:
            data = load_snapshot(path)
            print(f"  {data.get('fetched_at')}  {len(data.get('items') or [])} записей  {path.name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trendshift", description="Тренды GitHub с trendshift.io")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--period", action="append", choices=PERIODS,
                       help="период; можно повторять (по умолчанию все)")
        p.add_argument("--limit", type=int, default=10)
        p.add_argument("--ai-only", action="store_true", help="только ИИ-тематика")
        p.add_argument("--language", help="фильтр по языку, например Python")
        p.add_argument("--timeout", type=float, default=40.0)
        p.add_argument("--json", action="store_true")

    top = sub.add_parser("top", help="текущий топ периода")
    add_common(top)
    top.add_argument("--keyword", help="подстрока в имени/описании/тегах")
    top.add_argument("--min-gain", type=int, help="минимальный прирост звёзд")
    top.set_defaults(func=cmd_top)

    digest = sub.add_parser("digest", help="markdown-сводка по периодам")
    add_common(digest)
    digest.set_defaults(func=cmd_digest)

    watch = sub.add_parser("watch", help="снапшот + только изменения")
    add_common(watch)
    watch.add_argument("--jump", type=int, default=3, help="порог скачка позиций")
    watch.add_argument("--quiet", action="store_true")
    watch.set_defaults(func=cmd_watch)

    diff = sub.add_parser("diff", help="разница двух последних снапшотов")
    diff.add_argument("--period", default="daily", choices=PERIODS)
    diff.add_argument("--limit", type=int, default=10)
    diff.add_argument("--json", action="store_true")
    diff.set_defaults(func=cmd_diff)

    history = sub.add_parser("history", help="что лежит в хранилище")
    history.add_argument("--limit", type=int, default=5)
    history.set_defaults(func=cmd_history)
    return parser


def force_utf8_stdio() -> None:
    """Консоль Windows по умолчанию cp1251 — звёзды и эмодзи в ней падают."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except TrendshiftError as exc:
        print(f"trendshift: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
