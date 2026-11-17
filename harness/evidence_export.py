"""Экспорт evidence в доверенный каталог.

cosign подписывает отчёт в CI, но artifact живёт по срокам GitHub. Здесь evidence
копируется в каталог, который оператор считает доверенным (NAS, S3-gateway,
сетевой диск), вместе с sha256-манифестом; `verify` проверяет, что копия не
испорчена. Никакого облачного SDK: только файлы и хэши.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from typing import Any


class ExportError(RuntimeError):
    def __init__(self, rule: str, message: str, **details: Any):
        super().__init__(message)
        self.rule = rule
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "rule": self.rule, "reason": str(self), "details": self.details}


MANIFEST = "evidence-export.json"


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files(base: str) -> list[str]:
    out: list[str] = []
    for dirpath, _dirs, names in os.walk(base):
        for name in names:
            full = os.path.join(dirpath, name)
            out.append(os.path.relpath(full, base).replace("\\", "/"))
    return sorted(out)


def export(root: str, dest: str, *, change: str | None = None) -> dict[str, Any]:
    """Копирует evidence/<change>|evidence целиком в dest и пишет манифест."""
    root = os.path.abspath(root)
    dest = os.path.abspath(dest)
    source = os.path.join(root, "evidence")
    if change:
        source = os.path.join(source, f"change-{change}")
    if not os.path.isdir(source):
        raise ExportError("R-EVID-1", f"evidence не найден: {source}")
    src_real = os.path.realpath(source)
    dst_real = os.path.realpath(dest)
    try:
        common = os.path.commonpath([src_real, dst_real])
    except ValueError:
        common = ""
    if common in (src_real, dst_real):
        raise ExportError("R-EVID-1", "источник и назначение не должны совпадать или вкладываться",
                          source=src_real, dest=dst_real)
    os.makedirs(dest, exist_ok=True)
    copied: list[dict[str, Any]] = []
    for rel in _files(source):
        src = os.path.join(source, rel)
        dst = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        source_hash = _sha256(src)
        shutil.copy2(src, dst)
        dest_hash = _sha256(dst)
        if source_hash != dest_hash:
            raise ExportError("R-EVID-1", f"копия не совпала с источником: {rel}")
        copied.append({"path": rel, "sha256": source_hash, "copied_sha256": dest_hash,
                       "bytes": os.path.getsize(dst)})
    manifest = {
        "ok": True,
        "source": source,
        "dest": dest,
        "change": change or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": copied,
        "total": len(copied),
    }
    with open(os.path.join(dest, MANIFEST), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return manifest


def verify(dest: str) -> dict[str, Any]:
    """Сверяет файлы с манифестом; порча/пропажа — отказ."""
    dest = os.path.abspath(dest)
    path = os.path.join(dest, MANIFEST)
    try:
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ExportError("R-EVID-1", f"манифест не прочитан: {exc}") from exc
    problems: list[str] = []
    for item in manifest.get("files", []):
        rel = str(item.get("path"))
        if os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/"):
            problems.append(f"недопустимый путь в манифесте: {rel}")
            continue
        target = os.path.join(dest, rel)
        if not os.path.isfile(target):
            problems.append(f"нет файла: {rel}")
        elif _sha256(target) != item.get("sha256"):
            problems.append(f"хэш не совпал: {rel}")
    return {"ok": not problems, "files": len(manifest.get("files", [])),
            "problems": problems, "source": manifest.get("source", "")}


CONTROL_POINTS = {"evidence.export": export, "evidence.verify": verify}
