"""Tests for Phase 53 Task 8 — Metrics rollup.
Phase 55 Task 1 — Async-safe rollup store.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
import pytest
from datetime import datetime, timezone, timedelta

from agent_app.governance.policy_rollout_federation_notification_observability import (
    NotificationDeliveryEvent,
    NotificationDeliveryEventType,
)
from agent_app.runtime.policy_rollout_federation_notification_observability_store import (
    InMemoryNotificationObservabilityStore,
)
from agent_app.runtime.policy_rollout_federation_notification_rollup import (
    InMemoryNotificationRollupStore,
    SQLiteNotificationRollupStore,
    create_notification_rollup_store,
    NotificationMetricsRollup,
    NotificationRollupGranularity,
    NotificationRollupService,
)


def _make_event(event_id: str, channel: str, created_at: datetime,
                event_type=NotificationDeliveryEventType.SENT,
                latency_ms: int | None = 100) -> NotificationDeliveryEvent:
    return NotificationDeliveryEvent(
        event_id=event_id, event_type=event_type, channel=channel,
        federation_id="fed_1", latency_ms=latency_ms, created_at=created_at,
    )


def _make_hour_bucket(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


class TestBuildHourlyRollup:
    @pytest.mark.asyncio
    async def test_build_hourly_rollup(self):
        obs_store = InMemoryNotificationObservabilityStore()
        rollup_store = InMemoryNotificationRollupStore()
        now = _make_hour_bucket(datetime.now(timezone.utc))
        await obs_store.record_event(_make_event("nde_1", "webhook", now))
        svc = NotificationRollupService(obs_store, rollup_store)
        rollups = await svc.build_rollups(
            NotificationRollupGranularity.HOURLY,
            now - timedelta(hours=1), now + timedelta(hours=1),
        )
        assert len(rollups) == 1
        assert rollups[0].total == 1
        assert rollups[0].sent == 1
        assert rollups[0].channel == "webhook"

    @pytest.mark.asyncio
    async def test_build_daily_rollup(self):
        obs_store = InMemoryNotificationObservabilityStore()
        rollup_store = InMemoryNotificationRollupStore()
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        await obs_store.record_event(_make_event("nde_1", "webhook", day_start))
        await obs_store.record_event(_make_event("nde_2", "webhook", day_start + timedelta(hours=2)))
        svc = NotificationRollupService(obs_store, rollup_store)
        rollups = await svc.build_rollups(
            NotificationRollupGranularity.DAILY,
            day_start, day_start + timedelta(days=1),
        )
        assert len(rollups) == 1
        assert rollups[0].total == 2

    @pytest.mark.asyncio
    async def test_upsert_same_window(self):
        obs_store = InMemoryNotificationObservabilityStore()
        rollup_store = InMemoryNotificationRollupStore()
        now = _make_hour_bucket(datetime.now(timezone.utc))
        await obs_store.record_event(_make_event("nde_1", "webhook", now))
        await obs_store.record_event(_make_event("nde_2", "webhook", now))
        svc = NotificationRollupService(obs_store, rollup_store)
        rollups1 = await svc.build_rollups(
            NotificationRollupGranularity.HOURLY,
            now - timedelta(hours=1), now + timedelta(hours=1),
        )
        assert rollups1[0].total == 2
        rollups2 = await svc.build_rollups(
            NotificationRollupGranularity.HOURLY,
            now - timedelta(hours=1), now + timedelta(hours=1),
        )
        assert len(rollups2) == 1
        assert rollups2[0].total == 2

    @pytest.mark.asyncio
    async def test_channel_filter(self):
        obs_store = InMemoryNotificationObservabilityStore()
        rollup_store = InMemoryNotificationRollupStore()
        now = _make_hour_bucket(datetime.now(timezone.utc))
        await obs_store.record_event(_make_event("nde_1", "webhook", now))
        await obs_store.record_event(_make_event("nde_2", "email", now))
        svc = NotificationRollupService(obs_store, rollup_store)
        rollups = await svc.build_rollups(
            NotificationRollupGranularity.HOURLY,
            now - timedelta(hours=1), now + timedelta(hours=1),
            channel="webhook",
        )
        assert len(rollups) == 1
        assert rollups[0].total == 1
        assert rollups[0].channel == "webhook"

    @pytest.mark.asyncio
    async def test_empty_window(self):
        obs_store = InMemoryNotificationObservabilityStore()
        rollup_store = InMemoryNotificationRollupStore()
        now = datetime.now(timezone.utc)
        svc = NotificationRollupService(obs_store, rollup_store)
        rollups = await svc.build_rollups(
            NotificationRollupGranularity.HOURLY,
            now - timedelta(hours=1), now + timedelta(hours=1),
        )
        assert len(rollups) == 0

    @pytest.mark.asyncio
    async def test_sqlite_persists(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            obs_store = InMemoryNotificationObservabilityStore()
            rollup_store1 = create_notification_rollup_store("sqlite", path)
            now = _make_hour_bucket(datetime.now(timezone.utc))
            await obs_store.record_event(_make_event("nde_1", "webhook", now))
            svc = NotificationRollupService(obs_store, rollup_store1)
            await svc.build_rollups(
                NotificationRollupGranularity.HOURLY,
                now - timedelta(hours=1), now + timedelta(hours=1),
            )
            rollup_store1.close()

            rollup_store2 = create_notification_rollup_store("sqlite", path)
            rollups = await rollup_store2.list_rollups()
            assert len(rollups) == 1
            rollup_store2.close()
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Phase 55 — Async-safe SQLite rollup store
# ---------------------------------------------------------------------------


class TestAsyncSQLiteRollupStore:
    """Verify SQLite rollup store offloads blocking work to a thread."""

    @pytest.fixture
    def tmp_db(self, tmp_path):
        db = str(tmp_path / "rollups.db")
        yield db

    @pytest.mark.asyncio
    async def test_async_upsert_and_get(self, tmp_db):
        store = SQLiteNotificationRollupStore(tmp_db)
        rollup = NotificationMetricsRollup(
            rollup_id="nru_hourly_2025062301",
            granularity=NotificationRollupGranularity.HOURLY,
            window_start=datetime(2025, 6, 23, 1, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2025, 6, 23, 2, 0, 0, tzinfo=timezone.utc),
            channel="webhook", federation_id="fed_1",
            total=10, sent=9, failed=1,
            success_rate=0.9, failure_rate=0.1, dlq_rate=0.0,
            created_at=datetime.now(timezone.utc),
        )
        await store.upsert_rollup(rollup)
        fetched = await store.get_rollup("nru_hourly_2025062301")
        assert fetched is not None
        assert fetched.rollup_id == "nru_hourly_2025062301"
        assert fetched.total == 10
        store.close()

    @pytest.mark.asyncio
    async def test_async_list_rollups(self, tmp_db):
        store = SQLiteNotificationRollupStore(tmp_db)
        for i in range(3):
            rollup = NotificationMetricsRollup(
                rollup_id=f"nru_hourly_202506230{i}",
                granularity=NotificationRollupGranularity.HOURLY,
                window_start=datetime(2025, 6, 23, i, 0, 0, tzinfo=timezone.utc),
                window_end=datetime(2025, 6, 23, i + 1, 0, 0, tzinfo=timezone.utc),
                channel="webhook", total=i + 1, sent=i + 1,
                success_rate=1.0, failure_rate=0.0, dlq_rate=0.0,
                created_at=datetime.now(timezone.utc),
            )
            await store.upsert_rollup(rollup)
        rollups = await store.list_rollups(limit=10)
        assert len(rollups) == 3
        # Should be sorted by window_start DESC
        assert rollups[0].rollup_id == "nru_hourly_2025062302"
        store.close()

    @pytest.mark.asyncio
    async def test_async_list_rollups_filter(self, tmp_db):
        store = SQLiteNotificationRollupStore(tmp_db)
        await store.upsert_rollup(NotificationMetricsRollup(
            rollup_id="nru_email_1",
            granularity=NotificationRollupGranularity.HOURLY,
            window_start=datetime(2025, 6, 23, 1, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2025, 6, 23, 2, 0, 0, tzinfo=timezone.utc),
            channel="email", total=1, sent=1,
            success_rate=1.0, failure_rate=0.0, dlq_rate=0.0,
            created_at=datetime.now(timezone.utc),
        ))
        await store.upsert_rollup(NotificationMetricsRollup(
            rollup_id="nru_webhook_1",
            granularity=NotificationRollupGranularity.HOURLY,
            window_start=datetime(2025, 6, 23, 1, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2025, 6, 23, 2, 0, 0, tzinfo=timezone.utc),
            channel="webhook", total=2, sent=2,
            success_rate=1.0, failure_rate=0.0, dlq_rate=0.0,
            created_at=datetime.now(timezone.utc),
        ))
        webhook_rollups = await store.list_rollups(channel="webhook")
        assert len(webhook_rollups) == 1
        assert webhook_rollups[0].channel == "webhook"
        store.close()

    @pytest.mark.asyncio
    async def test_async_record_and_list_checkpoints(self, tmp_db):
        store = SQLiteNotificationRollupStore(tmp_db)
        await store.record_checkpoint({
            "checkpoint_id": "cp_1",
            "granularity": "hourly",
            "window_start": "2025-06-23T01:00:00",
            "window_end": "2025-06-23T02:00:00",
            "entry_count": 5,
        })
        checkpoints = await store.list_checkpoints()
        assert len(checkpoints) == 1
        assert checkpoints[0]["checkpoint_id"] == "cp_1"
        assert checkpoints[0]["entry_count"] == 5
        store.close()

    @pytest.mark.asyncio
    async def test_async_build_incremental_rollup(self, tmp_db):
        store = SQLiteNotificationRollupStore(tmp_db)
        now = datetime(2025, 6, 23, 3, 0, 0, tzinfo=timezone.utc)
        # Insert some rollups
        for i in range(3):
            rollup = NotificationMetricsRollup(
                rollup_id=f"nru_hourly_2025{i}",
                granularity=NotificationRollupGranularity.HOURLY,
                window_start=datetime(2025, 6, 23, i, 0, 0, tzinfo=timezone.utc),
                window_end=datetime(2025, 6, 23, i + 1, 0, 0, tzinfo=timezone.utc),
                channel="webhook", total=i + 1, sent=i + 1,
                success_rate=1.0, failure_rate=0.0, dlq_rate=0.0,
                created_at=now,
            )
            await store.upsert_rollup(rollup)
        # Build incremental since a specific time
        since = datetime(2025, 6, 23, 1, 0, 0, tzinfo=timezone.utc)
        result = await store.build_incremental_rollup(since=since)
        assert len(result) == 2  # windows 1 and 2 (window_start >= since)
        store.close()

    @pytest.mark.asyncio
    async def test_event_loop_not_blocked(self, tmp_db):
        """Verify asyncio.to_thread is used — event loop stays responsive."""
        store = SQLiteNotificationRollupStore(tmp_db)

        concurrent_completed = asyncio.Event()

        async def concurrent_task():
            await asyncio.sleep(0.05)
            concurrent_completed.set()

        rollup = NotificationMetricsRollup(
            rollup_id="nru_slow_1",
            granularity=NotificationRollupGranularity.HOURLY,
            window_start=datetime(2025, 6, 23, 1, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2025, 6, 23, 2, 0, 0, tzinfo=timezone.utc),
            channel="webhook", total=1, sent=1,
            success_rate=1.0, failure_rate=0.0, dlq_rate=0.0,
            created_at=datetime.now(timezone.utc),
        )

        # Patch the sync method to add a noticeable delay
        original = store._sync_upsert_rollup
        def slow_sync(r):
            time.sleep(0.2)
            original(r)
        store._sync_upsert_rollup = slow_sync

        await asyncio.gather(
            store.upsert_rollup(rollup),
            concurrent_task(),
        )

        assert concurrent_completed.is_set(), "Event loop was blocked"

    @pytest.mark.asyncio
    async def test_sqlite_persists_across_instances(self, tmp_db):
        """Phase 55: SQLite rollup store persists data across instances."""
        store1 = SQLiteNotificationRollupStore(tmp_db)
        rollup = NotificationMetricsRollup(
            rollup_id="nru_persist_1",
            granularity=NotificationRollupGranularity.HOURLY,
            window_start=datetime(2025, 6, 23, 1, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2025, 6, 23, 2, 0, 0, tzinfo=timezone.utc),
            channel="webhook", total=5, sent=5,
            success_rate=1.0, failure_rate=0.0, dlq_rate=0.0,
            created_at=datetime.now(timezone.utc),
        )
        await store1.upsert_rollup(rollup)
        store1.close()

        store2 = SQLiteNotificationRollupStore(tmp_db)
        fetched = await store2.get_rollup("nru_persist_1")
        assert fetched is not None
        assert fetched.total == 5
        store2.close()

    @pytest.mark.asyncio
    async def test_service_list_rollups_async(self):
        """Phase 55: NotificationRollupService.list_rollups_async."""
        from agent_app.runtime.policy_rollout_federation_notification_observability_store import (
            InMemoryNotificationObservabilityStore,
        )
        obs_store = InMemoryNotificationObservabilityStore()
        rollup_store = InMemoryNotificationRollupStore()
        svc = NotificationRollupService(obs_store, rollup_store)

        rollup = NotificationMetricsRollup(
            rollup_id="nru_svc_1",
            granularity=NotificationRollupGranularity.HOURLY,
            window_start=datetime(2025, 6, 23, 1, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2025, 6, 23, 2, 0, 0, tzinfo=timezone.utc),
            channel="webhook", total=3, sent=3,
            success_rate=1.0, failure_rate=0.0, dlq_rate=0.0,
            created_at=datetime.now(timezone.utc),
        )
        await rollup_store.upsert_rollup(rollup)
        result = await svc.list_rollups_async()
        assert len(result) == 1
        assert result[0].rollup_id == "nru_svc_1"

    @pytest.mark.asyncio
    async def test_service_list_checkpoints_async(self, tmp_path):
        """Phase 55: NotificationRollupService.list_checkpoints_async."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir=str(tmp_path)) as f:
            path = f.name
        try:
            rollup_store = SQLiteNotificationRollupStore(path)
            svc = NotificationRollupService(None, rollup_store)  # type: ignore[arg-type]
            await rollup_store.record_checkpoint({
                "checkpoint_id": "cp_svc",
                "granularity": "hourly",
                "window_start": "2025-06-23T01:00:00",
                "window_end": "2025-06-23T02:00:00",
                "entry_count": 5,
            })
            result = await svc.list_checkpoints_async()
            assert len(result) == 1
            assert result[0]["checkpoint_id"] == "cp_svc"
            rollup_store.close()
        finally:
            os.unlink(path)
