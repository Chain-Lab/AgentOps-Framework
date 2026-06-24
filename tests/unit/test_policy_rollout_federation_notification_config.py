"""Phase 49 Task 7: Config schema, loader, RBAC, change events, federation history events, AgentApp properties."""

from agent_app.config.schema import (
    RolloutFederationConfig,
    RolloutFederationNotificationConfig,
    RolloutFederationWorkerConfig,
)
from agent_app.core.app import AgentApp
from agent_app.governance.policy_change_event import PolicyChangeEventType
from agent_app.governance.policy_rbac import PolicyReleasePermission, _DEFAULT_ALLOWED
from agent_app.governance.policy_rollout_federation_history import FederationHistoryEventType


# ---------------------------------------------------------------------------
# RolloutFederationNotificationConfig defaults
# ---------------------------------------------------------------------------


class TestRolloutFederationNotificationConfig:
    """Tests for RolloutFederationNotificationConfig defaults."""

    def test_default_enabled(self):
        cfg = RolloutFederationNotificationConfig()
        assert cfg.enabled is False

    def test_default_type(self):
        cfg = RolloutFederationNotificationConfig()
        assert cfg.type == "memory"

    def test_default_path(self):
        cfg = RolloutFederationNotificationConfig()
        assert cfg.path == ".agent_app/federation_notifications.db"

    def test_default_channels(self):
        cfg = RolloutFederationNotificationConfig()
        assert cfg.default_channels == ["console"]

    def test_default_channels_dict(self):
        cfg = RolloutFederationNotificationConfig()
        assert cfg.channels == {}

    def test_default_retry_max_attempts(self):
        cfg = RolloutFederationNotificationConfig()
        assert cfg.retry_max_attempts == 3

    def test_default_retry_backoff_seconds(self):
        cfg = RolloutFederationNotificationConfig()
        assert cfg.retry_backoff_seconds == 60


# ---------------------------------------------------------------------------
# RolloutFederationWorkerConfig defaults
# ---------------------------------------------------------------------------


class TestRolloutFederationWorkerConfig:
    """Tests for RolloutFederationWorkerConfig defaults."""

    def test_default_enabled(self):
        cfg = RolloutFederationWorkerConfig()
        assert cfg.enabled is False

    def test_default_lock_type(self):
        cfg = RolloutFederationWorkerConfig()
        assert cfg.lock_type == "memory"

    def test_default_lock_path(self):
        cfg = RolloutFederationWorkerConfig()
        assert cfg.lock_path == ".agent_app/federation_worker_locks.db"

    def test_default_lock_ttl_seconds(self):
        cfg = RolloutFederationWorkerConfig()
        assert cfg.lock_ttl_seconds == 300


# ---------------------------------------------------------------------------
# RolloutFederationConfig has notifications and worker fields
# ---------------------------------------------------------------------------


class TestRolloutFederationConfigPhase49:
    """Tests for RolloutFederationConfig Phase 49 fields."""

    def test_has_notifications_field(self):
        cfg = RolloutFederationConfig()
        assert hasattr(cfg, "notifications")
        assert isinstance(cfg.notifications, RolloutFederationNotificationConfig)

    def test_has_worker_field(self):
        cfg = RolloutFederationConfig()
        assert hasattr(cfg, "worker")
        assert isinstance(cfg.worker, RolloutFederationWorkerConfig)

    def test_notifications_disabled_by_default(self):
        cfg = RolloutFederationConfig()
        assert cfg.notifications.enabled is False

    def test_worker_disabled_by_default(self):
        cfg = RolloutFederationConfig()
        assert cfg.worker.enabled is False


# ---------------------------------------------------------------------------
# PolicyReleasePermission new Phase 49 permissions
# ---------------------------------------------------------------------------


class TestPolicyReleasePermissionPhase49:
    """Tests for new Phase 49 RBAC permissions."""

    def test_federation_notification_list_exists(self):
        assert hasattr(PolicyReleasePermission, "FEDERATION_NOTIFICATION_LIST")
        assert PolicyReleasePermission.FEDERATION_NOTIFICATION_LIST == "policy.federation.notification.list"

    def test_federation_notification_dispatch_exists(self):
        assert hasattr(PolicyReleasePermission, "FEDERATION_NOTIFICATION_DISPATCH")
        assert PolicyReleasePermission.FEDERATION_NOTIFICATION_DISPATCH == "policy.federation.notification.dispatch"

    def test_federation_escalation_run_exists(self):
        assert hasattr(PolicyReleasePermission, "FEDERATION_ESCALATION_RUN")
        assert PolicyReleasePermission.FEDERATION_ESCALATION_RUN == "policy.federation.escalation.run"

    def test_federation_notification_list_in_default_allowed(self):
        assert PolicyReleasePermission.FEDERATION_NOTIFICATION_LIST in _DEFAULT_ALLOWED


# ---------------------------------------------------------------------------
# PolicyChangeEventType new Phase 49 events
# ---------------------------------------------------------------------------


class TestPolicyChangeEventTypePhase49:
    """Tests for new Phase 49 change event types."""

    def test_federation_notification_created(self):
        assert hasattr(PolicyChangeEventType, "FEDERATION_NOTIFICATION_CREATED")
        assert PolicyChangeEventType.FEDERATION_NOTIFICATION_CREATED == "policy.federation.notification.created"

    def test_federation_notification_sent(self):
        assert hasattr(PolicyChangeEventType, "FEDERATION_NOTIFICATION_SENT")
        assert PolicyChangeEventType.FEDERATION_NOTIFICATION_SENT == "policy.federation.notification.sent"

    def test_federation_notification_failed(self):
        assert hasattr(PolicyChangeEventType, "FEDERATION_NOTIFICATION_FAILED")
        assert PolicyChangeEventType.FEDERATION_NOTIFICATION_FAILED == "policy.federation.notification.failed"

    def test_escalation_worker_ticked(self):
        assert hasattr(PolicyChangeEventType, "FEDERATION_APPROVAL_ESCALATION_WORKER_TICKED")
        assert PolicyChangeEventType.FEDERATION_APPROVAL_ESCALATION_WORKER_TICKED == "policy.federation.approval.escalation_worker_ticked"

    def test_escalation_due(self):
        assert hasattr(PolicyChangeEventType, "FEDERATION_APPROVAL_ESCALATION_DUE")
        assert PolicyChangeEventType.FEDERATION_APPROVAL_ESCALATION_DUE == "policy.federation.approval.escalation_due"

    def test_escalation_lock_skipped(self):
        assert hasattr(PolicyChangeEventType, "FEDERATION_APPROVAL_ESCALATION_LOCK_SKIPPED")
        assert PolicyChangeEventType.FEDERATION_APPROVAL_ESCALATION_LOCK_SKIPPED == "policy.federation.approval.escalation_lock_skipped"

    def test_event_type_count(self):
        """94 original + 6 Phase 49 + 6 Phase 50 + 12 Phase 51 + 6 Phase 52 + 9 Phase 53 = 133."""
        assert len(PolicyChangeEventType) == 150

    def test_phase53_alert_delivery_event_types_exist(self) -> None:
        """Phase 53 alert delivery PolicyChangeEventType members."""
        assert PolicyChangeEventType.FEDERATION_NOTIFICATION_ALERT_DELIVERY_TARGET_CREATED == \
            "policy.federation.notification.alert_delivery.target_created"
        assert PolicyChangeEventType.FEDERATION_NOTIFICATION_ALERT_DELIVERY_TARGET_UPDATED == \
            "policy.federation.notification.alert_delivery.target_updated"
        assert PolicyChangeEventType.FEDERATION_NOTIFICATION_ALERT_DELIVERY_TARGET_DISABLED == \
            "policy.federation.notification.alert_delivery.target_disabled"
        assert PolicyChangeEventType.FEDERATION_NOTIFICATION_ALERT_DELIVERY_ATTEMPT_RECORDED == \
            "policy.federation.notification.alert_delivery.attempt_recorded"
        assert PolicyChangeEventType.FEDERATION_NOTIFICATION_ALERT_DELIVERY_DLQ_CREATED == \
            "policy.federation.notification.alert_delivery.dlq_created"

    def test_phase53_export_retention_rollup_event_types_exist(self) -> None:
        """Phase 53 prometheus/jsonl/retention/rollup PolicyChangeEventType members."""
        assert PolicyChangeEventType.FEDERATION_NOTIFICATION_PROMETHEUS_EXPORTED == \
            "policy.federation.notification.prometheus.exported"
        assert PolicyChangeEventType.FEDERATION_NOTIFICATION_JSONL_EXPORTED == \
            "policy.federation.notification.jsonl.exported"
        assert PolicyChangeEventType.FEDERATION_NOTIFICATION_RETENTION_CLEANUP_RAN == \
            "policy.federation.notification.retention.cleanup_ran"
        assert PolicyChangeEventType.FEDERATION_NOTIFICATION_ROLLUP_BUILT == \
            "policy.federation.notification.rollup.built"


# ---------------------------------------------------------------------------
# FederationHistoryEventType new Phase 49 events
# ---------------------------------------------------------------------------


class TestFederationHistoryEventTypePhase49:
    """Tests for new Phase 49 federation history event types."""

    def test_notification_created_exists(self):
        """NOTIFICATION_CREATED was added in Phase 47, Phase 49 reuses it."""
        assert hasattr(FederationHistoryEventType, "NOTIFICATION_CREATED")
        assert FederationHistoryEventType.NOTIFICATION_CREATED == "federation.notification.created"

    def test_notification_sent_exists(self):
        """NOTIFICATION_SENT was added in Phase 47, Phase 49 reuses it."""
        assert hasattr(FederationHistoryEventType, "NOTIFICATION_SENT")
        assert FederationHistoryEventType.NOTIFICATION_SENT == "federation.notification.sent"

    def test_notification_failed_exists(self):
        """NOTIFICATION_FAILED was added in Phase 47, Phase 49 reuses it."""
        assert hasattr(FederationHistoryEventType, "NOTIFICATION_FAILED")
        assert FederationHistoryEventType.NOTIFICATION_FAILED == "federation.notification.failed"

    def test_escalation_worker_ticked(self):
        assert hasattr(FederationHistoryEventType, "ESCALATION_WORKER_TICKED")
        assert FederationHistoryEventType.ESCALATION_WORKER_TICKED == "federation.escalation.worker_ticked"

    def test_escalation_lock_skipped(self):
        assert hasattr(FederationHistoryEventType, "ESCALATION_LOCK_SKIPPED")
        assert FederationHistoryEventType.ESCALATION_LOCK_SKIPPED == "federation.escalation.lock_skipped"

    def test_event_type_count(self):
        """28 original + 2 Phase 49 + 3 Phase 50 + 3 Phase 51 + 6 Phase 52 + 9 Phase 53 = 51."""
        assert len(FederationHistoryEventType) == 51

    def test_phase53_federation_history_event_types_exist(self) -> None:
        """Phase 53 alert delivery/export/retention/rollup FederationHistoryEventType members."""
        assert FederationHistoryEventType.NOTIFICATION_ALERT_DELIVERY_TARGET_CREATED == \
            "notification.alert_delivery.target_created"
        assert FederationHistoryEventType.NOTIFICATION_ALERT_DELIVERY_TARGET_UPDATED == \
            "notification.alert_delivery.target_updated"
        assert FederationHistoryEventType.NOTIFICATION_ALERT_DELIVERY_TARGET_DISABLED == \
            "notification.alert_delivery.target_disabled"
        assert FederationHistoryEventType.NOTIFICATION_ALERT_DELIVERY_ATTEMPT_RECORDED == \
            "notification.alert_delivery.attempt_recorded"
        assert FederationHistoryEventType.NOTIFICATION_ALERT_DELIVERY_DLQ_CREATED == \
            "notification.alert_delivery.dlq_created"
        assert FederationHistoryEventType.NOTIFICATION_PROMETHEUS_METRICS_EXPORTED == \
            "notification.prometheus.metrics_exported"
        assert FederationHistoryEventType.NOTIFICATION_JSONL_EXPORTED == \
            "notification.jsonl.exported"
        assert FederationHistoryEventType.NOTIFICATION_RETENTION_CLEANUP_RAN == \
            "notification.retention.cleanup_ran"
        assert FederationHistoryEventType.NOTIFICATION_ROLLUP_BUILT == \
            "notification.rollup.built"


# ---------------------------------------------------------------------------
# AgentApp Phase 49 properties
# ---------------------------------------------------------------------------


class TestAgentAppPhase49Properties:
    """Tests for new Phase 49 AgentApp properties."""

    def test_federation_notification_store_default_none(self):
        app = AgentApp()
        assert app.federation_notification_store is None

    def test_federation_notification_service_default_none(self):
        app = AgentApp()
        assert app.federation_notification_service is None

    def test_federation_escalation_worker_default_none(self):
        app = AgentApp()
        assert app.federation_escalation_worker is None

    def test_distributed_lock_default_none(self):
        app = AgentApp()
        assert app.distributed_lock is None
