"""Policy Decision Store — persistence for policy evaluation traces.

Phase 25: Provides a protocol and two implementations (in-memory, SQLite)
for storing and querying policy decision traces.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from agent_app.governance.policy import PolicyAction, PolicyDecisionTrace
from pydantic import BaseModel, Field


class PolicyDecisionStore(Protocol):
    """Protocol for policy decision persistence.

    Implementations store PolicyDecisionTrace records and support
    filtered querying with pagination.
    """

    async def record(self, decision: PolicyDecisionTrace) -> PolicyDecisionTrace:
        """Store a policy decision trace.

        Args:
            decision: The trace to store.

        Returns:
            The stored trace (may be modified by implementation).
        """
        ...

    async def get(self, decision_id: str) -> PolicyDecisionTrace:
        """Retrieve a single decision by ID.

        Args:
            decision_id: The decision identifier.

        Returns:
            The matching PolicyDecisionTrace.

        Raises:
            KeyError: If no decision with that ID exists.
        """
        ...

    async def query(
        self,
        run_id: str | None = None,
        tenant_id: str | None = None,
        agent_name: str | None = None,
        tool_name: str | None = None,
        rule_name: str | None = None,
        action: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PolicyDecisionTrace]:
        """Query decision traces with optional filters.

        Args:
            run_id: Filter by run ID.
            tenant_id: Filter by tenant ID (from context_summary).
            agent_name: Filter by agent name (from context_summary).
            tool_name: Filter by tool name.
            rule_name: Filter by matched rule name.
            action: Filter by policy action string.
            limit: Maximum results to return (default 100).
            offset: Number of results to skip (default 0).

        Returns:
            List of matching traces, sorted by created_at descending.
        """
        ...

    async def count(
        self,
        run_id: str | None = None,
        tenant_id: str | None = None,
        agent_name: str | None = None,
        tool_name: str | None = None,
        rule_name: str | None = None,
        action: str | None = None,
    ) -> int:
        """Count decision traces matching the given filters.

        Args:
            run_id: Filter by run ID.
            tenant_id: Filter by tenant ID.
            agent_name: Filter by agent name.
            tool_name: Filter by tool name.
            rule_name: Filter by matched rule name.
            action: Filter by policy action string.

        Returns:
            Number of matching traces.
        """
        ...


class InMemoryPolicyDecisionStore:
    """In-memory policy decision store for testing and development.

    Stores traces in a simple list with filter support.
    Results are sorted by created_at descending (newest first).
    """

    def __init__(self) -> None:
        self._traces: list[PolicyDecisionTrace] = []

    async def record(self, decision: PolicyDecisionTrace) -> PolicyDecisionTrace:
        """Store a policy decision trace."""
        self._traces.append(decision)
        return decision

    async def get(self, decision_id: str) -> PolicyDecisionTrace:
        """Retrieve a single decision by ID."""
        for trace in self._traces:
            if trace.decision_id == decision_id:
                return trace
        raise KeyError(f"Policy decision not found: {decision_id}")

    async def query(
        self,
        run_id: str | None = None,
        tenant_id: str | None = None,
        agent_name: str | None = None,
        tool_name: str | None = None,
        rule_name: str | None = None,
        action: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PolicyDecisionTrace]:
        """Query with optional filters, sorted newest first."""
        results = list(self._traces)

        if run_id is not None:
            results = [t for t in results if t.run_id == run_id]
        if tenant_id is not None:
            results = [t for t in results if t.context_summary.get("tenant_id") == tenant_id]
        if agent_name is not None:
            results = [t for t in results if t.context_summary.get("agent_name") == agent_name]
        if tool_name is not None:
            results = [t for t in results if t.tool_name == tool_name]
        if rule_name is not None:
            results = [t for t in results if t.rule_name == rule_name]
        if action is not None:
            results = [t for t in results if t.action.value == action]

        # Sort newest first
        results.sort(key=lambda t: t.created_at, reverse=True)

        # Apply pagination
        if offset > 0:
            results = results[offset:]
        if limit is not None and limit >= 0:
            results = results[:limit]

        return results

    async def count(
        self,
        run_id: str | None = None,
        tenant_id: str | None = None,
        agent_name: str | None = None,
        tool_name: str | None = None,
        rule_name: str | None = None,
        action: str | None = None,
    ) -> int:
        """Count traces matching filters."""
        return len(await self.query(
            run_id=run_id,
            tenant_id=tenant_id,
            agent_name=agent_name,
            tool_name=tool_name,
            rule_name=rule_name,
            action=action,
            limit=None,
            offset=0,
        ))


class SQLitePolicyDecisionStore:
    """SQLite-backed policy decision store for persistence.

    Stores policy decision traces in a SQLite database file,
    supporting filtered queries and pagination.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str = ".agent_app/policy_decisions.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._db_path), check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        """Create tables and indexes if they don't exist."""
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_decisions (
                decision_id           TEXT PRIMARY KEY,
                run_id                TEXT,
                tenant_id             TEXT,
                user_id               TEXT,
                agent_name            TEXT,
                tool_name             TEXT,
                workflow_type         TEXT,
                target_agent          TEXT,
                rule_name             TEXT,
                action                TEXT NOT NULL,
                reason                TEXT,
                matched_conditions_json TEXT NOT NULL,
                context_summary_json  TEXT NOT NULL,
                created_at            TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_policy_decisions_run_id "
            "ON policy_decisions(run_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_policy_decisions_tenant_id "
            "ON policy_decisions(tenant_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_policy_decisions_rule_name "
            "ON policy_decisions(rule_name)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_policy_decisions_action "
            "ON policy_decisions(action)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_policy_decisions_created_at "
            "ON policy_decisions(created_at)"
        )
        self._conn.commit()

    async def record(self, decision: PolicyDecisionTrace) -> PolicyDecisionTrace:
        """Store a policy decision trace."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO policy_decisions
                (decision_id, run_id, tenant_id, user_id, agent_name,
                 tool_name, workflow_type, target_agent, rule_name, action,
                 reason, matched_conditions_json, context_summary_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.decision_id,
                decision.run_id,
                decision.context_summary.get("tenant_id"),
                decision.context_summary.get("user_id"),
                decision.context_summary.get("agent_name"),
                decision.tool_name,
                decision.context_summary.get("workflow_type"),
                decision.context_summary.get("target_agent"),
                decision.rule_name,
                decision.action.value,
                decision.reason,
                json.dumps(decision.matched_conditions),
                json.dumps(decision.context_summary),
                decision.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return decision

    async def get(self, decision_id: str) -> PolicyDecisionTrace:
        """Retrieve a single decision by ID."""
        row = self._conn.execute(
            "SELECT * FROM policy_decisions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Policy decision not found: {decision_id}")
        return self._row_to_trace(row)

    async def query(
        self,
        run_id: str | None = None,
        tenant_id: str | None = None,
        agent_name: str | None = None,
        tool_name: str | None = None,
        rule_name: str | None = None,
        action: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PolicyDecisionTrace]:
        """Query with optional filters, sorted newest first."""
        query = "SELECT * FROM policy_decisions WHERE 1=1"
        params: list[Any] = []

        if run_id is not None:
            query += " AND run_id = ?"
            params.append(run_id)
        if tenant_id is not None:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        if agent_name is not None:
            query += " AND agent_name = ?"
            params.append(agent_name)
        if tool_name is not None:
            query += " AND tool_name = ?"
            params.append(tool_name)
        if rule_name is not None:
            query += " AND rule_name = ?"
            params.append(rule_name)
        if action is not None:
            query += " AND action = ?"
            params.append(action)

        query += " ORDER BY created_at DESC"

        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_trace(r) for r in rows]

    async def count(
        self,
        run_id: str | None = None,
        tenant_id: str | None = None,
        agent_name: str | None = None,
        tool_name: str | None = None,
        rule_name: str | None = None,
        action: str | None = None,
    ) -> int:
        """Count traces matching filters."""
        query = "SELECT COUNT(*) FROM policy_decisions WHERE 1=1"
        params: list[Any] = []

        if run_id is not None:
            query += " AND run_id = ?"
            params.append(run_id)
        if tenant_id is not None:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        if agent_name is not None:
            query += " AND agent_name = ?"
            params.append(agent_name)
        if tool_name is not None:
            query += " AND tool_name = ?"
            params.append(tool_name)
        if rule_name is not None:
            query += " AND rule_name = ?"
            params.append(rule_name)
        if action is not None:
            query += " AND action = ?"
            params.append(action)

        row = self._conn.execute(query, params).fetchone()
        return row[0] if row else 0

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def _row_to_trace(self, row: sqlite3.Row) -> PolicyDecisionTrace:
        """Convert a database row to PolicyDecisionTrace."""
        return PolicyDecisionTrace(
            decision_id=row["decision_id"],
            run_id=row["run_id"],
            rule_name=row["rule_name"],
            action=PolicyAction(row["action"]),
            reason=row["reason"],
            matched_conditions=json.loads(row["matched_conditions_json"]),
            context_summary=json.loads(row["context_summary_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )


# ---------------------------------------------------------------------------
# Phase 25: Policy Reporting Service
# ---------------------------------------------------------------------------

class PolicyReport(BaseModel):
    """Aggregated policy decision report.

    Attributes:
        total_decisions: Total number of decisions in the report.
        action_breakdown: Count of decisions per action.
        rule_breakdown: Count of decisions per rule name.
        tool_breakdown: Count of decisions per tool name.
        time_range: Start and end of the report window.
    """

    total_decisions: int = Field(..., description="Total decisions counted")
    action_breakdown: dict[str, int] = Field(
        default_factory=dict, description="Counts per policy action"
    )
    rule_breakdown: dict[str, int] = Field(
        default_factory=dict, description="Counts per rule name"
    )
    tool_breakdown: dict[str, int] = Field(
        default_factory=dict, description="Counts per tool name"
    )
    time_range: dict[str, datetime | None] = Field(
        default_factory=dict, description="Start and end timestamps"
    )


class PolicyReportingService:
    """Aggregates policy decision data into reports.

    Built on top of a PolicyDecisionStore — works with any implementation.

    Args:
        store: The policy decision store to query.
    """

    def __init__(self, store: PolicyDecisionStore) -> None:
        self._store = store

    async def generate_report(
        self,
        run_id: str | None = None,
        tenant_id: str | None = None,
        agent_name: str | None = None,
        tool_name: str | None = None,
        rule_name: str | None = None,
        action: str | None = None,
        limit: int = 1000,
    ) -> PolicyReport:
        """Generate an aggregated policy decision report.

        Args:
            run_id: Optional run ID filter.
            tenant_id: Optional tenant filter.
            agent_name: Optional agent filter.
            tool_name: Optional tool filter.
            rule_name: Optional rule filter.
            action: Optional action filter.
            limit: Maximum decisions to include (default 1000).

        Returns:
            PolicyReport with aggregated statistics.
        """
        traces = await self._store.query(
            run_id=run_id,
            tenant_id=tenant_id,
            agent_name=agent_name,
            tool_name=tool_name,
            rule_name=rule_name,
            action=action,
            limit=limit,
        )

        action_breakdown: dict[str, int] = {}
        rule_breakdown: dict[str, int] = {}
        tool_breakdown: dict[str, int] = {}

        for trace in traces:
            # Action breakdown
            act = trace.action.value
            action_breakdown[act] = action_breakdown.get(act, 0) + 1
            # Rule breakdown
            rn = trace.rule_name or "(default)"
            rule_breakdown[rn] = rule_breakdown.get(rn, 0) + 1
            # Tool breakdown
            tn = trace.tool_name or "(unknown)"
            tool_breakdown[tn] = tool_breakdown.get(tn, 0) + 1

        time_range: dict[str, datetime | None] = {
            "start": None,
            "end": None,
        }
        if traces:
            time_range["start"] = min(t.created_at for t in traces)
            time_range["end"] = max(t.created_at for t in traces)

        return PolicyReport(
            total_decisions=len(traces),
            action_breakdown=action_breakdown,
            rule_breakdown=rule_breakdown,
            tool_breakdown=tool_breakdown,
            time_range=time_range,
        )

    async def export_jsonl(
        self,
        file_path: str,
        run_id: str | None = None,
        tenant_id: str | None = None,
        limit: int = 10000,
    ) -> int:
        """Export policy decisions as JSON Lines to a file.

        Args:
            file_path: Destination file path.
            run_id: Optional run ID filter.
            tenant_id: Optional tenant filter.
            limit: Maximum records to export.

        Returns:
            Number of records exported.
        """
        traces = await self._store.query(
            run_id=run_id,
            tenant_id=tenant_id,
            limit=limit,
        )
        from pathlib import Path as _Path
        path = _Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for trace in traces:
                fh.write(trace.model_dump_json() + "\n")
        return len(traces)

    async def export_csv(
        self,
        file_path: str,
        run_id: str | None = None,
        tenant_id: str | None = None,
        limit: int = 10000,
    ) -> int:
        """Export policy decisions as CSV to a file.

        Args:
            file_path: Destination file path.
            run_id: Optional run ID filter.
            tenant_id: Optional tenant filter.
            limit: Maximum records to export.

        Returns:
            Number of records exported.
        """
        import csv
        traces = await self._store.query(
            run_id=run_id,
            tenant_id=tenant_id,
            limit=limit,
        )
        from pathlib import Path as _Path
        path = _Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "decision_id", "run_id", "rule_name", "action",
            "reason", "tool_name", "created_at",
        ]
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for trace in traces:
                writer.writerow({
                    "decision_id": trace.decision_id,
                    "run_id": trace.run_id or "",
                    "rule_name": trace.rule_name or "",
                    "action": trace.action.value,
                    "reason": trace.reason or "",
                    "tool_name": trace.tool_name or "",
                    "created_at": trace.created_at.isoformat(),
                })
        return len(traces)
