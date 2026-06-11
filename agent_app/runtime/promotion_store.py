"""Promotion request store — persistence for policy promotion requests.

Phase 30 Task 3: PromotionRequestStore protocol with InMemory and SQLite
backends, following the same patterns as Phase 29 stores.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from agent_app.governance.policy_promotion import (
    PromotionRequest,
    PromotionRequestStatus,
)


# ---------------------------------------------------------------------------
# Store protocol
# ---------------------------------------------------------------------------


class PromotionRequestStore(Protocol):
    """Protocol for persisting promotion requests."""

    async def create(self, request: PromotionRequest) -> PromotionRequest:
        """Create a new promotion request. Overwrites if promotion_id exists."""
        ...

    async def get(self, promotion_id: str) -> PromotionRequest | None:
        """Retrieve a promotion request by ID. Returns None if not found."""
        ...

    async def approve(
        self,
        promotion_id: str,
        approved_by: str,
        reason: str | None = None,
    ) -> PromotionRequest:
        """Approve a pending promotion request. No-op if already resolved."""
        ...

    async def reject(
        self,
        promotion_id: str,
        rejected_by: str,
        reason: str | None = None,
    ) -> PromotionRequest:
        """Reject a pending promotion request. No-op if already resolved."""
        ...

    async def mark_executed(
        self,
        promotion_id: str,
        executed_by: str,
    ) -> PromotionRequest:
        """Mark an approved promotion request as executed."""
        ...

    async def list(
        self,
        status: PromotionRequestStatus | None = None,
        tenant_id: str | None = None,
    ) -> list[PromotionRequest]:
        """List promotion requests, optionally filtered by status and tenant_id."""
        ...


# ---------------------------------------------------------------------------
# InMemoryPromotionRequestStore
# ---------------------------------------------------------------------------


class InMemoryPromotionRequestStore:
    """In-memory promotion request store for testing and development."""

    def __init__(self) -> None:
        self._requests: dict[str, PromotionRequest] = {}
        self._order: list[str] = []

    async def create(self, request: PromotionRequest) -> PromotionRequest:
        """Create a new promotion request. Overwrites if promotion_id exists."""
        if request.promotion_id not in self._requests:
            self._order.append(request.promotion_id)
        self._requests[request.promotion_id] = request
        return request

    async def get(self, promotion_id: str) -> PromotionRequest | None:
        """Retrieve a promotion request by ID."""
        return self._requests.get(promotion_id)

    async def approve(
        self,
        promotion_id: str,
        approved_by: str,
        reason: str | None = None,
    ) -> PromotionRequest:
        """Approve a pending promotion request."""
        request = self._requests.get(promotion_id)
        if request is None:
            raise KeyError(
                f"Promotion request '{promotion_id}' not found in store."
            )
        if request.status != PromotionRequestStatus.PENDING:
            # No-op: already resolved
            return request
        request.status = PromotionRequestStatus.APPROVED
        request.resolved_by = approved_by
        request.approval_reason = reason
        request.resolved_at = datetime.now(timezone.utc)
        self._requests[promotion_id] = request
        return request

    async def reject(
        self,
        promotion_id: str,
        rejected_by: str,
        reason: str | None = None,
    ) -> PromotionRequest:
        """Reject a pending promotion request."""
        request = self._requests.get(promotion_id)
        if request is None:
            raise KeyError(
                f"Promotion request '{promotion_id}' not found in store."
            )
        if request.status != PromotionRequestStatus.PENDING:
            # No-op: already resolved
            return request
        request.status = PromotionRequestStatus.REJECTED
        request.resolved_by = rejected_by
        request.rejection_reason = reason
        request.resolved_at = datetime.now(timezone.utc)
        self._requests[promotion_id] = request
        return request

    async def mark_executed(
        self,
        promotion_id: str,
        executed_by: str,
    ) -> PromotionRequest:
        """Mark an approved promotion request as executed."""
        request = self._requests.get(promotion_id)
        if request is None:
            raise KeyError(
                f"Promotion request '{promotion_id}' not found in store."
            )
        if request.status != PromotionRequestStatus.APPROVED:
            # No-op: only APPROVED requests can be marked as executed
            return request
        request.status = PromotionRequestStatus.EXECUTED
        request.executed_by = executed_by
        request.executed_at = datetime.now(timezone.utc)
        self._requests[promotion_id] = request
        return request

    async def list(
        self,
        status: PromotionRequestStatus | None = None,
        tenant_id: str | None = None,
    ) -> list[PromotionRequest]:
        """List promotion requests, optionally filtered."""
        results = []
        for pid in reversed(self._order):
            r = self._requests.get(pid)
            if r is None:
                continue
            if status is not None and r.status != status:
                continue
            if tenant_id is not None and r.tenant_id != tenant_id:
                continue
            results.append(r)
        return results


# ---------------------------------------------------------------------------
# SQLitePromotionRequestStore
# ---------------------------------------------------------------------------


class SQLitePromotionRequestStore:
    """SQLite-backed promotion request store.

    Persists promotion requests to a SQLite database file. Survives process
    restarts and can be shared across instances.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str = ".agent_app/promotion_requests.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS policy_promotion_requests (
                promotion_id TEXT PRIMARY KEY,
                bundle_id TEXT NOT NULL,
                gate_result_id TEXT,
                requested_by TEXT NOT NULL,
                tenant_id TEXT,
                status TEXT NOT NULL,
                reason TEXT,
                approval_reason TEXT,
                rejection_reason TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                resolved_by TEXT,
                executed_at TEXT,
                executed_by TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_promo_requests_status
                ON policy_promotion_requests(status);
            CREATE INDEX IF NOT EXISTS idx_promo_requests_tenant
                ON policy_promotion_requests(tenant_id);
            CREATE INDEX IF NOT EXISTS idx_promo_requests_created
                ON policy_promotion_requests(created_at);
        """)
        self._conn.commit()

    async def create(self, request: PromotionRequest) -> PromotionRequest:
        """Create a new promotion request (INSERT OR REPLACE)."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO policy_promotion_requests
                (promotion_id, bundle_id, gate_result_id, requested_by,
                 tenant_id, status, reason, approval_reason, rejection_reason,
                 created_at, resolved_at, resolved_by, executed_at, executed_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.promotion_id,
                request.bundle_id,
                request.gate_result_id,
                request.requested_by,
                request.tenant_id,
                request.status,
                request.reason,
                request.approval_reason,
                request.rejection_reason,
                request.created_at.isoformat(),
                request.resolved_at.isoformat() if request.resolved_at else None,
                request.resolved_by,
                request.executed_at.isoformat() if request.executed_at else None,
                request.executed_by,
            ),
        )
        self._conn.commit()
        return request

    async def get(self, promotion_id: str) -> PromotionRequest | None:
        """Retrieve a promotion request by ID."""
        row = self._conn.execute(
            "SELECT * FROM policy_promotion_requests WHERE promotion_id = ?",
            (promotion_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_request(row)

    async def approve(
        self,
        promotion_id: str,
        approved_by: str,
        reason: str | None = None,
    ) -> PromotionRequest:
        """Approve a pending promotion request."""
        row = self._conn.execute(
            "SELECT * FROM policy_promotion_requests WHERE promotion_id = ?",
            (promotion_id,),
        ).fetchone()
        if row is None:
            raise KeyError(
                f"Promotion request '{promotion_id}' not found in store."
            )
        current_status = PromotionRequestStatus(row["status"])
        if current_status != PromotionRequestStatus.PENDING:
            # No-op: already resolved, return current state
            return self._row_to_request(row)

        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            UPDATE policy_promotion_requests
            SET status = ?, resolved_by = ?, approval_reason = ?, resolved_at = ?
            WHERE promotion_id = ?
            """,
            (
                PromotionRequestStatus.APPROVED,
                approved_by,
                reason,
                now,
                promotion_id,
            ),
        )
        self._conn.commit()

        updated = self._conn.execute(
            "SELECT * FROM policy_promotion_requests WHERE promotion_id = ?",
            (promotion_id,),
        ).fetchone()
        return self._row_to_request(updated)

    async def reject(
        self,
        promotion_id: str,
        rejected_by: str,
        reason: str | None = None,
    ) -> PromotionRequest:
        """Reject a pending promotion request."""
        row = self._conn.execute(
            "SELECT * FROM policy_promotion_requests WHERE promotion_id = ?",
            (promotion_id,),
        ).fetchone()
        if row is None:
            raise KeyError(
                f"Promotion request '{promotion_id}' not found in store."
            )
        current_status = PromotionRequestStatus(row["status"])
        if current_status != PromotionRequestStatus.PENDING:
            # No-op: already resolved, return current state
            return self._row_to_request(row)

        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            UPDATE policy_promotion_requests
            SET status = ?, resolved_by = ?, rejection_reason = ?, resolved_at = ?
            WHERE promotion_id = ?
            """,
            (
                PromotionRequestStatus.REJECTED,
                rejected_by,
                reason,
                now,
                promotion_id,
            ),
        )
        self._conn.commit()

        updated = self._conn.execute(
            "SELECT * FROM policy_promotion_requests WHERE promotion_id = ?",
            (promotion_id,),
        ).fetchone()
        return self._row_to_request(updated)

    async def mark_executed(
        self,
        promotion_id: str,
        executed_by: str,
    ) -> PromotionRequest:
        """Mark an approved promotion request as executed."""
        row = self._conn.execute(
            "SELECT * FROM policy_promotion_requests WHERE promotion_id = ?",
            (promotion_id,),
        ).fetchone()
        if row is None:
            raise KeyError(
                f"Promotion request '{promotion_id}' not found in store."
            )
        current_status = PromotionRequestStatus(row["status"])
        if current_status != PromotionRequestStatus.APPROVED:
            # No-op: only APPROVED requests can be marked as executed
            return self._row_to_request(row)

        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            UPDATE policy_promotion_requests
            SET status = ?, executed_by = ?, executed_at = ?
            WHERE promotion_id = ?
            """,
            (
                PromotionRequestStatus.EXECUTED,
                executed_by,
                now,
                promotion_id,
            ),
        )
        self._conn.commit()

        updated = self._conn.execute(
            "SELECT * FROM policy_promotion_requests WHERE promotion_id = ?",
            (promotion_id,),
        ).fetchone()
        if updated is None:
            raise KeyError(
                f"Promotion request '{promotion_id}' not found in store."
            )
        return self._row_to_request(updated)

    async def list(
        self,
        status: PromotionRequestStatus | None = None,
        tenant_id: str | None = None,
    ) -> list[PromotionRequest]:
        """List promotion requests, optionally filtered."""
        query = "SELECT * FROM policy_promotion_requests WHERE 1=1"
        params: list = []

        if status is not None:
            query += " AND status = ?"
            params.append(status)

        if tenant_id is not None:
            query += " AND tenant_id = ?"
            params.append(tenant_id)

        query += " ORDER BY created_at DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_request(row) for row in rows]

    def _row_to_request(self, row: sqlite3.Row) -> PromotionRequest:
        """Convert a database row to PromotionRequest."""
        data = dict(row)
        data["status"] = PromotionRequestStatus(data.pop("status"))
        for ts_field in ("created_at", "resolved_at", "executed_at"):
            val = data.get(ts_field)
            data[ts_field] = datetime.fromisoformat(val) if val else None
        return PromotionRequest(**data)

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


def create_promotion_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> PromotionRequestStore:
    """Factory function to create a PromotionRequestStore.

    Args:
        store_type: "memory" or "sqlite".
        db_path: Path for SQLite store (ignored for memory).

    Returns:
        A PromotionRequestStore implementation.

    Raises:
        ValueError: If store_type is unknown.
    """
    if store_type == "memory":
        return InMemoryPromotionRequestStore()
    if store_type == "sqlite":
        return SQLitePromotionRequestStore(
            db_path=db_path or ".agent_app/promotion_requests.db"
        )
    raise ValueError(
        f"Unknown promotion store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )
