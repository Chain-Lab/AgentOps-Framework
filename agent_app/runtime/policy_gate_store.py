"""Policy gate store — persistence for policy gate evaluation results.

Phase 29: stores PolicyGateResult records with InMemory and SQLite backends.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Re-export models for convenience
# ---------------------------------------------------------------------------

from agent_app.governance.policy_gate import (
    PolicyGateResult,
    PolicyGateStatus,
)


# ---------------------------------------------------------------------------
# Gate store protocol
# ---------------------------------------------------------------------------

class PolicyGateStore(Protocol):
    """Protocol for persisting policy gate results."""

    async def save(self, result: PolicyGateResult) -> PolicyGateResult:
        """Save a gate result. Overwrites if gate_result_id exists."""
        ...

    async def get(self, gate_result_id: str) -> PolicyGateResult | None:
        """Retrieve a gate result by ID. Returns None if not found."""
        ...

    async def list(
        self,
        bundle_id: str | None = None,
        limit: int = 50,
    ) -> list[PolicyGateResult]:
        """List gate results, optionally filtered by bundle_id."""
        ...


# ---------------------------------------------------------------------------
# InMemoryPolicyGateStore
# ---------------------------------------------------------------------------

class InMemoryPolicyGateStore:
    """In-memory policy gate result store for testing and development."""

    def __init__(self) -> None:
        self._results: dict[str, PolicyGateResult] = {}
        self._order: list[str] = []

    async def save(self, result: PolicyGateResult) -> PolicyGateResult:
        """Save a gate result."""
        if result.gate_result_id not in self._results:
            self._order.append(result.gate_result_id)
        self._results[result.gate_result_id] = result
        return result

    async def get(self, gate_result_id: str) -> PolicyGateResult | None:
        """Retrieve a gate result by ID."""
        return self._results.get(gate_result_id)

    async def list(
        self,
        bundle_id: str | None = None,
        limit: int = 50,
    ) -> list[PolicyGateResult]:
        """List gate results, optionally filtered by bundle_id."""
        ids = list(reversed(self._order[-limit:]))
        results = []
        for rid in ids:
            r = self._results.get(rid)
            if r is None:
                continue
            if bundle_id is not None and r.bundle_id != bundle_id:
                continue
            results.append(r)
        return results


# ---------------------------------------------------------------------------
# SQLitePolicyGateStore
# ---------------------------------------------------------------------------

class SQLitePolicyGateStore:
    """SQLite-backed policy gate result store.

    Persists gate results to a SQLite database file. Survives process
    restarts and can be shared across instances.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str = ".agent_app/policy_gates.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS policy_gate_results (
                gate_result_id TEXT PRIMARY KEY,
                bundle_id TEXT NOT NULL,
                replay_id TEXT NOT NULL,
                status TEXT NOT NULL,
                passed INTEGER NOT NULL,
                total_decisions INTEGER NOT NULL,
                changed_decisions INTEGER NOT NULL,
                failed_replays INTEGER NOT NULL,
                changed_ratio REAL NOT NULL,
                new_denies INTEGER NOT NULL,
                new_approvals INTEGER NOT NULL,
                missing_context_count INTEGER NOT NULL,
                rule_results_json TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_by TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_gate_results_bundle
                ON policy_gate_results(bundle_id);
            CREATE INDEX IF NOT EXISTS idx_gate_results_created
                ON policy_gate_results(created_at);
        """)
        self._conn.commit()

    async def save(self, result: PolicyGateResult) -> PolicyGateResult:
        """Save a gate result."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO policy_gate_results
                (gate_result_id, bundle_id, replay_id, status, passed,
                 total_decisions, changed_decisions, failed_replays,
                 changed_ratio, new_denies, new_approvals,
                 missing_context_count, rule_results_json, summary_json,
                 created_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.gate_result_id,
                result.bundle_id,
                result.replay_id,
                result.status,
                1 if result.passed else 0,
                result.total_decisions,
                result.changed_decisions,
                result.failed_replays,
                result.changed_ratio,
                result.new_denies,
                result.new_approvals,
                result.missing_context_count,
                json.dumps(result.rule_results),
                json.dumps(result.summary),
                result.created_at.isoformat(),
                result.created_by,
            ),
        )
        self._conn.commit()
        return result

    async def get(self, gate_result_id: str) -> PolicyGateResult | None:
        """Retrieve a gate result by ID."""
        row = self._conn.execute(
            "SELECT * FROM policy_gate_results WHERE gate_result_id = ?",
            (gate_result_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_result(row)

    async def list(
        self,
        bundle_id: str | None = None,
        limit: int = 50,
    ) -> list[PolicyGateResult]:
        """List gate results, optionally filtered by bundle_id."""
        if bundle_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM policy_gate_results WHERE bundle_id = ? ORDER BY created_at DESC LIMIT ?",
                (bundle_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM policy_gate_results ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_result(row) for row in rows]

    def _row_to_result(self, row: sqlite3.Row) -> PolicyGateResult:
        """Convert a database row to PolicyGateResult."""
        data = dict(row)
        data["rule_results"] = json.loads(data.pop("rule_results_json", "[]"))
        data["summary"] = json.loads(data.pop("summary_json", "{}"))
        data["passed"] = bool(data.pop("passed"))
        for float_field in ("changed_ratio",):
            data[float_field] = float(data[float_field])
        for int_field in ("total_decisions", "changed_decisions", "failed_replays",
                          "new_denies", "new_approvals", "missing_context_count"):
            data[int_field] = int(data[int_field])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        return PolicyGateResult(**data)

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------

def create_gate_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> PolicyGateStore:
    """Factory function to create a PolicyGateStore.

    Args:
        store_type: "memory" or "sqlite".
        db_path: Path for SQLite store (ignored for memory).

    Returns:
        A PolicyGateStore implementation.

    Raises:
        ValueError: If store_type is unknown.
    """
    if store_type == "memory":
        return InMemoryPolicyGateStore()
    if store_type == "sqlite":
        return SQLitePolicyGateStore(db_path=db_path or ".agent_app/policy_gates.db")
    raise ValueError(
        f"Unknown gate store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )
