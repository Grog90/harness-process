"""Хуки: детерминированные предикаты на событиях инструментов.

Событие до инструмента — предикат перехода: deny запрещает действие, автомат
остаётся на месте и получает причину. Неопределённость закрывает переход
(fail-closed), а не превращается в молчаливое разрешение.

Хук — не песочница: он ловит привычные обходы (sed -i, redirect, tee), но не
заменяет изоляцию прав на уровне ОС. Это записано в остаточных рисках матрицы.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .policy import Decision, Policy, allow, deny, normalize_path

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


# --------------------------------------------------------------------- реестр

HOOKS: dict[str, Callable[[HookContext], Decision]] = {
    "policy.role_identity": policy_role_identity,
    "policy.tool_scope": policy_tool_scope,
    "policy.write_scope": policy_write_scope,
    "policy.worker_scope": policy_worker_scope,
    "policy.protected_paths": policy_protected_paths,
    "bash.write_bypass": bash_write_bypass,
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
