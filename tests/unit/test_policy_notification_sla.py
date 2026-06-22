"""Tests for NotificationSlaService — SLA policy evaluation."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from agent_app.governance.policy_rollout_federation_notification_observability import (
    NotificationDeliveryEvent,
    NotificationDeliveryEventType,
    NotificationMetricWindow,
)
from agent_app.governance.policy_rollout_federation_notification_sla import (
    NotificationChannelSlaOverride,
    NotificationSlaPolicy,
    NotificationSlaViolation,
)
from agent_app.runtime.policy_rollout_federation_notification_sla_service import (
    NotificationSlaService,
)
from agent_app.runtime.policy_rollout_federation_notification_observability_store import (
    InMemoryNotificationObservabilityStore,
)


def _now(offset_seconds: int = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)


def _make_event(**overrides) -> NotificationDeliveryEvent:
    now = _now()
    defaults = dict(
        event_id=f"nde_{uuid.uuid4().hex[:12]}",
        notification_id="fn_001",
        approval_id="fap_001",
        federation_id="fed_a",
        channel="webhook",
        event_type=NotificationDeliveryEventType.SENT,
        status="delivered",
        attempt=1,
        latency_ms=150,
        error_code=None,
        error_message=None,
        adapter_name="webhook_adapter",
        template_id="fnt_001",
        preference_decision="send",
        metadata={},
        created_at=now,
    )
    defaults.update(overrides)
    return NotificationDeliveryEvent(**defaults)


def _make_metric_window(**overrides) -> NotificationMetricWindow:
    now = _now()
    defaults = dict(
        window_start=now - timedelta(minutes=60),
        window_end=now,
        federation_id="fed_a",
        channel="webhook",
        total=100,
        sent=95,
        failed=3,
        suppressed=1,
        dlq=1,
        retry_scheduled=0,
        success_rate=0.95,
        failure_rate=0.03,
        dlq_rate=0.01,
        avg_latency_ms=5000.0,
        p95_latency_ms=10000.0,
    )
    defaults.update(overrides)
    return NotificationMetricWindow(**defaults)


def _run_async(coro):
    """Run an async coroutine from synchronous test code."""
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store_with_events(events: list[NotificationDeliveryEvent]) -> InMemoryNotificationObservabilityStore:
    store = InMemoryNotificationObservabilityStore()
    for event in events:
        _run_async(store.record_event(event))
    return store


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNotificationSlaService:
    """Tests for NotificationSlaService.evaluate()."""

    # 1. No data returns empty violations
    async def test_no_data_returns_empty(self) -> None:
        store = InMemoryNotificationObservabilityStore()
        policy = NotificationSlaPolicy()
        service = NotificationSlaService(observability_store=store, sla_policy=policy)
        result = await service.evaluate()
        assert result == []

    # 2. Latency violation — warning
    async def test_latency_violation_warning(self) -> None:
        # avg_latency_ms=60000 > threshold=30000 but not > 60000 (2x)
        metric = _make_metric_window(avg_latency_ms=60000.0, total=10)
        store = InMemoryNotificationObservabilityStore()
        # Patch aggregate_metrics to return our metric
        store.aggregate_metrics = AsyncMock(return_value=metric)
        policy = NotificationSlaPolicy(max_delivery_latency_ms=30000)
        service = NotificationSlaService(observability_store=store, sla_policy=policy)
        result = await service.evaluate()
        assert len(result) == 1
        assert result[0].metric == "avg_latency_ms"
        assert result[0].severity == "warning"
        assert result[0].observed_value == 60000.0
        assert result[0].threshold == 30000.0

    # 3. Latency violation — critical (over 2x)
    async def test_latency_violation_critical(self) -> None:
        # avg_latency_ms=70000 > 2 * threshold=30000
        metric = _make_metric_window(avg_latency_ms=70000.0, total=10)
        store = InMemoryNotificationObservabilityStore()
        store.aggregate_metrics = AsyncMock(return_value=metric)
        policy = NotificationSlaPolicy(max_delivery_latency_ms=30000)
        service = NotificationSlaService(observability_store=store, sla_policy=policy)
        result = await service.evaluate()
        assert len(result) == 1
        assert result[0].metric == "avg_latency_ms"
        assert result[0].severity == "critical"

    # 4. Success rate violation
    async def test_success_rate_violation(self) -> None:
        # success_rate=0.90 < threshold=0.95 but not < 0.475 (50%)
        metric = _make_metric_window(success_rate=0.90, total=100)
        store = InMemoryNotificationObservabilityStore()
        store.aggregate_metrics = AsyncMock(return_value=metric)
        policy = NotificationSlaPolicy(min_success_rate=0.95)
        service = NotificationSlaService(observability_store=store, sla_policy=policy)
        result = await service.evaluate()
        assert len(result) == 1
        assert result[0].metric == "success_rate"
        assert result[0].severity == "warning"
        assert result[0].observed_value == 0.90
        assert result[0].threshold == 0.95

    # 5. Failure rate violation
    async def test_failure_rate_violation(self) -> None:
        # failure_rate=0.10 > threshold=0.05 but not > 0.10 (2x)
        metric = _make_metric_window(failure_rate=0.10, total=100)
        store = InMemoryNotificationObservabilityStore()
        store.aggregate_metrics = AsyncMock(return_value=metric)
        policy = NotificationSlaPolicy(max_failure_rate=0.05)
        service = NotificationSlaService(observability_store=store, sla_policy=policy)
        result = await service.evaluate()
        assert len(result) == 1
        assert result[0].metric == "failure_rate"
        assert result[0].severity == "warning"

    # 6. DLQ rate violation
    async def test_dlq_rate_violation(self) -> None:
        # dlq_rate=0.015 > threshold=0.01 but not > 0.02 (2x)
        metric = _make_metric_window(dlq_rate=0.015, total=100)
        store = InMemoryNotificationObservabilityStore()
        store.aggregate_metrics = AsyncMock(return_value=metric)
        policy = NotificationSlaPolicy(max_dlq_rate=0.01)
        service = NotificationSlaService(observability_store=store, sla_policy=policy)
        result = await service.evaluate()
        assert len(result) == 1
        assert result[0].metric == "dlq_rate"
        assert result[0].severity == "warning"

    # 7. Channel override applied
    async def test_channel_override_applied(self) -> None:
        # Default max_failure_rate=0.05, but webhook override sets it to 0.15
        # failure_rate=0.10 > 0.05 (default) would trigger, but with override 0.15 it should not
        metric = _make_metric_window(failure_rate=0.10, channel="webhook", total=100)
        store = InMemoryNotificationObservabilityStore()
        store.aggregate_metrics = AsyncMock(return_value=metric)
        policy = NotificationSlaPolicy(
            max_failure_rate=0.05,
            channels={
                "webhook": NotificationChannelSlaOverride(max_failure_rate=0.15),
            },
        )
        service = NotificationSlaService(observability_store=store, sla_policy=policy)
        result = await service.evaluate(channel="webhook")
        assert result == []  # override prevents violation

    # 8. Disabled policy returns no violations
    async def test_disabled_policy_returns_no_violations(self) -> None:
        metric = _make_metric_window(avg_latency_ms=70000.0, total=10)
        store = InMemoryNotificationObservabilityStore()
        store.aggregate_metrics = AsyncMock(return_value=metric)
        policy = NotificationSlaPolicy(enabled=False, max_delivery_latency_ms=30000)
        service = NotificationSlaService(observability_store=store, sla_policy=policy)
        result = await service.evaluate()
        assert result == []

    # 9. Federation filter applied
    async def test_federation_filter_applied(self) -> None:
        metric = _make_metric_window(
            avg_latency_ms=70000.0,
            federation_id="fed_a",
            total=10,
        )
        store = InMemoryNotificationObservabilityStore()
        store.aggregate_metrics = AsyncMock(return_value=metric)
        policy = NotificationSlaPolicy(max_delivery_latency_ms=30000)
        service = NotificationSlaService(observability_store=store, sla_policy=policy)
        result = await service.evaluate(federation_id="fed_a")
        assert len(result) == 1
        assert result[0].federation_id == "fed_a"

    # 10. Multiple violations for multiple breaches
    async def test_multiple_violations(self) -> None:
        # Both latency and failure rate breached
        metric = _make_metric_window(
            avg_latency_ms=70000.0,
            failure_rate=0.15,
            total=100,
        )
        store = InMemoryNotificationObservabilityStore()
        store.aggregate_metrics = AsyncMock(return_value=metric)
        policy = NotificationSlaPolicy(
            max_delivery_latency_ms=30000,
            max_failure_rate=0.05,
        )
        service = NotificationSlaService(observability_store=store, sla_policy=policy)
        result = await service.evaluate()
        assert len(result) == 2
        metrics_found = {v.metric for v in result}
        assert "avg_latency_ms" in metrics_found
        assert "failure_rate" in metrics_found

    # Additional: channel override with critical severity
    async def test_channel_override_critical(self) -> None:
        # Override sets max_delivery_latency_ms=50000, actual is 110000 (>2x)
        metric = _make_metric_window(avg_latency_ms=110000.0, channel="email", total=10)
        store = InMemoryNotificationObservabilityStore()
        store.aggregate_metrics = AsyncMock(return_value=metric)
        policy = NotificationSlaPolicy(
            max_delivery_latency_ms=30000,
            channels={
                "email": NotificationChannelSlaOverride(max_delivery_latency_ms=50000),
            },
        )
        service = NotificationSlaService(observability_store=store, sla_policy=policy)
        result = await service.evaluate(channel="email")
        assert len(result) == 1
        assert result[0].metric == "avg_latency_ms"
        assert result[0].severity == "critical"

    # Additional: success rate critical (below 50%)
    async def test_success_rate_critical(self) -> None:
        metric = _make_metric_window(success_rate=0.40, total=100)
        store = InMemoryNotificationObservabilityStore()
        store.aggregate_metrics = AsyncMock(return_value=metric)
        policy = NotificationSlaPolicy(min_success_rate=0.95)
        service = NotificationSlaService(observability_store=store, sla_policy=policy)
        result = await service.evaluate()
        assert len(result) == 1
        assert result[0].metric == "success_rate"
        assert result[0].severity == "critical"

    # Additional: DLQ rate critical (over 2x)
    async def test_dlq_rate_critical(self) -> None:
        metric = _make_metric_window(dlq_rate=0.05, total=100)
        store = InMemoryNotificationObservabilityStore()
        store.aggregate_metrics = AsyncMock(return_value=metric)
        policy = NotificationSlaPolicy(max_dlq_rate=0.01)
        service = NotificationSlaService(observability_store=store, sla_policy=policy)
        result = await service.evaluate()
        assert len(result) == 1
        assert result[0].metric == "dlq_rate"
        assert result[0].severity == "critical"

    # Additional: no policy defaults to enabled policy with defaults
    async def test_no_policy_uses_defaults(self) -> None:
        metric = _make_metric_window(avg_latency_ms=70000.0, total=10)
        store = InMemoryNotificationObservabilityStore()
        store.aggregate_metrics = AsyncMock(return_value=metric)
        service = NotificationSlaService(observability_store=store, sla_policy=None)
        result = await service.evaluate()
        assert len(result) == 1
        assert result[0].metric == "avg_latency_ms"
        assert result[0].severity == "critical"
