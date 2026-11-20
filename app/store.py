"""SQLite persistence.

SQLite keeps the demo zero-setup. Every query goes through this module, so a
Postgres implementation only has to provide the same methods (tracked in the
roadmap). Timestamps are stored as UTC ISO-8601 strings.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    name            TEXT,
    phone           TEXT,
    email           TEXT,
    message         TEXT NOT NULL DEFAULT '',
    urgency         TEXT NOT NULL,
    urgency_reason  TEXT NOT NULL,
    received_at     TEXT NOT NULL,
    auto_replied_at TEXT,
    human_touched_at TEXT,
    escalated_at    TEXT,
    crm_id          TEXT,
    duplicate_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(phone, received_at);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email, received_at);
CREATE INDEX IF NOT EXISTS idx_leads_open ON leads(human_touched_at, escalated_at);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key        TEXT PRIMARY KEY,
    lead_id    INTEGER NOT NULL REFERENCES leads(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id    INTEGER REFERENCES leads(id),
    kind       TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_lead ON events(lead_id);

CREATE TABLE IF NOT EXISTS dead_letters (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id    INTEGER REFERENCES leads(id),
    channel    TEXT NOT NULL,
    payload    TEXT NOT NULL,
    error      TEXT NOT NULL,
    attempts   INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


@dataclass
class Lead:
    id: int
    source: str
    name: str | None
    phone: str | None
    email: str | None
    message: str
    urgency: str
    urgency_reason: str
    received_at: datetime
    auto_replied_at: datetime | None
    human_touched_at: datetime | None
    escalated_at: datetime | None
    crm_id: str | None
    duplicate_count: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Lead:
        data = dict(row)
        for key in ("received_at", "auto_replied_at", "human_touched_at", "escalated_at"):
            data[key] = parse(data[key])
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in self.__dict__.items():
            out[key] = iso(value) if isinstance(value, datetime) else value
        return out


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # One shared connection guarded by a lock. Fine for a single-process
        # service; the Postgres backend will use a pool instead.
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.RLock()
        self._conn.executescript(SCHEMA)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def close(self) -> None:
        self._conn.close()

    # --- idempotency -----------------------------------------------------

    def lead_for_key(self, conn: sqlite3.Connection, key: str) -> int | None:
        row = conn.execute("SELECT lead_id FROM idempotency_keys WHERE key = ?", (key,)).fetchone()
        return row["lead_id"] if row else None

    def remember_key(self, conn: sqlite3.Connection, key: str, lead_id: int, at: datetime) -> None:
        conn.execute(
            "INSERT INTO idempotency_keys(key, lead_id, created_at) VALUES (?, ?, ?)",
            (key, lead_id, iso(at)),
        )

    # --- leads -----------------------------------------------------------

    def find_recent_match(
        self, conn: sqlite3.Connection, *, phone: str | None, email: str | None, since: datetime
    ) -> Lead | None:
        clauses, params = [], []
        if phone:
            clauses.append("phone = ?")
            params.append(phone)
        if email:
            clauses.append("email = ?")
            params.append(email)
        if not clauses:
            return None
        row = conn.execute(
            f"SELECT * FROM leads WHERE ({' OR '.join(clauses)}) AND received_at >= ? "
            "ORDER BY received_at DESC LIMIT 1",
            (*params, iso(since)),
        ).fetchone()
        return Lead.from_row(row) if row else None

    def insert_lead(self, conn: sqlite3.Connection, **fields: Any) -> int:
        fields["received_at"] = iso(fields["received_at"])
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        cur = conn.execute(f"INSERT INTO leads({cols}) VALUES ({marks})", tuple(fields.values()))
        return int(cur.lastrowid)

    def bump_duplicate(self, conn: sqlite3.Connection, lead_id: int) -> None:
        conn.execute("UPDATE leads SET duplicate_count = duplicate_count + 1 WHERE id = ?", (lead_id,))

    def get(self, lead_id: int) -> Lead | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return Lead.from_row(row) if row else None

    def recent(self, limit: int = 50) -> list[Lead]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM leads ORDER BY received_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Lead.from_row(r) for r in rows]

    def all_since(self, since: datetime) -> list[Lead]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM leads WHERE received_at >= ? ORDER BY received_at", (iso(since),)
            ).fetchall()
        return [Lead.from_row(r) for r in rows]

    def set_timestamp(self, lead_id: int, column: str, at: datetime, *, only_if_null: bool = True) -> bool:
        if column not in {"auto_replied_at", "human_touched_at", "escalated_at"}:
            raise ValueError(column)
        guard = f" AND {column} IS NULL" if only_if_null else ""
        with self._lock:
            cur = self._conn.execute(f"UPDATE leads SET {column} = ? WHERE id = ?{guard}", (iso(at), lead_id))
        return cur.rowcount == 1

    def set_crm_id(self, lead_id: int, crm_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE leads SET crm_id = ? WHERE id = ?", (crm_id, lead_id))

    def claim_for_escalation(self, cutoff: datetime, at: datetime) -> list[Lead]:
        """Atomically mark and return leads that waited past the cutoff.

        The UPDATE ... RETURNING makes escalation idempotent: two workers
        running at once cannot page the owner twice for the same lead.
        """
        with self.transaction() as conn:
            rows = conn.execute(
                "UPDATE leads SET escalated_at = ? "
                "WHERE human_touched_at IS NULL AND escalated_at IS NULL AND received_at <= ? "
                "RETURNING *",
                (iso(at), iso(cutoff)),
            ).fetchall()
        return [Lead.from_row(r) for r in rows]

    # --- audit trail -----------------------------------------------------

    def log(self, lead_id: int | None, kind: str, at: datetime | None = None, **detail: Any) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events(lead_id, kind, detail, created_at) VALUES (?, ?, ?, ?)",
                (lead_id, kind, json.dumps(detail, default=str), iso(at or utcnow())),
            )

    def events(self, lead_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind, detail, created_at FROM events WHERE lead_id = ? ORDER BY id", (lead_id,)
            ).fetchall()
        return [{"kind": r["kind"], "detail": json.loads(r["detail"]), "at": r["created_at"]} for r in rows]

    def dead_letter(
        self, lead_id: int | None, channel: str, payload: dict[str, Any], error: str, attempts: int
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO dead_letters(lead_id, channel, payload, error, attempts, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (lead_id, channel, json.dumps(payload), error, attempts, iso(utcnow())),
            )

    def dead_letters(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM dead_letters ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]

    def reset(self) -> None:
        with self.transaction() as conn:
            for table in ("dead_letters", "events", "idempotency_keys", "leads"):
                conn.execute(f"DELETE FROM {table}")


def window_start(now: datetime, hours: int) -> datetime:
    return now - timedelta(hours=hours)
