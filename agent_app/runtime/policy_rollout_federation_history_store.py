"""Federation history store -- persists FederationHistoryEvent with Protocol + InMemory + SQLite."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agent_app.governance.policy_rollout_federation_history import (
    FederationHistoryEventType,
    FederationHistoryEvent,
)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class FederationHistoryStore(Protocol):
    """Protocol for persisting federation history events."""

    async def append(self, event: FederationHistoryEvent) -> FederationHistoryEvent: ...
    async def get(self, history_event_id: str) -> FederationHistoryEvent | None: ...
    async def list(
        self,
        federation_id: str | None = None,
        target_id: str | None = None,
        rollout_id: str | None = None,
        wave_id: str | None = None,
        event_type: FederationHistoryEventType | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        limit: int | None = None,
    ) -> list[FederationHistoryEvent]: ...


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryFederationHistoryStore:
    """In-memory federation history event store."""

    def __init__(self) -> None:
        self._events: dict[str, FederationHistoryEvent] = {}

    async def append(self, event: FederationHistoryEvent) -> FederationHistoryEvent:
        self._events[event.history_event_id] = event
        return event

    async def get(self, history_event_id: str) -> FederationHistoryEvent | None:
        return self._events.get(history_event_id)

    async def list(
        self,
        federation_id: str | None = None,
        target_id: str | None = None,
        rollout_id: str | None = None,
        wave_id: str | None = None,
        event_type: FederationHistoryEventType | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        limit: int | None = None,
    ) -> list[FederationHistoryEvent]:
        results: list[FederationHistoryEvent] = []
        for event in self._events.values():
            if federation_id is not None and event.federation_id != federation_id:
                continue
            if target_id is not None and event.target_id != target_id:
                continue
            if rollout_id is not None and event.rollout_id != rollout_id:
                continue
            if wave_id is not None and event.wave_id != wave_id:
                continue
            if event_type is not None and event.event_type != event_type:
                continue
            if window_start is not None and event.created_at < window_start:
                continue
            if window_end is not None and event.created_at > window_end:
                continue
            results.append(event)
        results.sort(key=lambda e: (e.created_at, e.history_event_id))
        if limit is not None:
            results = results[:limit]
        return results


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteFederationHistoryStore:
    """SQLite-backed federation history event store."""

    def __init__(self, db_path: str = ".agent_app/policy_federation_history.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS policy_federation_history_events (
                history_event_id TEXT PRIMARY KEY,
                federation_id TEXT,
                target_id TEXT,
                rollout_id TEXT,
                wave_id TEXT,
                event_type TEXT NOT NULL,
                tenant_id TEXT,
                environment TEXT,
                ring_name TEXT,
                region TEXT,
                actor_id TEXT,
                source_type TEXT,
                source_id TEXT,
                message TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_pfhe_federation ON policy_federation_history_events(federation_id);
            CREATE INDEX IF NOT EXISTS idx_pfhe_target ON policy_federation_history_events(target_id);
            CREATE INDEX IF NOT EXISTS idx_pfhe_event_type ON policy_federation_history_events(event_type);
            CREATE INDEX IF NOT EXISTS idx_pfhe_created_at ON policy_federation_history_events(created_at);
        """)
        self._conn.commit()

    async def append(self, event: FederationHistoryEvent) -> FederationHistoryEvent:
        self._conn.execute(
            """INSERT INTO policy_federation_history_events
               (history_event_id, federation_id, target_id, rollout_id, wave_id,
                event_type, tenant_id, environment, ring_name, region,
                actor_id, source_type, source_id, message,
                metadata_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.history_event_id,
                event.federation_id,
                event.target_id,
                event.rollout_id,
                event.wave_id,
                event.event_type.value,
                event.tenant_id,
                event.environment,
                event.ring_name,
                event.region,
                event.actor_id,
                event.source_type,
                event.source_id,
                event.message,
                json.dumps(event.metadata),
                event.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return event

    async def get(self, history_event_id: str) -> FederationHistoryEvent | None:
        row = self._conn.execute(
            "SELECT * FROM policy_federation_history_events WHERE history_event_id=?",
            (history_event_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_event(row)

    async def list(
        self,
        federation_id: str | None = None,
        target_id: str | None = None,
        rollout_id: str | None = None,
        wave_id: str | None = None,
        event_type: FederationHistoryEventType | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        limit: int | None = None,
    ) -> list[FederationHistoryEvent]:
        clauses: list[str] = []
        params: list[object] = []
        if federation_id is not None:
            clauses.append("federation_id=?")
            params.append(federation_id)
        if target_id is not None:
            clauses.append("target_id=?")
            params.append(target_id)
        if rollout_id is not None:
            clauses.append("rollout_id=?")
            params.append(rollout_id)
        if wave_id is not None:
            clauses.append("wave_id=?")
            params.append(wave_id)
        if event_type is not None:
            clauses.append("event_type=?")
            params.append(event_type.value)
        if window_start is not None:
            clauses.append("created_at>=?")
            params.append(window_start.isoformat())
        if window_end is not None:
            clauses.append("created_at<=?")
            params.append(window_end.isoformat())
        where = ""
        if clauses:
            where = " WHERE " + " AND ".join(clauses)
        sql = f"SELECT * FROM policy_federation_history_events{where} ORDER BY created_at ASC, history_event_id ASC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_event(row) for row in rows]

    def _row_to_event(self, row: sqlite3.Row) -> FederationHistoryEvent:
        data = dict(row)
        data["event_type"] = FederationHistoryEventType(data["event_type"])
        data["metadata"] = json.loads(data.pop("metadata_json"))
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        return FederationHistoryEvent(**data)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_federation_history_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> FederationHistoryStore:
    """Factory for creating federation history store instances."""
    if store_type == "memory":
        return InMemoryFederationHistoryStore()
    if store_type == "sqlite":
        return SQLiteFederationHistoryStore(db_path=db_path or ".agent_app/policy_federation_history.db")
    raise ValueError(f"Unknown federation history store type '{store_type}'. Supported: 'memory', 'sqlite'.")
