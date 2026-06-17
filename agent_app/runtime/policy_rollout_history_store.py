"""Rollout history store — persistent storage for rollout history events."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Protocol

from agent_app.governance.policy_rollout_history import (
    RolloutHistoryEvent,
    RolloutHistoryEventType,
)

try:
    from typing import runtime_checkable
except ImportError:
    def runtime_checkable(cls):  # type: ignore[misc]
        return cls


@runtime_checkable
class RolloutHistoryStore(Protocol):
    """Protocol for persisting rollout history events."""

    async def append(self, event: RolloutHistoryEvent) -> RolloutHistoryEvent: ...
    async def get(self, history_event_id: str) -> RolloutHistoryEvent | None: ...
    async def list(
        self,
        rollout_id: str | None = None,
        step_id: str | None = None,
        event_type: RolloutHistoryEventType | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        limit: int | None = None,
    ) -> list[RolloutHistoryEvent]: ...


class InMemoryRolloutHistoryStore:
    """In-memory rollout history store."""

    def __init__(self) -> None:
        self._items: dict[str, RolloutHistoryEvent] = {}
        self._order: list[str] = []

    async def append(self, event: RolloutHistoryEvent) -> RolloutHistoryEvent:
        self._items[event.history_event_id] = event
        self._order.append(event.history_event_id)
        return event

    async def get(self, history_event_id: str) -> RolloutHistoryEvent | None:
        return self._items.get(history_event_id)

    async def list(
        self,
        rollout_id: str | None = None,
        step_id: str | None = None,
        event_type: RolloutHistoryEventType | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        limit: int | None = None,
    ) -> list[RolloutHistoryEvent]:
        results: list[RolloutHistoryEvent] = []
        for eid in self._order:
            event = self._items[eid]
            if rollout_id is not None and event.rollout_id != rollout_id:
                continue
            if step_id is not None and event.step_id != step_id:
                continue
            if event_type is not None and event.event_type != event_type:
                continue
            if window_start is not None and event.created_at < window_start:
                continue
            if window_end is not None and event.created_at > window_end:
                continue
            results.append(event)
        # Sort by created_at ASC, then history_event_id ASC for determinism
        results.sort(key=lambda e: (e.created_at, e.history_event_id))
        if limit is not None:
            results = results[:limit]
        return results


class SQLiteRolloutHistoryStore:
    """SQLite-backed rollout history store."""

    def __init__(self, db_path: str = ".agent_app/policy_rollout_history.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS policy_rollout_history_events (
                history_event_id TEXT PRIMARY KEY,
                rollout_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                step_id TEXT,
                environment TEXT,
                ring_name TEXT,
                actor_id TEXT,
                source_type TEXT,
                source_id TEXT,
                message TEXT,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rollout_history_rollout_id
                ON policy_rollout_history_events(rollout_id);
            CREATE INDEX IF NOT EXISTS idx_rollout_history_event_type
                ON policy_rollout_history_events(event_type);
            CREATE INDEX IF NOT EXISTS idx_rollout_history_created_at
                ON policy_rollout_history_events(created_at);
        """)
        self._conn.commit()

    async def append(self, event: RolloutHistoryEvent) -> RolloutHistoryEvent:
        metadata_json = json.dumps(event.metadata)
        self._conn.execute(
            """INSERT OR REPLACE INTO policy_rollout_history_events
               (history_event_id, rollout_id, event_type, step_id,
                environment, ring_name, actor_id, source_type,
                source_id, message, metadata_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.history_event_id,
                event.rollout_id,
                event.event_type.value,
                event.step_id,
                event.environment,
                event.ring_name,
                event.actor_id,
                event.source_type,
                event.source_id,
                event.message,
                metadata_json,
                event.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return event

    async def get(self, history_event_id: str) -> RolloutHistoryEvent | None:
        row = self._conn.execute(
            "SELECT * FROM policy_rollout_history_events WHERE history_event_id=?",
            (history_event_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_event(row)

    async def list(
        self,
        rollout_id: str | None = None,
        step_id: str | None = None,
        event_type: RolloutHistoryEventType | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        limit: int | None = None,
    ) -> list[RolloutHistoryEvent]:
        clauses: list[str] = []
        params: list[object] = []
        if rollout_id is not None:
            clauses.append("rollout_id=?")
            params.append(rollout_id)
        if step_id is not None:
            clauses.append("step_id=?")
            params.append(step_id)
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
        sql = (
            f"SELECT * FROM policy_rollout_history_events{where}"
            f" ORDER BY created_at ASC, history_event_id ASC"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_event(row) for row in rows]

    def _row_to_event(self, row: sqlite3.Row) -> RolloutHistoryEvent:
        data = dict(row)
        data["event_type"] = RolloutHistoryEventType(data["event_type"])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        metadata_json = data.pop("metadata_json", None)
        if metadata_json:
            data["metadata"] = json.loads(metadata_json)
        else:
            data.pop("metadata", None)
        return RolloutHistoryEvent(**data)

    def close(self) -> None:
        self._conn.close()


def create_rollout_history_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> RolloutHistoryStore:
    """Factory function for creating rollout history store instances."""
    if store_type == "sqlite":
        return SQLiteRolloutHistoryStore(db_path=db_path or ".agent_app/policy_rollout_history.db")
    return InMemoryRolloutHistoryStore()
