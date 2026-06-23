"""Phase 54 tests — alert delivery productionization: change events, dedup, rollup, webhook signing, DLQ replay, retention."""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from agent_app.governance.policy_change_event import (
    PolicyChangeEventType,
    PolicyChangeEvent,
)
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
    AlertDeliveryRetryRunResult,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_dedup import (
    NotificationAlertDedupService,
)
from agent_app.runtime.policy_rollout_federation_notification_webhook_signing import (
    sign_payload,
    make_signed_headers,
    redact_sensitive,
)
from agent_app.runtime.policy_rollout_federation_notification_rollup import (
    InMemoryNotificationRollupStore,
    NotificationMetricsRollup,
    NotificationRollupGranularity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_alert(alert_id="nae_phase54_1", severity="warning",
                channel=None, federation_id=None) -> NotificationAlertEvent:
    return NotificationAlertEvent(
        alert_id=alert_id, rule_id="nar_1", name="Phase54 Alert", severity=severity,
        metric="failure_rate", observed_value=0.1, threshold=0.05,
        message="Phase54 test alert", status="open",
        channel=channel, federation_id=federation_id,
        created_at=datetime.now(timezone.utc),
    )


def _make_target(target_id="ndt_phase54_1", **kwargs) -> AlertDeliveryTarget:
    defaults = dict(
        target_id=target_id, name="Phase54 Target",
        channel_type=AlertDeliveryChannelType.CONSOLE,
    )
    defaults.update(kwargs)
    return AlertDeliveryTarget(**defaults)


class _FakeChangeEventStore:
    """Fake change event store for testing — records calls to record()."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, event_type, payload):  # noqa: A003
        self.events.append({"event_type": event_type, "payload": payload})


# ---------------------------------------------------------------------------
# Phase 54: Change event wiring
# ---------------------------------------------------------------------------


class TestAlertDeliveryChangeEvents:
    @pytest.mark.asyncio
    async def test_run_once_records_retry_ran_event(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        change_store = _FakeChangeEventStore()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
            change_event_store=change_store,
        )
        await store.create_target(_make_target("ndt_1"))
        result = await service.run_once(dry_run=True)
        assert result.scanned == 0  # No pending retries
        # Should still record event even with 0 scanned
        assert len(change_store.events) == 1
        assert change_store.events[0]["event_type"] == PolicyChangeEventType.FEDERATION_NOTIFICATION_ALERT_DELIVERY_RETRY_RAN

    @pytest.mark.asyncio
    async def test_run_once_event_payload_contains_counts(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        change_store = _FakeChangeEventStore()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
            change_event_store=change_store,
        )
        await store.create_target(_make_target("ndt_1"))
        result = await service.run_once(dry_run=True)
        payload = change_store.events[0]["payload"]
        assert payload["dry_run"] is True
        assert "scanned" in payload
        assert "delivered" in payload

    @pytest.mark.asyncio
    async def test_replay_dlq_records_replay_event_dry_run(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        change_store = _FakeChangeEventStore()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
            change_event_store=change_store,
        )
        await store.create_target(_make_target("ndt_1"))
        # Create a DLQ attempt
        dlq_attempt = AlertDeliveryAttempt(
            attempt_id="nda_dlq_1", alert_id="nae_1", target_id="ndt_1",
            channel_type=AlertDeliveryChannelType.CONSOLE,
            status=AlertDeliveryStatus.DLQ, attempt=1,
            created_at=datetime.now(timezone.utc),
        )
        await store.record_attempt(dlq_attempt)
        result = await service.replay_dlq_attempt("nda_dlq_1", dry_run=True)
        assert result is not None
        assert result.status == AlertDeliveryStatus.SUPPRESSED
        assert len(change_store.events) == 1
        assert change_store.events[0]["event_type"] == PolicyChangeEventType.FEDERATION_NOTIFICATION_ALERT_DELIVERY_DLQ_REPLAYED
        assert change_store.events[0]["payload"]["dry_run"] is True
        assert change_store.events[0]["payload"]["success"] is True

    @pytest.mark.asyncio
    async def test_replay_dlq_records_replay_event_live(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        change_store = _FakeChangeEventStore()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
            change_event_store=change_store,
        )
        await store.create_target(_make_target("ndt_1"))
        dlq_attempt = AlertDeliveryAttempt(
            attempt_id="nda_dlq_2", alert_id="nae_2", target_id="ndt_1",
            channel_type=AlertDeliveryChannelType.CONSOLE,
            status=AlertDeliveryStatus.DLQ, attempt=1,
            created_at=datetime.now(timezone.utc),
        )
        await store.record_attempt(dlq_attempt)
        result = await service.replay_dlq_attempt("nda_dlq_2")
        assert result is not None
        assert result.status == AlertDeliveryStatus.DELIVERED
        assert len(change_store.events) == 1
        assert change_store.events[0]["payload"]["dry_run"] is False
        assert change_store.events[0]["payload"]["success"] is True

    @pytest.mark.asyncio
    async def test_no_change_event_store_no_crash(self):
        """Service works fine without change_event_store (backward compat)."""
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
        )
        await store.create_target(_make_target("ndt_1"))
        result = await service.run_once(dry_run=True)
        assert result.scanned == 0  # No crash

    @pytest.mark.asyncio
    async def test_replay_nonexistent_dlq_no_crash(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        change_store = _FakeChangeEventStore()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
            change_event_store=change_store,
        )
        result = await service.replay_dlq_attempt("nda_nonexistent")
        assert result is None
        assert len(change_store.events) == 0  # No event for non-DLQ


# ---------------------------------------------------------------------------
# Phase 54: Dedup service
# ---------------------------------------------------------------------------


class TestNotificationAlertDedupService:
    def test_first_alert_not_suppressed(self):
        svc = NotificationAlertDedupService(merge_window_seconds=300)
        decision = svc.should_suppress_or_merge("alert_1", "target_1")
        assert decision["suppressed"] is False
        assert decision["merged_with"] is None

    def test_duplicate_within_window_is_suppressed(self):
        svc = NotificationAlertDedupService(merge_window_seconds=300)
        now = datetime.now(timezone.utc)
        svc.should_suppress_or_merge("alert_1", "target_1", now=now)
        decision = svc.should_suppress_or_merge("alert_1", "target_1", now=now)
        assert decision["suppressed"] is True
        assert "merged_with" in decision

    def test_duplicate_outside_window_not_suppressed(self):
        svc = NotificationAlertDedupService(merge_window_seconds=60)
        now = datetime.now(timezone.utc)
        svc.should_suppress_or_merge("alert_1", "target_1", now=now)
        later = now + timedelta(minutes=5)
        decision = svc.should_suppress_or_merge("alert_1", "target_1", now=later)
        assert decision["suppressed"] is False

    def test_different_alert_not_suppressed(self):
        svc = NotificationAlertDedupService(merge_window_seconds=300)
        now = datetime.now(timezone.utc)
        svc.should_suppress_or_merge("alert_1", "target_1", now=now)
        decision = svc.should_suppress_or_merge("alert_2", "target_1", now=now)
        assert decision["suppressed"] is False

    def test_different_target_not_suppressed(self):
        svc = NotificationAlertDedupService(merge_window_seconds=300)
        now = datetime.now(timezone.utc)
        svc.should_suppress_or_merge("alert_1", "target_1", now=now)
        decision = svc.should_suppress_or_merge("alert_1", "target_2", now=now)
        assert decision["suppressed"] is False

    def test_custom_key_fields(self):
        svc = NotificationAlertDedupService(
            merge_window_seconds=300,
            key_fields=["alert_id", "target_id", "channel"],
        )
        now = datetime.now(timezone.utc)
        svc.should_suppress_or_merge("alert_1", "target_1", now=now)
        # Same alert_id + target_id but different channel (implicit via key_fields)
        # Since channel isn't passed as param, this tests key_fields affect dedup
        decision = svc.should_suppress_or_merge("alert_1", "target_1", now=now)
        assert decision["suppressed"] is True  # Same keys

    def test_prune_removes_expired(self):
        svc = NotificationAlertDedupService(merge_window_seconds=60)
        now = datetime.now(timezone.utc)
        svc.should_suppress_or_merge("alert_1", "target_1", now=now)
        assert len(svc._recent) == 1
        later = now + timedelta(minutes=5)
        svc.prune(now=later)
        assert len(svc._recent) == 0


# ---------------------------------------------------------------------------
# Phase 54: Webhook signing
# ---------------------------------------------------------------------------


class TestWebhookSigning:
    def test_sign_payload_format(self):
        sig = sign_payload(b'{"hello": "world"}', "secret123")
        assert sig.startswith("v1=")
        assert len(sig) > 3

    def test_sign_payload_deterministic_with_timestamp(self):
        ts = 1234567890
        sig1 = sign_payload(b'{"test": true}', "secret", timestamp=ts)
        sig2 = sign_payload(b'{"test": true}', "secret", timestamp=ts)
        assert sig1 == sig2

    def test_sign_payload_changes_with_different_secret(self):
        sig1 = sign_payload(b'{"test": true}', "secret1")
        sig2 = sign_payload(b'{"test": true}', "secret2")
        assert sig1 != sig2

    def test_make_signed_headers_contains_required_fields(self):
        headers = make_signed_headers(b'{"alert": "data"}', "mysecret")
        assert "Content-Type" in headers
        assert headers["Content-Type"] == "application/json"
        assert "X-Signature" in headers
        assert headers["X-Signature"].startswith("v1=")
        assert "X-Timestamp" in headers

    def test_make_signed_headers_with_base_headers(self):
        base = {"X-Custom": "value"}
        headers = make_signed_headers(b'{}', "secret", base_headers=base)
        assert headers["X-Custom"] == "value"
        assert "X-Signature" in headers

    def test_redact_sensitive_redacts_keys(self):
        data = {
            "authorization": "Bearer token123",
            "token": "abc",
            "secret": "shhh",
            "summary": "safe data",
            "api_key": "key123",
        }
        redacted = redact_sensitive(data)
        assert redacted["authorization"] == "[REDACTED]"
        assert redacted["token"] == "[REDACTED]"
        assert redacted["secret"] == "[REDACTED]"
        assert redacted["summary"] == "safe data"
        assert redacted["api_key"] == "[REDACTED]"

    def test_redact_sensitive_case_insensitive(self):
        data = {"Authorization": "Bearer token", "TOKEN": "abc"}
        redacted = redact_sensitive(data)
        assert redacted["Authorization"] == "[REDACTED]"
        assert redacted["TOKEN"] == "[REDACTED]"


# ---------------------------------------------------------------------------
# Phase 54: Webhook adapter signing integration
# ---------------------------------------------------------------------------


class TestWebhookAdapterSigning:
    def test_dry_run_no_signing(self):
        adapter = WebhookAlertDeliveryAdapter(dry_run=True)
        target = _make_target(
            channel_type=AlertDeliveryChannelType.WEBHOOK,
            endpoint="https://example.invalid/alerts",
            webhook_secret="mysecret",
        )
        result = adapter.deliver(target, None, {})
        assert result.success is True
        assert result.response_metadata["mode"] == "dry_run"

    def test_no_endpoint_fails(self):
        adapter = WebhookAlertDeliveryAdapter(dry_run=False)
        target = _make_target(
            channel_type=AlertDeliveryChannelType.WEBHOOK,
            endpoint=None,
        )
        result = adapter.deliver(target, None, {})
        assert result.success is False
        assert result.error_code == "NO_ENDPOINT"


# ---------------------------------------------------------------------------
# Phase 54: DLQ replay via service
# ---------------------------------------------------------------------------


class TestDLQReplay:
    @pytest.mark.asyncio
    async def test_replay_nonexistent_returns_none(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
        )
        result = await service.replay_dlq_attempt("nda_nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_replay_non_dlq_returns_none(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
        )
        # Create a DELIVERED attempt (not DLQ)
        delivered = AlertDeliveryAttempt(
            attempt_id="nda_delivered_1", alert_id="nae_1", target_id="ndt_1",
            channel_type=AlertDeliveryChannelType.CONSOLE,
            status=AlertDeliveryStatus.DELIVERED, attempt=1,
            created_at=datetime.now(timezone.utc),
        )
        await store.record_attempt(delivered)
        result = await service.replay_dlq_attempt("nda_delivered_1")
        assert result is None

    @pytest.mark.asyncio
    async def test_replay_dlq_creates_new_attempt(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
        )
        await store.create_target(_make_target("ndt_1"))
        dlq_attempt = AlertDeliveryAttempt(
            attempt_id="nda_dlq_replay_1", alert_id="nae_1", target_id="ndt_1",
            channel_type=AlertDeliveryChannelType.CONSOLE,
            status=AlertDeliveryStatus.DLQ, attempt=1,
            created_at=datetime.now(timezone.utc),
        )
        await store.record_attempt(dlq_attempt)
        result = await service.replay_dlq_attempt("nda_dlq_replay_1")
        assert result is not None
        assert result.attempt_id != "nda_dlq_replay_1"  # New attempt
        assert result.status == AlertDeliveryStatus.DELIVERED
        assert result.attempt == 2  # Incremented


# ---------------------------------------------------------------------------
# Phase 54: Run once retry scheduler
# ---------------------------------------------------------------------------


class TestRunOnce:
    @pytest.mark.asyncio
    async def test_no_due_attempts_scans_zero(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
        )
        result = await service.run_once(dry_run=True)
        assert result.scanned == 0
        assert result.dry_run is True

    @pytest.mark.asyncio
    async def test_dry_run_does_not_deliver(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
        )
        await store.create_target(_make_target("ndt_1"))
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        a = AlertDeliveryAttempt(
            attempt_id="nda_dry_1", alert_id="nae_1", target_id="ndt_1",
            channel_type=AlertDeliveryChannelType.CONSOLE,
            status=AlertDeliveryStatus.RETRY_SCHEDULED, attempt=1,
            next_retry_at=past, created_at=past,
        )
        await store.record_attempt(a)
        result = await service.run_once(dry_run=True)
        assert result.scanned == 1
        assert result.retry_scheduled == 1
        assert result.delivered == 0

    @pytest.mark.asyncio
    async def test_limit_respected(self):
        store = InMemoryAlertDeliveryStore()
        adapter = MemoryAlertDeliveryAdapter()
        service = NotificationAlertDeliveryService(
            store=store, adapters={"console": adapter},
            retry_policy=AlertDeliveryRetryPolicy(max_attempts=3),
        )
        await store.create_target(_make_target("ndt_1"))
        now = datetime.now(timezone.utc)
        for i in range(5):
            a = AlertDeliveryAttempt(
                attempt_id=f"nda_limit_{i}", alert_id=f"nae_limit_{i}", target_id="ndt_1",
                channel_type=AlertDeliveryChannelType.CONSOLE,
                status=AlertDeliveryStatus.RETRY_SCHEDULED, attempt=1,
                next_retry_at=now - timedelta(seconds=1), created_at=now,
            )
            await store.record_attempt(a)
        # With limit=3, list_attempts returns at most 3 items
        due = await store.list_attempts(status=AlertDeliveryStatus.RETRY_SCHEDULED, limit=3)
        assert len(due) == 3


# ---------------------------------------------------------------------------
# Phase 54: Rollup incremental + checkpoints
# ---------------------------------------------------------------------------


class TestInMemoryRollupIncremental:
    @pytest.mark.asyncio
    async def test_build_incremental_returns_newer_rollups(self):
        store = InMemoryNotificationRollupStore()
        now = datetime.now(timezone.utc)
        old_rollup = NotificationMetricsRollup(
            rollup_id="nru_old", granularity=NotificationRollupGranularity.HOURLY,
            window_start=now - timedelta(hours=3),
            window_end=now - timedelta(hours=2),
            total_alerts=5, delivered=5, failed=0, dlq=0,
            avg_latency_ms=100.0, channel="webhook",
        )
        new_rollup = NotificationMetricsRollup(
            rollup_id="nru_new", granularity=NotificationRollupGranularity.HOURLY,
            window_start=now - timedelta(hours=1),
            window_end=now,
            total_alerts=3, delivered=3, failed=0, dlq=0,
            avg_latency_ms=80.0, channel="webhook",
        )
        store._rollups["r_old"] = old_rollup
        store._rollups["r_new"] = new_rollup

        since = now - timedelta(hours=2)
        result = await store.build_incremental_rollup(since=since)
        assert len(result) == 1
        assert result[0].rollup_id == "nru_new"

    @pytest.mark.asyncio
    async def test_build_incremental_no_since_returns_all(self):
        store = InMemoryNotificationRollupStore()
        now = datetime.now(timezone.utc)
        r = NotificationMetricsRollup(
            rollup_id="nru_all", granularity=NotificationRollupGranularity.HOURLY,
            window_start=now - timedelta(hours=1),
            window_end=now,
            total_alerts=1, delivered=1, failed=0, dlq=0,
            avg_latency_ms=50.0, channel="console",
        )
        store._rollups["r_all"] = r
        result = await store.build_incremental_rollup()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_list_checkpoints_empty_by_default(self):
        store = InMemoryNotificationRollupStore()
        assert await store.list_checkpoints() == []

    @pytest.mark.asyncio
    async def test_record_checkpoint_noop_inmemory(self):
        store = InMemoryNotificationRollupStore()
        # Should not raise
        await store.record_checkpoint({"checkpoint_id": "cp_1", "window_end": "2024-01-01T00:00:00"})
        assert await store.list_checkpoints() == []


# ---------------------------------------------------------------------------
# Phase 54: RetryRunResult model
# ---------------------------------------------------------------------------


class TestAlertDeliveryRetryRunResult:
    def test_default_values(self):
        result = AlertDeliveryRetryRunResult(dry_run=False)
        assert result.dry_run is False
        assert result.scanned == 0
        assert result.retried == 0
        assert result.delivered == 0
        assert result.retry_scheduled == 0
        assert result.dlq == 0
        assert result.failed == 0
        assert result.attempt_ids == []

    def test_with_counts(self):
        result = AlertDeliveryRetryRunResult(
            dry_run=True, scanned=10, delivered=5, dlq=2,
        )
        assert result.scanned == 10
        assert result.delivered == 5
        assert result.dlq == 2
