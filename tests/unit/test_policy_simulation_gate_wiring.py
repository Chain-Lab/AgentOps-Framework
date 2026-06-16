"""Tests for Phase 41 Task 4 — config, loader, RBAC, events for simulation gate."""
from __future__ import annotations

import pytest

from agent_app.config.schema import PolicySimulationConfig, PolicyGateRuleConfig
from agent_app.governance.policy_rbac import PolicyReleasePermission, _DEFAULT_ALLOWED
from agent_app.governance.policy_change_event import PolicyChangeEventType


class TestSimulationGateConfig:
    """Tests for PolicySimulationConfig.gates field."""

    def test_missing_gates_preserves_behavior(self):
        """PolicySimulationConfig with no gates defaults to empty list."""
        config = PolicySimulationConfig(enabled=True)
        assert config.gates == []

    def test_config_gate_rules_load(self):
        """Gate rules can be loaded into PolicySimulationConfig."""
        rule1 = PolicyGateRuleConfig(
            name="gate-a",
            description="First gate",
            max_changed_decisions=10,
        )
        rule2 = PolicyGateRuleConfig(
            name="gate-b",
            description="Second gate",
            max_changed_ratio=0.5,
        )
        config = PolicySimulationConfig(enabled=True, gates=[rule1, rule2])
        assert len(config.gates) == 2
        assert config.gates[0].name == "gate-a"
        assert config.gates[0].max_changed_decisions == 10
        assert config.gates[1].name == "gate-b"
        assert config.gates[1].max_changed_ratio == 0.5

    def test_invalid_gate_rule_fails(self):
        """PolicyGateRuleConfig without required 'name' should raise."""
        with pytest.raises(Exception):
            PolicyGateRuleConfig()


class TestSimulationGateRBAC:
    """Tests for simulation gate RBAC permissions."""

    def test_simulation_gate_run_permission_exists(self):
        assert PolicyReleasePermission.SIMULATION_GATE_RUN == "policy.simulation.gate.run"

    def test_simulation_gate_view_permission_exists(self):
        assert PolicyReleasePermission.SIMULATION_GATE_VIEW == "policy.simulation.gate.view"

    def test_gate_view_in_default_allowed(self):
        assert PolicyReleasePermission.SIMULATION_GATE_VIEW in _DEFAULT_ALLOWED


class TestSimulationGateEvents:
    """Tests for simulation gate change event types."""

    def test_gate_run_event(self):
        assert PolicyChangeEventType.SIMULATION_GATE_RUN == "policy.simulation.gate_run"

    def test_gate_passed_event(self):
        assert PolicyChangeEventType.SIMULATION_GATE_PASSED == "policy.simulation.gate_passed"

    def test_gate_failed_event(self):
        assert PolicyChangeEventType.SIMULATION_GATE_FAILED == "policy.simulation.gate_failed"

    def test_gate_permission_denied_event(self):
        assert PolicyChangeEventType.SIMULATION_GATE_PERMISSION_DENIED == "policy.simulation.gate_permission_denied"
