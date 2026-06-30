"""Persistent approval store for daemon operator control.

Phase 63: Persistent Approval / Control Plane — SQLite-backed store for
persistent approval requests that allow operators to approve or reject
daemon actions with durable audit trail.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class PersistentApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


_TERMINAL_APPROVAL_STATUSES = {
    PersistentApprovalStatus.APPROVED,
    PersistentApprovalStatus.REJECTED,
    PersistentApprovalStatus.EXPIRED,
}


class PersistentApprovalRequest(BaseModel):
    """Persistent approval request for daemon operator control."""

    approval_id: str
    run_id: str | None = None
    daemon_id: str | None = None
    approval_type: str
    status: PersistentApprovalStatus
    requested_by: str | None = None
    resolved_by: str | None = None
    reason: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    resolved_at: datetime | None = None
    expires_at: datetime | None = None


class PersistentApprovalStore:
    """SQLite-backed persistent store for approval requests."""

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
            CREATE TABLE IF NOT EXISTS approval_requests (
                approval_id TEXT PRIMARY KEY,
                run_id TEXT,
                daemon_id TEXT,
                approval_type TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_by TEXT,
                resolved_by TEXT,
                reason TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                expires_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_approval_requests_status
                ON approval_requests(status);
            CREATE INDEX IF NOT EXISTS idx_approval_requests_daemon_id
                ON approval_requests(daemon_id);
            CREATE INDEX IF NOT EXISTS idx_approval_requests_expires_at
                ON approval_requests(expires_at);
        """)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        approval_id: str,
        approval_type: str,
        requested_by: str | None = None,
        reason: str | None = None,
        payload: dict[str, Any] | None = None,
        run_id: str | None = None,
        daemon_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> PersistentApprovalRequest:
        """Create a new pending approval request."""
        now = datetime.now(timezone.utc)
        approval = PersistentApprovalRequest(
            approval_id=approval_id,
            run_id=run_id,
            daemon_id=daemon_id,
            approval_type=approval_type,
            status=PersistentApprovalStatus.PENDING,
            requested_by=requested_by,
            reason=reason,
            payload=payload or {},
            created_at=now,
            expires_at=expires_at,
        )
        self._conn.execute(
            """
            INSERT INTO approval_requests
                (approval_id, run_id, daemon_id, approval_type, status,
                 requested_by, resolved_by, reason, payload_json,
                 created_at, resolved_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval.approval_id,
                approval.run_id,
                approval.daemon_id,
                approval.approval_type,
                approval.status.value,
                approval.requested_by,
                None,
                approval.reason,
                self._json_dumps(approval.payload),
                approval.created_at.isoformat(),
                None,
                approval.expires_at.isoformat() if approval.expires_at else None,
            ),
        )
        self._conn.commit()
        return approval

    def get(self, approval_id: str) -> PersistentApprovalRequest | None:
        """Retrieve an approval by ID."""
        row = self._conn.execute(
            "SELECT * FROM approval_requests WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_approval(row)

    def list_pending(self, limit: int = 100) -> list[PersistentApprovalRequest]:
        """List pending approval requests ordered by creation time."""
        rows = self._conn.execute(
            "SELECT * FROM approval_requests WHERE status = ? "
            "ORDER BY created_at ASC LIMIT ?",
            (PersistentApprovalStatus.PENDING.value, limit),
        ).fetchall()
        return [self._row_to_approval(r) for r in rows]

    def list_by_daemon(self, daemon_id: str, limit: int = 100) -> list[PersistentApprovalRequest]:
        """List approvals for a specific daemon."""
        rows = self._conn.execute(
            "SELECT * FROM approval_requests WHERE daemon_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (daemon_id, limit),
        ).fetchall()
        return [self._row_to_approval(r) for r in rows]

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def approve(
        self,
        approval_id: str,
        approved_by: str,
        reason: str | None = None,
    ) -> PersistentApprovalRequest:
        """Approve a pending approval request."""
        approval = self._transition(
            approval_id,
            allowed={PersistentApprovalStatus.PENDING},
            new_status=PersistentApprovalStatus.APPROVED,
            resolved_by=approved_by,
            reason=reason,
            timestamp_field="resolved_at",
        )
        return approval

    def reject(
        self,
        approval_id: str,
        rejected_by: str,
        reason: str | None = None,
    ) -> PersistentApprovalRequest:
        """Reject a pending approval request."""
        approval = self._transition(
            approval_id,
            allowed={PersistentApprovalStatus.PENDING},
            new_status=PersistentApprovalStatus.REJECTED,
            resolved_by=rejected_by,
            reason=reason,
            timestamp_field="resolved_at",
        )
        return approval

    def expire_old(self, now: datetime | None = None) -> int:
        """Expire pending approvals past their expires_at time.

        Returns the number of expired approvals.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        cursor = self._conn.execute(
            "UPDATE approval_requests SET status = ? "
            "WHERE status = ? AND expires_at IS NOT NULL AND expires_at < ?",
            (
                PersistentApprovalStatus.EXPIRED.value,
                PersistentApprovalStatus.PENDING.value,
                now.isoformat(),
            ),
        )
        self._conn.commit()
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _transition(
        self,
        approval_id: str,
        allowed: set[PersistentApprovalStatus],
        new_status: PersistentApprovalStatus,
        resolved_by: str,
        reason: str | None = None,
        timestamp_field: str | None = None,
    ) -> PersistentApprovalRequest:
        """Execute a state transition if the current status is in allowed."""
        current = self.get(approval_id)
        if current is None:
            raise KeyError(f"Approval not found: {approval_id}")
        if current.status in _TERMINAL_APPROVAL_STATUSES:
            raise ValueError(
                f"Approval {approval_id} is in terminal state {current.status.value}, "
                f"cannot transition to {new_status.value}"
            )
        if current.status not in allowed:
            raise ValueError(
                f"Approval {approval_id} is in status {current.status.value}, "
                f"expected one of {[s.value for s in allowed]}"
            )
        now_iso = datetime.now(timezone.utc).isoformat()
        sets = ["status = ?", "resolved_by = ?"]
        params: list[Any] = [new_status.value, resolved_by]
        if reason is not None:
            sets.append("reason = ?")
            params.append(reason)
        if timestamp_field is not None:
            sets.append(f"{timestamp_field} = ?")
            params.append(now_iso)
        params.append(approval_id)
        self._conn.execute(
            f"UPDATE approval_requests SET {', '.join(sets)} WHERE approval_id = ?",
            params,
        )
        self._conn.commit()
        updated = self.get(approval_id)
        if updated is None:
            raise RuntimeError(f"Approval disappeared after transition: {approval_id}")
        return updated

    def _row_to_approval(self, row: sqlite3.Row) -> PersistentApprovalRequest:
        return PersistentApprovalRequest(
            approval_id=row["approval_id"],
            run_id=row["run_id"],
            daemon_id=row["daemon_id"],
            approval_type=row["approval_type"],
            status=PersistentApprovalStatus(row["status"]),
            requested_by=row["requested_by"],
            resolved_by=row["resolved_by"],
            reason=row["reason"],
            payload=self._json_loads(row["payload_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=(
                datetime.fromisoformat(row["resolved_at"])
                if row["resolved_at"]
                else None
            ),
            expires_at=(
                datetime.fromisoformat(row["expires_at"])
                if row["expires_at"]
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
