"""Phase 45 Task 5 tests — config schema, RBAC, change events, AgentApp properties."""

import pytest

from agent_app.config.schema import (
    PolicyReleaseConfig,
    PolicyReleaseStoreConfig,
    RolloutHistoryConfig,
)
from agent_app.governance.policy_rbac import PolicyReleasePermission, _DEFAULT_ALLOWED
from agent_app.governance.policy_change_event import PolicyChangeEventType
from agent_app.core.app import AgentApp


# ---------------------------------------------------------------------------
# 1. RolloutHistoryConfig
# ---------------------------------------------------------------------------


class TestRolloutHistoryConfig:
    """Tests for RolloutHistoryConfig model."""

    def test_rollout_history_config_default(self) -> None:
        """Default config has enabled=False."""
        cfg = RolloutHistoryConfig()
        assert cfg.enabled is False
        assert cfg.store is None

    def test_rollout_history_config_enabled(self) -> None:
        """Can set enabled=True."""
        cfg = RolloutHistoryConfig(enabled=True)
        assert cfg.enabled is True

    def test_rollout_history_config_with_store(self) -> None:
        """Store config works."""
        store_cfg = PolicyReleaseStoreConfig(type="sqlite", path="/tmp/rh.db")
        cfg = RolloutHistoryConfig(enabled=True, store=store_cfg)
        assert cfg.store is not None
        assert cfg.store.type == "sqlite"
        assert cfg.store.path == "/tmp/rh.db"


# ---------------------------------------------------------------------------
# 2. RBAC permissions
# ---------------------------------------------------------------------------


class TestRolloutHistoryRBAC:
    """Tests for rollout history RBAC permissions."""

    def test_rbac_rolout_history_view(self) -> None:
        """ROLLOUT_HISTORY_VIEW permission exists."""
        assert hasattr(PolicyReleasePermission, "ROLLOUT_HISTORY_VIEW")
        assert PolicyReleasePermission.ROLLOUT_HISTORY_VIEW.value == "policy.rollout.history.view"

    def test_rbac_rolout_analytics_view(self) -> None:
        """ROLLOUT_ANALYTICS_VIEW permission exists."""
        assert hasattr(PolicyReleasePermission, "ROLLOUT_ANALYTICS_VIEW")
        assert PolicyReleasePermission.ROLLOUT_ANALYTICS_VIEW.value == "policy.rollout.analytics.view"

    def test_rbac_rolout_analytics_export(self) -> None:
        """ROLLOUT_ANALYTICS_EXPORT permission exists."""
        assert hasattr(PolicyReleasePermission, "ROLLOUT_ANALYTICS_EXPORT")
        assert PolicyReleasePermission.ROLLOUT_ANALYTICS_EXPORT.value == "policy.rollout.analytics.export"

    def test_rbac_history_view_default_allowed(self) -> None:
        """ROLLOUT_HISTORY_VIEW in _DEFAULT_ALLOWED."""
        assert PolicyReleasePermission.ROLLOUT_HISTORY_VIEW in _DEFAULT_ALLOWED

    def test_rbac_analytics_view_default_allowed(self) -> None:
        """ROLLOUT_ANALYTICS_VIEW in _DEFAULT_ALLOWED."""
        assert PolicyReleasePermission.ROLLOUT_ANALYTICS_VIEW in _DEFAULT_ALLOWED


# ---------------------------------------------------------------------------
# 3. Change event types
# ---------------------------------------------------------------------------


class TestRolloutHistoryChangeEvents:
    """Tests for rollout history change event types."""

    def test_change_event_types_count(self) -> None:
        """133 event types total (124 previous + 9 Phase 53)."""
        assert len(PolicyChangeEventType) == 150

    def test_change_event_rolout_history_recorded(self) -> None:
        """ROLLOUT_HISTORY_RECORDED exists."""
        assert PolicyChangeEventType.ROLLOUT_HISTORY_RECORDED.value == "policy.rollout.history.recorded"

    def test_change_event_rolout_history_viewed(self) -> None:
        """ROLLOUT_HISTORY_VIEWED exists."""
        assert PolicyChangeEventType.ROLLOUT_HISTORY_VIEWED.value == "policy.rollout.history.viewed"

    def test_change_event_rolout_timeline_generated(self) -> None:
        """ROLLOUT_TIMELINE_GENERATED exists."""
        assert PolicyChangeEventType.ROLLOUT_TIMELINE_GENERATED.value == "policy.rollout.timeline.generated"

    def test_change_event_rolout_analytics_generated(self) -> None:
        """ROLLOUT_ANALYTICS_GENERATED exists."""
        assert PolicyChangeEventType.ROLLOUT_ANALYTICS_GENERATED.value == "policy.rollout.analytics.generated"

    def test_change_event_rolout_analytics_export_generated(self) -> None:
        """ROLLOUT_ANALYTICS_EXPORT_GENERATED exists."""
        assert PolicyChangeEventType.ROLLOUT_ANALYTICS_EXPORT_GENERATED.value == "policy.rollout.analytics.export_generated"

    def test_change_event_rolout_analytics_export_failed(self) -> None:
        """ROLLOUT_ANALYTICS_EXPORT_FAILED exists."""
        assert PolicyChangeEventType.ROLLOUT_ANALYTICS_EXPORT_FAILED.value == "policy.rollout.analytics.export_failed"

    def test_change_event_rolout_analytics_permission_denied(self) -> None:
        """ROLLOUT_ANALYTICS_PERMISSION_DENIED exists."""
        assert PolicyChangeEventType.ROLLOUT_ANALYTICS_PERMISSION_DENIED.value == "policy.rollout.analytics.permission_denied"


# ---------------------------------------------------------------------------
# 4. AgentApp properties
# ---------------------------------------------------------------------------


class TestAgentAppRolloutHistoryProperties:
    """Tests for AgentApp rollout history properties."""

    def test_agent_app_rolout_history_store_property(self) -> None:
        """AgentApp has rollout_history_store property."""
        app = AgentApp()
        assert app.rollout_history_store is None
        app.rollout_history_store = "test_store"
        assert app.rollout_history_store == "test_store"

    def test_agent_app_rolout_history_recorder_property(self) -> None:
        """AgentApp has rollout_history_recorder property."""
        app = AgentApp()
        assert app.rollout_history_recorder is None
        app.rollout_history_recorder = "test_recorder"
        assert app.rollout_history_recorder == "test_recorder"

    def test_agent_app_rolout_history_service_property(self) -> None:
        """AgentApp has rollout_history_service property."""
        app = AgentApp()
        assert app.rollout_history_service is None
        app.rollout_history_service = "test_service"
        assert app.rollout_history_service == "test_service"
