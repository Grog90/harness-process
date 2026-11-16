"""Роли и полномочия — детерминированный слой.

Решение о допустимости действия принимает этот модуль, а не текст промпта.
Инструкция модели — рекомендация; здесь — ограждение (см. docs/ANALYSIS.md).

Ключевые правила (см. policies/rules.json):
  R-ROLE-1  роль задаёт запускающий процесс, а не заявление агента;
  R-ORCH-1  оркестратор не пишет продуктовый код;
  R-VER-1   верификатор не меняет продукт;
  R-WORK-1  воркер пишет только в allowed_files брифа;
  R-WORK-2  воркер не делает git-мутаций и не диспетчит;
  R-PROT-1  политики, схемы, состояние и evidence защищены.
"""

from __future__ import annotations

import fnmatch
import json
import os
import posixpath
from dataclasses import dataclass, field
from typing import Any, Iterable

ROLE_ENV = "HARNESS_ROLE"
TASK_ENV = "HARNESS_TASK_ID"


class PolicyError(RuntimeError):
    """Ошибка конфигурации политики: это дефект установки, а не действия агента."""


@dataclass(frozen=True)
class Decision:
    """Вердикт хука. deny побеждает advise; неопределённость закрывает переход."""

    decision: str  # "allow" | "deny" | "advise"
    rule: str = ""
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision != "deny"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "decision": self.decision,
            "rule": self.rule,
            "reason": self.reason,
        }
        if self.details:
            out["details"] = self.details
        return out


def allow(rule: str = "", reason: str = "") -> Decision:
    return Decision("allow", rule, reason)


def deny(rule: str, reason: str, **details: Any) -> Decision:
    return Decision("deny", rule, reason, details)


def advise(rule: str, reason: str, **details: Any) -> Decision:
    return Decision("advise", rule, reason, details)


def normalize_path(root: str, path: str) -> str:
    """Приводит путь к виду 'относительно корня, через слэши'.

    Абсолютные пути вне корня и выход через '..' не «чинятся», а остаются
    заметными: такой путь не совпадёт ни с одним разрешённым глобом.
    """
    if not path:
        raise PolicyError("пустой путь")
    p = path.replace("\\", "/")
    if os.path.isabs(p) or (len(p) > 1 and p[1] == ":"):
        try:
            p = posixpath.relpath(os.path.abspath(path).replace("\\", "/"), root.replace("\\", "/"))
        except ValueError:
            return "<outside-root>"
    p = posixpath.normpath(p)
    if p.startswith("./"):
        p = p[2:]
    if p.startswith(".."):
        return "<outside-root>"
    return p


def service_base(root: str, service: dict[str, Any]) -> str:
    raw = str(service.get("path") or "")
    return os.path.abspath(raw if os.path.isabs(raw) else os.path.join(root, raw))


def resolve_target(root: str, path: str, services: Iterable[dict[str, Any]] | None = None) -> str:
    """Путь → ключ политики: обычный rel или `service:<name>/<rel>`.

    Абсолютный путь вне главного корня не «прощается», а ищется под объявленными
    сервисными корнями. Если сервис не объявлен — путь остаётся outside-root.
    """
    rel = normalize_path(root, path)
    if rel.startswith("service:"):
        return rel
    root_real = os.path.realpath(root)
    target = os.path.realpath(
        os.path.abspath(path) if os.path.isabs(path) else os.path.join(root, path)
    )
    for service in services or []:
        name = str(service.get("name") or "")
        if not name:
            continue
        base = os.path.realpath(service_base(root, service))
        # корень-сервис, совпадающий с проектом, не должен переименовывать все пути:
        # иначе R-PROT-1 проверялся бы только через service-ключ
        if base == root_real:
            continue
        if target == base or target.startswith(base + os.sep):
            inner = os.path.relpath(target, base).replace("\\", "/")
            return f"service:{name}/{inner}" if inner != "." else f"service:{name}/"
    if rel != "<outside-root>":
        return rel
    return "<outside-root>"


def matches_any(path: str, globs: Iterable[str]) -> bool:
    """Сопоставление с глобами. '*' захватывает '/', поэтому 'src/**' покрывает поддерево.

    Регистр не важен: политика не должна зависеть от файловой системы хоста.
    """
    low = path.lower()
    for g in globs:
        if fnmatch.fnmatch(low, g.lower()):
            return True
        if g.endswith("/") and fnmatch.fnmatch(low, g.lower() + "*"):
            return True
    return False


class Policy:
    """Загруженная политика полномочий."""

    def __init__(self, data: dict[str, Any], root: str):
        self.data = data
        self.root = os.path.abspath(root)
        self.version = str(data.get("version", "0"))
        self.product_globs = list(data.get("product_globs", []))
        self.protected_globs = list(data.get("protected_globs", []))
        self.spec_globs = list(data.get("spec_globs", []))
        self.privileged_tools = set(data.get("privileged_tools", []))
        self.privileged_prefixes = list(data.get("privileged_prefixes", []))
        self.mcp_allow_globs = list(data.get("mcp_allow_globs", []))
        self.roles: dict[str, dict[str, Any]] = dict(data.get("roles", {}))
        self.limits: dict[str, Any] = dict(data.get("limits", {}))
        if not self.roles:
            raise PolicyError("в политике нет ни одной роли")

    # ------------------------------------------------------------------ загрузка
    @classmethod
    def load(cls, root: str = ".", path: str | None = None) -> "Policy":
        p = path or os.path.join(root, "policies", "policy.json")
        with open(p, encoding="utf-8") as fh:
            return cls(json.load(fh), root)

    # ------------------------------------------------------------------ запросы
    def role(self, name: str) -> dict[str, Any]:
        try:
            return self.roles[name]
        except KeyError as exc:
            raise PolicyError(f"неизвестная роль: {name!r}") from exc

    def is_product(self, rel: str) -> bool:
        return matches_any(rel, self.product_globs)

    def is_protected(self, rel: str) -> bool:
        return matches_any(rel, self.protected_globs)

    def is_spec(self, rel: str) -> bool:
        return matches_any(rel, self.spec_globs)

    def limit(self, name: str, default: Any = None) -> Any:
        return self.limits.get(name, default)

    # ------------------------------------------------------------------ проверки
    def check_role_identity(self, assigned_role: str, claimed_role: str | None) -> Decision:
        """R-ROLE-1. Роль выдаёт запускающий процесс; агент не может её повысить."""
        if claimed_role and claimed_role != assigned_role:
            return deny(
                "R-ROLE-1",
                f"заявленная роль {claimed_role!r} не совпадает с выданной {assigned_role!r}",
                assigned_role=assigned_role,
                claimed_role=claimed_role,
            )
        return allow("R-ROLE-1")

    def check_tool(self, role_name: str, tool: str) -> Decision:
        """R-WORK-2 / R-TOOL-2: набор инструментов роли закрыт, привилегированные — всем рабочим."""
        role = self.role(role_name)
        if role_name not in ("human", "system"):
            if tool in self.privileged_tools or any(
                tool.startswith(prefix) for prefix in self.privileged_prefixes
            ):
                return deny(
                    "R-TOOL-2",
                    f"инструмент {tool!r} привилегированный: рабочие роли его не получают",
                    role=role_name,
                    tool=tool,
                )
        tools = list(role.get("tools", []))
        if tool.startswith("mcp__") and matches_any(tool, self.mcp_allow_globs):
            return allow("R-WORK-2")
        if "*" in tools or tool in tools:
            return allow("R-WORK-2")
        if any(t.endswith("*") and tool.startswith(t[:-1]) for t in tools):
            return allow("R-WORK-2")
        return deny(
            "R-WORK-2",
            f"инструмент {tool!r} не разрешён роли {role_name!r}",
            role=role_name,
            tool=tool,
            allowed=tools,
        )

    def check_write(
        self,
        role_name: str,
        path: str,
        allowed_files: Iterable[str] | None = None,
        services: Iterable[dict[str, Any]] | None = None,
    ) -> Decision:
        """Полная проверка записи: защищённые пути, продуктовый код, allowed_files."""
        rel = resolve_target(self.root, path, services)
        if rel == "<outside-root>":
            return deny("R-PROT-1", f"путь вне рабочего корня: {path!r}", path=path)

        role = self.role(role_name)

        if rel.startswith("service:"):
            name = rel.split("/", 1)[0][len("service:"):]
            inner = rel.split("/", 1)[1] if "/" in rel else ""
            service = next((s for s in services or [] if str(s.get("name")) == name), None)
            if service is None:
                return deny("R-PROT-1", f"сервис {name!r} не объявлен в change", path=rel)
            if not role.get("can_write_services", False):
                rule = "R-VER-1" if role_name == "verifier" else "R-PROT-1"
                return deny(rule,
                            f"{rel} — код сервиса; роль {role_name!r} не пишет в сервисы",
                            path=rel, role=role_name)
            if not service.get("write", False):
                return deny("R-PROT-1",
                            f"сервис {name!r} не открыт для записи (read-only)",
                            path=rel, service=name)
            if not inner or inner.startswith(".."):
                return deny("R-PROT-1", f"недопустимый путь внутри сервиса: {rel!r}", path=rel)
            scope = list(allowed_files or [])
            if not scope:
                return deny("R-WORK-1", "воркер запущен без allowed_files", path=rel)
            if not matches_any(rel, scope):
                return deny("R-WORK-1", f"{rel} вне allowed_files брифа", path=rel,
                            allowed_files=scope)

            # Сервис может лежать внутри главного корня (monorepo). Тогда его файлы
            # не получают «вторую жизнь»: те же protected/spec/product-правила
            # проверяются по главному относительному пути, а не только по ключу.
            base = os.path.realpath(service_base(self.root, service))
            root_real = os.path.realpath(self.root)
            target = os.path.realpath(os.path.join(base, inner))
            # Проверяем главные правила для любого target внутри проекта, даже если
            # сервисный корень — предок проекта (path='..'): иначе service-ключ
            # становится вторым именем для policies/** и обходит R-PROT-1.
            if target == root_real or target.startswith(root_real + os.sep):
                main_rel = os.path.relpath(target, root_real).replace("\\", "/")
                if main_rel not in ("", ".") and not main_rel.startswith(".."):
                    if (self.is_spec(main_rel) and role_name not in ("human", "system")
                            and not role.get("can_write_specs", False)):
                        return deny("R-WORK-3", f"{main_rel} — каталог спеки", path=rel)
                    if (self.is_protected(main_rel) and role_name != "system"
                            and not matches_any(main_rel, role.get("can_write", []))):
                        return deny("R-PROT-1", f"{main_rel} — защищённый путь", path=rel)
                    if (self.is_product(main_rel) and not role.get("can_write_product", False)
                            and not matches_any(main_rel, role.get("product_write_globs", []))):
                        return deny("R-PROT-1", f"{main_rel} — продуктовый код", path=rel)
            return allow("R-WORK-1")

        # R-WORK-3: спеку плана пишут BA/SA (analyst) и QA-кейсы, а не рабочий код.
        if role_name not in ("human", "system") and self.is_spec(rel) and not role.get("can_write_specs", False):
            return deny(
                "R-WORK-3",
                f"{rel} — каталог спеки; роль {role_name!r} не пишет план",
                path=rel,
                role=role_name,
            )

        # R-PROT-1: политики, схемы, состояние, evidence.
        if self.is_protected(rel) and role_name != "system":
            own = list(role.get("can_write", []))
            if not matches_any(rel, own):
                return deny(
                    "R-PROT-1",
                    f"{rel} — защищённый путь, запись ролью {role_name!r} запрещена",
                    path=rel,
                )

        # R-ORCH-1 / R-VER-1 / R-PROT-1: продуктовый код.
        if self.is_product(rel) and not role.get("can_write_product", False):
            if matches_any(rel, role.get("product_write_globs", [])):
                pass
            else:
                rule = "R-ORCH-1" if role_name == "orchestrator" else (
                    "R-PROT-1" if role_name in ("analyst", "qa") else "R-VER-1"
                )
                return deny(
                    rule,
                    f"{rel} — продуктовый код; роль {role_name!r} не имеет права записи",
                    path=rel,
                    role=role_name,
                )

        # R-WORK-1: воркер ограничен allowed_files брифа.
        if role.get("write_scope_from_brief"):
            scope = list(allowed_files or [])
            if not scope:
                return deny(
                    "R-WORK-1",
                    "воркер запущен без allowed_files: писать некуда — это дефект брифа",
                    path=rel,
                )
            if not matches_any(rel, scope):
                return deny(
                    "R-WORK-1",
                    f"{rel} вне allowed_files брифа",
                    path=rel,
                    allowed_files=scope,
                )
            write_globs = list(role.get("can_write", []))
            if write_globs and not matches_any(rel, write_globs):
                return deny(
                    "R-PROT-1",
                    f"{rel} не входит в разрешённую область записи роли {role_name!r}",
                    path=rel,
                    allowed=write_globs,
                )
            if self.is_protected(rel):
                return deny("R-PROT-1", f"{rel} — защищённый путь", path=rel)
            return allow("R-WORK-1")

        # Общий случай: путь должен попадать в can_write роли.
        write_globs = list(role.get("can_write", []))
        if not matches_any(rel, write_globs):
            return deny(
                "R-PROT-1",
                f"{rel} не входит в разрешённую область записи роли {role_name!r}",
                path=rel,
                allowed=write_globs,
            )
        return allow("R-PROT-1")


def role_from_env(default: str | None = None) -> str | None:
    """Роль приходит из окружения запускающего процесса (R-ROLE-1)."""
    return os.environ.get(ROLE_ENV) or default
