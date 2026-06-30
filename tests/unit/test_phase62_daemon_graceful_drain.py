"""Phase 62 Task 1: Graceful drain tests."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from agent_app.runtime.policy_rollout_federation_notification_retry_daemon import (
    AlertDeliveryRetryDaemon,
    AlertDeliveryRetryDaemonConfig,
    AlertDeliveryRetryDaemonRunResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGracefulDrain:
    """Tests for graceful shutdown / drain."""

    @pytest.mark.asyncio
    async def test_stop_sets_draining_flag(self):
        """stop() sets _draining=True during drain."""
        cfg = _cfg(drain_timeout_seconds=0.5)
        daemon = _daemon(cfg)
        daemon._running = True
        # is_running checks _task is not None and not done
        daemon._task = asyncio.ensure_future(asyncio.sleep(10))

        async def _slow_item():
            await asyncio.sleep(10)

        async def _stop():
            await daemon.stop()

        with daemon._track_inflight():
            task = daemon._create_tracked_task(_slow_item())
            await asyncio.sleep(0)
            stop_task = asyncio.ensure_future(_stop())
            await asyncio.sleep(0.05)
            # While stop is running, draining should be True
            assert daemon._draining is True
            assert stop_task.done() is False
        await stop_task
        assert daemon._draining is False

    @pytest.mark.asyncio
    async def test_stop_waits_for_inflight_completion(self):
        """stop() waits for in-flight items to complete."""
        cfg = _cfg()
        daemon = _daemon(cfg)
        daemon._running = True
        daemon._task = asyncio.ensure_future(asyncio.sleep(10))

        completed = False

        async def _fake_item():
            nonlocal completed
            await asyncio.sleep(0.05)
            completed = True

        with daemon._track_inflight():
            task = daemon._create_tracked_task(_fake_item())
            await asyncio.sleep(0)
            assert daemon.inflight_count == 1
            await daemon.stop()
            assert completed is True
        assert daemon.inflight_count == 0

    @pytest.mark.asyncio
    async def test_stop_cancels_inflight_on_timeout(self):
        """stop() cancels remaining in-flight tasks after drain timeout."""
        cfg = _cfg(drain_timeout_seconds=0.1, cancel_inflight_on_timeout=True)
        daemon = _daemon(cfg)
        daemon._running = True
        daemon._task = asyncio.ensure_future(asyncio.sleep(10))

        cancelled = False

        async def _slow_item():
            nonlocal cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled = True
                raise

        with daemon._track_inflight():
            task = daemon._create_tracked_task(_slow_item())
            await asyncio.sleep(0)
            assert daemon.inflight_count == 1
            await daemon.stop()
            assert cancelled is True
        assert daemon.inflight_count == 0

    @pytest.mark.asyncio
    async def test_drain_disables_new_inflight(self):
        """When draining, _track_inflight still counts but stop waits."""
        cfg = _cfg()
        daemon = _daemon(cfg)
        daemon._running = True
        daemon._task = asyncio.ensure_future(asyncio.sleep(10))

        async def _quick_item():
            await asyncio.sleep(0.02)

        with daemon._track_inflight():
            task = daemon._create_tracked_task(_quick_item())
            await asyncio.sleep(0)
            await daemon.stop()
            assert task.done()

    @pytest.mark.asyncio
    async def test_stop_records_drain_duration(self):
        """stop() records _last_drain_duration_seconds."""
        cfg = _cfg()
        daemon = _daemon(cfg)
        daemon._running = True
        daemon._task = asyncio.ensure_future(asyncio.sleep(10))

        await daemon.stop()
        assert daemon._last_drain_duration_seconds is not None
        assert daemon._last_drain_duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_stop_idempotent_when_not_running(self):
        """stop() is idempotent when daemon is not running."""
        cfg = _cfg()
        daemon = _daemon(cfg)
        daemon._running = False
        await daemon.stop()  # Should not raise
        assert daemon._draining is False

    @pytest.mark.asyncio
    async def test_no_drain_when_no_inflight(self):
        """stop() returns immediately when no in-flight items."""
        cfg = _cfg()
        daemon = _daemon(cfg)
        daemon._running = True
        daemon._task = asyncio.ensure_future(asyncio.sleep(10))

        await daemon.stop()
        assert daemon._inflight_count == 0
        assert daemon._draining is False
