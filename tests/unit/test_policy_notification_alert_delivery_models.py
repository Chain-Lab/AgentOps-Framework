"""Tests for Phase 53 Task 3 — Alert delivery service and adapters."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryChannelType,
    AlertDeliveryStatus,
    AlertDeliveryTarget,
    AlertDeliveryAttempt,
    AlertDeliveryRetryPolicy,
)
from agent_app.governance.policy_rollout_federation_notification_observability import (
    NotificationAlertEvent,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_store import (
    InMemoryAlertDeliveryStore,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_adapters import (
    AlertDeliveryAdapterResult,
    MemoryAlertDeliveryAdapter,
    WebhookAlertDeliveryAdapter,
    ConsoleAlertDeliveryAdapter,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_service import (
    NotificationAlertDeliveryService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_alert(alert_id: str = "nae_1", severity: str = "warning",
                channel: str | None = None, federation_id: str | None = None) -> NotificationAlertEvent:
    return NotificationAlertEvent(
        alert_id=alert_id, rule_id="nar_1", name="Test Alert", severity=severity,
        metric="failure_rate", observed_value=0.1, threshold=0.05,
        message="Test alert message", status="open",
        channel=channel, federation_id=federation_id,
        created_at=datetime.now(timezone.utc),
    )


def _make_target(target_id: str = "ndt_1", **kwargs) -> AlertDeliveryTarget:
    defaults = dict(
        target_id=target_id, name="Ops Console",
        channel_type=AlertDeliveryChannelType.CONSOLE,
    )
    defaults.update(kwargs)
    return AlertDeliveryTarget(**defaults)


# ---------------------------------------------------------------------------
# Service: deliver_alert
# ---------------------------------------------------------------------------


class TestDeliverAlert:
    @pytest.mark.asyncio
    async def test_matching_target_receives_alert(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
        )
        await store.create_target(_make_target("ndt_1"))
        alert = _make_alert()
        attempts = await service.deliver_alert(alert)
        assert len(attempts) == 1
        assert attempts[0].status == AlertDeliveryStatus.DELIVERED
        assert attempts[0].alert_id == "nae_1"

    @pytest.mark.asyncio
    async def test_severity_filter_blocks(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
        )
        t = _make_target("ndt_1", severity_filter=["critical"])
        await store.create_target(t)
        alert = _make_alert(severity="warning")
        attempts = await service.deliver_alert(alert)
        assert len(attempts) == 0

    @pytest.mark.asyncio
    async def test_severity_filter_passes(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
        )
        t = _make_target("ndt_1", severity_filter=["critical", "warning"])
        await store.create_target(t)
        alert = _make_alert(severity="warning")
        attempts = await service.deliver_alert(alert)
        assert len(attempts) == 1

    @pytest.mark.asyncio
    async def test_channel_filter_blocks(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
        )
        t = _make_target("ndt_1", channel_filter=["webhook"])
        await store.create_target(t)
        alert = _make_alert(channel="console")
        attempts = await service.deliver_alert(alert)
        assert len(attempts) == 0

    @pytest.mark.asyncio
    async def test_federation_filter_blocks(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
        )
        t = _make_target("ndt_1", federation_filter=["fed_002"])
        await store.create_target(t)
        alert = _make_alert(federation_id="fed_001")
        attempts = await service.deliver_alert(alert)
        assert len(attempts) == 0

    @pytest.mark.asyncio
    async def test_disabled_target_ignored(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
        )
        t = _make_target("ndt_1", enabled=False)
        await store.create_target(t)
        alert = _make_alert()
        attempts = await service.deliver_alert(alert)
        assert len(attempts) == 0

    @pytest.mark.asyncio
    async def test_successful_delivery_records_delivered(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
        )
        await store.create_target(_make_target("ndt_1"))
        alert = _make_alert()
        attempts = await service.deliver_alert(alert)
        assert attempts[0].status == AlertDeliveryStatus.DELIVERED
        assert attempts[0].delivered_at is not None

    @pytest.mark.asyncio
    async def test_dry_run_records_suppressed(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
        )
        await store.create_target(_make_target("ndt_1"))
        alert = _make_alert()
        attempts = await service.deliver_alert(alert, dry_run=True)
        assert len(attempts) == 1
        assert attempts[0].status == AlertDeliveryStatus.SUPPRESSED
        assert attempts[0].delivered_at is None

    @pytest.mark.asyncio
    async def test_retryable_failure_schedules_retry(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter(fail_always=True)
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
            retry_policy=AlertDeliveryRetryPolicy(max_attempts=3, base_delay_seconds=60),
        )
        await store.create_target(_make_target("ndt_1"))
        alert = _make_alert()
        now = datetime.now(timezone.utc)
        attempts = await service.deliver_alert(alert, now=now)
        assert len(attempts) == 1
        assert attempts[0].status == AlertDeliveryStatus.RETRY_SCHEDULED
        assert attempts[0].next_retry_at is not None
        assert attempts[0].next_retry_at > now

    @pytest.mark.asyncio
    async def test_non_retryable_failure_goes_dlq(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter(fail_always=True, retryable=False)
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
            retry_policy=AlertDeliveryRetryPolicy(max_attempts=1),
        )
        await store.create_target(_make_target("ndt_1"))
        alert = _make_alert()
        attempts = await service.deliver_alert(alert)
        assert len(attempts) == 1
        assert attempts[0].status == AlertDeliveryStatus.DLQ

    @pytest.mark.asyncio
    async def test_max_attempts_goes_dlq(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter(fail_always=True)
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
            retry_policy=AlertDeliveryRetryPolicy(max_attempts=2),
        )
        await store.create_target(_make_target("ndt_1"))
        alert = _make_alert()
        # First attempt: fails -> RETRY_SCHEDULED
        await service.deliver_alert(alert)
        # Second attempt: also fails -> DLQ (exhausted max_attempts=2)
        now = datetime.now(timezone.utc) + timedelta(minutes=10)
        retried = await service.retry_failed(now=now)
        assert len(retried) == 1
        assert retried[0].status == AlertDeliveryStatus.DLQ

    @pytest.mark.asyncio
    async def test_retry_failed_processes_due_only(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
            retry_policy=AlertDeliveryRetryPolicy(max_attempts=3),
        )
        await store.create_target(_make_target("ndt_1"))
        alert = _make_alert()
        # Create a RETRY_SCHEDULED attempt that is NOT yet due
        future = datetime.now(timezone.utc) + timedelta(hours=2)
        a = AlertDeliveryAttempt(
            attempt_id="nda_1", alert_id="nae_1", target_id="ndt_1",
            channel_type=AlertDeliveryChannelType.CONSOLE,
            status=AlertDeliveryStatus.RETRY_SCHEDULED, attempt=1,
            next_retry_at=future, created_at=datetime.now(timezone.utc),
        )
        await store.record_attempt(a)
        now = datetime.now(timezone.utc)
        retried = await service.retry_failed(now=now)
        assert len(retried) == 0  # Not yet due

    @pytest.mark.asyncio
    async def test_retry_failed_processes_due(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
            retry_policy=AlertDeliveryRetryPolicy(max_attempts=3),
        )
        await store.create_target(_make_target("ndt_1"))
        alert = _make_alert()
        # Create a RETRY_SCHEDULED attempt that IS due
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        a = AlertDeliveryAttempt(
            attempt_id="nda_1", alert_id="nae_1", target_id="ndt_1",
            channel_type=AlertDeliveryChannelType.CONSOLE,
            status=AlertDeliveryStatus.RETRY_SCHEDULED, attempt=1,
            next_retry_at=past, created_at=past,
        )
        await store.record_attempt(a)
        now = datetime.now(timezone.utc)
        retried = await service.retry_failed(now=now)
        assert len(retried) == 1
        assert retried[0].status == AlertDeliveryStatus.DELIVERED


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


class TestMemoryAlertDeliveryAdapter:
    def test_captures_payload(self):
        adapter = MemoryAlertDeliveryAdapter()
        target = _make_target()
        alert = _make_alert()
        result = adapter.deliver(target, alert, {"key": "value"})
        assert result.success is True
        assert len(adapter.delivered) == 1
        assert adapter.delivered[0]["key"] == "value"

    def test_fail_next(self):
        adapter = MemoryAlertDeliveryAdapter(fail_next=True)
        target = _make_target()
        alert = _make_alert()
        result = adapter.deliver(target, alert, {})
        assert result.success is False
        assert result.error_code == "MEMORY_FAIL_NEXT"

    def test_fail_always(self):
        adapter = MemoryAlertDeliveryAdapter(fail_always=True)
        target = _make_target()
        alert = _make_alert()
        result = adapter.deliver(target, alert, {})
        assert result.success is False
        assert result.retryable is True

    def test_fail_next_resets(self):
        adapter = MemoryAlertDeliveryAdapter(fail_next=True)
        target = _make_target()
        alert = _make_alert()
        adapter.deliver(target, alert, {})
        result = adapter.deliver(target, alert, {})
        assert result.success is True

    def test_payload_redacted(self):
        adapter = MemoryAlertDeliveryAdapter()
        target = _make_target()
        alert = _make_alert()
        payload = {"authorization": "Bearer secret", "summary": "ok"}
        adapter.deliver(target, alert, payload)
        assert adapter.delivered[0]["authorization"] == "[REDACTED]"
        assert adapter.delivered[0]["summary"] == "ok"


class TestWebhookAlertDeliveryAdapter:
    def test_dry_run_succeeds(self):
        adapter = WebhookAlertDeliveryAdapter(dry_run=True)
        target = _make_target(channel_type=AlertDeliveryChannelType.WEBHOOK,
                              endpoint="https://example.invalid/alerts")
        alert = _make_alert()
        result = adapter.deliver(target, alert, {})
        assert result.success is True


class TestConsoleAlertDeliveryAdapter:
    def test_success(self):
        adapter = ConsoleAlertDeliveryAdapter()
        target = _make_target()
        alert = _make_alert()
        result = adapter.deliver(target, alert, {})
        assert result.success is True
