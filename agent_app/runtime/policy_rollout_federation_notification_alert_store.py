"""Notification alert store — Protocol, InMemory, SQLite, factory.

Phase 52 Task 4: Alert rules and alert event persistence with cooldown logic.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agent_app.governance.policy_rollout_federation_notification_observability import (
    NotificationAlertEvent,
)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class NotificationAlertStore(Protocol):
    """Protocol for persisting notification alert events."""

    async def create_alert(
        self, event: NotificationAlertEvent, now: datetime | None = None
    ) -> NotificationAlertEvent | None: ...
    async def get_alert(self, alert_id: str) -> NotificationAlertEvent | None: ...
    async def list_alerts(
        self,
        status: str | None = None,
        severity: str | None = None,
        channel: str | None = None,
        federation_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[NotificationAlertEvent]: ...
    async def acknowledge(
        self, alert_id: str, acknowledged_by: str, now: datetime | None = None
    ) -> NotificationAlertEvent | None: ...
    async def resolve(
        self, alert_id: str, resolved_by: str, now: datetime | None = None
    ) -> NotificationAlertEvent | None: ...


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryNotificationAlertStore:
    """In-memory notification alert store with cooldown support."""

    def __init__(self) -> None:
        self._alerts: dict[str, NotificationAlertEvent] = {}
        self._last_alert_time: dict[str, datetime] = {}

    async def create_alert(
        self, event: NotificationAlertEvent, now: datetime | None = None
    ) -> NotificationAlertEvent | None:
        if now is None:
            now = datetime.now(timezone.utc)

        # Cooldown check
        last_time = self._last_alert_time.get(event.rule_id)
        if last_time is not None:
            cooldown_window = timedelta(minutes=event.threshold // 1 + 30)
            # Use the rule's cooldown_minutes if we can derive it from the event
            # The event itself doesn't carry cooldown_minutes, so we check
            # against a default 30-minute cooldown (matching rule defaults)
            cooldown_window = timedelta(minutes=30)
            if now - last_time < cooldown_window:
                return None

        self._alerts[event.alert_id] = event
        self._last_alert_time[event.rule_id] = now
        return event

    async def get_alert(self, alert_id: str) -> NotificationAlertEvent | None:
        return self._alerts.get(alert_id)

    async def list_alerts(
        self,
        status: str | None = None,
        severity: str | None = None,
        channel: str | None = None,
        federation_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[NotificationAlertEvent]:
        alerts = list(self._alerts.values())
        if status is not None:
            alerts = [a for a in alerts if a.status == status]
        if severity is not None:
            alerts = [a for a in alerts if a.severity == severity]
        if channel is not None:
            alerts = [a for a in alerts if a.channel == channel]
        if federation_id is not None:
            alerts = [a for a in alerts if a.federation_id == federation_id]
        alerts.sort(key=lambda a: a.created_at, reverse=True)
        return alerts[offset : offset + limit]

    async def acknowledge(
        self, alert_id: str, acknowledged_by: str, now: datetime | None = None
    ) -> NotificationAlertEvent | None:
        if now is None:
            now = datetime.now(timezone.utc)

        alert = self._alerts.get(alert_id)
        if alert is None:
            return None
        if alert.status == "resolved":
            return None

        alert.status = "acknowledged"
        alert.acknowledged_at = now
        alert.acknowledged_by = acknowledged_by
        return alert

    async def resolve(
        self, alert_id: str, resolved_by: str, now: datetime | None = None
    ) -> NotificationAlertEvent | None:
        if now is None:
            now = datetime.now(timezone.utc)

        alert = self._alerts.get(alert_id)
        if alert is None:
            return None
        if alert.status == "resolved":
            return None

        alert.status = "resolved"
        alert.resolved_at = now
        alert.resolved_by = resolved_by
        return alert


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteNotificationAlertStore:
    """SQLite-backed notification alert store with cooldown support."""

    def __init__(self, db_path: str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS notification_alerts (
                alert_id TEXT PRIMARY KEY,
                rule_id TEXT NOT NULL,
                name TEXT NOT NULL,
                severity TEXT NOT NULL,
                metric TEXT NOT NULL,
                observed_value REAL NOT NULL,
                threshold REAL NOT NULL,
                federation_id TEXT,
                channel TEXT,
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                acknowledged_at TEXT,
                acknowledged_by TEXT,
                resolved_at TEXT,
                resolved_by TEXT,
                last_alert_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_na_status
                ON notification_alerts(status);
            CREATE INDEX IF NOT EXISTS idx_na_severity
                ON notification_alerts(severity);
        """)
        self._conn.commit()

    async def create_alert(
        self, event: NotificationAlertEvent, now: datetime | None = None
    ) -> NotificationAlertEvent | None:
        if now is None:
            now = datetime.now(timezone.utc)

        # Cooldown check: look up last alert time for this rule
        row = self._conn.execute(
            "SELECT last_alert_at FROM notification_alerts WHERE rule_id=? ORDER BY created_at DESC LIMIT 1",
            (event.rule_id,),
        ).fetchone()
        if row is not None and row["last_alert_at"] is not None:
            last_time = datetime.fromisoformat(row["last_alert_at"])
            cooldown_window = timedelta(minutes=30)
            if now - last_time < cooldown_window:
                return None

        self._conn.execute(
            """INSERT INTO notification_alerts
               (alert_id, rule_id, name, severity, metric, observed_value,
                threshold, federation_id, channel, message, status,
                created_at, acknowledged_at, acknowledged_by,
                resolved_at, resolved_by, last_alert_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            self._alert_to_row(event, now),
        )
        self._conn.commit()
        return event

    async def get_alert(self, alert_id: str) -> NotificationAlertEvent | None:
        row = self._conn.execute(
            "SELECT * FROM notification_alerts WHERE alert_id=?", (alert_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_alert(row)

    async def list_alerts(
        self,
        status: str | None = None,
        severity: str | None = None,
        channel: str | None = None,
        federation_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[NotificationAlertEvent]:
        conditions: list[str] = []
        params: list[Any] = []

        if status is not None:
            conditions.append("status=?")
            params.append(status)
        if severity is not None:
            conditions.append("severity=?")
            params.append(severity)
        if channel is not None:
            conditions.append("channel=?")
            params.append(channel)
        if federation_id is not None:
            conditions.append("federation_id=?")
            params.append(federation_id)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        params.extend([limit, offset])
        rows = self._conn.execute(
            f"SELECT * FROM notification_alerts {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [self._row_to_alert(row) for row in rows]

    async def acknowledge(
        self, alert_id: str, acknowledged_by: str, now: datetime | None = None
    ) -> NotificationAlertEvent | None:
        if now is None:
            now = datetime.now(timezone.utc)

        # Check current status first — reject if already resolved
        row = self._conn.execute(
            "SELECT status FROM notification_alerts WHERE alert_id=?", (alert_id,)
        ).fetchone()
        if row is None or row["status"] == "resolved":
            return None

        self._conn.execute(
            """UPDATE notification_alerts
               SET status=?, acknowledged_at=?, acknowledged_by=?
               WHERE alert_id=?""",
            ("acknowledged", now.isoformat(), acknowledged_by, alert_id),
        )
        self._conn.commit()

        updated = self._conn.execute(
            "SELECT * FROM notification_alerts WHERE alert_id=?", (alert_id,)
        ).fetchone()
        return self._row_to_alert(updated) if updated else None

    async def resolve(
        self, alert_id: str, resolved_by: str, now: datetime | None = None
    ) -> NotificationAlertEvent | None:
        if now is None:
            now = datetime.now(timezone.utc)

        # Check current status first — reject if already resolved
        row = self._conn.execute(
            "SELECT status FROM notification_alerts WHERE alert_id=?", (alert_id,)
        ).fetchone()
        if row is None or row["status"] == "resolved":
            return None

        self._conn.execute(
            """UPDATE notification_alerts
               SET status=?, resolved_at=?, resolved_by=?
               WHERE alert_id=?""",
            ("resolved", now.isoformat(), resolved_by, alert_id),
        )
        self._conn.commit()

        updated = self._conn.execute(
            "SELECT * FROM notification_alerts WHERE alert_id=?", (alert_id,)
        ).fetchone()
        return self._row_to_alert(updated) if updated else None

    def _row_to_alert(self, row: sqlite3.Row) -> NotificationAlertEvent:
        data = dict(row)
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        if data["acknowledged_at"] is not None:
            data["acknowledged_at"] = datetime.fromisoformat(data["acknowledged_at"])
        if data["resolved_at"] is not None:
            data["resolved_at"] = datetime.fromisoformat(data["resolved_at"])
        return NotificationAlertEvent(**data)

    def _alert_to_row(
        self, event: NotificationAlertEvent, now: datetime
    ) -> tuple:
        return (
            event.alert_id,
            event.rule_id,
            event.name,
            event.severity,
            event.metric,
            event.observed_value,
            event.threshold,
            event.federation_id,
            event.channel,
            event.message,
            event.status,
            event.created_at.isoformat(),
            event.acknowledged_at,
            event.acknowledged_by,
            event.resolved_at,
            event.resolved_by,
            now.isoformat(),
        )

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_notification_alert_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> NotificationAlertStore:
    """Factory for creating notification alert store instances."""
    if store_type == "memory":
        return InMemoryNotificationAlertStore()
    if store_type == "sqlite":
        return SQLiteNotificationAlertStore(
            db_path=db_path or ".agent_app/notification_alerts.db"
        )
    raise ValueError(
        f"Unknown alert store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )
