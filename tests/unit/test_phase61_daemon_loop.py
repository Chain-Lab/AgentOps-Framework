"""Phase 61 Task 1: Continuous daemon loop tests."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent_app.runtime.policy_rollout_federation_notification_retry_daemon import (
    AlertDeliveryRetryDaemon,
    AlertDeliveryRetryDaemonConfig,
    AlertDeliveryRetryDaemonRunResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> AlertDeliveryRetryDaemonConfig:
    defaults: dict[str, Any] = {
        "enabled": True,
        "interval_seconds": 60.0,
        "batch_limit": 10,
        "run_immediately": False,
        "poll_interval_seconds": 0.01,
        "idle_sleep_seconds": 0.01,
        "error_sleep_seconds": 0.01,
        "max_consecutive_errors": 3,
        "shutdown_timeout_seconds": 2.0,
    }
    defaults.update(overrides)
    return AlertDeliveryRetryDaemonConfig(**defaults)


def _make_minimal_daemon(config: AlertDeliveryRetryDaemonConfig) -> AlertDeliveryRetryDaemon:
    return AlertDeliveryRetryDaemon(
        scheduler=MagicMock(),
        config=config,
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
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunOnceContinuous:
    """Tests for _run_once_continuous."""

    def test_delegates_to_run_once_with_leader_mode(self):
        """_run_once_continuous calls run_once with _leader_mode=True."""
        config = _make_config()
        daemon = _make_minimal_daemon(config)

        async def fake_run_once(**kwargs):
            return AlertDeliveryRetryDaemonRunResult(dry_run=False)

        with patch.object(daemon, "run_once", side_effect=fake_run_once) as mock_run:
            asyncio.run(daemon._run_once_continuous())
            mock_run.assert_called_once_with(dry_run=False, _leader_mode=True)


class TestRunForever:
    """Tests for run_forever."""

    def test_sets_running_true_then_false(self):
        """run_forever sets _running=True then back to False."""
        config = _make_config()
        daemon = _make_minimal_daemon(config)

        async def fake_loop():
            daemon._running = False

        with patch.object(daemon, "_loop", side_effect=fake_loop):
            asyncio.run(daemon.run_forever())
        assert daemon._running is False

    def test_flushes_metrics_on_exception(self):
        """run_forever calls _flush_metrics in finally block."""
        config = _make_config()
        daemon = _make_minimal_daemon(config)
        daemon._running = True

        async def failing_loop():
            raise Exception("test error")

        with patch.object(daemon, "_loop", side_effect=failing_loop):
            with patch.object(daemon, "_flush_metrics") as mock_flush:
                with pytest.raises(Exception):
                    asyncio.run(daemon.run_forever())
                assert daemon._running is False
                mock_flush.assert_called_once()


class TestContinuousLoop:
    """Tests for _loop continuous operation."""

    def test_run_immediately_with_leader(self):
        """Loop runs once when run_immediately=True and leader mode."""
        config = _make_config(run_immediately=True)
        daemon = _make_minimal_daemon(config)
        daemon._leader_mode = True
        daemon._running = True

        call_count = 0

        async def fake_run():
            nonlocal call_count
            call_count += 1
            daemon._running = False
            return AlertDeliveryRetryDaemonRunResult(dry_run=False)

        with patch.object(daemon, "_run_once_continuous", side_effect=fake_run):
            with patch.object(daemon, "_should_renew_lock", return_value=False):
                asyncio.run(daemon._loop())
        assert call_count >= 1

    def test_no_lock_store_acquire_returns_true(self):
        """Without lock store, _acquire_distributed_lock returns True."""
        config = _make_config()
        daemon = _make_minimal_daemon(config)
        assert daemon._distributed_lock_store is None
        result = daemon._acquire_distributed_lock()
        assert result is True

    def test_max_errors_releases_lock_and_stops(self):
        """Loop releases lock and stops running after max consecutive errors."""
        config = _make_config()
        daemon = _make_minimal_daemon(config)
        daemon._leader_mode = True
        daemon._running = True
        daemon._consecutive_failures = 0

        acquire_count = 0

        def fake_acquire():
            nonlocal acquire_count
            acquire_count += 1
            if acquire_count >= 2:
                daemon._running = False
            return False  # Never re-acquire → stays in standby

        async def failing_run():
            daemon._consecutive_failures += 1
            raise Exception("persistent failure")

        with patch.object(daemon, "_run_once_continuous", side_effect=failing_run):
            with patch.object(daemon, "_should_renew_lock", return_value=False):
                with patch.object(daemon, "_acquire_distributed_lock", side_effect=fake_acquire):
                    with patch("asyncio.sleep"):
                        asyncio.run(daemon._loop())
        assert daemon._consecutive_failures >= config.max_consecutive_errors
        assert daemon._leader_mode is False

    def test_cancelled_error_breaks_loop(self):
        """CancelledError breaks the loop cleanly."""
        config = _make_config()
        daemon = _make_minimal_daemon(config)
        daemon._leader_mode = True
        daemon._running = True

        async def cancelled_run():
            raise asyncio.CancelledError()

        with patch.object(daemon, "_run_once_continuous", side_effect=cancelled_run):
            with patch.object(daemon, "_should_renew_lock", return_value=False):
                asyncio.run(daemon._loop())
        # _loop breaks on CancelledError but doesn't set _running=False
        # (caller like stop() or run_forever handles that)
        assert daemon._running is True  # Loop broke but didn't clear running


class TestConfigDefaults:
    """Tests for Phase 61 config defaults."""

    def test_phase61_defaults(self):
        """All Phase 61 config fields have correct defaults."""
        cfg = AlertDeliveryRetryDaemonConfig()
        assert cfg.poll_interval_seconds == 1.0
        assert cfg.idle_sleep_seconds == 1.0
        assert cfg.error_sleep_seconds == 5.0
        assert cfg.max_consecutive_errors == 10
        assert cfg.shutdown_timeout_seconds == 10.0

    def test_phase61_custom_values(self):
        """Can set all Phase 61 config fields."""
        cfg = AlertDeliveryRetryDaemonConfig(
            poll_interval_seconds=0.5,
            idle_sleep_seconds=0.2,
            error_sleep_seconds=3.0,
            max_consecutive_errors=5,
            shutdown_timeout_seconds=5.0,
        )
        assert cfg.poll_interval_seconds == 0.5
        assert cfg.idle_sleep_seconds == 0.2
        assert cfg.error_sleep_seconds == 3.0
        assert cfg.max_consecutive_errors == 5
        assert cfg.shutdown_timeout_seconds == 5.0
