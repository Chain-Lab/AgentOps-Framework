"""Federation notification dead-letter queue store — Protocol, InMemory, SQLite, factory.

Phase 50: DLQ persistence for failed notifications that exceed retry limits.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from agent_app.governance.policy_rollout_federation_notification import (
    FederationNotificationDeadLetter,
    FederationNotificationDLQReason,
    FederationNotificationDLQStatus,
)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class FederationNotificationDLQStore(Protocol):
    """Protocol for persisting federation notification dead-letter queue entries."""

    async def create(self, item: FederationNotificationDeadLetter) -> FederationNotificationDeadLetter: ...
    async def get(self, dlq_id: str) -> FederationNotificationDeadLetter | None: ...
    async def list(
        self,
        status: str | None = None,
        federation_id: str | None = None,
        approval_id: str | None = None,
        channel: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FederationNotificationDeadLetter]: ...
    async def mark_retried(self, dlq_id: str) -> FederationNotificationDeadLetter: ...
    async def mark_purged(self, dlq_id: str) -> FederationNotificationDeadLetter: ...
    async def delete(self, dlq_id: str) -> None: ...


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryFederationNotificationDLQStore:
    """In-memory federation notification dead-letter queue store."""

    def __init__(self) -> None:
        self._items: dict[str, FederationNotificationDeadLetter] = {}

    async def create(self, item: FederationNotificationDeadLetter) -> FederationNotificationDeadLetter:
        self._items[item.dlq_id] = item
        return item

    async def get(self, dlq_id: str) -> FederationNotificationDeadLetter | None:
        return self._items.get(dlq_id)

    async def list(
        self,
        status: str | None = None,
        federation_id: str | None = None,
        approval_id: str | None = None,
        channel: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FederationNotificationDeadLetter]:
        items = list(self._items.values())
        if status is not None:
            items = [i for i in items if i.status == status]
        if federation_id is not None:
            items = [i for i in items if i.federation_id == federation_id]
        if approval_id is not None:
            items = [i for i in items if i.approval_id == approval_id]
        if channel is not None:
            items = [i for i in items if i.channel == channel]
        items.sort(key=lambda i: i.created_at)
        return items[offset : offset + limit]

    async def mark_retried(self, dlq_id: str) -> FederationNotificationDeadLetter:
        item = self._items.get(dlq_id)
        if item is None:
            raise ValueError(f"DLQ entry '{dlq_id}' not found")
        item.status = FederationNotificationDLQStatus.RETRIED
        item.retried_at = datetime.now(timezone.utc)
        item.updated_at = datetime.now(timezone.utc)
        return item

    async def mark_purged(self, dlq_id: str) -> FederationNotificationDeadLetter:
        item = self._items.get(dlq_id)
        if item is None:
            raise ValueError(f"DLQ entry '{dlq_id}' not found")
        item.status = FederationNotificationDLQStatus.PURGED
        item.purged_at = datetime.now(timezone.utc)
        item.updated_at = datetime.now(timezone.utc)
        return item

    async def delete(self, dlq_id: str) -> None:
        self._items.pop(dlq_id, None)


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteFederationNotificationDLQStore:
    """SQLite-backed federation notification dead-letter queue store."""

    def __init__(self, db_path: str = ".agent_app/federation_notification_dlq.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS federation_notification_dlq (
                dlq_id TEXT PRIMARY KEY,
                notification_id TEXT NOT NULL,
                approval_id TEXT,
                federation_id TEXT,
                channel TEXT NOT NULL,
                adapter TEXT,
                recipient TEXT,
                reason TEXT NOT NULL,
                status TEXT NOT NULL,
                failure_count INTEGER NOT NULL,
                last_error TEXT,
                payload_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                retried_at TEXT,
                purged_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_fdlq_status ON federation_notification_dlq(status);
            CREATE INDEX IF NOT EXISTS idx_fdlq_federation_id ON federation_notification_dlq(federation_id);
            CREATE INDEX IF NOT EXISTS idx_fdlq_approval_id ON federation_notification_dlq(approval_id);
            CREATE INDEX IF NOT EXISTS idx_fdlq_channel ON federation_notification_dlq(channel);
        """)
        self._conn.commit()

    async def create(self, item: FederationNotificationDeadLetter) -> FederationNotificationDeadLetter:
        self._conn.execute(
            """INSERT INTO federation_notification_dlq
               (dlq_id, notification_id, approval_id, federation_id, channel,
                adapter, recipient, reason, status, failure_count, last_error,
                payload_json, metadata_json, created_at, updated_at,
                retried_at, purged_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.dlq_id,
                item.notification_id,
                item.approval_id,
                item.federation_id,
                item.channel,
                item.adapter,
                item.recipient,
                item.reason.value,
                item.status.value,
                item.failure_count,
                item.last_error,
                json.dumps(item.payload),
                json.dumps(item.metadata),
                item.created_at.isoformat(),
                item.updated_at.isoformat(),
                item.retried_at.isoformat() if item.retried_at else None,
                item.purged_at.isoformat() if item.purged_at else None,
            ),
        )
        self._conn.commit()
        return item

    async def get(self, dlq_id: str) -> FederationNotificationDeadLetter | None:
        row = self._conn.execute(
            "SELECT * FROM federation_notification_dlq WHERE dlq_id=?",
            (dlq_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_item(row)

    async def list(
        self,
        status: str | None = None,
        federation_id: str | None = None,
        approval_id: str | None = None,
        channel: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FederationNotificationDeadLetter]:
        conditions: list[str] = []
        params: list[str | int] = []

        if status is not None:
            conditions.append("status=?")
            params.append(status)
        if federation_id is not None:
            conditions.append("federation_id=?")
            params.append(federation_id)
        if approval_id is not None:
            conditions.append("approval_id=?")
            params.append(approval_id)
        if channel is not None:
            conditions.append("channel=?")
            params.append(channel)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        params.extend([limit, offset])
        rows = self._conn.execute(
            f"SELECT * FROM federation_notification_dlq {where} ORDER BY created_at ASC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [self._row_to_item(row) for row in rows]

    async def mark_retried(self, dlq_id: str) -> FederationNotificationDeadLetter:
        row = self._conn.execute(
            "SELECT * FROM federation_notification_dlq WHERE dlq_id=?",
            (dlq_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"DLQ entry '{dlq_id}' not found")
        item = self._row_to_item(row)
        item.status = FederationNotificationDLQStatus.RETRIED
        item.retried_at = datetime.now(timezone.utc)
        item.updated_at = datetime.now(timezone.utc)
        self._conn.execute(
            """UPDATE federation_notification_dlq
               SET status=?, retried_at=?, updated_at=?
               WHERE dlq_id=?""",
            (
                FederationNotificationDLQStatus.RETRIED.value,
                item.retried_at.isoformat(),
                item.updated_at.isoformat(),
                dlq_id,
            ),
        )
        self._conn.commit()
        return item

    async def mark_purged(self, dlq_id: str) -> FederationNotificationDeadLetter:
        row = self._conn.execute(
            "SELECT * FROM federation_notification_dlq WHERE dlq_id=?",
            (dlq_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"DLQ entry '{dlq_id}' not found")
        item = self._row_to_item(row)
        item.status = FederationNotificationDLQStatus.PURGED
        item.purged_at = datetime.now(timezone.utc)
        item.updated_at = datetime.now(timezone.utc)
        self._conn.execute(
            """UPDATE federation_notification_dlq
               SET status=?, purged_at=?, updated_at=?
               WHERE dlq_id=?""",
            (
                FederationNotificationDLQStatus.PURGED.value,
                item.purged_at.isoformat(),
                item.updated_at.isoformat(),
                dlq_id,
            ),
        )
        self._conn.commit()
        return item

    async def delete(self, dlq_id: str) -> None:
        self._conn.execute(
            "DELETE FROM federation_notification_dlq WHERE dlq_id=?",
            (dlq_id,),
        )
        self._conn.commit()

    def _row_to_item(self, row: sqlite3.Row) -> FederationNotificationDeadLetter:
        data = dict(row)
        data["reason"] = FederationNotificationDLQReason(data["reason"])
        data["status"] = FederationNotificationDLQStatus(data["status"])
        data["payload"] = json.loads(data.pop("payload_json"))
        data["metadata"] = json.loads(data.pop("metadata_json"))
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        if data["retried_at"] is not None:
            data["retried_at"] = datetime.fromisoformat(data["retried_at"])
        if data["purged_at"] is not None:
            data["purged_at"] = datetime.fromisoformat(data["purged_at"])
        return FederationNotificationDeadLetter(**data)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_federation_notification_dlq_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> FederationNotificationDLQStore:
    """Factory for creating federation notification DLQ store instances."""
    if store_type == "memory":
        return InMemoryFederationNotificationDLQStore()
    if store_type == "sqlite":
        return SQLiteFederationNotificationDLQStore(db_path=db_path or ".agent_app/federation_notification_dlq.db")
    raise ValueError(f"Unknown DLQ store type '{store_type}'. Supported: 'memory', 'sqlite'.")
