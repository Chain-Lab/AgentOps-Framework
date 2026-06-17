"""Release gate requirement store — persistence for ReleaseGateRequirement records.

Phase 42: Policy Release Automation and Simulation Gate Enforcement.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from agent_app.governance.policy_release_gate import (
    ReleaseGateRequirement,
    ReleaseGateRequirementStatus,
)

try:
    from typing import runtime_checkable
except ImportError:
    def runtime_checkable(cls):  # type: ignore[misc]
        return cls


# ---------------------------------------------------------------------------
# Store protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class ReleaseGateRequirementStore(Protocol):
    """Protocol for persisting release gate requirements."""

    async def create(self, requirement: ReleaseGateRequirement) -> ReleaseGateRequirement:
        """Create a requirement. Overwrites if source_type+source_id already exists."""
        ...

    async def get(self, requirement_id: str) -> ReleaseGateRequirement | None:
        """Retrieve a requirement by ID. Returns None if not found."""
        ...

    async def get_for_source(self, source_type: str, source_id: str) -> ReleaseGateRequirement | None:
        """Retrieve a requirement by source_type and source_id. Returns None if not found."""
        ...

    async def update(self, requirement: ReleaseGateRequirement) -> ReleaseGateRequirement:
        """Update an existing requirement. Raises KeyError if not found."""
        ...

    async def list(
        self,
        source_type: str | None = None,
        status: ReleaseGateRequirementStatus | None = None,
    ) -> list[ReleaseGateRequirement]:
        """List requirements, optionally filtered by source_type and/or status."""
        ...


# ---------------------------------------------------------------------------
# InMemoryReleaseGateRequirementStore
# ---------------------------------------------------------------------------

class InMemoryReleaseGateRequirementStore:
    """In-memory release gate requirement store for testing and development."""

    def __init__(self) -> None:
        self._requirements: dict[str, ReleaseGateRequirement] = {}
        self._source_index: dict[str, str] = {}  # (source_type, source_id) -> requirement_id

    async def create(self, requirement: ReleaseGateRequirement) -> ReleaseGateRequirement:
        """Create a requirement. Overwrites if source_type+source_id already exists."""
        source_key = (requirement.source_type, requirement.source_id)
        if source_key in self._source_index:
            old_id = self._source_index[source_key]
            if old_id in self._requirements:
                del self._requirements[old_id]
        self._source_index[source_key] = requirement.requirement_id
        self._requirements[requirement.requirement_id] = requirement
        return requirement

    async def get(self, requirement_id: str) -> ReleaseGateRequirement | None:
        """Retrieve a requirement by ID."""
        return self._requirements.get(requirement_id)

    async def get_for_source(self, source_type: str, source_id: str) -> ReleaseGateRequirement | None:
        """Retrieve a requirement by source_type and source_id."""
        source_key = (source_type, source_id)
        rid = self._source_index.get(source_key)
        if rid is None:
            return None
        return self._requirements.get(rid)

    async def update(self, requirement: ReleaseGateRequirement) -> ReleaseGateRequirement:
        """Update an existing requirement."""
        if requirement.requirement_id not in self._requirements:
            raise KeyError(f"ReleaseGateRequirement '{requirement.requirement_id}' not found")
        source_key = (requirement.source_type, requirement.source_id)
        self._source_index[source_key] = requirement.requirement_id
        self._requirements[requirement.requirement_id] = requirement
        return requirement

    async def list(
        self,
        source_type: str | None = None,
        status: ReleaseGateRequirementStatus | None = None,
    ) -> list[ReleaseGateRequirement]:
        """List requirements, optionally filtered by source_type and/or status."""
        results: list[ReleaseGateRequirement] = []
        for req in self._requirements.values():
            if source_type is not None and req.source_type != source_type:
                continue
            if status is not None and req.status != status:
                continue
            results.append(req)
        return results


# ---------------------------------------------------------------------------
# SQLiteReleaseGateRequirementStore
# ---------------------------------------------------------------------------

class SQLiteReleaseGateRequirementStore:
    """SQLite-backed release gate requirement store.

    Persists release gate requirements to a SQLite database file.
    Survives process restarts and can be shared across instances.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str = ".agent_app/policy_release_gate_requirements.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS policy_release_gate_requirements (
                requirement_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                gate_result_id TEXT,
                simulation_id TEXT,
                required INTEGER NOT NULL,
                status TEXT NOT NULL,
                max_age_seconds INTEGER,
                created_at TEXT NOT NULL,
                satisfied_at TEXT,
                metadata_json TEXT NOT NULL,
                UNIQUE(source_type, source_id)
            );

            CREATE INDEX IF NOT EXISTS idx_rgr_source
                ON policy_release_gate_requirements(source_type, source_id);
            CREATE INDEX IF NOT EXISTS idx_rgr_status
                ON policy_release_gate_requirements(status);
        """)
        self._conn.commit()

    async def create(self, requirement: ReleaseGateRequirement) -> ReleaseGateRequirement:
        """Create a requirement. Uses INSERT OR REPLACE to handle UNIQUE(source_type, source_id)."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO policy_release_gate_requirements
                (requirement_id, source_type, source_id, gate_result_id,
                 simulation_id, required, status, max_age_seconds,
                 created_at, satisfied_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                requirement.requirement_id,
                requirement.source_type,
                requirement.source_id,
                requirement.gate_result_id,
                requirement.simulation_id,
                1 if requirement.required else 0,
                requirement.status.value,
                requirement.max_age_seconds,
                requirement.created_at.isoformat(),
                requirement.satisfied_at.isoformat() if requirement.satisfied_at else None,
                json.dumps(requirement.metadata),
            ),
        )
        self._conn.commit()
        return requirement

    async def get(self, requirement_id: str) -> ReleaseGateRequirement | None:
        """Retrieve a requirement by ID."""
        row = self._conn.execute(
            "SELECT * FROM policy_release_gate_requirements WHERE requirement_id = ?",
            (requirement_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_requirement(row)

    async def get_for_source(self, source_type: str, source_id: str) -> ReleaseGateRequirement | None:
        """Retrieve a requirement by source_type and source_id."""
        row = self._conn.execute(
            "SELECT * FROM policy_release_gate_requirements WHERE source_type = ? AND source_id = ?",
            (source_type, source_id),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_requirement(row)

    async def update(self, requirement: ReleaseGateRequirement) -> ReleaseGateRequirement:
        """Update an existing requirement. Raises KeyError if not found."""
        existing = self._conn.execute(
            "SELECT requirement_id FROM policy_release_gate_requirements WHERE requirement_id = ?",
            (requirement.requirement_id,),
        ).fetchone()
        if existing is None:
            raise KeyError(f"ReleaseGateRequirement '{requirement.requirement_id}' not found")
        self._conn.execute(
            """
            UPDATE policy_release_gate_requirements
            SET source_type=?, source_id=?, gate_result_id=?,
                simulation_id=?, required=?, status=?, max_age_seconds=?,
                created_at=?, satisfied_at=?, metadata_json=?
            WHERE requirement_id=?
            """,
            (
                requirement.source_type,
                requirement.source_id,
                requirement.gate_result_id,
                requirement.simulation_id,
                1 if requirement.required else 0,
                requirement.status.value,
                requirement.max_age_seconds,
                requirement.created_at.isoformat(),
                requirement.satisfied_at.isoformat() if requirement.satisfied_at else None,
                json.dumps(requirement.metadata),
                requirement.requirement_id,
            ),
        )
        self._conn.commit()
        return requirement

    async def list(
        self,
        source_type: str | None = None,
        status: ReleaseGateRequirementStatus | None = None,
    ) -> list[ReleaseGateRequirement]:
        """List requirements, optionally filtered by source_type and/or status."""
        clauses: list[str] = []
        params: list[object] = []
        if source_type is not None:
            clauses.append("source_type=?")
            params.append(source_type)
        if status is not None:
            clauses.append("status=?")
            params.append(status.value)
        where = ""
        if clauses:
            where = " WHERE " + " AND ".join(clauses)
        sql = f"SELECT * FROM policy_release_gate_requirements{where} ORDER BY created_at ASC"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_requirement(row) for row in rows]

    def _row_to_requirement(self, row: sqlite3.Row) -> ReleaseGateRequirement:
        """Convert a database row to ReleaseGateRequirement."""
        data = dict(row)
        data["status"] = ReleaseGateRequirementStatus(data["status"])
        data["required"] = bool(data.pop("required"))
        data["metadata"] = json.loads(data.pop("metadata_json", "{}"))
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        if data["created_at"].tzinfo is None:
            data["created_at"] = data["created_at"].replace(tzinfo=timezone.utc)
        satisfied_at = data.get("satisfied_at")
        if satisfied_at is not None:
            data["satisfied_at"] = datetime.fromisoformat(satisfied_at)
            if data["satisfied_at"].tzinfo is None:
                data["satisfied_at"] = data["satisfied_at"].replace(tzinfo=timezone.utc)
        return ReleaseGateRequirement(**data)

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------

def create_release_gate_requirement_store(
    store_type: str = "memory",
    path: str | None = None,
) -> ReleaseGateRequirementStore:
    """Factory function to create a ReleaseGateRequirementStore.

    Args:
        store_type: "memory" or "sqlite".
        path: Path for SQLite store (ignored for memory).

    Returns:
        A ReleaseGateRequirementStore implementation.

    Raises:
        ValueError: If store_type is unknown.
    """
    if store_type == "memory":
        return InMemoryReleaseGateRequirementStore()
    if store_type == "sqlite":
        return SQLiteReleaseGateRequirementStore(
            db_path=path or ".agent_app/policy_release_gate_requirements.db"
        )
    raise ValueError(
        f"Unknown release gate requirement store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )
