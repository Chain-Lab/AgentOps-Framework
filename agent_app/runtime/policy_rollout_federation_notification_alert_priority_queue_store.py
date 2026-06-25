"""Alert priority queue store — Protocol, InMemory, SQLite, factory.

Phase 56 Task 730: Persistent priority queue store for alert delivery ordering.
Phase 57 Task 2: Atomic claim / acknowledge / fail / requeue / lease expiry.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator

from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryChannelType,
    AlertDeliveryStatus,
    severity_to_priority,
)


# ---------------------------------------------------------------------------
# Queue item status
# ---------------------------------------------------------------------------


class AlertPriorityQueueItemStatus(StrEnum):
    """Extended statuses for priority queue item lifecycle."""

    QUEUED = "queued"
    CLAIMED = "claimed"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REQUEUED = "requeued"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    @classmethod
    def _missing_(cls, value: str) -> "AlertPriorityQueueItemStatus | None":
        """Allow backward-compatible lookup from raw string values."""
        # Map legacy AlertDeliveryStatus values to queue statuses
        _legacy_map = {
            "retry_scheduled": cls.QUEUED,
            "delivered": cls.COMPLETED,
            "pending": cls.QUEUED,
            "dlq": cls.FAILED,
            "suppressed": cls.CANCELLED,
        }
        return _legacy_map.get(value)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class AlertPriorityQueueItem(BaseModel):
    """A single item in the alert priority queue.

    Phase 57: Extended with claim/lease lifecycle fields.
    """

    attempt_id: str = Field(..., description="Unique attempt identifier")
    alert_id: str = Field(..., description="Alert being delivered")
    target_id: str = Field(..., description="Target being delivered to")
    channel_type: AlertDeliveryChannelType = Field(..., description="Delivery channel")
    # Phase 57: accept both legacy AlertDeliveryStatus and new queue statuses
    status: str = Field(..., description="Current queue status")
    priority: int = Field(default=0, description="Priority (higher = more urgent)")
    created_at: datetime = Field(..., description="Timezone-aware creation timestamp")
    next_retry_at: datetime | None = Field(default=None, description="When to retry")
    attempt: int = Field(default=1, description="Attempt number (1-based)")
    # Phase 57: claim / lease fields
    claimed_by: str | None = Field(default=None, description="Worker that claimed this item")
    claimed_at: datetime | None = Field(default=None, description="When item was claimed")
    lease_expires_at: datetime | None = Field(default=None, description="When the claim lease expires")
    available_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="When item becomes eligible for claim")
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

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str) -> str:
        """Accept both legacy and Phase 57 status values."""
        valid_legacy = {s.value for s in AlertDeliveryStatus}
        valid_queue = {s.value for s in AlertPriorityQueueItemStatus}
        if v not in valid_legacy and v not in valid_queue:
            raise ValueError(
                f"Invalid status '{v}'. "
                f"Valid: {sorted(valid_legacy | valid_queue)}"
            )
        return v

    def is_claimable(self, now: datetime | None = None) -> bool:
        """Whether this item can be claimed by a worker."""
        if now is None:
            now = datetime.now(timezone.utc)
        status = AlertPriorityQueueItemStatus(self.status)
        return (
            status in (AlertPriorityQueueItemStatus.QUEUED, AlertPriorityQueueItemStatus.REQUEUED)
            and self.available_at <= now
        )

    def is_lease_expired(self, now: datetime | None = None) -> bool:
        """Whether the claim lease has expired."""
        if self.lease_expires_at is None:
            return False
        if now is None:
            now = datetime.now(timezone.utc)
        return self.lease_expires_at < now


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class AlertPriorityQueueStore(Protocol):
    """Protocol for persisting alert priority queue entries.

    Phase 57: Extended with atomic claim/ack/fail/requeue/lease lifecycle.
    """

    # --- Phase 56: base CRUD ---

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

    # --- Phase 57: atomic lifecycle ---

    async def claim_next(
        self,
        now: datetime | None = None,
        limit: int = 100,
        worker_id: str | None = None,
        lease_seconds: int = 300,
    ) -> list[AlertPriorityQueueItem]: ...
    async def acknowledge(
        self,
        queue_id: str,
        worker_id: str | None = None,
    ) -> AlertPriorityQueueItem | None: ...
    async def fail(
        self,
        queue_id: str,
        error: str | None = None,
        worker_id: str | None = None,
    ) -> AlertPriorityQueueItem | None: ...
    async def requeue(
        self,
        queue_id: str,
        available_at: datetime | None = None,
        priority: int | None = None,
        reason: str | None = None,
    ) -> AlertPriorityQueueItem | None: ...
    async def reset_expired_leases(
        self,
        now: datetime | None = None,
        limit: int = 100,
    ) -> int: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _redact_error(error: str | None) -> str | None:
    """Redact sensitive patterns from error messages."""
    if not error:
        return error
    _patterns = ["token=", "secret=", "api_key=", "password=", "authorization:", "x-signature:", "x-api-key:"]
    redacted = error
    for pattern in _patterns:
        if pattern.lower() in redacted.lower():
            # Replace entire value after the pattern up to a delimiter
            import re
            regex = re.escape(pattern) + r'[^\s,;}]*'
            redacted = re.sub(
                regex,
                f'{pattern}[REDACTED]',
                redacted,
                flags=re.IGNORECASE,
            )
    return redacted


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryAlertPriorityQueueStore:
    """In-memory alert priority queue store.

    Phase 57: Supports atomic claim/ack/fail/requeue with in-process locking.
    """

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

    async def claim_next(
        self,
        now: datetime | None = None,
        limit: int = 100,
        worker_id: str | None = None,
        lease_seconds: int = 300,
    ) -> list[AlertPriorityQueueItem]:
        """Atomically claim the highest-priority claimable items.

        Returns items whose status has been updated to 'claimed'.
        """
        if now is None:
            now = _now()

        claimable = [
            item for item in self._items.values()
            if item.is_claimable(now)
        ]
        # Sort: priority DESC, available_at ASC, created_at ASC
        claimable.sort(
            key=lambda i: (i.priority, -i.available_at.timestamp(), -i.created_at.timestamp()),
            reverse=True,
        )
        claimed: list[AlertPriorityQueueItem] = []
        for item in claimable[:limit]:
            item.status = AlertPriorityQueueItemStatus.CLAIMED.value
            item.claimed_by = worker_id
            item.claimed_at = now
            item.lease_expires_at = now + __import__("datetime").timedelta(seconds=lease_seconds)
            claimed.append(item)
        return claimed

    async def acknowledge(
        self,
        queue_id: str,
        worker_id: str | None = None,
    ) -> AlertPriorityQueueItem | None:
        item = self._items.get(queue_id)
        if item is None:
            return None
        status = AlertPriorityQueueItemStatus(item.status)
        if status not in (
            AlertPriorityQueueItemStatus.CLAIMED,
            AlertPriorityQueueItemStatus.PROCESSING,
        ):
            return None
        if worker_id is not None and item.claimed_by is not None and item.claimed_by != worker_id:
            return None
        item.status = AlertPriorityQueueItemStatus.COMPLETED.value
        item.claimed_by = None
        item.claimed_at = None
        item.lease_expires_at = None
        return item

    async def fail(
        self,
        queue_id: str,
        error: str | None = None,
        worker_id: str | None = None,
    ) -> AlertPriorityQueueItem | None:
        item = self._items.get(queue_id)
        if item is None:
            return None
        status = AlertPriorityQueueItemStatus(item.status)
        if status not in (
            AlertPriorityQueueItemStatus.CLAIMED,
            AlertPriorityQueueItemStatus.PROCESSING,
        ):
            return None
        if worker_id is not None and item.claimed_by is not None and item.claimed_by != worker_id:
            return None
        item.status = AlertPriorityQueueItemStatus.FAILED.value
        # Store redacted error in metadata
        metadata = json.loads(item.metadata_json) if item.metadata_json else {}
        if error:
            metadata["last_error"] = _redact_error(error)
        item.metadata_json = json.dumps(metadata)
        item.claimed_by = None
        item.claimed_at = None
        item.lease_expires_at = None
        return item

    async def requeue(
        self,
        queue_id: str,
        available_at: datetime | None = None,
        priority: int | None = None,
        reason: str | None = None,
    ) -> AlertPriorityQueueItem | None:
        item = self._items.get(queue_id)
        if item is None:
            return None
        status = AlertPriorityQueueItemStatus(item.status)
        if status not in (
            AlertPriorityQueueItemStatus.CLAIMED,
            AlertPriorityQueueItemStatus.FAILED,
            AlertPriorityQueueItemStatus.EXPIRED,
        ):
            return None
        item.status = AlertPriorityQueueItemStatus.REQUEUED.value
        if available_at is not None:
            item.available_at = available_at
        else:
            item.available_at = _now()
        if priority is not None:
            item.priority = priority
        item.claimed_by = None
        item.claimed_at = None
        item.lease_expires_at = None
        # Record requeue reason in metadata
        metadata = json.loads(item.metadata_json) if item.metadata_json else {}
        if reason:
            metadata["last_requeue_reason"] = _redact_error(reason)
        metadata.setdefault("requeue_count", 0)
        metadata["requeue_count"] = metadata.get("requeue_count", 0) + 1
        item.metadata_json = json.dumps(metadata)
        item.attempt += 1
        return item

    async def reset_expired_leases(
        self,
        now: datetime | None = None,
        limit: int = 100,
    ) -> int:
        """Reset expired leases back to QUEUED status."""
        if now is None:
            now = _now()
        expired = [
            item for item in self._items.values()
            if item.is_lease_expired(now)
        ]
        # Sort by priority DESC, created_at ASC
        expired.sort(
            key=lambda i: (i.priority, i.created_at),
            reverse=True,
        )
        reset_count = 0
        for item in expired[:limit]:
            item.status = AlertPriorityQueueItemStatus.QUEUED.value
            item.claimed_by = None
            item.claimed_at = None
            item.lease_expires_at = None
            metadata = json.loads(item.metadata_json) if item.metadata_json else {}
            metadata.setdefault("lease_expired_count", 0)
            metadata["lease_expired_count"] = metadata.get("lease_expired_count", 0) + 1
            item.metadata_json = json.dumps(metadata)
            reset_count += 1
        return reset_count


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteAlertPriorityQueueStore:
    """SQLite-backed alert priority queue store.

    Phase 57: Transaction-based atomic claim, lease management.
    """

    def __init__(self, db_path: str = ".agent_app/alert_priority_queue.db") -> None:
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
                claimed_by TEXT,
                claimed_at TEXT,
                lease_expires_at TEXT,
                available_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_apq_status ON alert_priority_queue(status);
            CREATE INDEX IF NOT EXISTS idx_apq_priority ON alert_priority_queue(priority DESC);
            CREATE INDEX IF NOT EXISTS idx_apq_created ON alert_priority_queue(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_apq_available ON alert_priority_queue(available_at);
            CREATE INDEX IF NOT EXISTS idx_apq_lease ON alert_priority_queue(lease_expires_at);
        """)
        self._conn.commit()

    async def enqueue(self, item: AlertPriorityQueueItem) -> AlertPriorityQueueItem:
        self._conn.execute(
            """INSERT OR REPLACE INTO alert_priority_queue
               (attempt_id, alert_id, target_id, channel_type, status,
                priority, created_at, next_retry_at, attempt,
                claimed_by, claimed_at, lease_expires_at, available_at, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.attempt_id,
                item.alert_id,
                item.target_id,
                item.channel_type.value,
                item.status,
                item.priority,
                item.created_at.isoformat(),
                item.next_retry_at.isoformat() if item.next_retry_at else None,
                item.attempt,
                item.claimed_by,
                item.claimed_at.isoformat() if item.claimed_at else None,
                item.lease_expires_at.isoformat() if item.lease_expires_at else None,
                item.available_at.isoformat(),
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

    async def claim_next(
        self,
        now: datetime | None = None,
        limit: int = 100,
        worker_id: str | None = None,
        lease_seconds: int = 300,
    ) -> list[AlertPriorityQueueItem]:
        """Atomically claim highest-priority claimable items in a transaction."""
        if now is None:
            now = _now()
        now_iso = now.isoformat()
        lease_expires = (now + __import__("datetime").timedelta(seconds=lease_seconds)).isoformat()

        # Select claimable items (queued/requeued + available_at <= now)
        # ORDER BY priority DESC, available_at ASC, created_at ASC
        rows = self._conn.execute(
            """SELECT * FROM alert_priority_queue
               WHERE status IN (?, ?) AND available_at <= ?
               ORDER BY priority DESC, available_at ASC, created_at ASC
               LIMIT ?""",
            (
                AlertPriorityQueueItemStatus.QUEUED.value,
                AlertPriorityQueueItemStatus.REQUEUED.value,
                now_iso,
                limit,
            ),
        ).fetchall()

        claimed: list[AlertPriorityQueueItem] = []
        for row in rows:
            # Atomic update: claim only if still in claimable status
            cursor = self._conn.execute(
                """UPDATE alert_priority_queue
                   SET status=?, claimed_by=?, claimed_at=?, lease_expires_at=?
                   WHERE attempt_id=? AND status IN (?, ?)""",
                (
                    AlertPriorityQueueItemStatus.CLAIMED.value,
                    worker_id,
                    now_iso,
                    lease_expires,
                    row["attempt_id"],
                    AlertPriorityQueueItemStatus.QUEUED.value,
                    AlertPriorityQueueItemStatus.REQUEUED.value,
                ),
            )
            if cursor.rowcount > 0:
                item = self._row_to_item(row)
                item.status = AlertPriorityQueueItemStatus.CLAIMED.value
                item.claimed_by = worker_id
                item.claimed_at = now
                item.lease_expires_at = now + __import__("datetime").timedelta(seconds=lease_seconds)
                claimed.append(item)

        self._conn.commit()
        return claimed

    async def acknowledge(
        self,
        queue_id: str,
        worker_id: str | None = None,
    ) -> AlertPriorityQueueItem | None:
        """Mark a claimed/processing item as completed."""
        # First verify the item exists and is in claimable terminal state
        row = self._conn.execute(
            "SELECT * FROM alert_priority_queue WHERE attempt_id=?",
            (queue_id,),
        ).fetchone()
        if row is None:
            return None

        status = AlertPriorityQueueItemStatus(row["status"])
        if status not in (
            AlertPriorityQueueItemStatus.CLAIMED,
            AlertPriorityQueueItemStatus.PROCESSING,
        ):
            return None

        # If worker_id specified, verify ownership
        if worker_id is not None and row["claimed_by"] is not None and row["claimed_by"] != worker_id:
            return None

        self._conn.execute(
            """UPDATE alert_priority_queue
               SET status=?, claimed_by=NULL, claimed_at=NULL, lease_expires_at=NULL
               WHERE attempt_id=?""",
            (AlertPriorityQueueItemStatus.COMPLETED.value, queue_id),
        )
        self._conn.commit()
        item = self._row_to_item(row)
        item.status = AlertPriorityQueueItemStatus.COMPLETED.value
        item.claimed_by = None
        item.claimed_at = None
        item.lease_expires_at = None
        return item

    async def fail(
        self,
        queue_id: str,
        error: str | None = None,
        worker_id: str | None = None,
    ) -> AlertPriorityQueueItem | None:
        """Mark a claimed/processing item as failed."""
        row = self._conn.execute(
            "SELECT * FROM alert_priority_queue WHERE attempt_id=?",
            (queue_id,),
        ).fetchone()
        if row is None:
            return None

        status = AlertPriorityQueueItemStatus(row["status"])
        if status not in (
            AlertPriorityQueueItemStatus.CLAIMED,
            AlertPriorityQueueItemStatus.PROCESSING,
        ):
            return None

        if worker_id is not None and row["claimed_by"] is not None and row["claimed_by"] != worker_id:
            return None

        # Update metadata with redacted error
        metadata = json.loads(row["metadata_json"] or "{}")
        if error:
            metadata["last_error"] = _redact_error(error)

        self._conn.execute(
            """UPDATE alert_priority_queue
               SET status=?, claimed_by=NULL, claimed_at=NULL, lease_expires_at=NULL, metadata_json=?
               WHERE attempt_id=?""",
            (
                AlertPriorityQueueItemStatus.FAILED.value,
                json.dumps(metadata),
                queue_id,
            ),
        )
        self._conn.commit()
        item = self._row_to_item(row)
        item.status = AlertPriorityQueueItemStatus.FAILED.value
        item.metadata_json = json.dumps(metadata)
        item.claimed_by = None
        item.claimed_at = None
        item.lease_expires_at = None
        return item

    async def requeue(
        self,
        queue_id: str,
        available_at: datetime | None = None,
        priority: int | None = None,
        reason: str | None = None,
    ) -> AlertPriorityQueueItem | None:
        """Return a claimed/failed/expired item to the queue."""
        row = self._conn.execute(
            "SELECT * FROM alert_priority_queue WHERE attempt_id=?",
            (queue_id,),
        ).fetchone()
        if row is None:
            return None

        status = AlertPriorityQueueItemStatus(row["status"])
        if status not in (
            AlertPriorityQueueItemStatus.CLAIMED,
            AlertPriorityQueueItemStatus.FAILED,
            AlertPriorityQueueItemStatus.EXPIRED,
        ):
            return None

        new_available = (available_at or _now()).isoformat()
        new_priority = priority if priority is not None else row["priority"]

        # Update metadata
        metadata = json.loads(row["metadata_json"] or "{}")
        if reason:
            metadata["last_requeue_reason"] = _redact_error(reason)
        metadata.setdefault("requeue_count", 0)
        metadata["requeue_count"] = metadata.get("requeue_count", 0) + 1

        self._conn.execute(
            """UPDATE alert_priority_queue
               SET status=?, available_at=?, priority=?, attempt=attempt+1,
                   claimed_by=NULL, claimed_at=NULL, lease_expires_at=NULL, metadata_json=?
               WHERE attempt_id=?""",
            (
                AlertPriorityQueueItemStatus.REQUEUED.value,
                new_available,
                new_priority,
                json.dumps(metadata),
                queue_id,
            ),
        )
        self._conn.commit()
        item = self._row_to_item(row)
        item.status = AlertPriorityQueueItemStatus.REQUEUED.value
        item.available_at = datetime.fromisoformat(new_available)
        item.priority = new_priority
        item.attempt = row["attempt"] + 1
        item.metadata_json = json.dumps(metadata)
        item.claimed_by = None
        item.claimed_at = None
        item.lease_expires_at = None
        return item

    async def reset_expired_leases(
        self,
        now: datetime | None = None,
        limit: int = 100,
    ) -> int:
        """Reset expired leases back to QUEUED status."""
        if now is None:
            now = _now()
        now_iso = now.isoformat()

        # Find expired items
        rows = self._conn.execute(
            """SELECT * FROM alert_priority_queue
               WHERE status IN (?, ?) AND lease_expires_at IS NOT NULL AND lease_expires_at < ?
               ORDER BY priority DESC, created_at ASC
               LIMIT ?""",
            (
                AlertPriorityQueueItemStatus.CLAIMED.value,
                AlertPriorityQueueItemStatus.PROCESSING.value,
                now_iso,
                limit,
            ),
        ).fetchall()

        reset_count = 0
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            metadata.setdefault("lease_expired_count", 0)
            metadata["lease_expired_count"] = metadata.get("lease_expired_count", 0) + 1

            self._conn.execute(
                """UPDATE alert_priority_queue
                   SET status=?, claimed_by=NULL, claimed_at=NULL, lease_expires_at=NULL, metadata_json=?
                   WHERE attempt_id=?""",
                (
                    AlertPriorityQueueItemStatus.QUEUED.value,
                    json.dumps(metadata),
                    row["attempt_id"],
                ),
            )
            reset_count += 1

        if reset_count > 0:
            self._conn.commit()
        return reset_count

    def _row_to_item(self, row: sqlite3.Row) -> AlertPriorityQueueItem:
        data = dict(row)
        data["channel_type"] = AlertDeliveryChannelType(data["channel_type"])
        # status stays as raw string — validator accepts both legacy and new values
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        if data["next_retry_at"] is not None:
            data["next_retry_at"] = datetime.fromisoformat(data["next_retry_at"])
        if data["claimed_at"] is not None:
            data["claimed_at"] = datetime.fromisoformat(data["claimed_at"])
        if data["lease_expires_at"] is not None:
            data["lease_expires_at"] = datetime.fromisoformat(data["lease_expires_at"])
        if data.get("available_at") is not None:
            data["available_at"] = datetime.fromisoformat(data["available_at"])
        else:
            # Fallback for backward compat: use created_at
            data["available_at"] = data["created_at"]
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
