"""Phase 24: Tests for eval policy_decisions assertions."""

from __future__ import annotations

import pytest

from agent_app.core.result import AppRunResult
from agent_app.evals.assertions import run_assertions
from agent_app.evals.schema import EvalCase, EvalExpect


def _make_case(**overrides):
    """Build an EvalCase with defaults."""
    defaults = dict(
        id="test_case",
        input="test",
        agent=None,
        workflow=None,
        user_id="eval_user",
        tenant_id="eval_tenant",
        permissions=[],
        expect={},
    )
    defaults.update(overrides)
    if "expect" in defaults and isinstance(defaults["expect"], dict):
        defaults["expect"] = EvalExpect(**defaults["expect"])
    return EvalCase(**defaults)


def _make_result_with_policy_events(policy_events):
    """Create an AppRunResult with policy-like events in trace_events.

    Policy events are audit events (not RunEventType enum), so we store
    them in a side-channel list for the assertion to check.
    """
    from agent_app.observability.events import RunEvent, RunEventType

    events = []
    for pe in policy_events:
        # Use a valid RunEventType and stash policy data in the event's data field
        ev = RunEvent(
            event_id="evt_1",
            event_type=RunEventType.TOOL_COMPLETED,  # valid enum
            trace_id="trace_1",
            run_id="run_1",
            user_id="u1",
            tenant_id="t1",
            tool_name=pe.get("tool"),
            data=pe.get("data", {}),
        )
        events.append(ev)

    return AppRunResult(
        run_id="run_1",
        status=policy_events[-1].get("final_status", "completed") if policy_events else "completed",
        final_output="test output",
        tool_calls=[],
        interruptions=[],
        handoffs=[],
        trace_events=events,
    )


class TestPolicyDecisionsAssertion:
    def test_policy_decisions_pass_when_matched(self):
        """policy_decisions assertion passes when events match."""
        case = _make_case(
            expect={
                "policy_decisions": [
                    {"rule_name": "require_approval_for_refunds", "action": "require_approval"},
                ]
            }
        )
        result = _make_result_with_policy_events([
            {
                "type": "policy.approval_required",
                "tool": "refund.request",
                "data": {
                    "action": "require_approval",
                    "rule_name": "require_approval_for_refunds",
                    "reason": "Refunds need approval",
                },
            }
        ])
        errors = run_assertions(case, result)
        assert len(errors) == 0

    def test_policy_decisions_fail_when_no_match(self):
        """policy_decisions assertion fails when no matching event."""
        case = _make_case(
            expect={
                "policy_decisions": [
                    {"rule_name": "nonexistent_rule", "action": "deny"},
                ]
            }
        )
        result = _make_result_with_policy_events([
            {
                "type": "policy.evaluated",
                "data": {"action": "allow", "rule_name": "other_rule"},
            }
        ])
        errors = run_assertions(case, result)
        assert len(errors) >= 1
        assert "nonexistent_rule" in errors[0]

    def test_policy_decisions_match_by_action_only(self):
        """Can match by action without specifying rule_name."""
        case = _make_case(
            expect={
                "policy_decisions": [
                    {"action": "deny"},
                ]
            }
        )
        result = _make_result_with_policy_events([
            {
                "type": "policy.denied",
                "data": {"action": "deny", "reason": "Blocked"},
            }
        ])
        errors = run_assertions(case, result)
        assert len(errors) == 0

    def test_policy_decisions_match_by_reason_contains(self):
        """reason_contains matches substring in reason."""
        case = _make_case(
            expect={
                "policy_decisions": [
                    {"rule_name": "r1", "reason_contains": "approval"},
                ]
            }
        )
        result = _make_result_with_policy_events([
            {
                "type": "policy.approval_required",
                "data": {
                    "action": "require_approval",
                    "rule_name": "r1",
                    "reason": "Requires human approval",
                },
            }
        ])
        errors = run_assertions(case, result)
        assert len(errors) == 0

    def test_policy_decisions_fail_wrong_action(self):
        """Fails when action doesn't match."""
        case = _make_case(
            expect={
                "policy_decisions": [
                    {"action": "deny"},
                ]
            }
        )
        result = _make_result_with_policy_events([
            {
                "type": "policy.evaluated",
                "data": {"action": "allow", "rule_name": "r1"},
            }
        ])
        errors = run_assertions(case, result)
        assert len(errors) >= 1
        assert "deny" in errors[0].lower()

    def test_policy_decisions_empty_list_no_check(self):
        """Empty policy_decisions list means no check."""
        case = _make_case(expect={"policy_decisions": []})
        result = _make_result_with_policy_events([])
        errors = run_assertions(case, result)
        assert len(errors) == 0

    def test_policy_decisions_multiple_checks(self):
        """Multiple policy_decisions checks."""
        case = _make_case(
            expect={
                "policy_decisions": [
                    {"rule_name": "r1", "action": "allow"},
                    {"rule_name": "r2", "action": "audit_only"},
                ]
            }
        )
        result = _make_result_with_policy_events([
            {
                "type": "policy.evaluated",
                "data": {"action": "allow", "rule_name": "r1"},
            },
            {
                "type": "policy.audit_only",
                "data": {"action": "audit_only", "rule_name": "r2"},
            },
        ])
        errors = run_assertions(case, result)
        assert len(errors) == 0

    def test_policy_decisions_fail_when_no_policy_events(self):
        """Fails when expecting policy events but none recorded."""
        case = _make_case(
            expect={
                "policy_decisions": [
                    {"action": "allow"},
                ]
            }
        )
        result = _make_result_with_policy_events([])
        errors = run_assertions(case, result)
        assert len(errors) >= 1
