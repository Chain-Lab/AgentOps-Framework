"""Phase 23: Tests for policy config schema and loader integration."""

from __future__ import annotations

import pytest

from agent_app.config.schema import (
    AppConfig,
    GovernanceConfig,
    PolicyEngineConfig,
    PolicyRuleConfig,
)
from agent_app.governance.policy import (
    ConfigurablePolicyEngine,
    PolicyEngine,
    PolicyEvaluationContext,
)


# ---------------------------------------------------------------------------
# Policy config model tests
# ---------------------------------------------------------------------------


class TestPolicyRuleConfig:
    def test_minimal_rule(self):
        rule = PolicyRuleConfig(
            name="test_rule",
            when={"tool_name": "refund.request"},
            then={"action": "require_approval"},
        )
        assert rule.name == "test_rule"
        assert rule.when == {"tool_name": "refund.request"}
        assert rule.then["action"] == "require_approval"

    def test_rule_with_all_fields(self):
        rule = PolicyRuleConfig(
            name="full_rule",
            when={"tool_name": "refund.request", "risk_level": "high"},
            then={
                "action": "require_approval",
                "reason": "High-risk refund",
                "ttl_seconds": 1800,
            },
        )
        assert rule.then["reason"] == "High-risk refund"
        assert rule.then["ttl_seconds"] == 1800

    def test_invalid_action_raises(self):
        """Invalid action in rule is caught when engine is created from config."""
        from agent_app.governance.policy import ConfigurablePolicyEngine
        rule_data = {
            "name": "bad",
            "when": {"tool_name": "x"},
            "then": {"action": "invalid_action"},
        }
        rule = PolicyRuleConfig(**rule_data)
        assert rule.then["action"] == "invalid_action"
        # Engine validates on construction
        with pytest.raises(ValueError):
            ConfigurablePolicyEngine(rules=[rule.model_dump()])

    def test_empty_when_is_valid(self):
        """Empty 'when' block is valid — matches all calls."""
        rule = PolicyRuleConfig(
            name="catch_all",
            when={},
            then={"action": "deny", "reason": "Catch-all deny"},
        )
        # No error — empty when is valid (catch-all)
        assert rule.name == "catch_all"
        from agent_app.governance.policy import ConfigurablePolicyEngine
        engine = ConfigurablePolicyEngine(rules=[rule.model_dump()])
        # Verify it actually matches everything
        import asyncio
        ctx = PolicyEvaluationContext(tool_name="anything", risk_level="low")
        d = asyncio.run(engine.evaluate_tool_call(ctx))
        assert d.action.value == "deny"


class TestPolicyEngineConfig:
    def test_defaults(self):
        cfg = PolicyEngineConfig()
        assert cfg.enabled is False
        assert cfg.default_action == "allow"
        assert cfg.rules == []

    def test_enabled_with_rules(self):
        cfg = PolicyEngineConfig(
            enabled=True,
            default_action="deny",
            rules=[
                {
                    "name": "allow_safe",
                    "when": {"tool_name": "order.query"},
                    "then": {"action": "allow"},
                }
            ],
        )
        assert cfg.enabled is True
        assert cfg.default_action == "deny"
        assert len(cfg.rules) == 1
        assert cfg.rules[0].name == "allow_safe"

    def test_invalid_action_in_then_raises(self):
        with pytest.raises(Exception):  # ValidationError from field_validator
            PolicyEngineConfig(
                enabled=True,
                default_action="invalid_action",
            )

    def test_invalid_rule_raises_on_engine(self):
        """Invalid action in rule 'then' is caught by engine validation."""
        with pytest.raises(ValueError, match="invalid action"):
            ConfigurablePolicyEngine(
                rules=[
                    {
                        "name": "bad",
                        "when": {"tool_name": "x"},
                        "then": {"action": "nonexistent"},
                    }
                ]
            )


class TestGovernanceConfigWithPolicies:
    def test_default_no_policies(self):
        cfg = GovernanceConfig()
        assert cfg.policies is None or not getattr(cfg.policies, "enabled", False)

    def test_with_policies(self):
        cfg = GovernanceConfig(
            policies=PolicyEngineConfig(
                enabled=True,
                rules=[
                    {
                        "name": "require_approval_for_refunds",
                        "when": {"tool_name": "refund.request"},
                        "then": {
                            "action": "require_approval",
                            "reason": "Refunds require approval",
                            "ttl_seconds": 1800,
                        },
                    }
                ],
            )
        )
        assert cfg.policies is not None
        assert cfg.policies.enabled is True
        assert len(cfg.policies.rules) == 1

    def test_from_dict(self):
        cfg = GovernanceConfig(
            policies={
                "enabled": True,
                "default_action": "deny",
                "rules": [
                    {
                        "name": "deny_dangerous",
                        "when": {"tool_name": "dangerous.delete"},
                        "then": {"action": "deny"},
                    }
                ],
            }
        )
        assert cfg.policies.enabled is True
        assert cfg.policies.rules[0].name == "deny_dangerous"


class TestAppConfigWithPolicies:
    def test_app_config_with_governance_policies(self):
        raw = {
            "agents": [{"name": "bot", "instructions": "help"}],
            "governance": {
                "approvals": {"type": "memory"},
                "policies": {
                    "enabled": True,
                    "rules": [
                        {
                            "name": "require_approval_for_refunds",
                            "when": {"tool_name": "refund.request"},
                            "then": {
                                "action": "require_approval",
                                "reason": "Refunds require approval",
                                "ttl_seconds": 1800,
                            },
                        }
                    ],
                },
            },
        }
        cfg = AppConfig(**raw)
        assert cfg.governance is not None
        assert cfg.governance.policies is not None
        assert cfg.governance.policies.enabled is True
        assert len(cfg.governance.policies.rules) == 1

    def test_app_config_without_policies_backward_compat(self):
        """Config without policies section should work (Phase 22 compat)."""
        raw = {
            "agents": [{"name": "bot", "instructions": "help"}],
            "governance": {
                "approvals": {"type": "memory"},
            },
        }
        cfg = AppConfig(**raw)
        assert cfg.governance is not None
        assert cfg.governance.policies is None or not getattr(
            cfg.governance.policies, "enabled", False
        )


# ---------------------------------------------------------------------------
# Loader integration tests
# ---------------------------------------------------------------------------


class TestPolicyLoaderIntegration:
    def test_load_config_with_policies(self, tmp_path):
        """Verify policies load from YAML config file."""
        from agent_app.config.loader import load_config

        yaml_content = """
app:
  name: test
agents:
  - name: support
    instructions: help
governance:
  approvals:
    type: memory
  policies:
    enabled: true
    default_action: allow
    rules:
      - name: require_approval_for_refunds
        when:
          tool_name: refund.request
        then:
          action: require_approval
          reason: Refunds require approval
          ttl_seconds: 1800
"""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(yaml_content)
        cfg = load_config(str(config_file))
        assert cfg.governance is not None
        assert cfg.governance.policies is not None
        assert cfg.governance.policies.enabled is True
        assert len(cfg.governance.policies.rules) == 1
        assert cfg.governance.policies.rules[0].then["ttl_seconds"] == 1800

    def test_load_config_without_policies(self, tmp_path):
        """Config without policies section loads fine (backward compat)."""
        from agent_app.config.loader import load_config

        yaml_content = """
app:
  name: test
agents:
  - name: support
    instructions: help
governance:
  approvals:
    type: memory
"""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(yaml_content)
        cfg = load_config(str(config_file))
        assert cfg.governance is not None
        policies = getattr(cfg.governance, "policies", None)
        assert policies is None or not getattr(policies, "enabled", False)

    def test_load_config_invalid_policy_rule(self, tmp_path):
        """Invalid action in rule — schema accepts it; engine validates at use time."""
        from agent_app.config.loader import load_config

        yaml_content = """
app:
  name: test
agents:
  - name: support
    instructions: help
governance:
  approvals:
    type: memory
  policies:
    enabled: true
    rules:
      - name: bad_rule
        when:
          tool_name: refund.request
        then:
          action: totally_invalid
"""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(yaml_content)
        # Schema loads it (validation deferred to engine)
        cfg = load_config(str(config_file))
        assert cfg.governance.policies is not None
        assert cfg.governance.policies.enabled is True
        # But creating an engine from it should raise
        from agent_app.governance.policy import ConfigurablePolicyEngine
        with pytest.raises(Exception):
            ConfigurablePolicyEngine(rules=[
                r.model_dump() for r in cfg.governance.policies.rules
            ])

    def test_default_policy_config_preserves_behavior(self, tmp_path):
        """Default policy config (disabled) preserves Phase 22 behavior."""
        from agent_app.config.loader import load_config

        yaml_content = """
app:
  name: test
agents:
  - name: support
    instructions: help
governance:
  approvals:
    type: memory
"""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(yaml_content)
        cfg = load_config(str(config_file))
        policies = getattr(cfg.governance, "policies", None)
        assert policies is None or not getattr(policies, "enabled", False)
