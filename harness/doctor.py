"""Проверка конфигурации: то, что нельзя увидеть из тестов.

Слой может быть исправен и при этом работать вхолостую: песочница выключена
режимом `danger-full-access`, плагин не установлен, указатель на корень потерян,
а права висят после отменённой задачи. Эти вещи видно только в живой среде,
поэтому они собраны в одну команду.
"""

from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass
from typing import Any

from . import pluginctl

PLUGIN_PACKAGE = "@harness/dsh-guard"
SANDBOX_MODES = ("read-only", "workspace-write", "danger-full-access")


@dataclass
class Finding:
    id: str
    ok: bool
    title: str
    detail: str = ""
    action: str = ""
    severity: str = "warn"  # warn | fail

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ok": self.ok,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "action": self.action,
        }


def _profile_dir() -> str:
    return os.environ.get("DSH_PROFILE_DIR") or os.path.join(pluginctl.dsh_home(), "profiles", "desktop")


def _installed_bundles(profile: str) -> list[str]:
    path = os.path.join(profile, "package.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    bundles = (data.get("dsh") or {}).get("profile", {}).get("bundles") or []
    deps = (data.get("dependencies") or {}).keys()
    return list(bundles) + list(deps)


def installed_packages(profile: str | None = None) -> set[str]:
    """Пакеты, реально стоящие в профиле: по ним проверяется делегирование."""
    return {str(b) for b in _installed_bundles(profile or _profile_dir())}


def _permission_presets(profile: str) -> dict[str, Any]:
    """Пресеты подтверждений из патча профиля (если он есть)."""
    path = os.path.join(profile, "cordis.patch.yml")
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}
    presets: dict[str, Any] = {}
    current = None
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped in (f"{m}:" for m in SANDBOX_MODES):
            current = stripped[:-1]
            presets[current] = {}
        elif current and stripped.startswith("sandbox:"):
            presets[current]["sandbox"] = stripped.split(":", 1)[1].strip()
        elif current and stripped.startswith("approval:"):
            presets[current]["approval"] = stripped.split(":", 1)[1].strip()
        elif line and not line.startswith(" ") and current:
            current = None
    return presets


def _stale_scope(root: str) -> Finding:
    from .scope import read_scope
    from .state import Store

    scope = read_scope(root)
    if not scope:
        return Finding("scope", True, "область прав не висит", "guard-scope.json отсутствует")
    db = os.path.join(root, ".harness", "state.db")
    if not os.path.isfile(db):
        return Finding("scope", False, "область прав без задачи",
                       f"файл есть, состояния нет: {scope}", "удалите .harness/guard-scope.json")
    store = Store(db)
    try:
        tasks = store.list_tasks()
    finally:
        store.close()
    running = [t for t in tasks if t["state"] == "RUNNING"]
    if running:
        return Finding("scope", True, "область прав соответствует задаче",
                       f"активная задача: {running[0]['task_id']}")
    return Finding("scope", False, "область прав висит после задачи",
                   f"активных задач нет, файл: {scope}", "harness event --task <id> --event cancel")


def run(root: str) -> dict[str, Any]:
    root = os.path.abspath(root)
    profile = _profile_dir()
    findings: list[Finding] = []

    policy = os.path.join(root, "policies", "policy.json")
    findings.append(Finding(
        "policy", os.path.isfile(policy),
        "политика на месте" if os.path.isfile(policy) else "нет policies/policy.json",
        policy, "скопируйте policies/ из репозитория harness",
    ))

    bundles = _installed_bundles(profile)
    installed = any(PLUGIN_PACKAGE in str(b) for b in bundles)
    findings.append(Finding(
        "plugin", installed,
        "плагин установлен" if installed else "плагин не установлен в профиль",
        f"профиль: {profile}",
        "Plugins → Add plugin → путь к plugins/harness-guard",
    ))

    pointer = pluginctl.read_pointer()
    pointer_ok = bool(pointer) and os.path.abspath(pointer) == root
    findings.append(Finding(
        "pointer", pointer_ok,
        "указатель на корень верный" if pointer_ok else "указатель на корень не настроен",
        f"{pluginctl.pointer_path()} → {pointer}", "python -m harness --root . plugin prepare",
    ))

    mode = os.environ.get("DSH_PERMISSION_MODE", "")
    presets = _permission_presets(profile)
    if mode:
        ok = mode in ("workspace-write", "read-only")
        detail = f"DSH_PERMISSION_MODE={mode}"
    else:
        # Режим сессии живёт в UI и не виден процессу: честнее предупредить,
        # чем считать песочницу работающей по факту наличия пресета.
        ok = False
        detail = "DSH_PERMISSION_MODE не задан; режим переключается в UI (/permission)"
        if presets:
            detail += "; пресеты профиля: " + ", ".join(sorted(presets))
    findings.append(Finding(
        "sandbox", ok,
        "рабочий режим ограничен" if ok else "песочница не подтверждена",
        detail,
        "переключите сессию на workspace-write: /permission",
    ))

    findings.append(_stale_scope(root))

    failed = [f for f in findings if not f.ok]
    return {
        "root": root,
        "profile": profile,
        "mode": mode,
        "checks": [f.to_dict() for f in findings],
        "ok": not failed,
        "warnings": len(failed),
    }


def render(report: dict[str, Any]) -> str:
    lines = [f"корень: {report['root']}", f"профиль: {report['profile']}", ""]
    for check in report["checks"]:
        mark = "ok " if check["ok"] else "!! "
        lines.append(f"  [{mark}] {check['title']}")
        if check["detail"]:
            lines.append(f"         {check['detail']}")
        if not check["ok"] and check["action"]:
            lines.append(f"         → {check['action']}")
    lines.append("")
    lines.append("ИТОГ: " + ("конфигурация согласована" if report["ok"] else f"требует внимания: {report['warnings']}"))
    return "\n".join(lines)


# Точки контроля, которые реально существуют в коде (для отчёта о покрытии матрицы).
CONTROL_POINTS = {
    "doctor.run": run,
}
