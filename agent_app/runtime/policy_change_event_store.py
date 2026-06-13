"""Policy change event store -- persists policy lifecycle events."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Protocol

from agent_app.governance.policy_change_event import PolicyChangeEvent

try:
    from typing import runtime_checkable
except ImportError:
    def runtime_checkable(cls):  # type: ignore[misc]
        return cls


@runtime_checkable
class PolicyChangeEventStore(Protocol):
    """Protocol for persisting policy change events."""

    async def append(self, event: PolicyChangeEvent) -> PolicyChangeEvent: ...
    async def get(self, event_id: str) -> PolicyChangeEvent | None: ...
    async def list(
        self,
        environment: str | None = None,
        ring_name: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[PolicyChangeEvent]: ...
    async def latest(
        self,
        environment: str | None = None,
        ring_name: str | None = None,
    ) -> PolicyChangeEvent | None: ...


class InMemoryPolicyChangeEventStore:
    """In-memory policy change event store."""

    def __init__(self) -> None:
        self._events: dict[str, PolicyChangeEvent] = {}
        self._order: list[str] = []

    async def append(self, event: PolicyChangeEvent) -> PolicyChangeEvent:
        self._events[event.event_id] = event
        self._order.append(event.event_id)
        return event

    async def get(self, event_id: str) -> PolicyChangeEvent | None:
        return self._events.get(event_id)

    async def list(
        self,
        environment: str | None = None,
        ring_name: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[PolicyChangeEvent]:
        results: list[PolicyChangeEvent] = []
        for eid in self._order:
            event = self._events[eid]
            if environment is not None and event.environment != environment:
                continue
            if ring_name is not None and event.ring_name != ring_name:
                continue
            if since is not None and event.created_at < since:
                continue
            results.append(event)
        if limit is not None:
            results = results[:limit]
        return results

    async def latest(
        self,
        environment: str | None = None,
        ring_name: str | None = None,
    ) -> PolicyChangeEvent | None:
        result: PolicyChangeEvent | None = None
        for eid in self._order:
            event = self._events[eid]
            if environment is not None and event.environment != environment:
                continue
            if ring_name is not None and event.ring_name != ring_name:
                continue
            result = event
        return result


class SQLitePolicyChangeEventStore:
    """SQLite-backed policy change event store."""

    def __init__(self, db_path: str = ".agent_app/policy_change_events.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS policy_change_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                environment TEXT,
                ring_name TEXT,
                bundle_id TEXT,
                activation_id TEXT,
                assignment_id TEXT,
                actor_id TEXT,
                reason TEXT,
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_pce_env ON policy_change_events(environment);
            CREATE INDEX IF NOT EXISTS idx_pce_ring ON policy_change_events(ring_name);
            CREATE INDEX IF NOT EXISTS idx_pce_created ON policy_change_events(created_at);
        """)
        self._conn.commit()

    async def append(self, event: PolicyChangeEvent) -> PolicyChangeEvent:
        self._conn.execute(
            """INSERT INTO policy_change_events
               (event_id, event_type, environment, ring_name, bundle_id,
                activation_id, assignment_id, actor_id, reason, data_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.event_id,
                event.event_type.value,
                event.environment,
                event.ring_name,
                event.bundle_id,
                event.activation_id,
                event.assignment_id,
                event.actor_id,
                event.reason,
                json.dumps(event.data),
                event.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return event

    async def get(self, event_id: str) -> PolicyChangeEvent | None:
        row = self._conn.execute(
            "SELECT * FROM policy_change_events WHERE event_id=?", (event_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_event(row)

    async def list(
        self,
        environment: str | None = None,
        ring_name: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[PolicyChangeEvent]:
        clauses: list[str] = []
        params: list[object] = []
        if environment is not None:
            clauses.append("environment=?")
            params.append(environment)
        if ring_name is not None:
            clauses.append("ring_name=?")
            params.append(ring_name)
        if since is not None:
            clauses.append("created_at>=?")
            params.append(since.isoformat())
        where = ""
        if clauses:
            where = " WHERE " + " AND ".join(clauses)
        sql = f"SELECT * FROM policy_change_events{where} ORDER BY created_at ASC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_event(row) for row in rows]

    async def latest(
        self,
        environment: str | None = None,
        ring_name: str | None = None,
    ) -> PolicyChangeEvent | None:
        clauses: list[str] = []
        params: list[object] = []
        if environment is not None:
            clauses.append("environment=?")
            params.append(environment)
        if ring_name is not None:
            clauses.append("ring_name=?")
            params.append(ring_name)
        where = ""
        if clauses:
            where = " WHERE " + " AND ".join(clauses)
        sql = f"SELECT * FROM policy_change_events{where} ORDER BY created_at DESC LIMIT 1"
        row = self._conn.execute(sql, params).fetchone()
        if row is None:
            return None
        return self._row_to_event(row)

    def _row_to_event(self, row: sqlite3.Row) -> PolicyChangeEvent:
        from agent_app.governance.policy_change_event import PolicyChangeEventType

        data = dict(row)
        data["event_type"] = PolicyChangeEventType(data["event_type"])
        data["data"] = json.loads(data.pop("data_json"))
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        return PolicyChangeEvent(**data)

    def close(self) -> None:
        self._conn.close()


def create_policy_change_event_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> PolicyChangeEventStore:
    if store_type == "memory":
        return InMemoryPolicyChangeEventStore()
    if store_type == "sqlite":
        return SQLitePolicyChangeEventStore(db_path=db_path or ".agent_app/policy_change_events.db")
    raise ValueError(f"Unknown change event store type '{store_type}'. Supported: 'memory', 'sqlite'.")
