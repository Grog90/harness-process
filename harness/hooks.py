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

from .policy import Decision, Policy, advise, allow, deny, normalize_path, resolve_target

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
    services: list[dict[str, Any]] = field(default_factory=list)
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
    return ctx.policy.check_write(ctx.role, ctx.path, allowed_files=ctx.allowed_files,
                                  services=ctx.services)


def policy_worker_scope(ctx: HookContext) -> Decision:
    assert ctx.policy is not None
    if ctx.role != "worker" or ctx.tool not in WRITE_TOOLS or not ctx.path:
        return allow("R-WORK-1")
    return ctx.policy.check_write(ctx.role, ctx.path, allowed_files=ctx.allowed_files,
                                  services=ctx.services)


READ_TOOLS = ("read_file", "glob", "grep")


def policy_read_scope(ctx: HookContext) -> Decision:
    assert ctx.policy is not None
    if ctx.tool not in READ_TOOLS or not ctx.path:
        return allow("R-READ-1")
    return ctx.policy.check_read(ctx.role, ctx.path, services=ctx.services)


def policy_protected_paths(ctx: HookContext) -> Decision:
    assert ctx.policy is not None
    if not ctx.path:
        return allow("R-PROT-1")
    # защищённые пути ограничивают ЗАПИСЬ: читать политику должна мочь любая роль,
    # иначе агент не увидит правил, по которым его проверяют
    if ctx.tool not in WRITE_TOOLS:
        return allow("R-PROT-1")
    rel = resolve_target(ctx.root, ctx.path, ctx.services)
    if rel == "<outside-root>":
        return deny("R-PROT-1", f"путь вне рабочего корня: {ctx.path!r}")
    if rel.startswith("service:"):
        # права на сервис проверяет policy.check_write; здесь нет главных protected-глобов
        return allow("R-PROT-1")
    if ctx.policy.is_protected(rel) and ctx.role != "system":
        allowed = ctx.policy.role(ctx.role).get("can_write", [])
        from .policy import matches_any

        if not matches_any(rel, allowed):
            return deny("R-PROT-1", f"{rel} — защищённый путь, запись ролью {ctx.role!r} запрещена")
    return allow("R-PROT-1")


_REDIRECT_RE = re.compile(r"(?:>>?|\btee\b(?:\s+-a)?)\s*([^\s;&|]+)")
_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"", re.S)
_SED_INPLACE_RE = re.compile(r"sed\s+(-i\b|-i\.?\S*)\s+(?:-e\s+)?(?:'[^']*'|\"[^\"]*\"|\S+)\s+([^\s;&|]+)")
_CP_MV_RE = re.compile(r"\b(?:cp|mv|install|rsync)\b[^;|&]*\s+([^\s;&|]+)\s*$")

# Разрушительный git: то, что нельзя откатить, и что видно по тексту команды.
# Между `git` и подкомандой могут стоять опции (`-C dir`, `--worktree`), поэтому
# шаблон ищет подкоманду в любом месте строки, а не сразу после `git`.
_DESTRUCTIVE_GIT = (
    (re.compile(r"\bgit\b[^\n]*?\bpush\b[^\n]*?(?:--force\b|\s-f\b)"), "git push --force"),
    (re.compile(r"\bgit\b[^\n]*?\breset\s+--hard\b"), "git reset --hard"),
    (re.compile(r"\bgit\b[^\n]*?\bclean\s+-[a-z]*f"), "git clean -f"),
    (re.compile(r"\bgit\b[^\n]*?\bcheckout\s+--\s+\."), "git checkout -- ."),
    (re.compile(r"\bgit\b[^\n]*?\brestore\s+\."), "git restore ."),
    (re.compile(r"\bgit\b[^\n]*?\bbranch\s+-D"), "git branch -D"),
)
# Дерево названо явно: команда целится в конкретный worktree/каталог, а не в текущий.
_NAMED_TREE = re.compile(r"\bgit\s+(-C\s+\S+|--worktree[= ]\S+|--git-dir[= ]\S+)", re.IGNORECASE)


def bash_git_destructive(ctx: HookContext) -> Decision:
    """Необратимый git без явно названного дерева — отказ; с деревом — предупреждение."""
    cmd = ctx.command or ""
    if not cmd:
        return allow("R-GIT-1")
    named = bool(_NAMED_TREE.search(cmd))
    for pattern, label in _DESTRUCTIVE_GIT:
        if pattern.search(cmd):
            if not named:
                return deny(
                    "R-GIT-1",
                    f"{label} без явно названного дерева: работа может быть потеряна",
                    command=cmd[:200],
                )
            return advise("R-GIT-1", f"{label}: дерево названо, но работу надо сохранить до отката")
    return allow("R-GIT-1")


def bash_write_bypass(ctx: HookContext) -> Decision:
    """Эвристика: запись в продукт через shell, а не через инструмент записи."""
    assert ctx.policy is not None
    cmd = ctx.command or ""
    if not cmd or ctx.role == "system":
        return allow("R-ORCH-1")

    # `>` внутри кавычек — это текст, а не перенаправление: `python -c "a >> b"`
    # не пишет в файл `b`. Поэтому разбор идёт по строке без содержимого кавычек.
    scan = _QUOTED_RE.sub("''", cmd)

    candidates: list[str] = []
    for m in _REDIRECT_RE.finditer(scan):
        tgt = m.group(1).strip().strip("'\"")
        if tgt in ("/dev/null", "&1", "&2", "NUL", "nul"):
            continue
        candidates.append(tgt)
    m = _SED_INPLACE_RE.search(scan)
    if m:
        candidates.append(m.group(2))
    for m in _CP_MV_RE.finditer(scan):
        candidates.append(m.group(1))

    for cand in candidates:
        if cand.startswith("-") or "$" in cand:
            continue
        rel = resolve_target(ctx.root, cand, ctx.services)
        if rel == "<outside-root>":
            return deny("R-PROT-1", f"запись вне корня через shell: {cand!r}")
        if rel.startswith("service:"):
            return deny("R-PROT-1", f"shell-запись в сервис не открыта: {cand!r}")
        if rel == ".harness" or rel.startswith(".harness/"):
            return deny("R-PROT-1", f"shell-запись в control-plane .harness запрещена: {cand!r}")
        if ctx.policy.is_protected(rel):
            return deny("R-PROT-1", f"shell-запись в защищённый путь запрещена: {cand!r}")
        if ctx.policy.is_product(rel):
            d = ctx.policy.check_write(ctx.role, rel, allowed_files=ctx.allowed_files,
                                       services=ctx.services)
            if not d.allowed:
                return deny(d.rule, f"обход через shell в {rel}: {d.reason}")
    return allow("R-ORCH-1")


# --------------------------------------------------------------------- реестр

HOOKS: dict[str, Callable[[HookContext], Decision]] = {
    "policy.role_identity": policy_role_identity,
    "policy.tool_scope": policy_tool_scope,
    "policy.write_scope": policy_write_scope,
    "policy.read_scope": policy_read_scope,
    "policy.worker_scope": policy_worker_scope,
    "policy.protected_paths": policy_protected_paths,
    "bash.write_bypass": bash_write_bypass,
    "bash.git_destructive": bash_git_destructive,
}

# Какие хуки запускаются на событие. Порядок не важен: deny доминирует.
EVENT_HOOKS: dict[str, list[str]] = {
    "pretooluse": [
        "policy.role_identity",
        "policy.tool_scope",
        "policy.read_scope",
        "policy.protected_paths",
        "policy.write_scope",
        "policy.worker_scope",
        "bash.write_bypass",
        "bash.git_destructive",
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
