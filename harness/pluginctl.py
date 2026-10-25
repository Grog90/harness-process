"""Подготовка установленного плагина: указатель на корень и проверка манифеста.

Плагин работает в процессе DSH, поэтому не знает, где лежит проект. Вместо
переменных окружения (их надо ставить в систему и перезапускать приложение)
используется один файл-указатель: `~/.dsh/harness-root` (или `$DSH_HOME/harness-root`).
"""

from __future__ import annotations

import json
import os
from typing import Any

POINTER_NAME = "harness-root"
BUNDLE_REL = os.path.join("plugins", "harness-guard")


def dsh_home() -> str:
    return os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh")


def pointer_path() -> str:
    return os.path.join(dsh_home(), POINTER_NAME)


def bundle_dir(root: str) -> str:
    return os.path.join(root, BUNDLE_REL)


def read_pointer() -> str | None:
    try:
        with open(pointer_path(), encoding="utf-8") as fh:
            value = fh.read().strip()
    except OSError:
        return None
    return value or None


def write_pointer(root: str) -> str:
    path = pointer_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(os.path.abspath(root) + "\n")
    return path


def status(root: str) -> dict[str, Any]:
    """Что готово к работе плагина и что мешает."""
    target = os.path.abspath(root)
    pointer = read_pointer()
    bundle = bundle_dir(target)
    policy = os.path.join(target, "policies", "policy.json")

    manifest_ok = False
    manifest_error = ""
    manifest = os.path.join(bundle, "package.json")
    try:
        with open(manifest, encoding="utf-8") as fh:
            data = json.load(fh)
        manifest_ok = bool(data.get("dsh", {}).get("bundle", {}).get("patch"))
        if not manifest_ok:
            manifest_error = "в package.json нет dsh.bundle.patch"
    except OSError as exc:
        manifest_error = f"манифест не читается: {exc}"
    except json.JSONDecodeError as exc:
        manifest_error = f"манифест не JSON: {exc}"

    files = ["index.js", "guard-core.mjs", "cordis.patch.yml", "icon.svg",
             os.path.join("locale", "en.json"), os.path.join("locale", "ru.json")]
    missing = [f for f in files if not os.path.isfile(os.path.join(bundle, f))]

    checks = {
        "policy": os.path.isfile(policy),
        "manifest": manifest_ok,
        "bundle_files": not missing,
        "pointer": bool(pointer) and os.path.abspath(pointer) == target,
    }
    return {
        "root": target,
        "bundle": bundle,
        "policy": policy,
        "pointer": {"path": pointer_path(), "value": pointer},
        "manifest_error": manifest_error,
        "missing_files": missing,
        "checks": checks,
        "ok": all(checks.values()),
    }


def prepare(root: str) -> dict[str, Any]:
    """Записывает указатель на корень и возвращает отчёт о готовности."""
    path = write_pointer(root)
    report = status(root)
    report["pointer_written"] = path
    return report


def render(report: dict[str, Any]) -> str:
    checks = report["checks"]
    lines = [f"корень проекта: {report['root']}", f"пакет плагина: {report['bundle']}"]
    for key, label in (
        ("policy", "policies/policy.json на месте"),
        ("manifest", "манифест bundle корректен"),
        ("bundle_files", "файлы пакета на месте"),
        ("pointer", f"указатель {report['pointer']['path']} ведёт на этот корень"),
    ):
        lines.append(f"  [{'ok' if checks[key] else 'НЕТ'}] {label}")
    if report.get("pointer_written"):
        lines.append(f"  записан: {report['pointer_written']}")
    if report["missing_files"]:
        lines.append("  отсутствуют: " + ", ".join(report["missing_files"]))
    if report["manifest_error"]:
        lines.append("  " + report["manifest_error"])
    lines.append("ИТОГ: " + ("готов к включению" if report["ok"] else "не готов"))
    return "\n".join(lines)


# Точки контроля, которые реально существуют в коде (для отчёта о покрытии матрицы).
CONTROL_POINTS = {
    "plugin.status": status,
}
