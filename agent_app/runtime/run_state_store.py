"""RunStateStore implementations — InMemory and SQLite.

Phase 9: Provides persistent storage for interrupted runs, enabling
framework-level resume capability independent of any specific backend SDK.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_app.core.context import RunContext
from agent_app.core.result import AppRunResult
from agent_app.runtime.run_state import (
    InterruptedRun,
    RunStateStatus,
    RunStateStore,
    _deserialize_run,
    _serialize_run,
)


# ---------------------------------------------------------------------------
# InMemoryRunStateStore
# ---------------------------------------------------------------------------

class InMemoryRunStateStore:
    """In-memory run state store.

    Suitable for development and testing. State is lost when the process
    exits.

    Stores only INTERRUPTED runs. Completed/failed/resumed runs are
    tracked but not returned by list_interrupted().
    """

    def __init__(self) -> None:
        self._runs: dict[str, InterruptedRun] = {}

    async def save_interrupted(self, run: InterruptedRun) -> InterruptedRun:
        """Save or update an interrupted run."""
        existing = self._runs.get(run.run_id)
        if existing is not None:
            # Preserve created_at from existing entry
            run.created_at = existing.created_at
        run.updated_at = _now()
        self._runs[run.run_id] = run
        return run

    async def get(self, run_id: str) -> InterruptedRun:
        """Retrieve a run by ID.

        Raises:
            KeyError: If run_id not found.
        """
        if run_id not in self._runs:
            raise KeyError(f"Run '{run_id}' not found in run state store.")
        return self._runs[run_id]

    async def mark_resumed(self, run_id: str) -> InterruptedRun:
        """Mark a run as resumed."""
        run = await self.get(run_id)
        run.status = RunStateStatus.RESUMED.value
        run.updated_at = _now()
        run.resumed_at = _now()
        self._runs[run_id] = run
        return run

    async def mark_completed(self, run_id: str) -> InterruptedRun:
        """Mark a run as completed."""
        run = await self.get(run_id)
        run.status = RunStateStatus.COMPLETED.value
        run.updated_at = _now()
        self._runs[run_id] = run
        return run

    async def mark_failed(self, run_id: str, error: dict[str, Any]) -> InterruptedRun:
        """Mark a run as failed."""
        run = await self.get(run_id)
        run.status = RunStateStatus.FAILED.value
        run.updated_at = _now()
        run.error = error
        self._runs[run_id] = run
        return run

    async def list_interrupted(self, tenant_id: str | None = None) -> list[InterruptedRun]:
        """List interrupted runs, optionally filtered by tenant."""
        results = [
            run for run in self._runs.values()
            if run.status == RunStateStatus.INTERRUPTED.value
        ]
        if tenant_id is not None:
            results = [
                run for run in results
                if run.context.tenant_id == tenant_id
            ]
        return sorted(results, key=lambda r: r.created_at)


# ---------------------------------------------------------------------------
# SQLiteRunStateStore
# ---------------------------------------------------------------------------

class SQLiteRunStateStore:
    """SQLite-backed run state store.

    Persists run states to a SQLite database file. Survives process restarts
    and can be shared across instances.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str = ".agent_app/run_states.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        """Create the run_states table if it doesn't exist."""
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_states (
                run_id             TEXT PRIMARY KEY,
                status             TEXT NOT NULL,
                agent_name         TEXT,
                workflow_name      TEXT,
                workflow_type      TEXT,
                input              TEXT NOT NULL,
                context_json       TEXT NOT NULL,
                interruptions_json TEXT NOT NULL,
                approval_ids_json  TEXT NOT NULL,
                backend_name       TEXT NOT NULL,
                backend_state_json TEXT NOT NULL,
                result_snapshot_json TEXT,
                error_json         TEXT,
                created_at         TEXT NOT NULL,
                updated_at         TEXT NOT NULL,
                resumed_at         TEXT
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_states_status ON run_states(status)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_states_tenant ON run_states(context_json)"
        )
        self._conn.commit()

    async def save_interrupted(self, run: InterruptedRun) -> InterruptedRun:
        """Save or update a run."""
        existing = self._row_to_run(self._conn.execute(
            "SELECT * FROM run_states WHERE run_id = ?", (run.run_id,)
        ).fetchone())

        if existing is not None:
            run.created_at = existing.created_at

        run.updated_at = _now()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO run_states
                (run_id, status, agent_name, workflow_name, workflow_type,
                 input, context_json, interruptions_json, approval_ids_json,
                 backend_name, backend_state_json, result_snapshot_json,
                 error_json, created_at, updated_at, resumed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.run_id,
                run.status,
                run.agent_name,
                run.workflow_name,
                run.workflow_type,
                run.input,
                json.dumps(run.context.model_dump(mode="json")),
                json.dumps(run.interruptions),
                json.dumps(run.approval_ids),
                run.backend_name,
                json.dumps(run.backend_state),
                json.dumps(run.result_snapshot) if run.result_snapshot else None,
                json.dumps(run.error) if run.error else None,
                run.created_at.isoformat(),
                run.updated_at.isoformat(),
                run.resumed_at.isoformat() if run.resumed_at else None,
            ),
        )
        self._conn.commit()
        return run

    async def get(self, run_id: str) -> InterruptedRun:
        """Retrieve a run by ID.

        Raises:
            KeyError: If run_id not found.
        """
        row = self._conn.execute(
            "SELECT * FROM run_states WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Run '{run_id}' not found in run state store.")
        return self._row_to_run(row)

    async def mark_resumed(self, run_id: str) -> InterruptedRun:
        """Mark a run as resumed."""
        now = _now()
        self._conn.execute(
            """
            UPDATE run_states
            SET status = ?, updated_at = ?, resumed_at = ?
            WHERE run_id = ?
            """,
            (RunStateStatus.RESUMED.value, now.isoformat(), now.isoformat(), run_id),
        )
        self._conn.commit()
        return await self.get(run_id)

    async def mark_completed(self, run_id: str) -> InterruptedRun:
        """Mark a run as completed."""
        now = _now()
        self._conn.execute(
            """
            UPDATE run_states
            SET status = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (RunStateStatus.COMPLETED.value, now.isoformat(), run_id),
        )
        self._conn.commit()
        return await self.get(run_id)

    async def mark_failed(self, run_id: str, error: dict[str, Any]) -> InterruptedRun:
        """Mark a run as failed."""
        now = _now()
        self._conn.execute(
            """
            UPDATE run_states
            SET status = ?, updated_at = ?, error_json = ?
            WHERE run_id = ?
            """,
            (
                RunStateStatus.FAILED.value,
                now.isoformat(),
                json.dumps(error),
                run_id,
            ),
        )
        self._conn.commit()
        return await self.get(run_id)

    async def list_interrupted(self, tenant_id: str | None = None) -> list[InterruptedRun]:
        """List interrupted runs, optionally filtered by tenant."""
        query = "SELECT * FROM run_states WHERE status = ?"
        params: list = [RunStateStatus.INTERRUPTED.value]

        if tenant_id is not None:
            # Filter by tenant_id in context_json
            query += " AND context_json LIKE ?"
            params.append(f'%"tenant_id": "{tenant_id}"%')

        query += " ORDER BY created_at"
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_run(row) for row in rows]

    def _row_to_run(self, row: sqlite3.Row | None) -> InterruptedRun | None:
        """Convert a database row to an InterruptedRun."""
        if row is None:
            return None
        data = dict(row)
        data["context"] = json.loads(data["context_json"])
        data["interruptions"] = json.loads(data["interruptions_json"])
        data["approval_ids"] = json.loads(data["approval_ids_json"])
        data["backend_state"] = json.loads(data["backend_state_json"]) if data["backend_state_json"] else {}
        data["result_snapshot"] = json.loads(data["result_snapshot_json"]) if data["result_snapshot_json"] else None
        data["error"] = json.loads(data["error_json"]) if data["error_json"] else None
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        if data.get("resumed_at"):
            data["resumed_at"] = datetime.fromisoformat(data["resumed_at"])
        else:
            data["resumed_at"] = None
        return InterruptedRun(**data)

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def create_run_state_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> RunStateStore:
    """Factory function to create a RunStateStore.

    Args:
        store_type: "memory" or "sqlite".
        db_path: Path for SQLite store (ignored for memory).

    Returns:
        A RunStateStore implementation.

    Raises:
        ValueError: If store_type is unknown.
    """
    if store_type == "memory":
        return InMemoryRunStateStore()
    if store_type == "sqlite":
        return SQLiteRunStateStore(db_path=db_path or ".agent_app/run_states.db")
    raise ValueError(
        f"Unknown run_state store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )
