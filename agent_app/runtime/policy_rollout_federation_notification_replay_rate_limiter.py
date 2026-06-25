"""Replay rate limiter for DLQ replay burst control.

Phase 59 Task 735: Limits replay rate to prevent thundering herd.
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


class ReplayRateLimiterRecord(BaseModel):
    """Tracks rate limiter state for a key."""

    rate_limit_key: str = Field(..., description="Rate limit key (e.g., alert_id, target_id, 'global')")
    window_seconds: int = Field(..., description="Rate limit window in seconds")
    max_attempts: int = Field(..., description="Max attempts allowed in window")
    attempt_timestamps: list[datetime] = Field(default_factory=list, description="Timestamps of recent attempts")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReplayRateLimiterResult(BaseModel):
    """Result of a rate limit check."""

    allowed: bool
    remaining: int
    reset_at: datetime | None = None
    current_count: int = 0


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ReplayRateLimiterStore(Protocol):
    """Protocol for replay rate limiter storage."""

    def check_and_record(
        self,
        rate_limit_key: str,
        window_seconds: int,
        max_attempts: int,
        now: datetime | None = None,
    ) -> ReplayRateLimiterResult: ...

    def reset(self, rate_limit_key: str) -> bool: ...

    def get_record(self, rate_limit_key: str) -> ReplayRateLimiterRecord | None: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryReplayRateLimiterStore:
    """In-memory replay rate limiter store."""

    def __init__(self) -> None:
        self._records: dict[str, ReplayRateLimiterRecord] = {}

    def check_and_record(
        self,
        rate_limit_key: str,
        window_seconds: int,
        max_attempts: int,
        now: datetime | None = None,
    ) -> ReplayRateLimiterResult:
        if now is None:
            now = _now()
        window_start = now - timedelta(seconds=window_seconds)

        record = self._records.get(rate_limit_key)
        if record is None:
            record = ReplayRateLimiterRecord(
                rate_limit_key=rate_limit_key,
                window_seconds=window_seconds,
                max_attempts=max_attempts,
            )
            self._records[rate_limit_key] = record

        # Prune expired timestamps
        record.attempt_timestamps = [
            ts for ts in record.attempt_timestamps if ts > window_start
        ]
        current_count = len(record.attempt_timestamps)

        if current_count >= max_attempts:
            # Find oldest timestamp to calculate reset time
            oldest = min(record.attempt_timestamps)
            reset_at = oldest + timedelta(seconds=window_seconds)
            return ReplayRateLimiterResult(
                allowed=False,
                remaining=0,
                reset_at=reset_at,
                current_count=current_count,
            )

        # Allow and record
        record.attempt_timestamps.append(now)
        record.updated_at = now
        remaining = max_attempts - current_count - 1
        return ReplayRateLimiterResult(
            allowed=True,
            remaining=remaining,
            current_count=current_count + 1,
        )

    def reset(self, rate_limit_key: str) -> bool:
        if rate_limit_key in self._records:
            del self._records[rate_limit_key]
            return True
        return False

    def get_record(self, rate_limit_key: str) -> ReplayRateLimiterRecord | None:
        return self._records.get(rate_limit_key)


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteReplayRateLimiterStore:
    """SQLite-backed replay rate limiter store."""

    def __init__(self, db_path: str = ".agent_app/replay_rate_limiter.db") -> None:
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
            CREATE TABLE IF NOT EXISTS replay_rate_limiter (
                rate_limit_key TEXT PRIMARY KEY,
                window_seconds INTEGER NOT NULL,
                max_attempts INTEGER NOT NULL,
                attempt_timestamps TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        self._conn.commit()

    def check_and_record(
        self,
        rate_limit_key: str,
        window_seconds: int,
        max_attempts: int,
        now: datetime | None = None,
    ) -> ReplayRateLimiterResult:
        if now is None:
            now = _now()
        now_iso = now.isoformat()
        window_start = now - timedelta(seconds=window_seconds)
        window_start_iso = window_start.isoformat()

        row = self._conn.execute(
            "SELECT * FROM replay_rate_limiter WHERE rate_limit_key=?", (rate_limit_key,)
        ).fetchone()

        if row is not None:
            # Update window/max if changed
            if row["window_seconds"] != window_seconds or row["max_attempts"] != max_attempts:
                self._conn.execute(
                    "UPDATE replay_rate_limiter SET window_seconds=?, max_attempts=?, updated_at=? WHERE rate_limit_key=?",
                    (window_seconds, max_attempts, now_iso, rate_limit_key),
                )
                self._conn.commit()
            # Parse timestamps
            timestamps = [
                datetime.fromisoformat(ts)
                for ts in __import__("json").loads(row["attempt_timestamps"])
                if ts
            ]
        else:
            timestamps = []
            self._conn.execute(
                """INSERT INTO replay_rate_limiter
                   (rate_limit_key, window_seconds, max_attempts, attempt_timestamps, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (rate_limit_key, window_seconds, max_attempts, "[]", now_iso, now_iso),
            )
            self._conn.commit()

        # Prune expired timestamps
        active = [ts for ts in timestamps if ts > window_start]
        current_count = len(active)

        if current_count >= max_attempts:
            oldest = min(active)
            reset_at = oldest + timedelta(seconds=window_seconds)
            return ReplayRateLimiterResult(
                allowed=False,
                remaining=0,
                reset_at=reset_at,
                current_count=current_count,
            )

        # Allow and record
        active.append(now)
        timestamps_json = __import__("json").dumps([ts.isoformat() for ts in active])
        self._conn.execute(
            "UPDATE replay_rate_limiter SET attempt_timestamps=?, updated_at=? WHERE rate_limit_key=?",
            (timestamps_json, now_iso, rate_limit_key),
        )
        self._conn.commit()
        remaining = max_attempts - current_count - 1
        return ReplayRateLimiterResult(
            allowed=True,
            remaining=remaining,
            current_count=current_count + 1,
        )

    def reset(self, rate_limit_key: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM replay_rate_limiter WHERE rate_limit_key=?", (rate_limit_key,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def get_record(self, rate_limit_key: str) -> ReplayRateLimiterRecord | None:
        row = self._conn.execute(
            "SELECT * FROM replay_rate_limiter WHERE rate_limit_key=?", (rate_limit_key,)
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["attempt_timestamps"] = [
            datetime.fromisoformat(ts) for ts in __import__("json").loads(data["attempt_timestamps"])
        ]
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return ReplayRateLimiterRecord(**data)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_replay_rate_limiter_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> ReplayRateLimiterStore:
    """Factory for creating replay rate limiter store instances."""
    if store_type == "memory":
        return InMemoryReplayRateLimiterStore()
    if store_type == "sqlite":
        return SQLiteReplayRateLimiterStore(
            db_path=db_path or ".agent_app/replay_rate_limiter.db"
        )
    raise ValueError(
        f"Unknown replay rate limiter store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )
