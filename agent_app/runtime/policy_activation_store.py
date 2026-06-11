"""Policy activation store -- persists environment-specific policy activations."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from agent_app.governance.policy_activation import PolicyActivation, PolicyActivationStatus

try:
    from typing import runtime_checkable
except ImportError:
    def runtime_checkable(cls):  # type: ignore[misc]
        return cls


@runtime_checkable
class PolicyActivationStore(Protocol):
    """Protocol for persisting policy activations."""
    async def activate(self, activation: PolicyActivation) -> PolicyActivation: ...
    async def get(self, activation_id: str) -> PolicyActivation | None: ...
    async def get_active(self, environment: str) -> PolicyActivation | None: ...
    async def list(self, environment: str | None = None) -> list[PolicyActivation]: ...
    async def mark_rolled_back(self, activation_id: str, rolled_back_by: str) -> PolicyActivation: ...


class InMemoryPolicyActivationStore:
    """In-memory policy activation store for testing and development."""
    def __init__(self) -> None:
        self._activations: dict[str, PolicyActivation] = {}
        self._order: list[str] = []

    async def activate(self, activation: PolicyActivation) -> PolicyActivation:
        for aid in self._order:
            existing = self._activations.get(aid)
            if (existing is not None and existing.environment == activation.environment
                    and existing.status == PolicyActivationStatus.ACTIVE
                    and existing.activation_id != activation.activation_id):
                existing.status = PolicyActivationStatus.SUPERSEDED
                existing.superseded_at = datetime.now(timezone.utc)
                existing.superseded_by_activation_id = activation.activation_id
                self._activations[aid] = existing
        if activation.activation_id not in self._activations:
            self._order.append(activation.activation_id)
        self._activations[activation.activation_id] = activation
        return activation

    async def get(self, activation_id: str) -> PolicyActivation | None:
        return self._activations.get(activation_id)

    async def get_active(self, environment: str) -> PolicyActivation | None:
        for aid in reversed(self._order):
            a = self._activations.get(aid)
            if a and a.environment == environment and a.status == PolicyActivationStatus.ACTIVE:
                return a
        return None

    async def list(self, environment: str | None = None) -> list[PolicyActivation]:
        results = []
        for aid in reversed(self._order):
            a = self._activations.get(aid)
            if a is None:
                continue
            if environment is not None and a.environment != environment:
                continue
            results.append(a)
        return results

    async def mark_rolled_back(self, activation_id: str, rolled_back_by: str) -> PolicyActivation:
        activation = self._activations.get(activation_id)
        if activation is None:
            raise KeyError(f"Activation '{activation_id}' not found in store.")
        activation.status = PolicyActivationStatus.ROLLED_BACK
        activation.superseded_at = datetime.now(timezone.utc)
        self._activations[activation_id] = activation
        return activation


class SQLitePolicyActivationStore:
    """SQLite-backed policy activation store."""
    def __init__(self, db_path: str = ".agent_app/policy_activations.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS policy_activations (
                activation_id TEXT PRIMARY KEY, environment TEXT NOT NULL, bundle_id TEXT NOT NULL,
                config_hash TEXT NOT NULL, promotion_id TEXT, activated_by TEXT NOT NULL,
                status TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL,
                superseded_at TEXT, superseded_by_activation_id TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_activations_env ON policy_activations(environment);
            CREATE INDEX IF NOT EXISTS idx_activations_status ON policy_activations(status);
        """)
        self._conn.commit()

    async def activate(self, activation: PolicyActivation) -> PolicyActivation:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE policy_activations SET status=?, superseded_at=?, superseded_by_activation_id=?
               WHERE environment=? AND status=? AND activation_id!=?""",
            (PolicyActivationStatus.SUPERSEDED.value, now, activation.activation_id,
             activation.environment, PolicyActivationStatus.ACTIVE.value, activation.activation_id))
        self._conn.execute(
            """INSERT OR REPLACE INTO policy_activations
               (activation_id, environment, bundle_id, config_hash, promotion_id,
                activated_by, status, reason, created_at, superseded_at, superseded_by_activation_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (activation.activation_id, activation.environment, activation.bundle_id,
             activation.config_hash, activation.promotion_id, activation.activated_by,
             activation.status.value, activation.reason, activation.created_at.isoformat(),
             None, None))
        self._conn.commit()
        return activation

    async def get(self, activation_id: str) -> PolicyActivation | None:
        row = self._conn.execute("SELECT * FROM policy_activations WHERE activation_id = ?", (activation_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_activation(row)

    async def get_active(self, environment: str) -> PolicyActivation | None:
        row = self._conn.execute(
            "SELECT * FROM policy_activations WHERE environment=? AND status=? ORDER BY created_at DESC LIMIT 1",
            (environment, PolicyActivationStatus.ACTIVE.value)).fetchone()
        if row is None:
            return None
        return self._row_to_activation(row)

    async def list(self, environment: str | None = None) -> list[PolicyActivation]:
        if environment is not None:
            rows = self._conn.execute("SELECT * FROM policy_activations WHERE environment=? ORDER BY created_at DESC", (environment,)).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM policy_activations ORDER BY created_at DESC").fetchall()
        return [self._row_to_activation(row) for row in rows]

    async def mark_rolled_back(self, activation_id: str, rolled_back_by: str) -> PolicyActivation:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute("UPDATE policy_activations SET status=?, superseded_at=? WHERE activation_id=?",
                           (PolicyActivationStatus.ROLLED_BACK.value, now, activation_id))
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM policy_activations WHERE activation_id=?", (activation_id,)).fetchone()
        if row is None:
            raise KeyError(f"Activation '{activation_id}' not found in store.")
        return self._row_to_activation(row)

    def _row_to_activation(self, row: sqlite3.Row) -> PolicyActivation:
        data = dict(row)
        for ts_field in ("created_at", "superseded_at"):
            val = data.get(ts_field)
            data[ts_field] = datetime.fromisoformat(val) if val else None
        return PolicyActivation(**data)

    def close(self) -> None:
        self._conn.close()


def create_policy_activation_store(store_type: str = "memory", db_path: str | None = None) -> PolicyActivationStore:
    if store_type == "memory":
        return InMemoryPolicyActivationStore()
    if store_type == "sqlite":
        return SQLitePolicyActivationStore(db_path=db_path or ".agent_app/policy_activations.db")
    raise ValueError(f"Unknown activation store type '{store_type}'. Supported: 'memory', 'sqlite'.")
