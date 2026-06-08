"""Audit module — event logging for tool calls, approvals, and runs.

Phase 3: InMemoryAuditLogger with query support.
Phase 4: SQLiteAuditLogger added for persistence.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field


class AuditEvent(BaseModel):
    """A single audit log entry."""

    event_id: str = Field(..., description="Unique event ID")
    run_id: str | None = Field(default=None, description="Associated run ID")
    event_type: str = Field(..., description="Event category")
    user_id: str | None = Field(default=None, description="User identifier")
    tenant_id: str | None = Field(default=None, description="Tenant ID")
    tool_name: str | None = Field(default=None, description="Tool name")
    approval_id: str | None = Field(default=None, description="Approval ID")
    data: dict = Field(default_factory=dict, description="Event details")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Event timestamp",
    )


class AuditLogger(Protocol):
    """Protocol for audit logging."""

    async def log(self, event: AuditEvent) -> None:
        """Record an audit event."""
        ...


class InMemoryAuditLogger:
    """In-memory audit logger with query support."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    async def log(self, event: AuditEvent) -> None:
        self._events.append(event)

    def list_events(
        self,
        run_id: str | None = None,
        tenant_id: str | None = None,
        event_type: str | None = None,
    ) -> list[AuditEvent]:
        results = self._events
        if run_id is not None:
            results = [e for e in results if e.run_id == run_id]
        if tenant_id is not None:
            results = [e for e in results if e.tenant_id == tenant_id]
        if event_type is not None:
            results = [e for e in results if e.event_type == event_type]
        return sorted(results, key=lambda e: e.created_at)

    def clear(self) -> None:
        self._events.clear()


class SQLiteAuditLogger:
    """SQLite-backed audit logger.

    Persists audit events to a SQLite database file.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str = ".agent_app/audit.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id    TEXT PRIMARY KEY,
                run_id      TEXT,
                event_type  TEXT NOT NULL,
                user_id     TEXT,
                tenant_id   TEXT,
                tool_name   TEXT,
                approval_id TEXT,
                data_json   TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_run_id ON audit_events(run_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_events(tenant_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_events(event_type)"
        )
        self._conn.commit()

    async def log(self, event: AuditEvent) -> None:
        self._conn.execute(
            """
            INSERT INTO audit_events
                (event_id, run_id, event_type, user_id, tenant_id,
                 tool_name, approval_id, data_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.run_id,
                event.event_type,
                event.user_id,
                event.tenant_id,
                event.tool_name,
                event.approval_id,
                json.dumps(event.data),
                event.created_at.isoformat(),
            ),
        )
        self._conn.commit()

    def list_events(
        self,
        run_id: str | None = None,
        tenant_id: str | None = None,
        event_type: str | None = None,
    ) -> list[AuditEvent]:
        query = "SELECT * FROM audit_events WHERE 1=1"
        params: list = []
        if run_id is not None:
            query += " AND run_id = ?"
            params.append(run_id)
        if tenant_id is not None:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        if event_type is not None:
            query += " AND event_type = ?"
            params.append(event_type)
        query += " ORDER BY created_at"
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def close(self) -> None:
        self._conn.close()

    def _row_to_event(self, row: sqlite3.Row) -> AuditEvent:
        from datetime import datetime, timezone

        created_at = datetime.fromisoformat(row["created_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return AuditEvent(
            event_id=row["event_id"],
            run_id=row["run_id"],
            event_type=row["event_type"],
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            tool_name=row["tool_name"],
            approval_id=row["approval_id"],
            data=json.loads(row["data_json"]),
            created_at=created_at,
        )
