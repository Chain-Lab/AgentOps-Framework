"""Policy replay result store.

Phase 27: persistence for policy replay results.
Phase 28: SQLite-backed store with change queries.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from agent_app.governance.policy_replay import PolicyReplayResult, PolicyReplayStore


# ---------------------------------------------------------------------------
# InMemoryPolicyReplayStore (Phase 27)
# ---------------------------------------------------------------------------

class InMemoryPolicyReplayStore:
    """In-memory policy replay store for testing and development.

    Stores replay results in a simple dict, preserving insertion order.
    """

    def __init__(self) -> None:
        self._results: dict[str, PolicyReplayResult] = {}
        self._order: list[str] = []

    async def save(self, result: PolicyReplayResult) -> PolicyReplayResult:
        """Persist a replay result."""
        rid = result.replay.replay_id
        if rid not in self._results:
            self._order.append(rid)
        self._results[rid] = result
        return result

    async def get(self, replay_id: str) -> PolicyReplayResult | None:
        """Retrieve a replay result by ID. Returns None if not found."""
        return self._results.get(replay_id)

    async def list(self, limit: int = 50) -> list[Any]:
        """List recent replay runs (most recent first), returning the run summary."""
        from agent_app.governance.policy_replay import PolicyReplayRun
        ids = list(reversed(self._order[-limit:]))
        runs: list[PolicyReplayRun] = []
        for rid in ids:
            r = self._results.get(rid)
            if r:
                runs.append(r.replay)
        return runs


# ---------------------------------------------------------------------------
# SQLitePolicyReplayStore (Phase 28)
# ---------------------------------------------------------------------------

class SQLitePolicyReplayStore:
    """SQLite-backed policy replay store.

    Persists replay results to a SQLite database file. Survives process
    restarts and can be shared across instances.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str = ".agent_app/policy_replays.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS policy_replay_runs (
                replay_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                source_decision_count INTEGER NOT NULL,
                changed_count INTEGER NOT NULL,
                unchanged_count INTEGER NOT NULL,
                failed_count INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                metadata_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS policy_replay_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                replay_id TEXT NOT NULL,
                decision_id TEXT NOT NULL,
                original_action TEXT,
                replayed_action TEXT,
                original_rule_id TEXT,
                replayed_rule_id TEXT,
                changed INTEGER NOT NULL,
                failed INTEGER NOT NULL,
                failure_reason TEXT,
                original_decision_json TEXT NOT NULL,
                replayed_decision_json TEXT,
                context_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(replay_id) REFERENCES policy_replay_runs(replay_id)
            );

            CREATE INDEX IF NOT EXISTS idx_replay_runs_created
                ON policy_replay_runs(created_at);
            CREATE INDEX IF NOT EXISTS idx_replay_changes_replay
                ON policy_replay_changes(replay_id);
        """)
        self._conn.commit()

    async def save(self, result: PolicyReplayResult) -> PolicyReplayResult:
        """Persist a replay result and its changes."""
        run = result.replay
        now = _now().isoformat()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO policy_replay_runs
                (replay_id, status, source_decision_count, changed_count,
                 unchanged_count, failed_count, created_at, completed_at,
                 metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.replay_id,
                run.status,
                run.source_decision_count,
                run.changed_count,
                run.unchanged_count,
                run.failed_count,
                run.created_at.isoformat(),
                now,
                json.dumps(run.metadata),
            ),
        )

        # Delete existing changes for this replay_id (handle overwrites)
        self._conn.execute(
            "DELETE FROM policy_replay_changes WHERE replay_id = ?",
            (run.replay_id,),
        )

        # Insert changes
        for c in result.changes:
            self._conn.execute(
                """
                INSERT INTO policy_replay_changes
                    (replay_id, decision_id, original_action, replayed_action,
                     original_rule_id, replayed_rule_id, changed, failed,
                     failure_reason, original_decision_json, replayed_decision_json,
                     context_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.replay_id,
                    c.decision_id,
                    c.original_action,
                    c.replayed_action,
                    c.original_rule_id,
                    c.replayed_rule_id,
                    1 if c.changed else 0,
                    1 if c.replayed_action == "error" else 0,
                    c.reason if c.replayed_action == "error" else None,
                    json.dumps({
                        "decision_id": c.decision_id,
                        "original_action": c.original_action,
                        "original_rule_id": c.original_rule_id,
                    }),
                    json.dumps({
                        "replayed_action": c.replayed_action,
                        "replayed_rule_id": c.replayed_rule_id,
                        "reason": c.reason,
                    }) if c.changed or c.replayed_action == "error" else None,
                    json.dumps({"decision_id": c.decision_id}),
                    now,
                ),
            )

        self._conn.commit()
        return result

    async def get(self, replay_id: str) -> PolicyReplayResult | None:
        """Retrieve a replay result by ID.

        Raises:
            KeyError: If replay_id not found.
        """
        row = self._conn.execute(
            "SELECT * FROM policy_replay_runs WHERE replay_id = ?",
            (replay_id,),
        ).fetchone()
        if row is None:
            raise KeyError(
                f"Replay '{replay_id}' not found in policy replay store."
            )
        run = self._row_to_run(row)
        changes = self._load_changes(replay_id)
        return PolicyReplayResult(replay=run, changes=changes)

    async def list(self, limit: int = 50) -> list[Any]:
        """List recent replay runs (most recent first)."""
        from agent_app.governance.policy_replay import PolicyReplayRun
        rows = self._conn.execute(
            "SELECT * FROM policy_replay_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        runs: list[PolicyReplayRun] = []
        for row in rows:
            runs.append(self._row_to_run(row))
        return runs

    async def list_changes(
        self,
        replay_id: str,
        changed_only: bool = False,
        failed_only: bool = False,
    ) -> list[PolicyReplayDecisionChange]:
        """List changes for a specific replay, with optional filters.

        Args:
            replay_id: The replay to get changes for.
            changed_only: If True, only return changed decisions.
            failed_only: If True, only return failed decisions.

        Returns:
            List of PolicyReplayDecisionChange objects.

        Raises:
            KeyError: If replay_id not found.
        """
        # Verify replay exists
        row = self._conn.execute(
            "SELECT replay_id FROM policy_replay_runs WHERE replay_id = ?",
            (replay_id,),
        ).fetchone()
        if row is None:
            raise KeyError(
                f"Replay '{replay_id}' not found in policy replay store."
            )

        query = "SELECT * FROM policy_replay_changes WHERE replay_id = ?"
        params: list = [replay_id]

        if changed_only and failed_only:
            # No results can be both changed and failed
            return []
        elif changed_only:
            query += " AND changed = 1 AND failed = 0"
        elif failed_only:
            query += " AND failed = 1"

        query += " ORDER BY id"
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_change(row) for row in rows]

    def _row_to_run(self, row: sqlite3.Row) -> PolicyReplayRun:
        """Convert a database row to PolicyReplayRun."""
        from agent_app.governance.policy_replay import PolicyReplayRun, PolicyReplayStatus
        data = dict(row)
        metadata = json.loads(data.pop("metadata_json", "{}"))
        created_at = datetime.fromisoformat(data["created_at"])
        return PolicyReplayRun(
            replay_id=data["replay_id"],
            status=data["status"],
            source_decision_count=data["source_decision_count"],
            changed_count=data["changed_count"],
            unchanged_count=data["unchanged_count"],
            failed_count=data["failed_count"],
            created_at=created_at,
            metadata=metadata,
        )

    def _load_changes(self, replay_id: str) -> list[PolicyReplayDecisionChange]:
        """Load all changes for a replay."""
        rows = self._conn.execute(
            "SELECT * FROM policy_replay_changes WHERE replay_id = ? ORDER BY id",
            (replay_id,),
        ).fetchall()
        return [self._row_to_change(row) for row in rows]

    def _row_to_change(self, row: sqlite3.Row) -> PolicyReplayDecisionChange:
        """Convert a database row to PolicyReplayDecisionChange."""
        from agent_app.governance.policy_replay import PolicyReplayDecisionChange
        data = dict(row)
        changed = bool(data.pop("changed"))
        return PolicyReplayDecisionChange(
            decision_id=data.pop("decision_id"),
            original_action=data.pop("original_action"),
            replayed_action=data.pop("replayed_action"),
            changed=changed,
            original_rule_id=data.pop("original_rule_id", None),
            replayed_rule_id=data.pop("replayed_rule_id", None),
            reason=data.pop("reason", None),
        )

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


def create_replay_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> PolicyReplayStore:
    """Factory function to create a PolicyReplayStore.

    Args:
        store_type: "memory" or "sqlite".
        db_path: Path for SQLite store (ignored for memory).

    Returns:
        A PolicyReplayStore implementation.

    Raises:
        ValueError: If store_type is unknown.
    """
    if store_type == "memory":
        return InMemoryPolicyReplayStore()
    if store_type == "sqlite":
        return SQLitePolicyReplayStore(db_path=db_path or ".agent_app/policy_replays.db")
    raise ValueError(
        f"Unknown replay store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )


def _now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)
