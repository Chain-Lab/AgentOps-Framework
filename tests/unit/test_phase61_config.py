"""Phase 61 Task 4: Daemon YAML config loading tests."""
from __future__ import annotations

import pytest

from agent_app.runtime.policy_rollout_federation_notification_retry_daemon import (
    AlertDeliveryRetryDaemonConfig,
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDaemonConfigDefaults:
    """Tests for AlertDeliveryRetryDaemonConfig Phase 61 defaults."""

    def test_default_poll_interval(self):
        """Default poll_interval_seconds is 1.0."""
        cfg = AlertDeliveryRetryDaemonConfig()
        assert cfg.poll_interval_seconds == 1.0

    def test_default_idle_sleep(self):
        """Default idle_sleep_seconds is 1.0."""
        cfg = AlertDeliveryRetryDaemonConfig()
        assert cfg.idle_sleep_seconds == 1.0

    def test_default_error_sleep(self):
        """Default error_sleep_seconds is 5.0."""
        cfg = AlertDeliveryRetryDaemonConfig()
        assert cfg.error_sleep_seconds == 5.0

    def test_default_max_consecutive_errors(self):
        """Default max_consecutive_errors is 10."""
        cfg = AlertDeliveryRetryDaemonConfig()
        assert cfg.max_consecutive_errors == 10

    def test_default_shutdown_timeout(self):
        """Default shutdown_timeout_seconds is 10.0."""
        cfg = AlertDeliveryRetryDaemonConfig()
        assert cfg.shutdown_timeout_seconds == 10.0


class TestDaemonConfigValues:
    """Tests for setting Phase 61 config values."""

    def test_set_all_phase61_fields(self):
        """Can set all Phase 61 config fields."""
        cfg = AlertDeliveryRetryDaemonConfig(
            enabled=True,
            interval_seconds=30.0,
            batch_limit=50,
            poll_interval_seconds=0.5,
            idle_sleep_seconds=0.2,
            error_sleep_seconds=3.0,
            max_consecutive_errors=5,
            shutdown_timeout_seconds=5.0,
        )
        assert cfg.enabled is True
        assert cfg.poll_interval_seconds == 0.5
        assert cfg.idle_sleep_seconds == 0.2
        assert cfg.error_sleep_seconds == 3.0
        assert cfg.max_consecutive_errors == 5
        assert cfg.shutdown_timeout_seconds == 5.0


class TestDaemonConfigDictCompat:
    """Tests for dict[str, Any] YAML compatibility."""

    def test_from_dict_with_phase61_fields(self):
        """Config can be created from dict with Phase 61 fields."""
        data = {
            "enabled": True,
            "interval_seconds": 30.0,
            "batch_limit": 50,
            "poll_interval_seconds": 0.5,
            "idle_sleep_seconds": 0.2,
            "error_sleep_seconds": 3.0,
            "max_consecutive_errors": 5,
            "shutdown_timeout_seconds": 5.0,
        }
        cfg = AlertDeliveryRetryDaemonConfig(**data)
        assert cfg.poll_interval_seconds == 0.5
        assert cfg.idle_sleep_seconds == 0.2
        assert cfg.error_sleep_seconds == 3.0

    def test_from_dict_omits_phase61_uses_defaults(self):
        """Config from dict without Phase 61 fields uses defaults."""
        data = {"enabled": True}
        cfg = AlertDeliveryRetryDaemonConfig(**data)
        assert cfg.poll_interval_seconds == 1.0
        assert cfg.idle_sleep_seconds == 1.0
        assert cfg.error_sleep_seconds == 5.0
