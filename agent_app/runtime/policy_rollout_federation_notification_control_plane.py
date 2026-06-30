"""Persistent control plane store for daemon operator control.

Phase 63: Persistent Approval / Control Plane — SQLite-backed store for
persistent control commands that allow operators to manage daemon lifecycle
(pause, resume, drain, shutdown, flush_metrics, release_lock,
health_snapshot) via durable commands.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ControlCommandStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ControlCommandType(str, Enum):
    PAUSE = "pause"
    RESUME = "resume"
    DRAIN = "drain"
    SHUTDOWN = "shutdown"
    FLUSH_METRICS = "flush_metrics"
    RELEASE_LOCK = "release_lock"
    HEALTH_SNAPSHOT = "health_snapshot"


_TERMINAL_STATUSES = {
    ControlCommandStatus.COMPLETED,
    ControlCommandStatus.FAILED,
    ControlCommandStatus.REJECTED,
    ControlCommandStatus.EXPIRED,
}


class ControlCommand(BaseModel):
    """Persistent control command for daemon operator control."""

    command_id: str
    command_type: ControlCommandType
    status: ControlCommandStatus
    requested_by: str | None = None
    reason: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    created_at: datetime
    accepted_at: datetime | None = None
    completed_at: datetime | None = None
    error: dict[str, Any] | None = None


class ControlPlaneStore:
    """SQLite-backed persistent store for control commands."""

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
            CREATE TABLE IF NOT EXISTS control_commands (
                command_id TEXT PRIMARY KEY,
                command_type TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_by TEXT,
                reason TEXT,
                payload_json TEXT NOT NULL,
                idempotency_key TEXT,
                created_at TEXT NOT NULL,
                accepted_at TEXT,
                completed_at TEXT,
                error_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_control_commands_status
                ON control_commands(status);
            CREATE INDEX IF NOT EXISTS idx_control_commands_idempotency
                ON control_commands(idempotency_key);
            CREATE INDEX IF NOT EXISTS idx_control_commands_created_at
                ON control_commands(created_at);
        """)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_command(
        self,
        command_id: str,
        command_type: ControlCommandType,
        status: ControlCommandStatus = ControlCommandStatus.PENDING,
        requested_by: str | None = None,
        reason: str | None = None,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> ControlCommand:
        """Create a new control command and persist it."""
        now = datetime.now(timezone.utc)
        cmd = ControlCommand(
            command_id=command_id,
            command_type=command_type,
            status=status,
            requested_by=requested_by,
            reason=reason,
            payload=payload or {},
            idempotency_key=idempotency_key,
            created_at=now,
        )
        self._conn.execute(
            """
            INSERT INTO control_commands
                (command_id, command_type, status, requested_by, reason,
                 payload_json, idempotency_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cmd.command_id,
                cmd.command_type.value,
                cmd.status.value,
                cmd.requested_by,
                cmd.reason,
                self._json_dumps(cmd.payload),
                cmd.idempotency_key,
                cmd.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return cmd

    def get_command(self, command_id: str) -> ControlCommand | None:
        """Retrieve a command by ID."""
        row = self._conn.execute(
            "SELECT * FROM control_commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_command(row)

    def list_commands(
        self,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ControlCommand]:
        """List commands, optionally filtered by status."""
        if status is not None:
            rows = self._conn.execute(
                "SELECT * FROM control_commands WHERE status = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM control_commands "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_command(r) for r in rows]

    def list_pending_commands(self, limit: int = 100) -> list[ControlCommand]:
        """List pending commands ordered by creation time."""
        return self.list_commands(status=ControlCommandStatus.PENDING.value, limit=limit)

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def mark_accepted(self, command_id: str) -> ControlCommand:
        """Mark a pending command as accepted."""
        cmd = self._transition(
            command_id,
            allowed={ControlCommandStatus.PENDING},
            new_status=ControlCommandStatus.ACCEPTED,
            timestamp_field="accepted_at",
        )
        return cmd

    def mark_running(self, command_id: str) -> ControlCommand:
        """Mark an accepted command as running."""
        return self._transition(
            command_id,
            allowed={ControlCommandStatus.ACCEPTED},
            new_status=ControlCommandStatus.RUNNING,
        )

    def mark_completed(self, command_id: str) -> ControlCommand:
        """Mark a running command as completed."""
        return self._transition(
            command_id,
            allowed={ControlCommandStatus.RUNNING},
            new_status=ControlCommandStatus.COMPLETED,
            timestamp_field="completed_at",
        )

    def mark_failed(self, command_id: str, error: dict[str, Any]) -> ControlCommand:
        """Mark a running command as failed with error details."""
        return self._transition(
            command_id,
            allowed={ControlCommandStatus.RUNNING},
            new_status=ControlCommandStatus.FAILED,
            timestamp_field="completed_at",
            error=error,
        )

    def expire_old_commands(self, max_age_seconds: int) -> int:
        """Expire pending commands older than max_age_seconds.

        Returns the number of expired commands.
        """
        cutoff = datetime.now(timezone.utc).timestamp() - max_age_seconds
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        cursor = self._conn.execute(
            "UPDATE control_commands SET status = ? "
            "WHERE status = ? AND created_at < ?",
            (
                ControlCommandStatus.EXPIRED.value,
                ControlCommandStatus.PENDING.value,
                cutoff_iso,
            ),
        )
        self._conn.commit()
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _transition(
        self,
        command_id: str,
        allowed: set[ControlCommandStatus],
        new_status: ControlCommandStatus,
        timestamp_field: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> ControlCommand:
        """Execute a state transition if the current status is in allowed."""
        current = self.get_command(command_id)
        if current is None:
            raise KeyError(f"Command not found: {command_id}")
        if current.status in _TERMINAL_STATUSES:
            raise ValueError(
                f"Command {command_id} is in terminal state {current.status.value}, "
                f"cannot transition to {new_status.value}"
            )
        if current.status not in allowed:
            raise ValueError(
                f"Command {command_id} is in status {current.status.value}, "
                f"expected one of {[s.value for s in allowed]}"
            )
        now_iso = datetime.now(timezone.utc).isoformat()
        sets = ["status = ?"]
        params: list[Any] = [new_status.value]
        if timestamp_field is not None:
            sets.append(f"{timestamp_field} = ?")
            params.append(now_iso)
        if error is not None:
            sets.append("error_json = ?")
            params.append(self._json_dumps(error))
        params.append(command_id)
        self._conn.execute(
            f"UPDATE control_commands SET {', '.join(sets)} WHERE command_id = ?",
            params,
        )
        self._conn.commit()
        updated = self.get_command(command_id)
        if updated is None:
            raise RuntimeError(f"Command disappeared after transition: {command_id}")
        return updated

    def _row_to_command(self, row: sqlite3.Row) -> ControlCommand:
        return ControlCommand(
            command_id=row["command_id"],
            command_type=ControlCommandType(row["command_type"]),
            status=ControlCommandStatus(row["status"]),
            requested_by=row["requested_by"],
            reason=row["reason"],
            payload=self._json_loads(row["payload_json"]),
            idempotency_key=row["idempotency_key"],
            created_at=datetime.fromisoformat(row["created_at"]),
            accepted_at=(
                datetime.fromisoformat(row["accepted_at"])
                if row["accepted_at"]
                else None
            ),
            completed_at=(
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"]
                else None
            ),
            error=(
                self._json_loads(row["error_json"])
                if row["error_json"]
                else None
            ),
        )

    @staticmethod
    def _json_dumps(data: Any) -> str:
        import json
        return json.dumps(data, default=str)

    @staticmethod
    def _json_loads(text: str) -> Any:
        import json
        return json.loads(text)
