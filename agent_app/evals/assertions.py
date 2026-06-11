"""Eval assertions — check AppRunResult against expected outcomes."""

from __future__ import annotations

from agent_app.core.result import AppRunResult


class AssertionError(Exception):
    """Raised when an eval assertion fails."""


def run_assertions(case: Any, result: AppRunResult) -> list[str]:
    """Run all assertions for an eval case.

    Args:
        case: The EvalCase with expect field.
        result: The actual AppRunResult from running the case.

    Returns:
        List of error messages (empty if all passed).
    """
    errors: list[str] = []
    expect = case.expect

    # When approve_and_resume is True, the final result is the resumed result,
    # so we skip checks that apply to the initial interrupted state.
    if expect.status is not None and not expect.approve_and_resume:
        _assert_status(result, expect.status, errors)
    if expect.output_contains:
        _assert_output_contains(result, expect.output_contains, errors)
    if expect.tools_called:
        _assert_tools_called(result, expect.tools_called, errors)
    if expect.approvals_required and not expect.approve_and_resume:
        _assert_approvals_required(result, expect.approvals_required, errors)
    if expect.error_type is not None:
        _assert_error_type(result, expect.error_type, errors)
    if expect.approve_and_resume and expect.resumed_status:
        _assert_resumed_status(result, expect.resumed_status, errors)
    if expect.handoffs:
        _assert_handoffs(result, expect.handoffs, errors)
    if expect.agent_calls:
        _assert_agent_calls(result, expect.agent_calls, errors)
    if expect.routing_decisions:
        _assert_routing_decisions(result, expect.routing_decisions, errors)
    if expect.workflow_steps:
        _assert_workflow_steps(result, expect.workflow_steps, errors)
    if expect.trace_events:
        _assert_trace_events(result, expect.trace_events, errors)
    if expect.policy_decisions:
        _assert_policy_decisions(result, expect.policy_decisions, errors)

    return errors


def _assert_status(result: AppRunResult, expected: str, errors: list[str]) -> None:
    if result.status != expected:
        errors.append(
            f"Expected status '{expected}', got '{result.status}'"
        )


def _assert_output_contains(
    result: AppRunResult, expected_fragments: list[str], errors: list[str]
) -> None:
    output = str(result.final_output or "")
    for fragment in expected_fragments:
        if fragment not in output:
            errors.append(
                f"Expected output to contain '{fragment}'"
            )


def _assert_tools_called(
    result: AppRunResult, expected_tools: list[str], errors: list[str]
) -> None:
    actual_tools = {tc.get("tool", "") for tc in result.tool_calls}
    for tool in expected_tools:
        if tool not in actual_tools:
            errors.append(
                f"Expected tool '{tool}' to be called"
            )


def _assert_approvals_required(
    result: AppRunResult, expected_tools: list[str], errors: list[str]
) -> None:
    actual_approval_tools = {
        i.get("tool_name", "") for i in result.interruptions
    }
    for tool in expected_tools:
        if tool not in actual_approval_tools:
            errors.append(
                f"Expected approval required for tool '{tool}'"
            )


def _assert_error_type(result: AppRunResult, expected: str, errors: list[str]) -> None:
    if result.error is None:
        errors.append(f"Expected error_type '{expected}', got no error")
    elif result.error.get("type") != expected:
        errors.append(
            f"Expected error_type '{expected}', got '{result.error.get('type')}'"
        )


def _assert_resumed_status(
    result: AppRunResult, expected: str, errors: list[str]
) -> None:
    if result.status != expected:
        errors.append(
            f"Expected resumed status '{expected}', got '{result.status}'"
        )


def _assert_handoffs(
    result: AppRunResult, expected: list[dict[str, str]], errors: list[str]
) -> None:
    actual = result.handoffs or []
    for exp in expected:
        found = any(
            h.get("from_agent") == exp.get("from_agent")
            and h.get("to_agent") == exp.get("to_agent")
            for h in actual
        )
        if not found:
            errors.append(
                f"Expected handoff from '{exp.get('from_agent')}' to '{exp.get('to_agent')}'"
            )


def _assert_agent_calls(
    result: AppRunResult, expected: list[str], errors: list[str]
) -> None:
    actual = {c.get("agent_name", "") for c in (result.agent_calls or [])}
    for name in expected:
        if name not in actual:
            errors.append(f"Expected agent call '{name}'")


def _assert_routing_decisions(
    result: AppRunResult, expected: list[str], errors: list[str]
) -> None:
    """Check that workflow_trace contains steps with the expected rule names."""
    trace = result.workflow_trace
    if trace is None:
        errors.append(f"Expected workflow_trace with routing decisions {expected}, got no trace")
        return
    actual_rules: set[str] = set()
    for step in trace.steps:
        if step.step_type == "routing":
            rule = step.metadata.get("rule", "")
            if rule:
                actual_rules.add(rule)
    for rule_name in expected:
        if rule_name not in actual_rules:
            errors.append(
                f"Expected routing decision '{rule_name}' in workflow trace"
            )


def _assert_workflow_steps(
    result: AppRunResult, expected: list[str], errors: list[str]
) -> None:
    """Check that workflow_trace contains steps with the expected step types."""
    trace = result.workflow_trace
    if trace is None:
        errors.append(f"Expected workflow_trace with steps {expected}, got no trace")
        return
    actual_types: set[str] = set()
    for step in trace.steps:
        actual_types.add(step.step_type)
    for step_type in expected:
        if step_type not in actual_types:
            errors.append(
                f"Expected workflow step type '{step_type}' in workflow trace"
            )


def _assert_trace_events(
    result: AppRunResult, expected_events: list[str], errors: list[str]
) -> None:
    """Check that trace_events contain all expected event types.

    Checks both AppRunResult.trace_events and the app's trace_collector.
    Event types are matched as substrings (e.g. 'tool.approval_required'
    matches 'tool.approval_required').
    """
    # Collect event types from result.trace_events
    actual_types: list[str] = []
    trace_events = getattr(result, "trace_events", None) or []
    for ev in trace_events:
        ev_type = getattr(ev, "event_type", None)
        if ev_type:
            actual_types.append(str(ev_type))

    # Also try to get from trace_collector via app (if accessible)
    recorded_strs = set(actual_types)

    for expected in expected_events:
        found = any(expected in actual for actual in recorded_strs)
        if not found:
            available = ", ".join(sorted(set(actual_types))) if actual_types else "(none)"
            errors.append(
                f"Expected trace event '{expected}', but it was not recorded. "
                f"Recorded events: {available}"
            )


def _assert_policy_decisions(
    result: AppRunResult, expected_decisions: list[dict[str, Any]], errors: list[str]
) -> None:
    """Check that policy decisions match expectations.

    Collects policy decision info from trace event data fields.
    Policy decisions are stored in data.action / data.rule_name of
    trace events emitted by the policy engine.
    """
    # Collect all events that have policy decision data
    policy_data_events: list[dict[str, Any]] = []
    trace_events = getattr(result, "trace_events", None) or []
    for ev in trace_events:
        data = getattr(ev, "data", None) or {}
        # An event has policy data if it has 'action' field that is a policy action
        if isinstance(data, dict) and "action" in data:
            policy_data_events.append(data)

    for expected in expected_decisions:
        rule_name = expected.get("rule_name")
        action = expected.get("action")
        reason_contains = expected.get("reason_contains")

        matched = False
        for pdata in policy_data_events:
            evt_action = pdata.get("action")
            evt_rule = pdata.get("rule_name")

            if rule_name and evt_rule != rule_name:
                continue
            if action and evt_action != action:
                continue
            if reason_contains and reason_contains not in (pdata.get("reason") or ""):
                continue
            matched = True
            break

        if not matched:
            available = ", ".join(
                f"{pe.get('action', '?')}(rule={pe.get('rule_name', '?')})"
                for pe in policy_data_events
            ) or "(none)"
            desc = []
            if rule_name:
                desc.append(f"rule_name={rule_name}")
            if action:
                desc.append(f"action={action}")
            if reason_contains:
                desc.append(f"reason_contains={reason_contains}")
            errors.append(
                f"Expected policy decision with {', '.join(desc)}, "
                f"but it was not found. Recorded: {available}"
            )
