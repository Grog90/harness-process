"""Устойчивое состояние управляющего процесса.

Файлы с человеческими отчётами — производные. Источник истины — транзакционное
хранилище (SQLite): оно переживает перезапуск и не сбрасывается сменой агента.
Резервирование попытки и запись её запуска выполняются одной транзакцией, поэтому
счётчик не «теряет» виток при падении между шагами.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterable, Iterator

SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS tasks (
    task_id           TEXT PRIMARY KEY,
    lane              TEXT NOT NULL DEFAULT 'main',
    state             TEXT NOT NULL,
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL,
    spec_version      TEXT,
    change_id         TEXT,
    base_commit       TEXT,
    brief_json        TEXT,
    brief_hash        TEXT,
    brief_version     INTEGER NOT NULL DEFAULT 0,
    attempt           INTEGER NOT NULL DEFAULT 0,
    max_attempts      INTEGER NOT NULL DEFAULT 3,
    deadline_ts       REAL,
    candidate_snapshot TEXT,
    verifying_snapshot TEXT,
    accepted_snapshot  TEXT,
    accepted_files_snapshot TEXT,
    verdict_id        TEXT,
    policy_version    TEXT,
    escalated_reason  TEXT,
    last_event_id     TEXT
);

CREATE TABLE IF NOT EXISTS attempts (
    task_id     TEXT NOT NULL,
    attempt     INTEGER NOT NULL,
    started_at  REAL NOT NULL,
    deadline_ts REAL,
    worker_id   TEXT,
    snapshot_id TEXT,
    status      TEXT NOT NULL,
    ended_at    REAL,
    PRIMARY KEY (task_id, attempt)
);

CREATE TABLE IF NOT EXISTS verdicts (
    verdict_id    TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    attempt       INTEGER NOT NULL,
    snapshot_id   TEXT NOT NULL,
    verdict       TEXT NOT NULL,
    reviewer_id   TEXT,
    reviewer_role TEXT,
    payload       TEXT NOT NULL,
    created_at    REAL NOT NULL,
    stale         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    event_id   TEXT PRIMARY KEY,
    task_id    TEXT,
    type       TEXT NOT NULL,
    payload    TEXT,
    result     TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS findings (
    finding_id  TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    attempt     INTEGER,
    severity    TEXT NOT NULL,
    statement   TEXT NOT NULL,
    evidence    TEXT,
    owner       TEXT,
    track       TEXT,
    status      TEXT NOT NULL DEFAULT 'open',
    closed_reason TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_log (
    entry_id   TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL,
    attempt    INTEGER,
    message    TEXT NOT NULL,
    snapshot_id TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS commits (
    commit_sha   TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL,
    snapshot_id  TEXT NOT NULL,
    verdict_id   TEXT,
    message      TEXT,
    finalizer    TEXT,
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tools_audit (
    audit_id   TEXT PRIMARY KEY,
    task_id    TEXT,
    role       TEXT,
    tool       TEXT NOT NULL,
    decision   TEXT NOT NULL,
    rule       TEXT,
    path       TEXT,
    summary    TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS costs (
    entry_id   TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL,
    attempt    INTEGER,
    kind       TEXT NOT NULL,
    amount     REAL NOT NULL,
    unit       TEXT NOT NULL,
    note       TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS rule_changes (
    change_id    TEXT PRIMARY KEY,
    rule_id      TEXT NOT NULL,
    reason       TEXT NOT NULL,
    rules_hash   TEXT NOT NULL,
    tests_run    INTEGER NOT NULL,
    tests_passed INTEGER NOT NULL,
    author       TEXT,
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS lane_resources (
    slot       INTEGER PRIMARY KEY,
    task_id    TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS changes (
    change_id    TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    revision     INTEGER NOT NULL DEFAULT 1,
    risk_class   TEXT NOT NULL DEFAULT 'L1',
    owner        TEXT NOT NULL,
    title        TEXT,
    spec_ref     TEXT,
    base_commit  TEXT,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    payload      TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS change_events (
    event_id   TEXT PRIMARY KEY,
    change_id  TEXT NOT NULL,
    type       TEXT NOT NULL,
    payload    TEXT,
    result     TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS change_reviews (
    review_id      TEXT PRIMARY KEY,
    change_id      TEXT NOT NULL,
    revision       INTEGER NOT NULL,
    artifact       TEXT NOT NULL,
    artifact_hash  TEXT NOT NULL,
    reviewer_id    TEXT NOT NULL,
    reviewer_role  TEXT NOT NULL,
    author_id      TEXT,
    verdict        TEXT NOT NULL,
    criteria       TEXT,
    findings       TEXT,
    created_at     REAL NOT NULL
);
"""


def now() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class StoreError(RuntimeError):
    pass


class Store:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None, timeout=10.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),)
        )

    def _migrate(self) -> None:
        """Догоняющие изменения схемы: старые файлы состояния должны открываться."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(tasks)")}
        if "snapshot_scope" not in cols:
            self.conn.execute("ALTER TABLE tasks ADD COLUMN snapshot_scope TEXT NOT NULL DEFAULT ''")
        if "commit_sha" not in cols:
            self.conn.execute("ALTER TABLE tasks ADD COLUMN commit_sha TEXT")
        if "accepted_files_snapshot" not in cols:
            self.conn.execute("ALTER TABLE tasks ADD COLUMN accepted_files_snapshot TEXT")
        if "change_id" not in cols:
            self.conn.execute("ALTER TABLE tasks ADD COLUMN change_id TEXT")

    # ------------------------------------------------------------------ транзакции
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ задачи
    def create_task(
        self,
        task_id: str,
        *,
        spec_version: str | None = None,
        base_commit: str | None = None,
        lane: str = "main",
        policy_version: str | None = None,
        max_attempts: int = 3,
        change_id: str | None = None,
    ) -> dict[str, Any]:
        ts = now()
        with self.transaction() as c:
            c.execute(
                """INSERT INTO tasks(task_id, lane, state, created_at, updated_at, spec_version,
                                     change_id, base_commit, policy_version, max_attempts)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (task_id, lane, "CLAIMED", ts, ts, spec_version, change_id, base_commit,
                 policy_version, max_attempts),
            )
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise StoreError(f"задача не найдена: {task_id}")
        return dict(row)

    def find_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def list_tasks(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM tasks ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def update_task(self, task_id: str, **fields: Any) -> dict[str, Any]:
        if not fields:
            return self.get_task(task_id)
        fields["updated_at"] = now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.transaction() as c:
            cur = c.execute(f"UPDATE tasks SET {cols} WHERE task_id = ?", (*fields.values(), task_id))
            if cur.rowcount == 0:
                raise StoreError(f"задача не найдена: {task_id}")
        return self.get_task(task_id)

    # ------------------------------------------------------------- изменения
    def create_change(
        self,
        change_id: str,
        *,
        owner: str,
        risk_class: str = "L1",
        title: str = "",
        status: str = "DRAFT",
        revision: int = 1,
        spec_ref: str | None = None,
        base_commit: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ts = now()
        with self.transaction() as c:
            c.execute(
                """INSERT INTO changes(change_id, status, revision, risk_class, owner, title,
                                        spec_ref, base_commit, created_at, updated_at, payload)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (change_id, status, revision, risk_class, owner, title, spec_ref, base_commit,
                 ts, ts, json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)),
            )
        return self.get_change(change_id)

    def get_change(self, change_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM changes WHERE change_id = ?", (change_id,)).fetchone()
        if row is None:
            raise StoreError(f"change не найден: {change_id}")
        d = dict(row)
        d["payload"] = json.loads(d["payload"] or "{}")
        return d

    def find_change(self, change_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM changes WHERE change_id = ?", (change_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["payload"] = json.loads(d["payload"] or "{}")
        return d

    def list_changes(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM changes ORDER BY created_at").fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["payload"] = json.loads(d["payload"] or "{}")
            out.append(d)
        return out

    def update_change(self, change_id: str, **fields: Any) -> dict[str, Any]:
        if not fields:
            return self.get_change(change_id)
        if "payload" in fields and not isinstance(fields["payload"], str):
            fields["payload"] = json.dumps(fields["payload"], ensure_ascii=False, sort_keys=True)
        fields["updated_at"] = now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.transaction() as c:
            cur = c.execute(f"UPDATE changes SET {cols} WHERE change_id = ?", (*fields.values(), change_id))
            if cur.rowcount == 0:
                raise StoreError(f"change не найден: {change_id}")
        return self.get_change(change_id)

    def append_change_event(
        self,
        event_id: str,
        change_id: str,
        type_: str,
        payload: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> bool:
        sql = ("INSERT OR IGNORE INTO change_events(event_id, change_id, type, payload, result, created_at) "
               "VALUES(?,?,?,?,?,?)")
        params = (
            event_id,
            change_id,
            type_,
            json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
            json.dumps(result or {}, ensure_ascii=False, sort_keys=True),
            now(),
        )
        if conn is not None:
            return conn.execute(sql, params).rowcount > 0
        with self.transaction() as c:
            return c.execute(sql, params).rowcount > 0

    def change_events_for(self, change_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM change_events WHERE change_id = ? ORDER BY created_at, rowid", (change_id,)
        ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["payload"] = json.loads(d["payload"] or "{}")
            d["result"] = json.loads(d["result"] or "{}")
            out.append(d)
        return out

    def add_change_review(self, review: dict[str, Any]) -> dict[str, Any]:
        with self.transaction() as c:
            c.execute(
                """INSERT OR REPLACE INTO change_reviews(review_id, change_id, revision, artifact,
                       artifact_hash, reviewer_id, reviewer_role, author_id, verdict, criteria,
                       findings, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    review["review_id"],
                    review["change_id"],
                    int(review["revision"]),
                    review["artifact"],
                    review["artifact_hash"],
                    review["reviewer_id"],
                    review["reviewer_role"],
                    review.get("author_id"),
                    review["verdict"],
                    json.dumps(review.get("criteria") or [], ensure_ascii=False, sort_keys=True),
                    json.dumps(review.get("findings") or [], ensure_ascii=False, sort_keys=True),
                    review.get("created_at", now()),
                ),
            )
        return review

    def change_reviews(self, change_id: str, revision: int | None = None) -> list[dict[str, Any]]:
        if revision is None:
            rows = self.conn.execute(
                "SELECT * FROM change_reviews WHERE change_id = ? ORDER BY created_at, rowid", (change_id,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM change_reviews WHERE change_id = ? AND revision = ? ORDER BY created_at, rowid",
                (change_id, revision),
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["criteria"] = json.loads(d["criteria"] or "[]")
            d["findings"] = json.loads(d["findings"] or "[]")
            out.append(d)
        return out

    def tasks_for_change(self, change_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE change_id = ? ORDER BY created_at, rowid", (change_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------- ресурсы лейнов
    def reserve_lane_slot(self, task_id: str, slot: int) -> bool:
        """Занимает слот одной транзакцией: коллизия — False, а не совпавшие ресурсы."""
        try:
            with self.transaction() as c:
                c.execute(
                    "INSERT INTO lane_resources(slot, task_id, created_at) VALUES(?,?,?)",
                    (slot, task_id, now()),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def lane_slot(self, task_id: str) -> int | None:
        row = self.conn.execute("SELECT slot FROM lane_resources WHERE task_id = ?", (task_id,)).fetchone()
        return int(row["slot"]) if row else None

    def slot_owner(self, slot: int) -> str | None:
        row = self.conn.execute("SELECT task_id FROM lane_resources WHERE slot = ?", (slot,)).fetchone()
        return str(row["task_id"]) if row else None

    def lane_slots(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT slot, task_id, created_at FROM lane_resources ORDER BY slot"
        ).fetchall()
        return [dict(r) for r in rows]

    def release_lane_slot(self, task_id: str) -> bool:
        with self.transaction() as c:
            cur = c.execute("DELETE FROM lane_resources WHERE task_id = ?", (task_id,))
            return cur.rowcount > 0

    # ------------------------------------------------------------------ события
    def append_event(self, event_id: str, task_id: str | None, type_: str, payload: dict[str, Any] | None = None,
                     result: dict[str, Any] | None = None, *, conn: sqlite3.Connection | None = None) -> bool:
        """Возвращает True, если событие новое. Повторная доставка — no-op."""
        payload_json = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
        result_json = json.dumps(result or {}, ensure_ascii=False, sort_keys=True)
        sql = "INSERT OR IGNORE INTO events(event_id, task_id, type, payload, result, created_at) VALUES(?,?,?,?,?,?)"
        params = (event_id, task_id, type_, payload_json, result_json, now())
        if conn is not None:
            cur = conn.execute(sql, params)
            return cur.rowcount > 0
        with self.transaction() as c:
            cur = c.execute(sql, params)
            return cur.rowcount > 0

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["payload"] = json.loads(d["payload"] or "{}")
        d["result"] = json.loads(d["result"] or "{}")
        return d

    def events_for(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE task_id = ? ORDER BY created_at, rowid", (task_id,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"] or "{}")
            d["result"] = json.loads(d["result"] or "{}")
            out.append(d)
        return out

    # ------------------------------------------------------------------ попытки
    def reserve_attempt(self, task_id: str, *, deadline_ts: float | None = None,
                        worker_id: str | None = None) -> int:
        """Атомарно: проверить бюджет, увеличить счётчик, открыть попытку."""
        with self.transaction() as c:
            row = c.execute(
                "SELECT attempt, max_attempts, state FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise StoreError(f"задача не найдена: {task_id}")
            attempt, max_attempts, state = row["attempt"], row["max_attempts"], row["state"]
            if attempt >= max_attempts:
                raise StoreError(f"бюджет попыток исчерпан: {attempt}/{max_attempts}")
            attempt += 1
            ts = now()
            c.execute(
                "UPDATE tasks SET attempt = ?, state = 'RUNNING', deadline_ts = ?, updated_at = ? WHERE task_id = ?",
                (attempt, deadline_ts, ts, task_id),
            )
            c.execute(
                """INSERT INTO attempts(task_id, attempt, started_at, deadline_ts, worker_id, status)
                   VALUES(?,?,?,?,?,'running')""",
                (task_id, attempt, ts, deadline_ts, worker_id),
            )
            return attempt

    def finish_attempt(self, task_id: str, attempt: int, *, status: str,
                       snapshot_id: str | None = None) -> None:
        with self.transaction() as c:
            c.execute(
                """UPDATE attempts SET status = ?, ended_at = ?, snapshot_id = COALESCE(?, snapshot_id)
                   WHERE task_id = ? AND attempt = ?""",
                (status, now(), snapshot_id, task_id, attempt),
            )

    def get_attempt(self, task_id: str, attempt: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM attempts WHERE task_id = ? AND attempt = ?", (task_id, attempt)
        ).fetchone()
        return dict(row) if row else None

    def attempts_for(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM attempts WHERE task_id = ? ORDER BY attempt", (task_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ вердикты
    def save_verdict(self, verdict: dict[str, Any], *, conn: sqlite3.Connection | None = None) -> str:
        vid = str(verdict["verdict_id"])
        sql = """INSERT OR REPLACE INTO verdicts(verdict_id, task_id, attempt, snapshot_id, verdict, reviewer_id,
                                                reviewer_role, payload, created_at, stale)
                 VALUES(?,?,?,?,?,?,?,?,?,0)"""
        params = (
            vid,
            verdict["task_id"],
            int(verdict["attempt"]),
            verdict["snapshot_id"],
            verdict["verdict"],
            verdict.get("reviewer_id"),
            verdict.get("reviewer_role"),
            json.dumps(verdict, ensure_ascii=False, sort_keys=True),
            now(),
        )
        if conn is not None:
            conn.execute(sql, params)
            return vid
        with self.transaction() as c:
            c.execute(sql, params)
        return vid

    def get_verdict(self, verdict_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM verdicts WHERE verdict_id = ?", (verdict_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["payload"] = json.loads(d["payload"])
        return d

    def latest_verdict(self, task_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM verdicts WHERE task_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (task_id,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["payload"] = json.loads(d["payload"])
        return d

    def mark_verdicts_stale(self, task_id: str, keep_snapshot: str | None = None) -> None:
        with self.transaction() as c:
            if keep_snapshot:
                c.execute(
                    "UPDATE verdicts SET stale = 1 WHERE task_id = ? AND snapshot_id != ?", (task_id, keep_snapshot)
                )
            else:
                c.execute("UPDATE verdicts SET stale = 1 WHERE task_id = ?", (task_id,))

    # ------------------------------------------------------------------ находки
    def add_finding(
        self,
        *,
        finding_id: str,
        task_id: str,
        severity: str,
        statement: str,
        evidence: str = "",
        owner: str | None = None,
        track: str | None = None,
        attempt: int | None = None,
        status: str = "open",
    ) -> dict[str, Any]:
        ts = now()
        with self.transaction() as c:
            c.execute(
                """INSERT OR REPLACE INTO findings(finding_id, task_id, attempt, severity, statement, evidence,
                                                   owner, track, status, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (finding_id, task_id, attempt, severity, statement, evidence, owner, track, status, ts, ts),
            )
        return self.get_finding(finding_id)

    def get_finding(self, finding_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM findings WHERE finding_id = ?", (finding_id,)).fetchone()
        if row is None:
            raise StoreError(f"находка не найдена: {finding_id}")
        return dict(row)

    def findings_for(self, task_id: str, *, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM findings WHERE task_id = ? AND status = ? ORDER BY created_at", (task_id, status)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM findings WHERE task_id = ? ORDER BY created_at", (task_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def close_finding(self, finding_id: str, reason: str) -> dict[str, Any]:
        if not (reason or "").strip():
            raise StoreError("R-FIND-1: закрытие находки требует причины")
        with self.transaction() as c:
            cur = c.execute(
                "UPDATE findings SET status = 'closed', closed_reason = ?, updated_at = ? WHERE finding_id = ?",
                (reason, now(), finding_id),
            )
            if cur.rowcount == 0:
                raise StoreError(f"находка не найдена: {finding_id}")
        return self.get_finding(finding_id)

    # ------------------------------------------------------------------ журнал
    def log(self, task_id: str, message: str, *, attempt: int | None = None,
            snapshot_id: str | None = None) -> str:
        eid = new_id("log")
        with self.transaction() as c:
            c.execute(
                "INSERT INTO pipeline_log(entry_id, task_id, attempt, message, snapshot_id, created_at) VALUES(?,?,?,?,?,?)",
                (eid, task_id, attempt, message, snapshot_id, now()),
            )
        return eid

    def log_for(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM pipeline_log WHERE task_id = ? ORDER BY created_at, rowid", (task_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ коммиты
    def record_commit(self, *, commit_sha: str, task_id: str, snapshot_id: str,
                      verdict_id: str | None = None, message: str = "",
                      finalizer: str = "harness") -> dict[str, Any]:
        """Отображение «проверенный снимок → итоговый коммит».

        Вызывается доверенным финализатором после успешного git commit; хеш коммита
        нельзя записать внутрь самого коммита, поэтому связь хранится отдельно.
        """
        with self.transaction() as c:
            c.execute(
                """INSERT OR REPLACE INTO commits(commit_sha, task_id, snapshot_id, verdict_id, message,
                                                   finalizer, created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (commit_sha, task_id, snapshot_id, verdict_id, message, finalizer, now()),
            )
            c.execute("UPDATE tasks SET commit_sha = ? WHERE task_id = ?", (commit_sha, task_id))
        return self.get_commit(commit_sha)

    def commits_for(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM commits WHERE task_id = ? ORDER BY created_at", (task_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_commit(self, commit_sha: str) -> None:
        """Откат частичного финализатора: запись о коммите не должна пережить reset."""
        with self.transaction() as c:
            c.execute("DELETE FROM commits WHERE commit_sha = ?", (commit_sha,))
            c.execute("UPDATE tasks SET commit_sha = NULL WHERE commit_sha = ?", (commit_sha,))

    def get_commit(self, commit_sha: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM commits WHERE commit_sha = ?", (commit_sha,)).fetchone()
        if row is None:
            raise StoreError(f"коммит не найден: {commit_sha}")
        return dict(row)

    def commit_for_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM commits WHERE task_id = ? ORDER BY created_at DESC LIMIT 1", (task_id,)
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ аудит инструментов
    def audit_tool(self, *, role: str, tool: str, decision: str, rule: str = "",
                   task_id: str | None = None, path: str | None = None,
                   summary: str = "") -> str:
        aid = new_id("audit")
        with self.transaction() as c:
            c.execute(
                """INSERT INTO tools_audit(audit_id, task_id, role, tool, decision, rule, path, summary, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (aid, task_id, role, tool, decision, rule, path, summary[:400], now()),
            )
        return aid

    def audits_for(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM tools_audit WHERE task_id = ? ORDER BY created_at, rowid", (task_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ стоимость
    def add_cost(self, *, task_id: str, amount: float, unit: str = "cost_units",
                 kind: str = "model", attempt: int | None = None, note: str = "") -> dict[str, Any]:
        eid = new_id("cost")
        with self.transaction() as c:
            c.execute(
                """INSERT INTO costs(entry_id, task_id, attempt, kind, amount, unit, note, created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (eid, task_id, attempt, kind, float(amount), unit, note, now()),
            )
        return {"entry_id": eid, "task_id": task_id, "amount": float(amount), "unit": unit, "kind": kind}

    def costs_for(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM costs WHERE task_id = ? ORDER BY created_at, rowid", (task_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def cost_total(self, task_id: str, unit: str = "cost_units") -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM costs WHERE task_id = ? AND unit = ?",
            (task_id, unit),
        ).fetchone()
        return float(row["total"])

    def cost_report(self, unit: str = "cost_units") -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT task_id, COUNT(*) AS entries, COALESCE(SUM(amount), 0) AS total
               FROM costs WHERE unit = ? GROUP BY task_id ORDER BY total DESC""",
            (unit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ изменения правил
    def record_rule_change(self, *, rule_id: str, reason: str, rules_hash: str,
                           tests_run: int, tests_passed: int, author: str = "human") -> dict[str, Any]:
        if not (reason or "").strip():
            raise StoreError("R-HOOK-1: изменение правила требует причины")
        cid = new_id("rulechg")
        with self.transaction() as c:
            c.execute(
                """INSERT INTO rule_changes(change_id, rule_id, reason, rules_hash, tests_run,
                                            tests_passed, author, created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (cid, rule_id, reason, rules_hash, tests_run, tests_passed, author, now()),
            )
        return {
            "change_id": cid,
            "rule_id": rule_id,
            "reason": reason,
            "rules_hash": rules_hash,
            "tests_passed": tests_passed,
        }

    def latest_rule_change(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM rule_changes ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def rule_changes(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM rule_changes ORDER BY created_at, rowid").fetchall()
        return [dict(r) for r in rows]

