"""Phase 40: Tests for policy simulation models."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_simulation import (
    PolicySimulationCase,
    PolicySimulationOutcome,
    PolicySimulationReport,
    PolicySimulationResult,
    PolicySimulationSummary,
)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestPolicySimulationOutcome:
    def test_enum_values(self):
        assert PolicySimulationOutcome.UNCHANGED == "unchanged"
        assert PolicySimulationOutcome.WOULD_ALLOW == "would_allow"
        assert PolicySimulationOutcome.WOULD_DENY == "would_deny"
        assert PolicySimulationOutcome.WOULD_REQUIRE_APPROVAL == "would_require_approval"
        assert PolicySimulationOutcome.WOULD_CHANGE == "would_change"
        assert PolicySimulationOutcome.ERROR == "error"

    def test_enum_members(self):
        members = list(PolicySimulationOutcome)
        assert len(members) == 6


class TestPolicySimulationCase:
    def test_valid_case(self):
        c = PolicySimulationCase(
            case_id="psc_001",
            action_type="tool.execute",
        )
        assert c.case_id == "psc_001"
        assert c.action_type == "tool.execute"

    def test_case_with_all_fields(self):
        c = PolicySimulationCase(
            case_id="psc_002",
            action_type="tool.execute",
            subject="refund",
            tool_name="payment.refund",
            risk_level="high",
            actor_id="agent_1",
            user_id="user_1",
            tenant_id="tenant_a",
            roles=["admin", "operator"],
            permissions=["payment.refund"],
            baseline_status="denied",
            metadata={"source": "audit"},
        )
        assert c.subject == "refund"
        assert c.tool_name == "payment.refund"
        assert c.risk_level == "high"
        assert c.actor_id == "agent_1"
        assert c.user_id == "user_1"
        assert c.tenant_id == "tenant_a"
        assert c.roles == ["admin", "operator"]
        assert c.permissions == ["payment.refund"]
        assert c.baseline_status == "denied"
        assert c.metadata == {"source": "audit"}

    def test_case_id_psc_prefix(self):
        """case_id uses psc_ prefix convention (not enforced by validator)."""
        c = PolicySimulationCase(case_id="psc_abc123", action_type="tool.execute")
        assert c.case_id.startswith("psc_")

    def test_defaults(self):
        c = PolicySimulationCase(case_id="psc_003", action_type="tool.execute")
        assert c.subject is None
        assert c.tool_name is None
        assert c.risk_level is None
        assert c.actor_id is None
        assert c.user_id is None
        assert c.tenant_id is None
        assert c.roles == []
        assert c.permissions == []
        assert c.baseline_status is None
        assert c.metadata == {}


class TestPolicySimulationResult:
    def test_unchanged_result(self):
        r = PolicySimulationResult(
            case_id="psc_001",
            baseline_status="allowed",
            candidate_status="allowed",
            outcome=PolicySimulationOutcome.UNCHANGED,
        )
        assert r.outcome == PolicySimulationOutcome.UNCHANGED
        assert r.baseline_status == "allowed"
        assert r.candidate_status == "allowed"
        assert r.reason is None
        assert r.errors == []

    def test_would_deny_result(self):
        r = PolicySimulationResult(
            case_id="psc_002",
            baseline_status="allowed",
            candidate_status="denied",
            outcome=PolicySimulationOutcome.WOULD_DENY,
            reason="new rule blocks high-risk tools",
        )
        assert r.outcome == PolicySimulationOutcome.WOULD_DENY
        assert r.reason == "new rule blocks high-risk tools"

    def test_error_result(self):
        r = PolicySimulationResult(
            case_id="psc_003",
            outcome=PolicySimulationOutcome.ERROR,
            errors=["evaluation failed: missing context"],
        )
        assert r.outcome == PolicySimulationOutcome.ERROR
        assert len(r.errors) == 1
        assert "missing context" in r.errors[0]

    def test_decision_id(self):
        r = PolicySimulationResult(
            case_id="psc_004",
            outcome=PolicySimulationOutcome.WOULD_ALLOW,
            decision_id="dec_123",
        )
        assert r.decision_id == "dec_123"


class TestPolicySimulationSummary:
    def test_default_summary(self):
        s = PolicySimulationSummary()
        assert s.total == 0
        assert s.unchanged == 0
        assert s.would_allow == 0
        assert s.would_deny == 0
        assert s.would_require_approval == 0
        assert s.would_change == 0
        assert s.errors == 0

    def test_summary_with_counts(self):
        s = PolicySimulationSummary(
            total=100,
            unchanged=70,
            would_allow=5,
            would_deny=10,
            would_require_approval=3,
            would_change=18,
            errors=2,
        )
        assert s.total == 100
        assert s.unchanged == 70
        assert s.would_allow == 5
        assert s.would_deny == 10
        assert s.would_require_approval == 3
        assert s.would_change == 18
        assert s.errors == 2


class TestPolicySimulationReport:
    def test_valid_report(self):
        r = PolicySimulationReport(
            simulation_id="psim_001",
            generated_at=datetime.now(timezone.utc),
            summary=PolicySimulationSummary(total=1, unchanged=1),
        )
        assert r.simulation_id == "psim_001"
        assert r.summary.total == 1
        assert r.candidate_rule_ids == []
        assert r.results == []
        assert r.metadata == {}

    def test_psim_prefix_validation(self):
        with pytest.raises(ValueError, match="psim_"):
            PolicySimulationReport(
                simulation_id="bad_001",
                generated_at=datetime.now(timezone.utc),
                summary=PolicySimulationSummary(),
            )

    def test_timezone_aware_required(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            PolicySimulationReport(
                simulation_id="psim_002",
                generated_at=datetime.now(),  # naive
                summary=PolicySimulationSummary(),
            )

    def test_optional_fields(self):
        r = PolicySimulationReport(
            simulation_id="psim_003",
            generated_at=datetime.now(timezone.utc),
            summary=PolicySimulationSummary(),
        )
        assert r.name is None
        assert r.candidate_rule_ids == []
        assert r.results == []
        assert r.metadata == {}

    def test_json_serializable(self):
        r = PolicySimulationReport(
            simulation_id="psim_004",
            name="test simulation",
            generated_at=datetime.now(timezone.utc),
            candidate_rule_ids=["rpr_001", "rpr_002"],
            summary=PolicySimulationSummary(
                total=3,
                unchanged=1,
                would_allow=1,
                would_deny=1,
            ),
            results=[
                PolicySimulationResult(
                    case_id="psc_001",
                    baseline_status="allowed",
                    candidate_status="allowed",
                    outcome=PolicySimulationOutcome.UNCHANGED,
                ),
                PolicySimulationResult(
                    case_id="psc_002",
                    baseline_status="denied",
                    candidate_status="allowed",
                    outcome=PolicySimulationOutcome.WOULD_ALLOW,
                ),
                PolicySimulationResult(
                    case_id="psc_003",
                    baseline_status="allowed",
                    candidate_status="denied",
                    outcome=PolicySimulationOutcome.WOULD_DENY,
                    reason="new rule",
                ),
            ],
            metadata={"env": "staging"},
        )
        json_str = r.model_dump_json()
        assert "psim_004" in json_str
        assert "test simulation" in json_str
        # Verify it round-trips cleanly
        data = json.loads(json_str)
        assert data["simulation_id"] == "psim_004"
        assert data["summary"]["total"] == 3
        assert len(data["results"]) == 3
