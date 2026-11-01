"""Мост между OpenSpec-change и брифом задачи.

Спека отвечает на вопрос «что должно быть верно», бриф — «кто, чем и в каких границах
это делает». Пока эти два документа пишут руками, они расходятся: в спеке сценарий,
в брифе критерий, в задачах третий список файлов. Здесь один источник — каталог change:
сценарии становятся критериями приёмки, упомянутые в задачах файлы — границами записи.

Чего модуль намеренно не делает: не придумывает команды проверки под каждый сценарий.
Пока сценарии не названы тестами, честная команда одна — прогон набора. Врать, что
критерий проверен отдельным тестом, хуже, чем признать, что он проверен набором.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from typing import Any

SPEC_NAME = "spec.md"
CODE_PREFIXES = ("harness/", "tests/", "tools/", "examples/", "src/", "bin/", "plugins/")
WRITABLE_EXT = (".py", ".json", ".ps1", ".cmd", ".toml")
DEFAULT_CHECK = "python -m unittest discover -s tests -v"

_REQ = re.compile(r"^###\s+Requirement:\s*(.+?)\s*$")
_SCEN = re.compile(r"^####\s+Scenario:\s*(.+?)\s*$")
_SECTION = re.compile(r"^##\s+")
_WHEN = re.compile(r"^[-*]\s+\*\*WHEN\*\*\s*(.+?)\s*$", re.IGNORECASE)
_THEN = re.compile(r"^[-*]\s+\*\*THEN\*\*\s*(.+?)\s*$", re.IGNORECASE)
_TICK = re.compile(r"`([^`\n]+)`")


class SpecBriefError(RuntimeError):
    """Change нельзя превратить в бриф: причина называется, а не угадывается."""


def change_path(root: str, change: str) -> str:
    """Живой change, а если он уже архивный — его копия в archive/<дата>-<имя>.

    Артефакты архивации не удаляются: бриф по завершённому change можно собрать
    заново, не поднимая change из архива.
    """
    base = os.path.join(os.path.abspath(root), "openspec", "changes")
    live = os.path.join(base, change)
    if os.path.isdir(live):
        return live
    archive = os.path.join(base, "archive")
    if os.path.isdir(archive):
        for name in sorted(os.listdir(archive), reverse=True):
            if name == change or name.endswith("-" + change):
                return os.path.join(archive, name)
    return live


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def parse_scenarios(text: str) -> list[dict[str, str]]:
    """Сценарии из дельты спеки: требование, имя сценария, WHEN/THEN."""
    out: list[dict[str, str]] = []
    requirement = ""
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        m_req = _REQ.match(line)
        if m_req:
            requirement = m_req.group(1)
            current = None
            continue
        m_scen = _SCEN.match(line)
        if m_scen:
            current = {"requirement": requirement, "scenario": m_scen.group(1), "when": "", "then": ""}
            out.append(current)
            continue
        if _SECTION.match(line):
            current = None
            continue
        if current is None:
            continue
        m_when = _WHEN.match(line)
        if m_when:
            current["when"] = m_when.group(1)
            continue
        m_then = _THEN.match(line)
        if m_then:
            current["then"] = m_then.group(1)
    return out


def statement(scenario: dict[str, str]) -> str:
    when = scenario.get("when") or "условие не задано"
    then = scenario.get("then") or "результат не задан"
    return f"когда {when} — тогда {then}"


def parse_paths(text: str) -> list[str]:
    """Файлы, упомянутые в задачах в обратных кавычках, в порядке появления."""
    seen: list[str] = []
    for candidate in _TICK.findall(text):
        path = candidate.strip().replace("\\", "/")
        if " " in path or "/" not in path:
            continue
        if not path.endswith(WRITABLE_EXT):
            continue
        base = os.path.basename(path)
        if base.startswith(".") or path.startswith(("openspec/", "journal/")):
            continue
        if path not in seen:
            seen.append(path)
    return seen


def parse_test_modules(text: str) -> list[str]:
    """Модули вида `tests.test_lanes` или `tests/test_lanes.py` из команд в задачах."""
    found: list[str] = []
    for match in re.finditer(r"\btests[./]test_[A-Za-z0-9_]+", text):
        path = match.group(0).replace(".", "/") + ".py"
        if path not in found:
            found.append(path)
    return found


def parse_identifiers(text: str) -> list[str]:
    """Имена в обратных кавычках, похожие на идентификаторы кода (snake_case)."""
    names: list[str] = []
    for candidate in _TICK.findall(text):
        token = candidate.strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,}", token):
            continue
        if token not in names:
            names.append(token)
    return names


def _digest(paths: list[str]) -> str:
    h = hashlib.sha256()
    for path in sorted(paths):
        h.update(os.path.basename(path).encode())
        h.update(_read(path).encode("utf-8"))
    return h.hexdigest()[:8]


def _git(root: str, *args: str) -> str:
    try:
        proc = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _why(proposal: str) -> str:
    lines = proposal.splitlines()
    for index, line in enumerate(lines):
        if line.strip().lower().startswith("## why"):
            body: list[str] = []
            for follow in lines[index + 1:]:
                if follow.startswith("## "):
                    break
                if follow.strip():
                    body.append(follow.strip())
            text = " ".join(body).strip()
            if not text:
                return ""
            first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
            return first.strip()
    return ""


def collect(root: str, change: str) -> dict[str, Any]:
    """Читает change и возвращает разобранные части. Побочных эффектов нет."""
    path = change_path(root, change)
    if not os.path.isdir(path):
        raise SpecBriefError(f"change не найден: {os.path.relpath(path, root)}")
    proposal_file = os.path.join(path, "proposal.md")
    tasks_file = os.path.join(path, "tasks.md")
    if not os.path.isfile(proposal_file):
        raise SpecBriefError("в change нет proposal.md")
    if not os.path.isfile(tasks_file):
        raise SpecBriefError("в change нет tasks.md")

    spec_files: list[str] = []
    specs_root = os.path.join(path, "specs")
    for dirpath, _dirnames, filenames in os.walk(specs_root):
        for name in sorted(filenames):
            if name == SPEC_NAME:
                spec_files.append(os.path.join(dirpath, name))
    if not spec_files:
        raise SpecBriefError("в change нет specs/**/spec.md: критериям неоткуда взяться")

    scenarios: list[dict[str, str]] = []
    for spec_file in spec_files:
        scenarios.extend(parse_scenarios(_read(spec_file)))
    if not scenarios:
        raise SpecBriefError("в спеке нет сценариев: сценарий — это будущий критерий приёмки")

    proposal_text = _read(proposal_file)
    tasks_text = _read(tasks_file)
    writable = [p for p in parse_paths(tasks_text) if p.startswith(CODE_PREFIXES)]
    for test_path in parse_test_modules(tasks_text):
        if test_path not in writable:
            writable.append(test_path)
    if not writable:
        raise SpecBriefError("в tasks.md нет файлов кода: границы записи неизвестны")

    docs = [p for p in parse_paths(tasks_text) if not p.startswith(CODE_PREFIXES)]
    return {
        "path": path,
        "spec_files": spec_files,
        "scenarios": scenarios,
        "writable": writable,
        "docs": docs,
        "names": parse_identifiers(tasks_text + "\n" + proposal_text),
        "why": _why(proposal_text),
        "digest": _digest(spec_files),
    }


def build(
    root: str,
    change: str,
    *,
    task_id: str | None = None,
    base_commit: str | None = None,
    check_commands: list[str] | None = None,
    attempt_limit: int = 3,
    timeout_seconds: int = 3600,
) -> dict[str, Any]:
    """Собирает бриф из change. Возвращает данные, ничего не пишет на диск."""
    parts = collect(root, change)
    checks = list(check_commands or [DEFAULT_CHECK])
    criteria = [
        {
            "id": f"AC-{index}",
            "statement": statement(scenario),
            "mandatory": True,
            "check": checks[0],
        }
        for index, scenario in enumerate(parts["scenarios"], 1)
    ]
    goal = parts["why"] or f"Реализовать change {change}"
    behavior = " ".join(statement(s) for s in parts["scenarios"])
    if len(behavior) > 900:
        behavior = behavior[:897].rstrip() + "..."

    brief: dict[str, Any] = {
        "task_id": task_id or change,
        "brief_version": 1,
        "base_commit": base_commit or _git(root, "rev-parse", "--short", "HEAD") or "HEAD",
        "spec_version": f"openspec:{change}@{parts['digest']}",
        "goal": goal,
        "expected_behavior": behavior,
        "scope": {
            "in_scope": parts["writable"],
            "out_of_scope": parts["docs"] or ["openspec/**"],
        },
        "allowed_read": ["harness/**", "tests/**", "docs/**", "openspec/**"],
        "allowed_files": parts["writable"],
        "contract": {"names": parts["names"]},
        "acceptance": {
            "criteria": criteria,
            "check_commands": checks,
            "required_scenarios": [s["scenario"] for s in parts["scenarios"]],
        },
        "control": {
            "attempt_limit": attempt_limit,
            "timeout_seconds": timeout_seconds,
            "open_questions": [],
            "escalation_route": "человек-владелец change",
        },
    }
    return brief


def render(brief: dict[str, Any]) -> str:
    lines = [
        f"задача {brief['task_id']} · {brief['spec_version']}",
        f"цель: {brief['goal']}",
        f"файлов на запись: {len(brief['allowed_files'])}",
    ]
    for path in brief["allowed_files"]:
        lines.append(f"  + {path}")
    lines.append(f"критериев: {len(brief['acceptance']['criteria'])}")
    for criterion in brief["acceptance"]["criteria"]:
        lines.append(f"  [{criterion['id']}] {criterion['statement'][:110]}")
    lines.append("проверки: " + ", ".join(brief["acceptance"]["check_commands"]))
    return "\n".join(lines)


def write(brief: dict[str, Any], path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(brief, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path
