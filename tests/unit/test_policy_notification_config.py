"""Tests for Phase 44 notification/expiration config, RBAC, events, and AgentApp properties."""

from __future__ import annotations

import pytest

from agent_app.governance.policy_rbac import PolicyReleasePermission, _DEFAULT_ALLOWED
from agent_app.governance.policy_change_event import PolicyChangeEventType
from agent_app.config.schema import (
    ExpirationConfig,
    NotificationConfig,
    NotificationRuleConfig,
    PolicyReleaseConfig,
    PolicyReleaseStoreConfig,
)
from agent_app.core.app import AgentApp


# ---------------------------------------------------------------------------
# RBAC permissions
# ---------------------------------------------------------------------------


class TestRBACPermissions:
    """Tests for the 7 new Phase 44 RBAC permissions."""

    def test_notification_permissions_exist(self) -> None:
        """All 5 notification permissions exist in the enum."""
        assert PolicyReleasePermission.NOTIFICATION_VIEW == "policy.notification.view"
        assert PolicyReleasePermission.NOTIFICATION_SEND == "policy.notification.send"
        assert PolicyReleasePermission.NOTIFICATION_RULE_VIEW == "policy.notification.rule.view"
        assert PolicyReleasePermission.NOTIFICATION_RULE_ENABLE == "policy.notification.rule.enable"
        assert PolicyReleasePermission.NOTIFICATION_RULE_DISABLE == "policy.notification.rule.disable"

    def test_expiration_permissions_exist(self) -> None:
        """Both expiration permissions exist in the enum."""
        assert PolicyReleasePermission.EXPIRATION_SWEEP == "policy.expiration.sweep"
        assert PolicyReleasePermission.EXPIRATION_VIEW == "policy.expiration.view"

    def test_view_permissions_default_allowed(self) -> None:
        """NOTIFICATION_VIEW and EXPIRATION_VIEW are in the default-allowed set."""
        assert PolicyReleasePermission.NOTIFICATION_VIEW in _DEFAULT_ALLOWED
        assert PolicyReleasePermission.EXPIRATION_VIEW in _DEFAULT_ALLOWED


# ---------------------------------------------------------------------------
# Change events
# ---------------------------------------------------------------------------


class TestChangeEvents:
    """Tests for the 10 new Phase 44 change event types."""

    def test_new_event_types(self) -> None:
        """All 10 new event types exist with correct values."""
        assert PolicyChangeEventType.NOTIFICATION_CREATED == "policy.notification.created"
        assert PolicyChangeEventType.NOTIFICATION_SENT == "policy.notification.sent"
        assert PolicyChangeEventType.NOTIFICATION_FAILED == "policy.notification.failed"
        assert PolicyChangeEventType.NOTIFICATION_RULE_ENABLED == "policy.notification.rule.enabled"
        assert PolicyChangeEventType.NOTIFICATION_RULE_DISABLED == "policy.notification.rule.disabled"
        assert PolicyChangeEventType.EXPIRATION_SWEEP_STARTED == "policy.expiration.sweep_started"
        assert PolicyChangeEventType.EXPIRATION_SWEEP_COMPLETED == "policy.expiration.sweep_completed"
        assert PolicyChangeEventType.EXPIRATION_SWEEP_FAILED == "policy.expiration.sweep_failed"
        assert PolicyChangeEventType.EXPIRATION_TARGET_EXPIRED == "policy.expiration.target_expired"
        assert PolicyChangeEventType.EXPIRATION_PERMISSION_DENIED == "policy.expiration.permission_denied"

    def test_event_type_count(self) -> None:
        """Total event types should be 94 (88 previous + 6 Phase 48 federation approval)."""
        assert len(PolicyChangeEventType) == 94


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------


class TestConfigModels:
    """Tests for the 3 new config models and PolicyReleaseConfig fields."""

    def test_notification_config_defaults(self) -> None:
        """NotificationConfig defaults to disabled with no store and empty rules."""
        cfg = NotificationConfig()
        assert cfg.enabled is False
        assert cfg.store is None
        assert cfg.rules == []

    def test_expiration_config_defaults(self) -> None:
        """ExpirationConfig defaults to disabled with 300s interval."""
        cfg = ExpirationConfig()
        assert cfg.enabled is False
        assert cfg.sweep_interval_seconds == 300

    def test_policy_release_config_has_new_fields(self) -> None:
        """PolicyReleaseConfig has notifications and expiration fields."""
        cfg = PolicyReleaseConfig()
        assert cfg.notifications is None
        assert cfg.expiration is None

    def test_notification_rule_config(self) -> None:
        """NotificationRuleConfig stores rule definition fields."""
        rule = NotificationRuleConfig(
            name="alert-on-rollback",
            event_types=["policy.activation.rolled_back"],
            severity="warning",
            channels=["log"],
        )
        assert rule.name == "alert-on-rollback"
        assert rule.event_types == ["policy.activation.rolled_back"]
        assert rule.severity == "warning"
        assert rule.channels == ["log"]
        assert rule.title_template is None
        assert rule.body_template is None

    def test_notification_config_with_store(self) -> None:
        """NotificationConfig can be constructed with a store config."""
        store_cfg = PolicyReleaseStoreConfig(type="sqlite", path="/tmp/notifications.db")
        cfg = NotificationConfig(enabled=True, store=store_cfg)
        assert cfg.enabled is True
        assert cfg.store is not None
        assert cfg.store.type == "sqlite"

    def test_expiration_config_custom_interval(self) -> None:
        """ExpirationConfig accepts custom sweep interval."""
        cfg = ExpirationConfig(enabled=True, sweep_interval_seconds=60)
        assert cfg.enabled is True
        assert cfg.sweep_interval_seconds == 60

    def test_policy_release_config_with_notifications(self) -> None:
        """PolicyReleaseConfig accepts notification and expiration sub-configs."""
        cfg = PolicyReleaseConfig(
            notifications=NotificationConfig(enabled=True),
            expiration=ExpirationConfig(enabled=True, sweep_interval_seconds=120),
        )
        assert cfg.notifications is not None
        assert cfg.notifications.enabled is True
        assert cfg.expiration is not None
        assert cfg.expiration.enabled is True
        assert cfg.expiration.sweep_interval_seconds == 120


# ---------------------------------------------------------------------------
# AgentApp properties
# ---------------------------------------------------------------------------


class TestAgentAppProperties:
    """Tests for the 3 new Phase 44 AgentApp properties."""

    def test_notification_service_property(self) -> None:
        """notification_service defaults to None."""
        app = AgentApp()
        assert app.notification_service is None

    def test_expiration_service_property(self) -> None:
        """expiration_service defaults to None."""
        app = AgentApp()
        assert app.expiration_service is None

    def test_expiration_worker_property(self) -> None:
        """expiration_worker defaults to None."""
        app = AgentApp()
        assert app.expiration_worker is None

    def test_set_notification_service(self) -> None:
        """notification_service can be set and retrieved."""
        app = AgentApp()
        sentinel = object()
        app.notification_service = sentinel
        assert app.notification_service is sentinel

    def test_set_expiration_service(self) -> None:
        """expiration_service can be set and retrieved."""
        app = AgentApp()
        sentinel = object()
        app.expiration_service = sentinel
        assert app.expiration_service is sentinel

    def test_set_expiration_worker(self) -> None:
        """expiration_worker can be set and retrieved."""
        app = AgentApp()
        sentinel = object()
        app.expiration_worker = sentinel
        assert app.expiration_worker is sentinel
