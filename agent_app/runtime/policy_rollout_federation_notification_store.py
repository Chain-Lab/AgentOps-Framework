"""Federation notification store -- persists FederationNotificationMessage with Protocol + InMemory + SQLite."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from agent_app.governance.policy_rollout_federation_notification import (
    FederationNotificationMessage,
    FederationNotificationStatus,
)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class FederationNotificationStore(Protocol):
    """Protocol for persisting federation notification messages."""

    async def create(self, message: FederationNotificationMessage) -> FederationNotificationMessage: ...
    async def get(self, notification_id: str) -> FederationNotificationMessage | None: ...
    async def list(self, status: FederationNotificationStatus | None = None, limit: int = 100) -> list[FederationNotificationMessage]: ...
    async def list_pending(self, limit: int = 100) -> list[FederationNotificationMessage]: ...
    async def mark_sent(self, notification_id: str) -> FederationNotificationMessage: ...
    async def mark_failed(self, notification_id: str, error: str, next_attempt_at: datetime | None = None) -> FederationNotificationMessage: ...
    async def cancel(self, notification_id: str) -> FederationNotificationMessage: ...
    async def list_by_approval(self, approval_id: str) -> list[FederationNotificationMessage]: ...


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryFederationNotificationStore:
    """In-memory federation notification message store."""

    def __init__(self) -> None:
        self._messages: dict[str, FederationNotificationMessage] = {}

    async def create(self, message: FederationNotificationMessage) -> FederationNotificationMessage:
        self._messages[message.notification_id] = message
        return message

    async def get(self, notification_id: str) -> FederationNotificationMessage | None:
        return self._messages.get(notification_id)

    async def list(self, status: FederationNotificationStatus | None = None, limit: int = 100) -> list[FederationNotificationMessage]:
        msgs = list(self._messages.values())
        if status is not None:
            msgs = [m for m in msgs if m.status == status]
        msgs.sort(key=lambda m: m.created_at)
        return msgs[:limit]

    async def list_pending(self, limit: int = 100) -> list[FederationNotificationMessage]:
        pending = [
            msg for msg in self._messages.values()
            if msg.status == FederationNotificationStatus.PENDING
        ]
        pending.sort(key=lambda m: m.created_at)
        return pending[:limit]

    async def mark_sent(self, notification_id: str) -> FederationNotificationMessage:
        msg = self._messages.get(notification_id)
        if msg is None:
            raise ValueError(f"Federation notification '{notification_id}' not found")
        msg.status = FederationNotificationStatus.SENT
        msg.sent_at = datetime.now(timezone.utc)
        return msg

    async def mark_failed(self, notification_id: str, error: str, next_attempt_at: datetime | None = None) -> FederationNotificationMessage:
        msg = self._messages.get(notification_id)
        if msg is None:
            raise ValueError(f"Federation notification '{notification_id}' not found")
        msg.attempt_count += 1
        msg.last_error = error
        msg.next_attempt_at = next_attempt_at
        if next_attempt_at is not None:
            msg.status = FederationNotificationStatus.PENDING
        else:
            msg.status = FederationNotificationStatus.FAILED
        return msg

    async def cancel(self, notification_id: str) -> FederationNotificationMessage:
        msg = self._messages.get(notification_id)
        if msg is None:
            raise ValueError(f"Federation notification '{notification_id}' not found")
        msg.status = FederationNotificationStatus.CANCELLED
        return msg

    async def list_by_approval(self, approval_id: str) -> list[FederationNotificationMessage]:
        results = [
            msg for msg in self._messages.values()
            if msg.approval_id == approval_id
        ]
        results.sort(key=lambda m: m.created_at)
        return results


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteFederationNotificationStore:
    """SQLite-backed federation notification message store."""

    def __init__(self, db_path: str = ".agent_app/federation_notifications.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS federation_notifications (
                notification_id TEXT PRIMARY KEY,
                approval_id TEXT NOT NULL,
                federation_id TEXT,
                event_type TEXT NOT NULL,
                channel TEXT NOT NULL,
                recipients_json TEXT NOT NULL,
                subject TEXT,
                body TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL,
                max_attempts INTEGER NOT NULL,
                last_error TEXT,
                created_at TEXT NOT NULL,
                sent_at TEXT,
                next_attempt_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_fn_status ON federation_notifications(status);
            CREATE INDEX IF NOT EXISTS idx_fn_approval_id ON federation_notifications(approval_id);
        """)
        self._conn.commit()

    async def create(self, message: FederationNotificationMessage) -> FederationNotificationMessage:
        self._conn.execute(
            """INSERT INTO federation_notifications
               (notification_id, approval_id, federation_id, event_type, channel,
                recipients_json, subject, body, payload_json,
                status, attempt_count, max_attempts, last_error,
                created_at, sent_at, next_attempt_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                message.notification_id,
                message.approval_id,
                message.federation_id,
                message.event_type.value,
                message.channel.value,
                json.dumps(message.recipients),
                message.subject,
                message.body,
                json.dumps(message.payload),
                message.status.value,
                message.attempt_count,
                message.max_attempts,
                message.last_error,
                message.created_at.isoformat(),
                message.sent_at.isoformat() if message.sent_at else None,
                message.next_attempt_at.isoformat() if message.next_attempt_at else None,
            ),
        )
        self._conn.commit()
        return message

    async def get(self, notification_id: str) -> FederationNotificationMessage | None:
        row = self._conn.execute(
            "SELECT * FROM federation_notifications WHERE notification_id=?",
            (notification_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_message(row)

    async def list(self, status: FederationNotificationStatus | None = None, limit: int = 100) -> list[FederationNotificationMessage]:
        if status is not None:
            rows = self._conn.execute(
                "SELECT * FROM federation_notifications WHERE status=? ORDER BY created_at ASC LIMIT ?",
                (status.value, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM federation_notifications ORDER BY created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_message(row) for row in rows]

    async def list_pending(self, limit: int = 100) -> list[FederationNotificationMessage]:
        rows = self._conn.execute(
            "SELECT * FROM federation_notifications WHERE status=? ORDER BY created_at ASC LIMIT ?",
            (FederationNotificationStatus.PENDING.value, limit),
        ).fetchall()
        return [self._row_to_message(row) for row in rows]

    async def mark_sent(self, notification_id: str) -> FederationNotificationMessage:
        row = self._conn.execute(
            "SELECT * FROM federation_notifications WHERE notification_id=?",
            (notification_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Federation notification '{notification_id}' not found")
        msg = self._row_to_message(row)
        msg.status = FederationNotificationStatus.SENT
        msg.sent_at = datetime.now(timezone.utc)
        self._conn.execute(
            """UPDATE federation_notifications
               SET status=?, sent_at=?
               WHERE notification_id=?""",
            (
                FederationNotificationStatus.SENT.value,
                msg.sent_at.isoformat(),
                notification_id,
            ),
        )
        self._conn.commit()
        return msg

    async def mark_failed(self, notification_id: str, error: str, next_attempt_at: datetime | None = None) -> FederationNotificationMessage:
        row = self._conn.execute(
            "SELECT * FROM federation_notifications WHERE notification_id=?",
            (notification_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Federation notification '{notification_id}' not found")
        msg = self._row_to_message(row)
        msg.attempt_count += 1
        msg.last_error = error
        msg.next_attempt_at = next_attempt_at
        if next_attempt_at is not None:
            msg.status = FederationNotificationStatus.PENDING
        else:
            msg.status = FederationNotificationStatus.FAILED
        self._conn.execute(
            """UPDATE federation_notifications
               SET status=?, attempt_count=?, last_error=?, next_attempt_at=?
               WHERE notification_id=?""",
            (
                msg.status.value,
                msg.attempt_count,
                msg.last_error,
                msg.next_attempt_at.isoformat() if msg.next_attempt_at else None,
                notification_id,
            ),
        )
        self._conn.commit()
        return msg

    async def cancel(self, notification_id: str) -> FederationNotificationMessage:
        row = self._conn.execute(
            "SELECT * FROM federation_notifications WHERE notification_id=?",
            (notification_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Federation notification '{notification_id}' not found")
        msg = self._row_to_message(row)
        msg.status = FederationNotificationStatus.CANCELLED
        self._conn.execute(
            "UPDATE federation_notifications SET status=? WHERE notification_id=?",
            (FederationNotificationStatus.CANCELLED.value, notification_id),
        )
        self._conn.commit()
        return msg

    async def list_by_approval(self, approval_id: str) -> list[FederationNotificationMessage]:
        rows = self._conn.execute(
            "SELECT * FROM federation_notifications WHERE approval_id=? ORDER BY created_at ASC",
            (approval_id,),
        ).fetchall()
        return [self._row_to_message(row) for row in rows]

    def _row_to_message(self, row: sqlite3.Row) -> FederationNotificationMessage:
        from agent_app.governance.policy_rollout_federation_notification import (
            FederationNotificationChannel,
            FederationNotificationEventType,
        )

        data = dict(row)
        data["event_type"] = FederationNotificationEventType(data["event_type"])
        data["channel"] = FederationNotificationChannel(data["channel"])
        data["recipients"] = json.loads(data.pop("recipients_json"))
        data["payload"] = json.loads(data.pop("payload_json"))
        data["status"] = FederationNotificationStatus(data["status"])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        if data["sent_at"] is not None:
            data["sent_at"] = datetime.fromisoformat(data["sent_at"])
        if data["next_attempt_at"] is not None:
            data["next_attempt_at"] = datetime.fromisoformat(data["next_attempt_at"])
        return FederationNotificationMessage(**data)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_federation_notification_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> FederationNotificationStore:
    """Factory for creating federation notification store instances."""
    if store_type == "memory":
        return InMemoryFederationNotificationStore()
    elif store_type == "sqlite":
        return SQLiteFederationNotificationStore(db_path=db_path or ".agent_app/federation_notifications.db")
    else:
        raise ValueError(f"Unknown federation notification store type: {store_type}")
