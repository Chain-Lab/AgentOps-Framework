"""Tests for Phase 43 Task 5 — rollout gate automation config, loader, RBAC, events."""

from __future__ import annotations

import pytest

from agent_app.config.schema import (
    RolloutGateAutomationConfig,
    SimulationGateRuleConfig,
    PolicyReleaseConfig,
)


# ---------------------------------------------------------------------------
# 1. RolloutGateAutomationConfig defaults
# ---------------------------------------------------------------------------


class TestRolloutGateAutomationConfigDefaults:
    """Test RolloutGateAutomationConfig default values."""

    def test_enabled_defaults_false(self) -> None:
        cfg = RolloutGateAutomationConfig()
        assert cfg.enabled is False

    def test_default_mode_is_manual(self) -> None:
        cfg = RolloutGateAutomationConfig()
        assert cfg.default_mode == "manual"

    def test_default_failure_action_is_block(self) -> None:
        cfg = RolloutGateAutomationConfig()
        assert cfg.default_failure_action == "block"

    def test_default_max_age_seconds_is_none(self) -> None:
        cfg = RolloutGateAutomationConfig()
        assert cfg.default_max_age_seconds is None

    def test_default_gate_rules_is_empty_list(self) -> None:
        cfg = RolloutGateAutomationConfig()
        assert cfg.default_gate_rules == []

    def test_explicit_values(self) -> None:
        rule = SimulationGateRuleConfig(name="r1", metric="simulation.changed_ratio", threshold=0.1)
        cfg = RolloutGateAutomationConfig(
            enabled=True,
            default_mode="auto",
            default_failure_action="skip",
            default_max_age_seconds=300,
            default_gate_rules=[rule],
        )
        assert cfg.enabled is True
        assert cfg.default_mode == "auto"
        assert cfg.default_failure_action == "skip"
        assert cfg.default_max_age_seconds == 300
        assert len(cfg.default_gate_rules) == 1
        assert cfg.default_gate_rules[0].name == "r1"


# ---------------------------------------------------------------------------
# 2. SimulationGateRuleConfig model
# ---------------------------------------------------------------------------


class TestSimulationGateRuleConfig:
    """Test SimulationGateRuleConfig model."""

    def test_required_name(self) -> None:
        rule = SimulationGateRuleConfig(name="my_rule")
        assert rule.name == "my_rule"

    def test_default_metric(self) -> None:
        rule = SimulationGateRuleConfig(name="r")
        assert rule.metric == "simulation.changed_ratio"

    def test_default_operator(self) -> None:
        rule = SimulationGateRuleConfig(name="r")
        assert rule.operator == "lte"

    def test_default_threshold(self) -> None:
        rule = SimulationGateRuleConfig(name="r")
        assert rule.threshold == 0.05

    def test_custom_threshold_int(self) -> None:
        rule = SimulationGateRuleConfig(name="r", metric="simulation.errors", threshold=5)
        assert rule.threshold == 5

    def test_custom_metric(self) -> None:
        rule = SimulationGateRuleConfig(name="r", metric="simulation.new_denies", threshold=0)
        assert rule.metric == "simulation.new_denies"


# ---------------------------------------------------------------------------
# 3. Config field on PolicyReleaseConfig
# ---------------------------------------------------------------------------


class TestPolicyReleaseConfigField:
    """Test rollout_gate_automation field on PolicyReleaseConfig."""

    def test_rollout_gate_automation_defaults_none(self) -> None:
        cfg = PolicyReleaseConfig()
        assert cfg.rollout_gate_automation is None

    def test_rollout_gate_automation_can_be_set(self) -> None:
        inner = RolloutGateAutomationConfig(enabled=True)
        cfg = PolicyReleaseConfig(rollout_gate_automation=inner)
        assert cfg.rollout_gate_automation is not None
        assert cfg.rollout_gate_automation.enabled is True

    def test_simulation_gate_enforcement_still_works(self) -> None:
        """Phase 42 config is not broken."""
        from agent_app.config.schema import SimulationGateEnforcementConfig
        inner = SimulationGateEnforcementConfig(require_for_promotion=True)
        cfg = PolicyReleaseConfig(simulation_gate_enforcement=inner)
        assert cfg.simulation_gate_enforcement is not None
        assert cfg.simulation_gate_enforcement.require_for_promotion is True


# ---------------------------------------------------------------------------
# 4. RBAC permissions
# ---------------------------------------------------------------------------


class TestRolloutGateRBACPermissions:
    """Test new RBAC permissions for rollout gate automation."""

    def test_rolout_gate_run_permission(self) -> None:
        from agent_app.governance.policy_rbac import PolicyReleasePermission
        assert PolicyReleasePermission.ROLLOUT_GATE_RUN == "policy.rollout.gate.run"

    def test_rolout_gate_attach_permission(self) -> None:
        from agent_app.governance.policy_rbac import PolicyReleasePermission
        assert PolicyReleasePermission.ROLLOUT_GATE_ATTACH == "policy.rollout.gate.attach"

    def test_rolout_gate_view_permission(self) -> None:
        from agent_app.governance.policy_rbac import PolicyReleasePermission
        assert PolicyReleasePermission.ROLLOUT_GATE_VIEW == "policy.rollout.gate.view"

    @pytest.mark.asyncio
    async def test_rolout_gate_view_is_default_allowed(self) -> None:
        from agent_app.governance.policy_rbac import PolicyReleasePermission, PolicyReleasePermissionChecker
        from agent_app.core.context import RunContext
        checker = PolicyReleasePermissionChecker()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[])
        assert await checker.check(PolicyReleasePermission.ROLLOUT_GATE_VIEW, ctx) is True

    @pytest.mark.asyncio
    async def test_rolout_gate_run_requires_explicit_permission(self) -> None:
        from agent_app.governance.policy_rbac import PolicyReleasePermission, PolicyReleasePermissionChecker
        from agent_app.core.context import RunContext
        checker = PolicyReleasePermissionChecker()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[])
        assert await checker.check(PolicyReleasePermission.ROLLOUT_GATE_RUN, ctx) is False

    @pytest.mark.asyncio
    async def test_rolout_gate_attach_requires_explicit_permission(self) -> None:
        from agent_app.governance.policy_rbac import PolicyReleasePermission, PolicyReleasePermissionChecker
        from agent_app.core.context import RunContext
        checker = PolicyReleasePermissionChecker()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[])
        assert await checker.check(PolicyReleasePermission.ROLLOUT_GATE_ATTACH, ctx) is False

    @pytest.mark.asyncio
    async def test_promotion_gate_view_still_default_allowed(self) -> None:
        """Phase 42 PROMOTION_GATE_VIEW still works."""
        from agent_app.governance.policy_rbac import PolicyReleasePermission, PolicyReleasePermissionChecker
        from agent_app.core.context import RunContext
        checker = PolicyReleasePermissionChecker()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[])
        assert await checker.check(PolicyReleasePermission.PROMOTION_GATE_VIEW, ctx) is True


# ---------------------------------------------------------------------------
# 5. Change event types
# ---------------------------------------------------------------------------


class TestRolloutGateChangeEvents:
    """Test new PolicyChangeEventType members for rollout gate automation."""

    def test_rolout_gate_run_event(self) -> None:
        from agent_app.governance.policy_change_event import PolicyChangeEventType
        assert PolicyChangeEventType.ROLLOUT_GATE_RUN == "policy.rollout.gate.run"

    def test_rolout_gate_satisfied_event(self) -> None:
        from agent_app.governance.policy_change_event import PolicyChangeEventType
        assert PolicyChangeEventType.ROLLOUT_GATE_SATISFIED == "policy.rollout.gate.satisfied"

    def test_rolout_gate_blocked_event(self) -> None:
        from agent_app.governance.policy_change_event import PolicyChangeEventType
        assert PolicyChangeEventType.ROLLOUT_GATE_BLOCKED == "policy.rollout.gate.blocked"

    def test_rolout_gate_failed_event(self) -> None:
        from agent_app.governance.policy_change_event import PolicyChangeEventType
        assert PolicyChangeEventType.ROLLOUT_GATE_FAILED == "policy.rollout.gate.failed"

    def test_rolout_gate_skipped_event(self) -> None:
        from agent_app.governance.policy_change_event import PolicyChangeEventType
        assert PolicyChangeEventType.ROLLOUT_GATE_SKIPPED == "policy.rollout.gate.skipped"

    def test_rolout_gate_attached_event(self) -> None:
        from agent_app.governance.policy_change_event import PolicyChangeEventType
        assert PolicyChangeEventType.ROLLOUT_GATE_ATTACHED == "policy.rollout.gate.attached"

    def test_rolout_gate_permission_denied_event(self) -> None:
        from agent_app.governance.policy_change_event import PolicyChangeEventType
        assert PolicyChangeEventType.ROLLOUT_GATE_PERMISSION_DENIED == "policy.rollout.gate.permission_denied"

    def test_total_event_type_count(self) -> None:
        """48 original + 7 Phase 43 + 10 Phase 44 = 65 total."""
        from agent_app.governance.policy_change_event import PolicyChangeEventType
        assert len(PolicyChangeEventType) == 65


# ---------------------------------------------------------------------------
# 6. Loader wiring
# ---------------------------------------------------------------------------


class TestLoaderWiring:
    """Test that the config loader wires rollout gate automation correctly."""

    def test_missing_config_preserves_behavior(self) -> None:
        """Without rollout_gate_automation config, no service is created."""
        from agent_app.core.app import AgentApp
        app = AgentApp()
        assert app.rollout_gate_automation_service is None

    def test_enabled_config_creates_service(self, tmp_path) -> None:
        """With enabled rollout_gate_automation config, service is created."""
        import yaml
        from agent_app.config.loader import build_app

        config_data = {
            "governance": {
                "policy_release": {
                    "bundles": {"type": "memory"},
                    "gates": {"type": "memory"},
                    "change_events": {"type": "memory"},
                    "rollouts": {"type": "memory"},
                    "rollout_gate_automation": {
                        "enabled": True,
                        "default_mode": "auto",
                        "default_failure_action": "fail",
                        "default_max_age_seconds": 600,
                        "default_gate_rules": [
                            {"name": "ratio_rule", "metric": "simulation.changed_ratio", "threshold": 0.1},
                            {"name": "error_rule", "metric": "simulation.errors", "threshold": 3},
                            {"name": "deny_rule", "metric": "simulation.new_denies", "threshold": 0},
                        ],
                    },
                },
            },
        }
        config_path = tmp_path / "agentapp.yaml"
        config_path.write_text(yaml.dump(config_data))

        app = build_app(str(config_path))
        assert app.rollout_gate_automation_service is not None
        from agent_app.runtime.policy_rollout_gate_service import RolloutGateAutomationService
        assert isinstance(app.rollout_gate_automation_service, RolloutGateAutomationService)

    def test_disabled_config_no_service(self, tmp_path) -> None:
        """With rollout_gate_automation disabled, no service is created."""
        import yaml
        from agent_app.config.loader import build_app

        config_data = {
            "governance": {
                "policy_release": {
                    "bundles": {"type": "memory"},
                    "gates": {"type": "memory"},
                    "rollout_gate_automation": {
                        "enabled": False,
                    },
                },
            },
        }
        config_path = tmp_path / "agentapp.yaml"
        config_path.write_text(yaml.dump(config_data))

        app = build_app(str(config_path))
        assert app.rollout_gate_automation_service is None


# ---------------------------------------------------------------------------
# 7. AgentApp property
# ---------------------------------------------------------------------------


class TestAgentAppProperty:
    """Test the rollout_gate_automation_service property on AgentApp."""

    def test_property_defaults_none(self) -> None:
        from agent_app.core.app import AgentApp
        app = AgentApp()
        assert app.rollout_gate_automation_service is None

    def test_property_setter(self) -> None:
        from agent_app.core.app import AgentApp

        class FakeService:
            pass

        app = AgentApp()
        app.rollout_gate_automation_service = FakeService()
        assert app.rollout_gate_automation_service is not None
        assert isinstance(app.rollout_gate_automation_service, FakeService)

    def test_property_setter_overwrite(self) -> None:
        from agent_app.core.app import AgentApp

        class FakeService1:
            pass

        class FakeService2:
            pass

        app = AgentApp()
        app.rollout_gate_automation_service = FakeService1()
        assert isinstance(app.rollout_gate_automation_service, FakeService1)
        app.rollout_gate_automation_service = FakeService2()
        assert isinstance(app.rollout_gate_automation_service, FakeService2)
