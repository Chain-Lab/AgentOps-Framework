"""Tests for Phase 60 — Daemon closed-loop integration with Phase 59 stores."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_app.runtime.policy_rollout_federation_notification_retry_daemon import (
    AlertDeliveryRetryDaemon,
    AlertDeliveryRetryDaemonConfig,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_service import (
    AlertDeliveryRetryRunResult,
    NotificationAlertDeliveryService,
)
from agent_app.runtime.policy_rollout_federation_notification_replay_idempotency import (
    ReplayIdempotencyStore,
    ReplayIdempotencyRecord,
    InMemoryReplayIdempotencyStore,
)
from agent_app.runtime.policy_rollout_federation_notification_replay_rate_limiter import (
    ReplayRateLimiterStore,
    ReplayRateLimiterResult,
    InMemoryReplayRateLimiterStore,
)
from agent_app.runtime.policy_rollout_federation_notification_dead_letter_policy import (
    DeadLetterPolicyStore,
    DeadLetterPolicyResult,
    DeadLetterRecord,
    DeadLetterPolicyConfig,
    InMemoryDeadLetterPolicyStore,
)
from agent_app.runtime.policy_rollout_federation_notification_distributed_lock import (
    DistributedLockStore,
    DistributedLockStatus,
    InMemoryDistributedLockStore,
)
from agent_app.runtime.policy_rollout_federation_notification_metrics_enhanced import (
    EnhancedMetrics,
)
from agent_app.runtime.policy_rollout_federation_notification_webhook_key_rotation import (
    WebhookKeyRotationService,
    WebhookKeyRotationConfig,
    WebhookKeyRotationStore,
    InMemoryWebhookKeyRotationStore,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeChannelType:
    value = "console"


class _FakeTarget:
    channel_type = _FakeChannelType()


def _make_fake_adapter(success: bool = True, retryable: bool = True):
    """Create a fake adapter for testing."""
    adapter = MagicMock()
    adapter.deliver.return_value = MagicMock(
        success=success,
        retryable=retryable,
        error_message="test error" if not success else None,
    )
    return adapter


def _make_fake_scheduler():
    """Create a fake scheduler."""
    scheduler = MagicMock()
    store = MagicMock()
    store.get_attempt = AsyncMock(return_value=MagicMock())
    store.get_target = AsyncMock(return_value=_FakeTarget())
    scheduler._store = store
    scheduler._adapters = {"console": _make_fake_adapter()}
    return scheduler


def _make_daemon(scheduler=None, **kwargs):
    """Create a daemon with Phase 60 stores."""
    if scheduler is None:
        scheduler = _make_fake_scheduler()

    # Extract Phase 60 config fields from kwargs so they go into the config
    # object rather than being passed as unexpected kwargs to the daemon
    # constructor.
    _PHASE60_CONFIG_FIELDS = {
        "distributed_lock_enabled",
        "lock_name",
        "lock_lease_seconds",
        "lock_renew_interval_seconds",
        "key_rotation_enabled",
        "rate_limit_enabled",
        "rate_limit_window_seconds",
        "rate_limit_max_attempts",
        "rate_limit_scope",
        "idempotency_enabled",
        "idempotency_ttl_hours",
        "dead_letter_enabled",
        "dead_letter_max_retries",
    }
    config_kwargs: dict[str, Any] = {}
    remaining_kwargs: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key in _PHASE60_CONFIG_FIELDS:
            config_kwargs[key] = value
        else:
            remaining_kwargs[key] = value

    cfg = AlertDeliveryRetryDaemonConfig(
        worker_id="worker-1",
        **config_kwargs,
    )
    return AlertDeliveryRetryDaemon(
        scheduler=scheduler,
        config=cfg,
        **remaining_kwargs,
    )


# ---------------------------------------------------------------------------
# Phase 60 Task 1: Closed-loop success path
# ---------------------------------------------------------------------------


class TestClosedLoopSuccess:
    """Tests for successful closed-loop processing."""

    @pytest.mark.asyncio
    async def test_success_with_all_stores(self):
        """Full closed-loop: claim → rate limit → idempotency → success → ack."""
        pq_store = MagicMock()
        pq_store.claim_next = AsyncMock(return_value=[
            MagicMock(attempt_id="att_001", target_id="tgt_001", alert_id="alert_001"),
        ])
        pq_store.acknowledge = AsyncMock()
        pq_store.reset_expired_leases = AsyncMock()

        idem_store = InMemoryReplayIdempotencyStore()
        rl_store = InMemoryReplayRateLimiterStore()
        dl_store = InMemoryDeadLetterPolicyStore(DeadLetterPolicyConfig(max_retries=5))
        lock_store = InMemoryDistributedLockStore()
        metrics = EnhancedMetrics()

        scheduler = _make_fake_scheduler()

        daemon = _make_daemon(
            scheduler=scheduler,
            priority_queue_store=pq_store,
            idempotency_store=idem_store,
            rate_limiter_store=rl_store,
            dead_letter_policy_store=dl_store,
            distributed_lock_store=lock_store,
            enhanced_metrics=metrics,
            rate_limit_enabled=True,
            idempotency_enabled=True,
            dead_letter_enabled=True,
            distributed_lock_enabled=True,
        )

        result = await daemon.run_once(dry_run=False)

        assert result.queue_claimed == 1
        assert result.queue_completed == 1
        assert metrics.snapshot().replay.successes == 1
        assert metrics.snapshot().replay.attempts == 1

    @pytest.mark.asyncio
    async def test_idempotency_hit_skips_replay(self):
        """When idempotency key already completed, skip replay."""
        pq_store = MagicMock()
        pq_store.claim_next = AsyncMock(return_value=[
            MagicMock(attempt_id="att_001", target_id="tgt_001", alert_id="alert_001"),
        ])
        pq_store.acknowledge = AsyncMock()
        pq_store.reset_expired_leases = AsyncMock()

        idem_store = InMemoryReplayIdempotencyStore()
        # Pre-populate completed idempotency record
        idem_store.begin(ReplayIdempotencyRecord(
            idempotency_key="replay:att_001:tgt_001:alert_001",
            original_attempt_id="att_001",
            replay_type="single",
            status="completed",
            new_attempt_id="att_completed",
        ))
        metrics = EnhancedMetrics()

        scheduler = _make_fake_scheduler()

        daemon = _make_daemon(
            scheduler=scheduler,
            priority_queue_store=pq_store,
            idempotency_store=idem_store,
            enhanced_metrics=metrics,
            idempotency_enabled=True,
        )

        result = await daemon.run_once(dry_run=False)

        assert result.queue_completed == 1
        assert metrics.snapshot().replay.idempotency_hits == 1
        assert metrics.snapshot().replay.successes == 0


# ---------------------------------------------------------------------------
# Phase 60 Task 2: Rate limited path
# ---------------------------------------------------------------------------


class TestRateLimitedPath:
    """Tests for rate-limited replay path."""

    @pytest.mark.asyncio
    async def test_rate_limited_item_requeued(self):
        """When rate limited, item is requeued and replay skipped."""
        pq_store = MagicMock()
        pq_store.claim_next = AsyncMock(return_value=[
            MagicMock(attempt_id="att_001", target_id="tgt_001", alert_id="alert_001"),
        ])
        pq_store.requeue = AsyncMock()
        pq_store.reset_expired_leases = AsyncMock()

        rl_store = InMemoryReplayRateLimiterStore()
        for _ in range(10):
            rl_store.check_and_record("tgt_001", window_seconds=60, max_attempts=10)

        metrics = EnhancedMetrics()
        scheduler = _make_fake_scheduler()

        daemon = _make_daemon(
            scheduler=scheduler,
            priority_queue_store=pq_store,
            rate_limiter_store=rl_store,
            enhanced_metrics=metrics,
            rate_limit_enabled=True,
            rate_limit_window_seconds=60,
            rate_limit_max_attempts=10,
        )

        result = await daemon.run_once(dry_run=False)

        assert result.queue_requeued == 1
        assert metrics.snapshot().rate_limiter.denied == 1
        assert metrics.snapshot().replay.rate_limited == 1


# ---------------------------------------------------------------------------
# Phase 60 Task 3: Dead letter path
# ---------------------------------------------------------------------------


class TestDeadLetterPath:
    """Tests for dead letter evaluation path."""

    @pytest.mark.asyncio
    async def test_dead_letter_created(self):
        """When adapter returns non-retryable error, dead letter is created."""
        pq_store = MagicMock()
        pq_store.claim_next = AsyncMock(return_value=[
            MagicMock(attempt_id="att_001", target_id="tgt_001", alert_id="alert_001", attempt=6),
        ])
        pq_store.fail = AsyncMock()
        pq_store.reset_expired_leases = AsyncMock()

        dl_store = InMemoryDeadLetterPolicyStore(DeadLetterPolicyConfig(max_retries=5))
        metrics = EnhancedMetrics()

        scheduler = _make_fake_scheduler()
        scheduler._adapters = {"console": _make_fake_adapter(success=False, retryable=False)}

        daemon = _make_daemon(
            scheduler=scheduler,
            priority_queue_store=pq_store,
            dead_letter_policy_store=dl_store,
            enhanced_metrics=metrics,
            dead_letter_enabled=True,
        )

        result = await daemon.run_once(dry_run=False)

        assert result.queue_failed == 1
        assert metrics.snapshot().dead_letter.dead_lettered >= 1


# ---------------------------------------------------------------------------
# Phase 60 Task 4: Distributed lock leader election
# ---------------------------------------------------------------------------


class TestDistributedLockLeaderElection:
    """Tests for distributed lock leader election."""

    @pytest.mark.asyncio
    async def test_lock_acquired_before_processing(self):
        """When lock is acquired, items are processed."""
        lock_store = InMemoryDistributedLockStore()
        pq_store = MagicMock()
        pq_store.claim_next = AsyncMock(return_value=[])
        pq_store.reset_expired_leases = AsyncMock()
        metrics = EnhancedMetrics()

        daemon = _make_daemon(
            priority_queue_store=pq_store,
            distributed_lock_store=lock_store,
            enhanced_metrics=metrics,
            distributed_lock_enabled=True,
            lock_name="test-lock",
            lock_lease_seconds=30,
        )

        result = await daemon.run_once(dry_run=False)

        assert daemon._lock_owner_id is None
        assert metrics.snapshot().distributed_lock.acquire_successes == 1
        assert metrics.snapshot().distributed_lock.release_successes == 1

    @pytest.mark.asyncio
    async def test_lock_denied_skips_processing(self):
        """When lock is denied by another instance, items are not processed."""
        lock_store = InMemoryDistributedLockStore()
        lock_store.acquire("test-lock", "worker-1", lease_seconds=30)

        pq_store = MagicMock()
        pq_store.claim_next = AsyncMock(return_value=[])
        pq_store.reset_expired_leases = AsyncMock()
        metrics = EnhancedMetrics()

        daemon = _make_daemon(
            priority_queue_store=pq_store,
            distributed_lock_store=lock_store,
            enhanced_metrics=metrics,
            distributed_lock_enabled=True,
            lock_name="test-lock",
            lock_lease_seconds=30,
        )

        result = await daemon.run_once(dry_run=False)

        assert pq_store.claim_next.call_count == 0
        assert metrics.snapshot().distributed_lock.acquire_denied == 1

    @pytest.mark.asyncio
    async def test_lock_released_after_run(self):
        """Lock is released after run_once completes."""
        lock_store = InMemoryDistributedLockStore()
        pq_store = MagicMock()
        pq_store.claim_next = AsyncMock(return_value=[])
        pq_store.reset_expired_leases = AsyncMock()
        metrics = EnhancedMetrics()

        daemon = _make_daemon(
            priority_queue_store=pq_store,
            distributed_lock_store=lock_store,
            enhanced_metrics=metrics,
            distributed_lock_enabled=True,
            lock_name="test-lock",
            lock_lease_seconds=30,
        )

        await daemon.run_once(dry_run=False)

        status = lock_store.get_status("test-lock")
        assert status.acquired is False


# ---------------------------------------------------------------------------
# Phase 60 Task 5: Key rotation scheduling
# ---------------------------------------------------------------------------


class TestKeyRotationScheduling:
    """Tests for automatic key rotation in daemon loop."""

    @pytest.mark.asyncio
    async def test_no_rotation_when_not_due(self):
        """Key rotation is skipped when not due."""
        rotation_store = InMemoryWebhookKeyRotationStore()
        rotation_service = WebhookKeyRotationService(
            WebhookKeyRotationConfig(rotation_interval_hours=24, key_bits=256),
            rotation_store,
        )
        rotation_service.generate_new_key()

        pq_store = MagicMock()
        pq_store.claim_next = AsyncMock(return_value=[])
        pq_store.reset_expired_leases = AsyncMock()
        metrics = EnhancedMetrics()

        daemon = _make_daemon(
            priority_queue_store=pq_store,
            key_rotation_service=rotation_service,
            enhanced_metrics=metrics,
            key_rotation_enabled=True,
        )

        result = await daemon.run_once(dry_run=False)

        assert len(rotation_store.list_rotations()) == 1

    @pytest.mark.asyncio
    async def test_rotation_when_due(self):
        """Key rotation happens when due."""
        rotation_store = InMemoryWebhookKeyRotationStore()
        rotation_service = WebhookKeyRotationService(
            WebhookKeyRotationConfig(rotation_interval_hours=24, key_bits=256),
            rotation_store,
        )
        rotation_service.generate_new_key()

        last = rotation_store.get_last_rotation()
        if last:
            last.rotated_at = datetime.now(timezone.utc) - timedelta(hours=25)
            rotation_store._rotations[-1] = last

        pq_store = MagicMock()
        pq_store.claim_next = AsyncMock(return_value=[])
        pq_store.reset_expired_leases = AsyncMock()
        metrics = EnhancedMetrics()

        daemon = _make_daemon(
            priority_queue_store=pq_store,
            key_rotation_service=rotation_service,
            enhanced_metrics=metrics,
            key_rotation_enabled=True,
        )

        result = await daemon.run_once(dry_run=False)

        assert len(rotation_store.list_rotations()) == 2


# ---------------------------------------------------------------------------
# Phase 60 Task 6: Enhanced metrics recording
# ---------------------------------------------------------------------------


class TestEnhancedMetricsRecording:
    """Tests for metrics recording in daemon closed-loop."""

    @pytest.mark.asyncio
    async def test_metrics_recorded_on_success(self):
        """Metrics are recorded on successful replay."""
        pq_store = MagicMock()
        pq_store.claim_next = AsyncMock(return_value=[
            MagicMock(attempt_id="att_001", target_id="tgt_001", alert_id="alert_001"),
        ])
        pq_store.acknowledge = AsyncMock()
        pq_store.reset_expired_leases = AsyncMock()

        metrics = EnhancedMetrics()
        scheduler = _make_fake_scheduler()

        daemon = _make_daemon(
            scheduler=scheduler,
            priority_queue_store=pq_store,
            enhanced_metrics=metrics,
        )

        await daemon.run_once(dry_run=False)

        snap = metrics.snapshot()
        assert snap.replay.attempts == 1
        assert snap.replay.successes == 1

    @pytest.mark.asyncio
    async def test_metrics_prometheus_format(self):
        """Prometheus text format is valid."""
        metrics = EnhancedMetrics()
        metrics.record_replay_attempt()
        metrics.record_replay_success()
        metrics.record_lock_acquire_success()

        snapshot = metrics.snapshot()
        lines = [
            f"notification_replay_attempts_total {snapshot.replay.attempts}",
            f"notification_replay_success_total {snapshot.replay.successes}",
            f"notification_replay_lock_acquire_total {snapshot.distributed_lock.acquire_attempts}",
        ]
        output = "\n".join(lines) + "\n"

        assert "notification_replay_attempts_total 1" in output
        assert "notification_replay_success_total 1" in output
        assert "notification_replay_lock_acquire_total 1" in output


# ---------------------------------------------------------------------------
# Phase 60 Task 7: Config extensions
# ---------------------------------------------------------------------------


class TestPhase60Config:
    """Tests for Phase 60 config extensions."""

    def test_daemon_config_defaults(self):
        """Default daemon config has Phase 60 fields."""
        cfg = AlertDeliveryRetryDaemonConfig()
        assert cfg.distributed_lock_enabled is False
        assert cfg.lock_name == "notification-replay-daemon"
        assert cfg.lock_lease_seconds == 30
        assert cfg.key_rotation_enabled is False
        assert cfg.rate_limit_enabled is False
        assert cfg.idempotency_enabled is False
        assert cfg.dead_letter_enabled is False

    def test_daemon_config_phase60_overrides(self):
        """Phase 60 config fields can be overridden."""
        cfg = AlertDeliveryRetryDaemonConfig(
            distributed_lock_enabled=True,
            lock_name="custom-lock",
            lock_lease_seconds=60,
            key_rotation_enabled=True,
            rate_limit_enabled=True,
            rate_limit_window_seconds=120,
            rate_limit_max_attempts=20,
            idempotency_enabled=True,
            dead_letter_enabled=True,
        )
        assert cfg.distributed_lock_enabled is True
        assert cfg.lock_name == "custom-lock"
        assert cfg.lock_lease_seconds == 60
        assert cfg.key_rotation_enabled is True
        assert cfg.rate_limit_enabled is True
        assert cfg.rate_limit_window_seconds == 120
        assert cfg.rate_limit_max_attempts == 20
        assert cfg.idempotency_enabled is True
        assert cfg.dead_letter_enabled is True


# ---------------------------------------------------------------------------
# Phase 60 Task 8: Regression
# ---------------------------------------------------------------------------


class TestPhase59Regression:
    """Ensure Phase 60 changes don't break existing daemon behavior."""

    @pytest.mark.asyncio
    async def test_daemon_basic_run_without_phase60_stores(self):
        """Daemon works without Phase 60 stores (backward compat)."""
        scheduler = _make_fake_scheduler()
        daemon = _make_daemon(scheduler=scheduler)

        result = await daemon.run_once(dry_run=False)
        assert result is not None
        assert hasattr(result, "queue_claimed")

    @pytest.mark.asyncio
    async def test_daemon_health_status_without_phase60_stores(self):
        """Health status works without Phase 60 stores."""
        scheduler = _make_fake_scheduler()
        daemon = _make_daemon(scheduler=scheduler)

        status = daemon.get_health_status()
        assert "state" in status
        assert "source" in status
