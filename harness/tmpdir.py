"""Временные каталоги внутри проекта.

Песочница DSH по умолчанию — `workspace-write`: писать можно только под рабочей
папкой. Системный `%TEMP%` при этом закрыт, а `tempfile.mkdtemp` создаёт каталог с
такой дискреционной защитой, что ограниченный процесс не может создать в нём
дочерний элемент (`WinError 5`). Поэтому рабочие копии создаются обычным
`makedirs` под `.harness/tmp`: каталог наследует права родителя.
"""

from __future__ import annotations

import os
import uuid


def local_base(root: str | None = None) -> str:
    base = os.path.join(root or os.getcwd(), ".harness", "tmp")
    os.makedirs(base, exist_ok=True)
    return base


def scratch(root: str | None = None, prefix: str = "harness-") -> str:
    """Каталог для разовой работы внутри проекта с наследуемыми правами."""
    base = local_base(root)
    for _ in range(50):
        path = os.path.join(base, f"{prefix}{uuid.uuid4().hex[:12]}")
        try:
            os.makedirs(path)
            return path
        except FileExistsError:
            continue
    raise RuntimeError(f"не удалось создать рабочий каталог в {base}")


def cleanup(path: str) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
