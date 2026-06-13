"""Tests for PolicyEnvironmentState model (Phase 32)."""
import pytest
from datetime import datetime, timezone
from agent_app.governance.policy_environment import (
    PolicyEnvironmentStatus,
    PolicyEnvironmentState,
)


class TestPolicyEnvironmentStatus:
    def test_enabled_value(self):
        assert PolicyEnvironmentStatus.ENABLED == "enabled"

    def test_disabled_value(self):
        assert PolicyEnvironmentStatus.DISABLED == "disabled"

    def test_all_statuses(self):
        values = {s.value for s in PolicyEnvironmentStatus}
        assert values == {"enabled", "disabled"}


class TestPolicyEnvironmentState:
    def test_default_enabled(self):
        state = PolicyEnvironmentState(environment="prod")
        assert state.status == PolicyEnvironmentStatus.ENABLED
        assert state.disabled_reason is None
        assert state.disabled_by is None
        assert state.disabled_at is None
        assert state.enabled_by is None
        assert state.enabled_at is None

    def test_disabled_state(self):
        now = datetime.now(timezone.utc)
        state = PolicyEnvironmentState(
            environment="prod",
            status=PolicyEnvironmentStatus.DISABLED,
            disabled_reason="Emergency",
            disabled_by="admin",
            disabled_at=now,
        )
        assert state.status == PolicyEnvironmentStatus.DISABLED
        assert state.disabled_reason == "Emergency"
        assert state.disabled_by == "admin"
        assert state.disabled_at is not None

    def test_requires_environment(self):
        with pytest.raises(Exception):
            PolicyEnvironmentState()

    def test_updated_at_timezone_aware(self):
        state = PolicyEnvironmentState(environment="staging")
        assert state.updated_at.tzinfo is not None
