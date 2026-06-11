"""Phase 24: Tests for policy validation module."""

from __future__ import annotations

import pytest

from agent_app.governance.policy_validation import (
    PolicyValidationIssue,
    PolicyValidationResult,
    validate_policy_config,
)


def _make_config(**overrides):
    """Build a PolicyEngineConfig with optional overrides."""
    from agent_app.config.schema import PolicyEngineConfig, PolicyRuleConfig

    defaults = dict(
        enabled=True,
        default_action="allow",
        rules=[
            PolicyRuleConfig(
                name="allow_safe",
                when={"tool_name": "order.query"},
                then={"action": "allow"},
            )
        ],
    )
    defaults.update(overrides)
    return PolicyEngineConfig(**defaults)


class TestPolicyValidationResult:
    def test_empty_issues_is_valid(self):
        r = PolicyValidationResult(valid=True)
        assert r.valid is True
        assert r.issues == []

    def test_errors_make_invalid(self):
        r = PolicyValidationResult(
            valid=False,
            issues=[PolicyValidationIssue(level="error", message="bad rule")],
        )
        assert r.valid is False

    def test_warnings_only_still_valid(self):
        r = PolicyValidationResult(
            valid=True,
            issues=[PolicyValidationIssue(level="warning", message="no rules")],
        )
        assert r.valid is True


class TestValidatePolicyConfig:
    # -- valid config --

    def test_valid_config_passes(self):
        cfg = _make_config()
        result = validate_policy_config(cfg)
        assert result.valid is True
        assert len(result.issues) == 0

    def test_valid_config_with_multiple_rules(self):
        from agent_app.config.schema import PolicyRuleConfig

        cfg = _make_config(
            rules=[
                PolicyRuleConfig(
                    name="r1", when={"tool_name": "a"}, then={"action": "allow"}
                ),
                PolicyRuleConfig(
                    name="r2", when={"tool_name": "b"}, then={"action": "deny"}
                ),
            ]
        )
        result = validate_policy_config(cfg)
        assert result.valid is True

    # -- duplicate rule names --

    def test_duplicate_rule_names_error(self):
        from agent_app.config.schema import PolicyRuleConfig

        cfg = _make_config(
            rules=[
                PolicyRuleConfig(
                    name="dup", when={"tool_name": "a"}, then={"action": "allow"}
                ),
                PolicyRuleConfig(
                    name="dup", when={"tool_name": "b"}, then={"action": "deny"}
                ),
            ]
        )
        result = validate_policy_config(cfg)
        assert result.valid is False
        dup_errors = [i for i in result.issues if i.level == "error" and "duplicate" in i.message.lower()]
        assert len(dup_errors) >= 1

    # -- invalid action --

    def test_invalid_action_in_rule(self):
        cfg = _make_config(
            rules=[
                {
                    "name": "bad",
                    "when": {"tool_name": "x"},
                    "then": {"action": "nonexistent"},
                }
            ]
        )
        result = validate_policy_config(cfg)
        assert result.valid is False
        action_errors = [i for i in result.issues if "invalid action" in i.message.lower()]
        assert len(action_errors) >= 1

    # -- invalid default_action --

    def test_invalid_default_action(self):
        result = validate_policy_config({"enabled": True, "default_action": "invalid_action"})
        assert result.valid is False
        da_errors = [i for i in result.issues if "default_action" in i.message.lower()]
        assert len(da_errors) >= 1

    # -- unsupported condition fields --

    def test_unsupported_condition_field(self):
        cfg = _make_config(
            rules=[
                {
                    "name": "bad_cond",
                    "when": {"totally_unknown": "x"},
                    "then": {"action": "allow"},
                }
            ]
        )
        result = validate_policy_config(cfg)
        assert result.valid is False
        cond_errors = [i for i in result.issues if "unsupported" in i.message.lower()]
        assert len(cond_errors) >= 1

    # -- conflicting tool_name and tool_name_prefix --

    def test_conflicting_tool_name_and_prefix(self):
        cfg = _make_config(
            rules=[
                {
                    "name": "conflict",
                    "when": {"tool_name": "refund.request", "tool_name_prefix": "billing."},
                    "then": {"action": "allow"},
                }
            ]
        )
        result = validate_policy_config(cfg)
        assert result.valid is False
        conflict_errors = [
            i for i in result.issues if "conflict" in i.message.lower()
        ]
        assert len(conflict_errors) >= 1

    # -- missing_roles / missing_permissions type check --

    def test_missing_roles_must_be_list(self):
        cfg = _make_config(
            rules=[
                {
                    "name": "bad_roles",
                    "when": {"missing_roles": "not_a_list"},
                    "then": {"action": "deny"},
                }
            ]
        )
        result = validate_policy_config(cfg)
        assert result.valid is False
        type_errors = [i for i in result.issues if "missing_roles" in i.message.lower()]
        assert len(type_errors) >= 1

    def test_missing_permissions_must_be_list(self):
        cfg = _make_config(
            rules=[
                {
                    "name": "bad_perms",
                    "when": {"missing_permissions": 123},
                    "then": {"action": "deny"},
                }
            ]
        )
        result = validate_policy_config(cfg)
        assert result.valid is False
        type_errors = [i for i in result.issues if "missing_permissions" in i.message.lower()]
        assert len(type_errors) >= 1

    # -- ttl_seconds validation --

    def test_negative_ttl_error(self):
        cfg = _make_config(
            rules=[
                {
                    "name": "bad_ttl",
                    "when": {},
                    "then": {"action": "require_approval", "ttl_seconds": -1},
                }
            ]
        )
        result = validate_policy_config(cfg)
        assert result.valid is False
        ttl_errors = [i for i in result.issues if "ttl_seconds" in i.message.lower()]
        assert len(ttl_errors) >= 1

    def test_zero_ttl_error(self):
        cfg = _make_config(
            rules=[
                {
                    "name": "zero_ttl",
                    "when": {},
                    "then": {"action": "require_approval", "ttl_seconds": 0},
                }
            ]
        )
        result = validate_policy_config(cfg)
        assert result.valid is False
        ttl_errors = [i for i in result.issues if "ttl_seconds" in i.message.lower()]
        assert len(ttl_errors) >= 1

    def test_positive_ttl_ok(self):
        cfg = _make_config(
            rules=[
                {
                    "name": "good_ttl",
                    "when": {},
                    "then": {"action": "require_approval", "ttl_seconds": 300},
                }
            ]
        )
        result = validate_policy_config(cfg)
        ttl_errors = [i for i in result.issues if "ttl_seconds" in i.message.lower()]
        assert len(ttl_errors) == 0

    # -- enabled with no rules --

    def test_enabled_no_rules_warning(self):
        result = validate_policy_config({"enabled": True, "rules": []})
        assert result.valid is True  # warnings don't fail
        warn_issues = [i for i in result.issues if i.level == "warning"]
        assert len(warn_issues) >= 1
        assert "no rules" in warn_issues[0].message.lower() or "empty" in warn_issues[0].message.lower()

    def test_disabled_no_rules_no_warning(self):
        result = validate_policy_config({"enabled": False, "rules": []})
        assert result.valid is True
        assert len(result.issues) == 0

    # -- rule_name in issues --

    def test_issue_includes_rule_name(self):
        from agent_app.config.schema import PolicyRuleConfig

        cfg = _make_config(
            rules=[
                PolicyRuleConfig(
                    name="my_rule",
                    when={"tool_name": "x", "tool_name_prefix": "y"},
                    then={"action": "allow"},
                )
            ]
        )
        result = validate_policy_config(cfg)
        named = [i for i in result.issues if i.rule_name == "my_rule"]
        assert len(named) >= 1

    # -- multiple issues at once --

    def test_multiple_issues_reported(self):
        result = validate_policy_config({
            "enabled": True,
            "default_action": "allow",
            "rules": [
                {
                    "name": "r1",
                    "when": {"tool_name": "a", "tool_name_prefix": "b"},
                    "then": {"action": "nonexistent"},
                },
                {
                    "name": "r1",  # duplicate
                    "when": {"tool_name": "c"},
                    "then": {"action": "allow", "ttl_seconds": -5},
                },
            ],
        })
        assert result.valid is False
        assert len(result.issues) >= 3  # conflict + dup + bad action + bad ttl

    # -- edge cases --

    def test_empty_rules_list_valid_when_disabled(self):
        result = validate_policy_config({"enabled": False, "rules": []})
        assert result.valid is True
        assert len(result.issues) == 0

    def test_valid_audit_only_rule(self):
        cfg = _make_config(
            rules=[
                {
                    "name": "audit",
                    "when": {"tool_name": "billing.query"},
                    "then": {"action": "audit_only", "reason": "Log for compliance"},
                }
            ]
        )
        result = validate_policy_config(cfg)
        assert result.valid is True

    def test_valid_rate_limit_rule(self):
        cfg = _make_config(
            rules=[
                {
                    "name": "rate",
                    "when": {"tool_name": "api.call"},
                    "then": {
                        "action": "rate_limit",
                        "reason": "Limit API calls",
                        "rate_limit": {"max": 10, "window": 60},
                    },
                }
            ]
        )
        result = validate_policy_config(cfg)
        assert result.valid is True
