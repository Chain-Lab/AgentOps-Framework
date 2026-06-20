"""Tests for Phase 48 Task 4: Config Schema, Loader Wiring, RBAC, Change Events, AgentApp Properties."""

import pytest

from agent_app.config.schema import (
    RolloutFederationApprovalConfig,
    RolloutFederationConfig,
)
from agent_app.governance.policy_rbac import PolicyReleasePermission
from agent_app.governance.policy_change_event import PolicyChangeEventType
from agent_app.governance.policy_rollout_federation_history import FederationHistoryEventType
from agent_app.core.app import AgentApp


# ---------------------------------------------------------------------------
# 1. RolloutFederationApprovalConfig defaults
# ---------------------------------------------------------------------------

class TestRolloutFederationApprovalConfigDefaults:
    """Verify default values for RolloutFederationApprovalConfig."""

    def test_enabled_defaults_to_false(self):
        cfg = RolloutFederationApprovalConfig()
        assert cfg.enabled is False

    def test_type_defaults_to_memory(self):
        cfg = RolloutFederationApprovalConfig()
        assert cfg.type == "memory"

    def test_path_defaults(self):
        cfg = RolloutFederationApprovalConfig()
        assert cfg.path == ".agent_app/federation_approvals.db"

    def test_require_approval_for_defaults(self):
        cfg = RolloutFederationApprovalConfig()
        assert cfg.require_approval_for == [
            "federation.plan.start",
            "federation.plan.run_all",
            "federation.override_conflicts",
        ]

    def test_default_required_approvers_defaults(self):
        cfg = RolloutFederationApprovalConfig()
        assert cfg.default_required_approvers == ["release_manager", "policy_admin"]

    def test_delegation_enabled_defaults_to_false(self):
        cfg = RolloutFederationApprovalConfig()
        assert cfg.delegation_enabled is False

    def test_escalation_enabled_defaults_to_false(self):
        cfg = RolloutFederationApprovalConfig()
        assert cfg.escalation_enabled is False

    def test_escalation_after_minutes_defaults_to_60(self):
        cfg = RolloutFederationApprovalConfig()
        assert cfg.escalation_after_minutes == 60

    def test_escalate_to_defaults_to_empty_list(self):
        cfg = RolloutFederationApprovalConfig()
        assert cfg.escalate_to == []

    def test_custom_values(self):
        cfg = RolloutFederationApprovalConfig(
            enabled=True,
            type="sqlite",
            path="/data/approvals.db",
            require_approval_for=["custom.action"],
            default_required_approvers=["admin"],
            delegation_enabled=True,
            escalation_enabled=True,
            escalation_after_minutes=30,
            escalate_to=["senior_admin"],
        )
        assert cfg.enabled is True
        assert cfg.type == "sqlite"
        assert cfg.path == "/data/approvals.db"
        assert cfg.require_approval_for == ["custom.action"]
        assert cfg.default_required_approvers == ["admin"]
        assert cfg.delegation_enabled is True
        assert cfg.escalation_enabled is True
        assert cfg.escalation_after_minutes == 30
        assert cfg.escalate_to == ["senior_admin"]


# ---------------------------------------------------------------------------
# 2. Missing config preserves behavior (approvals disabled by default)
# ---------------------------------------------------------------------------

class TestMissingConfigPreservesBehavior:
    """Verify that missing/None approval config does not break anything."""

    def test_federation_config_has_approvals_field(self):
        cfg = RolloutFederationConfig()
        assert hasattr(cfg, "approvals")
        assert isinstance(cfg.approvals, RolloutFederationApprovalConfig)

    def test_approvals_disabled_by_default(self):
        cfg = RolloutFederationConfig()
        assert cfg.approvals.enabled is False

    def test_federation_config_with_explicit_approvals(self):
        approval_cfg = RolloutFederationApprovalConfig(enabled=True, type="sqlite")
        cfg = RolloutFederationConfig(enabled=True, approvals=approval_cfg)
        assert cfg.approvals.enabled is True
        assert cfg.approvals.type == "sqlite"


# ---------------------------------------------------------------------------
# 3 & 4. Memory and SQLite approval config wiring
# ---------------------------------------------------------------------------

class TestApprovalConfigWiring:
    """Verify that approval config correctly wires store and service."""

    def test_memory_approval_config_creates_store(self):
        """Memory type config should create an InMemory store."""
        from agent_app.runtime.policy_rollout_federation_approval_store import (
            create_federation_approval_store,
            InMemoryFederationApprovalStore,
        )
        store = create_federation_approval_store(type="memory")
        assert isinstance(store, InMemoryFederationApprovalStore)

    def test_sqlite_approval_config_creates_store(self):
        """SQLite type config should create an SQLite store."""
        from agent_app.runtime.policy_rollout_federation_approval_store import (
            create_federation_approval_store,
            SQLiteFederationApprovalStore,
        )
        store = create_federation_approval_store(type="sqlite", path=":memory:")
        assert isinstance(store, SQLiteFederationApprovalStore)

    def test_approval_policy_creation(self):
        """FederationApprovalPolicy can be created from config values."""
        from agent_app.governance.policy_rollout_federation_approval import FederationApprovalPolicy
        cfg = RolloutFederationApprovalConfig(
            enabled=True,
            require_approval_for=["federation.plan.start"],
            default_required_approvers=["release_manager"],
            delegation_enabled=True,
            escalation_enabled=True,
            escalation_after_minutes=30,
            escalate_to=["senior_admin"],
        )
        policy = FederationApprovalPolicy(
            enabled=cfg.enabled,
            require_approval_for=cfg.require_approval_for,
            default_required_approvers=cfg.default_required_approvers,
            delegation_enabled=cfg.delegation_enabled,
            escalation_enabled=cfg.escalation_enabled,
            escalation_after_minutes=cfg.escalation_after_minutes,
            escalate_to=cfg.escalate_to,
        )
        assert policy.enabled is True
        assert policy.require_approval_for == ["federation.plan.start"]
        assert policy.default_required_approvers == ["release_manager"]
        assert policy.delegation_enabled is True
        assert policy.escalation_enabled is True
        assert policy.escalation_after_minutes == 30
        assert policy.escalate_to == ["senior_admin"]


# ---------------------------------------------------------------------------
# 5. RBAC permissions exist in enum
# ---------------------------------------------------------------------------

class TestRBACPermissions:
    """Verify federation approval RBAC permissions exist."""

    def test_federation_approval_list_exists(self):
        assert PolicyReleasePermission.FEDERATION_APPROVAL_LIST.value == "policy.federation.approval.list"

    def test_federation_approval_approve_exists(self):
        assert PolicyReleasePermission.FEDERATION_APPROVAL_APPROVE.value == "policy.federation.approval.approve"

    def test_federation_approval_reject_exists(self):
        assert PolicyReleasePermission.FEDERATION_APPROVAL_REJECT.value == "policy.federation.approval.reject"

    def test_federation_approval_escalate_exists(self):
        assert PolicyReleasePermission.FEDERATION_APPROVAL_ESCALATE.value == "policy.federation.approval.escalate"

    def test_approval_list_is_default_allowed(self):
        """FEDERATION_APPROVAL_LIST should be in the default-allowed set."""
        from agent_app.governance.policy_rbac import _DEFAULT_ALLOWED
        assert PolicyReleasePermission.FEDERATION_APPROVAL_LIST in _DEFAULT_ALLOWED

    def test_approval_approve_is_not_default_allowed(self):
        """FEDERATION_APPROVAL_APPROVE should require explicit permission."""
        from agent_app.governance.policy_rbac import _DEFAULT_ALLOWED
        assert PolicyReleasePermission.FEDERATION_APPROVAL_APPROVE not in _DEFAULT_ALLOWED

    def test_approval_reject_is_not_default_allowed(self):
        """FEDERATION_APPROVAL_REJECT should require explicit permission."""
        from agent_app.governance.policy_rbac import _DEFAULT_ALLOWED
        assert PolicyReleasePermission.FEDERATION_APPROVAL_REJECT not in _DEFAULT_ALLOWED

    def test_approval_escalate_is_not_default_allowed(self):
        """FEDERATION_APPROVAL_ESCALATE should require explicit permission."""
        from agent_app.governance.policy_rbac import _DEFAULT_ALLOWED
        assert PolicyReleasePermission.FEDERATION_APPROVAL_ESCALATE not in _DEFAULT_ALLOWED


# ---------------------------------------------------------------------------
# 6. Change event types exist in enum
# ---------------------------------------------------------------------------

class TestChangeEventType:
    """Verify federation approval change event types exist."""

    def test_federation_approval_created(self):
        assert PolicyChangeEventType.FEDERATION_APPROVAL_CREATED.value == "policy.federation.approval.created"

    def test_federation_approval_approved(self):
        assert PolicyChangeEventType.FEDERATION_APPROVAL_APPROVED.value == "policy.federation.approval.approved"

    def test_federation_approval_rejected(self):
        assert PolicyChangeEventType.FEDERATION_APPROVAL_REJECTED.value == "policy.federation.approval.rejected"

    def test_federation_approval_escalated(self):
        assert PolicyChangeEventType.FEDERATION_APPROVAL_ESCALATED.value == "policy.federation.approval.escalated"

    def test_federation_approval_cancelled(self):
        assert PolicyChangeEventType.FEDERATION_APPROVAL_CANCELLED.value == "policy.federation.approval.cancelled"

    def test_federation_approval_permission_denied(self):
        assert PolicyChangeEventType.FEDERATION_APPROVAL_PERMISSION_DENIED.value == "policy.federation.approval.permission_denied"


# ---------------------------------------------------------------------------
# 7. FederationHistoryEventType has approval events
# ---------------------------------------------------------------------------

class TestFederationHistoryEventType:
    """Verify FederationHistoryEventType has approval events."""

    def test_approval_created(self):
        assert FederationHistoryEventType.APPROVAL_CREATED.value == "approval.created"

    def test_approval_approved(self):
        assert FederationHistoryEventType.APPROVAL_APPROVED.value == "approval.approved"

    def test_approval_rejected(self):
        assert FederationHistoryEventType.APPROVAL_REJECTED.value == "approval.rejected"

    def test_approval_escalated(self):
        assert FederationHistoryEventType.APPROVAL_ESCALATED.value == "approval.escalated"

    def test_approval_cancelled(self):
        assert FederationHistoryEventType.APPROVAL_CANCELLED.value == "approval.cancelled"


# ---------------------------------------------------------------------------
# 8. AgentApp properties work
# ---------------------------------------------------------------------------

class TestAgentAppProperties:
    """Verify AgentApp properties for federation approval work correctly."""

    def test_federation_approval_store_default_is_none(self):
        app = AgentApp()
        assert app.federation_approval_store is None

    def test_federation_approval_policy_default_is_none(self):
        app = AgentApp()
        assert app.federation_approval_policy is None

    def test_federation_approval_service_default_is_none(self):
        app = AgentApp()
        assert app.federation_approval_service is None

    def test_federation_approval_store_setter(self):
        app = AgentApp()
        mock_store = object()
        app.federation_approval_store = mock_store
        assert app.federation_approval_store is mock_store

    def test_federation_approval_policy_setter(self):
        app = AgentApp()
        mock_policy = object()
        app.federation_approval_policy = mock_policy
        assert app.federation_approval_policy is mock_policy

    def test_federation_approval_service_setter(self):
        app = AgentApp()
        mock_service = object()
        app.federation_approval_service = mock_service
        assert app.federation_approval_service is mock_service

    def test_federation_approval_store_overwrite(self):
        app = AgentApp()
        store1 = object()
        store2 = object()
        app.federation_approval_store = store1
        assert app.federation_approval_store is store1
        app.federation_approval_store = store2
        assert app.federation_approval_store is store2

    def test_federation_approval_service_overwrite(self):
        app = AgentApp()
        service1 = object()
        service2 = object()
        app.federation_approval_service = service1
        assert app.federation_approval_service is service1
        app.federation_approval_service = service2
        assert app.federation_approval_service is service2
