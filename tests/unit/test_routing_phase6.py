"""Tests for Phase 6: Routing Policy and Workflow Observability."""

from __future__ import annotations

import re

import pytest

from agent_app.core.result import WorkflowStep, WorkflowTrace
from agent_app.core.routing import (
    RoutingMatchType,
    RoutingPolicy,
    RoutingRule,
)
from agent_app.runtime.routing import (
    RoutingDecision,
    RoutingPolicyExecutor,
)


# ---------------------------------------------------------------------------
# Routing models
# ---------------------------------------------------------------------------

class TestRoutingRule:
    def test_keyword_rule_defaults(self):
        rule = RoutingRule(name="r1", target="agent_a", keywords=["hello"])
        assert rule.match_type == RoutingMatchType.KEYWORD
        assert rule.priority == 100

    def test_regex_rule(self):
        rule = RoutingRule(
            name="r2", target="agent_b", match_type=RoutingMatchType.REGEX,
            pattern=r"\d+",
        )
        assert rule.match_type == RoutingMatchType.REGEX
        assert rule.pattern == r"\d+"

    def test_default_rule(self):
        rule = RoutingRule(
            name="r3", target="agent_c", match_type=RoutingMatchType.DEFAULT,
        )
        assert rule.match_type == RoutingMatchType.DEFAULT
        assert rule.keywords == []

    def test_priority(self):
        rule = RoutingRule(name="r4", target="agent_d", priority=5)
        assert rule.priority == 5

    def test_metadata(self):
        rule = RoutingRule(
            name="r5", target="agent_e", metadata={"lang": "en"}
        )
        assert rule.metadata == {"lang": "en"}


class TestRoutingPolicy:
    def test_sorted_rules(self):
        rules = [
            RoutingRule(name="low", target="a", priority=50),
            RoutingRule(name="high", target="b", priority=10),
            RoutingRule(name="mid", target="c", priority=30),
        ]
        policy = RoutingPolicy(name="p", rules=rules)
        sorted_rules = policy.sorted_rules()
        assert [r.name for r in sorted_rules] == ["high", "mid", "low"]

    def test_empty_rules(self):
        policy = RoutingPolicy(name="empty")
        assert policy.sorted_rules() == []


# ---------------------------------------------------------------------------
# RoutingPolicyExecutor
# ---------------------------------------------------------------------------

class TestRoutingPolicyExecutorRouteOne:
    @pytest.fixture
    def policy(self):
        return RoutingPolicy(name="test", rules=[
            RoutingRule(
                name="refund_intent", target="refund",
                match_type=RoutingMatchType.KEYWORD,
                keywords=["refund", "退款"], priority=10,
            ),
            RoutingRule(
                name="billing_intent", target="billing",
                match_type=RoutingMatchType.KEYWORD,
                keywords=["invoice", "billing"], priority=20,
            ),
            RoutingRule(
                name="default_rule", target="triage",
                match_type=RoutingMatchType.DEFAULT, priority=999,
            ),
        ])

    @pytest.fixture
    def executor(self):
        return RoutingPolicyExecutor()

    def test_keyword_match(self, executor, policy):
        decision = executor.route_one(policy, "I want a refund", ["refund", "billing", "triage"])
        assert decision is not None
        assert decision.target == "refund"
        assert decision.rule_name == "refund_intent"

    def test_second_keyword_match(self, executor, policy):
        decision = executor.route_one(policy, "send me an invoice", ["refund", "billing", "triage"])
        assert decision is not None
        assert decision.target == "billing"

    def test_default_fallback(self, executor, policy):
        decision = executor.route_one(policy, "hello there", ["refund", "billing", "triage"])
        assert decision is not None
        assert decision.target == "triage"
        assert decision.rule_name == "default_rule"

    def test_priority_order(self, executor):
        """Lower priority number = higher priority."""
        policy = RoutingPolicy(name="p", rules=[
            RoutingRule(name="low_p", target="a", keywords=["x"], priority=100),
            RoutingRule(name="high_p", target="b", keywords=["x"], priority=5),
        ])
        decision = executor.route_one(policy, "x", ["a", "b"])
        assert decision.target == "b"  # priority 5 wins

    def test_regex_match(self, executor):
        policy = RoutingPolicy(name="p", rules=[
            RoutingRule(
                name="order_re", target="order_agent",
                match_type=RoutingMatchType.REGEX,
                pattern=r"order\s*\d+", priority=10,
            ),
            RoutingRule(
                name="default_r", target="triage",
                match_type=RoutingMatchType.DEFAULT, priority=999,
            ),
        ])
        decision = executor.route_one(policy, "check order 123", ["order_agent", "triage"])
        assert decision is not None
        assert decision.target == "order_agent"
        assert decision.rule_name == "order_re"

    def test_no_match_no_default(self, executor):
        policy = RoutingPolicy(name="p", rules=[
            RoutingRule(name="only", target="a", keywords=["xyz"], priority=10),
        ])
        decision = executor.route_one(policy, "hello", ["a", "b"])
        assert decision is None

    def test_disallowed_target_skipped(self, executor):
        """Rule targeting an agent not in allowed_targets should be skipped."""
        policy = RoutingPolicy(name="p", rules=[
            RoutingRule(name="ghost", target="ghost", keywords=["hello"], priority=10),
            RoutingRule(
                name="default_r", target="triage",
                match_type=RoutingMatchType.DEFAULT, priority=999,
            ),
        ])
        decision = executor.route_one(policy, "hello", ["triage"])
        assert decision is not None
        assert decision.target == "triage"  # default rule still applies


class TestRoutingPolicyExecutorRouteMany:
    @pytest.fixture
    def policy(self):
        return RoutingPolicy(name="test", rules=[
            RoutingRule(
                name="research_task", target="researcher",
                match_type=RoutingMatchType.KEYWORD,
                keywords=["research", "研究"], priority=10,
            ),
            RoutingRule(
                name="data_task", target="analyst",
                match_type=RoutingMatchType.KEYWORD,
                keywords=["analyze", "分析"], priority=20,
            ),
            RoutingRule(
                name="writing_task", target="writer",
                match_type=RoutingMatchType.KEYWORD,
                keywords=["write", "写"], priority=30,
            ),
            RoutingRule(
                name="default_r", target="manager",
                match_type=RoutingMatchType.DEFAULT, priority=999,
            ),
        ])

    @pytest.fixture
    def executor(self):
        return RoutingPolicyExecutor()

    def test_single_match(self, executor, policy):
        decisions = executor.route_many(policy, "research AI", ["researcher", "analyst", "writer"])
        assert len(decisions) == 1
        assert decisions[0].target == "researcher"
        assert decisions[0].rule_name == "research_task"

    def test_multiple_matches(self, executor, policy):
        decisions = executor.route_many(
            policy, "research and write", ["researcher", "analyst", "writer"]
        )
        targets = {d.target for d in decisions}
        assert "researcher" in targets
        assert "writer" in targets
        assert "analyst" not in targets

    def test_default_excluded(self, executor, policy):
        """route_many should not include default rule matches."""
        decisions = executor.route_many(policy, "hello", ["researcher", "analyst", "writer"])
        assert len(decisions) == 0

    def test_no_match(self, executor, policy):
        decisions = executor.route_many(policy, "xyz", ["researcher", "analyst", "writer"])
        assert decisions == []

    def test_two_matches(self, executor, policy):
        """research triggers researcher, write triggers writer."""
        decisions = executor.route_many(
            policy, "research AI trends and write a report",
            ["researcher", "analyst", "writer"],
        )
        targets = {d.target for d in decisions}
        assert targets == {"researcher", "writer"}


# ---------------------------------------------------------------------------
# WorkflowTrace
# ---------------------------------------------------------------------------

class TestWorkflowTrace:
    def test_create_empty(self):
        trace = WorkflowTrace(workflow_name="wf", workflow_type="handoff")
        assert trace.workflow_name == "wf"
        assert trace.steps == []

    def test_add_step(self):
        trace = WorkflowTrace()
        step = WorkflowStep(
            step_id="s1", step_type="routing", agent_name="triage",
            output_summary="→ refund", status="completed",
            metadata={"rule": "refund_intent"},
        )
        trace.steps.append(step)
        assert len(trace.steps) == 1
        assert trace.steps[0].metadata.get("rule") == "refund_intent"

    def test_step_serialization(self):
        step = WorkflowStep(
            step_id="abc", step_type="agent", agent_name="manager",
            input_summary="test input", output_summary="done", status="completed",
        )
        data = step.model_dump()
        assert data["step_id"] == "abc"
        assert data["step_type"] == "agent"


# ---------------------------------------------------------------------------
# Config loader integration — use build_app to get Workflow objects
# ---------------------------------------------------------------------------

class TestConfigLoaderRouting:
    def test_load_handoff_with_routing(self):
        from agent_app.config.loader import build_app
        import tempfile, os

        yaml_content = """
app:
  name: test
models:
  default: gpt-4o
agents:
  triage:
    instructions: test
  refund:
    instructions: test
workflows:
  cs:
    type: handoff
    entry: triage
    agents: [refund]
    routing:
      rules:
        - name: refund_intent
          target: refund
          match_type: keyword
          keywords: [refund]
          priority: 10
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            path = f.name
        try:
            app = build_app(path)
            wf = app.workflow_registry.get("cs")
            assert wf.routing_policy is not None
            assert len(wf.routing_policy.rules) == 1
            assert wf.routing_policy.rules[0].name == "refund_intent"
        finally:
            os.unlink(path)

    def test_load_without_routing(self):
        from agent_app.config.loader import build_app
        import tempfile, os

        yaml_content = """
app:
  name: test
models:
  default: gpt-4o
agents:
  triage:
    instructions: test
  refund:
    instructions: test
workflows:
  cs:
    type: handoff
    entry: triage
    agents: [refund]
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            path = f.name
        try:
            app = build_app(path)
            wf = app.workflow_registry.get("cs")
            assert wf.routing_policy is None
        finally:
            os.unlink(path)

    def test_invalid_match_type(self):
        from agent_app.config.loader import build_app
        import tempfile, os

        yaml_content = """
app:
  name: test
models:
  default: gpt-4o
agents:
  triage:
    instructions: test
  refund:
    instructions: test
workflows:
  cs:
    type: handoff
    entry: triage
    agents: [refund]
    routing:
      rules:
        - name: bad
          target: refund
          match_type: invalid_type
          keywords: [x]
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            path = f.name
        try:
            with pytest.raises(ValueError, match="Invalid match_type"):
                build_app(path)
        finally:
            os.unlink(path)
