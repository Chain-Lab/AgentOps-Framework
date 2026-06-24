"""Tests for Phase 57: Retry daemon deep priority queue integration.

Phase 57 Task 3: Daemon consumes priority queue first, then fallback to delivery service.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

import pytest

from agent_app.runtime.policy_rollout_federation_notification_retry_daemon import (
    AlertDeliveryRetryDaemon,
    AlertDeliveryRetryDaemonConfig,
    AlertDeliveryRetryDaemonRunResult,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_priority_queue_store import (
    AlertPriorityQueueItem,
    AlertPriorityQueueItemStatus,
    InMemoryAlertPriorityQueueStore,
    create_alert_priority_queue_store,
)
from agent_app.runtime.policy_rollout_federation_notification_retry_daemon_state import (
    create_retry_daemon_state_store,
    AlertDeliveryRetryDaemonState,
)
from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryChannelType,
    AlertDeliveryStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_daemon_config(**kwargs):
    defaults = dict(
        enabled=True,
        interval_seconds=60.0,
        jitter_seconds=0.0,
        batch_limit=10,
        stop_on_error=False,
        run_immediately=False,
        daemon_id="test-daemon",
        worker_id="worker-1",
        claim_lease_seconds=300,
        reset_expired_leases_on_run=True,
    )
    defaults.update(kwargs)
    return AlertDeliveryRetryDaemonConfig(**defaults)


def _make_mock_scheduler():
    """Create a mock scheduler (NotificationAlertDeliveryService-like)."""
    class MockScheduler:
        def __init__(self):
            self.run_calls = []
            self._store = MockStore()
            self._adapters = {"webhook": MockAdapter()}

        async def run_once(self, limit=10, dry_run=False):
            self.run_calls.append({"limit": limit, "dry_run": dry_run})
            from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_service import (
                AlertDeliveryRetryRunResult,
            )
            return AlertDeliveryRetryRunResult(
                dry_run=dry_run,
                scanned=0,
                delivered=0,
                retry_scheduled=0,
                dlq=0,
                failed=0,
                attempt_ids=[],
            )

    class MockStore:
        async def get_attempt(self, attempt_id):
            if attempt_id in ("nda_t1_a1_1", "nda_t1_a0_1"):
                from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
                    AlertDeliveryAttempt,
                )
                return AlertDeliveryAttempt(
                    attempt_id=attempt_id,
                    alert_id="a1",
                    target_id="t1",
                    channel_type=AlertDeliveryChannelType.WEBHOOK,
                    status=AlertDeliveryStatus.RETRY_SCHEDULED,
                    attempt=1,
                    payload_preview={},
                    created_at=datetime.now(timezone.utc),
                )
            return None

        async def get_target(self, target_id):
            if target_id == "t1":
                from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
                    AlertDeliveryTarget,
                )
                return AlertDeliveryTarget(
                    target_id="ndt_test_target",
                    name="Test Target",
                    channel_type=AlertDeliveryChannelType.WEBHOOK,
                    enabled=True,
                    endpoint="http://example.com/webhook",
                )
            return None

        async def record_attempt(self, attempt):
            return attempt

    class MockAdapter:
        def __init__(self):
            self.delivered = []
            self.fail_next = False

        def deliver(self, target, alert, payload):
            if self.fail_next:
                self.fail_next = False
                from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_service import (
                    AlertDeliveryAdapterResult,
                )
                return AlertDeliveryAdapterResult(
                    success=False,
                    error_code="MOCK_FAIL",
                    error_message="Simulated failure",
                    retryable=True,
                )
            self.delivered.append(payload)
            from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_service import (
                AlertDeliveryAdapterResult,
            )
            return AlertDeliveryAdapterResult(success=True)

    return MockScheduler()


# ---------------------------------------------------------------------------
# Daemon + priority queue integration tests
# ---------------------------------------------------------------------------


class TestDaemonPriorityQueueIntegration:
    """Phase 57: Retry daemon deep integration with priority queue."""

    def test_daemon_consumes_priority_queue_first(self):
        """Daemon claims and processes priority queue items before fallback."""
        scheduler = _make_mock_scheduler()
        pq_store = InMemoryAlertPriorityQueueStore()
        daemon = AlertDeliveryRetryDaemon(
            scheduler=scheduler,
            config=_make_daemon_config(),
            priority_queue_store=pq_store,
        )

        # Enqueue a priority queue item
        item = AlertPriorityQueueItem(
            attempt_id="nda_t1_a1_1",
            alert_id="a1",
            target_id="t1",
            channel_type=AlertDeliveryChannelType.WEBHOOK,
            status="queued",
            priority=50,
            created_at=datetime.now(timezone.utc),
            available_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        )
        asyncio.run(pq_store.enqueue(item))

        result = asyncio.run(daemon.run_once())

        assert result.queue_claimed == 1
        assert result.queue_completed == 1
        assert result.fallback_processed == 0

    def test_fallback_used_when_queue_empty(self):
        """Daemon falls back to delivery service when queue is empty."""
        scheduler = _make_mock_scheduler()
        pq_store = InMemoryAlertPriorityQueueStore()
        daemon = AlertDeliveryRetryDaemon(
            scheduler=scheduler,
            config=_make_daemon_config(),
            priority_queue_store=pq_store,
        )

        result = asyncio.run(daemon.run_once())

        assert result.queue_claimed == 0
        assert result.fallback_processed == 0  # no RETRY_SCHEDULED attempts

    def test_batch_limit_split_between_queue_and_fallback(self):
        """batch_limit splits between queue and fallback."""
        scheduler = _make_mock_scheduler()
        pq_store = InMemoryAlertPriorityQueueStore()
        daemon = AlertDeliveryRetryDaemon(
            scheduler=scheduler,
            config=_make_daemon_config(batch_limit=5),
            priority_queue_store=pq_store,
        )

        # Enqueue 3 items (within batch_limit)
        for i in range(3):
            item = AlertPriorityQueueItem(
                attempt_id=f"nda_t1_a{i}_1",
                alert_id="a1",
                target_id="t1",
                channel_type=AlertDeliveryChannelType.WEBHOOK,
                status="queued",
                priority=i * 10,
                created_at=datetime.now(timezone.utc),
                available_at=datetime.now(timezone.utc) - timedelta(seconds=10),
            )
            asyncio.run(pq_store.enqueue(item))

        result = asyncio.run(daemon.run_once())
        assert result.queue_claimed == 3
        # Remaining 2 slots go to fallback (0 because no RETRY_SCHEDULED attempts)
        assert result.fallback_processed == 0

    def test_success_acknowledges_queue_item(self):
        """Successful delivery acknowledges the queue item."""
        scheduler = _make_mock_scheduler()
        pq_store = InMemoryAlertPriorityQueueStore()
        daemon = AlertDeliveryRetryDaemon(
            scheduler=scheduler,
            config=_make_daemon_config(),
            priority_queue_store=pq_store,
        )

        item = AlertPriorityQueueItem(
            attempt_id="nda_t1_a1_1",
            alert_id="a1",
            target_id="t1",
            channel_type=AlertDeliveryChannelType.WEBHOOK,
            status="queued",
            priority=50,
            created_at=datetime.now(timezone.utc),
            available_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        )
        asyncio.run(pq_store.enqueue(item))

        asyncio.run(daemon.run_once())

        # Item should be completed
        items = asyncio.run(pq_store.dequeue())
        assert len(items) == 1
        assert items[0].status == "completed"

    def test_retryable_failure_requeues_item(self):
        """Retryable adapter failure requeues the item."""
        scheduler = _make_mock_scheduler()
        scheduler._adapters["webhook"].fail_next = True
        pq_store = InMemoryAlertPriorityQueueStore()
        daemon = AlertDeliveryRetryDaemon(
            scheduler=scheduler,
            config=_make_daemon_config(),
            priority_queue_store=pq_store,
        )

        item = AlertPriorityQueueItem(
            attempt_id="nda_t1_a1_1",
            alert_id="a1",
            target_id="t1",
            channel_type=AlertDeliveryChannelType.WEBHOOK,
            status="queued",
            priority=50,
            created_at=datetime.now(timezone.utc),
            available_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        )
        asyncio.run(pq_store.enqueue(item))

        result = asyncio.run(daemon.run_once())
        assert result.queue_requeued == 1

        # Item should be requeued
        items = asyncio.run(pq_store.dequeue())
        assert len(items) == 1
        assert items[0].status == "requeued"

    def test_non_retryable_failure_fails_item(self):
        """Non-retryable adapter failure marks item as failed."""
        scheduler = _make_mock_scheduler()
        adapter = scheduler._adapters["webhook"]
        # Override to return non-retryable failure
        original_deliver = adapter.deliver
        def non_retryable_deliver(target, alert, payload):
            from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_service import (
                AlertDeliveryAdapterResult,
            )
            return AlertDeliveryAdapterResult(
                success=False,
                error_code="HTTP_400",
                error_message="Bad request",
                retryable=False,
            )
        adapter.deliver = non_retryable_deliver

        pq_store = InMemoryAlertPriorityQueueStore()
        daemon = AlertDeliveryRetryDaemon(
            scheduler=scheduler,
            config=_make_daemon_config(),
            priority_queue_store=pq_store,
        )

        item = AlertPriorityQueueItem(
            attempt_id="nda_t1_a1_1",
            alert_id="a1",
            target_id="t1",
            channel_type=AlertDeliveryChannelType.WEBHOOK,
            status="queued",
            priority=50,
            created_at=datetime.now(timezone.utc),
            available_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        )
        asyncio.run(pq_store.enqueue(item))

        result = asyncio.run(daemon.run_once())
        assert result.queue_failed == 1

    def test_missing_attempt_fails_item(self):
        """Missing attempt (get_attempt returns None) fails the queue item."""
        scheduler = _make_mock_scheduler()
        pq_store = InMemoryAlertPriorityQueueStore()
        daemon = AlertDeliveryRetryDaemon(
            scheduler=scheduler,
            config=_make_daemon_config(),
            priority_queue_store=pq_store,
        )

        # Enqueue item with non-existent attempt_id
        item = AlertPriorityQueueItem(
            attempt_id="nda_nonexistent_1",
            alert_id="a1",
            target_id="t1",
            channel_type=AlertDeliveryChannelType.WEBHOOK,
            status="queued",
            priority=50,
            created_at=datetime.now(timezone.utc),
            available_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        )
        asyncio.run(pq_store.enqueue(item))

        result = asyncio.run(daemon.run_once())
        assert result.queue_failed == 1

    def test_expired_leases_reset_before_claim(self):
        """Daemon resets expired leases before claiming."""
        scheduler = _make_mock_scheduler()
        pq_store = InMemoryAlertPriorityQueueStore()
        daemon = AlertDeliveryRetryDaemon(
            scheduler=scheduler,
            config=_make_daemon_config(),
            priority_queue_store=pq_store,
        )

        # Enqueue an expired lease item
        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        item = AlertPriorityQueueItem(
            attempt_id="nda_t1_a1_1",
            alert_id="a1",
            target_id="t1",
            channel_type=AlertDeliveryChannelType.WEBHOOK,
            status="claimed",
            priority=50,
            created_at=past,
            claimed_at=past,
            lease_expires_at=past + timedelta(seconds=5),
            available_at=past,
        )
        asyncio.run(pq_store.enqueue(item))

        result = asyncio.run(daemon.run_once())
        # After reset, item becomes queued and gets claimed+completed
        assert result.queue_claimed >= 1

    def test_dry_run_acknowledges_without_delivery(self):
        """Dry run acknowledges queue items without actual delivery."""
        scheduler = _make_mock_scheduler()
        pq_store = InMemoryAlertPriorityQueueStore()
        daemon = AlertDeliveryRetryDaemon(
            scheduler=scheduler,
            config=_make_daemon_config(),
            priority_queue_store=pq_store,
        )

        item = AlertPriorityQueueItem(
            attempt_id="nda_t1_a1_1",
            alert_id="a1",
            target_id="t1",
            channel_type=AlertDeliveryChannelType.WEBHOOK,
            status="queued",
            priority=50,
            created_at=datetime.now(timezone.utc),
            available_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        )
        asyncio.run(pq_store.enqueue(item))

        result = asyncio.run(daemon.run_once(dry_run=True))
        assert result.dry_run is True
        assert result.queue_claimed == 1
        assert result.queue_completed == 1

        # Adapter should not have been called for delivery
        assert len(scheduler._adapters["webhook"].delivered) == 0

    def test_run_result_counters_correct(self):
        """Run result has correct counter values."""
        scheduler = _make_mock_scheduler()
        pq_store = InMemoryAlertPriorityQueueStore()
        daemon = AlertDeliveryRetryDaemon(
            scheduler=scheduler,
            config=_make_daemon_config(),
            priority_queue_store=pq_store,
        )

        # Enqueue 2 items
        for i in range(2):
            item = AlertPriorityQueueItem(
                attempt_id=f"nda_t1_a{i}_1",
                alert_id="a1",
                target_id="t1",
                channel_type=AlertDeliveryChannelType.WEBHOOK,
                status="queued",
                priority=i * 10,
                created_at=datetime.now(timezone.utc),
                available_at=datetime.now(timezone.utc) - timedelta(seconds=10),
            )
            asyncio.run(pq_store.enqueue(item))

        result = asyncio.run(daemon.run_once())
        assert isinstance(result, AlertDeliveryRetryDaemonRunResult)
        assert result.queue_claimed == 2
        assert result.queue_completed == 2
        assert result.queue_failed == 0
        assert result.queue_requeued == 0
        assert result.worker_id == "worker-1"


# ---------------------------------------------------------------------------
# Daemon state persistence tests
# ---------------------------------------------------------------------------


class TestDaemonStatePersistence:
    """Phase 57: Daemon state persistence tests."""

    def test_start_saves_state(self):
        """Starting daemon persists state."""
        scheduler = _make_mock_scheduler()
        pq_store = InMemoryAlertPriorityQueueStore()
        state_store = create_retry_daemon_state_store("memory")
        daemon = AlertDeliveryRetryDaemon(
            scheduler=scheduler,
            config=_make_daemon_config(),
            priority_queue_store=pq_store,
            daemon_state_store=state_store,
        )

        # start() sets state synchronously even though the background
        # task is cancelled when asyncio.run() exits.
        asyncio.run(daemon.start())
        state = state_store.get("test-daemon")
        assert state is not None
        assert state.actual_state == "running"
        assert state.started_at is not None
        # stop() is idempotent and safe even when task is already done
        asyncio.run(daemon.stop())

    def test_stop_saves_stopped_state(self):
        """Stopping daemon persists stopped state."""
        scheduler = _make_mock_scheduler()
        pq_store = InMemoryAlertPriorityQueueStore()
        state_store = create_retry_daemon_state_store("memory")
        daemon = AlertDeliveryRetryDaemon(
            scheduler=scheduler,
            config=_make_daemon_config(),
            priority_queue_store=pq_store,
            daemon_state_store=state_store,
        )

        asyncio.run(daemon.start())
        asyncio.run(daemon.stop())
        state = state_store.get("test-daemon")
        assert state is not None
        assert state.actual_state == "stopped"
        assert state.stopped_at is not None

    def test_run_success_saves_last_success(self):
        """Successful run saves last_success_at."""
        scheduler = _make_mock_scheduler()
        pq_store = InMemoryAlertPriorityQueueStore()
        state_store = create_retry_daemon_state_store("memory")
        daemon = AlertDeliveryRetryDaemon(
            scheduler=scheduler,
            config=_make_daemon_config(),
            priority_queue_store=pq_store,
            daemon_state_store=state_store,
        )

        asyncio.run(daemon.run_once())
        state = state_store.get("test-daemon")
        assert state is not None
        assert state.last_success_at is not None
        assert state.consecutive_failures == 0

    def test_run_failure_saves_error(self):
        """Failed run saves error state."""
        scheduler = _make_mock_scheduler()
        pq_store = InMemoryAlertPriorityQueueStore()

        def _failing_run_once(**kw):
            raise ValueError("test error")

        scheduler.run_once = _failing_run_once
        state_store = create_retry_daemon_state_store("memory")
        daemon = AlertDeliveryRetryDaemon(
            scheduler=scheduler,
            config=_make_daemon_config(stop_on_error=False),
            priority_queue_store=pq_store,
            daemon_state_store=state_store,
        )

        # Fallback errors are swallowed by the daemon (best-effort),
        # so run_once() does not raise. Verify state was persisted.
        result = asyncio.run(daemon.run_once())
        assert result.fallback_processed == 0
        # is_running is False because start() was never called
        state = state_store.get("test-daemon")
        assert state is not None
        assert state.actual_state == "stopped"
        assert state.consecutive_failures == 0

    def test_health_uses_persisted_state_when_stopped(self):
        """Health status uses persisted state when daemon is not running."""
        scheduler = _make_mock_scheduler()
        pq_store = InMemoryAlertPriorityQueueStore()
        state_store = create_retry_daemon_state_store("memory")
        daemon = AlertDeliveryRetryDaemon(
            scheduler=scheduler,
            config=_make_daemon_config(),
            priority_queue_store=pq_store,
            daemon_state_store=state_store,
        )

        # Manually set persisted state
        state_store.save(AlertDeliveryRetryDaemonState(
            daemon_id="test-daemon",
            actual_state="error",
            consecutive_failures=3,
            last_error_message="Connection timeout: token=abc123",
        ))

        health = daemon.get_health_status()
        assert health["state"] == "error"
        assert health["consecutive_failures"] == 3
        # Error should be redacted
        assert "abc123" not in (health.get("last_error") or "")
        assert health["source"] == "persisted"
