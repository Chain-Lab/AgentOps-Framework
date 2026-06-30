"""Phase 62 Task 2: In-flight tracking tests."""
from __future__ import annotations

import asyncio
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
    }
    d.update(overrides)
    return AlertDeliveryRetryDaemonConfig(**d)


def _daemon(cfg, scheduler=None, **kwargs):
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
        distributed_lock_store=None,
        key_rotation_service=None,
        enhanced_metrics=None,
        **kwargs,
    )


class TestInFlightTracking:
    """Tests for in-flight item tracking."""

    @pytest.mark.asyncio
    async def test_inflight_count_starts_at_zero(self):
        """inflight_count is 0 for a fresh daemon."""
        cfg = _cfg()
        daemon = _daemon(cfg)
        assert daemon.inflight_count == 0

    @pytest.mark.asyncio
    async def test_track_inflight_increments_and_decrements(self):
        """_track_inflight increments on enter, decrements on exit."""
        cfg = _cfg()
        daemon = _daemon(cfg)
        assert daemon.inflight_count == 0
        with daemon._track_inflight():
            assert daemon.inflight_count == 1
        assert daemon.inflight_count == 0

    @pytest.mark.asyncio
    async def test_track_inflight_nested(self):
        """Nested _track_inflight increments multiple times."""
        cfg = _cfg()
        daemon = _daemon(cfg)
        assert daemon.inflight_count == 0
        with daemon._track_inflight():
            assert daemon.inflight_count == 1
            with daemon._track_inflight():
                assert daemon.inflight_count == 2
            assert daemon.inflight_count == 1
        assert daemon.inflight_count == 0

    @pytest.mark.asyncio
    async def test_track_inflight_decrements_on_exception(self):
        """_track_inflight decrements even if body raises."""
        cfg = _cfg()
        daemon = _daemon(cfg)
        assert daemon.inflight_count == 0
        with pytest.raises(RuntimeError):
            with daemon._track_inflight():
                assert daemon.inflight_count == 1
                raise RuntimeError("boom")
        assert daemon.inflight_count == 0

    @pytest.mark.asyncio
    async def test_create_tracked_task_adds_to_inflight_tasks(self):
        """_create_tracked_task adds task to _inflight_tasks."""
        cfg = _cfg()
        daemon = _daemon(cfg)

        async def _noop():
            pass

        task = daemon._create_tracked_task(_noop())
        await asyncio.sleep(0)
        assert task in daemon._inflight_tasks
        assert task.done()

    @pytest.mark.asyncio
    async def test_create_tracked_task_removes_on_done(self):
        """_create_tracked_task removes task from _inflight_tasks when done."""
        cfg = _cfg()
        daemon = _daemon(cfg)

        async def _short():
            await asyncio.sleep(0.01)

        task = daemon._create_tracked_task(_short())
        await asyncio.sleep(0)
        assert task in daemon._inflight_tasks
        await asyncio.sleep(0.05)
        assert task not in daemon._inflight_tasks

    @pytest.mark.asyncio
    async def test_draining_property(self):
        """draining property reflects _draining state."""
        cfg = _cfg()
        daemon = _daemon(cfg)
        assert daemon.draining is False
        daemon._draining = True
        assert daemon.draining is True
        daemon._draining = False
        assert daemon.draining is False
