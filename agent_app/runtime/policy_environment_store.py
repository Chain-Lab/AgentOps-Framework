"""Policy environment store -- persists environment enable/disable state."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from agent_app.governance.policy_environment import PolicyEnvironmentState, PolicyEnvironmentStatus

try:
    from typing import runtime_checkable
except ImportError:
    def runtime_checkable(cls):  # type: ignore[misc]
        return cls


@runtime_checkable
class PolicyEnvironmentStore(Protocol):
    """Protocol for persisting policy environment states."""
    async def get(self, environment: str) -> PolicyEnvironmentState: ...
    async def disable(self, environment: str, disabled_by: str, reason: str) -> PolicyEnvironmentState: ...
    async def enable(self, environment: str, enabled_by: str, reason: str | None = None) -> PolicyEnvironmentState: ...
    async def list(self) -> list[PolicyEnvironmentState]: ...


class InMemoryPolicyEnvironmentStore:
    """In-memory policy environment store."""
    def __init__(self) -> None:
        self._states: dict[str, PolicyEnvironmentState] = {}

    async def get(self, environment: str) -> PolicyEnvironmentState:
        state = self._states.get(environment)
        if state is not None:
            return state
        return PolicyEnvironmentState(environment=environment)

    async def disable(self, environment: str, disabled_by: str, reason: str) -> PolicyEnvironmentState:
        now = datetime.now(timezone.utc)
        state = PolicyEnvironmentState(
            environment=environment,
            status=PolicyEnvironmentStatus.DISABLED,
            disabled_reason=reason,
            disabled_by=disabled_by,
            disabled_at=now,
            updated_at=now,
        )
        self._states[environment] = state
        return state

    async def enable(self, environment: str, enabled_by: str, reason: str | None = None) -> PolicyEnvironmentState:
        now = datetime.now(timezone.utc)
        state = PolicyEnvironmentState(
            environment=environment,
            status=PolicyEnvironmentStatus.ENABLED,
            enabled_by=enabled_by,
            enabled_at=now,
            updated_at=now,
        )
        self._states[environment] = state
        return state

    async def list(self) -> list[PolicyEnvironmentState]:
        return list(self._states.values())


class SQLitePolicyEnvironmentStore:
    """SQLite-backed policy environment store."""
    def __init__(self, db_path: str = ".agent_app/policy_environments.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS policy_environment_states (
                environment TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                disabled_reason TEXT,
                disabled_by TEXT,
                disabled_at TEXT,
                enabled_by TEXT,
                enabled_at TEXT,
                updated_at TEXT NOT NULL
            );
        """)
        self._conn.commit()

    async def get(self, environment: str) -> PolicyEnvironmentState:
        row = self._conn.execute(
            "SELECT * FROM policy_environment_states WHERE environment=?", (environment,)
        ).fetchone()
        if row is None:
            return PolicyEnvironmentState(environment=environment)
        return self._row_to_state(row)

    async def disable(self, environment: str, disabled_by: str, reason: str) -> PolicyEnvironmentState:
        now = datetime.now(timezone.utc)
        self._conn.execute(
            """INSERT OR REPLACE INTO policy_environment_states
               (environment, status, disabled_reason, disabled_by, disabled_at, enabled_by, enabled_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (environment, PolicyEnvironmentStatus.DISABLED.value, reason, disabled_by,
             now.isoformat(), None, None, now.isoformat()))
        self._conn.commit()
        return await self.get(environment)

    async def enable(self, environment: str, enabled_by: str, reason: str | None = None) -> PolicyEnvironmentState:
        now = datetime.now(timezone.utc)
        self._conn.execute(
            """INSERT OR REPLACE INTO policy_environment_states
               (environment, status, disabled_reason, disabled_by, disabled_at, enabled_by, enabled_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (environment, PolicyEnvironmentStatus.ENABLED.value, None, None, None,
             enabled_by, now.isoformat(), now.isoformat()))
        self._conn.commit()
        return await self.get(environment)

    async def list(self) -> list[PolicyEnvironmentState]:
        rows = self._conn.execute("SELECT * FROM policy_environment_states ORDER BY environment").fetchall()
        return [self._row_to_state(row) for row in rows]

    def _row_to_state(self, row: sqlite3.Row) -> PolicyEnvironmentState:
        data = dict(row)
        for ts_field in ("disabled_at", "enabled_at", "updated_at"):
            val = data.get(ts_field)
            data[ts_field] = datetime.fromisoformat(val) if val else None
        return PolicyEnvironmentState(**data)

    def close(self) -> None:
        self._conn.close()


def create_policy_environment_store(store_type: str = "memory", db_path: str | None = None) -> PolicyEnvironmentStore:
    if store_type == "memory":
        return InMemoryPolicyEnvironmentStore()
    if store_type == "sqlite":
        return SQLitePolicyEnvironmentStore(db_path=db_path or ".agent_app/policy_environments.db")
    raise ValueError(f"Unknown environment store type '{store_type}'. Supported: 'memory', 'sqlite'.")
