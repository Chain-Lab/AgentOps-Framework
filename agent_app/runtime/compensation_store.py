"""Compensation state store — persistent tracking of DAG compensation execution.

Phase 16.1: Introduces a dedicated store for compensation execution state,
providing CRUD operations on CompensationExecutionState.  The store is
separate from snapshots and lease state — each has its own persistence layer.

This is NOT a distributed transaction log, NOT Celery, NOT Temporal.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_app.runtime.compensation_state import (
    CompensationActionState,
    CompensationActionStatus,
    CompensationExecutionState,
    CompensationRunStatus,
    deserialize_compensation_state,
    serialize_compensation_state,
)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class CompensationStateStore:
    """Protocol for persisting and querying compensation execution state.

    Implementations track CompensationExecutionState per workflow run,
    with individual CompensationActionState records for each action.
    """

    async def save_compensation_state(
        self,
        state: CompensationExecutionState,
    ) -> CompensationExecutionState:
        """Create or update a compensation execution state.

        Args:
            state: The compensation state to persist.

        Returns:
            The persisted state.
        """
        raise NotImplementedError

    async def get_compensation_state(
        self,
        run_id: str,
    ) -> CompensationExecutionState | None:
        """Get the latest compensation state for a workflow run.

        Args:
            run_id: Parent workflow run ID.

        Returns:
            CompensationExecutionState if found, None otherwise.
        """
        raise NotImplementedError

    async def update_compensation_action(
        self,
        run_id: str,
        action: CompensationActionState,
    ) -> CompensationExecutionState:
        """Update a single compensation action within a run's state.

        Args:
            run_id: Parent workflow run ID.
            action: The action state to update (must have action_id).

        Returns:
            Updated CompensationExecutionState.

        Raises:
            KeyError: If the run or action is not found.
        """
        raise NotImplementedError

    async def list_compensation_states(
        self,
        workflow_name: str | None = None,
    ) -> list[CompensationExecutionState]:
        """List compensation states, optionally filtered by workflow name.

        Args:
            workflow_name: Filter to this workflow name (None = all).

        Returns:
            List of CompensationExecutionState objects.
        """
        raise NotImplementedError

    async def delete_compensation_state(
        self,
        run_id: str,
    ) -> None:
        """Delete all compensation state for a workflow run.

        Args:
            run_id: Parent workflow run ID.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# InMemory implementation
# ---------------------------------------------------------------------------


class InMemoryCompensationStateStore:
    """In-memory compensation state store for development and testing.

    Stores CompensationExecutionState in a dict keyed by run_id.
    """

    def __init__(self) -> None:
        self._states: dict[str, CompensationExecutionState] = {}

    async def save_compensation_state(
        self,
        state: CompensationExecutionState,
    ) -> CompensationExecutionState:
        """Create or update a compensation execution state."""
        state.updated_at = datetime.now(timezone.utc)
        if state.compensation_id not in self._states:
            state.created_at = datetime.now(timezone.utc)
            state.started_at = state.started_at or datetime.now(timezone.utc)
        self._states[state.run_id] = state
        return state

    async def get_compensation_state(
        self,
        run_id: str,
    ) -> CompensationExecutionState | None:
        """Get the latest compensation state for a run."""
        return self._states.get(run_id)

    async def update_compensation_action(
        self,
        run_id: str,
        action: CompensationActionState,
    ) -> CompensationExecutionState:
        """Update a single action within a run's compensation state."""
        state = self._states.get(run_id)
        if state is None:
            raise KeyError(
                f"No compensation state found for run_id '{run_id}'."
            )
        if action.action_id not in state.actions:
            raise KeyError(
                f"Action '{action.action_id}' not found in compensation "
                f"state for run_id '{run_id}'."
            )
        state.actions[action.action_id] = action
        state.updated_at = datetime.now(timezone.utc)
        # Recompute action_order to keep it consistent
        state.action_order = list(state.actions.keys())
        return state

    async def list_compensation_states(
        self,
        workflow_name: str | None = None,
    ) -> list[CompensationExecutionState]:
        """List compensation states, optionally filtered by workflow name."""
        states = list(self._states.values())
        if workflow_name is not None:
            states = [s for s in states if s.workflow_name == workflow_name]
        return states

    async def delete_compensation_state(
        self,
        run_id: str,
    ) -> None:
        """Delete all compensation state for a run."""
        self._states.pop(run_id, None)


# ---------------------------------------------------------------------------
# SQLite implementation
# ---------------------------------------------------------------------------


class SQLiteCompensationStateStore:
    """SQLite-backed compensation state store.

    Persists CompensationExecutionState as JSON in a SQLite table.
    Auto-creates the table on first use.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = str(Path(db_path).expanduser().resolve())
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Create the compensation state table if it doesn't exist."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dag_compensation_states (
                    compensation_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE,
                    workflow_name TEXT,
                    status TEXT NOT NULL,
                    schema_version INTEGER NOT NULL DEFAULT 1,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                    idx_dag_compensation_states_run_id
                ON dag_compensation_states(run_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                    idx_dag_compensation_states_workflow_status
                ON dag_compensation_states(workflow_name, status)
            """)
            conn.commit()
        finally:
            conn.close()

    async def save_compensation_state(
        self,
        state: CompensationExecutionState,
    ) -> CompensationExecutionState:
        """Create or update a compensation execution state."""
        state.updated_at = datetime.now(timezone.utc)
        if state.compensation_id not in self._get_existing_ids(state.run_id):
            state.created_at = datetime.now(timezone.utc)
            state.started_at = state.started_at or datetime.now(timezone.utc)

        state_json = serialize_compensation_state(state)
        now_iso = datetime.now(timezone.utc).isoformat()

        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO dag_compensation_states
                    (compensation_id, run_id, workflow_name, status,
                     schema_version, state_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, COALESCE(
                    (SELECT created_at FROM dag_compensation_states
                     WHERE run_id = ?),
                    ?
                ), ?)
                """,
                (
                    state.compensation_id,
                    state.run_id,
                    state.workflow_name,
                    state.status,
                    state.schema_version,
                    state_json,
                    state.run_id,
                    now_iso,
                    now_iso,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return state

    def _get_existing_ids(self, run_id: str) -> set[str]:
        """Get existing compensation IDs for a run (sync helper)."""
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                "SELECT compensation_id FROM dag_compensation_states WHERE run_id = ?",
                (run_id,),
            ).fetchall()
            return {r[0] for r in rows}
        finally:
            conn.close()

    async def get_compensation_state(
        self,
        run_id: str,
    ) -> CompensationExecutionState | None:
        """Get the latest compensation state for a run."""
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT state_json FROM dag_compensation_states WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            return deserialize_compensation_state(row[0])
        finally:
            conn.close()

    async def update_compensation_action(
        self,
        run_id: str,
        action: CompensationActionState,
    ) -> CompensationExecutionState:
        """Update a single action within a run's compensation state."""
        state = await self.get_compensation_state(run_id)
        if state is None:
            raise KeyError(
                f"No compensation state found for run_id '{run_id}'."
            )
        if action.action_id not in state.actions:
            raise KeyError(
                f"Action '{action.action_id}' not found in compensation "
                f"state for run_id '{run_id}'."
            )
        state.actions[action.action_id] = action
        state.updated_at = datetime.now(timezone.utc)
        state.action_order = list(state.actions.keys())
        # Recompute overall status
        await self._recompute_status(state)
        await self.save_compensation_state(state)
        return state

    async def _recompute_status(
        self, state: CompensationExecutionState
    ) -> None:
        """Recompute overall compensation run status from action states."""
        completed = [
            a for a in state.actions.values()
            if a.status == CompensationActionStatus.COMPLETED.value
        ]
        failed = [
            a for a in state.actions.values()
            if a.status == CompensationActionStatus.FAILED.value
        ]
        running = [
            a for a in state.actions.values()
            if a.status in (
                CompensationActionStatus.RUNNING.value,
                CompensationActionStatus.PENDING.value,
            )
        ]

        if not failed and not running and completed:
            state.status = CompensationRunStatus.COMPLETED.value
            state.completed_at = datetime.now(timezone.utc)
        elif failed and running:
            state.status = CompensationRunStatus.PARTIAL_FAILED.value
        elif failed and not running and not completed:
            state.status = CompensationRunStatus.FAILED.value
            state.completed_at = datetime.now(timezone.utc)

    async def list_compensation_states(
        self,
        workflow_name: str | None = None,
    ) -> list[CompensationExecutionState]:
        """List compensation states, optionally filtered by workflow name."""
        conn = sqlite3.connect(self._db_path)
        try:
            if workflow_name is not None:
                rows = conn.execute(
                    """
                    SELECT state_json FROM dag_compensation_states
                    WHERE workflow_name = ?
                    ORDER BY updated_at ASC
                    """,
                    (workflow_name,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT state_json FROM dag_compensation_states "
                    "ORDER BY updated_at ASC"
                ).fetchall()
            result = []
            for row in rows:
                try:
                    result.append(deserialize_compensation_state(row[0]))
                except Exception:
                    pass  # Skip corrupted entries
            return result
        finally:
            conn.close()

    async def delete_compensation_state(
        self,
        run_id: str,
    ) -> None:
        """Delete all compensation state for a run."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "DELETE FROM dag_compensation_states WHERE run_id = ?",
                (run_id,),
            )
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_compensation_state_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> CompensationStateStore:
    """Create a compensation state store.

    Args:
        store_type: Store type — "memory" or "sqlite".
        db_path: SQLite database path (required when store_type="sqlite").

    Returns:
        A CompensationStateStore implementation.

    Raises:
        ValueError: If store_type is unknown or db_path is missing for sqlite.
    """
    if store_type == "memory":
        return InMemoryCompensationStateStore()
    if store_type == "sqlite":
        if not db_path:
            raise ValueError(
                "db_path is required when store_type='sqlite'. "
                "Provide a path like '.agent_app/dag_compensation.db'."
            )
        return SQLiteCompensationStateStore(db_path=db_path)
    raise ValueError(
        f"Unknown compensation state store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )
