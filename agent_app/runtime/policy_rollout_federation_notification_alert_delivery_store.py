"""Alert delivery store — Protocol, InMemory, SQLite, factory.

Phase 53 Task 2: Alert delivery persistence.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryAttempt,
    AlertDeliveryTarget,
)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class AlertDeliveryStore(Protocol):
    """Protocol for alert delivery target and attempt persistence."""

    # Target CRUD
    async def create_target(self, target: AlertDeliveryTarget) -> AlertDeliveryTarget: ...
    async def get_target(self, target_id: str) -> AlertDeliveryTarget | None: ...
    async def list_targets(
        self,
        enabled: bool | None = None,
    ) -> list[AlertDeliveryTarget]: ...
    async def update_target(self, target: AlertDeliveryTarget) -> AlertDeliveryTarget: ...
    async def delete_target(self, target_id: str) -> None: ...

    # Attempt CRUD
    async def record_attempt(self, attempt: AlertDeliveryAttempt) -> AlertDeliveryAttempt: ...
    async def get_attempt(self, attempt_id: str) -> AlertDeliveryAttempt | None: ...
    async def list_attempts(
        self,
        alert_id: str | None = None,
        target_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AlertDeliveryAttempt]: ...


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryAlertDeliveryStore:
    """In-memory alert delivery store."""

    def __init__(self) -> None:
        self._targets: dict[str, AlertDeliveryTarget] = {}
        self._attempts: dict[str, AlertDeliveryAttempt] = {}
        self._attempts_by_alert: dict[str, list[str]] = {}
        self._attempts_by_target: dict[str, list[str]] = {}

    async def create_target(self, target: AlertDeliveryTarget) -> AlertDeliveryTarget:
        if target.target_id in self._targets:
            raise ValueError(f"Target '{target.target_id}' already exists")
        self._targets[target.target_id] = target
        return target

    async def get_target(self, target_id: str) -> AlertDeliveryTarget | None:
        return self._targets.get(target_id)

    async def list_targets(
        self,
        enabled: bool | None = None,
    ) -> list[AlertDeliveryTarget]:
        targets = list(self._targets.values())
        if enabled is not None:
            targets = [t for t in targets if t.enabled == enabled]
        return targets

    async def update_target(self, target: AlertDeliveryTarget) -> AlertDeliveryTarget:
        if target.target_id not in self._targets:
            raise ValueError(f"Target '{target.target_id}' not found")
        self._targets[target.target_id] = target
        return target

    async def delete_target(self, target_id: str) -> None:
        self._targets.pop(target_id, None)

    async def record_attempt(self, attempt: AlertDeliveryAttempt) -> AlertDeliveryAttempt:
        if attempt.attempt_id in self._attempts:
            raise ValueError(f"Attempt '{attempt.attempt_id}' already exists")
        self._attempts[attempt.attempt_id] = attempt
        # Index by alert_id
        if attempt.alert_id not in self._attempts_by_alert:
            self._attempts_by_alert[attempt.alert_id] = []
        self._attempts_by_alert[attempt.alert_id].append(attempt.attempt_id)
        # Index by target_id
        if attempt.target_id not in self._attempts_by_target:
            self._attempts_by_target[attempt.target_id] = []
        self._attempts_by_target[attempt.target_id].append(attempt.attempt_id)
        return attempt

    async def get_attempt(self, attempt_id: str) -> AlertDeliveryAttempt | None:
        return self._attempts.get(attempt_id)

    async def list_attempts(
        self,
        alert_id: str | None = None,
        target_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AlertDeliveryAttempt]:
        ids: set[str] | None = None

        if alert_id is not None:
            ids = set(self._attempts_by_alert.get(alert_id, []))
        if target_id is not None:
            target_ids = set(self._attempts_by_target.get(target_id, []))
            ids = target_ids if ids is None else ids & target_ids

        if ids is not None:
            attempts = [self._attempts[i] for i in ids if i in self._attempts]
        else:
            attempts = list(self._attempts.values())

        if status is not None:
            attempts = [a for a in attempts if a.status == status]

        attempts.sort(key=lambda a: a.created_at, reverse=True)
        return attempts[offset: offset + limit]


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteAlertDeliveryStore:
    """SQLite-backed alert delivery store."""

    def __init__(self, db_path: str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS notification_alert_delivery_targets (
                target_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                channel_type TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                severity_filter_json TEXT NOT NULL,
                channel_filter_json TEXT NOT NULL,
                federation_filter_json TEXT NOT NULL,
                endpoint TEXT,
                headers_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notification_alert_delivery_attempts (
                attempt_id TEXT PRIMARY KEY,
                alert_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                channel_type TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                next_retry_at TEXT,
                error_code TEXT,
                error_message TEXT,
                payload_preview_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                delivered_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_nda_alert
                ON notification_alert_delivery_attempts(alert_id);
            CREATE INDEX IF NOT EXISTS idx_nda_target
                ON notification_alert_delivery_attempts(target_id);
            CREATE INDEX IF NOT EXISTS idx_nda_status
                ON notification_alert_delivery_attempts(status);
        """)
        self._conn.commit()

    async def create_target(self, target: AlertDeliveryTarget) -> AlertDeliveryTarget:
        import json
        now = datetime.now(timezone.utc).isoformat()
        try:
            self._conn.execute(
                """INSERT INTO notification_alert_delivery_targets
                   (target_id, name, channel_type, enabled, severity_filter_json,
                    channel_filter_json, federation_filter_json, endpoint,
                    headers_json, metadata_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    target.target_id,
                    target.name,
                    target.channel_type.value,
                    1 if target.enabled else 0,
                    json.dumps(target.severity_filter),
                    json.dumps(target.channel_filter),
                    json.dumps(target.federation_filter),
                    target.endpoint,
                    json.dumps(target.headers),
                    json.dumps(target.metadata),
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"Target '{target.target_id}' already exists")
        self._conn.commit()
        return target

    async def get_target(self, target_id: str) -> AlertDeliveryTarget | None:
        row = self._conn.execute(
            "SELECT * FROM notification_alert_delivery_targets WHERE target_id=?",
            (target_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_target(row)

    async def list_targets(
        self,
        enabled: bool | None = None,
    ) -> list[AlertDeliveryTarget]:
        query = "SELECT * FROM notification_alert_delivery_targets"
        params: list[Any] = []
        if enabled is not None:
            query += " WHERE enabled=?"
            params.append(1 if enabled else 0)
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_target(r) for r in rows]

    async def update_target(self, target: AlertDeliveryTarget) -> AlertDeliveryTarget:
        import json
        now = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            """UPDATE notification_alert_delivery_targets
               SET name=?, channel_type=?, enabled=?, severity_filter_json=?,
                   channel_filter_json=?, federation_filter_json=?, endpoint=?,
                   headers_json=?, metadata_json=?, updated_at=?
               WHERE target_id=?""",
            (
                target.name,
                target.channel_type.value,
                1 if target.enabled else 0,
                json.dumps(target.severity_filter),
                json.dumps(target.channel_filter),
                json.dumps(target.federation_filter),
                target.endpoint,
                json.dumps(target.headers),
                json.dumps(target.metadata),
                now,
                target.target_id,
            ),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Target '{target.target_id}' not found")
        self._conn.commit()
        return target

    async def delete_target(self, target_id: str) -> None:
        self._conn.execute(
            "DELETE FROM notification_alert_delivery_targets WHERE target_id=?",
            (target_id,),
        )
        self._conn.commit()

    async def record_attempt(self, attempt: AlertDeliveryAttempt) -> AlertDeliveryAttempt:
        import json
        self._conn.execute(
            """INSERT INTO notification_alert_delivery_attempts
               (attempt_id, alert_id, target_id, channel_type, status, attempt,
                next_retry_at, error_code, error_message, payload_preview_json,
                created_at, delivered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                attempt.attempt_id,
                attempt.alert_id,
                attempt.target_id,
                attempt.channel_type.value,
                attempt.status.value,
                attempt.attempt,
                attempt.next_retry_at.isoformat() if attempt.next_retry_at else None,
                attempt.error_code,
                attempt.error_message,
                json.dumps(attempt.payload_preview),
                attempt.created_at.isoformat(),
                attempt.delivered_at.isoformat() if attempt.delivered_at else None,
            ),
        )
        self._conn.commit()
        return attempt

    async def get_attempt(self, attempt_id: str) -> AlertDeliveryAttempt | None:
        row = self._conn.execute(
            "SELECT * FROM notification_alert_delivery_attempts WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_attempt(row)

    async def list_attempts(
        self,
        alert_id: str | None = None,
        target_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AlertDeliveryAttempt]:
        conditions: list[str] = []
        params: list[Any] = []

        if alert_id is not None:
            conditions.append("alert_id=?")
            params.append(alert_id)
        if target_id is not None:
            conditions.append("target_id=?")
            params.append(target_id)
        if status is not None:
            conditions.append("status=?")
            params.append(status)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        params.extend([limit, offset])
        rows = self._conn.execute(
            f"SELECT * FROM notification_alert_delivery_attempts {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [self._row_to_attempt(r) for r in rows]

    def _row_to_target(self, row: sqlite3.Row) -> AlertDeliveryTarget:
        import json
        data = dict(row)
        data["channel_type"] = data["channel_type"]
        data["severity_filter"] = json.loads(data.pop("severity_filter_json"))
        data["channel_filter"] = json.loads(data.pop("channel_filter_json"))
        data["federation_filter"] = json.loads(data.pop("federation_filter_json"))
        data["headers"] = json.loads(data.pop("headers_json"))
        data["metadata"] = json.loads(data.pop("metadata_json"))
        data["enabled"] = bool(data.pop("enabled"))
        return AlertDeliveryTarget(**data)

    def _row_to_attempt(self, row: sqlite3.Row) -> AlertDeliveryAttempt:
        import json
        data = dict(row)
        data["channel_type"] = data["channel_type"]
        data["status"] = data["status"]
        data["payload_preview"] = json.loads(data.pop("payload_preview_json"))
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        if data["delivered_at"] is not None:
            data["delivered_at"] = datetime.fromisoformat(data["delivered_at"])
        if data["next_retry_at"] is not None:
            data["next_retry_at"] = datetime.fromisoformat(data["next_retry_at"])
        return AlertDeliveryAttempt(**data)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_alert_delivery_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> AlertDeliveryStore:
    """Factory for creating alert delivery store instances."""
    if store_type == "memory":
        return InMemoryAlertDeliveryStore()
    if store_type == "sqlite":
        return SQLiteAlertDeliveryStore(
            db_path=db_path or ".agent_app/federation_notification_alert_delivery.db"
        )
    raise ValueError(
        f"Unknown alert delivery store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )
