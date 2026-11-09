#!/usr/bin/env python3
"""instincts.py — записи о привычках: проверка, поиск и расчёт уверенности.

Запись живёт в `docs/instincts/I-YYYYMMDD-NN.md` и отвечает на вопрос «когда это
срабатывает и насколько этому верить». Формат описан в `docs/INSTINCTS.md`.

Команды:
    check           поля, числа, дата, статус и разрешение ссылок
    match "<текст>" записи, чьё условие/правило/область содержат слова запроса
    explain <id>    поля записи и расчёт уверенности по шагам

Только стандартная библиотека, Python 3.10+.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ENTRY_RE = re.compile(r"^#{1,2} (?P<id>I-\d{8}-\d{2})\s*(?:·\s*(?P<slug>.*?))?\s*$", re.MULTILINE)
FIELD_RE = re.compile(r"^- \*\*(?P<key>[^*]+?):\*\*\s*(?P<value>.*)$")
JOURNAL_HEAD_RE = re.compile(r"^## (?P<id>L-\d{8}-\d{2})(?:\s|$)", re.MULTILINE)
JOURNAL_REF_RE = re.compile(r"^journal:(?P<id>L-\d{8}-\d{2})$")
FINDING_REF_RE = re.compile(r"^finding:(?P<id>\S+)$")

FIELDS = (
    "Условие",
    "Правило",
    "Область",
    "Подтверждений",
    "Опровержений",
    "Последнее",
    "Проверка",
    "Доказательства",
    "Статус",
)
LIST_FIELD = "Доказательства"
MATCH_FIELDS = ("Условие", "Правило", "Область")
COUNT_FIELDS = ("Подтверждений", "Опровержений")
STATUSES = ("active", "retired")

FRESH_DAYS = 180
SHOW_THRESHOLD = 0.5
CHECK_BONUS = 0.2
STALE_PENALTY = 0.3

MIN_RATIO = 0.5
MIN_WORD = 3
STOPWORDS = frozenset(
    {
        "чтобы", "что", "как", "для", "при", "где", "это", "или", "если",
        "был", "была", "были", "быть", "его", "она", "они", "так", "там",
        "тут", "уже", "ещё", "еще", "мне", "все", "всё", "них", "чем", "когда",
    }
)


@dataclass
class Instinct:
    id: str
    slug: str
    fields: dict[str, str]
    evidence: list[str]
    path: Path
    line: int

    def get(self, key: str, default: str = "") -> str:
        return self.fields.get(key, default).strip()

    @property
    def where(self) -> str:
        return f"{self.path.name}:{self.line}"

    def confidence(self, today: date) -> float:
        return confidence_steps(self, today)[0]


# --------------------------------------------------------------------------- разбор


def instincts_dir(root: Path) -> Path:
    return root / "docs" / "instincts"


def _clean_ref(value: str) -> str:
    text = value.strip()
    text = text.strip("`").strip()
    text = text.strip("\"'«»").strip()
    return text.rstrip(".,;").strip()


def _split_refs(value: str) -> list[str]:
    parts = re.split(r"[,;]", value)
    return [ref for ref in (_clean_ref(part) for part in parts) if ref]


def parse_body(body: str) -> tuple[dict[str, str], list[str]]:
    """Поля записи и список доказательств.

    Доказательства бывают одной строкой (`- **Доказательства:** journal:L-...`)
    и вложенным списком под полем.
    """
    fields: dict[str, str] = {}
    evidence: list[str] = []
    current = ""
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = FIELD_RE.match(line)
        if match:
            current = match.group("key").strip()
            value = match.group("value").strip()
            fields[current] = value
            if current == LIST_FIELD:
                evidence = _split_refs(value)
            continue
        if current == LIST_FIELD and line.startswith("- "):
            ref = _clean_ref(line[2:])
            if ref:
                evidence.append(ref)
            continue
        if not raw.startswith(" "):
            current = ""
    return fields, evidence


def load_instincts(root: Path) -> list[Instinct]:
    directory = instincts_dir(root)
    if not directory.is_dir():
        return []
    found: list[Instinct] = []
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        matches = list(ENTRY_RE.finditer(text))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            fields, evidence = parse_body(text[match.end():end])
            found.append(
                Instinct(
                    id=match.group("id"),
                    slug=(match.group("slug") or "").strip(),
                    fields=fields,
                    evidence=evidence,
                    path=path,
                    line=text[: match.start()].count("\n") + 1,
                )
            )
    return found


def by_id(instincts: list[Instinct], instinct_id: str) -> Instinct | None:
    for instinct in instincts:
        if instinct.id == instinct_id:
            return instinct
    return None


# --------------------------------------------------------------------------- ссылки


def load_journal_ids(root: Path) -> set[str]:
    ids: set[str] = set()
    directory = root / "journal"
    if not directory.is_dir():
        return ids
    for path in sorted(directory.glob("*.md")):
        ids.update(match.group("id") for match in JOURNAL_HEAD_RE.finditer(path.read_text(encoding="utf-8")))
    return ids


def load_finding_ids(db_path: Path) -> set[str]:
    """id находок из реестра `.harness/state.db`; база открывается только на чтение."""
    uri = db_path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        try:
            rows = connection.execute("select finding_id from findings")
        except sqlite3.Error:
            return set()
        return {str(row[0]) for row in rows}


def resolve_ref(ref: str, root: Path, journal_ids: set[str], db_path: Path) -> str:
    """Пустая строка — ссылка разрешилась; иначе причина отказа."""
    journal = JOURNAL_REF_RE.match(ref)
    if journal:
        case_id = journal.group("id")
        if case_id in journal_ids:
            return ""
        return f"нет случая {case_id} в journal/"
    finding = FINDING_REF_RE.match(ref)
    if finding:
        finding_id = finding.group("id")
        if not db_path.is_file():
            return "нет .harness/state.db — находку негде искать"
        try:
            known = load_finding_ids(db_path)
        except (sqlite3.Error, OSError) as exc:
            return f".harness/state.db не прочиталась ({exc})"
        if finding_id in known:
            return ""
        return f"нет находки {finding_id} в findings"
    if not ref:
        return "пустая ссылка"
    candidate = Path(ref)
    if not candidate.is_absolute():
        candidate = root / ref
    if candidate.exists():
        return ""
    return f"нет файла или каталога «{ref}»"


# --------------------------------------------------------------------------- уверенность


def parse_count(raw: str) -> int | None:
    text = raw.strip().strip("`").strip()
    if not text:
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    return value if value >= 0 else None


def parse_date(raw: str) -> date | None:
    text = raw.strip().strip("`").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def num(value: float) -> str:
    return f"{value:.3f}".replace(".", ",")


def confidence_steps(instinct: Instinct, today: date) -> tuple[float, list[str]]:
    """Формула и её шаги: подтверждения / (... + 2), бонус за проверку, штраф за возраст."""
    confirmed = parse_count(instinct.get("Подтверждений")) or 0
    refuted = parse_count(instinct.get("Опровержений")) or 0
    value = confirmed / (confirmed + refuted + 2)
    steps = [f"база {num(value)} = {confirmed} / ({confirmed} + {refuted} + 2)"]

    if instinct.get("Проверка"):
        value += CHECK_BONUS
        steps.append(f"+{num(CHECK_BONUS)} за непустую «Проверку» = {num(value)}")
    else:
        steps.append(f"+{num(0.0)}: «Проверка» пуста — бонуса нет")

    last = parse_date(instinct.get("Последнее"))
    if last is None:
        steps.append("возраст не считается: «Последнее» не дата")
    else:
        age = (today - last).days
        if age > FRESH_DAYS:
            value -= STALE_PENALTY
            steps.append(f"−{num(STALE_PENALTY)}: «Последнее» старше {FRESH_DAYS} дн. ({age} дн.) = {num(value)}")
        else:
            steps.append(f"без штрафа: {age} дн. с «Последнего», порог {FRESH_DAYS}")

    clamped = min(1.0, max(0.0, value))
    if clamped != value:
        steps.append(f"обрезано в отрезок 0…1: {num(clamped)}")
    return clamped, steps


# --------------------------------------------------------------------------- проверка


def validate(
    instinct: Instinct,
    root: Path,
    today: date,
    journal_ids: set[str],
    db_path: Path,
) -> list[str]:
    errors: list[str] = []
    where = instinct.where

    for key in FIELDS:
        if key == LIST_FIELD:
            if not instinct.evidence:
                errors.append(
                    f"{where}: у {instinct.id} пустое поле «{LIST_FIELD}» — запись без доказательства "
                    f"неотличима от догадки"
                )
            continue
        if not instinct.get(key):
            errors.append(f"{where}: у {instinct.id} нет поля «{key}»")

    for key in COUNT_FIELDS:
        raw = instinct.get(key)
        if raw and parse_count(raw) is None:
            errors.append(f"{where}: «{key}» должно быть целым числом >= 0, а стоит «{raw}»")

    raw_last = instinct.get("Последнее")
    if raw_last:
        last = parse_date(raw_last)
        if last is None:
            errors.append(f"{where}: «Последнее» не дата вида ГГГГ-ММ-ДД, а «{raw_last}»")
        elif last > today:
            errors.append(f"{where}: «Последнее» ({raw_last}) в будущем — уверенность была бы выдуманной")

    status = instinct.get("Статус")
    if status and status not in STATUSES:
        errors.append(f"{where}: неизвестный статус «{status}» — допустимы {', '.join(STATUSES)}")

    for ref in instinct.evidence:
        problem = resolve_ref(ref, root, journal_ids, db_path)
        if problem:
            errors.append(f"{where}: ссылка «{ref}» не разрешилась: {problem}")
    return errors


def check_root(root: Path, today: date) -> tuple[list[Instinct], list[str]]:
    instincts = load_instincts(root)
    errors: list[str] = []
    if not instincts:
        errors.append("docs/instincts: нет ни одной записи — проверять нечего")

    seen: dict[str, Instinct] = {}
    for instinct in instincts:
        if instinct.id in seen:
            first = seen[instinct.id]
            errors.append(
                f"{instinct.where}: повтор id {instinct.id} (первый — {first.where})"
            )
            continue
        seen[instinct.id] = instinct

    journal_ids = load_journal_ids(root)
    db_path = root / ".harness" / "state.db"
    for instinct in instincts:
        errors.extend(validate(instinct, root, today, journal_ids, db_path))
    return instincts, errors


# --------------------------------------------------------------------------- поиск


def norm(value: str) -> str:
    value = value.lower().replace("ё", "е")
    value = re.sub(r"[^\w]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def words(value: str, drop_short: bool = True) -> list[str]:
    found = []
    for word in norm(value).split():
        if drop_short and len(word) < MIN_WORD:
            continue
        if word in STOPWORDS:
            continue
        if word not in found:
            found.append(word)
    return found


def common_prefix(left: str, right: str) -> int:
    size = 0
    for a, b in zip(left, right):
        if a != b:
            break
        size += 1
    return size


def words_match(left: str, right: str) -> bool:
    """Грубое сравнение word-форм: «перезапустить» ~ «перезапускает», «роутер» ~ «роутере».

    Точного стеммера в стандартной библиотеке нет, поэтому: одинаковая основа (одно слово —
    начало другого с хвостом до 3 букв) или общее начало не короче 6 букв и не меньше 60%
    от короткого слова. Этого хватает на падежи и глагольные формы и не склеивает разные корни.
    """
    if left == right:
        return True
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    if len(short) < MIN_WORD:
        return False
    if long.startswith(short) and len(long) - len(short) <= 3:
        return True
    shared = common_prefix(left, right)
    return shared >= 6 and shared / len(short) >= 0.6


def coverage(query_words: list[str], text: str) -> float:
    if not query_words:
        return 0.0
    haystack = words(text)
    hits = sum(1 for word in query_words if any(words_match(word, other) for other in haystack))
    return hits / len(query_words)


def match(root: Path, query: str, today: date, threshold: float = SHOW_THRESHOLD) -> list[tuple[Instinct, float, float]]:
    """Записи, где слова запроса есть в условии/правиле/области, по убыванию уверенности."""
    query_words = words(query)
    rows = []
    for instinct in load_instincts(root):
        text = " ".join(instinct.get(key) for key in MATCH_FIELDS)
        ratio = coverage(query_words, text)
        if query_words and ratio < MIN_RATIO:
            continue
        score = instinct.confidence(today)
        if score < threshold:
            continue
        rows.append((instinct, score, ratio))
    rows.sort(key=lambda row: (-row[1], -row[2], row[0].id))
    return rows


# --------------------------------------------------------------------------- команды


def _today(args: argparse.Namespace) -> date:
    return args.today or date.today()


def cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    today = _today(args)
    instincts, errors = check_root(root, today)
    if errors:
        for error in errors:
            print(f"ОТКАЗ {error}", file=sys.stderr)
        return 1
    for instinct in sorted(instincts, key=lambda item: item.id):
        print(f"OK {instinct.id}: уверенность {num(instinct.confidence(today))} ({instinct.where})")
    print(f"OK: записей {len(instincts)}, сегодня {today.isoformat()}")
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    today = _today(args)
    rows = match(root, args.query, today, threshold=args.min_confidence)
    if not rows:
        print(
            f"совпадений нет: порог уверенности {num(args.min_confidence)}, "
            f"слова запроса {', '.join(words(args.query)) or '—'}"
        )
        return 0
    for instinct, score, ratio in rows:
        print(f"{instinct.id}  уверенность {num(score)}  слов запроса {ratio:.0%}  {instinct.slug}")
        print(f"   условие: {instinct.get('Условие')}")
        print(f"   правило: {instinct.get('Правило')}")
        print(f"   проверка: {instinct.get('Проверка') or '—'}")
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    today = _today(args)
    instinct = by_id(load_instincts(root), args.instinct_id)
    if instinct is None:
        print(f"ОТКАЗ: записи с id {args.instinct_id} нет в docs/instincts/", file=sys.stderr)
        return 1

    print(f"{instinct.id} · {instinct.slug}")
    print(f"файл: {instinct.where}")
    for key in FIELDS:
        if key == LIST_FIELD:
            value = ", ".join(instinct.evidence) or "—"
        else:
            value = instinct.get(key) or "—"
        print(f"{key}: {value}")

    score, steps = confidence_steps(instinct, today)
    print(f"уверенность: {num(score)}  (порог показа {num(SHOW_THRESHOLD)})")
    for step in steps:
        print(f"  {step}")
    shown = "показывается в match" if score >= SHOW_THRESHOLD else "не показывается в match — подтверждать или снимать"
    print(f"  {shown}")
    return 0


# --------------------------------------------------------------------------- CLI


def iso_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"нужна дата вида ГГГГ-ММ-ДД, а не «{raw}»") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="instincts.py",
        description="Привычки: поля, доказательства, уверенность и поиск по тексту задачи.",
    )
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]), help="корень репозитория")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="проверить записи и разрешить ссылки (CI)")
    check.add_argument("--today", type=iso_date, default=None, help="дата отсчёта возраста (по умолчанию сегодня)")
    check.set_defaults(func=cmd_check)

    matching = sub.add_parser("match", help="найти записи по словам запроса")
    matching.add_argument("query")
    matching.add_argument("--min-confidence", type=float, default=SHOW_THRESHOLD)
    matching.add_argument("--today", type=iso_date, default=None)
    matching.set_defaults(func=cmd_match)

    explain = sub.add_parser("explain", help="показать запись и расчёт уверенности")
    explain.add_argument("instinct_id")
    explain.add_argument("--today", type=iso_date, default=None)
    explain.set_defaults(func=cmd_explain)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
