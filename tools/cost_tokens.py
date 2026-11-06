#!/usr/bin/env python3
"""cost_tokens.py — перевод `usage` модели в эквивалент токенов.

Деньги здесь не считаются: тарифы и валюта зависят от провайдера, поэтому
стоимость приводится к сопоставимой единице — свежему входному токену.
Четыре слагаемых `usage` (вход, чтение кэша, запись кэша, выход) получают
веса из модели microsoft/TokenEconomizer и складываются в `token_equiv`.

Команды:
    effective <файл|->                 — посчитать эквивалент, ничего не писать
    record <файл|-> --task ID [...]    — записать измерение в журнал стоимости
    show --task ID [--unit U]          — суммы по видам, работа и накладные

`usage` читается из JSON-файла в UTF-8 или из stdin (``-``). Неполный,
нечисловой или отрицательный `usage` — ошибка с именем поля, а не ноль.

Только стандартная библиотека, Python 3.10+.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

# Корень проекта — родитель каталога tools: без этого `import harness`
# не работает при запуске `python tools/cost_tokens.py` из другого каталога.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CACHE_WEIGHTS: dict[str, float] = {
    "input": 1.0,
    "cache_read": 0.10,
    "cache_write": 1.25,
    "output": 6.0,
}
TOKEN_UNIT = "token_equiv"
DEFAULT_DB = ".harness/state.db"

#: Поле `usage` -> ключ нормализованного словаря.
FIELDS = {
    "input_tokens": "input",
    "cache_read_input_tokens": "cache_read",
    "cache_creation_input_tokens": "cache_write",
    "output_tokens": "output",
}


class UsageError(ValueError):
    """Испорченное или неполное `usage`; текст называет поле."""


def _number(payload: Mapping[str, Any], field: str, default: float | None = None) -> float:
    if field not in payload:
        if default is None:
            raise UsageError(f"{field}: обязательное поле отсутствует")
        return default
    value = payload[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UsageError(f"{field}: ожидается число, получено {type(value).__name__}")
    number = float(value)
    if number < 0:
        raise UsageError(f"{field}: отрицательное значение {number:g}")
    return number


def normalize_usage(usage: Mapping[str, Any]) -> dict[str, float]:
    """Привести `usage` к четырём ключам; обёртка ``{"usage": {...}}`` тоже принимается."""
    if isinstance(usage, Mapping) and isinstance(usage.get("usage"), Mapping):
        usage = usage["usage"]
    if not isinstance(usage, Mapping):
        raise UsageError(f"usage: ожидается объект, получено {type(usage).__name__}")
    return {
        "input": _number(usage, "input_tokens"),
        "cache_read": _number(usage, "cache_read_input_tokens", 0.0),
        "cache_write": _number(usage, "cache_creation_input_tokens", 0.0),
        "output": _number(usage, "output_tokens", 0.0),
    }


def _weighed(normalized: Mapping[str, float], weights: Mapping[str, float]) -> float:
    table = CACHE_WEIGHTS if weights is None else weights
    for key in CACHE_WEIGHTS:
        if key not in table:
            raise UsageError(f"weights.{key}: отсутствует обязательный вес")
    total = 0.0
    for key, amount in normalized.items():
        weight = table[key]
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise UsageError(f"weights.{key}: ожидается число")
        if float(weight) < 0:
            raise UsageError(f"weights.{key}: отрицательный вес")
        total += amount * float(weight)
    return float(total)


def effective_tokens(usage: Mapping[str, Any], weights: Mapping[str, float] | None = None) -> float:
    """Эквивалент свежих входных токенов. Переданная таблица заменяет веса целиком."""
    return _weighed(normalize_usage(usage), weights)


def parse_usage(text: str) -> dict[str, Any]:
    """Разобрать JSON с `usage`; битый JSON — `UsageError`, а не traceback."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UsageError(f"usage: некорректный JSON ({exc.msg})") from exc
    if not isinstance(data, Mapping):
        raise UsageError(f"usage: ожидается JSON-объект, получено {type(data).__name__}")
    return dict(data)


def read_usage(source: str) -> str:
    """Прочитать `usage` из файла UTF-8 или из stdin при источнике ``-``."""
    if source == "-":
        return sys.stdin.read()
    with open(source, encoding="utf-8") as handle:
        return handle.read()


def _fmt(value: float) -> str:
    return f"{value:g}"


def record_cost(
    store: Any,
    task_id: str,
    usage: Mapping[str, Any],
    *,
    kind: str = "worker",
    attempt: int | None = None,
    unit: str = TOKEN_UNIT,
    note: str = "",
) -> dict[str, Any]:
    """Посчитать эквивалент и записать его в журнал стоимости задачи."""
    normalized = normalize_usage(usage)
    tokens = _weighed(normalized, CACHE_WEIGHTS)
    breakdown = (
        f"in={_fmt(normalized['input'])} read={_fmt(normalized['cache_read'])} "
        f"write={_fmt(normalized['cache_write'])} out={_fmt(normalized['output'])} "
        f"tokens={_fmt(tokens)} weights=default"
    )
    note_text = breakdown if not note else f"{breakdown} · {note}"

    from harness import cost as harness_cost

    entry = harness_cost.record(
        store, task_id, tokens, unit=unit, kind=kind, attempt=attempt, note=note_text
    )
    return {
        "tokens": tokens,
        "entry_id": entry["entry_id"],
        "unit": unit,
        "kind": kind,
        "attempt": attempt,
    }


def _selected(entries: Iterable[Mapping[str, Any]], unit: str) -> list[Mapping[str, Any]]:
    return [entry for entry in entries if entry.get("unit") == unit]


def by_kind(entries: Iterable[Mapping[str, Any]], unit: str = TOKEN_UNIT) -> dict[str, float]:
    """Суммы по видам расхода; записи другой единицы не учитываются."""
    totals: dict[str, float] = {}
    for entry in _selected(entries, unit):
        kind = str(entry.get("kind") or "unknown")
        totals[kind] = totals.get(kind, 0.0) + float(entry.get("amount") or 0.0)
    return {kind: round(total, 4) for kind, total in totals.items()}


def overhead_share(
    entries: Iterable[Mapping[str, Any]],
    unit: str = TOKEN_UNIT,
    work_kinds: Iterable[str] = ("worker", "model"),
) -> dict[str, Any]:
    """Работа, накладные расходы и их доля. Пустая выборка даёт `None`, а не 0."""
    selected = _selected(entries, unit)
    kinds = by_kind(selected, unit)
    work_set = set(work_kinds)
    work = sum(total for kind, total in kinds.items() if kind in work_set)
    total = sum(kinds.values())
    overhead = total - work
    return {
        "work": round(work, 4),
        "overhead": round(overhead, 4),
        "overhead_frac": None if total == 0 else round(overhead / total, 4),
        "entries": len(selected),
    }


def open_store(db_path: str = DEFAULT_DB) -> Any:
    """Открыть журнал состояния, создав каталог под базу при необходимости."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
    from harness.state import Store

    return Store(db_path)


def _usage_from(source: str) -> dict[str, Any]:
    return parse_usage(read_usage(source))


def _cmd_effective(args: argparse.Namespace) -> int:
    usage = _usage_from(args.source)
    result = {
        "tokens": effective_tokens(usage),
        "unit": TOKEN_UNIT,
        "usage": normalize_usage(usage),
        "weights": dict(CACHE_WEIGHTS),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    usage = _usage_from(args.source)
    store = open_store(args.db)
    try:
        result = record_cost(
            store,
            args.task,
            usage,
            kind=args.kind,
            attempt=args.attempt,
            unit=args.unit,
            note=args.note,
        )
    finally:
        store.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    store = open_store(args.db)
    try:
        entries = store.costs_for(args.task)
    finally:
        store.close()
    selected = _selected(entries, args.unit)
    share = overhead_share(entries, args.unit)
    result = {
        "task": args.task,
        "unit": args.unit,
        "total": round(sum(float(entry.get("amount") or 0.0) for entry in selected), 4),
        "by_kind": by_kind(entries, args.unit),
        **share,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=None, help=f"база журнала (по умолчанию {DEFAULT_DB})")

    parser = argparse.ArgumentParser(
        prog="cost_tokens.py",
        description="Измерение стоимости задачи в эквиваленте токенов.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    effective = sub.add_parser(
        "effective", parents=[common], help="посчитать эквивалент без записи"
    )
    effective.add_argument("source", help="файл с usage или - для stdin")
    effective.set_defaults(func=_cmd_effective)

    record = sub.add_parser("record", parents=[common], help="записать измерение")
    record.add_argument("source", help="файл с usage или - для stdin")
    record.add_argument("--task", required=True, help="идентификатор задачи")
    record.add_argument("--kind", default="worker", help="вид расхода (worker, verifier, ...)")
    record.add_argument("--attempt", type=int, default=None, help="номер попытки")
    record.add_argument("--unit", default=TOKEN_UNIT, help=f"единица журнала ({TOKEN_UNIT})")
    record.add_argument("--note", default="", help="комментарий к записи")
    record.set_defaults(func=_cmd_record)

    show = sub.add_parser("show", parents=[common], help="суммы по видам и доля накладных")
    show.add_argument("--task", required=True, help="идентификатор задачи")
    show.add_argument("--unit", default=TOKEN_UNIT, help=f"единица журнала ({TOKEN_UNIT})")
    show.set_defaults(func=_cmd_show)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.db is None:
        args.db = DEFAULT_DB
    try:
        return int(args.func(args))
    except (UsageError, OSError, ValueError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
