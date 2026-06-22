"""Tests for Phase 53 Task 7 — Retention service."""
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
from agent_app.runtime.policy_rollout_federation_notification_alert_store import (
    InMemoryNotificationAlertStore,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_store import (
    InMemoryAlertDeliveryStore,
)
from agent_app.runtime.policy_rollout_federation_notification_retention import (
    NotificationRetentionPolicy,
    NotificationRetentionResult,
    NotificationRetentionService,
)


def _make_event(event_id: str, created_at: datetime) -> NotificationDeliveryEvent:
    return NotificationDeliveryEvent(
        event_id=event_id, event_type=NotificationDeliveryEventType.SENT,
        channel="webhook", created_at=created_at,
    )


class TestRetentionService:
    @pytest.mark.asyncio
    async def test_dry_run_does_not_delete_events(self):
        store = InMemoryNotificationObservabilityStore()
        old = datetime.now(timezone.utc) - timedelta(days=60)
        await store.record_event(_make_event("nde_old", old))
        policy = NotificationRetentionPolicy(enabled=True, raw_event_retention_days=30)
        svc = NotificationRetentionService(observability_store=store, policy=policy)
        result = await svc.run_cleanup(dry_run=True)
        assert result.dry_run is True
        assert result.events_deleted == 0

    @pytest.mark.asyncio
    async def test_cleanup_deletes_old_events(self):
        store = InMemoryNotificationObservabilityStore()
        old = datetime.now(timezone.utc) - timedelta(days=60)
        await store.record_event(_make_event("nde_old", old))
        await store.record_event(_make_event("nde_new", datetime.now(timezone.utc)))
        policy = NotificationRetentionPolicy(enabled=True, raw_event_retention_days=30)
        svc = NotificationRetentionService(observability_store=store, policy=policy)
        result = await svc.run_cleanup(dry_run=False)
        assert result.events_deleted == 1

    @pytest.mark.asyncio
    async def test_cleanup_keeps_recent_events(self):
        store = InMemoryNotificationObservabilityStore()
        await store.record_event(_make_event("nde_new", datetime.now(timezone.utc)))
        policy = NotificationRetentionPolicy(enabled=True, raw_event_retention_days=30)
        svc = NotificationRetentionService(observability_store=store, policy=policy)
        result = await svc.run_cleanup(dry_run=False)
        assert result.events_deleted == 0

    @pytest.mark.asyncio
    async def test_disabled_policy_no_op(self):
        store = InMemoryNotificationObservabilityStore()
        old = datetime.now(timezone.utc) - timedelta(days=60)
        await store.record_event(_make_event("nde_old", old))
        policy = NotificationRetentionPolicy(enabled=False)
        svc = NotificationRetentionService(observability_store=store, policy=policy)
        result = await svc.run_cleanup(dry_run=False)
        assert result.events_deleted == 0

    @pytest.mark.asyncio
    async def test_result_counts_correct(self):
        store = InMemoryNotificationObservabilityStore()
        old = datetime.now(timezone.utc) - timedelta(days=60)
        await store.record_event(_make_event("nde_old", old))
        policy = NotificationRetentionPolicy(enabled=True, raw_event_retention_days=30)
        svc = NotificationRetentionService(observability_store=store, policy=policy)
        result = await svc.run_cleanup(dry_run=False)
        assert result.events_deleted == 1
        assert result.dry_run is False
