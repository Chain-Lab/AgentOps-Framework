"""Persistent audit store for daemon operator control.

Phase 63: Persistent Approval / Control Plane — SQLite-backed store for
persistent audit events that record operator and daemon control actions
with full context for compliance and debugging.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class PersistentAuditEvent(BaseModel):
    """Persistent audit event for daemon operator control."""

    event_id: str
    event_type: str
    command_id: str | None = None
    approval_id: str | None = None
    daemon_id: str | None = None
    actor: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class PersistentAuditStore:
    """SQLite-backed persistent store for audit events."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                command_id TEXT,
                approval_id TEXT,
                daemon_id TEXT,
                actor TEXT,
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_events_event_type
                ON audit_events(event_type);
            CREATE INDEX IF NOT EXISTS idx_audit_events_command_id
                ON audit_events(command_id);
            CREATE INDEX IF NOT EXISTS idx_audit_events_approval_id
                ON audit_events(approval_id);
            CREATE INDEX IF NOT EXISTS idx_audit_events_created_at
                ON audit_events(created_at);
        """)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def append(
        self,
        event_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
        command_id: str | None = None,
        approval_id: str | None = None,
        daemon_id: str | None = None,
        actor: str | None = None,
    ) -> PersistentAuditEvent:
        """Append a new audit event."""
        now = datetime.now(timezone.utc)
        event = PersistentAuditEvent(
            event_id=event_id,
            event_type=event_type,
            command_id=command_id,
            approval_id=approval_id,
            daemon_id=daemon_id,
            actor=actor,
            data=data or {},
            created_at=now,
        )
        self._conn.execute(
            """
            INSERT INTO audit_events
                (event_id, event_type, command_id, approval_id, daemon_id,
                 actor, data_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.event_type,
                event.command_id,
                event.approval_id,
                event.daemon_id,
                event.actor,
                self._json_dumps(event.data),
                event.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return event

    def get(self, event_id: str) -> PersistentAuditEvent | None:
        """Retrieve an audit event by ID."""
        row = self._conn.execute(
            "SELECT * FROM audit_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_event(row)

    def list_recent(
        self,
        limit: int = 100,
        event_type: str | None = None,
        command_id: str | None = None,
        approval_id: str | None = None,
    ) -> list[PersistentAuditEvent]:
        """List recent audit events, optionally filtered.

        Filters are ANDed together. Results ordered by creation time
        descending.
        """
        query = "SELECT * FROM audit_events WHERE 1=1"
        params: list[Any] = []
        if event_type is not None:
            query += " AND event_type = ?"
            params.append(event_type)
        if command_id is not None:
            query += " AND command_id = ?"
            params.append(command_id)
        if approval_id is not None:
            query += " AND approval_id = ?"
            params.append(approval_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def list_by_event_type(
        self,
        event_type: str,
        limit: int = 100,
    ) -> list[PersistentAuditEvent]:
        """List events of a specific type."""
        return self.list_recent(limit=limit, event_type=event_type)

    def list_by_command_id(
        self,
        command_id: str,
        limit: int = 100,
    ) -> list[PersistentAuditEvent]:
        """List events for a specific command."""
        return self.list_recent(limit=limit, command_id=command_id)

    def list_by_approval_id(
        self,
        approval_id: str,
        limit: int = 100,
    ) -> list[PersistentAuditEvent]:
        """List events for a specific approval."""
        return self.list_recent(limit=limit, approval_id=approval_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_event(self, row: sqlite3.Row) -> PersistentAuditEvent:
        return PersistentAuditEvent(
            event_id=row["event_id"],
            event_type=row["event_type"],
            command_id=row["command_id"],
            approval_id=row["approval_id"],
            daemon_id=row["daemon_id"],
            actor=row["actor"],
            data=self._json_loads(row["data_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _json_dumps(data: Any) -> str:
        import json
        return json.dumps(data, default=str)

    @staticmethod
    def _json_loads(text: str) -> Any:
        import json
        return json.loads(text)
