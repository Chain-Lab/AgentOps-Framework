"""Ring activation assignment store -- persists ring-to-activation mappings."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from agent_app.governance.policy_ring_assignment import (
    RingActivationAssignment,
    RingActivationAssignmentStatus,
)

try:
    from typing import runtime_checkable
except ImportError:
    def runtime_checkable(cls):  # type: ignore[misc]
        return cls


@runtime_checkable
class RingActivationAssignmentStore(Protocol):
    """Protocol for persisting ring activation assignments."""
    async def assign(self, assignment: RingActivationAssignment) -> RingActivationAssignment: ...
    async def get(self, assignment_id: str) -> RingActivationAssignment | None: ...
    async def get_active(self, environment: str, ring_name: str) -> RingActivationAssignment | None: ...
    async def list(self, environment: str | None = None, ring_name: str | None = None) -> list[RingActivationAssignment]: ...
    async def disable_active(self, environment: str, ring_name: str, disabled_by: str, reason: str | None = None) -> RingActivationAssignment | None: ...


class InMemoryRingActivationAssignmentStore:
    """In-memory ring activation assignment store."""
    def __init__(self) -> None:
        self._assignments: dict[str, RingActivationAssignment] = {}

    async def assign(self, assignment: RingActivationAssignment) -> RingActivationAssignment:
        # Supersede any current ACTIVE assignment for same environment+ring
        now = datetime.now(timezone.utc)
        for existing in self._assignments.values():
            if (
                existing.environment == assignment.environment
                and existing.ring_name == assignment.ring_name
                and existing.status == RingActivationAssignmentStatus.ACTIVE
            ):
                self._assignments[existing.assignment_id] = existing.model_copy(
                    update={
                        "status": RingActivationAssignmentStatus.SUPERSEDED,
                        "superseded_at": now,
                        "superseded_by_assignment_id": assignment.assignment_id,
                    }
                )
        self._assignments[assignment.assignment_id] = assignment
        return assignment

    async def get(self, assignment_id: str) -> RingActivationAssignment | None:
        return self._assignments.get(assignment_id)

    async def get_active(self, environment: str, ring_name: str) -> RingActivationAssignment | None:
        for assignment in self._assignments.values():
            if (
                assignment.environment == environment
                and assignment.ring_name == ring_name
                and assignment.status == RingActivationAssignmentStatus.ACTIVE
            ):
                return assignment
        return None

    async def list(self, environment: str | None = None, ring_name: str | None = None) -> list[RingActivationAssignment]:
        results = list(self._assignments.values())
        if environment is not None:
            results = [a for a in results if a.environment == environment]
        if ring_name is not None:
            results = [a for a in results if a.ring_name == ring_name]
        return results

    async def disable_active(self, environment: str, ring_name: str, disabled_by: str, reason: str | None = None) -> RingActivationAssignment | None:
        active = await self.get_active(environment, ring_name)
        if active is None:
            return None
        now = datetime.now(timezone.utc)
        updated = active.model_copy(
            update={
                "status": RingActivationAssignmentStatus.DISABLED,
                "superseded_at": now,
            }
        )
        self._assignments[updated.assignment_id] = updated
        return updated


class SQLiteRingActivationAssignmentStore:
    """SQLite-backed ring activation assignment store."""
    def __init__(self, db_path: str = ".agent_app/policy_ring_activation_assignments.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS policy_ring_activation_assignments (
                assignment_id TEXT PRIMARY KEY,
                environment TEXT NOT NULL,
                ring_name TEXT NOT NULL,
                activation_id TEXT NOT NULL,
                bundle_id TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                assigned_by TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL,
                superseded_at TEXT,
                superseded_by_assignment_id TEXT
            );
        """)
        self._conn.commit()

    async def assign(self, assignment: RingActivationAssignment) -> RingActivationAssignment:
        now = datetime.now(timezone.utc)
        # Supersede any current ACTIVE assignment for same environment+ring
        self._conn.execute(
            """UPDATE policy_ring_activation_assignments
               SET status=?, superseded_at=?, superseded_by_assignment_id=?
               WHERE environment=? AND ring_name=? AND status=?""",
            (
                RingActivationAssignmentStatus.SUPERSEDED.value,
                now.isoformat(),
                assignment.assignment_id,
                assignment.environment,
                assignment.ring_name,
                RingActivationAssignmentStatus.ACTIVE.value,
            ),
        )
        self._conn.execute(
            """INSERT OR REPLACE INTO policy_ring_activation_assignments
               (assignment_id, environment, ring_name, activation_id, bundle_id,
                config_hash, status, assigned_by, reason, created_at,
                superseded_at, superseded_by_assignment_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                assignment.assignment_id,
                assignment.environment,
                assignment.ring_name,
                assignment.activation_id,
                assignment.bundle_id,
                assignment.config_hash,
                assignment.status.value,
                assignment.assigned_by,
                assignment.reason,
                assignment.created_at.isoformat(),
                assignment.superseded_at.isoformat() if assignment.superseded_at else None,
                assignment.superseded_by_assignment_id,
            ),
        )
        self._conn.commit()
        return assignment

    async def get(self, assignment_id: str) -> RingActivationAssignment | None:
        row = self._conn.execute(
            "SELECT * FROM policy_ring_activation_assignments WHERE assignment_id=?",
            (assignment_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_assignment(row)

    async def get_active(self, environment: str, ring_name: str) -> RingActivationAssignment | None:
        row = self._conn.execute(
            "SELECT * FROM policy_ring_activation_assignments WHERE environment=? AND ring_name=? AND status=?",
            (environment, ring_name, RingActivationAssignmentStatus.ACTIVE.value),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_assignment(row)

    async def list(self, environment: str | None = None, ring_name: str | None = None) -> list[RingActivationAssignment]:
        conditions: list[str] = []
        params: list[str] = []
        if environment is not None:
            conditions.append("environment=?")
            params.append(environment)
        if ring_name is not None:
            conditions.append("ring_name=?")
            params.append(ring_name)
        if conditions:
            where = " AND ".join(conditions)
            rows = self._conn.execute(
                f"SELECT * FROM policy_ring_activation_assignments WHERE {where} ORDER BY created_at",
                params,
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM policy_ring_activation_assignments ORDER BY created_at"
            ).fetchall()
        return [self._row_to_assignment(row) for row in rows]

    async def disable_active(self, environment: str, ring_name: str, disabled_by: str, reason: str | None = None) -> RingActivationAssignment | None:
        active = await self.get_active(environment, ring_name)
        if active is None:
            return None
        now = datetime.now(timezone.utc)
        self._conn.execute(
            """UPDATE policy_ring_activation_assignments
               SET status=?, superseded_at=?
               WHERE assignment_id=?""",
            (RingActivationAssignmentStatus.DISABLED.value, now.isoformat(), active.assignment_id),
        )
        self._conn.commit()
        return await self.get(active.assignment_id)

    def _row_to_assignment(self, row: sqlite3.Row) -> RingActivationAssignment:
        data = dict(row)
        data["status"] = RingActivationAssignmentStatus(data["status"])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        if data["superseded_at"] is not None:
            data["superseded_at"] = datetime.fromisoformat(data["superseded_at"])
        return RingActivationAssignment(**data)

    def close(self) -> None:
        self._conn.close()


def create_ring_assignment_store(store_type: str = "memory", db_path: str | None = None) -> RingActivationAssignmentStore:
    if store_type == "memory":
        return InMemoryRingActivationAssignmentStore()
    if store_type == "sqlite":
        return SQLiteRingActivationAssignmentStore(db_path=db_path or ".agent_app/policy_ring_activation_assignments.db")
    raise ValueError(f"Unknown ring assignment store type '{store_type}'. Supported: 'memory', 'sqlite'.")
