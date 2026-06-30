"""Phase 62 Task 4: Lock lease extension tests."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from agent_app.runtime.policy_rollout_federation_notification_retry_daemon import (
    AlertDeliveryRetryDaemon,
    AlertDeliveryRetryDaemonConfig,
)


def _cfg(**overrides: Any) -> AlertDeliveryRetryDaemonConfig:
    d: dict[str, Any] = {
        "enabled": True,
        "interval_seconds": 60.0,
        "batch_limit": 10,
        "run_immediately": False,
        "poll_interval_seconds": 0.01,
        "idle_sleep_seconds": 0.01,
        "error_sleep_seconds": 0.01,
        "max_consecutive_errors": 3,
        "shutdown_timeout_seconds": 2.0,
        "graceful_shutdown_enabled": True,
        "drain_timeout_seconds": 5.0,
        "cancel_inflight_on_timeout": True,
        "flush_metrics_on_stop": False,
        "renew_lock_during_batch": True,
        "lock_renewal_failure_policy": "standby",
        "distributed_lock_enabled": True,
        "lock_lease_seconds": 30,
        "lock_renew_interval_seconds": 10,
    }
    d.update(overrides)
    return AlertDeliveryRetryDaemonConfig(**d)


def _daemon(cfg, scheduler=None, lock_store=None, enhanced_metrics=None, **kwargs):
    return AlertDeliveryRetryDaemon(
        scheduler=scheduler or MagicMock(),
        config=cfg,
        audit_logger=None,
        change_event_store=None,
        priority_queue_store=None,
        daemon_state_store=None,
        idempotency_store=None,
        rate_limiter_store=None,
        dead_letter_policy_store=None,
        distributed_lock_store=lock_store,
        key_rotation_service=None,
        enhanced_metrics=enhanced_metrics,
        **kwargs,
    )


class TestLockLeaseExtension:
    """Tests for lock lease renewal during batch processing."""

    @pytest.mark.asyncio
    async def test_should_renew_lock_true_when_never_renewed(self):
        """_should_renew_lock returns True when lock was never renewed."""
        cfg = _cfg()
        daemon = _daemon(cfg)
        daemon._last_lock_renew_at = None
        assert daemon._should_renew_lock() is True

    @pytest.mark.asyncio
    async def test_should_renew_lock_false_when_recently_renewed(self):
        """_should_renew_lock returns False when recently renewed."""
        cfg = _cfg()
        daemon = _daemon(cfg)
        daemon._last_lock_renew_at = datetime.now(timezone.utc)
        assert daemon._should_renew_lock() is False

    @pytest.mark.asyncio
    async def test_should_renew_lock_true_after_threshold(self):
        """_should_renew_lock returns True after 80% of interval."""
        cfg = _cfg(lock_renew_interval_seconds=10)
        daemon = _daemon(cfg)
        # Renew 9 seconds ago (> 80% of 10s = 8s)
        daemon._last_lock_renew_at = datetime.now(timezone.utc).replace(
            microsecond=0
        ) - __import__("datetime").timedelta(seconds=9)
        assert daemon._should_renew_lock() is True

    @pytest.mark.asyncio
    async def test_renew_lock_success(self):
        """_renew_distributed_lock renews and updates timestamp on success."""
        cfg = _cfg(distributed_lock_enabled=True)
        lock_store = MagicMock()
        status = MagicMock()
        status.acquired = True
        status.fencing_token = "new-token"
        lock_store.renew.return_value = status
        daemon = _daemon(cfg, lock_store=lock_store)
        daemon._lock_owner_id = "worker-1"
        daemon._leader_mode = True

        result = daemon._renew_distributed_lock()
        assert result is True
        assert daemon._lock_fencing_token == "new-token"
        assert daemon._last_lock_renew_at is not None

    @pytest.mark.asyncio
    async def test_renew_lock_failure_sets_standby(self):
        """Failed lock renewal sets leader_mode=False."""
        cfg = _cfg(
            distributed_lock_enabled=True,
            lock_renewal_failure_policy="standby",
        )
        lock_store = MagicMock()
        status = MagicMock()
        status.acquired = False
        lock_store.renew.return_value = status
        metrics = MagicMock()
        daemon = _daemon(cfg, lock_store=lock_store, enhanced_metrics=metrics)
        daemon._lock_owner_id = "worker-1"
        daemon._leader_mode = True

        result = daemon._renew_distributed_lock()
        assert result is False
        assert daemon._leader_mode is False
        assert daemon._lock_owner_id is None
        metrics.record_lock_renew_failed.assert_called_once()

    @pytest.mark.asyncio
    async def test_renew_lock_returns_true_when_no_lock_store(self):
        """_renew_distributed_lock returns True when no lock store (fail-open)."""
        cfg = _cfg(distributed_lock_enabled=True)
        daemon = _daemon(cfg, lock_store=None)
        daemon._leader_mode = True
        assert daemon._renew_distributed_lock() is True

    @pytest.mark.asyncio
    async def test_renew_lock_returns_true_when_lock_disabled(self):
        """_renew_distributed_lock returns True when distributed lock disabled."""
        cfg = _cfg(distributed_lock_enabled=False)
        lock_store = MagicMock()
        daemon = _daemon(cfg, lock_store=lock_store)
        daemon._leader_mode = True
        assert daemon._renew_distributed_lock() is True
        lock_store.renew.assert_not_called()
