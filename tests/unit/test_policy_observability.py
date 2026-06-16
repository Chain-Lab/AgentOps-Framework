"""Phase 39: Tests for policy observability models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_observability import (
    ApprovalLatencySummary,
    PolicyActionSummary,
    PolicyActorSummary,
    PolicyDecisionCount,
    PolicyObservabilityReport,
    PolicyToolSummary,
)


class TestPolicyDecisionCount:
    def test_valid(self):
        dc = PolicyDecisionCount(status="allowed", count=10)
        assert dc.status == "allowed"
        assert dc.count == 10


class TestPolicyActionSummary:
    def test_defaults(self):
        s = PolicyActionSummary(action_type="tool.execute")
        assert s.allowed == 0
        assert s.denied == 0
        assert s.approval_required == 0
        assert s.total == 0

    def test_with_values(self):
        s = PolicyActionSummary(action_type="tool.execute", allowed=5, denied=2, total=7)
        assert s.total == 7


class TestPolicyActorSummary:
    def test_defaults(self):
        s = PolicyActorSummary(actor_id="user_1")
        assert s.allowed == 0
        assert s.total == 0


class TestPolicyToolSummary:
    def test_defaults(self):
        s = PolicyToolSummary(tool_name="refund.request")
        assert s.denied == 0


class TestApprovalLatencySummary:
    def test_valid(self):
        s = ApprovalLatencySummary(count=5, average_seconds=12.5, min_seconds=3.0, max_seconds=30.0)
        assert s.count == 5
        assert s.average_seconds == 12.5


class TestPolicyObservabilityReport:
    def test_valid_report(self):
        r = PolicyObservabilityReport(
            report_id="por_001",
            generated_at=datetime.now(timezone.utc),
        )
        assert r.report_id == "por_001"
        assert r.total_decisions == 0
        assert r.decisions_by_status == []
        assert r.actions == []
        assert r.actors == []
        assert r.tools == []
        assert r.approval_latency is None
        assert r.top_denials == []
        assert r.metadata == {}

    def test_prefix_validation(self):
        with pytest.raises(ValueError, match="por_"):
            PolicyObservabilityReport(
                report_id="bad_001",
                generated_at=datetime.now(timezone.utc),
            )

    def test_tz_aware_required(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            PolicyObservabilityReport(
                report_id="por_002",
                generated_at=datetime.now(),  # naive
            )

    def test_json_serializable(self):
        r = PolicyObservabilityReport(
            report_id="por_003",
            generated_at=datetime.now(timezone.utc),
            total_decisions=10,
            decisions_by_status=[
                PolicyDecisionCount(status="allowed", count=7),
                PolicyDecisionCount(status="denied", count=3),
            ],
            actions=[
                PolicyActionSummary(action_type="tool.execute", allowed=5, denied=3, total=8),
            ],
            actors=[
                PolicyActorSummary(actor_id="user_1", allowed=3, total=3),
            ],
            tools=[
                PolicyToolSummary(tool_name="refund.request", denied=2, total=2),
            ],
            approval_latency=ApprovalLatencySummary(count=2, average_seconds=15.0),
        )
        # Should serialize without error
        json_str = r.model_dump_json()
        assert "por_003" in json_str
        assert "tool.execute" in json_str
