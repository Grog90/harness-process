"""Снимок кандидата: хеш содержимого дерева.

Зачем: вердикт обязан быть привязан к конкретному состоянию файлов, иначе
PASS можно «перенести» на изменённый код (R-SNAPSHOT-1). Хеш ловит изменение,
но не подтверждает авторство — это записано в остаточном риске правила.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from typing import Iterable

from .policy import service_base

DEFAULT_EXCLUDES = (
    ".git/**",
    ".harness/**",
    "node_modules/**",
    "__pycache__/**",
    "*.pyc",
    ".pytest_cache/**",
    "dist/**",
    "build/**",
)


def _iter_files(root: str, include: Iterable[str] | None, exclude: Iterable[str]) -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _is_excluded(os.path.relpath(os.path.join(dirpath, d), root), exclude))
        for name in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, name), root).replace("\\", "/")
            if include and not any(fnmatch.fnmatch(rel.lower(), pat.lower()) for pat in include):
                continue
            if _is_excluded(rel, exclude):
                continue
            out.append(rel)
    return sorted(out)


def _is_excluded(rel: str, exclude: Iterable[str]) -> bool:
    low = rel.replace("\\", "/").lower()
    return any(fnmatch.fnmatch(low, pat.lower()) for pat in exclude)


def manifest(root: str, include: Iterable[str] | None = None, exclude: Iterable[str] = DEFAULT_EXCLUDES) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for rel in _iter_files(root, include, exclude):
        full = os.path.join(root, rel)
        with open(full, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        entries.append({"path": rel, "sha256": digest})
    return entries


def compute_snapshot(root: str, include: Iterable[str] | None = None, exclude: Iterable[str] = DEFAULT_EXCLUDES) -> str:
    """Идентификатор снимка: sha256 по каноническому манифесту путей и хешей."""
    m = manifest(root, include=include, exclude=exclude)
    payload = json.dumps(m, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "snap_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_hash(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


WORKTREE_EXCLUDES = (
    ".git/**",
    ".harness/**",
    "evidence/**",
    "**/__pycache__/**",
    "__pycache__/**",
    "node_modules/**",
    "dist/**",
    "build/**",
    ".pytest_cache/**",
    "*.pyc",
    "*.db",
    "*.db-wal",
    "*.db-shm",
)


def compute_for_scope(root: str, scope: str = "") -> str:
    """Снимок в объявленной области: '' — всё рабочее дерево, иначе подкаталог.

    Область хранится вместе с задачей: без неё нельзя доказать, что проверяли тот же
    набор файлов, который собираются коммитить.
    """
    base = os.path.join(root, scope) if scope else root
    excludes = WORKTREE_EXCLUDES if not scope else DEFAULT_EXCLUDES
    return compute_snapshot(base, exclude=excludes)


def scope_of(root: str, path: str) -> str:
    """Нормализует область снимка: относительный путь внутри корня или '' для корня."""
    if not path:
        return ""
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root)).replace("\\", "/")
    if rel in (".", ""):
        return ""
    if rel.startswith(".."):
        raise ValueError(f"область снимка вне корня проекта: {path!r}")
    return rel

def files_snapshot(root: str, paths: "Iterable[str]", services: "Iterable[dict] | None" = None) -> str:
    """Хеш только перечисленных файлов: доказательство, что сам кандидат не менялся.

    Зачем: снимок дерева ловит любую правку, включая чужие файлы, которые владелец
    закоммитил уже после PASS. Такой сдвиг не переделывает кандидата, и повторная
    проверка (recheck) должна это доказать, а не поверить на слово. Отсутствующий
    файл учитывается отдельной меткой: иначе создание или удаление файла из набора
    не изменило бы хеш. Ключи `service:<name>/<rel>` разрешаются в сервисный корень.
    """
    services = list(services or [])
    entries = []
    for rel in sorted({str(p).replace("\\", "/") for p in paths}):
        if rel.startswith("service:"):
            rest = rel[len("service:"):]
            name = rest.split("/", 1)[0]
            inner = rest.split("/", 1)[1] if "/" in rest else ""
            service = next((s for s in services if str(s.get("name")) == name), None)
            full = os.path.join(service_base(root, service), inner) if service else ""
        else:
            full = os.path.join(root, rel)
        digest = file_hash(full) if full and os.path.isfile(full) else "missing"
        entries.append({"path": rel, "sha256": digest})
    payload = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "files_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()