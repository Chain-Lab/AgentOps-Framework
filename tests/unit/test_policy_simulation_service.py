"""Tests for PolicySimulationService — Phase 40 Task 4.

Tests audit case collection, simulation outcomes, error handling,
window/limit filtering, and the combined simulate_from_audit flow.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from agent_app.core.context import RunContext
from agent_app.governance.audit import AuditEvent, InMemoryAuditLogger
from agent_app.governance.policy_enforcement import PolicyActionType, PolicyDecisionStatus
from agent_app.governance.policy_simulation import (
    PolicySimulationCase,
    PolicySimulationOutcome,
    PolicySimulationReport,
    PolicySimulationResult,
    PolicySimulationSummary,
)
from agent_app.governance.runtime_policy import RuntimePolicyEffect, RuntimePolicyRule, RuntimePolicyRuleStatus
from agent_app.runtime.policy_simulation_service import PolicySimulationService
from agent_app.runtime.runtime_policy_store import InMemoryRuntimePolicyStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_rule(name, effect, **kwargs):
    return RuntimePolicyRule(
        rule_id=f"rpr_{name}",
        name=name,
        action_type=kwargs.get("action_type", PolicyActionType.TOOL_EXECUTE),
        effect=effect,
        tool_name=kwargs.get("tool_name"),
        risk_level=kwargs.get("risk_level"),
        status=kwargs.get("status", RuntimePolicyRuleStatus.ENABLED),
        required_permissions=kwargs.get("required_permissions", []),
        required_roles=kwargs.get("required_roles", []),
    )


def _make_case(case_id, action_type="tool.execute", **kwargs):
    return PolicySimulationCase(
        case_id=case_id,
        action_type=action_type,
        tool_name=kwargs.get("tool_name"),
        risk_level=kwargs.get("risk_level"),
        user_id=kwargs.get("user_id", "user_1"),
        tenant_id=kwargs.get("tenant_id", "tenant_1"),
        roles=kwargs.get("roles", []),
        permissions=kwargs.get("permissions", []),
        baseline_status=kwargs.get("baseline_status", "allowed"),
    )


def _log_enforcement_event(logger, status, **extra_data):
    data = {
        "action_type": "tool.execute",
        "tool_name": "refund.request",
        "user_id": "user_1",
    }
    data.update(extra_data)
    event = AuditEvent(
        event_id=f"evt_{status}",
        event_type=f"policy.runtime.enforcement.{status}",
        data=data,
    )
    _run_async(logger.log(event))


# ===========================================================================
# TestPolicySimulationService
# ===========================================================================

class TestPolicySimulationService:
    """Unit tests for PolicySimulationService."""

    # ------------------------------------------------------------------
    # collect_cases_from_audit
    # ------------------------------------------------------------------

    def test_collect_cases_from_audit(self):
        """Log enforcement events and collect them as simulation cases."""
        logger = InMemoryAuditLogger()
        _log_enforcement_event(logger, "allowed")
        _log_enforcement_event(logger, "denied")

        svc = PolicySimulationService(audit_logger=logger)
        cases = _run_async(svc.collect_cases_from_audit())

        assert len(cases) == 2
        # Cases should preserve baseline_status from event type
        statuses = {c.baseline_status for c in cases}
        assert "allowed" in statuses
        assert "denied" in statuses

    # ------------------------------------------------------------------
    # simulate_cases — unchanged
    # ------------------------------------------------------------------

    def test_simulate_unchanged(self):
        """Candidate rules that don't match produce UNCHANGED outcome."""
        logger = InMemoryAuditLogger()
        svc = PolicySimulationService(audit_logger=logger)

        # Case that was allowed, no candidate rules match
        case = _make_case("psc_1", baseline_status="allowed", tool_name="refund.request")

        # Deny rule for a different tool — won't match
        rules = [_make_rule("deny_other", RuntimePolicyEffect.DENY, tool_name="other_tool")]

        report = _run_async(svc.simulate_cases([case], rules, include_base=False))

        assert len(report.results) == 1
        r = report.results[0]
        assert r.outcome == PolicySimulationOutcome.UNCHANGED
        assert r.baseline_status == "allowed"
        assert r.candidate_status == "allowed"
        assert report.summary.unchanged == 1
        assert report.summary.total == 1

    # ------------------------------------------------------------------
    # simulate_cases — would_deny
    # ------------------------------------------------------------------

    def test_simulate_would_deny(self):
        """Candidate deny rule matching a previously-allowed case → WOULD_DENY."""
        logger = InMemoryAuditLogger()
        svc = PolicySimulationService(audit_logger=logger)

        case = _make_case(
            "psc_2", baseline_status="allowed",
            tool_name="refund.request",
        )

        # Deny rule that matches the case's tool
        rules = [_make_rule("deny_refund", RuntimePolicyEffect.DENY, tool_name="refund.request")]

        report = _run_async(svc.simulate_cases([case], rules, include_base=False))

        assert len(report.results) == 1
        r = report.results[0]
        assert r.outcome == PolicySimulationOutcome.WOULD_DENY
        assert r.candidate_status == "denied"
        assert report.summary.would_deny == 1

    # ------------------------------------------------------------------
    # simulate_cases — would_allow
    # ------------------------------------------------------------------

    def test_simulate_would_allow(self):
        """Candidate allow rule matching a previously-denied case → WOULD_ALLOW."""
        logger = InMemoryAuditLogger()
        svc = PolicySimulationService(audit_logger=logger)

        case = _make_case(
            "psc_3", baseline_status="denied",
            tool_name="refund.request",
            permissions=["refund:create"],
        )

        # Allow rule with matching permission — case has the required permission
        rules = [
            _make_rule(
                "allow_refund", RuntimePolicyEffect.ALLOW,
                tool_name="refund.request",
                required_permissions=["refund:create"],
            ),
        ]

        report = _run_async(svc.simulate_cases([case], rules, include_base=False))

        assert len(report.results) == 1
        r = report.results[0]
        assert r.outcome == PolicySimulationOutcome.WOULD_ALLOW
        assert r.candidate_status == "allowed"
        assert report.summary.would_allow == 1

    # ------------------------------------------------------------------
    # simulate_cases — would_require_approval
    # ------------------------------------------------------------------

    def test_simulate_would_require_approval(self):
        """Candidate require_approval matching previously-allowed case → WOULD_REQUIRE_APPROVAL."""
        logger = InMemoryAuditLogger()
        svc = PolicySimulationService(audit_logger=logger)

        case = _make_case(
            "psc_4", baseline_status="allowed",
            tool_name="refund.request",
            permissions=["refund:create"],
        )

        rules = [
            _make_rule(
                "require_approval_refund", RuntimePolicyEffect.REQUIRE_APPROVAL,
                tool_name="refund.request",
                required_permissions=["refund:create"],
            ),
        ]

        report = _run_async(svc.simulate_cases([case], rules, include_base=False))

        assert len(report.results) == 1
        r = report.results[0]
        assert r.outcome == PolicySimulationOutcome.WOULD_REQUIRE_APPROVAL
        assert r.candidate_status == "approval_required"
        assert report.summary.would_require_approval == 1

    # ------------------------------------------------------------------
    # simulate_cases — errors captured
    # ------------------------------------------------------------------

    def test_simulate_errors_captured(self):
        """Exceptions during evaluation produce ERROR outcome with error details."""
        logger = InMemoryAuditLogger()
        svc = PolicySimulationService(audit_logger=logger)

        # Create a case with an action_type that will be coerced, and
        # inject a faulty evaluator by monkeypatching
        case = _make_case("psc_5", baseline_status="allowed")

        # Use a store whose list() raises an exception
        class BrokenStore(InMemoryRuntimePolicyStore):
            async def list(self, action_type=None, status=None):
                raise RuntimeError("store broken")

        broken = BrokenStore()
        # The broken store will be used when building candidate store via
        # _runtime_policy_store — but build_candidate_policy_store creates
        # a new InMemoryRuntimePolicyStore. We need to inject the break
        # at evaluation time. The evaluator calls policy_store.list().
        # So let's directly test by passing a broken store via the evaluator path.
        # Easiest approach: use a service with a runtime_policy_store whose
        # list() raises, but set include_base=True so it reads base rules.
        # Actually build_candidate_policy_store calls base list first.
        # Let's just test by having the service itself use a broken store
        # and include_base=True.
        svc_broken = PolicySimulationService(
            audit_logger=logger,
            runtime_policy_store=broken,
        )

        rules = [_make_rule("test_rule", RuntimePolicyEffect.DENY, tool_name="refund.request")]

        report = _run_async(svc_broken.simulate_cases([case], rules, include_base=True))

        # The error should be captured per-case
        assert len(report.results) == 1
        r = report.results[0]
        assert r.outcome == PolicySimulationOutcome.ERROR
        assert len(r.errors) > 0
        assert "store broken" in r.errors[0]
        assert report.summary.errors == 1

    # ------------------------------------------------------------------
    # simulate_from_audit
    # ------------------------------------------------------------------

    def test_simulate_from_audit(self):
        """Convenience method combines collection and simulation."""
        logger = InMemoryAuditLogger()
        _log_enforcement_event(logger, "allowed")

        svc = PolicySimulationService(audit_logger=logger)
        rules = [_make_rule("deny_refund", RuntimePolicyEffect.DENY, tool_name="refund.request")]

        report = _run_async(svc.simulate_from_audit(rules, include_base=False))

        assert isinstance(report, PolicySimulationReport)
        assert report.summary.total >= 1
        assert report.simulation_id.startswith("psim_")

    # ------------------------------------------------------------------
    # limit applied
    # ------------------------------------------------------------------

    def test_limit_applied(self):
        """The limit parameter restricts the number of collected cases."""
        logger = InMemoryAuditLogger()
        for i in range(5):
            event = AuditEvent(
                event_id=f"evt_limit_{i}",
                event_type="policy.runtime.enforcement.allowed",
                data={
                    "action_type": "tool.execute",
                    "tool_name": "refund.request",
                    "user_id": "user_1",
                },
            )
            _run_async(logger.log(event))

        svc = PolicySimulationService(audit_logger=logger)
        cases = _run_async(svc.collect_cases_from_audit(limit=2))

        assert len(cases) == 2

    # ------------------------------------------------------------------
    # window filters applied
    # ------------------------------------------------------------------

    def test_window_filters_applied(self):
        """Window start/end filters restrict audit events by timestamp."""
        logger = InMemoryAuditLogger()

        now = datetime.now(timezone.utc)
        before = now - timedelta(hours=2)
        during = now - timedelta(hours=1)
        after = now + timedelta(hours=1)

        # Event before the window
        evt_before = AuditEvent(
            event_id="evt_before",
            event_type="policy.runtime.enforcement.allowed",
            data={"action_type": "tool.execute", "tool_name": "refund.request", "user_id": "u1"},
            created_at=before,
        )
        # Event during the window
        evt_during = AuditEvent(
            event_id="evt_during",
            event_type="policy.runtime.enforcement.denied",
            data={"action_type": "tool.execute", "tool_name": "refund.request", "user_id": "u2"},
            created_at=during,
        )
        # Event after the window
        evt_after = AuditEvent(
            event_id="evt_after",
            event_type="policy.runtime.enforcement.allowed",
            data={"action_type": "tool.execute", "tool_name": "refund.request", "user_id": "u3"},
            created_at=after,
        )

        _run_async(logger.log(evt_before))
        _run_async(logger.log(evt_during))
        _run_async(logger.log(evt_after))

        svc = PolicySimulationService(audit_logger=logger)

        # Window that includes only the "during" event
        window_start = now - timedelta(hours=1, minutes=30)
        window_end = now + timedelta(minutes=30)

        cases = _run_async(svc.collect_cases_from_audit(
            window_start=window_start,
            window_end=window_end,
        ))

        assert len(cases) == 1
        assert cases[0].baseline_status == "denied"
