"""Tests for PolicyReplayRunner with context builder integration."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from agent_app.governance.policy import (
    ConfigurablePolicyEngine,
    PolicyAction,
    PolicyDecisionTrace,
)
from agent_app.governance.policy_replay import (
    PolicyReplayDecisionChange,
    PolicyReplayResult,
    PolicyReplayRunner,
    PolicyReplayStatus,
)
from agent_app.governance.policy_replay_context import (
    PolicyReplayContextBuilder,
)
from agent_app.governance.policy_decision_store import InMemoryPolicyDecisionStore


def _make_trace(
    decision_id: str = "dec_1",
    tool_name: str = "refund.request",
    action: PolicyAction = PolicyAction.ALLOW,
    rule_name: str | None = None,
    context: dict | None = None,
) -> PolicyDecisionTrace:
    return PolicyDecisionTrace(
        decision_id=decision_id,
        run_id="run_1",
        rule_name=rule_name,
        action=action,
        reason="test",
        tool_name=tool_name,
        matched_conditions={"tool_name": tool_name},
        context_summary=context or {
            "tool_name": tool_name,
            "agent_name": "refund",
            "tenant_id": "t1",
            "user_id": "user_1",
            "roles": ["support"],
            "permissions": ["refund.read"],
        },
        created_at=datetime.now(timezone.utc),
    )


def _make_engine() -> ConfigurablePolicyEngine:
    return ConfigurablePolicyEngine(
        rules=[
            {
                "name": "refund_requires_approval",
                "when": {"tool_name": "refund.request"},
                "then": {"action": "require_approval", "reason": "needs approval"},
            },
        ],
        default_action="allow",
    )


class TestPolicyReplayRunnerWithContextBuilder:
    """Tests for PolicyReplayRunner with PolicyReplayContextBuilder."""

    def _make_runner(self, store, engine, context_builder=None, replay_store=None):
        return PolicyReplayRunner(
            decision_store=store,
            policy_engine=engine,
            replay_store=replay_store,
            context_builder=context_builder,
        )

    @pytest.mark.asyncio
    async def test_uses_context_builder(self):
        """Runner uses context builder when provided."""
        store = InMemoryPolicyDecisionStore()
        trace = _make_trace(action=PolicyAction.ALLOW, rule_name=None)
        await store.record(trace)

        engine = ConfigurablePolicyEngine(rules=[], default_action="allow")
        builder = PolicyReplayContextBuilder()
        runner = self._make_runner(store, engine, context_builder=builder)
        result = await runner.run_replay()

        assert result.replay.unchanged_count == 1
        # Context metadata should be present
        change = result.changes[0]
        assert change.context_metadata is not None
        assert "missing_fields" in change.context_metadata

    @pytest.mark.asyncio
    async def test_context_builder_records_missing_fields(self):
        """Runner records missing context fields when context_builder is used."""
        store = InMemoryPolicyDecisionStore()
        # Trace with minimal context
        trace = PolicyDecisionTrace(
            decision_id="dec_sparse",
            run_id="run_1",
            rule_name=None,
            action=PolicyAction.ALLOW,
            reason="test",
            tool_name="tool.ok",
            matched_conditions={},
            context_summary={
                "tool_name": "tool.ok",
                # Missing: tenant_id, user_id, permissions
            },
            created_at=datetime.now(timezone.utc),
        )
        await store.record(trace)

        engine = ConfigurablePolicyEngine(rules=[], default_action="allow")
        builder = PolicyReplayContextBuilder()
        runner = self._make_runner(store, engine, context_builder=builder)
        result = await runner.run_replay()

        assert result.replay.unchanged_count == 1
        change = result.changes[0]
        assert change.context_metadata is not None
        assert "permissions" in change.context_metadata["missing_fields"]
        assert "tenant_id" in change.context_metadata["missing_fields"]
        assert "user_id" in change.context_metadata["missing_fields"]

    @pytest.mark.asyncio
    async def test_failed_context_increments_failed_count(self):
        """When context builder can't build eval context, decision is failed."""
        store = InMemoryPolicyDecisionStore()
        # Trace without tool_name — context builder will return None
        trace = PolicyDecisionTrace(
            decision_id="dec_notool",
            run_id="run_1",
            rule_name=None,
            action=PolicyAction.ALLOW,
            reason="test",
            tool_name=None,
            matched_conditions={},
            context_summary={"agent_name": "test"},
            created_at=datetime.now(timezone.utc),
        )
        await store.record(trace)

        engine = ConfigurablePolicyEngine(rules=[], default_action="allow")
        builder = PolicyReplayContextBuilder()
        runner = self._make_runner(store, engine, context_builder=builder)
        result = await runner.run_replay()

        assert result.replay.failed_count == 1
        assert result.replay.unchanged_count == 0
        assert result.replay.changed_count == 0

    @pytest.mark.asyncio
    async def test_context_metadata_includes_permissions(self):
        """Context metadata includes permissions used during replay."""
        store = InMemoryPolicyDecisionStore()
        trace = _make_trace(
            context={
                "tool_name": "refund.request",
                "agent_name": "refund",
                "tenant_id": "t1",
                "user_id": "user_1",
                "roles": ["support", "admin"],
                "permissions": ["refund.read", "refund.write", "refund.approve"],
            },
        )
        await store.record(trace)

        engine = ConfigurablePolicyEngine(rules=[], default_action="allow")
        builder = PolicyReplayContextBuilder()
        runner = self._make_runner(store, engine, context_builder=builder)
        result = await runner.run_replay()

        change = result.changes[0]
        assert change.context_metadata is not None
        assert change.context_metadata["permissions_used"] == [
            "refund.read", "refund.write", "refund.approve"
        ]
        assert change.context_metadata["roles"] == ["support", "admin"]
        assert change.context_metadata["user_id"] == "user_1"
        assert change.context_metadata["tenant_id"] == "t1"

    @pytest.mark.asyncio
    async def test_without_context_builder_still_works(self):
        """Runner works without context builder (backward compat)."""
        store = InMemoryPolicyDecisionStore()
        trace = _make_trace(action=PolicyAction.ALLOW)
        await store.record(trace)

        engine = ConfigurablePolicyEngine(rules=[], default_action="allow")
        runner = self._make_runner(store, engine, context_builder=None)
        result = await runner.run_replay()

        assert result.replay.unchanged_count == 1
        # No context metadata without builder
        change = result.changes[0]
        assert change.context_metadata is None

    @pytest.mark.asyncio
    async def test_changed_decision_with_context_metadata(self):
        """Changed decisions include context metadata."""
        store = InMemoryPolicyDecisionStore()
        trace = _make_trace(
            decision_id="dec_change",
            tool_name="dangerous.delete",
            action=PolicyAction.ALLOW,
            rule_name=None,
            context={
                "tool_name": "dangerous.delete",
                "agent_name": "test",
                "tenant_id": "t1",
                "user_id": "user_1",
                "roles": ["admin"],
                "permissions": ["dangerous.delete"],
            },
        )
        await store.record(trace)

        engine = ConfigurablePolicyEngine(
            rules=[
                {
                    "name": "deny_dangerous",
                    "when": {"tool_name_prefix": "dangerous."},
                    "then": {"action": "deny", "reason": "blocked"},
                },
            ],
            default_action="allow",
        )
        builder = PolicyReplayContextBuilder()
        runner = self._make_runner(store, engine, context_builder=builder)
        result = await runner.run_replay()

        assert result.replay.changed_count == 1
        change = result.changes[0]
        assert change.changed
        assert change.original_action == "allow"
        assert change.replayed_action == "deny"
        assert change.context_metadata is not None
        assert "missing_fields" in change.context_metadata
