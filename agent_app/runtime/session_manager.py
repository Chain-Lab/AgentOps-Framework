"""Session manager — factory and configuration for session stores."""

from __future__ import annotations

from agent_app.runtime.session import InMemorySessionStore, SessionStore, SQLiteSessionStore


def create_session_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> SessionStore:
    """Create a SessionStore based on configuration.

    Args:
        store_type: "memory" or "sqlite".
        db_path: Path for SQLite store (required when store_type="sqlite").

    Returns:
        A SessionStore instance.
    """
    if store_type == "memory":
        return InMemorySessionStore()
    if store_type == "sqlite":
        path = db_path or ".agent_app/sessions.db"
        return SQLiteSessionStore(db_path=path)
    raise ValueError(
        f"Unknown session store type '{store_type}'. "
        "Choose 'memory' or 'sqlite'."
    )
