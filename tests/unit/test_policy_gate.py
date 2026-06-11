"""Tests for PolicyGate models and PolicyGateEvaluator."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_gate import (
    PolicyGateEvaluator,
    PolicyGateResult,
    PolicyGateRule,
    PolicyGateStatus,
)


def _make_rule(
    name: str = "safe_default",
    max_changed_decisions: int | None = None,
    max_changed_ratio: float | None = 0.10,
    max_failed_replays: int | None = 0,
    max_new_denies: int | None = 5,
    max_new_approvals: int | None = None,
    fail_on_missing_required_context: bool = False,
) -> PolicyGateRule:
    """Create a test PolicyGateRule."""
    return PolicyGateRule(
        name=name,
        max_changed_decisions=max_changed_decisions,
        max_changed_ratio=max_changed_ratio,
        max_failed_replays=max_failed_replays,
        max_new_denies=max_new_denies,
        max_new_approvals=max_new_approvals,
        fail_on_missing_required_context=fail_on_missing_required_context,
    )


def _make_replay_result(
    total: int = 100,
    changed: int = 0,
    failed: int = 0,
    missing_context: int = 0,
    new_denies: int = 0,
    new_approvals: int = 0,
) -> "PolicyReplayResult":
    """Create a mock PolicyReplayResult for testing."""
    from agent_app.governance.policy_replay import (
        PolicyReplayDecisionChange,
        PolicyReplayRun,
        PolicyReplayStatus,
        PolicyReplayResult,
    )

    run = PolicyReplayRun(
        replay_id="replay_test",
        status=PolicyReplayStatus.COMPLETED,
        source_decision_count=total,
        changed_count=changed,
        unchanged_count=total - changed - failed,
        failed_count=failed,
        created_at=datetime.now(timezone.utc),
    )
    changes = []
    for i in range(total):
        action = "error" if i < failed else ("deny" if i < changed else "allow")
        changes.append(PolicyReplayDecisionChange(
            decision_id=f"dec_{i}",
            original_action="allow",
            replayed_action=action,
            changed=(action != "allow"),
        ))
    return PolicyReplayResult(replay=run, changes=changes)


class TestPolicyGateModels:
    """Tests for PolicyGateStatus, PolicyGateRule, PolicyGateResult."""

    def test_gate_status_values(self):
        """PolicyGateStatus has expected values."""
        assert PolicyGateStatus.PASSED == "passed"
        assert PolicyGateStatus.WARNING == "warning"
        assert PolicyGateStatus.FAILED == "failed"

    def test_rule_defaults(self):
        """PolicyGateRule defaults."""
        rule = _make_rule()
        assert rule.name == "safe_default"
        assert rule.max_changed_ratio == 0.10
        assert rule.max_failed_replays == 0
        assert rule.max_new_denies == 5
        assert rule.fail_on_missing_required_context is False

    def test_result_creation(self):
        """PolicyGateResult can be created."""
        result = PolicyGateResult(
            gate_result_id="gr_1",
            bundle_id="pb_1",
            replay_id="replay_1",
            status=PolicyGateStatus.PASSED,
            passed=True,
            total_decisions=100,
            changed_decisions=5,
            failed_replays=0,
            changed_ratio=0.05,
            rule_results=[],
            summary={},
            created_at=datetime.now(timezone.utc),
        )
        assert result.passed is True
        assert result.changed_ratio == 0.05


class TestPolicyGateEvaluator:
    """Tests for PolicyGateEvaluator."""

    async def test_passed_when_all_thresholds_satisfied(self):
        """All rules pass when thresholds are satisfied."""
        evaluator = PolicyGateEvaluator(rules=[
            _make_rule(max_changed_ratio=0.10, max_failed_replays=0),
        ])
        replay = _make_replay_result(total=100, changed=5, failed=0)
        bundle = _make_bundle_for_gate("pb_test")
        result = await evaluator.evaluate(bundle, replay)
        assert result.status == PolicyGateStatus.PASSED
        assert result.passed is True

    async def test_failed_when_changed_decisions_exceed_threshold(self):
        """Gate fails when changed decisions exceed threshold."""
        evaluator = PolicyGateEvaluator(rules=[
            _make_rule(max_changed_ratio=0.10),
        ])
        # 20% changed — exceeds 10% threshold
        replay = _make_replay_result(total=100, changed=20, failed=0)
        bundle = _make_bundle_for_gate("pb_test")
        result = await evaluator.evaluate(bundle, replay)
        assert result.status == PolicyGateStatus.FAILED
        assert result.passed is False

    async def test_failed_when_failed_replays_exceed_threshold(self):
        """Gate fails when failed replays exceed threshold."""
        evaluator = PolicyGateEvaluator(rules=[
            _make_rule(max_failed_replays=0),
        ])
        replay = _make_replay_result(total=100, changed=0, failed=5)
        bundle = _make_bundle_for_gate("pb_test")
        result = await evaluator.evaluate(bundle, replay)
        assert result.status == PolicyGateStatus.FAILED
        assert result.passed is False

    async def test_failed_when_missing_context_configured_as_fail(self):
        """Gate fails when fail_on_missing_required_context is True and context is missing."""
        evaluator = PolicyGateEvaluator(rules=[
            _make_rule(fail_on_missing_required_context=True),
        ])
        # Simulate replay with missing context in changes
        replay = _make_replay_result(total=100, changed=0, failed=0)
        # Inject missing context into some changes
        for i, c in enumerate(replay.changes[:3]):
            c.context_metadata = {"missing_fields": ["user_id", "tenant_id"]}
        bundle = _make_bundle_for_gate("pb_test")
        result = await evaluator.evaluate(bundle, replay)
        assert result.status == PolicyGateStatus.FAILED
        assert result.passed is False

    async def test_passes_when_missing_context_not_fail(self):
        """Gate passes when fail_on_missing_required_context is False even with missing context."""
        evaluator = PolicyGateEvaluator(rules=[
            _make_rule(fail_on_missing_required_context=False),
        ])
        replay = _make_replay_result(total=100, changed=0, failed=0)
        for i, c in enumerate(replay.changes[:3]):
            c.context_metadata = {"missing_fields": ["user_id"]}
        bundle = _make_bundle_for_gate("pb_test")
        result = await evaluator.evaluate(bundle, replay)
        assert result.status == PolicyGateStatus.PASSED

    async def test_computes_changed_ratio(self):
        """Evaluator computes changed_ratio correctly."""
        evaluator = PolicyGateEvaluator(rules=[
            _make_rule(max_changed_ratio=0.50),
        ])
        replay = _make_replay_result(total=200, changed=30, failed=0)
        bundle = _make_bundle_for_gate("pb_test")
        result = await evaluator.evaluate(bundle, replay)
        assert result.changed_ratio == 0.15

    async def test_counts_new_denies(self):
        """Evaluator counts new denies from replay changes."""
        evaluator = PolicyGateEvaluator(rules=[
            _make_rule(max_new_denies=5),
        ])
        # 3 new denies should pass
        replay = _make_replay_result(total=100, changed=3, failed=0)
        bundle = _make_bundle_for_gate("pb_test")
        result = await evaluator.evaluate(bundle, replay)
        assert result.new_denies == 3
        assert result.status == PolicyGateStatus.PASSED

    async def test_fails_when_new_denies_exceed_threshold(self):
        """Gate fails when new denies exceed threshold."""
        evaluator = PolicyGateEvaluator(rules=[
            _make_rule(max_new_denies=5),
        ])
        # 10 new denies exceeds threshold of 5
        replay = _make_replay_result(total=100, changed=10, failed=0)
        bundle = _make_bundle_for_gate("pb_test")
        result = await evaluator.evaluate(bundle, replay)
        assert result.status == PolicyGateStatus.FAILED
        assert result.new_denies == 10

    async def test_empty_rules_passes(self):
        """Empty rules list results in PASSED."""
        evaluator = PolicyGateEvaluator(rules=[])
        replay = _make_replay_result(total=100, changed=50, failed=10)
        bundle = _make_bundle_for_gate("pb_test")
        result = await evaluator.evaluate(bundle, replay)
        assert result.status == PolicyGateStatus.PASSED

    async def test_multiple_rules_all_pass(self):
        """Multiple rules all passing = PASSED."""
        evaluator = PolicyGateEvaluator(rules=[
            _make_rule(name="r1", max_changed_ratio=0.20, max_new_denies=None),
            _make_rule(name="r2", max_new_denies=10),
        ])
        replay = _make_replay_result(total=100, changed=10, failed=0)
        bundle = _make_bundle_for_gate("pb_test")
        result = await evaluator.evaluate(bundle, replay)
        assert result.status == PolicyGateStatus.PASSED

    async def test_multiple_rules_one_fails(self):
        """One failing rule among multiple = FAILED."""
        evaluator = PolicyGateEvaluator(rules=[
            _make_rule(name="r1", max_changed_ratio=0.20),
            _make_rule(name="r2", max_failed_replays=0),
        ])
        replay = _make_replay_result(total=100, changed=10, failed=5)
        bundle = _make_bundle_for_gate("pb_test")
        result = await evaluator.evaluate(bundle, replay)
        assert result.status == PolicyGateStatus.FAILED

    async def test_rule_results_populated(self):
        """Each rule gets an individual result entry."""
        evaluator = PolicyGateEvaluator(rules=[
            _make_rule(name="r1", max_changed_ratio=0.10),
            _make_rule(name="r2", max_failed_replays=0),
        ])
        replay = _make_replay_result(total=100, changed=15, failed=0)
        bundle = _make_bundle_for_gate("pb_test")
        result = await evaluator.evaluate(bundle, replay)
        assert len(result.rule_results) == 2
        # r1 should fail (15% > 10%), r2 should pass
        statuses = [rr["status"] for rr in result.rule_results]
        assert "failed" in statuses


def _make_bundle_for_gate(bundle_id: str) -> "PolicyBundle":
    """Create a minimal bundle for gate evaluation tests."""
    from agent_app.governance.policy_bundle import PolicyBundle
    return PolicyBundle(
        bundle_id=bundle_id,
        name="test-bundle",
        version="1.0.0",
        config_hash="abc123",
        created_at=datetime.now(timezone.utc),
    )
