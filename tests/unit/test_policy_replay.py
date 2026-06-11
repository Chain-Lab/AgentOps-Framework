"""Tests for policy replay models and runner."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from agent_app.governance.policy import (
    ConfigurablePolicyEngine,
    PolicyAction,
    PolicyDecision,
    PolicyDecisionTrace,
    PolicyEvaluationContext,
)
from agent_app.governance.policy_replay import (
    PolicyReplayDecisionChange,
    PolicyReplayResult,
    PolicyReplayRun,
    PolicyReplayRunner,
    PolicyReplayStatus,
)


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
            {
                "name": "deny_dangerous",
                "when": {"tool_name_prefix": "dangerous."},
                "then": {"action": "deny", "reason": "blocked"},
            },
        ],
        default_action="allow",
    )


class TestReplayModels:
    def test_replay_run_creation(self):
        run = PolicyReplayRun(
            replay_id="replay_1",
            status=PolicyReplayStatus.COMPLETED,
            source_decision_count=10,
            changed_count=2,
            unchanged_count=8,
            failed_count=0,
            created_at=datetime.now(timezone.utc),
        )
        assert run.replay_id == "replay_1"
        assert run.status == PolicyReplayStatus.COMPLETED
        assert run.source_decision_count == 10

    def test_replay_run_failed_status(self):
        run = PolicyReplayRun(
            replay_id="replay_2",
            status=PolicyReplayStatus.FAILED,
            source_decision_count=5,
            changed_count=0,
            unchanged_count=0,
            failed_count=5,
            created_at=datetime.now(timezone.utc),
        )
        assert run.status == PolicyReplayStatus.FAILED

    def test_replay_decision_change_unchanged(self):
        change = PolicyReplayDecisionChange(
            decision_id="dec_1",
            original_action="allow",
            replayed_action="allow",
            changed=False,
            original_rule_id=None,
            replayed_rule_id=None,
        )
        assert not change.changed
        assert change.original_action == "allow"

    def test_replay_decision_change_changed(self):
        change = PolicyReplayDecisionChange(
            decision_id="dec_1",
            original_action="allow",
            replayed_action="deny",
            changed=True,
            original_rule_id=None,
            replayed_rule_id="new_rule",
            reason="policy updated",
        )
        assert change.changed
        assert change.replayed_rule_id == "new_rule"

    def test_replay_result_structure(self):
        run = PolicyReplayRun(
            replay_id="replay_1",
            status=PolicyReplayStatus.COMPLETED,
            source_decision_count=3,
            changed_count=1,
            unchanged_count=2,
            created_at=datetime.now(timezone.utc),
        )
        changes = [
            PolicyReplayDecisionChange(
                decision_id="dec_1",
                original_action="allow",
                replayed_action="require_approval",
                changed=True,
                original_rule_id=None,
                replayed_rule_id="refund_requires_approval",
            ),
        ]
        result = PolicyReplayResult(replay=run, changes=changes)
        assert result.replay == run
        assert len(result.changes) == 1
        assert result.changes[0].changed


class TestPolicyReplayRunner:
    def _make_runner(self, store, engine):
        return PolicyReplayRunner(
            decision_store=store,
            policy_engine=engine,
            replay_store=None,  # in-memory default
        )

    @pytest.mark.asyncio
    async def test_unchanged_decision_remains_unchanged(self):
        """A decision that matches the same rule under current policy stays unchanged."""
        from agent_app.governance.policy_decision_store import InMemoryPolicyDecisionStore

        store = InMemoryPolicyDecisionStore()
        trace = _make_trace(action=PolicyAction.ALLOW, rule_name=None)
        await store.record(trace)

        engine = ConfigurablePolicyEngine(
            rules=[],
            default_action="allow",
        )
        runner = self._make_runner(store, engine)
        result = await runner.run_replay()

        assert result.replay.changed_count == 0
        assert result.replay.unchanged_count == 1
        assert not result.changes[0].changed

    @pytest.mark.asyncio
    async def test_changed_decision_detected(self):
        """A decision whose action changes under new policy is detected."""
        from agent_app.governance.policy_decision_store import InMemoryPolicyDecisionStore

        store = InMemoryPolicyDecisionStore()
        # Originally allow (no matching rule), now policy has a deny rule
        trace = _make_trace(
            decision_id="dec_dangerous",
            tool_name="dangerous.delete",
            action=PolicyAction.ALLOW,
            rule_name=None,
            context={
                "tool_name": "dangerous.delete",
                "agent_name": "test",
                "tenant_id": "t1",
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
        runner = self._make_runner(store, engine)
        result = await runner.run_replay()

        assert result.replay.changed_count == 1
        assert result.changes[0].changed
        assert result.changes[0].original_action == "allow"
        assert result.changes[0].replayed_action == "deny"

    @pytest.mark.asyncio
    async def test_failed_replay_counted(self):
        """Decisions missing required replay context are counted as failed."""
        from agent_app.governance.policy_decision_store import InMemoryPolicyDecisionStore

        store = InMemoryPolicyDecisionStore()
        # Trace with no tool_name in context_summary — can't replay
        trace = PolicyDecisionTrace(
            decision_id="dec_bad",
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
        runner = self._make_runner(store, engine)
        result = await runner.run_replay()

        assert result.replay.failed_count == 1
        assert result.replay.unchanged_count == 0
        assert result.replay.changed_count == 0

    @pytest.mark.asyncio
    async def test_filters_passed_to_store(self):
        """Replay runner passes filters to the decision store."""
        from agent_app.governance.policy_decision_store import InMemoryPolicyDecisionStore

        store = InMemoryPolicyDecisionStore()
        trace1 = _make_trace(
            decision_id="dec_t1",
            context={"tool_name": "refund.request", "agent_name": "refund", "tenant_id": "tenant_a"},
        )
        trace2 = _make_trace(
            decision_id="dec_t2",
            tool_name="order.query",
            context={"tool_name": "order.query", "agent_name": "refund", "tenant_id": "tenant_b"},
        )
        await store.record(trace1)
        await store.record(trace2)

        engine = ConfigurablePolicyEngine(rules=[], default_action="allow")
        runner = self._make_runner(store, engine)
        result = await runner.run_replay(tenant_id="tenant_a")

        assert result.replay.source_decision_count == 1
        assert result.replay.unchanged_count == 1

    @pytest.mark.asyncio
    async def test_limit_filter(self):
        """Replay runner respects limit."""
        from agent_app.governance.policy_decision_store import InMemoryPolicyDecisionStore

        store = InMemoryPolicyDecisionStore()
        for i in range(5):
            trace = _make_trace(
                decision_id=f"dec_{i}",
                context={"tool_name": f"tool_{i}", "agent_name": "test", "tenant_id": "t1"},
            )
            await store.record(trace)

        engine = ConfigurablePolicyEngine(rules=[], default_action="allow")
        runner = self._make_runner(store, engine)
        result = await runner.run_replay(limit=3)

        assert result.replay.source_decision_count == 3

    @pytest.mark.asyncio
    async def test_replay_result_persisted(self):
        """Replay runner persists result to replay store."""
        from agent_app.governance.policy_decision_store import InMemoryPolicyDecisionStore
        from agent_app.runtime.policy_replay_store import InMemoryPolicyReplayStore

        store = InMemoryPolicyDecisionStore()
        trace = _make_trace(action=PolicyAction.ALLOW)
        await store.record(trace)

        engine = ConfigurablePolicyEngine(rules=[], default_action="allow")
        replay_store = InMemoryPolicyReplayStore()
        runner = PolicyReplayRunner(
            decision_store=store,
            policy_engine=engine,
            replay_store=replay_store,
        )
        result = await runner.run_replay()

        # Verify the result was saved
        saved = await replay_store.get(result.replay.replay_id)
        assert saved is not None
        assert saved.replay.source_decision_count == 1

    @pytest.mark.asyncio
    async def test_replay_rule_change_detected(self):
        """Detect when the matched rule changes."""
        from agent_app.governance.policy_decision_store import InMemoryPolicyDecisionStore

        store = InMemoryPolicyDecisionStore()
        # Originally matched "old_rule" with allow
        trace = PolicyDecisionTrace(
            decision_id="dec_rule_change",
            run_id="run_1",
            rule_name="old_rule",
            action=PolicyAction.ALLOW,
            reason="old reason",
            tool_name="tool.special",
            matched_conditions={"tool_name": "tool.special"},
            context_summary={
                "tool_name": "tool.special",
                "agent_name": "test",
                "tenant_id": "t1",
            },
            created_at=datetime.now(timezone.utc),
        )
        await store.record(trace)

        # New policy: different rule matches the same tool
        engine = ConfigurablePolicyEngine(
            rules=[
                {
                    "name": "new_rule",
                    "when": {"tool_name": "tool.special"},
                    "then": {"action": "deny", "reason": "new policy"},
                },
            ],
            default_action="allow",
        )
        runner = self._make_runner(store, engine)
        result = await runner.run_replay()

        assert result.replay.changed_count == 1
        change = result.changes[0]
        assert change.changed
        assert change.original_rule_id == "old_rule"
        assert change.replayed_rule_id == "new_rule"
        assert change.original_action == "allow"
        assert change.replayed_action == "deny"
