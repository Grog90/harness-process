#!/usr/bin/env python3
"""lessons.py — журнал ошибок и продвижение уроков в правила.

Ступени (см. docs/LESSONS.md):
    1. случай     -> journal/YYYY-MM.md
    2. правило    -> AGENTS.md, раздел «Уроки» (после маркера lessons:auto)
    3. автоматика -> policies/rules.json + исполняемая проверка

Только стандартная библиотека, Python 3.10+.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ENTRY_RE = re.compile(r"^## (?P<id>L-\d{8}-\d{2})\s*·\s*(?P<slug>.+?)\s*$", re.MULTILINE)
FIELD_RE = re.compile(r"^- \*\*(?P<key>[^*]+?):\*\*\s*(?P<value>.*)$")
MARKER = "<!-- lessons:auto -->"
SECTION = "## Уроки"
LESSON_LINE_RE = re.compile(r"^- \[(?P<id>L-\d{8}-\d{2})\]\s*(?P<text>.+)$")

TYPES = ("среда", "домен", "поведение")
REQUIRED = ("Тип", "Дата", "Триггер", "Симптом", "Правило", "Область")
SECRET_WORDS = ("password", "secret", "api_key", "apikey", "token", "passwd", "-----begin")


@dataclass
class Entry:
    id: str
    slug: str
    fields: dict[str, str]
    path: Path
    line: int

    def get(self, key: str, default: str = "") -> str:
        return self.fields.get(key, default).strip()

    @property
    def fingerprint(self) -> str:
        tag = self.get("Тег")
        return norm(tag) if tag else norm(self.get("Триггер"))


@dataclass
class Journal:
    root: Path
    entries: list[Entry] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def journal_dir(self) -> Path:
        return self.root / "journal"

    @property
    def agents_path(self) -> Path:
        return self.root / "AGENTS.md"

    def by_id(self, entry_id: str) -> Entry | None:
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        return None

    def agents_text(self) -> str:
        path = self.agents_path
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def lesson_lines(self) -> list[tuple[str, str]]:
        """Строки вида (id, текст) из раздела уроков в AGENTS.md."""
        found: list[tuple[str, str]] = []
        inside = False
        for line in self.agents_text().splitlines():
            if line.startswith(SECTION):
                inside = True
                continue
            if inside and line.startswith("## "):
                inside = False
                continue
            if not inside:
                continue
            match = LESSON_LINE_RE.match(line)
            if match:
                found.append((match.group("id"), match.group("text")))
        return found

    def is_promoted(self, entry: Entry) -> bool:
        return any(entry.id == lesson_id for lesson_id, _ in self.lesson_lines())

    def promoted_ids(self) -> set[str]:
        return {lesson_id for lesson_id, _ in self.lesson_lines()}


def norm(value: str) -> str:
    value = value.lower().replace("ё", "е")
    value = re.sub(r"[^\w\s]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def tokens(value: str) -> set[str]:
    return set(norm(value).split())


def jaccard(left: str, right: str) -> float:
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def similarity(left: str, right: str) -> float:
    """Похожесть правил: пересечение слов плюс вложенность (одно — подмножество другого).

    Короткие формулировки (< 3 слов) сравниваются только по Jaccard, иначе «запускать тесты»
    считалось бы дубликатом «запускать тесты перед коммитом».
    """
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    common = len(a & b)
    jac = common / len(a | b)
    if min(len(a), len(b)) < 3:
        return jac
    return max(jac, common / min(len(a), len(b)))


def load_journal(root: Path) -> Journal:
    journal = Journal(root=root)
    directory = journal.journal_dir
    if not directory.exists():
        return journal
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        matches = list(ENTRY_RE.finditer(text))
        for index, match in enumerate(matches):
            body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            body = text[match.end():body_end]
            fields: dict[str, str] = {}
            for raw in body.splitlines():
                field_match = FIELD_RE.match(raw.strip())
                if field_match:
                    fields[field_match.group("key").strip()] = field_match.group("value").strip()
            journal.entries.append(
                Entry(
                    id=match.group("id"),
                    slug=match.group("slug").strip(),
                    fields=fields,
                    path=path,
                    line=text[: match.start()].count("\n") + 1,
                )
            )
    seen: dict[str, Entry] = {}
    for entry in journal.entries:
        if entry.id in seen:
            journal.problems.append(
                f"{entry.path.name}:{entry.line}: повтор id {entry.id} "
                f"(первый — {seen[entry.id].path.name}:{seen[entry.id].line})"
            )
        seen[entry.id] = entry
    return journal


def render_entry(entry_id: str, slug: str, values: dict[str, str]) -> str:
    order = (
        "Дата",
        "Тип",
        "Тег",
        "Триггер",
        "Симптом",
        "Причина",
        "Правило",
        "Область",
        "Проверка",
        "Не сработало",
    )
    lines = [f"## {entry_id} · {slug}", ""]
    for key in order:
        value = values.get(key, "").strip()
        if value:
            lines.append(f"- **{key}:** {value}")
    lines.extend(["", ""])
    return "\n".join(lines)


def next_id(entries: list[Entry], day: str) -> str:
    prefix = f"L-{day.replace('-', '')}-"
    used = [int(e.id[-2:]) for e in entries if e.id.startswith(prefix)]
    return f"{prefix}{(max(used) + 1) if used else 1:02d}"


def collapse(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\n", " ")).strip()


# --------------------------------------------------------------------------- команды


def cmd_new(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    journal = load_journal(root)
    day = args.date or date.today().isoformat()
    entry_id = next_id(journal.entries, day)
    values = {
        "Дата": day,
        "Тип": args.type,
        "Тег": collapse(args.tag or ""),
        "Триггер": collapse(args.trigger),
        "Симптом": collapse(args.symptom),
        "Причина": collapse(args.cause or ""),
        "Правило": collapse(args.rule),
        "Область": collapse(args.scope),
        "Проверка": collapse(args.check or ""),
        "Не сработало": collapse(args.tried or ""),
    }
    if values["Тип"] not in TYPES:
        print(f"ОТКАЗ [тип]: ожидалось одно из {', '.join(TYPES)}", file=sys.stderr)
        return 2

    directory = journal.journal_dir
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{day[:7]}.md"
    if not path.exists():
        path.write_text(f"# Журнал ошибок · {day[:7]}\n\n", encoding="utf-8", newline="\n")
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(render_entry(entry_id, collapse(args.slug), values))
    print(entry_id)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    journal = load_journal(Path(args.root).resolve())
    promoted = journal.promoted_ids()
    rows = []
    for entry in journal.entries:
        state = "правило" if entry.id in promoted else "журнал"
        if args.state != "all" and args.state != state:
            continue
        check = entry.get("Проверка") or "—"
        rows.append((entry.id, state, entry.get("Тип"), entry.get("Область") or "—", check, entry.slug))
    if not rows:
        print("записей нет")
        return 0
    widths = [max(len(str(row[i])) for row in rows + [("#", "состояние", "тип", "область", "проверка", "slug")]) for i in range(5)]
    header = ("#", "состояние", "тип", "область", "проверка", "slug")
    print("  ".join(h.ljust(w) for h, w in zip(header, widths)))
    for row in rows:
        print("  ".join(str(v).ljust(w) for v, w in zip(row[:5], widths)) + "  " + row[5])
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    journal = load_journal(Path(args.root).resolve())
    promoted = journal.promoted_ids()
    groups: dict[str, list[Entry]] = {}
    for entry in journal.entries:
        if entry.id in promoted:
            continue
        groups.setdefault(entry.fingerprint, []).append(entry)

    candidates = []
    for key, items in groups.items():
        if key and len(items) >= args.min:
            candidates.append((len(items), key, items))
    candidates.sort(key=lambda item: (-item[0], item[1]))

    if args.json:
        payload = [
            {
                "count": count,
                "fingerprint": key,
                "entries": [
                    {"id": e.id, "slug": e.slug, "rule": e.get("Правило"), "scope": e.get("Область")}
                    for e in items
                ],
            }
            for count, key, items in candidates
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not candidates:
        print(f"кандидатов нет: нет повторов >= {args.min} среди непродвинутых записей")
        return 0
    print(f"кандидаты на ступень 2 (повторов >= {args.min}):\n")
    for count, key, items in candidates:
        print(f"[{count}x] {key}")
        for entry in items:
            print(f"   {entry.id}  {entry.slug}")
            print(f"      правило: {entry.get('Правило')}")
        print(f"   -> python tools/lessons.py promote {items[0].id}\n")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    journal = load_journal(root)
    errors: list[str] = []
    warnings: list[str] = []
    errors.extend(journal.problems)

    agents = journal.agents_path
    if not agents.exists():
        errors.append("AGENTS.md отсутствует: агенту нечего читать")
        text = ""
    else:
        text = agents.read_text(encoding="utf-8")

    if text:
        total = len(text.splitlines())
        if total > args.max_lines:
            errors.append(f"AGENTS.md: {total} строк при бюджете {args.max_lines} — курируйте, не дописывайте")
        section_lines = _section_lines(text)
        if section_lines > args.max_lesson_lines:
            errors.append(
                f"раздел «Уроки»: {section_lines} строк при бюджете {args.max_lesson_lines} "
                f"(всегда в контексте) — переносите в docs/pitfalls/"
            )
        if text.count(MARKER) != 1:
            errors.append(f"маркер {MARKER} должен встречаться ровно один раз (сейчас {text.count(MARKER)})")

    lessons = journal.lesson_lines()
    seen: dict[str, str] = {}
    for lesson_id, lesson_text in lessons:
        if lesson_id in seen:
            errors.append(f"AGENTS.md: {lesson_id} продублирован")
            continue
        seen[lesson_id] = lesson_text
        if journal.by_id(lesson_id) is None:
            warnings.append(f"AGENTS.md: {lesson_id} нет в журнале — правило без происхождения")

    entries = list(seen.items())
    for left in range(len(entries)):
        for right in range(left + 1, len(entries)):
            score = similarity(entries[left][1], entries[right][1])
            if score >= 0.85:
                errors.append(
                    f"AGENTS.md: {entries[left][0]} и {entries[right][0]} почти дубликаты "
                    f"(совпадение {score:.0%}) — оставьте одно"
                )

    for entry in journal.entries:
        missing = [key for key in REQUIRED if not entry.get(key)]
        if missing:
            errors.append(f"{entry.path.name}:{entry.line}: {entry.id} без полей: {', '.join(missing)}")
        if entry.get("Тип") and entry.get("Тип") not in TYPES:
            errors.append(f"{entry.id}: неизвестный тип «{entry.get('Тип')}»")
        haystack = " ".join(entry.fields.values()).lower()
        for word in SECRET_WORDS:
            if word in haystack:
                errors.append(f"{entry.id}: похоже на секрет («{word}») — журнал не место для кредов")
        if entry.id in journal.promoted_ids() and not entry.get("Проверка"):
            warnings.append(f"{entry.id}: правило без проверки — кандидат на ступень 3 (--shape json)")

    for warning in warnings:
        print(f"ВНИМАНИЕ {warning}")
    for error in errors:
        print(f"ОТКАЗ {error}")

    if errors:
        return 2
    print(
        f"OK: записей {len(journal.entries)}, правил в AGENTS.md {len(lessons)}, "
        f"строк {len(text.splitlines()) if text else 0}"
    )
    return 0


def _section_lines(text: str) -> int:
    inside = False
    count = 0
    for line in text.splitlines():
        if line.startswith(SECTION):
            inside = True
            continue
        if inside and line.startswith("## "):
            break
        if inside:
            count += 1
    return count


def cmd_promote(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    journal = load_journal(root)
    entry = journal.by_id(args.entry_id)
    if entry is None:
        print(f"ОТКАЗ [id]: {args.entry_id} нет в журнале", file=sys.stderr)
        return 2

    rule = args.rule or entry.get("Правило")
    scope = entry.get("Область")
    if not rule:
        print(f"ОТКАЗ [id]: у {entry.id} нет поля «Правило»", file=sys.stderr)
        return 2

    if args.shape == "json":
        payload = {
            "id": "R-НОВОЕ-1",
            "statement": rule,
            "event": "pretooluse",
            "enforcement": "blocking",
            "implemented": False,
            "hook": "",
            "positive_test": f"{entry.get('Триггер')} — разрешено",
            "negative_test": f"{entry.get('Симптом')} — deny",
            "owner": "",
            "residual_risk": entry.get("Причина") or "не описано",
        }
        print(
            "# заготовка для policies/rules.json; id, event, hook и тесты заполнить руками.\n"
            "# вносить через: python -m harness rule change --id <R-...> --reason \"...\"\n"
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    line = f"- [{entry.id}] {rule}"
    if scope:
        line += f" — {scope}"
    if len(line) > args.max_width:
        line = line[: args.max_width - 1].rstrip() + "…"

    agents = journal.agents_path
    text = agents.read_text(encoding="utf-8") if agents.exists() else ""
    if entry.id in journal.promoted_ids():
        print(f"{entry.id} уже в AGENTS.md — ничего не делаю")
        return 0
    if MARKER not in text:
        print(f"ОТКАЗ [маркер]: в AGENTS.md нет {MARKER}", file=sys.stderr)
        return 2

    print("--- AGENTS.md")
    print("+++ AGENTS.md")
    print(f"@@ {SECTION} @@")
    print(f" {MARKER}")
    print(f"+{line}")

    siblings = [
        other
        for other in journal.entries
        if other.id != entry.id
        and other.fingerprint == entry.fingerprint
        and other.id not in journal.promoted_ids()
    ]
    if siblings:
        print("\nв той же группе ещё записи (сведите в одно правило, не плодите близнецов):")
        for other in siblings:
            print(f"  {other.id}: {other.get('Правило')}")

    if args.apply:
        agents.write_text(_insert_after_marker(text, line, MARKER), encoding="utf-8", newline="\n")
        print(f"\nвставлено в {agents}")
    return 0


def _insert_after_marker(text: str, line: str, marker: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    inserted = False
    for current in lines:
        out.append(current)
        if not inserted and current.strip() == marker:
            out.append(line)
            inserted = True
    result = "\n".join(out)
    return result if result.endswith("\n") else result + "\n"


# --------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lessons.py",
        description="Журнал ошибок и продвижение уроков: случай -> правило -> автоматика.",
    )
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]), help="корень репозитория")
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="записать случай в журнал")
    new.add_argument("--slug", required=True)
    new.add_argument("--type", required=True, dest="type", help="среда | домен | поведение")
    new.add_argument("--trigger", required=True)
    new.add_argument("--symptom", required=True)
    new.add_argument("--rule", required=True)
    new.add_argument("--scope", required=True)
    new.add_argument("--cause", default="")
    new.add_argument("--tried", default="")
    new.add_argument("--check", default="")
    new.add_argument("--tag", default="")
    new.add_argument("--date", default="")
    new.set_defaults(func=cmd_new)

    listing = sub.add_parser("list", help="показать записи и состояние")
    listing.add_argument("--state", choices=("журнал", "правило", "all"), default="all")
    listing.set_defaults(func=cmd_list)

    scan = sub.add_parser("scan", help="кандидаты на ступень 2 (повторы)")
    scan.add_argument("--min", type=int, default=2)
    scan.add_argument("--json", action="store_true")
    scan.set_defaults(func=cmd_scan)

    check = sub.add_parser("check", help="дисциплина AGENTS.md и журнала (CI)")
    check.add_argument("--max-lines", type=int, default=160)
    check.add_argument("--max-lesson-lines", type=int, default=25)
    check.set_defaults(func=cmd_check)

    promote = sub.add_parser("promote", help="предложить правило для AGENTS.md или rules.json")
    promote.add_argument("entry_id")
    promote.add_argument("--shape", choices=("agents", "json"), default="agents")
    promote.add_argument("--apply", action="store_true")
    promote.add_argument("--rule", default="", help="своя формулировка правила (иначе из записи)")
    promote.add_argument("--max-width", type=int, default=110)
    promote.set_defaults(func=cmd_promote)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
