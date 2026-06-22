"""Phase 47 Task 6 tests — config schema, RBAC, change events, AgentApp properties."""

import pytest

from agent_app.config.schema import (
    PolicyReleaseConfig,
    PolicyReleaseStoreConfig,
    RolloutFederationHistoryConfig,
)
from agent_app.governance.policy_rbac import PolicyReleasePermission, _DEFAULT_ALLOWED
from agent_app.governance.policy_change_event import PolicyChangeEventType
from agent_app.core.app import AgentApp


class TestRolloutFederationHistoryConfig:
    """Tests for RolloutFederationHistoryConfig model."""

    def test_config_default_disabled(self) -> None:
        cfg = RolloutFederationHistoryConfig()
        assert cfg.enabled is False
        assert cfg.store is None

    def test_config_enabled(self) -> None:
        cfg = RolloutFederationHistoryConfig(enabled=True)
        assert cfg.enabled is True

    def test_config_with_store(self) -> None:
        store_cfg = PolicyReleaseStoreConfig(type="sqlite", path="/tmp/fed_hist.db")
        cfg = RolloutFederationHistoryConfig(enabled=True, store=store_cfg)
        assert cfg.store is not None
        assert cfg.store.type == "sqlite"

    def test_policy_release_config_has_field(self) -> None:
        cfg = PolicyReleaseConfig()
        assert cfg.rollout_federation_history is None


class TestFederationHistoryRBAC:
    """Tests for federation history RBAC permissions."""

    def test_federation_history_view(self) -> None:
        assert PolicyReleasePermission.FEDERATION_HISTORY_VIEW.value == "policy.federation.history.view"

    def test_federation_analytics_view(self) -> None:
        assert PolicyReleasePermission.FEDERATION_ANALYTICS_VIEW.value == "policy.federation.analytics.view"

    def test_federation_analytics_export(self) -> None:
        assert PolicyReleasePermission.FEDERATION_ANALYTICS_EXPORT.value == "policy.federation.analytics.export"

    def test_view_permissions_default_allowed(self) -> None:
        assert PolicyReleasePermission.FEDERATION_HISTORY_VIEW in _DEFAULT_ALLOWED
        assert PolicyReleasePermission.FEDERATION_ANALYTICS_VIEW in _DEFAULT_ALLOWED


class TestFederationHistoryChangeEvents:
    """Tests for federation history change event types."""

    def test_event_type_count(self) -> None:
        assert len(PolicyChangeEventType) == 118

    def test_federation_history_events_exist(self) -> None:
        assert PolicyChangeEventType.FEDERATION_HISTORY_RECORDED.value == "policy.federation.history.recorded"
        assert PolicyChangeEventType.FEDERATION_HISTORY_VIEWED.value == "policy.federation.history.viewed"
        assert PolicyChangeEventType.FEDERATION_TIMELINE_GENERATED.value == "policy.federation.timeline.generated"
        assert PolicyChangeEventType.FEDERATION_ANALYTICS_GENERATED.value == "policy.federation.analytics.generated"
        assert PolicyChangeEventType.FEDERATION_ANALYTICS_EXPORT_GENERATED.value == "policy.federation.analytics.export_generated"
        assert PolicyChangeEventType.FEDERATION_ANALYTICS_EXPORT_FAILED.value == "policy.federation.analytics.export_failed"
        assert PolicyChangeEventType.FEDERATION_ANALYTICS_PERMISSION_DENIED.value == "policy.federation.analytics.permission_denied"


class TestAgentAppFederationHistoryProperties:
    """Tests for AgentApp federation history properties."""

    def test_federation_history_store_property(self) -> None:
        app = AgentApp()
        assert app.federation_history_store is None
        app.federation_history_store = "test_store"
        assert app.federation_history_store == "test_store"

    def test_federation_history_recorder_property(self) -> None:
        app = AgentApp()
        assert app.federation_history_recorder is None
        app.federation_history_recorder = "test_recorder"
        assert app.federation_history_recorder == "test_recorder"

    def test_federation_observability_service_property(self) -> None:
        app = AgentApp()
        assert app.federation_observability_service is None
        app.federation_observability_service = "test_service"
        assert app.federation_observability_service == "test_service"
