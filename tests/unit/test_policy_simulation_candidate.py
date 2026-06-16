"""Tests for candidate policy store."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agent_app.governance.runtime_policy import RuntimePolicyRule, RuntimePolicyEffect, RuntimePolicyRuleStatus
from agent_app.governance.policy_enforcement import PolicyActionType
from agent_app.runtime.policy_candidate_store import (
    CandidateRuntimePolicySet,
    build_candidate_policy_store,
)
from agent_app.runtime.runtime_policy_store import InMemoryRuntimePolicyStore


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_rule(name: str, effect: RuntimePolicyEffect, **kwargs) -> RuntimePolicyRule:
    return RuntimePolicyRule(
        rule_id=f"rpr_{name}",
        name=name,
        action_type=kwargs.get("action_type", PolicyActionType.TOOL_EXECUTE),
        effect=effect,
        tool_name=kwargs.get("tool_name"),
        risk_level=kwargs.get("risk_level"),
        status=kwargs.get("status", RuntimePolicyRuleStatus.ENABLED),
    )


class TestCandidateRuntimePolicySet:
    def test_model(self):
        rules = [_make_rule("deny_refunds", RuntimePolicyEffect.DENY)]
        cs = CandidateRuntimePolicySet(name="test_set", rules=rules)
        assert cs.name == "test_set"
        assert len(cs.rules) == 1

    def test_default_name(self):
        cs = CandidateRuntimePolicySet(rules=[])
        assert cs.name is None


class TestBuildCandidatePolicyStore:
    def test_candidate_only(self):
        candidate_rules = [_make_rule("deny_all", RuntimePolicyEffect.DENY)]
        store = build_candidate_policy_store(
            base_rules=[], candidate_rules=candidate_rules, include_base=False,
        )
        rules = _run_async(store.list())
        assert len(rules) == 1
        assert rules[0].name == "deny_all"

    def test_base_plus_candidate(self):
        base_rules = [_make_rule("allow_all", RuntimePolicyEffect.ALLOW)]
        candidate_rules = [_make_rule("deny_refunds", RuntimePolicyEffect.DENY, tool_name="refund.request")]
        store = build_candidate_policy_store(
            base_rules=base_rules, candidate_rules=candidate_rules, include_base=True,
        )
        rules = _run_async(store.list())
        assert len(rules) == 2

    def test_disabled_candidate_ignored(self):
        candidate_rules = [
            _make_rule("disabled_rule", RuntimePolicyEffect.DENY, status=RuntimePolicyRuleStatus.DISABLED),
        ]
        store = build_candidate_policy_store(
            base_rules=[], candidate_rules=candidate_rules, include_base=False,
        )
        # Disabled rules exist in store but evaluator filters them
        all_rules = _run_async(store.list())
        enabled_rules = _run_async(store.list(status=RuntimePolicyRuleStatus.ENABLED))
        assert len(all_rules) == 1
        assert len(enabled_rules) == 0

    def test_actual_runtime_store_not_mutated(self):
        actual_store = InMemoryRuntimePolicyStore()
        # Pre-populate actual store
        _run_async(actual_store.create(_make_rule("base_rule", RuntimePolicyEffect.ALLOW)))

        # Build candidate store — should not affect actual
        candidate_rules = [_make_rule("candidate_rule", RuntimePolicyEffect.DENY)]
        candidate_store = build_candidate_policy_store(
            base_rules=[], candidate_rules=candidate_rules, include_base=False,
        )

        # Actual store should still only have base_rule
        actual_rules = _run_async(actual_store.list())
        assert len(actual_rules) == 1
        assert actual_rules[0].name == "base_rule"
