"""Tests for Phase 44 notification/expiration config, RBAC, events, and AgentApp properties.
Phase 52: Federation notification observability, SLA, and alert config tests."""

from __future__ import annotations

import textwrap

import pytest

from agent_app.config.loader import build_app, load_config
from agent_app.config.schema import (
    ExpirationConfig,
    NotificationConfig,
    NotificationRuleConfig,
    PolicyReleaseConfig,
    PolicyReleaseStoreConfig,
    RolloutFederationNotificationAlertConfig,
    RolloutFederationNotificationAlertRuleConfig,
    RolloutFederationNotificationConfig,
    RolloutFederationNotificationObservabilityConfig,
    RolloutFederationNotificationSlaChannelOverrideConfig,
    RolloutFederationNotificationSlaConfig,
    RolloutFederationStoreConfig,
)
from agent_app.core.app import AgentApp
from agent_app.governance.policy_change_event import PolicyChangeEventType
from agent_app.governance.policy_rbac import PolicyReleasePermission, _DEFAULT_ALLOWED


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
        """Total event types should be 118 (106 previous + 12 Phase 51)."""
        assert len(PolicyChangeEventType) == 118


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


# ---------------------------------------------------------------------------
# Phase 52: Store config
# ---------------------------------------------------------------------------


class TestRolloutFederationStoreConfig:
    def test_default_memory_store(self) -> None:
        cfg = RolloutFederationStoreConfig()
        assert cfg.type == "memory"
        assert cfg.path is None

    def test_sqlite_store(self) -> None:
        cfg = RolloutFederationStoreConfig(type="sqlite", path=".agent_app/test.db")
        assert cfg.type == "sqlite"
        assert cfg.path == ".agent_app/test.db"


# ---------------------------------------------------------------------------
# Phase 52: Observability config
# ---------------------------------------------------------------------------


class TestRolloutFederationNotificationObservabilityConfig:
    def test_defaults(self) -> None:
        cfg = RolloutFederationNotificationObservabilityConfig()
        assert cfg.enabled is True
        assert cfg.store.type == "sqlite"
        assert cfg.store.path == ".agent_app/federation_notification_observability.db"

    def test_custom_values(self) -> None:
        cfg = RolloutFederationNotificationObservabilityConfig(
            enabled=False,
            store=RolloutFederationStoreConfig(type="memory"),
        )
        assert cfg.enabled is False
        assert cfg.store.type == "memory"


# ---------------------------------------------------------------------------
# Phase 52: SLA channel override config
# ---------------------------------------------------------------------------


class TestRolloutFederationNotificationSlaChannelOverrideConfig:
    def test_all_none_defaults(self) -> None:
        cfg = RolloutFederationNotificationSlaChannelOverrideConfig()
        assert cfg.max_delivery_latency_ms is None
        assert cfg.min_success_rate is None
        assert cfg.max_failure_rate is None
        assert cfg.max_dlq_rate is None
        assert cfg.window_minutes is None

    def test_partial_override(self) -> None:
        cfg = RolloutFederationNotificationSlaChannelOverrideConfig(
            max_delivery_latency_ms=5000,
            min_success_rate=0.99,
        )
        assert cfg.max_delivery_latency_ms == 5000
        assert cfg.min_success_rate == 0.99
        assert cfg.max_failure_rate is None


# ---------------------------------------------------------------------------
# Phase 52: SLA config
# ---------------------------------------------------------------------------


class TestRolloutFederationNotificationSlaConfig:
    def test_defaults(self) -> None:
        cfg = RolloutFederationNotificationSlaConfig()
        assert cfg.enabled is True
        assert cfg.max_delivery_latency_ms == 30000
        assert cfg.min_success_rate == 0.95
        assert cfg.max_failure_rate == 0.05
        assert cfg.max_dlq_rate == 0.01
        assert cfg.window_minutes == 60
        assert cfg.channels == {}

    def test_with_channel_overrides(self) -> None:
        cfg = RolloutFederationNotificationSlaConfig(
            channels={
                "webhook": RolloutFederationNotificationSlaChannelOverrideConfig(
                    max_delivery_latency_ms=5000,
                ),
            }
        )
        assert cfg.channels["webhook"].max_delivery_latency_ms == 5000


# ---------------------------------------------------------------------------
# Phase 52: Alert rule config
# ---------------------------------------------------------------------------


class TestRolloutFederationNotificationAlertRuleConfig:
    def test_minimal_rule(self) -> None:
        cfg = RolloutFederationNotificationAlertRuleConfig(
            rule_id="rule-1",
            name="High failure rate",
            metric="failure_rate",
            operator=">",
            threshold=0.1,
        )
        assert cfg.rule_id == "rule-1"
        assert cfg.name == "High failure rate"
        assert cfg.metric == "failure_rate"
        assert cfg.operator == ">"
        assert cfg.threshold == 0.1
        assert cfg.enabled is True
        assert cfg.severity == "warning"
        assert cfg.channel is None
        assert cfg.federation_id is None
        assert cfg.window_minutes == 60
        assert cfg.cooldown_minutes == 30

    def test_full_rule(self) -> None:
        cfg = RolloutFederationNotificationAlertRuleConfig(
            rule_id="rule-2",
            name="Critical SLA breach",
            enabled=True,
            metric="success_rate",
            operator="<",
            threshold=0.9,
            severity="critical",
            channel="webhook",
            federation_id="fed-1",
            window_minutes=30,
            cooldown_minutes=15,
        )
        assert cfg.severity == "critical"
        assert cfg.channel == "webhook"
        assert cfg.federation_id == "fed-1"
        assert cfg.window_minutes == 30
        assert cfg.cooldown_minutes == 15


# ---------------------------------------------------------------------------
# Phase 52: Alert config
# ---------------------------------------------------------------------------


class TestRolloutFederationNotificationAlertConfig:
    def test_defaults(self) -> None:
        cfg = RolloutFederationNotificationAlertConfig()
        assert cfg.enabled is True
        assert cfg.store.type == "sqlite"
        assert cfg.store.path == ".agent_app/federation_notification_alerts.db"
        assert cfg.rules == []

    def test_with_rules(self) -> None:
        cfg = RolloutFederationNotificationAlertConfig(
            rules=[
                RolloutFederationNotificationAlertRuleConfig(
                    rule_id="r1",
                    name="Test rule",
                    metric="failure_rate",
                    operator=">",
                    threshold=0.1,
                ),
            ]
        )
        assert len(cfg.rules) == 1
        assert cfg.rules[0].rule_id == "r1"


# ---------------------------------------------------------------------------
# Phase 52: RolloutFederationNotificationConfig integration
# ---------------------------------------------------------------------------


class TestRolloutFederationNotificationConfigIntegration:
    def test_defaults_include_new_sections(self) -> None:
        cfg = RolloutFederationNotificationConfig()
        assert cfg.observability.enabled is True
        assert cfg.sla.enabled is True
        assert cfg.alerts.enabled is True

    def test_new_sections_roundtrip_via_model_dump(self) -> None:
        cfg = RolloutFederationNotificationConfig()
        data = cfg.model_dump()
        assert "observability" in data
        assert "sla" in data
        assert "alerts" in data

    def test_custom_new_sections(self) -> None:
        cfg = RolloutFederationNotificationConfig(
            observability=RolloutFederationNotificationObservabilityConfig(enabled=False),
            sla=RolloutFederationNotificationSlaConfig(
                enabled=False, max_delivery_latency_ms=5000
            ),
            alerts=RolloutFederationNotificationAlertConfig(
                enabled=False,
                rules=[
                    RolloutFederationNotificationAlertRuleConfig(
                        rule_id="custom-rule",
                        name="Custom",
                        metric="failure_rate",
                        operator=">=",
                        threshold=0.2,
                    ),
                ],
            ),
        )
        assert cfg.observability.enabled is False
        assert cfg.sla.enabled is False
        assert cfg.sla.max_delivery_latency_ms == 5000
        assert cfg.alerts.enabled is False
        assert len(cfg.alerts.rules) == 1
        assert cfg.alerts.rules[0].rule_id == "custom-rule"


# ---------------------------------------------------------------------------
# Phase 52: YAML loading
# ---------------------------------------------------------------------------


class TestPhase52YAMLLoading:
    def test_load_config_with_new_sections(self, tmp_path) -> None:
        config_path = tmp_path / "agentapp.yaml"
        config_path.write_text(
            textwrap.dedent(
                """\
                governance:
                  policy_release:
                    bundles:
                      type: memory
                    gates:
                      type: memory
                    rollout_federation:
                      enabled: true
                      notifications:
                        enabled: true
                        observability:
                          enabled: true
                          store:
                            type: sqlite
                            path: .agent_app/federation_notification_observability.db
                        sla:
                          enabled: true
                          max_delivery_latency_ms: 15000
                          min_success_rate: 0.98
                          channels:
                            webhook:
                              max_delivery_latency_ms: 3000
                        alerts:
                          enabled: true
                          rules:
                            - rule_id: alert-1
                              name: High failure rate
                              metric: failure_rate
                              operator: ">"
                              threshold: 0.1
                              severity: critical
                """
            )
        )
        cfg = load_config(config_path)
        notif_cfg = cfg.governance.policy_release.rollout_federation.notifications
        assert notif_cfg.enabled is True
        assert notif_cfg.observability.enabled is True
        assert notif_cfg.observability.store.type == "sqlite"
        assert notif_cfg.sla.enabled is True
        assert notif_cfg.sla.max_delivery_latency_ms == 15000
        assert notif_cfg.sla.min_success_rate == 0.98
        assert "webhook" in notif_cfg.sla.channels
        assert notif_cfg.sla.channels["webhook"].max_delivery_latency_ms == 3000
        assert notif_cfg.alerts.enabled is True
        assert len(notif_cfg.alerts.rules) == 1
        assert notif_cfg.alerts.rules[0].rule_id == "alert-1"
        assert notif_cfg.alerts.rules[0].severity == "critical"

    def test_load_config_without_new_sections_uses_defaults(self, tmp_path) -> None:
        config_path = tmp_path / "agentapp.yaml"
        config_path.write_text(
            textwrap.dedent(
                """\
                governance:
                  policy_release:
                    bundles:
                      type: memory
                    gates:
                      type: memory
                    rollout_federation:
                      enabled: true
                      notifications:
                        enabled: true
                        type: memory
                """
            )
        )
        cfg = load_config(config_path)
        notif_cfg = cfg.governance.policy_release.rollout_federation.notifications
        # New sections should have defaults
        assert notif_cfg.observability.enabled is True
        assert notif_cfg.sla.enabled is True
        assert notif_cfg.alerts.enabled is True


# ---------------------------------------------------------------------------
# Phase 52: Loader wiring
# ---------------------------------------------------------------------------


class TestPhase52LoaderWiring:
    def test_new_sections_attached_to_app(self, tmp_path) -> None:
        config_path = tmp_path / "agentapp.yaml"
        config_path.write_text(
            textwrap.dedent(
                """\
                governance:
                  policy_release:
                    bundles:
                      type: memory
                    gates:
                      type: memory
                    rollout_federation:
                      enabled: true
                      notifications:
                        enabled: true
                """
            )
        )
        app = build_app(config_path)
        assert hasattr(app, "_federation_notification_observability_config")
        assert hasattr(app, "_federation_notification_sla_config")
        assert hasattr(app, "_federation_notification_alert_config")
        assert app._federation_notification_observability_config.enabled is True
        assert app._federation_notification_sla_config.enabled is True
        assert app._federation_notification_alert_config.enabled is True

    def test_custom_sections_attached_to_app(self, tmp_path) -> None:
        config_path = tmp_path / "agentapp.yaml"
        config_path.write_text(
            textwrap.dedent(
                """\
                governance:
                  policy_release:
                    bundles:
                      type: memory
                    gates:
                      type: memory
                    rollout_federation:
                      enabled: true
                      notifications:
                        enabled: true
                        sla:
                          enabled: false
                          max_delivery_latency_ms: 5000
                        alerts:
                          enabled: false
                          rules:
                            - rule_id: r1
                              name: Test
                              metric: failure_rate
                              operator: ">"
                              threshold: 0.5
                """
            )
        )
        app = build_app(config_path)
        assert app._federation_notification_sla_config.enabled is False
        assert app._federation_notification_sla_config.max_delivery_latency_ms == 5000
        assert app._federation_notification_alert_config.enabled is False
        assert len(app._federation_notification_alert_config.rules) == 1

    def test_missing_federation_does_not_crash(self, tmp_path) -> None:
        config_path = tmp_path / "agentapp.yaml"
        config_path.write_text(
            textwrap.dedent(
                """\
                governance:
                  policy_release:
                    bundles:
                      type: memory
                    gates:
                      type: memory
                """
            )
        )
        app = build_app(config_path)
        assert not hasattr(app, "_federation_notification_observability_config")

    def test_disabled_notifications_does_not_attach_configs(self, tmp_path) -> None:
        config_path = tmp_path / "agentapp.yaml"
        config_path.write_text(
            textwrap.dedent(
                """\
                governance:
                  policy_release:
                    bundles:
                      type: memory
                    gates:
                      type: memory
                    rollout_federation:
                      enabled: true
                      notifications:
                        enabled: false
                """
            )
        )
        app = build_app(config_path)
        assert not hasattr(app, "_federation_notification_observability_config")
