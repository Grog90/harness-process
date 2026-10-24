"""Хуки: детерминированные предикаты на событиях инструментов.

Событие до инструмента — предикат перехода: deny запрещает действие, автомат
остаётся на месте и получает причину. Неопределённость закрывает переход
(fail-closed), а не превращается в молчаливое разрешение.

Хук — не песочница: он ловит привычные обходы (sed -i, redirect, tee), но не
заменяет изоляцию прав на уровне ОС. Это записано в остаточных рисках матрицы.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .policy import Decision, Policy, advise, allow, deny, normalize_path

WRITE_TOOLS = ("write_file", "edit_file", "apply_patch", "create_file")


@dataclass
class HookContext:
    event: str = "pretooluse"
    tool: str = ""
    role: str = ""
    claimed_role: str | None = None
    task_id: str | None = None
    path: str | None = None
    command: str | None = None
    allowed_files: list[str] = field(default_factory=list)
    target: str | None = None
    policy: Policy | None = None
    root: str = "."
    extra: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------- правила


def policy_role_identity(ctx: HookContext) -> Decision:
    assert ctx.policy is not None
    return ctx.policy.check_role_identity(ctx.role, ctx.claimed_role)


def policy_tool_scope(ctx: HookContext) -> Decision:
    assert ctx.policy is not None
    if not ctx.tool:
        return allow("R-WORK-2")
    return ctx.policy.check_tool(ctx.role, ctx.tool)


def policy_write_scope(ctx: HookContext) -> Decision:
    assert ctx.policy is not None
    if ctx.tool not in WRITE_TOOLS or not ctx.path:
        return allow("R-PROT-1")
    return ctx.policy.check_write(ctx.role, ctx.path, allowed_files=ctx.allowed_files)


def policy_worker_scope(ctx: HookContext) -> Decision:
    assert ctx.policy is not None
    if ctx.role != "worker" or ctx.tool not in WRITE_TOOLS or not ctx.path:
        return allow("R-WORK-1")
    return ctx.policy.check_write(ctx.role, ctx.path, allowed_files=ctx.allowed_files)


def policy_protected_paths(ctx: HookContext) -> Decision:
    assert ctx.policy is not None
    if not ctx.path:
        return allow("R-PROT-1")
    rel = normalize_path(ctx.root, ctx.path)
    if rel == "<outside-root>":
        return deny("R-PROT-1", f"путь вне рабочего корня: {ctx.path!r}")
    if ctx.policy.is_protected(rel) and ctx.role != "system":
        allowed = ctx.policy.role(ctx.role).get("can_write", [])
        from .policy import matches_any

        if not matches_any(rel, allowed):
            return deny("R-PROT-1", f"{rel} — защищённый путь, запись ролью {ctx.role!r} запрещена")
    return allow("R-PROT-1")


_SLEEP_RE = re.compile(r"(?:^|[;&|]\s*|\s)sleep\s+\d", re.IGNORECASE)
_SLEEP_API_RE = re.compile(r"time\.sleep\s*\(|Start-Sleep|Wait-Event|-Wait\b", re.IGNORECASE)
_LONG_TIMEOUT_RE = re.compile(r"timeout\s+(\d{3,})", re.IGNORECASE)


def bash_no_sleep(ctx: HookContext) -> Decision:
    cmd = ctx.command or ""
    if not cmd:
        return allow("R-SLEEP-1")
    if _SLEEP_RE.search(cmd) or _SLEEP_API_RE.search(cmd):
        return deny(
            "R-SLEEP-1",
            "ожидание вне переходов запрещено: автомат событийный, а не таймерный",
            command=cmd[:200],
        )
    m = _LONG_TIMEOUT_RE.search(cmd)
    if m and int(m.group(1)) > 120:
        return deny("R-SLEEP-1", f"длинный таймаут {m.group(1)}с: ожидание должно быть отменяемым", command=cmd[:200])
    return allow("R-SLEEP-1")


_DESTRUCTIVE_GIT = (
    (re.compile(r"git\s+reset\s+--hard"), "git reset --hard"),
    (re.compile(r"git\s+clean\s+-[a-z]*f"), "git clean -f"),
    (re.compile(r"git\s+checkout\s+--\s+\."), "git checkout -- ."),
    (re.compile(r"git\s+restore\s+\."), "git restore ."),
    (re.compile(r"git\s+push\s+.*--force"), "git push --force"),
    (re.compile(r"git\s+branch\s+-D"), "git branch -D"),
)
_NAMED_TREE = re.compile(r"\bgit\s+(-C\s+\S+|--worktree[= ]\S+|--git-dir[= ]\S+)", re.IGNORECASE)


def bash_git_destructive(ctx: HookContext) -> Decision:
    cmd = ctx.command or ""
    for pattern, label in _DESTRUCTIVE_GIT:
        if pattern.search(cmd):
            if not _NAMED_TREE.search(cmd):
                return deny(
                    "R-GIT-1",
                    f"{label} без явно названного дерева: работа может быть потеряна",
                    command=cmd[:200],
                )
            return advise("R-GIT-1", f"{label}: дерево названо, но работа должна быть сохранена до revert")
    return allow("R-GIT-1")


_REDIRECT_RE = re.compile(r"(?:>>?|\btee\b(?:\s+-a)?)\s*([^\s;&|]+)")
_SED_INPLACE_RE = re.compile(r"sed\s+(-i\b|-i\.?\S*)\s+(?:-e\s+)?(?:'[^']*'|\"[^\"]*\"|\S+)\s+([^\s;&|]+)")
_CP_MV_RE = re.compile(r"\b(?:cp|mv|install|rsync)\b[^;|&]*\s+([^\s;&|]+)\s*$")


def bash_write_bypass(ctx: HookContext) -> Decision:
    """Эвристика: запись в продукт через shell, а не через инструмент записи."""
    assert ctx.policy is not None
    cmd = ctx.command or ""
    if not cmd or ctx.role == "system":
        return allow("R-ORCH-1")

    candidates: list[str] = []
    for m in _REDIRECT_RE.finditer(cmd):
        tgt = m.group(1).strip().strip("'\"")
        if tgt in ("/dev/null", "&1", "&2", "NUL", "nul"):
            continue
        candidates.append(tgt)
    m = _SED_INPLACE_RE.search(cmd)
    if m:
        candidates.append(m.group(2))
    for m in _CP_MV_RE.finditer(cmd):
        candidates.append(m.group(1))

    for cand in candidates:
        if cand.startswith("-") or "$" in cand:
            continue
        rel = normalize_path(ctx.root, cand)
        if rel == "<outside-root>":
            return deny("R-PROT-1", f"запись вне корня через shell: {cand!r}")
        if ctx.policy.is_product(rel):
            d = ctx.policy.check_write(ctx.role, rel, allowed_files=ctx.allowed_files)
            if not d.allowed:
                return deny(d.rule, f"обход через shell в {rel}: {d.reason}")
    return allow("R-ORCH-1")


def _read_counter_path(root: str) -> str:
    return os.path.join(root, ".harness", "read_counter.json")


def bash_read_loop(ctx: HookContext) -> Decision:
    """Третий одинаковый read без промежуточного изменения — отказ."""
    assert ctx.policy is not None
    max_same = int(ctx.policy.limit("max_same_read", 2))
    target = ctx.target or ctx.path or ""
    if ctx.tool not in ("read_file", "bash_readonly") or not target:
        return allow("R-READ-1")

    rel = normalize_path(ctx.root, target)
    full = os.path.join(ctx.root, rel)
    try:
        stamp = ""
        if os.path.exists(full):
            st = os.stat(full)
            stamp = f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        stamp = ""

    path = _read_counter_path(ctx.root)
    data: dict[str, Any] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        data = {}

    key = hashlib.sha256(f"{ctx.role}|{rel}|{stamp}".encode()).hexdigest()[:16]
    count = int(data.get(key, 0)) + 1
    data[key] = count
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass

    if count > max_same:
        return deny(
            "R-READ-1",
            f"{rel} прочитан {count} раз без изменений: цикл чтения, нужен другой шаг",
            path=rel,
            count=count,
        )
    if count == max_same:
        return advise("R-READ-1", f"{rel} прочитан {count} раза без изменений", path=rel)
    return allow("R-READ-1")


# --------------------------------------------------------------------- реестр

HOOKS: dict[str, Callable[[HookContext], Decision]] = {
    "policy.role_identity": policy_role_identity,
    "policy.tool_scope": policy_tool_scope,
    "policy.write_scope": policy_write_scope,
    "policy.worker_scope": policy_worker_scope,
    "policy.protected_paths": policy_protected_paths,
    "bash.no_sleep": bash_no_sleep,
    "bash.git_destructive": bash_git_destructive,
    "bash.write_bypass": bash_write_bypass,
    "bash.read_loop": bash_read_loop,
}

# Какие хуки запускаются на событие. Порядок не важен: deny доминирует.
EVENT_HOOKS: dict[str, list[str]] = {
    "pretooluse": [
        "policy.role_identity",
        "policy.tool_scope",
        "policy.protected_paths",
        "policy.write_scope",
        "policy.worker_scope",
        "bash.write_bypass",
        "bash.no_sleep",
        "bash.git_destructive",
        "bash.read_loop",
    ],
    "posttooluse": [],
    "precommit": [],
}


def run(event: str, ctx: HookContext) -> dict[str, Any]:
    """Запускает все хуки события. deny доминирует; ошибка хука = deny (fail-closed)."""
    names = EVENT_HOOKS.get(event, [])
    decisions: list[tuple[str, Decision]] = []
    for name in names:
        fn = HOOKS.get(name)
        if fn is None:
            # Заявленный, но отсутствующий хук — это дыра, а не разрешение.
            decisions.append((name, deny("R-HOOK-ERR", f"хук {name!r} объявлен, но не реализован")))
            continue
        try:
            decisions.append((name, fn(ctx)))
        except Exception as exc:  # noqa: BLE001 - fail-closed по построению
            decisions.append((name, deny("R-HOOK-ERR", f"ошибка хука {name!r}: {exc}")))

    denies = [d for _, d in decisions if d.decision == "deny"]
    advises = [d for _, d in decisions if d.decision == "advice" or d.decision == "advise"]
    if denies:
        first = denies[0]
        return {
            "decision": "deny",
            "rule": first.rule,
            "reason": first.reason,
            "details": first.details,
            "all": [d.to_dict() for _, d in decisions],
        }
    if advises:
        first = advises[0]
        return {
            "decision": "advise",
            "rule": first.rule,
            "reason": first.reason,
            "details": first.details,
            "all": [d.to_dict() for _, d in decisions],
        }
    return {"decision": "allow", "rule": "", "reason": "", "all": [d.to_dict() for _, d in decisions]}
