"""Replay idempotency store for DLQ replay de-duplication.

Phase 59 Task 734: Prevents duplicate DLQ replay attempts.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ReplayIdempotencyRecord(BaseModel):
    """Tracks a replay attempt for idempotency."""

    idempotency_key: str = Field(..., description="Unique idempotency key")
    original_attempt_id: str = Field(..., description="Original DLQ attempt ID")
    replay_type: str = Field(..., description="Replay type: 'single' or 'batch'")
    status: str = Field(..., description="Status: 'started', 'completed', 'failed'")
    new_attempt_id: str | None = Field(default=None, description="New attempt ID on completion")
    error_message: str | None = Field(default=None, description="Error on failure")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = Field(default=None)
    expires_at: datetime | None = Field(default=None)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ReplayIdempotencyStore(Protocol):
    """Protocol for replay idempotency storage."""

    def begin(self, record: ReplayIdempotencyRecord) -> ReplayIdempotencyRecord: ...
    def complete(self, idempotency_key: str, new_attempt_id: str) -> ReplayIdempotencyRecord | None: ...
    def fail(self, idempotency_key: str, error_message: str) -> ReplayIdempotencyRecord | None: ...
    def get(self, idempotency_key: str) -> ReplayIdempotencyRecord | None: ...
    def prune_expired(self, now: datetime | None = None) -> int: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _redact_error(error: str | None) -> str | None:
    """Redact sensitive patterns from error messages."""
    if not error:
        return error
    import re
    _patterns = ["token=", "secret=", "api_key=", "password="]
    redacted = error
    for pattern in _patterns:
        if pattern.lower() in redacted.lower():
            regex = re.escape(pattern) + r'[^\s,;}]*'
            redacted = re.sub(regex, f'{pattern}[REDACTED]', redacted, flags=re.IGNORECASE)
    return redacted


def _make_key(original_attempt_id: str, target_id: str, alert_id: str) -> str:
    """Build default idempotency key."""
    return f"replay:{original_attempt_id}:{target_id}:{alert_id}"


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryReplayIdempotencyStore:
    """In-memory replay idempotency store."""

    def __init__(self) -> None:
        self._records: dict[str, ReplayIdempotencyRecord] = {}

    def begin(self, record: ReplayIdempotencyRecord) -> ReplayIdempotencyRecord:
        if record.idempotency_key in self._records:
            existing = self._records[record.idempotency_key]
            if existing.status == "completed":
                return existing  # Return existing completed record
            if existing.status == "started" and existing.expires_at and existing.expires_at > _now():
                return existing  # In-progress, return existing
        self._records[record.idempotency_key] = record
        return record

    def complete(self, idempotency_key: str, new_attempt_id: str) -> ReplayIdempotencyRecord | None:
        record = self._records.get(idempotency_key)
        if record is None:
            return None
        record.status = "completed"
        record.new_attempt_id = new_attempt_id
        record.completed_at = _now()
        return record

    def fail(self, idempotency_key: str, error_message: str) -> ReplayIdempotencyRecord | None:
        record = self._records.get(idempotency_key)
        if record is None:
            return None
        record.status = "failed"
        record.error_message = _redact_error(error_message)
        record.completed_at = _now()
        return record

    def get(self, idempotency_key: str) -> ReplayIdempotencyRecord | None:
        record = self._records.get(idempotency_key)
        if record is None:
            return None
        # Check if expired
        if record.expires_at and record.expires_at < _now():
            del self._records[idempotency_key]
            return None
        return record

    def prune_expired(self, now: datetime | None = None) -> int:
        if now is None:
            now = _now()
        expired = [k for k, v in self._records.items() if v.expires_at and v.expires_at < now]
        for k in expired:
            del self._records[k]
        return len(expired)


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteReplayIdempotencyStore:
    """SQLite-backed replay idempotency store."""

    def __init__(self, db_path: str = ".agent_app/replay_idempotency.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS replay_idempotency (
                idempotency_key TEXT PRIMARY KEY,
                original_attempt_id TEXT NOT NULL,
                replay_type TEXT NOT NULL,
                status TEXT NOT NULL,
                new_attempt_id TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                expires_at TEXT
            )
        """)
        self._conn.commit()

    def begin(self, record: ReplayIdempotencyRecord) -> ReplayIdempotencyRecord:
        existing = self._conn.execute(
            "SELECT * FROM replay_idempotency WHERE idempotency_key=?", (record.idempotency_key,)
        ).fetchone()
        if existing is not None:
            if existing["status"] == "completed":
                return self._row_to_record(existing)
            if existing["status"] == "started" and existing["expires_at"] and datetime.fromisoformat(existing["expires_at"]) > _now():
                return self._row_to_record(existing)
        self._conn.execute(
            """INSERT INTO replay_idempotency
               (idempotency_key, original_attempt_id, replay_type, status, new_attempt_id,
                error_message, created_at, completed_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.idempotency_key,
                record.original_attempt_id,
                record.replay_type,
                record.status,
                record.new_attempt_id,
                record.error_message,
                record.created_at.isoformat(),
                record.completed_at.isoformat() if record.completed_at else None,
                record.expires_at.isoformat() if record.expires_at else None,
            ),
        )
        self._conn.commit()
        return record

    def complete(self, idempotency_key: str, new_attempt_id: str) -> ReplayIdempotencyRecord | None:
        now_iso = _now().isoformat()
        self._conn.execute(
            "UPDATE replay_idempotency SET status=?, new_attempt_id=?, completed_at=? WHERE idempotency_key=?",
            ("completed", new_attempt_id, now_iso, idempotency_key),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM replay_idempotency WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        return self._row_to_record(row) if row else None

    def fail(self, idempotency_key: str, error_message: str) -> ReplayIdempotencyRecord | None:
        now_iso = _now().isoformat()
        self._conn.execute(
            "UPDATE replay_idempotency SET status=?, error_message=?, completed_at=? WHERE idempotency_key=?",
            ("failed", _redact_error(error_message), now_iso, idempotency_key),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM replay_idempotency WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        return self._row_to_record(row) if row else None

    def get(self, idempotency_key: str) -> ReplayIdempotencyRecord | None:
        row = self._conn.execute(
            "SELECT * FROM replay_idempotency WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        if row is None:
            return None
        record = self._row_to_record(row)
        if record.expires_at and record.expires_at < _now():
            self._conn.execute("DELETE FROM replay_idempotency WHERE idempotency_key=?", (idempotency_key,))
            self._conn.commit()
            return None
        return record

    def prune_expired(self, now: datetime | None = None) -> int:
        if now is None:
            now = _now()
        now_iso = now.isoformat()
        cursor = self._conn.execute(
            "DELETE FROM replay_idempotency WHERE expires_at IS NOT NULL AND expires_at < ?",
            (now_iso,),
        )
        self._conn.commit()
        return cursor.rowcount

    def _row_to_record(self, row: sqlite3.Row) -> ReplayIdempotencyRecord:
        data = dict(row)
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        if data.get("completed_at"):
            data["completed_at"] = datetime.fromisoformat(data["completed_at"])
        if data.get("expires_at"):
            data["expires_at"] = datetime.fromisoformat(data["expires_at"])
        return ReplayIdempotencyRecord(**data)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_replay_idempotency_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> ReplayIdempotencyStore:
    """Factory for creating replay idempotency store instances."""
    if store_type == "memory":
        return InMemoryReplayIdempotencyStore()
    if store_type == "sqlite":
        return SQLiteReplayIdempotencyStore(
            db_path=db_path or ".agent_app/replay_idempotency.db"
        )
    raise ValueError(
        f"Unknown replay idempotency store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )
