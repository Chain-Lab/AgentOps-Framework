"""Phase 61 Task 3: Daemon health status tests."""
from __future__ import annotations

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


@pytest.fixture
def config():
    return AlertDeliveryRetryDaemonConfig(
        enabled=True,
        interval_seconds=60.0,
        batch_limit=10,
    )


@pytest.fixture
def mock_scheduler():
    scheduler = MagicMock()
    scheduler.run_once.return_value = AlertDeliveryRetryDaemonRunResult(dry_run=False)
    return scheduler


def make_daemon(config, scheduler, **kwargs):
    return AlertDeliveryRetryDaemon(
        scheduler=scheduler,
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
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHealthStatus:
    """Tests for get_health_status."""

    def test_health_status_stopped_state(self, config, mock_scheduler):
        """Health status shows 'stopped' when not running."""
        daemon = make_daemon(config, mock_scheduler)
        daemon._running = False
        status = daemon.get_health_status()
        assert status["state"] == "stopped"

    def test_health_status_healthy_state(self, config, mock_scheduler):
        """Health status shows 'healthy' when running with no errors."""
        daemon = make_daemon(config, mock_scheduler)
        daemon._running = True
        daemon._task = MagicMock()
        daemon._task.done.return_value = False
        daemon._consecutive_failures = 0
        status = daemon.get_health_status()
        assert status["state"] == "healthy"

    def test_health_status_degraded_state(self, config, mock_scheduler):
        """Health status shows 'degraded' with 1-2 consecutive failures."""
        daemon = make_daemon(config, mock_scheduler)
        daemon._running = True
        daemon._task = MagicMock()
        daemon._task.done.return_value = False
        daemon._consecutive_failures = 2
        status = daemon.get_health_status()
        assert status["state"] == "degraded"

    def test_health_status_unhealthy_state(self, config, mock_scheduler):
        """Health status shows 'unhealthy' with 3+ consecutive failures."""
        daemon = make_daemon(config, mock_scheduler)
        daemon._running = True
        daemon._task = MagicMock()
        daemon._task.done.return_value = False
        daemon._consecutive_failures = 5
        status = daemon.get_health_status()
        assert status["state"] == "unhealthy"

    def test_health_status_has_phase61_fields(self, config, mock_scheduler):
        """Health status includes Phase 61 running and leader fields."""
        daemon = make_daemon(config, mock_scheduler)
        daemon._running = True
        daemon._task = MagicMock()
        daemon._task.done.return_value = False
        daemon._leader_mode = True
        status = daemon.get_health_status()
        assert "running" in status
        assert "leader" in status
        assert status["running"] is True
        assert status["leader"] is True

    def test_health_status_leader_false(self, config, mock_scheduler):
        """Health status shows leader=False when not in leader mode."""
        daemon = make_daemon(config, mock_scheduler)
        daemon._running = True
        daemon._task = MagicMock()
        daemon._task.done.return_value = False
        daemon._leader_mode = False
        status = daemon.get_health_status()
        assert status["leader"] is False

    def test_health_status_has_last_error(self, config, mock_scheduler):
        """Health status includes redacted last_error."""
        daemon = make_daemon(config, mock_scheduler)
        daemon._running = True
        daemon._task = MagicMock()
        daemon._task.done.return_value = False
        daemon._last_error = "some connection error"
        status = daemon.get_health_status()
        assert status["last_error"] is not None
        assert "connection" in status["last_error"]

    def test_health_status_interval_seconds(self, config, mock_scheduler):
        """Health status includes interval_seconds."""
        daemon = make_daemon(config, mock_scheduler)
        daemon._running = True
        daemon._task = MagicMock()
        daemon._task.done.return_value = False
        status = daemon.get_health_status()
        assert status["interval_seconds"] == 60.0

    def test_health_status_consecutive_failures(self, config, mock_scheduler):
        """Health status includes consecutive_failures."""
        daemon = make_daemon(config, mock_scheduler)
        daemon._running = True
        daemon._task = MagicMock()
        daemon._task.done.return_value = False
        daemon._consecutive_failures = 3
        status = daemon.get_health_status()
        assert status["consecutive_failures"] == 3
