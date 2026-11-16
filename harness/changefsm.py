"""Уровень изменения (change) над уровнем задачи.

Задача отвечает на вопрос «сделай этот кусок», изменение — «что и зачем делаем,
какие правила согласованы и кто это подтвердил». Без этого уровня бриф ссылается
на спеку, но никто не проверяет, что план вообще был согласован и что задачи
покрывают требования.

Здесь живут:

* автомат изменения (DRAFT → … → READY → … → ARCHIVED) с таблицей переходов;
* review-записи как объект: артефакт, его хеш, редакция, автор, проверяющий,
  критерии и вердикт; «LGTM» без критериев не принимается;
* trace-матрица REQ → CASE → task: разрыв блокирует Ready;
* `spec_ref` — полный Git SHA плана, а не имя ветки;
* гейт диспетча: задача по change запускается только из READY/IN_PROGRESS.

Состояние живёт в SQLite, review-записи дополнительно пишутся в каталог change:
их видно в Git и можно проверить без базы.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any

from . import specbrief
from .state import Store, new_id, now

RISK_CLASSES = ("L0", "L1", "L2", "L3")
REVIEW_ROLES = ("product_owner", "BA", "SA", "DEV", "QA", "verifier", "human")
REVIEW_VERDICTS = ("approve", "request_changes", "abstain")

STATES = (
    "DRAFT", "EXPLORED", "PROPOSED", "SPECS_AGREED", "DESIGN_AGREED", "READY",
    "IN_PROGRESS", "IMPLEMENTED", "VERIFIED", "RELEASED", "ACCEPTED",
    "ARCHIVED", "BLOCKED", "CANCELLED", "SUPERSEDED",
)
TERMINAL = {"ARCHIVED", "CANCELLED", "SUPERSEDED"}

TRANSITIONS: dict[str, dict[str, str]] = {
    "DRAFT": {"explore": "EXPLORED", "cancel": "CANCELLED", "block": "BLOCKED", "supersede": "SUPERSEDED"},
    "EXPLORED": {"propose": "PROPOSED", "cancel": "CANCELLED", "block": "BLOCKED", "supersede": "SUPERSEDED"},
    "PROPOSED": {"agree_specs": "SPECS_AGREED", "cancel": "CANCELLED", "block": "BLOCKED",
                 "supersede": "SUPERSEDED"},
    "SPECS_AGREED": {"agree_design": "DESIGN_AGREED", "revise": "SPECS_AGREED", "cancel": "CANCELLED",
                     "block": "BLOCKED", "supersede": "SUPERSEDED"},
    "DESIGN_AGREED": {"ready": "READY", "revise": "SPECS_AGREED", "cancel": "CANCELLED",
                      "block": "BLOCKED", "supersede": "SUPERSEDED"},
    "READY": {"start": "IN_PROGRESS", "revise": "SPECS_AGREED", "cancel": "CANCELLED", "block": "BLOCKED",
              "supersede": "SUPERSEDED"},
    "IN_PROGRESS": {"tasks_done": "IMPLEMENTED", "revise": "SPECS_AGREED", "block": "BLOCKED",
                    "cancel": "CANCELLED", "supersede": "SUPERSEDED"},
    "IMPLEMENTED": {"verify": "VERIFIED", "revise": "SPECS_AGREED", "block": "BLOCKED",
                    "cancel": "CANCELLED"},
    "VERIFIED": {"release": "RELEASED", "accept": "ACCEPTED", "revise": "SPECS_AGREED", "block": "BLOCKED"},
    "RELEASED": {"accept": "ACCEPTED", "revise": "SPECS_AGREED", "block": "BLOCKED"},
    "ACCEPTED": {"archive": "ARCHIVED", "revise": "SPECS_AGREED"},
    "BLOCKED": {"unblock": "__PREVIOUS__", "cancel": "CANCELLED", "supersede": "SUPERSEDED"},
    "ARCHIVED": {},
    "CANCELLED": {},
    "SUPERSEDED": {},
}

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_TASK = re.compile(r"^[-*]\s+\[[ xX]\]\s+(\d+(?:\.\d+)*)\s+(.*)$")
_CHECK = re.compile(r"`[^`]*(?:python|pytest|unittest|npm|node|go test|dotnet test)[^`]*`", re.IGNORECASE)
_PATH = re.compile(r"`([^`\n]+)`")


class ChangeError(RuntimeError):
    """Отказ уровня изменения: правило и причина, а не «не получилось»."""

    def __init__(self, rule: str, reason: str, **details: Any):
        super().__init__(f"{rule}: {reason}")
        self.rule = rule
        self.reason = reason
        self.details = details


def _iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts if ts is not None else now()))


def change_dir(root: str, change_id: str) -> str:
    return os.path.join(os.path.abspath(root), "openspec", "changes", change_id)


def live_change(root: str, change_id: str) -> str | None:
    path = change_dir(root, change_id)
    return path if os.path.isdir(path) else None


def _hash_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def artifact_hash(root: str, change_id: str, artifact: str) -> str:
    """Хеш артефакта: файла или каталога (`specs`, `contracts`).

    Каталог хешируется по отсортированному списку путей и содержимому, поэтому
    правка любого файла спеки меняет хеш и инвалидирует review.
    """
    base = change_dir(root, change_id)
    if not os.path.isdir(base):
        raise ChangeError("R-REV-1", f"каталог change не найден: {change_id}")
    rel = artifact.strip().replace("\\", "/").strip("/")
    if not rel or rel.startswith("..") or "/../" in rel:
        raise ChangeError("R-REV-1", f"некорректный артефакт: {artifact!r}")
    if rel in ("specs", "contracts"):
        directory = os.path.join(base, rel)
        if not os.path.isdir(directory):
            raise ChangeError("R-REV-1", f"артефакт {rel!r} отсутствует в change")
        h = hashlib.sha256()
        files: list[str] = []
        for dirpath, _dirnames, filenames in os.walk(directory):
            for name in filenames:
                files.append(os.path.join(dirpath, name))
        for path in sorted(files):
            h.update(os.path.relpath(path, directory).replace("\\", "/").encode("utf-8"))
            with open(path, "rb") as fh:
                h.update(fh.read())
        return "sha256:" + h.hexdigest()
    target = os.path.join(base, rel)
    if not os.path.isfile(target):
        raise ChangeError("R-REV-1", f"артефакт {rel!r} отсутствует в change")
    with open(target, "rb") as fh:
        return _hash_bytes(fh.read())


def required_artifacts(risk_class: str, change_path: str) -> dict[str, list[str]]:
    """Минимальный набор review-записей: артефакт → обязательные роли."""
    required: dict[str, list[str]] = {
        "proposal.md": ["BA"],
        "specs": ["BA", "QA"],
        "tasks.md": ["SA", "DEV", "QA"],
    }
    if risk_class in ("L1", "L2", "L3"):
        required["design.md"] = ["SA", "DEV"]
    elif os.path.isfile(os.path.join(change_path, "design.md")):
        required["design.md"] = ["SA"]
    contracts = os.path.join(change_path, "contracts")
    if os.path.isdir(contracts) and os.listdir(contracts):
        required["contracts"] = ["SA"]
    return required


def create(store: Store, root: str, change_id: str, *, owner: str, risk_class: str = "L1",
           title: str = "", base_commit: str | None = None) -> dict[str, Any]:
    if not _ID.match(change_id):
        raise ChangeError("R-CHG-1", "change_id: строчные латинские буквы, цифры и дефис, 3–64 символа")
    if risk_class not in RISK_CLASSES:
        raise ChangeError("R-CHG-1", f"неизвестный класс риска {risk_class!r}: ожидается {', '.join(RISK_CLASSES)}")
    if not (owner or "").strip():
        raise ChangeError("R-CHG-1", "у изменения должен быть владелец")
    if store.find_change(change_id):
        raise ChangeError("R-CHG-1", f"change {change_id!r} уже зарегистрирован в состоянии")
    change = store.create_change(change_id, owner=owner, risk_class=risk_class, title=title,
                                 base_commit=base_commit)
    store.append_change_event(new_id("cevt"), change_id, "create",
                              {"owner": owner, "risk_class": risk_class, "title": title},
                              {"ok": True, "state": "DRAFT"})
    return change


def get(store: Store, change_id: str) -> dict[str, Any]:
    return store.get_change(change_id)


def _check_spec_ref_format(spec_ref: str | None) -> str | None:
    if not spec_ref or not str(spec_ref).strip():
        return "spec_ref не задан: план не привязан к полному Git SHA"
    if not _SHA.match(str(spec_ref).strip()):
        return "spec_ref должен быть полным Git SHA (40 hex), а не веткой или коротким SHA"
    return None


def check_spec_ref(change: dict[str, Any] | None, brief: dict[str, Any]) -> dict[str, Any]:
    """R-REF-1: бриф, привязанный к change, обязан нести полный SHA плана."""
    if not change:
        return {"ok": True, "skipped": "бриф не привязан к зарегистрированному change"}
    problem = _check_spec_ref_format(brief.get("spec_ref"))
    if problem:
        return {"ok": False, "rule": "R-REF-1", "reason": problem}
    expected = change.get("spec_ref")
    if expected and str(brief.get("spec_ref")) != str(expected):
        return {
            "ok": False,
            "rule": "R-REF-1",
            "reason": f"бриф собран по spec_ref {brief.get('spec_ref')}, а план — {expected}",
            "brief_spec_ref": brief.get("spec_ref"),
            "change_spec_ref": expected,
        }
    return {"ok": True, "spec_ref": str(brief.get("spec_ref"))}


def gate_dispatch(store: Store, root: str, brief: dict[str, Any]) -> dict[str, Any]:
    """R-CHG-1: задача по change запускается только из READY/IN_PROGRESS.

    Первый диспетч переводит change READY → IN_PROGRESS: «план принят, работа
    началась» — отдельное событие, а не догадка по числу задач.
    """
    change_id = str(brief.get("change_id") or "").strip()
    if not change_id:
        spec_version = str(brief.get("spec_version") or "")
        match = re.match(r"^openspec:([A-Za-z0-9._-]+)@", spec_version)
        change_id = match.group(1) if match else ""
    if not change_id:
        return {"ok": True, "skipped": "бриф не привязан к change"}

    change = store.find_change(change_id)
    if change is None:
        return {
            "ok": False,
            "rule": "R-CHG-1",
            "reason": f"бриф ссылается на change {change_id!r}, который не зарегистрирован: "
                      f"сначала `harness change new`",
        }
    if change["status"] not in ("READY", "IN_PROGRESS"):
        return {
            "ok": False,
            "rule": "R-CHG-1",
            "reason": f"change {change_id} в состоянии {change['status']}: диспетч разрешён только из "
                      f"READY/IN_PROGRESS (сначала пройдите Ready-гейт)",
            "change_status": change["status"],
        }
    check = check_spec_ref(change, brief)
    if not check["ok"]:
        return check
    started = False
    if change["status"] == "READY":
        _apply_transition(store, change_id, "start", {"by": "dispatch"})
        started = True
    return {"ok": True, "change": change_id, "started": started}


# ------------------------------------------------------------------ review-записи


def review(store: Store, root: str, change_id: str, *, artifact: str, reviewer_id: str,
           reviewer_role: str, author_id: str | None = None, verdict: str = "approve",
           criteria: list[str] | None = None, findings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    change = store.get_change(change_id)
    if change["status"] in TERMINAL:
        raise ChangeError("R-REV-1", f"change {change_id} уже в терминальном состоянии {change['status']}")
    if reviewer_role not in REVIEW_ROLES:
        raise ChangeError("R-REV-1", f"неизвестная роль проверяющего {reviewer_role!r}")
    if verdict not in REVIEW_VERDICTS:
        raise ChangeError("R-REV-1", f"неизвестный вердикт {verdict!r}")
    if not (reviewer_id or "").strip():
        raise ChangeError("R-REV-1", "у review-записи должен быть проверяющий")
    if author_id and author_id == reviewer_id:
        raise ChangeError("R-REV-2", "автор артефакта не может быть его проверяющим")
    if verdict == "approve" and not criteria:
        raise ChangeError("R-REV-1", "approve без критериев проверки — это не review, а подпись")

    rel = artifact.strip().replace("\\", "/").strip("/")
    if rel.startswith("specs/"):
        rel = "specs"
    digest = artifact_hash(root, change_id, rel)
    rid = new_id("rev")
    record = {
        "review_id": rid,
        "change_id": change_id,
        "revision": int(change["revision"]),
        "artifact": rel,
        "artifact_hash": digest,
        "reviewer_id": reviewer_id,
        "reviewer_role": reviewer_role,
        "author_id": author_id,
        "verdict": verdict,
        "criteria": list(criteria or []),
        "findings": list(findings or []),
        "created_at": now(),
    }
    store.add_change_review(record)
    reviews_dir = os.path.join(change_dir(root, change_id), "reviews")
    os.makedirs(reviews_dir, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", rel)
    path = os.path.join(reviews_dir, f"r{change['revision']}-{safe}-{reviewer_role}-{rid}.json")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({**record, "created_at_iso": _iso(record["created_at"])}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    store.append_change_event(new_id("cevt"), change_id, "review",
                              {"artifact": rel, "reviewer_role": reviewer_role, "verdict": verdict},
                              {"ok": True, "path": os.path.relpath(path, root)})
    return {**record, "path": path}


# ------------------------------------------------------------------ трассировка


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _spec_texts(root: str, change_id: str) -> list[tuple[str, str]]:
    base = os.path.join(change_dir(root, change_id), "specs")
    out: list[tuple[str, str]] = []
    if os.path.isdir(base):
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in sorted(filenames):
                if name == "spec.md":
                    path = os.path.join(dirpath, name)
                    out.append((os.path.relpath(path, root).replace("\\", "/"), _read(path)))
    return out


def _tasks(text: str) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = _TASK.match(line.rstrip())
        if not match:
            continue
        body = match.group(2)
        paths = [p.strip().replace("\\", "/") for p in _PATH.findall(body)
                 if "/" in p and " " not in p.strip()]
        has_check = bool(_CHECK.search(body)) or "проверк" in body.lower() or "check" in body.lower()
        tasks.append({"id": match.group(1), "text": body.strip(), "paths": paths, "has_check": has_check})
    return tasks


def check_trace(store: Store, root: str, change_id: str) -> dict[str, Any]:
    """R-TRACE-2: каждое требование имеет сценарий, каждая задача — путь и проверку."""
    change = store.get_change(change_id)
    problems: list[str] = []
    requirements: list[dict[str, Any]] = []
    seen_req: set[str] = set()
    seen_case: set[str] = set()
    for path, text in _spec_texts(root, change_id):
        for req in specbrief.parse_requirements(text):
            rid = req["id"]
            if rid in seen_req:
                problems.append(f"{path}: дублирующийся ID требования {rid}")
            seen_req.add(rid)
            cases = []
            for case in req["scenarios"]:
                cid = case["id"]
                if cid in seen_case:
                    problems.append(f"{path}: дублирующийся ID сценария {cid}")
                seen_case.add(cid)
                cases.append(cid)
            if not cases:
                problems.append(f"{path}: требование {rid} без сценария — критерию неоткуда взяться")
            requirements.append({"id": rid, "title": req["title"], "cases": cases})

    tasks_path = os.path.join(change_dir(root, change_id), "tasks.md")
    tasks: list[dict[str, Any]] = []
    if not os.path.isfile(tasks_path):
        problems.append("в change нет tasks.md: задачи неоткуда взять")
    else:
        tasks = _tasks(_read(tasks_path))
        if not tasks:
            problems.append("tasks.md не содержит задач вида `- [ ] 1.1 ...`")
        for task in tasks:
            if not task["paths"]:
                problems.append(f"задача {task['id']}: не указан ни один файл в обратных кавычках")
            if not task["has_check"]:
                problems.append(f"задача {task['id']}: не указан способ проверки")

    # покрытие сценариев брифами уже привязанных задач — информационно
    covered: set[str] = set()
    for task in store.tasks_for_change(change_id):
        if not task.get("brief_json"):
            continue
        try:
            brief = json.loads(task["brief_json"])
        except (TypeError, ValueError):
            continue
        for criterion in (brief.get("acceptance") or {}).get("criteria") or []:
            if criterion.get("case_id") and criterion.get("mandatory", True):
                covered.add(str(criterion["case_id"]))
    missing_cases = [case for req in requirements for case in req["cases"] if case not in covered]

    return {
        "change_id": change_id,
        "revision": change["revision"],
        "requirements": requirements,
        "tasks": tasks,
        "coverage": {
            "requirements": len(requirements),
            "requirements_with_case": sum(1 for r in requirements if r["cases"]),
            "cases": len(seen_case),
            "tasks": len(tasks),
            "cases_covered_by_briefs": len(covered),
        },
        "cases_without_brief": missing_cases,
        "problems": problems,
        "ok": not problems,
    }


# ------------------------------------------------------------------ Ready-гейт


def ready_check(store: Store, root: str, change_id: str, spec_ref: str | None = None) -> dict[str, Any]:
    change = store.get_change(change_id)
    problems: list[str] = []
    path = change_dir(root, change_id)
    if not os.path.isdir(path):
        problems.append(f"каталог change не найден: openspec/changes/{change_id}")
        return {"ok": False, "problems": problems, "change": change}

    spec_ref_problem = _check_spec_ref_format(spec_ref or change.get("spec_ref"))
    if spec_ref_problem:
        problems.append(spec_ref_problem)

    trace = check_trace(store, root, change_id)
    problems.extend(trace["problems"])

    reviews = store.change_reviews(change_id, int(change["revision"]))
    review_rows: list[dict[str, Any]] = []
    required = required_artifacts(str(change["risk_class"]), path)
    for artifact, roles in required.items():
        try:
            current_hash = artifact_hash(root, change_id, artifact)
        except ChangeError as exc:
            problems.append(f"{artifact}: {exc.reason}")
            continue
        approved = [
            r for r in reviews
            if r["artifact"] == artifact and r["verdict"] == "approve" and r["artifact_hash"] == current_hash
            and r["reviewer_id"] != (r["author_id"] or "")
        ]
        by_role: dict[str, set[str]] = {}
        for r in approved:
            by_role.setdefault(str(r["reviewer_role"]), set()).add(str(r["reviewer_id"]))
        for role in roles:
            reviewers = by_role.get(role, set())
            need = 2 if change["risk_class"] == "L2" else 1
            if len(reviewers) < need:
                problems.append(
                    f"{artifact}: нет актуального approve от роли {role} (нужно {need}, есть {len(reviewers)})"
                )
        review_rows.append({
            "artifact": artifact,
            "roles": {role: sorted(by_role.get(role, set())) for role in roles},
            "approved": len(approved),
        })

    gaps = change.get("payload", {}).get("gaps") or []
    blocking = [g for g in gaps if g.get("blocking", True) and not g.get("resolved")]
    if blocking:
        problems.append("открытые блокирующие GAP: " + ", ".join(str(g.get("id") or g.get("question")) for g in blocking))

    return {
        "ok": not problems,
        "change_id": change_id,
        "revision": change["revision"],
        "risk_class": change["risk_class"],
        "spec_ref": change.get("spec_ref"),
        "reviews": review_rows,
        "trace": trace,
        "problems": problems,
    }


# ------------------------------------------------------------------ приёмка


def _record_path(root: str, change_id: str, name: str) -> str:
    return os.path.join(change_dir(root, change_id), name)


def _load_record(root: str, change_id: str, name: str) -> dict[str, Any] | None:
    try:
        with open(_record_path(root, change_id, name), encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_record(root: str, change_id: str, name: str, data: dict[str, Any]) -> str:
    path = _record_path(root, change_id, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def _valid_services(services: list[dict[str, Any]]) -> list[str]:
    problems = []
    if not services:
        return ["не указаны сервисы и их версии"]
    for service in services:
        if not str(service.get("name") or "").strip():
            problems.append("у сервиса нет имени")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", str(service.get("sha") or "")):
            problems.append(f"сервис {service.get('name')!r} без полного SHA")
    return problems


def _same_services(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    def key(row: dict[str, Any]) -> tuple[str, str]:
        return (str(row.get("name")), str(row.get("sha")).lower())
    return {key(x) for x in left or []} == {key(x) for x in right or []}


def _declared_services(change: dict[str, Any]) -> list[str]:
    return [str(s.get("name")) for s in (change.get("payload") or {}).get("services") or []
            if str(s.get("name") or "").strip()]


def _order_problem(change: dict[str, Any], services: list[dict[str, Any]]) -> str | None:
    """Порядок сервисов в записи — это merge order, он должен совпадать с планом."""
    declared = _declared_services(change)
    if not declared:
        return None
    got = [str(s.get("name")) for s in services]
    if set(got) != set(declared):
        return f"сервисы {sorted(got)} не совпадают с планом {declared}"
    if got != declared:
        return f"merge order {got} не совпадает с планом {declared}"
    return None


def verification_check(store: Store, root: str, change_id: str) -> dict[str, Any]:
    from . import verification

    return verification.check_report(store, root, change_id)


def write_release(store: Store, root: str, change_id: str, *, environment: str,
                  services: list[dict[str, Any]], by: str, by_role: str = "human",
                  flag: str = "") -> dict[str, Any]:
    change = store.get_change(change_id)
    if change["status"] != "VERIFIED":
        raise ChangeError("R-CHG-1", f"выпуск возможен из VERIFIED, а не из {change['status']}")
    report = verification_check(store, root, change_id)
    if not report["ok"]:
        raise ChangeError("R-QA-1", "выпуск без состоявшейся проверки: " + "; ".join(report["problems"]))
    problems = _valid_services(services)
    order = _order_problem(change, services)
    if order:
        problems.append(order)
    if not (environment or "").strip():
        problems.append("не указана опорная среда")
    if not (by or "").strip():
        problems.append("не указано, кто выпустил")
    if problems:
        raise ChangeError("R-ACCEPT-1", "; ".join(problems))
    record = {
        "change_id": change_id,
        "spec_ref": change["spec_ref"],
        "environment": environment,
        "services": services,
        "merge_order": [str(s.get("name")) for s in services],
        "by": by,
        "by_role": by_role,
        "flag": flag,
        "at": _iso(),
    }
    _write_record(root, change_id, "release.json", record)
    store.append_change_event(new_id("cevt"), change_id, "release_record", record,
                              {"ok": True})
    return record


def release_check(store: Store, root: str, change_id: str) -> dict[str, Any]:
    change = store.get_change(change_id)
    problems: list[str] = []
    report = verification_check(store, root, change_id)
    if not report["ok"]:
        problems.extend(report["problems"])
    record = _load_record(root, change_id, "release.json")
    if not record:
        return {"ok": False, "problems": problems + ["нет release.json — выпуск не записан"]}
    if str(record.get("spec_ref")) != str(change.get("spec_ref")):
        problems.append("release.spec_ref не совпадает с планом")
    if not str(record.get("environment") or "").strip():
        problems.append("release: не указана среда")
    problems.extend(_valid_services(record.get("services") or []))
    order = _order_problem(change, record.get("services") or [])
    if order:
        problems.append("release: " + order)
    if report.get("report") and not _same_services(record.get("services") or [],
                                                    report["report"].get("services") or []):
        problems.append("release.services не совпадают с проверенными версиями")
    if not str(record.get("by") or "").strip():
        problems.append("release: не указано, кто выпустил")
    if change["risk_class"] == "L2" and record.get("by_role") != "human":
        problems.append("L2: выпуск должен подтвердить человек")
    return {"ok": not problems, "problems": problems, "record": record}


def write_acceptance(store: Store, root: str, change_id: str, *, accepted_by: str,
                     criteria: list[str], environment: str,
                     services: list[dict[str, Any]], by_role: str = "human",
                     residual_risks: list[str] | None = None) -> dict[str, Any]:
    change = store.get_change(change_id)
    if change["status"] not in ("VERIFIED", "RELEASED"):
        raise ChangeError("R-ACCEPT-1", f"приёмка возможна после проверки/выпуска, а не из {change['status']}")
    if not (accepted_by or "").strip():
        raise ChangeError("R-ACCEPT-1", "приёмка без принимающего — это не приёмка")
    if not criteria:
        raise ChangeError("R-ACCEPT-1", "приёмка без критериев не принимается")
    problems = _valid_services(services)
    order = _order_problem(change, services)
    if order:
        problems.append(order)
    if problems:
        raise ChangeError("R-ACCEPT-1", "; ".join(problems))
    record = {
        "change_id": change_id,
        "spec_ref": change["spec_ref"],
        "environment": environment,
        "services": services,
        "merge_order": [str(s.get("name")) for s in services],
        "accepted_by": accepted_by,
        "accepted_by_role": by_role,
        "criteria": criteria,
        "residual_risks": residual_risks or [],
        "verification": "verification.json",
        "at": _iso(),
    }
    _write_record(root, change_id, "acceptance.json", record)
    store.append_change_event(new_id("cevt"), change_id, "acceptance_record", record,
                              {"ok": True})
    return record


def acceptance_check(store: Store, root: str, change_id: str) -> dict[str, Any]:
    change = store.get_change(change_id)
    problems: list[str] = []
    record = _load_record(root, change_id, "acceptance.json")
    if not record:
        return {"ok": False, "problems": ["нет acceptance.json — приёмки не было"]}
    if str(record.get("spec_ref")) != str(change.get("spec_ref")):
        problems.append("acceptance.spec_ref не совпадает с планом")
    if not str(record.get("accepted_by") or "").strip():
        problems.append("acceptance: не указан принимающий")
    if not record.get("criteria"):
        problems.append("acceptance: нет критериев приёмки")
    problems.extend(_valid_services(record.get("services") or []))
    order = _order_problem(change, record.get("services") or [])
    if order:
        problems.append("acceptance: " + order)
    release = _load_record(root, change_id, "release.json")
    if not release:
        if change["risk_class"] != "L0":
            problems.append("нет release.json: приёмка без выпуска допустима только для L0")
    else:
        if str(release.get("environment")) != str(record.get("environment")):
            problems.append("acceptance.environment не совпадает с release.environment")
        if [str(s.get("name")) for s in release.get("services") or []] != [
            str(s.get("name")) for s in record.get("services") or []
        ]:
            problems.append("acceptance.services не совпадают по порядку с release (merge order)")
    if change["risk_class"] == "L2":
        if record.get("accepted_by_role") != "human":
            problems.append("L2: приёмку должен подтвердить человек")
        if not release:
            problems.append("L2: приёмка без release-записи запрещена")
    verification = verification_check(store, root, change_id)
    if not verification["ok"]:
        problems.extend(verification["problems"])
    return {"ok": not problems, "problems": problems, "record": record}


def write_evidence(store: Store, root: str, change_id: str) -> dict[str, Any]:
    """Пакет доказательств: файлы change + вердикты задач, с хеш-манифестом."""
    base = change_dir(root, change_id)
    if not os.path.isdir(base):
        raise ChangeError("R-ACCEPT-1", f"каталог change не найден: {change_id}")
    target = os.path.join(os.path.abspath(root), "evidence", f"change-{change_id}")
    os.makedirs(target, exist_ok=True)
    files: list[dict[str, Any]] = []
    root_abs = os.path.abspath(root)
    candidates: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(base):
        for name in filenames:
            candidates.append(os.path.join(dirpath, name))
    for task in store.tasks_for_change(change_id):
        verdict_dir = os.path.join(root_abs, ".harness", "verdicts")
        if os.path.isdir(verdict_dir):
            for name in sorted(os.listdir(verdict_dir)):
                if name.startswith(task["task_id"] + "-"):
                    candidates.append(os.path.join(verdict_dir, name))
    for path in sorted(set(candidates)):
        with open(path, "rb") as fh:
            payload = fh.read()
        rel = os.path.relpath(path, root_abs).replace("\\", "/")
        dest = os.path.join(target, rel.replace("/", "__"))
        with open(dest, "wb") as fh:
            fh.write(payload)
        files.append({
            "path": rel,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "copy": os.path.relpath(dest, root_abs).replace("\\", "/"),
        })
    manifest = {
        "change_id": change_id,
        "revision": store.get_change(change_id)["revision"],
        "spec_ref": store.get_change(change_id)["spec_ref"],
        "created_at": _iso(),
        "files": files,
    }
    path = os.path.join(target, "manifest.json")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    store.append_change_event(new_id("cevt"), change_id, "evidence", {"files": len(files)},
                              {"ok": True, "path": os.path.relpath(path, root_abs)})
    return {"path": path, "manifest": manifest}


def archive_check(store: Store, root: str, change_id: str) -> dict[str, Any]:
    problems: list[str] = []
    acceptance = acceptance_check(store, root, change_id)
    if not acceptance["ok"]:
        problems.extend(acceptance["problems"])
    closing = _load_record(root, change_id, "closing.json")
    if not closing:
        problems.append("нет closing.json — закрытие не зафиксировано")
    else:
        for key in ("specs_synced", "cases_promoted", "contracts_promoted"):
            if not closing.get(key):
                problems.append(f"closing: {key} = false")
    manifest_path = os.path.join(os.path.abspath(root), "evidence", f"change-{change_id}", "manifest.json")
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (FileNotFoundError, ValueError):
        problems.append("нет пакета доказательств (evidence/change-<id>/manifest.json)")
        manifest = {"files": []}
    names = {str(item.get("path") or "") for item in manifest.get("files") or []}
    for needed in ("verification.json", "acceptance.json"):
        if not any(name.endswith(needed) for name in names):
            problems.append(f"в пакете доказательств нет {needed}")
    return {"ok": not problems, "problems": problems, "manifest": manifest_path}


def write_closing(root: str, change_id: str, *, by: str, specs_synced: bool,
                  cases_promoted: bool, contracts_promoted: bool) -> dict[str, Any]:
    record = {
        "change_id": change_id,
        "specs_synced": bool(specs_synced),
        "cases_promoted": bool(cases_promoted),
        "contracts_promoted": bool(contracts_promoted),
        "by": by,
        "at": _iso(),
    }
    _write_record(root, change_id, "closing.json", record)
    return record


# ------------------------------------------------------------------ переходы


def _apply_transition(store: Store, change_id: str, event: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    change = store.get_change(change_id)
    state = change["status"]
    allowed = TRANSITIONS.get(state, {})
    if event not in allowed:
        raise ChangeError(
            "R-CHG-1",
            f"переход {event!r} из состояния {state} запрещён",
            state=state,
            allowed=sorted(allowed),
        )
    target = allowed[event]
    if target == "__PREVIOUS__":
        target = str(change.get("payload", {}).get("pre_block_status") or "DRAFT")

    updates: dict[str, Any] = {"status": target}
    body = dict(change.get("payload") or {})

    if event == "ready":
        spec_ref = str(payload.get("spec_ref") or change.get("spec_ref") or "").strip()
        if not _SHA.match(spec_ref):
            raise ChangeError("R-REF-1", "Ready требует полный spec_ref (40 hex)")
        report = ready_check(store, root=payload.get("root") or ".", change_id=change_id,
                             spec_ref=spec_ref)
        if not report["ok"]:
            raise ChangeError("R-TRACE-2" if any("сценар" in p or "задач" in p for p in report["problems"])
                              else "R-CHG-1",
                              "Ready-гейт не пройден: " + "; ".join(report["problems"]),
                              problems=report["problems"])
        updates["spec_ref"] = spec_ref
    elif event == "revise":
        updates["revision"] = int(change["revision"]) + 1
        revisions = list(body.get("revision_history") or [])
        revisions.append({
            "revision": updates["revision"],
            "reason": payload.get("reason", ""),
            "at": _iso(),
        })
        body["revision_history"] = revisions
        updates["spec_ref"] = None
    elif event == "block":
        body["pre_block_status"] = state
        body["block_reason"] = payload.get("reason", "")
    elif event == "start":
        tasks = store.tasks_for_change(change_id)
        if not tasks:
            body["started_without_tasks"] = True
    elif event == "tasks_done":
        tasks = store.tasks_for_change(change_id)
        not_done = [t["task_id"] for t in tasks if t["state"] != "COMMITTED"]
        if not_done:
            raise ChangeError("R-CHG-1", "не все задачи закоммичены: " + ", ".join(not_done))
    elif event == "verify":
        check = verification_check(store, root=payload.get("root") or ".", change_id=change_id)
        if not check["ok"]:
            rule = "R-QA-2" if any("нулевой" in p for p in check["problems"]) else "R-QA-1"
            raise ChangeError(rule, "проверка изменения не пройдена: " + "; ".join(check["problems"]),
                              problems=check["problems"])
    elif event == "release":
        check = release_check(store, root=payload.get("root") or ".", change_id=change_id)
        if not check["ok"]:
            rule = "R-ACCEPT-2" if any("L2" in p for p in check["problems"]) else "R-ACCEPT-1"
            raise ChangeError(rule, "выпуск не подтверждён: " + "; ".join(check["problems"]),
                              problems=check["problems"])
    elif event == "accept":
        check = acceptance_check(store, root=payload.get("root") or ".", change_id=change_id)
        if not check["ok"]:
            rule = "R-ACCEPT-2" if any("L2" in p for p in check["problems"]) else "R-ACCEPT-1"
            raise ChangeError(rule, "приёмка не подтверждена: " + "; ".join(check["problems"]),
                              problems=check["problems"])
    elif event == "archive":
        check = archive_check(store, root=payload.get("root") or ".", change_id=change_id)
        if not check["ok"]:
            raise ChangeError("R-ACCEPT-1", "закрытие не подтверждено: " + "; ".join(check["problems"]),
                              problems=check["problems"])

    updates["payload"] = body
    store.update_change(change_id, **updates)
    store.append_change_event(new_id("cevt"), change_id, event, payload,
                              {"ok": True, "from": state, "to": target})
    return store.get_change(change_id)


def transition(store: Store, root: str, change_id: str, event: str,
               payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(payload or {})
    payload.setdefault("root", root)
    if event == "ready":
        change = store.get_change(change_id)
        if change["status"] != "DESIGN_AGREED":
            raise ChangeError("R-CHG-1", f"Ready возможен из DESIGN_AGREED, а не из {change['status']}")
    return _apply_transition(store, change_id, event, payload)


# ------------------------------------------------------------------ проверки правил

def check_review(record: dict[str, Any]) -> dict[str, Any]:
    """R-REV-1: review-запись без проверяющего, критериев и хеша не принимается."""
    missing = []
    if not record.get("reviewer_id"):
        missing.append("reviewer_id")
    if record.get("verdict") == "approve" and not record.get("criteria"):
        missing.append("criteria")
    if not str(record.get("artifact_hash") or "").startswith("sha256:"):
        missing.append("artifact_hash")
    if missing:
        return {"ok": False, "rule": "R-REV-1", "reason": "review без " + ", ".join(missing)}
    return {"ok": True}


def check_reviewer(record: dict[str, Any]) -> dict[str, Any]:
    """R-REV-2: автор артефакта не может быть его проверяющим."""
    author = record.get("author_id")
    if author and author == record.get("reviewer_id"):
        return {"ok": False, "rule": "R-REV-2", "reason": "автор артефакта проверяет сам себя"}
    return {"ok": True}


CONTROL_POINTS = {
    "changefsm.gate_dispatch": gate_dispatch,
    "changefsm.check_spec_ref": check_spec_ref,
    "changefsm.check_review": check_review,
    "changefsm.check_reviewer": check_reviewer,
    "changefsm.check_trace": check_trace,
    "changefsm.verification_check": verification_check,
    "changefsm.release_check": release_check,
    "changefsm.acceptance_check": acceptance_check,
    "changefsm.archive_check": archive_check,
}
