#!/usr/bin/env python3
"""Учебный «агент» для демонстрации полного цикла.

Он не содержит LLM: его задача — показать контракт, которому следует любой
исполнитель. Настоящий агент (CLI-модель, скрипт, фреймворк) подключается точно
так же: получает окружение, работает через шлюз, возвращает код 0.

Что делает:
  1. читает бриф из HARNESS_BRIEF;
  2. если есть HARNESS_FEEDBACK — учитывает замечания предыдущей проверки;
  3. пишет реализацию через шлюз (не напрямую в файл);
  4. завершаетcя с кодом 0 или 3 при отказе шлюза.
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # каталог examples, где лежит harness_client.py

from harness_client import HarnessClient, ToolDenied  # noqa: E402

FIXED = '''"""Комиссия 2.5% (исправленная версия после проверки)."""

RATE = 0.025


def calculate_fee(amount_minor: int) -> int:
    if amount_minor < 0:
        raise ValueError("FEE_NEGATIVE")
    return int(amount_minor * RATE + 0.5)
'''

BROKEN = '''RATE = 0.025


def calculate_fee(amount_minor: int) -> int:
    if amount_minor < 0:
        return 0
    return int(round(amount_minor * RATE))
'''


def main() -> int:
    root = os.environ.get("HARNESS_ROOT", ".")
    client = HarnessClient(root=root)

    brief_path = os.environ.get("HARNESS_BRIEF")
    brief = {}
    if brief_path and os.path.isfile(brief_path):
        with open(brief_path, encoding="utf-8") as fh:
            brief = json.load(fh)

    target = (brief.get("allowed_files") or ["src/fee.py"])[0]
    feedback_path = os.environ.get("HARNESS_FEEDBACK")

    # первая попытка «ошибается», доработка — исправляет
    content = BROKEN if not feedback_path else FIXED
    if feedback_path and os.path.isfile(feedback_path):
        with open(feedback_path, encoding="utf-8") as fh:
            packet = json.load(fh)
        print(f"учтены замечания: {packet.get('open_finding_ids')}")

    try:
        result = client.write(target, content)
    except ToolDenied as exc:
        print(f"шлюз отказал: {exc}", file=sys.stderr)
        return 3

    print(f"записано {result['bytes']} байт в {result['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
