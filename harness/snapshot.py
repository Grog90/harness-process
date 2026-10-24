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
