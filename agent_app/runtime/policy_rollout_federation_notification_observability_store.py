"""Federation notification observability store — Protocol, InMemory, SQLite, factory.

Phase 52 Task 2: Delivery event persistence and metrics aggregation.
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agent_app.governance.policy_rollout_federation_notification_observability import (
    NotificationDeliveryEvent,
    NotificationDeliveryEventType,
    NotificationMetricWindow,
    _redact_sensitive_values,
)


# ---------------------------------------------------------------------------
# Sensitive field handling
# ---------------------------------------------------------------------------

_SENSITIVE_KEYS = {
    "authorization",
    "token",
    "secret",
    "password",
    "api_key",
    "x-signature",
    "x-signature-key",
    "x-api-key",
    "x-secret",
    "x-auth-token",
    "x-webhook-secret",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "www-authenticate",
    "signature",
    "key",
    "private_key",
    "access_key",
}


def _sanitize_value(key: str, value: Any) -> Any:
    if key.lower() in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {k: _sanitize_value(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [
            _sanitize_value(key, item) if isinstance(item, dict) else item
            for item in value
        ]
    return value


def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {k: _sanitize_value(k, v) for k, v in metadata.items()}


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class NotificationObservabilityStore(Protocol):
    """Protocol for persisting federation notification delivery events and metrics."""

    async def record_event(
        self, event: NotificationDeliveryEvent
    ) -> NotificationDeliveryEvent: ...
    async def get_event(self, event_id: str) -> NotificationDeliveryEvent | None: ...
    async def list_events(
        self,
        notification_id: str | None = None,
        approval_id: str | None = None,
        federation_id: str | None = None,
        channel: str | None = None,
        event_type: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[NotificationDeliveryEvent]: ...
    async def aggregate_metrics(
        self,
        federation_id: str | None = None,
        channel: str | None = None,
        window_minutes: int = 60,
        now: datetime | None = None,
    ) -> NotificationMetricWindow: ...


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryNotificationObservabilityStore:
    """In-memory federation notification observability store."""

    def __init__(self) -> None:
        self._events: dict[str, NotificationDeliveryEvent] = {}

    async def record_event(
        self, event: NotificationDeliveryEvent
    ) -> NotificationDeliveryEvent:
        self._events[event.event_id] = event
        return event

    async def get_event(self, event_id: str) -> NotificationDeliveryEvent | None:
        return self._events.get(event_id)

    async def list_events(
        self,
        notification_id: str | None = None,
        approval_id: str | None = None,
        federation_id: str | None = None,
        channel: str | None = None,
        event_type: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[NotificationDeliveryEvent]:
        events = list(self._events.values())
        if notification_id is not None:
            events = [e for e in events if e.notification_id == notification_id]
        if approval_id is not None:
            events = [e for e in events if e.approval_id == approval_id]
        if federation_id is not None:
            events = [e for e in events if e.federation_id == federation_id]
        if channel is not None:
            events = [e for e in events if e.channel == channel]
        if event_type is not None:
            events = [e for e in events if e.event_type == event_type]
        if since is not None:
            events = [e for e in events if e.created_at >= since]
        if until is not None:
            events = [e for e in events if e.created_at <= until]
        events.sort(key=lambda e: e.created_at, reverse=True)
        return events[offset : offset + limit]

    async def aggregate_metrics(
        self,
        federation_id: str | None = None,
        channel: str | None = None,
        window_minutes: int = 60,
        now: datetime | None = None,
    ) -> NotificationMetricWindow:
        if now is None:
            now = datetime.now(timezone.utc)
        window_start = now - timedelta(minutes=window_minutes)

        events = [
            e
            for e in self._events.values()
            if e.created_at >= window_start
            and e.created_at <= now
            and (federation_id is None or e.federation_id == federation_id)
            and (channel is None or e.channel == channel)
        ]

        total = len(events)
        sent = sum(
            1 for e in events if e.event_type == NotificationDeliveryEventType.SENT
        )
        failed = sum(
            1 for e in events if e.event_type == NotificationDeliveryEventType.FAILED
        )
        suppressed = sum(
            1
            for e in events
            if e.event_type == NotificationDeliveryEventType.SUPPRESSED
        )
        dlq = sum(
            1
            for e in events
            if e.event_type == NotificationDeliveryEventType.DLQ_CREATED
        )
        retry_scheduled = sum(
            1
            for e in events
            if e.event_type == NotificationDeliveryEventType.RETRY_SCHEDULED
        )

        success_rate = sent / total if total > 0 else 0.0
        failure_rate = failed / total if total > 0 else 0.0
        dlq_rate = dlq / total if total > 0 else 0.0

        latencies = [e.latency_ms for e in events if e.latency_ms is not None]
        avg_latency_ms = sum(latencies) / len(latencies) if latencies else None
        p95_latency_ms = None
        if latencies:
            sorted_latencies = sorted(latencies)
            idx = math.ceil(len(sorted_latencies) * 0.95) - 1
            p95_latency_ms = sorted_latencies[idx]

        return NotificationMetricWindow(
            window_start=window_start,
            window_end=now,
            federation_id=federation_id,
            channel=channel,
            total=total,
            sent=sent,
            failed=failed,
            suppressed=suppressed,
            dlq=dlq,
            retry_scheduled=retry_scheduled,
            success_rate=success_rate,
            failure_rate=failure_rate,
            dlq_rate=dlq_rate,
            avg_latency_ms=avg_latency_ms,
            p95_latency_ms=p95_latency_ms,
        )


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteNotificationObservabilityStore:
    """SQLite-backed federation notification observability store."""

    def __init__(self, db_path: str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS notification_delivery_events (
                event_id TEXT PRIMARY KEY,
                notification_id TEXT,
                approval_id TEXT,
                federation_id TEXT,
                channel TEXT,
                event_type TEXT NOT NULL,
                status TEXT,
                attempt INTEGER,
                latency_ms INTEGER,
                error_code TEXT,
                error_message TEXT,
                adapter_name TEXT,
                template_id TEXT,
                preference_decision TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_nde_notification_id
                ON notification_delivery_events(notification_id);
            CREATE INDEX IF NOT EXISTS idx_nde_fed_channel_created
                ON notification_delivery_events(federation_id, channel, created_at);
            CREATE INDEX IF NOT EXISTS idx_nde_type_created
                ON notification_delivery_events(event_type, created_at);
        """)
        self._conn.commit()

    async def record_event(
        self, event: NotificationDeliveryEvent
    ) -> NotificationDeliveryEvent:
        self._conn.execute(
            """INSERT INTO notification_delivery_events
               (event_id, notification_id, approval_id, federation_id, channel,
                event_type, status, attempt, latency_ms, error_code, error_message,
                adapter_name, template_id, preference_decision, metadata_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            self._event_to_row(event),
        )
        self._conn.commit()
        return event

    async def get_event(self, event_id: str) -> NotificationDeliveryEvent | None:
        row = self._conn.execute(
            "SELECT * FROM notification_delivery_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_event(row)

    async def list_events(
        self,
        notification_id: str | None = None,
        approval_id: str | None = None,
        federation_id: str | None = None,
        channel: str | None = None,
        event_type: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[NotificationDeliveryEvent]:
        conditions: list[str] = []
        params: list[Any] = []

        if notification_id is not None:
            conditions.append("notification_id=?")
            params.append(notification_id)
        if approval_id is not None:
            conditions.append("approval_id=?")
            params.append(approval_id)
        if federation_id is not None:
            conditions.append("federation_id=?")
            params.append(federation_id)
        if channel is not None:
            conditions.append("channel=?")
            params.append(channel)
        if event_type is not None:
            conditions.append("event_type=?")
            params.append(event_type)
        if since is not None:
            conditions.append("created_at>=?")
            params.append(since.isoformat())
        if until is not None:
            conditions.append("created_at<=?")
            params.append(until.isoformat())

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        params.extend([limit, offset])
        rows = self._conn.execute(
            f"SELECT * FROM notification_delivery_events {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [self._row_to_event(row) for row in rows]

    async def aggregate_metrics(
        self,
        federation_id: str | None = None,
        channel: str | None = None,
        window_minutes: int = 60,
        now: datetime | None = None,
    ) -> NotificationMetricWindow:
        if now is None:
            now = datetime.now(timezone.utc)
        window_start = now - timedelta(minutes=window_minutes)

        conditions: list[str] = ["created_at >= ?", "created_at <= ?"]
        params: list[Any] = [window_start.isoformat(), now.isoformat()]

        if federation_id is not None:
            conditions.append("federation_id = ?")
            params.append(federation_id)
        if channel is not None:
            conditions.append("channel = ?")
            params.append(channel)

        where = "WHERE " + " AND ".join(conditions)

        row = self._conn.execute(
            f"""SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN event_type = 'sent' THEN 1 ELSE 0 END) as sent,
                    SUM(CASE WHEN event_type = 'failed' THEN 1 ELSE 0 END) as failed,
                    SUM(CASE WHEN event_type = 'suppressed' THEN 1 ELSE 0 END) as suppressed,
                    SUM(CASE WHEN event_type = 'dlq_created' THEN 1 ELSE 0 END) as dlq,
                    SUM(CASE WHEN event_type = 'retry_scheduled' THEN 1 ELSE 0 END) as retry_scheduled,
                    AVG(latency_ms) as avg_latency
                FROM notification_delivery_events
                {where}""",
            params,
        ).fetchone()

        total = row["total"] or 0
        sent = row["sent"] or 0
        failed = row["failed"] or 0
        suppressed = row["suppressed"] or 0
        dlq = row["dlq"] or 0
        retry_scheduled = row["retry_scheduled"] or 0
        avg_latency_ms = row["avg_latency"]

        success_rate = sent / total if total > 0 else 0.0
        failure_rate = failed / total if total > 0 else 0.0
        dlq_rate = dlq / total if total > 0 else 0.0

        # P95 latency: fetch sorted values and compute percentile in Python
        latency_rows = self._conn.execute(
            f"""SELECT latency_ms FROM notification_delivery_events
                {where} AND latency_ms IS NOT NULL
                ORDER BY latency_ms""",
            params,
        ).fetchall()

        p95_latency_ms = None
        latencies = [r["latency_ms"] for r in latency_rows]
        if latencies:
            idx = math.ceil(len(latencies) * 0.95) - 1
            p95_latency_ms = latencies[idx]

        return NotificationMetricWindow(
            window_start=window_start,
            window_end=now,
            federation_id=federation_id,
            channel=channel,
            total=total,
            sent=sent,
            failed=failed,
            suppressed=suppressed,
            dlq=dlq,
            retry_scheduled=retry_scheduled,
            success_rate=success_rate,
            failure_rate=failure_rate,
            dlq_rate=dlq_rate,
            avg_latency_ms=avg_latency_ms,
            p95_latency_ms=p95_latency_ms,
        )

    def _row_to_event(self, row: sqlite3.Row) -> NotificationDeliveryEvent:
        data = dict(row)
        data["event_type"] = NotificationDeliveryEventType(data["event_type"])
        data["metadata"] = json.loads(data.pop("metadata_json"))
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        return NotificationDeliveryEvent(**data)

    def _event_to_row(self, event: NotificationDeliveryEvent) -> tuple:
        sanitized_metadata = _sanitize_metadata(event.metadata)
        sanitized_error = (
            _redact_sensitive_values(event.error_message)
            if event.error_message
            else None
        )
        return (
            event.event_id,
            event.notification_id,
            event.approval_id,
            event.federation_id,
            event.channel,
            event.event_type.value,
            event.status,
            event.attempt,
            event.latency_ms,
            event.error_code,
            sanitized_error,
            event.adapter_name,
            event.template_id,
            event.preference_decision,
            json.dumps(sanitized_metadata),
            event.created_at.isoformat(),
        )

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_notification_observability_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> NotificationObservabilityStore:
    """Factory for creating notification observability store instances."""
    if store_type == "memory":
        return InMemoryNotificationObservabilityStore()
    if store_type == "sqlite":
        return SQLiteNotificationObservabilityStore(
            db_path=db_path or ".agent_app/notification_observability.db"
        )
    raise ValueError(
        f"Unknown observability store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )
