"""SQLiteApprovalStore — persistent approval storage.

Extends the ApprovalStore protocol with SQLite backing.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from agent_app.governance.approval import (
    ApprovalRequest,
    ApprovalStatus,
    InMemoryApprovalStore,
)

if TYPE_CHECKING:
    from agent_app.governance.approval import ApprovalStore


class SQLiteApprovalStore:
    """SQLite-backed approval store.

    Persists approval requests to a SQLite database file.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str = ".agent_app/approvals.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approvals (
                approval_id   TEXT PRIMARY KEY,
                run_id        TEXT NOT NULL,
                agent_name    TEXT,
                tool_name     TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                risk_level    TEXT NOT NULL,
                requested_by  TEXT,
                tenant_id     TEXT,
                status        TEXT NOT NULL,
                reason        TEXT,
                created_at    TEXT NOT NULL,
                resolved_at   TEXT,
                resolved_by   TEXT
            )
            """
        )
        columns = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(approvals)").fetchall()
        }
        if "decision_note" not in columns:
            self._conn.execute("ALTER TABLE approvals ADD COLUMN decision_note TEXT")
        if "expires_at" not in columns:
            self._conn.execute("ALTER TABLE approvals ADD COLUMN expires_at TEXT")
        if "metadata_json" not in columns:
            self._conn.execute("ALTER TABLE approvals ADD COLUMN metadata_json TEXT DEFAULT '{}'")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_approvals_tenant ON approvals(tenant_id)"
        )
        self._conn.commit()

    async def create(self, request: ApprovalRequest) -> ApprovalRequest:
        self._conn.execute(
            """
            INSERT INTO approvals
                (approval_id, run_id, agent_name, tool_name, arguments_json,
                 risk_level, requested_by, tenant_id, status, reason, created_at,
                 decision_note, expires_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.approval_id,
                request.run_id,
                request.agent_name,
                request.tool_name,
                json.dumps(request.arguments),
                request.risk_level,
                request.requested_by,
                request.tenant_id,
                request.status,
                request.reason,
                request.created_at.isoformat(),
                request.decision_note,
                request.expires_at.isoformat() if request.expires_at else None,
                json.dumps(request.metadata),
            ),
        )
        self._conn.commit()
        return request

    async def get(self, approval_id: str) -> ApprovalRequest:
        row = self._conn.execute(
            "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Approval '{approval_id}' not found.")
        return self._row_to_approval(row)

    async def approve(
        self,
        approval_id: str,
        approved_by: str,
        reason: str | None = None,
    ) -> ApprovalRequest:
        req = await self.get(approval_id)
        if req.status != "pending":
            raise ValueError(
                f"Cannot approve: approval '{approval_id}' is already {req.status}."
            )
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            UPDATE approvals
            SET status = ?, resolved_at = ?, resolved_by = ?, reason = ?, decision_note = ?
            WHERE approval_id = ?
            """,
            (ApprovalStatus.APPROVED, now, approved_by, reason, reason, approval_id),
        )
        self._conn.commit()
        return await self.get(approval_id)

    async def reject(
        self,
        approval_id: str,
        rejected_by: str,
        reason: str | None = None,
    ) -> ApprovalRequest:
        req = await self.get(approval_id)
        if req.status != "pending":
            raise ValueError(
                f"Cannot reject: approval '{approval_id}' is already {req.status}."
            )
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            UPDATE approvals
            SET status = ?, resolved_at = ?, resolved_by = ?, reason = ?, decision_note = ?
            WHERE approval_id = ?
            """,
            (ApprovalStatus.REJECTED, now, rejected_by, reason, reason, approval_id),
        )
        self._conn.commit()
        return await self.get(approval_id)

    async def list_pending(self, tenant_id: str | None = None) -> list[ApprovalRequest]:
        query = "SELECT * FROM approvals WHERE status = ?"
        params: list = [ApprovalStatus.PENDING]
        if tenant_id is not None:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        query += " ORDER BY created_at"
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_approval(r) for r in rows]

    def close(self) -> None:
        self._conn.close()

    def _row_to_approval(self, row: sqlite3.Row) -> ApprovalRequest:
        from datetime import datetime, timezone

        created_at = datetime.fromisoformat(row["created_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        resolved_at = datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None
        if resolved_at is not None and resolved_at.tzinfo is None:
            resolved_at = resolved_at.replace(tzinfo=timezone.utc)
        expires_at = (
            datetime.fromisoformat(row["expires_at"])
            if "expires_at" in row.keys() and row["expires_at"]
            else None
        )
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return ApprovalRequest(
            approval_id=row["approval_id"],
            run_id=row["run_id"],
            agent_name=row["agent_name"],
            tool_name=row["tool_name"],
            arguments=json.loads(row["arguments_json"]),
            risk_level=row["risk_level"],
            requested_by=row["requested_by"],
            tenant_id=row["tenant_id"],
            status=row["status"],
            reason=row["reason"],
            decision_note=(
                row["decision_note"] if "decision_note" in row.keys() else row["reason"]
            ),
            expires_at=expires_at,
            metadata=(
                json.loads(row["metadata_json"])
                if "metadata_json" in row.keys() and row["metadata_json"]
                else {}
            ),
            created_at=created_at,
            resolved_at=resolved_at,
            resolved_by=row["resolved_by"],
        )
