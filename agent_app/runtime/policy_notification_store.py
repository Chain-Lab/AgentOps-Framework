"""Policy notification store -- persists PolicyNotificationMessage instances with Protocol + InMemory + SQLite."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Protocol

from agent_app.governance.policy_notification import (
    PolicyNotificationMessage,
    PolicyNotificationSeverity,
    PolicyNotificationStatus,
)

try:
    from typing import runtime_checkable
except ImportError:
    def runtime_checkable(cls):  # type: ignore[misc]
        return cls


@runtime_checkable
class PolicyNotificationStore(Protocol):
    """Protocol for persisting policy notification messages."""

    async def create(self, message: PolicyNotificationMessage) -> PolicyNotificationMessage: ...
    async def get(self, notification_id: str) -> PolicyNotificationMessage | None: ...
    async def update(self, message: PolicyNotificationMessage) -> PolicyNotificationMessage: ...
    async def list(
        self,
        status: PolicyNotificationStatus | None = None,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> list[PolicyNotificationMessage]: ...


class InMemoryPolicyNotificationStore:
    """In-memory policy notification store."""

    def __init__(self) -> None:
        self._messages: dict[str, PolicyNotificationMessage] = {}
        self._order: list[str] = []

    async def create(self, message: PolicyNotificationMessage) -> PolicyNotificationMessage:
        self._messages[message.notification_id] = message
        self._order.append(message.notification_id)
        return message

    async def get(self, notification_id: str) -> PolicyNotificationMessage | None:
        return self._messages.get(notification_id)

    async def update(self, message: PolicyNotificationMessage) -> PolicyNotificationMessage:
        if message.notification_id not in self._messages:
            raise KeyError(f"Policy notification '{message.notification_id}' not found")
        self._messages[message.notification_id] = message
        return message

    async def list(
        self,
        status: PolicyNotificationStatus | None = None,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> list[PolicyNotificationMessage]:
        results: list[PolicyNotificationMessage] = []
        for nid in reversed(self._order):
            msg = self._messages[nid]
            if status is not None and msg.status != status:
                continue
            if event_type is not None and msg.event_type != event_type:
                continue
            results.append(msg)
            if limit is not None and len(results) >= limit:
                break
        return results


class SQLitePolicyNotificationStore:
    """SQLite-backed policy notification store."""

    def __init__(self, db_path: str = ".agent_app/policy_notifications.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS policy_notifications (
                notification_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                source_type TEXT,
                source_id TEXT,
                actor_id TEXT,
                metadata_json TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                sent_at TEXT,
                error_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_pn_status ON policy_notifications(status);
            CREATE INDEX IF NOT EXISTS idx_pn_event_type ON policy_notifications(event_type);
        """)
        self._conn.commit()

    async def create(self, message: PolicyNotificationMessage) -> PolicyNotificationMessage:
        metadata_json = json.dumps(message.metadata)
        error_json = json.dumps(message.error) if message.error else None
        sent_at_str = message.sent_at.isoformat() if message.sent_at else None
        self._conn.execute(
            """INSERT INTO policy_notifications
               (notification_id, event_type, severity, title, body,
                source_type, source_id, actor_id, metadata_json,
                status, created_at, sent_at, error_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                message.notification_id,
                message.event_type,
                message.severity.value,
                message.title,
                message.body,
                message.source_type,
                message.source_id,
                message.actor_id,
                metadata_json,
                message.status.value,
                message.created_at.isoformat(),
                sent_at_str,
                error_json,
            ),
        )
        self._conn.commit()
        return message

    async def get(self, notification_id: str) -> PolicyNotificationMessage | None:
        row = self._conn.execute(
            "SELECT * FROM policy_notifications WHERE notification_id=?", (notification_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_message(row)

    async def update(self, message: PolicyNotificationMessage) -> PolicyNotificationMessage:
        if await self.get(message.notification_id) is None:
            raise KeyError(f"Policy notification '{message.notification_id}' not found")
        metadata_json = json.dumps(message.metadata)
        error_json = json.dumps(message.error) if message.error else None
        sent_at_str = message.sent_at.isoformat() if message.sent_at else None
        self._conn.execute(
            """UPDATE policy_notifications
               SET event_type=?, severity=?, title=?, body=?,
                   source_type=?, source_id=?, actor_id=?,
                   metadata_json=?, status=?, created_at=?,
                   sent_at=?, error_json=?
               WHERE notification_id=?""",
            (
                message.event_type,
                message.severity.value,
                message.title,
                message.body,
                message.source_type,
                message.source_id,
                message.actor_id,
                metadata_json,
                message.status.value,
                message.created_at.isoformat(),
                sent_at_str,
                error_json,
                message.notification_id,
            ),
        )
        self._conn.commit()
        return message

    async def list(
        self,
        status: PolicyNotificationStatus | None = None,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> list[PolicyNotificationMessage]:
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("status=?")
            params.append(status.value)
        if event_type is not None:
            clauses.append("event_type=?")
            params.append(event_type)
        where = ""
        if clauses:
            where = " WHERE " + " AND ".join(clauses)
        sql = f"SELECT * FROM policy_notifications{where} ORDER BY created_at DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_message(row) for row in rows]

    def _row_to_message(self, row: sqlite3.Row) -> PolicyNotificationMessage:
        from datetime import datetime

        data = dict(row)
        data["severity"] = PolicyNotificationSeverity(data["severity"])
        data["status"] = PolicyNotificationStatus(data["status"])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        if data["sent_at"] is not None:
            data["sent_at"] = datetime.fromisoformat(data["sent_at"])
        # Parse metadata_json
        metadata_json = data.pop("metadata_json", None)
        if metadata_json:
            data["metadata"] = json.loads(metadata_json)
        else:
            data.pop("metadata", None)
        # Parse error_json
        error_json = data.pop("error_json", None)
        if error_json:
            data["error"] = json.loads(error_json)
        else:
            data.pop("error", None)
        return PolicyNotificationMessage(**data)

    def close(self) -> None:
        self._conn.close()


def create_policy_notification_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> PolicyNotificationStore:
    if store_type == "memory":
        return InMemoryPolicyNotificationStore()
    if store_type == "sqlite":
        if not db_path:
            raise ValueError("db_path is required when store_type='sqlite'")
        return SQLitePolicyNotificationStore(db_path=db_path)
    raise ValueError(f"Unknown policy notification store type '{store_type}'. Supported: 'memory', 'sqlite'.")
