"""Alert delivery retry daemon state — models and store implementations.

Phase 57 Task 4: Persistent daemon state store for health visibility across restarts.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class AlertDeliveryRetryDaemonState(BaseModel):
    """Persistent state for the alert delivery retry daemon."""

    daemon_id: str = Field(..., description="Unique daemon identifier")
    enabled: bool = Field(default=False, description="Whether daemon is enabled in config")
    desired_state: str = Field(default="stopped", description="Desired state (stopped|running)")
    actual_state: str = Field(default="stopped", description="Actual state (stopped|running|error|unknown)")
    started_at: datetime | None = Field(default=None, description="When daemon was started")
    stopped_at: datetime | None = Field(default=None, description="When daemon was stopped")
    last_run_at: datetime | None = Field(default=None, description="Last run completion timestamp")
    last_success_at: datetime | None = Field(default=None, description="Last successful run timestamp")
    last_error_at: datetime | None = Field(default=None, description="Last error timestamp")
    last_error_message: str | None = Field(default=None, description="Redacted last error message")
    consecutive_failures: int = Field(default=0, description="Consecutive failure count")
    last_result: dict[str, Any] = Field(default_factory=dict, description="Last run result summary")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Last update timestamp")


# ---------------------------------------------------------------------------
# Store Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class AlertDeliveryRetryDaemonStateStore(Protocol):
    """Protocol for persistent daemon state storage."""

    def get(self, daemon_id: str) -> AlertDeliveryRetryDaemonState | None: ...
    def save(self, state: AlertDeliveryRetryDaemonState) -> AlertDeliveryRetryDaemonState: ...
    def list_states(self) -> list[AlertDeliveryRetryDaemonState]: ...


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryAlertDeliveryRetryDaemonStateStore:
    """In-memory daemon state store."""

    def __init__(self) -> None:
        self._states: dict[str, AlertDeliveryRetryDaemonState] = {}

    def get(self, daemon_id: str) -> AlertDeliveryRetryDaemonState | None:
        return self._states.get(daemon_id)

    def save(self, state: AlertDeliveryRetryDaemonState) -> AlertDeliveryRetryDaemonState:
        state.updated_at = datetime.now(timezone.utc)
        # Redact error message
        if state.last_error_message:
            state.last_error_message = _redact_error_message(state.last_error_message)
        # Redact last_result
        if state.last_result:
            state.last_result = _redact_result(state.last_result)
        self._states[state.daemon_id] = state
        return state

    def list_states(self) -> list[AlertDeliveryRetryDaemonState]:
        return list(self._states.values())


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteAlertDeliveryRetryDaemonStateStore:
    """SQLite-backed daemon state store."""

    def __init__(self, db_path: str = ".agent_app/retry_daemon_state.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS retry_daemon_state (
                daemon_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                desired_state TEXT NOT NULL DEFAULT 'stopped',
                actual_state TEXT NOT NULL DEFAULT 'stopped',
                started_at TEXT,
                stopped_at TEXT,
                last_run_at TEXT,
                last_success_at TEXT,
                last_error_at TEXT,
                last_error_message TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                last_result TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            );
        """)
        self._conn.commit()

    def get(self, daemon_id: str) -> AlertDeliveryRetryDaemonState | None:
        row = self._conn.execute(
            "SELECT * FROM retry_daemon_state WHERE daemon_id=?",
            (daemon_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_state(row)

    def save(self, state: AlertDeliveryRetryDaemonState) -> AlertDeliveryRetryDaemonState:
        state.updated_at = datetime.now(timezone.utc)
        # Redact before saving
        error_msg = state.last_error_message
        if error_msg:
            error_msg = _redact_error_message(error_msg)
        result_json = "{}"
        if state.last_result:
            result_json = json.dumps(_redact_result(state.last_result))

        self._conn.execute(
            """INSERT OR REPLACE INTO retry_daemon_state
               (daemon_id, enabled, desired_state, actual_state,
                started_at, stopped_at, last_run_at, last_success_at,
                last_error_at, last_error_message, consecutive_failures,
                last_result, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                state.daemon_id,
                1 if state.enabled else 0,
                state.desired_state,
                state.actual_state,
                state.started_at.isoformat() if state.started_at else None,
                state.stopped_at.isoformat() if state.stopped_at else None,
                state.last_run_at.isoformat() if state.last_run_at else None,
                state.last_success_at.isoformat() if state.last_success_at else None,
                state.last_error_at.isoformat() if state.last_error_at else None,
                error_msg,
                state.consecutive_failures,
                result_json,
                state.updated_at.isoformat(),
            ),
        )
        self._conn.commit()
        return state

    def list_states(self) -> list[AlertDeliveryRetryDaemonState]:
        rows = self._conn.execute("SELECT * FROM retry_daemon_state").fetchall()
        return [self._row_to_state(row) for row in rows]

    def _row_to_state(self, row: sqlite3.Row) -> AlertDeliveryRetryDaemonState:
        import json as _json
        data = dict(row)
        data["enabled"] = bool(data["enabled"])
        for field in ("started_at", "stopped_at", "last_run_at", "last_success_at",
                       "last_error_at", "updated_at"):
            if data.get(field) is not None:
                data[field] = datetime.fromisoformat(data[field])
        result_raw = data.get("last_result", "{}") or "{}"
        data["last_result"] = _json.loads(result_raw)
        return AlertDeliveryRetryDaemonState(**data)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Redaction helpers
# ---------------------------------------------------------------------------


def _redact_error_message(msg: str | None) -> str | None:
    """Redact sensitive patterns from error messages."""
    if not msg:
        return msg
    import re
    _patterns = [
        r"token=[^\s,;}]*",
        r"secret=[^\s,;}]*",
        r"api_key=[^\s,;}]*",
        r"password=[^\s,;}]*",
        r"authorization:\s*[^\s,;}]*",
        r"x-signature:\s*[^\s,;}]*",
        r"x-api-key:\s*[^\s,;}]*",
    ]
    redacted = msg
    for pattern in _patterns:
        redacted = re.sub(pattern, "[REDACTED]", redacted, flags=re.IGNORECASE)
    return redacted


def _redact_result(result: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive fields from result dicts."""
    _sensitive = {"error_message", "body_preview", "payload", "headers", "authorization",
                  "token", "secret", "password", "api_key", "x-signature", "x-api-key",
                  "x-secret", "x-auth-token", "x-webhook-secret", "cookie"}
    redacted: dict[str, Any] = {}
    for k, v in result.items():
        if k.lower() in _sensitive:
            redacted[k] = "[REDACTED]" if not isinstance(v, dict) else {kk: "[REDACTED]" for kk in v}
        elif isinstance(v, dict):
            redacted[k] = _redact_result(v)
        else:
            redacted[k] = v
    return redacted


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_retry_daemon_state_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> AlertDeliveryRetryDaemonStateStore:
    """Factory for creating daemon state store instances."""
    if store_type == "memory":
        return InMemoryAlertDeliveryRetryDaemonStateStore()
    if store_type == "sqlite":
        return SQLiteAlertDeliveryRetryDaemonStateStore(
            db_path=db_path or ".agent_app/retry_daemon_state.db"
        )
    raise ValueError(
        f"Unknown daemon state store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )
