"""Metrics rollup — hourly/daily aggregation of delivery events.

Phase 53 Task 8: Metrics rollup.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator

from agent_app.governance.policy_rollout_federation_notification_observability import (
    NotificationDeliveryEventType,
    NotificationMetricWindow,
    NotificationRollupGranularity,
)


class NotificationMetricsRollup(BaseModel):
    """Aggregated metrics for a time window."""

    rollup_id: str
    granularity: NotificationRollupGranularity
    window_start: datetime
    window_end: datetime
    federation_id: str | None = None
    channel: str | None = None
    total: int = 0
    sent: int = 0
    failed: int = 0
    suppressed: int = 0
    dlq: int = 0
    retry_scheduled: int = 0
    success_rate: float = 0.0
    failure_rate: float = 0.0
    dlq_rate: float = 0.0
    avg_latency_ms: float | None = None
    p95_latency_ms: float | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("window_start")
    @classmethod
    def _validate_window_start_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("window_start must be timezone-aware")
        return v

    @field_validator("window_end")
    @classmethod
    def _validate_window_end_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("window_end must be timezone-aware")
        return v

    @field_validator("rollup_id")
    @classmethod
    def _validate_rollup_id(cls, v: str) -> str:
        if not v.startswith("nru_"):
            raise ValueError(f"rollup_id must start with 'nru_', got '{v}'")
        return v


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class NotificationRollupStore(Protocol):
    async def upsert_rollup(self, rollup: NotificationMetricsRollup) -> None: ...
    async def get_rollup(self, rollup_id: str) -> NotificationMetricsRollup | None: ...
    async def list_rollups(
        self,
        granularity: NotificationRollupGranularity | None = None,
        channel: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[NotificationMetricsRollup]: ...


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryNotificationRollupStore:
    """In-memory rollup store with upsert support."""

    def __init__(self) -> None:
        self._rollups: dict[str, NotificationMetricsRollup] = {}

    async def upsert_rollup(self, rollup: NotificationMetricsRollup) -> None:
        self._rollups[rollup.rollup_id] = rollup

    async def get_rollup(self, rollup_id: str) -> NotificationMetricsRollup | None:
        return self._rollups.get(rollup_id)

    async def list_rollups(
        self,
        granularity: NotificationRollupGranularity | None = None,
        channel: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[NotificationMetricsRollup]:
        rollups = list(self._rollups.values())
        if granularity is not None:
            rollups = [r for r in rollups if r.granularity == granularity]
        if channel is not None:
            rollups = [r for r in rollups if r.channel == channel]
        rollups.sort(key=lambda r: r.window_start, reverse=True)
        return rollups[offset: offset + limit]


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteNotificationRollupStore:
    """SQLite-backed rollup store."""

    def __init__(self, db_path: str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS notification_rollups (
                rollup_id TEXT PRIMARY KEY,
                granularity TEXT NOT NULL,
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                federation_id TEXT,
                channel TEXT,
                total INTEGER NOT NULL,
                sent INTEGER NOT NULL,
                failed INTEGER NOT NULL,
                suppressed INTEGER NOT NULL,
                dlq INTEGER NOT NULL,
                retry_scheduled INTEGER NOT NULL,
                success_rate REAL NOT NULL,
                failure_rate REAL NOT NULL,
                dlq_rate REAL NOT NULL,
                avg_latency_ms REAL,
                p95_latency_ms REAL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_nru_granularity
                ON notification_rollups(granularity);
            CREATE INDEX IF NOT EXISTS idx_nru_channel
                ON notification_rollups(channel);
            CREATE INDEX IF NOT EXISTS idx_nru_window
                ON notification_rollups(window_start);
        """)
        self._conn.commit()

    async def upsert_rollup(self, rollup: NotificationMetricsRollup) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO notification_rollups
               (rollup_id, granularity, window_start, window_end, federation_id,
                channel, total, sent, failed, suppressed, dlq, retry_scheduled,
                success_rate, failure_rate, dlq_rate, avg_latency_ms, p95_latency_ms,
                created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rollup.rollup_id,
                rollup.granularity.value,
                rollup.window_start.isoformat(),
                rollup.window_end.isoformat(),
                rollup.federation_id,
                rollup.channel,
                rollup.total,
                rollup.sent,
                rollup.failed,
                rollup.suppressed,
                rollup.dlq,
                rollup.retry_scheduled,
                rollup.success_rate,
                rollup.failure_rate,
                rollup.dlq_rate,
                rollup.avg_latency_ms,
                rollup.p95_latency_ms,
                rollup.created_at.isoformat(),
            ),
        )
        self._conn.commit()

    async def get_rollup(self, rollup_id: str) -> NotificationMetricsRollup | None:
        row = self._conn.execute(
            "SELECT * FROM notification_rollups WHERE rollup_id=?", (rollup_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_rollup(row)

    async def list_rollups(
        self,
        granularity: NotificationRollupGranularity | None = None,
        channel: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[NotificationMetricsRollup]:
        conditions: list[str] = []
        params: list[Any] = []

        if granularity is not None:
            conditions.append("granularity=?")
            params.append(granularity.value)
        if channel is not None:
            conditions.append("channel=?")
            params.append(channel)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        params.extend([limit, offset])
        rows = self._conn.execute(
            f"SELECT * FROM notification_rollups {where} "
            "ORDER BY window_start DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [self._row_to_rollup(r) for r in rows]

    def _row_to_rollup(self, row: sqlite3.Row) -> NotificationMetricsRollup:
        data = dict(row)
        data["granularity"] = NotificationRollupGranularity(data["granularity"])
        data["window_start"] = datetime.fromisoformat(data["window_start"])
        data["window_end"] = datetime.fromisoformat(data["window_end"])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        return NotificationMetricsRollup(**data)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_notification_rollup_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> NotificationRollupStore:
    """Factory for creating rollup store instances."""
    if store_type == "memory":
        return InMemoryNotificationRollupStore()
    if store_type == "sqlite":
        return SQLiteNotificationRollupStore(
            db_path=db_path or ".agent_app/federation_notification_rollups.db"
        )
    raise ValueError(
        f"Unknown rollup store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )


# ---------------------------------------------------------------------------
# Rollup Service
# ---------------------------------------------------------------------------


class NotificationRollupService:
    """Aggregates raw delivery events into hourly/daily metrics windows."""

    def __init__(
        self,
        observability_store: Any,
        rollup_store: NotificationRollupStore,
    ) -> None:
        self._observability_store = observability_store
        self._rollup_store = rollup_store

    async def build_rollups(
        self,
        granularity: NotificationRollupGranularity,
        since: datetime,
        until: datetime,
        federation_id: str | None = None,
        channel: str | None = None,
    ) -> list[NotificationMetricsRollup]:
        """Build rollup metrics from raw events.

        Upserts existing rollups for the same window.
        """
        events = await self._observability_store.list_events(
            since=since,
            until=until,
            limit=10000,
        )

        # Filter by channel/federation
        if channel is not None:
            events = [e for e in events if e.channel == channel]
        if federation_id is not None:
            events = [e for e in events if e.federation_id == federation_id]

        # Bucket events by window
        buckets: dict[tuple, list[Any]] = defaultdict(list)
        for event in events:
            bucket_key = self._bucket_key(event, granularity, channel, federation_id)
            buckets[bucket_key].append(event)

        rollups: list[NotificationMetricsRollup] = []
        for (window_start, w_channel, w_federation), evts in buckets.items():
            window_end = self._window_end(window_start, granularity)
            rollup = self._aggregate(
                granularity, window_start, window_end, w_channel, w_federation, evts,
            )
            await self._rollup_store.upsert_rollup(rollup)
            rollups.append(rollup)

        return rollups

    def _bucket_key(self, event: Any, granularity: NotificationRollupGranularity,
                    channel: str | None, federation_id: str | None) -> tuple:
        if granularity == NotificationRollupGranularity.HOURLY:
            bucket = event.created_at.replace(minute=0, second=0, microsecond=0)
        else:
            bucket = event.created_at.replace(hour=0, minute=0, second=0, microsecond=0)
        ch = channel if channel is not None else event.channel
        fid = federation_id if federation_id is not None else event.federation_id
        return (bucket, ch, fid)

    def _window_end(self, start: datetime, granularity: NotificationRollupGranularity) -> datetime:
        if granularity == NotificationRollupGranularity.HOURLY:
            return start + timedelta(hours=1)
        return start + timedelta(days=1)

    def _aggregate(
        self,
        granularity: NotificationRollupGranularity,
        window_start: datetime,
        window_end: datetime,
        channel: str | None,
        federation_id: str | None,
        events: list[Any],
    ) -> NotificationMetricsRollup:
        total = len(events)
        sent = sum(1 for e in events if e.event_type == NotificationDeliveryEventType.SENT)
        failed = sum(1 for e in events if e.event_type == NotificationDeliveryEventType.FAILED)
        suppressed = sum(1 for e in events if e.event_type == NotificationDeliveryEventType.SUPPRESSED)
        dlq = sum(1 for e in events if e.event_type == NotificationDeliveryEventType.DLQ_CREATED)
        retry_scheduled = sum(1 for e in events if e.event_type == NotificationDeliveryEventType.RETRY_SCHEDULED)

        success_rate = sent / total if total > 0 else 0.0
        failure_rate = failed / total if total > 0 else 0.0
        dlq_rate = dlq / total if total > 0 else 0.0

        latencies = [e.latency_ms for e in events if e.latency_ms is not None]
        avg_latency = sum(latencies) / len(latencies) if latencies else None
        p95_latency = self._percentile(latencies, 95) if latencies else None

        rollup_id = f"nru_{granularity.value}_{window_start.strftime('%Y%m%d%H%M')}"
        if channel:
            rollup_id += f"_{channel}"
        if federation_id:
            rollup_id += f"_{federation_id}"

        return NotificationMetricsRollup(
            rollup_id=rollup_id,
            granularity=granularity,
            window_start=window_start,
            window_end=window_end,
            federation_id=federation_id,
            channel=channel,
            total=total, sent=sent, failed=failed, suppressed=suppressed,
            dlq=dlq, retry_scheduled=retry_scheduled,
            success_rate=round(success_rate, 6),
            failure_rate=round(failure_rate, 6),
            dlq_rate=round(dlq_rate, 6),
            avg_latency_ms=avg_latency,
            p95_latency_ms=p95_latency,
        )

    @staticmethod
    def _percentile(values: list[float], pct: float) -> float | None:
        if not values:
            return None
        sorted_vals = sorted(values)
        idx = (pct / 100) * (len(sorted_vals) - 1)
        lower = int(idx)
        upper = min(lower + 1, len(sorted_vals) - 1)
        frac = idx - lower
        return sorted_vals[lower] + frac * (sorted_vals[upper] - sorted_vals[lower])
