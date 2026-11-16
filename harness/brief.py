"""Бриф: единственный вход в диспетч.

Проверяются структура и противоречия — смысл остаётся за брифером и человеком.
Пустое обязательное поле и противоречивое требование — разные причины отказа
(у них разные коды и разные адресаты исправления).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

REQUIRED_TOP = (
    "task_id",
    "brief_version",
    "base_commit",
    "spec_version",
    "goal",
    "expected_behavior",
    "allowed_files",
    "acceptance",
    "control",
)

VALID_SEVERITIES = ("blocker", "major", "minor", "cosmetic")


@dataclass
class Issue:
    code: str
    field: str
    message: str
    kind: str = "error"  # error | warning

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field, "message": self.message, "kind": self.kind}


@dataclass
class Brief:
    data: dict[str, Any]
    issues: list[Issue] = field(default_factory=list)

    # ---------------------------------------------------------------- доступ
    @property
    def task_id(self) -> str:
        return str(self.data.get("task_id", ""))

    @property
    def allowed_files(self) -> list[str]:
        return [str(x) for x in self.data.get("allowed_files", []) or []]

    @property
    def allowed_read(self) -> list[str]:
        return [str(x) for x in self.data.get("allowed_read", []) or []]

    @property
    def criteria(self) -> list[dict[str, Any]]:
        acc = self.data.get("acceptance") or {}
        return list(acc.get("criteria", []) or [])

    @property
    def mandatory_criteria(self) -> list[dict[str, Any]]:
        return [c for c in self.criteria if c.get("mandatory", True)]

    @property
    def check_commands(self) -> list[str]:
        acc = self.data.get("acceptance") or {}
        return [str(x) for x in acc.get("check_commands", []) or []]

    @property
    def open_questions(self) -> list[dict[str, Any]]:
        ctl = self.data.get("control") or {}
        return list(ctl.get("open_questions", []) or [])

    @property
    def blocking_questions(self) -> list[dict[str, Any]]:
        return [q for q in self.open_questions if q.get("blocking", True) and not q.get("resolved")]

    @property
    def attempt_limit(self) -> int | None:
        ctl = self.data.get("control") or {}
        return ctl.get("attempt_limit")

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.kind == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.kind == "warning"]

    @property
    def valid(self) -> bool:
        return not self.errors

    def hash(self) -> str:
        payload = json.dumps(self.data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "brief_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def criterion_ids(self) -> list[str]:
        return [str(c.get("id", "")) for c in self.criteria]


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple)):
        return len(value) == 0
    return False


def validate(data: dict[str, Any], max_attempts: int = 3) -> Brief:
    """Структурная проверка брифа. Возвращает Brief с накопленными замечаниями."""
    issues: list[Issue] = []

    if not isinstance(data, dict):
        return Brief({}, [Issue("E_NOT_OBJECT", "$", "бриф должен быть JSON-объектом")])

    # 1. Обязательные поля: отсутствие и пустота различаются.
    for name in REQUIRED_TOP:
        if name not in data:
            issues.append(Issue("E_MISSING_FIELD", name, f"обязательное поле {name!r} отсутствует"))
        elif _blank(data[name]):
            issues.append(Issue("E_EMPTY_FIELD", name, f"обязательное поле {name!r} пустое"))

    ident = re.compile(r"^[A-Za-z0-9._:-]+$")
    if not _blank(data.get("task_id")) and not ident.match(str(data["task_id"])):
        issues.append(Issue("E_BAD_TASK_ID", "task_id", "task_id содержит недопустимые символы"))

    change_id = data.get("change_id")
    if not _blank(change_id) and not ident.match(str(change_id)):
        issues.append(Issue("E_BAD_CHANGE_ID", "change_id", "change_id содержит недопустимые символы"))

    actor_role = data.get("actor_role")
    if not _blank(actor_role) and str(actor_role) not in ("BA", "SA", "DEV", "QA", "verifier"):
        issues.append(
            Issue("E_BAD_ACTOR_ROLE", "actor_role", "actor_role: BA, SA, DEV, QA или verifier")
        )

    spec_ref = data.get("spec_ref")
    if not _blank(spec_ref) and not re.fullmatch(r"[0-9a-fA-F]{40}", str(spec_ref)):
        issues.append(
            Issue(
                "E_BAD_SPEC_REF",
                "spec_ref",
                "spec_ref должен быть полным Git SHA (40 hex), а не именем ветки или коротким SHA",
            )
        )

    services = data.get("services")
    if services is not None:
        if not isinstance(services, list):
            issues.append(Issue("E_BAD_SERVICES", "services", "services должен быть списком"))
        else:
            for index, service in enumerate(services):
                field = f"services[{index}]"
                if not isinstance(service, dict):
                    issues.append(Issue("E_BAD_SERVICES", field, "сервис должен быть объектом"))
                    continue
                name = str(service.get("name") or "")
                if not name or not ident.match(name):
                    issues.append(Issue("E_BAD_SERVICES", field, "у сервиса нет допустимого name"))
                path = str(service.get("path") or "")
                if not path.strip():
                    issues.append(Issue("E_BAD_SERVICES", field + ".path",
                                        "path сервиса не должен быть пустым"))
                base = str(service.get("base_commit") or "")
                if base and not re.fullmatch(r"[0-9a-fA-F]{40}", base):
                    issues.append(Issue("E_BAD_SERVICES", field + ".base_commit",
                                        "base_commit должен быть полным SHA"))

    # 2. Границы записи.
    service_map: dict[str, dict] = {}
    if isinstance(services, list):
        service_map = {str(s.get("name")): s for s in services if isinstance(s, dict)}
    allowed = data.get("allowed_files") or []
    if isinstance(allowed, list):
        if not allowed and not _blank(data.get("allowed_files")):
            issues.append(Issue("E_EMPTY_FIELD", "allowed_files", "allowed_files пуст: воркеру некуда писать"))
        for glob in allowed:
            if not isinstance(glob, str) or not glob.strip():
                issues.append(Issue("E_BAD_GLOB", "allowed_files", f"некорректный глоб: {glob!r}"))
            elif glob.startswith("service:"):
                rest = glob[len("service:"):]
                name = rest.split("/", 1)[0]
                inner = rest.split("/", 1)[1] if "/" in rest else ""
                service = service_map.get(name)
                if service is None:
                    issues.append(Issue("E_BAD_GLOB", "allowed_files",
                                        f"сервис {name!r} не объявлен в services"))
                elif not service.get("write", False):
                    issues.append(Issue("E_CONTRADICTION", "allowed_files",
                                        f"сервис {name!r} не открыт для записи (write=false)"))
                elif not inner or inner.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", inner) \
                        or ".." in inner.split("/"):
                    issues.append(Issue("E_CONTRADICTION", "allowed_files",
                                        f"недопустимый путь внутри сервиса: {glob!r}"))
            elif glob.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", glob) or ".." in glob:
                issues.append(
                    Issue("E_CONTRADICTION", "allowed_files", f"абсолютный путь или '..' в allowed_files: {glob!r}")
                )
    else:
        issues.append(Issue("E_TYPE", "allowed_files", "allowed_files должен быть списком глобов"))

    # 3. Критерии приёмки.
    acc = data.get("acceptance")
    criteria = []
    if isinstance(acc, dict):
        criteria = acc.get("criteria") or []
        if not isinstance(criteria, list) or not criteria:
            issues.append(Issue("E_EMPTY_FIELD", "acceptance.criteria", "нет ни одного критерия приёмки"))
        else:
            seen: set[str] = set()
            for idx, c in enumerate(criteria):
                where = f"acceptance.criteria[{idx}]"
                if not isinstance(c, dict):
                    issues.append(Issue("E_TYPE", where, "критерий должен быть объектом"))
                    continue
                cid = str(c.get("id", "")).strip()
                if not cid:
                    issues.append(Issue("E_EMPTY_FIELD", where + ".id", "у критерия нет идентификатора"))
                elif cid in seen:
                    issues.append(Issue("E_CONTRADICTION", where + ".id", f"дублирующийся критерий {cid!r}"))
                seen.add(cid)
                if _blank(c.get("statement")) and _blank(c.get("description")):
                    issues.append(Issue("E_EMPTY_FIELD", where + ".statement", "критерий без формулировки"))
                if c.get("mandatory", True) and _blank(c.get("check")) and not (acc.get("check_commands") or []):
                    issues.append(
                        Issue(
                            "E_UNVERIFIABLE",
                            where + ".check",
                            f"обязательный критерий {cid or idx} не имеет команды проверки",
                        )
                    )
        if _blank(acc.get("check_commands")):
            issues.append(
                Issue(
                    "W_NO_CHECK_COMMANDS",
                    "acceptance.check_commands",
                    "не заданы команды проверки: вердикт будет опираться только на текст",
                    kind="warning",
                )
            )
    elif acc is not None:
        issues.append(Issue("E_TYPE", "acceptance", "acceptance должен быть объектом"))

    # 4. Контракт: имена без семантики — типичная дыра.
    contract = data.get("contract") or {}
    if not isinstance(contract, dict) or _blank(contract.get("names")):
        issues.append(
            Issue(
                "W_NO_CONTRACT",
                "contract.names",
                "контракт имён не задан: тест-агент и код-агент разойдутся в деталях",
                kind="warning",
            )
        )
    elif _blank(contract.get("error_codes")) and _blank(contract.get("required_fields")):
        issues.append(
            Issue(
                "W_THIN_CONTRACT",
                "contract",
                "контракт содержит только имена: нет типов, полей, кодов ошибок, единиц",
                kind="warning",
            )
        )

    # 5. Управление и бюджет.
    ctl = data.get("control") or {}
    if isinstance(ctl, dict):
        limit = ctl.get("attempt_limit")
        if limit is not None:
            if not isinstance(limit, int) or limit < 1:
                issues.append(Issue("E_TYPE", "control.attempt_limit", "attempt_limit должен быть целым >= 1"))
            elif limit > max_attempts:
                issues.append(
                    Issue(
                        "E_CONTRADICTION",
                        "control.attempt_limit",
                        f"бриф просит {limit} попыток, политика разрешает {max_attempts}",
                    )
                )
        for idx, q in enumerate(ctl.get("open_questions") or []):
            if not isinstance(q, dict) or _blank(q.get("question")):
                issues.append(Issue("E_EMPTY_FIELD", f"control.open_questions[{idx}]", "вопрос без формулировки"))
    elif ctl is not None:
        issues.append(Issue("E_TYPE", "control", "control должен быть объектом"))

    # 6. Согласованность области работ.
    scope = data.get("scope") or {}
    if isinstance(scope, dict):
        inside = scope.get("in_scope") or []
        outside = scope.get("out_of_scope") or []
        for a in inside:
            for b in outside:
                if isinstance(a, str) and isinstance(b, str) and a == b:
                    issues.append(
                        Issue("E_CONTRADICTION", "scope", f"путь {a!r} одновременно входит и не входит в задачу")
                    )

    return Brief(data if isinstance(data, dict) else {}, issues)


def load(path: str, max_attempts: int = 3) -> Brief:
    with open(path, encoding="utf-8") as fh:
        return validate(json.load(fh), max_attempts=max_attempts)


def render_issues(issues: Iterable[Issue]) -> str:
    lines = []
    for i in issues:
        mark = "ОШИБКА" if i.kind == "error" else "предупр."
        lines.append(f"  [{mark}] {i.code} {i.field}: {i.message}")
    return "\n".join(lines)


# Точки контроля, которые реально существуют в коде (для отчёта о покрытии матрицы).
CONTROL_POINTS = {
    "brief.gate_dispatch": validate,
    "brief.gate_gap": lambda b: not b.blocking_questions,
}
