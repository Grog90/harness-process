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

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS tasks (
    task_id           TEXT PRIMARY KEY,
    lane              TEXT NOT NULL DEFAULT 'main',
    state             TEXT NOT NULL,
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL,
    spec_version      TEXT,
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
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),)
        )

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
    ) -> dict[str, Any]:
        ts = now()
        with self.transaction() as c:
            c.execute(
                """INSERT INTO tasks(task_id, lane, state, created_at, updated_at, spec_version,
                                     base_commit, policy_version, max_attempts)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (task_id, lane, "CLAIMED", ts, ts, spec_version, base_commit, policy_version, max_attempts),
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
