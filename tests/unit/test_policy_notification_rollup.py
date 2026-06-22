"""Tests for Phase 53 Task 8 — Metrics rollup."""
from __future__ import annotations

import os
import tempfile
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
        # First build
        rollups1 = await svc.build_rollups(
            NotificationRollupGranularity.HOURLY,
            now - timedelta(hours=1), now + timedelta(hours=1),
        )
        assert rollups1[0].total == 2
        # Second build — should upsert, not duplicate
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

            # Verify across instance
            rollup_store2 = create_notification_rollup_store("sqlite", path)
            rollups = await rollup_store2.list_rollups()
            assert len(rollups) == 1
            rollup_store2.close()
        finally:
            os.unlink(path)


class TestInMemoryRollupStore:
    @pytest.mark.asyncio
    async def test_upsert_rollup(self):
        store = InMemoryNotificationRollupStore()
        rollup = NotificationMetricsRollup(
            rollup_id="nru_1", granularity=NotificationRollupGranularity.HOURLY,
            window_start=datetime.now(timezone.utc),
            window_end=datetime.now(timezone.utc) + timedelta(hours=1),
            channel="webhook", total=10, sent=9, failed=1,
            success_rate=0.9, failure_rate=0.1, dlq_rate=0.0,
            created_at=datetime.now(timezone.utc),
        )
        await store.upsert_rollup(rollup)
        await store.upsert_rollup(rollup)  # Should not duplicate
        rollups = await store.list_rollups()
        assert len(rollups) == 1

    @pytest.mark.asyncio
    async def test_list_rollups_empty(self):
        store = InMemoryNotificationRollupStore()
        rollups = await store.list_rollups()
        assert len(rollups) == 0


class TestFactory:
    def test_create_memory(self):
        from agent_app.runtime.policy_rollout_federation_notification_rollup import (
            create_notification_rollup_store,
        )
        store = create_notification_rollup_store("memory")
        assert isinstance(store, InMemoryNotificationRollupStore)

    def test_create_sqlite(self):
        from agent_app.runtime.policy_rollout_federation_notification_rollup import (
            create_notification_rollup_store,
        )
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            store = create_notification_rollup_store("sqlite", path)
            assert isinstance(store, SQLiteNotificationRollupStore)
            store.close()
        finally:
            os.unlink(path)
