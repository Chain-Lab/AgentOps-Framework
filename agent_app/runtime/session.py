"""Session management — stores conversation history per session_id.

Provides a SessionStore protocol with InMemorySessionStore (default) and
SQLiteSessionStore (persistent) implementations.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class SessionStore(Protocol):
    """Protocol for session history storage.

    Implementations store conversation items keyed by session_id.
    Each item is a dict with at least a "role" and "content" key.
    """

    async def get_items(self, session_id: str) -> list[dict]:
        """Return all items for a session, ordered by creation time."""
        ...

    async def add_items(self, session_id: str, items: list[dict]) -> None:
        """Append items to a session's history."""
        ...

    async def clear_session(self, session_id: str) -> None:
        """Remove all items for a session."""
        ...


class InMemorySessionStore:
    """In-memory session store (default).

    Items are lost when the process exits.  Suitable for tests and
    single-process development.
    """

    def __init__(self) -> None:
        self._store: dict[str, list[dict]] = {}

    async def get_items(self, session_id: str) -> list[dict]:
        return list(self._store.get(session_id, []))

    async def add_items(self, session_id: str, items: list[dict]) -> None:
        self._store.setdefault(session_id, []).extend(items)

    async def clear_session(self, session_id: str) -> None:
        self._store.pop(session_id, None)


class SQLiteSessionStore:
    """Persistent SQLite-backed session store.

    Args:
        db_path: Path to the SQLite database file.  Parent directories
                 are created automatically.
    """

    def __init__(self, db_path: str = ".agent_app/sessions.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT    NOT NULL,
                item_json   TEXT    NOT NULL,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_items_session "
            "ON session_items(session_id)"
        )
        self._conn.commit()

    async def get_items(self, session_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT item_json FROM session_items "
            "WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [json.loads(r["item_json"]) for r in rows]

    async def add_items(self, session_id: str, items: list[dict]) -> None:
        now = "datetime('now')"
        self._conn.executemany(
            "INSERT INTO session_items (session_id, item_json, created_at) "
            "VALUES (?, ?, " + now + ")",
            [(session_id, json.dumps(item),) for item in items],
        )
        self._conn.commit()

    async def clear_session(self, session_id: str) -> None:
        self._conn.execute(
            "DELETE FROM session_items WHERE session_id = ?",
            (session_id,),
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()
