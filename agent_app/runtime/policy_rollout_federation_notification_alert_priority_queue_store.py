"""Alert priority queue store — Protocol, InMemory, SQLite, factory.

Phase 56 Task 730: Persistent priority queue store for alert delivery ordering.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator

from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryChannelType,
    AlertDeliveryStatus,
    severity_to_priority,
)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class AlertPriorityQueueItem(BaseModel):
    """A single item in the alert priority queue.

    Lightweight view focused on queue ordering and dequeue semantics.
    """

    attempt_id: str = Field(..., description="Unique attempt identifier")
    alert_id: str = Field(..., description="Alert being delivered")
    target_id: str = Field(..., description="Target being delivered to")
    channel_type: AlertDeliveryChannelType = Field(..., description="Delivery channel")
    status: AlertDeliveryStatus = Field(..., description="Current queue status")
    priority: int = Field(default=0, description="Priority (higher = more urgent)")
    created_at: datetime = Field(..., description="Timezone-aware creation timestamp")
    next_retry_at: datetime | None = Field(default=None, description="When to retry")
    attempt: int = Field(default=1, description="Attempt number (1-based)")
    metadata_json: str = Field(default="{}", description="Serialized metadata")

    @field_validator("created_at")
    @classmethod
    def _validate_created_at_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("created_at must be timezone-aware")
        return v

    @field_validator("attempt_id")
    @classmethod
    def _validate_attempt_id_prefix(cls, v: str) -> str:
        if not v.startswith("nda_"):
            raise ValueError(f"attempt_id must start with 'nda_', got '{v}'")
        return v


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class AlertPriorityQueueStore(Protocol):
    """Protocol for persisting alert priority queue entries."""

    async def enqueue(self, item: AlertPriorityQueueItem) -> AlertPriorityQueueItem: ...
    async def dequeue(
        self,
        status: str | None = None,
        limit: int = 100,
    ) -> list[AlertPriorityQueueItem]: ...
    async def count(self, status: str | None = None) -> int: ...
    async def count_by_priority(self, status: str | None = None) -> dict[int, int]: ...
    async def update_status(
        self, attempt_id: str, status: str
    ) -> AlertPriorityQueueItem | None: ...
    async def remove(self, attempt_id: str) -> bool: ...


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryAlertPriorityQueueStore:
    """In-memory alert priority queue store."""

    def __init__(self) -> None:
        self._items: dict[str, AlertPriorityQueueItem] = {}

    async def enqueue(self, item: AlertPriorityQueueItem) -> AlertPriorityQueueItem:
        self._items[item.attempt_id] = item
        return item

    async def dequeue(
        self,
        status: str | None = None,
        limit: int = 100,
    ) -> list[AlertPriorityQueueItem]:
        items = list(self._items.values())
        if status is not None:
            items = [i for i in items if i.status == status]
        items.sort(key=lambda i: (i.priority, i.created_at), reverse=True)
        return items[:limit]

    async def count(self, status: str | None = None) -> int:
        items = list(self._items.values())
        if status is not None:
            items = [i for i in items if i.status == status]
        return len(items)

    async def count_by_priority(self, status: str | None = None) -> dict[int, int]:
        items = list(self._items.values())
        if status is not None:
            items = [i for i in items if i.status == status]
        counts: dict[int, int] = {}
        for item in items:
            counts[item.priority] = counts.get(item.priority, 0) + 1
        return counts

    async def update_status(
        self, attempt_id: str, status: str
    ) -> AlertPriorityQueueItem | None:
        item = self._items.get(attempt_id)
        if item is None:
            return None
        item.status = status  # type: ignore[assignment]
        return item

    async def remove(self, attempt_id: str) -> bool:
        if attempt_id not in self._items:
            return False
        del self._items[attempt_id]
        return True


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteAlertPriorityQueueStore:
    """SQLite-backed alert priority queue store."""

    def __init__(self, db_path: str = ".agent_app/alert_priority_queue.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS alert_priority_queue (
                attempt_id TEXT PRIMARY KEY,
                alert_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                channel_type TEXT NOT NULL,
                status TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                next_retry_at TEXT,
                attempt INTEGER NOT NULL DEFAULT 1,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_apq_status ON alert_priority_queue(status);
            CREATE INDEX IF NOT EXISTS idx_apq_priority ON alert_priority_queue(priority DESC);
            CREATE INDEX IF NOT EXISTS idx_apq_created ON alert_priority_queue(created_at DESC);
        """)
        self._conn.commit()

    async def enqueue(self, item: AlertPriorityQueueItem) -> AlertPriorityQueueItem:
        self._conn.execute(
            """INSERT OR REPLACE INTO alert_priority_queue
               (attempt_id, alert_id, target_id, channel_type, status,
                priority, created_at, next_retry_at, attempt, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.attempt_id,
                item.alert_id,
                item.target_id,
                item.channel_type.value,
                item.status.value,
                item.priority,
                item.created_at.isoformat(),
                item.next_retry_at.isoformat() if item.next_retry_at else None,
                item.attempt,
                item.metadata_json,
            ),
        )
        self._conn.commit()
        return item

    async def dequeue(
        self,
        status: str | None = None,
        limit: int = 100,
    ) -> list[AlertPriorityQueueItem]:
        conditions: list[str] = []
        params: list[str | int] = []

        if status is not None:
            conditions.append("status=?")
            params.append(status)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        params.append(limit)
        rows = self._conn.execute(
            f"SELECT * FROM alert_priority_queue {where} "
            "ORDER BY priority DESC, created_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [self._row_to_item(row) for row in rows]

    async def count(self, status: str | None = None) -> int:
        if status is not None:
            row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM alert_priority_queue WHERE status=?",
                (status,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM alert_priority_queue"
            ).fetchone()
        return row["cnt"] if row else 0

    async def count_by_priority(self, status: str | None = None) -> dict[int, int]:
        if status is not None:
            rows = self._conn.execute(
                "SELECT priority, COUNT(*) AS cnt FROM alert_priority_queue "
                "WHERE status=? GROUP BY priority",
                (status,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT priority, COUNT(*) AS cnt FROM alert_priority_queue "
                "GROUP BY priority"
            ).fetchall()
        return {row["priority"]: row["cnt"] for row in rows}

    async def update_status(
        self, attempt_id: str, status: str
    ) -> AlertPriorityQueueItem | None:
        row = self._conn.execute(
            "SELECT * FROM alert_priority_queue WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
        if row is None:
            return None
        item = self._row_to_item(row)
        item.status = status  # type: ignore[assignment]
        self._conn.execute(
            "UPDATE alert_priority_queue SET status=? WHERE attempt_id=?",
            (status, attempt_id),
        )
        self._conn.commit()
        return item

    async def remove(self, attempt_id: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM alert_priority_queue WHERE attempt_id=?",
            (attempt_id,),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def _row_to_item(self, row: sqlite3.Row) -> AlertPriorityQueueItem:
        import json

        data = dict(row)
        data["channel_type"] = AlertDeliveryChannelType(data["channel_type"])
        data["status"] = AlertDeliveryStatus(data["status"])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        if data["next_retry_at"] is not None:
            data["next_retry_at"] = datetime.fromisoformat(data["next_retry_at"])
        return AlertPriorityQueueItem(**data)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_alert_priority_queue_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> AlertPriorityQueueStore:
    """Factory for creating alert priority queue store instances."""
    if store_type == "memory":
        return InMemoryAlertPriorityQueueStore()
    if store_type == "sqlite":
        return SQLiteAlertPriorityQueueStore(
            db_path=db_path or ".agent_app/alert_priority_queue.db"
        )
    raise ValueError(
        f"Unknown priority queue store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )
