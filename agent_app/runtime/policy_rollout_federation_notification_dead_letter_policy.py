"""Dead letter policy for alert priority queue.

Phase 59 Task 736: Evaluates when items should move to dead letter queue
based on retry count and policy configuration.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from agent_app.runtime.policy_rollout_federation_notification_alert_priority_queue_store import (
    AlertPriorityQueueItem,
    AlertPriorityQueueItemStatus,
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class DeadLetterPolicyConfig(BaseModel):
    """Configuration for dead letter policy."""

    max_retries: int = Field(default=5, description="Max retry attempts before dead letter")
    dead_letter_status: str = Field(
        default=AlertPriorityQueueItemStatus.FAILED.value,
        description="Status to set when moving to dead letter",
    )


class DeadLetterRecord(BaseModel):
    """Record of an item moved to dead letter queue."""

    attempt_id: str = Field(..., description="Original attempt ID")
    alert_id: str = Field(..., description="Alert ID")
    target_id: str = Field(..., description="Target ID")
    reason: str = Field(..., description="Reason for dead letter (e.g., 'max_retries_exceeded')")
    attempt_count: int = Field(..., description="Total attempt count when dead-lettered")
    metadata_json: str = Field(default="{}", description="Serialized metadata from original item")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DeadLetterPolicyResult(BaseModel):
    """Result of dead letter policy evaluation."""

    is_dead_letter: bool
    reason: str | None = None
    record: DeadLetterRecord | None = None


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class DeadLetterPolicyStore(Protocol):
    """Protocol for dead letter policy storage."""

    def evaluate(self, item: AlertPriorityQueueItem) -> DeadLetterPolicyResult: ...

    def record_dead_letter(self, record: DeadLetterRecord) -> DeadLetterRecord: ...

    def get_record(self, attempt_id: str) -> DeadLetterRecord | None: ...

    def list_records(
        self, alert_id: str | None = None, target_id: str | None = None
    ) -> list[DeadLetterRecord]: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _extract_metadata(item: AlertPriorityQueueItem) -> str:
    """Extract metadata from item, ensuring it's valid JSON."""
    if item.metadata_json:
        try:
            __import__("json").loads(item.metadata_json)
            return item.metadata_json
        except (ValueError, TypeError):
            pass
    return "{}"


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryDeadLetterPolicyStore:
    """In-memory dead letter policy store."""

    def __init__(self, config: DeadLetterPolicyConfig | None = None) -> None:
        self._config = config or DeadLetterPolicyConfig()
        self._records: dict[str, DeadLetterRecord] = {}
        self._index_alert: dict[str, set[str]] = {}
        self._index_target: dict[str, set[str]] = {}

    @property
    def config(self) -> DeadLetterPolicyConfig:
        return self._config

    def evaluate(self, item: AlertPriorityQueueItem) -> DeadLetterPolicyResult:
        if item.attempt > self._config.max_retries:
            record = DeadLetterRecord(
                attempt_id=item.attempt_id,
                alert_id=item.alert_id,
                target_id=item.target_id,
                reason="max_retries_exceeded",
                attempt_count=item.attempt,
                metadata_json=_extract_metadata(item),
            )
            return DeadLetterPolicyResult(
                is_dead_letter=True,
                reason="max_retries_exceeded",
                record=record,
            )
        return DeadLetterPolicyResult(is_dead_letter=False)

    def record_dead_letter(self, record: DeadLetterRecord) -> DeadLetterRecord:
        self._records[record.attempt_id] = record
        self._index_alert.setdefault(record.alert_id, set()).add(record.attempt_id)
        self._index_target.setdefault(record.target_id, set()).add(record.attempt_id)
        return record

    def get_record(self, attempt_id: str) -> DeadLetterRecord | None:
        return self._records.get(attempt_id)

    def list_records(
        self, alert_id: str | None = None, target_id: str | None = None
    ) -> list[DeadLetterRecord]:
        if alert_id is not None:
            keys = self._index_alert.get(alert_id, set())
            return [self._records[k] for k in keys if k in self._records]
        if target_id is not None:
            keys = self._index_target.get(target_id, set())
            return [self._records[k] for k in keys if k in self._records]
        return list(self._records.values())


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteDeadLetterPolicyStore:
    """SQLite-backed dead letter policy store."""

    def __init__(
        self,
        db_path: str = ".agent_app/dead_letter_policy.db",
        config: DeadLetterPolicyConfig | None = None,
    ) -> None:
        self._config = config or DeadLetterPolicyConfig()
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

    @property
    def config(self) -> DeadLetterPolicyConfig:
        return self._config

    def _init_db(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS dead_letter_records (
                attempt_id TEXT PRIMARY KEY,
                alert_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                attempt_count INTEGER NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_dl_alert_id
            ON dead_letter_records (alert_id)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_dl_target_id
            ON dead_letter_records (target_id)
        """)
        self._conn.commit()

    def evaluate(self, item: AlertPriorityQueueItem) -> DeadLetterPolicyResult:
        if item.attempt > self._config.max_retries:
            record = DeadLetterRecord(
                attempt_id=item.attempt_id,
                alert_id=item.alert_id,
                target_id=item.target_id,
                reason="max_retries_exceeded",
                attempt_count=item.attempt,
                metadata_json=_extract_metadata(item),
            )
            return DeadLetterPolicyResult(
                is_dead_letter=True,
                reason="max_retries_exceeded",
                record=record,
            )
        return DeadLetterPolicyResult(is_dead_letter=False)

    def record_dead_letter(self, record: DeadLetterRecord) -> DeadLetterRecord:
        self._conn.execute(
            """INSERT OR REPLACE INTO dead_letter_records
               (attempt_id, alert_id, target_id, reason, attempt_count, metadata_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                record.attempt_id,
                record.alert_id,
                record.target_id,
                record.reason,
                record.attempt_count,
                record.metadata_json,
                record.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return record

    def get_record(self, attempt_id: str) -> DeadLetterRecord | None:
        row = self._conn.execute(
            "SELECT * FROM dead_letter_records WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def list_records(
        self, alert_id: str | None = None, target_id: str | None = None
    ) -> list[DeadLetterRecord]:
        if alert_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM dead_letter_records WHERE alert_id=?", (alert_id,)
            ).fetchall()
        elif target_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM dead_letter_records WHERE target_id=?", (target_id,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM dead_letter_records").fetchall()
        return [self._row_to_record(row) for row in rows]

    def _row_to_record(self, row: sqlite3.Row) -> DeadLetterRecord:
        data = dict(row)
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        return DeadLetterRecord(**data)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_dead_letter_policy_store(
    store_type: str = "memory",
    db_path: str | None = None,
    max_retries: int = 5,
) -> DeadLetterPolicyStore:
    """Factory for creating dead letter policy store instances."""
    config = DeadLetterPolicyConfig(max_retries=max_retries)
    if store_type == "memory":
        return InMemoryDeadLetterPolicyStore(config=config)
    if store_type == "sqlite":
        return SQLiteDeadLetterPolicyStore(
            db_path=db_path or ".agent_app/dead_letter_policy.db",
            config=config,
        )
    raise ValueError(
        f"Unknown dead letter policy store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )
