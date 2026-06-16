"""Phase 39: Tests for policy observability models and service."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agent_app.governance.policy_observability import (
    ApprovalLatencySummary,
    PolicyActionSummary,
    PolicyActorSummary,
    PolicyDecisionCount,
    PolicyObservabilityReport,
    PolicyToolSummary,
)
from agent_app.runtime.policy_compliance_export import report_to_csv_rows, report_to_json
from tests.conftest import _run_async


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


class TestPolicyObservabilityService:
    """Tests for PolicyObservabilityService (Phase 39 Task 2)."""

    def test_empty_sources_produce_empty_report(self):
        """No audit logger at all should produce an empty report."""
        from agent_app.runtime.policy_observability_service import (
            PolicyObservabilityService,
        )

        service = PolicyObservabilityService()
        report = _run_async(service.generate_report())
        assert report.total_decisions == 0
        assert report.actions == []
        assert report.actors == []
        assert report.tools == []
        assert report.decisions_by_status == []
        assert report.top_denials == []

    def test_allowed_decision_counted(self):
        """A single allowed enforcement event is counted in the report."""
        from agent_app.governance.audit import AuditEvent, InMemoryAuditLogger
        from agent_app.runtime.policy_observability_service import (
            PolicyObservabilityService,
        )

        audit = InMemoryAuditLogger()
        _run_async(
            audit.log(
                AuditEvent(
                    event_id="ae_allowed1",
                    event_type="policy.runtime.enforcement.allowed",
                    user_id="user_1",
                    tool_name="refund.request",
                    data={"action_type": "tool.execute", "reason": "no_matching_rule"},
                    created_at=datetime.now(timezone.utc),
                )
            )
        )
        service = PolicyObservabilityService(audit_logger=audit)
        report = _run_async(service.generate_report())

        assert report.total_decisions == 1
        assert len(report.actions) == 1
        assert report.actions[0].allowed == 1
        assert report.actions[0].denied == 0
        assert report.actions[0].total == 1
        # Verify decision status breakdown
        allowed_counts = [d for d in report.decisions_by_status if d.status == "allowed"]
        assert len(allowed_counts) == 1
        assert allowed_counts[0].count == 1

    def test_denied_decision_counted(self):
        """A single denied enforcement event is counted in the report."""
        from agent_app.governance.audit import AuditEvent, InMemoryAuditLogger
        from agent_app.runtime.policy_observability_service import (
            PolicyObservabilityService,
        )

        audit = InMemoryAuditLogger()
        _run_async(
            audit.log(
                AuditEvent(
                    event_id="ae_denied1",
                    event_type="policy.runtime.enforcement.denied",
                    user_id="user_2",
                    tool_name="payment.refund",
                    data={
                        "action_type": "tool.execute",
                        "reason": "missing_permission",
                    },
                    created_at=datetime.now(timezone.utc),
                )
            )
        )
        service = PolicyObservabilityService(audit_logger=audit)
        report = _run_async(service.generate_report())

        assert report.total_decisions == 1
        assert report.actions[0].denied == 1
        denied_counts = [d for d in report.decisions_by_status if d.status == "denied"]
        assert len(denied_counts) == 1
        assert denied_counts[0].count == 1

    def test_approval_required_counted(self):
        """An approval_required enforcement event is counted in the report."""
        from agent_app.governance.audit import AuditEvent, InMemoryAuditLogger
        from agent_app.runtime.policy_observability_service import (
            PolicyObservabilityService,
        )

        audit = InMemoryAuditLogger()
        _run_async(
            audit.log(
                AuditEvent(
                    event_id="ae_approval1",
                    event_type="policy.runtime.enforcement.approval_required",
                    user_id="user_3",
                    tool_name="deploy.production",
                    data={"action_type": "tool.execute", "reason": "high_risk"},
                    created_at=datetime.now(timezone.utc),
                )
            )
        )
        service = PolicyObservabilityService(audit_logger=audit)
        report = _run_async(service.generate_report())

        assert report.total_decisions == 1
        assert report.actions[0].approval_required == 1
        ar_counts = [
            d for d in report.decisions_by_status if d.status == "approval_required"
        ]
        assert len(ar_counts) == 1
        assert ar_counts[0].count == 1

    def test_action_summary_works(self):
        """Multiple events grouped by action_type produce correct summaries."""
        from agent_app.governance.audit import AuditEvent, InMemoryAuditLogger
        from agent_app.runtime.policy_observability_service import (
            PolicyObservabilityService,
        )

        audit = InMemoryAuditLogger()
        now = datetime.now(timezone.utc)
        for i in range(3):
            _run_async(
                audit.log(
                    AuditEvent(
                        event_id=f"ae_act_allowed_{i}",
                        event_type="policy.runtime.enforcement.allowed",
                        user_id="user_1",
                        tool_name="order.query",
                        data={"action_type": "tool.execute"},
                        created_at=now + timedelta(seconds=i),
                    )
                )
            )
        _run_async(
            audit.log(
                AuditEvent(
                    event_id="ae_act_denied_1",
                    event_type="policy.runtime.enforcement.denied",
                    user_id="user_2",
                    tool_name="deploy.production",
                    data={"action_type": "workflow.run"},
                    created_at=now + timedelta(seconds=10),
                )
            )
        )

        service = PolicyObservabilityService(audit_logger=audit)
        actions = _run_async(service.summarize_enforcement_decisions())

        assert len(actions) == 2
        by_type = {a.action_type: a for a in actions}
        assert by_type["tool.execute"].allowed == 3
        assert by_type["tool.execute"].total == 3
        assert by_type["workflow.run"].denied == 1
        assert by_type["workflow.run"].total == 1

    def test_actor_summary_works(self):
        """Events grouped by user_id produce correct actor summaries."""
        from agent_app.governance.audit import AuditEvent, InMemoryAuditLogger
        from agent_app.runtime.policy_observability_service import (
            PolicyObservabilityService,
        )

        audit = InMemoryAuditLogger()
        now = datetime.now(timezone.utc)
        # user_1: 2 allowed, 1 denied
        for i in range(2):
            _run_async(
                audit.log(
                    AuditEvent(
                        event_id=f"ae_actor_a_{i}",
                        event_type="policy.runtime.enforcement.allowed",
                        user_id="user_1",
                        tool_name="order.query",
                        data={"action_type": "tool.execute"},
                        created_at=now + timedelta(seconds=i),
                    )
                )
            )
        _run_async(
            audit.log(
                AuditEvent(
                    event_id="ae_actor_d_1",
                    event_type="policy.runtime.enforcement.denied",
                    user_id="user_1",
                    tool_name="payment.refund",
                    data={"action_type": "tool.execute"},
                    created_at=now + timedelta(seconds=5),
                )
            )
        )
        # user_2: 1 approval_required
        _run_async(
            audit.log(
                AuditEvent(
                    event_id="ae_actor_ar_1",
                    event_type="policy.runtime.enforcement.approval_required",
                    user_id="user_2",
                    tool_name="deploy.production",
                    data={"action_type": "tool.execute"},
                    created_at=now + timedelta(seconds=10),
                )
            )
        )

        service = PolicyObservabilityService(audit_logger=audit)
        actors = _run_async(service.summarize_actors())

        assert len(actors) == 2
        by_actor = {a.actor_id: a for a in actors}
        assert by_actor["user_1"].allowed == 2
        assert by_actor["user_1"].denied == 1
        assert by_actor["user_1"].total == 3
        assert by_actor["user_2"].approval_required == 1
        assert by_actor["user_2"].total == 1

    def test_tool_summary_works(self):
        """Events grouped by tool_name produce correct tool summaries."""
        from agent_app.governance.audit import AuditEvent, InMemoryAuditLogger
        from agent_app.runtime.policy_observability_service import (
            PolicyObservabilityService,
        )

        audit = InMemoryAuditLogger()
        now = datetime.now(timezone.utc)
        _run_async(
            audit.log(
                AuditEvent(
                    event_id="ae_tool_a1",
                    event_type="policy.runtime.enforcement.allowed",
                    user_id="user_1",
                    tool_name="order.query",
                    data={"action_type": "tool.execute"},
                    created_at=now,
                )
            )
        )
        _run_async(
            audit.log(
                AuditEvent(
                    event_id="ae_tool_d1",
                    event_type="policy.runtime.enforcement.denied",
                    user_id="user_2",
                    tool_name="payment.refund",
                    data={"action_type": "tool.execute"},
                    created_at=now + timedelta(seconds=1),
                )
            )
        )
        _run_async(
            audit.log(
                AuditEvent(
                    event_id="ae_tool_d2",
                    event_type="policy.runtime.enforcement.denied",
                    user_id="user_3",
                    tool_name="payment.refund",
                    data={"action_type": "tool.execute"},
                    created_at=now + timedelta(seconds=2),
                )
            )
        )

        service = PolicyObservabilityService(audit_logger=audit)
        tools = _run_async(service.summarize_tools())

        assert len(tools) == 2
        by_tool = {t.tool_name: t for t in tools}
        assert by_tool["order.query"].allowed == 1
        assert by_tool["payment.refund"].denied == 2
        assert by_tool["payment.refund"].total == 2

    def test_top_denials_generated(self):
        """Denied events are grouped by reason for top denials."""
        from agent_app.governance.audit import AuditEvent, InMemoryAuditLogger
        from agent_app.runtime.policy_observability_service import (
            PolicyObservabilityService,
        )

        audit = InMemoryAuditLogger()
        now = datetime.now(timezone.utc)
        # 3 denied with reason "missing_permission", 1 with "high_risk"
        for i in range(3):
            _run_async(
                audit.log(
                    AuditEvent(
                        event_id=f"ae_top_d_{i}",
                        event_type="policy.runtime.enforcement.denied",
                        user_id=f"user_{i}",
                        tool_name="payment.refund",
                        data={"action_type": "tool.execute", "reason": "missing_permission"},
                        created_at=now + timedelta(seconds=i),
                    )
                )
            )
        _run_async(
            audit.log(
                AuditEvent(
                    event_id="ae_top_d_hr",
                    event_type="policy.runtime.enforcement.denied",
                    user_id="user_99",
                    tool_name="deploy.production",
                    data={"action_type": "tool.execute", "reason": "high_risk"},
                    created_at=now + timedelta(seconds=10),
                )
            )
        )

        service = PolicyObservabilityService(audit_logger=audit)
        top_denials = _run_async(service._top_denials())

        assert len(top_denials) == 2
        # First should be "missing_permission" with count 3
        assert top_denials[0]["reason"] == "missing_permission"
        assert top_denials[0]["count"] == 3
        assert top_denials[1]["reason"] == "high_risk"
        assert top_denials[1]["count"] == 1

    def test_window_filter_works(self):
        """Events outside the time window are excluded from the report."""
        from agent_app.governance.audit import AuditEvent, InMemoryAuditLogger
        from agent_app.runtime.policy_observability_service import (
            PolicyObservabilityService,
        )

        audit = InMemoryAuditLogger()
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        # Event inside window
        _run_async(
            audit.log(
                AuditEvent(
                    event_id="ae_win_in",
                    event_type="policy.runtime.enforcement.allowed",
                    user_id="user_1",
                    tool_name="order.query",
                    data={"action_type": "tool.execute"},
                    created_at=t0 + timedelta(minutes=30),
                )
            )
        )
        # Event outside window (before start)
        _run_async(
            audit.log(
                AuditEvent(
                    event_id="ae_win_out_before",
                    event_type="policy.runtime.enforcement.denied",
                    user_id="user_2",
                    tool_name="payment.refund",
                    data={"action_type": "tool.execute"},
                    created_at=t0 - timedelta(hours=1),
                )
            )
        )
        # Event outside window (after end)
        _run_async(
            audit.log(
                AuditEvent(
                    event_id="ae_win_out_after",
                    event_type="policy.runtime.enforcement.denied",
                    user_id="user_3",
                    tool_name="deploy.production",
                    data={"action_type": "tool.execute"},
                    created_at=t0 + timedelta(hours=2),
                )
            )
        )

        service = PolicyObservabilityService(audit_logger=audit)
        report = _run_async(
            service.generate_report(
                window_start=t0,
                window_end=t0 + timedelta(hours=1),
            )
        )

        # Only the one allowed event should be counted
        assert report.total_decisions == 1
        assert report.actions[0].allowed == 1
        assert report.window_start == t0
        assert report.window_end == t0 + timedelta(hours=1)

    def test_missing_audit_logger_partial_report(self):
        """None audit logger produces a partial report with empty summaries."""
        from agent_app.runtime.policy_observability_service import (
            PolicyObservabilityService,
        )

        service = PolicyObservabilityService(audit_logger=None)
        report = _run_async(service.generate_report())

        assert report.total_decisions == 0
        assert report.actions == []
        assert report.actors == []
        assert report.tools == []
        assert report.top_denials == []
        # report_id should still have the por_ prefix
        assert report.report_id.startswith("por_")

    def test_approval_latency_from_store(self):
        """Approval store with resolved approvals computes latency correctly."""
        from datetime import datetime, timedelta, timezone

        from agent_app.runtime.policy_observability_service import (
            PolicyObservabilityService,
        )

        # Create a mock approval store with resolved approvals
        class MockApproval:
            def __init__(self, created_at, resolved_at):
                self.created_at = created_at
                self.resolved_at = resolved_at

        class MockApprovalStore:
            def __init__(self, approvals):
                self._approvals = approvals

            async def list(self, status=None, rollout_id=None):
                return self._approvals

        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        approvals = [
            MockApproval(created_at=t0, resolved_at=t0 + timedelta(seconds=60)),
            MockApproval(created_at=t0, resolved_at=t0 + timedelta(seconds=120)),
            MockApproval(created_at=t0, resolved_at=t0 + timedelta(seconds=30)),
        ]
        store = MockApprovalStore(approvals)
        service = PolicyObservabilityService(rollout_approval_store=store)
        latency = _run_async(service.approval_latency_summary())

        assert latency.count == 3
        assert latency.min_seconds == 30.0
        assert latency.max_seconds == 120.0
        assert latency.average_seconds == 70.0


class TestComplianceExport:
    """Tests for compliance export helpers (Phase 39 Task 3)."""

    def test_json_export_works(self):
        """report_to_json returns valid JSON string with report_id."""
        report = PolicyObservabilityReport(
            report_id="por_exp1",
            generated_at=datetime.now(timezone.utc),
            total_decisions=5,
        )
        json_str = report_to_json(report)
        data = json.loads(json_str)
        assert data["report_id"] == "por_exp1"
        assert data["total_decisions"] == 5

    def test_csv_rows_include_action_summaries(self):
        """CSV rows contain action section entries."""
        report = PolicyObservabilityReport(
            report_id="por_exp2",
            generated_at=datetime.now(timezone.utc),
            actions=[
                PolicyActionSummary(
                    action_type="tool.execute",
                    allowed=3,
                    denied=1,
                    approval_required=1,
                    total=5,
                ),
            ],
        )
        rows = report_to_csv_rows(report)
        action_rows = [r for r in rows if r["section"] == "action"]
        assert len(action_rows) == 1
        assert action_rows[0]["key"] == "tool.execute"
        assert action_rows[0]["allowed"] == 3
        assert action_rows[0]["denied"] == 1
        assert action_rows[0]["approval_required"] == 1
        assert action_rows[0]["total"] == 5

    def test_csv_rows_include_actor_summaries(self):
        """CSV rows contain actor section entries."""
        report = PolicyObservabilityReport(
            report_id="por_exp3",
            generated_at=datetime.now(timezone.utc),
            actors=[
                PolicyActorSummary(
                    actor_id="user_1",
                    allowed=2,
                    denied=1,
                    total=3,
                ),
            ],
        )
        rows = report_to_csv_rows(report)
        actor_rows = [r for r in rows if r["section"] == "actor"]
        assert len(actor_rows) == 1
        assert actor_rows[0]["key"] == "user_1"
        assert actor_rows[0]["allowed"] == 2
        assert actor_rows[0]["denied"] == 1
        assert actor_rows[0]["total"] == 3

    def test_csv_rows_include_tool_summaries(self):
        """CSV rows contain tool section entries."""
        report = PolicyObservabilityReport(
            report_id="por_exp4",
            generated_at=datetime.now(timezone.utc),
            tools=[
                PolicyToolSummary(
                    tool_name="refund.request",
                    denied=2,
                    total=2,
                ),
            ],
        )
        rows = report_to_csv_rows(report)
        tool_rows = [r for r in rows if r["section"] == "tool"]
        assert len(tool_rows) == 1
        assert tool_rows[0]["key"] == "refund.request"
        assert tool_rows[0]["denied"] == 2
        assert tool_rows[0]["total"] == 2

    def test_csv_rows_empty_report(self):
        """Empty report produces empty CSV rows."""
        report = PolicyObservabilityReport(
            report_id="por_exp5",
            generated_at=datetime.now(timezone.utc),
        )
        rows = report_to_csv_rows(report)
        assert rows == []


class TestObservabilityConfig:
    """Tests for PolicyObservabilityConfig (Phase 39 Task 4)."""

    def test_observability_config_defaults(self):
        """PolicyObservabilityConfig defaults to enabled=True."""
        from agent_app.config.schema import PolicyObservabilityConfig

        cfg = PolicyObservabilityConfig()
        assert cfg.enabled is True

    def test_observability_config_disabled(self):
        """PolicyObservabilityConfig can be explicitly disabled."""
        from agent_app.config.schema import PolicyObservabilityConfig

        cfg = PolicyObservabilityConfig(enabled=False)
        assert cfg.enabled is False


class TestObservabilityRBAC:
    """Tests for observability RBAC permissions (Phase 39 Task 4)."""

    def test_observability_view_permission_exists(self):
        """OBSERVABILITY_VIEW is a valid PolicyReleasePermission member."""
        from agent_app.governance.policy_rbac import PolicyReleasePermission

        assert hasattr(PolicyReleasePermission, "OBSERVABILITY_VIEW")
        assert PolicyReleasePermission.OBSERVABILITY_VIEW.value == "policy.observability.view"

    def test_observability_export_permission_exists(self):
        """OBSERVABILITY_EXPORT is a valid PolicyReleasePermission member."""
        from agent_app.governance.policy_rbac import PolicyReleasePermission

        assert hasattr(PolicyReleasePermission, "OBSERVABILITY_EXPORT")
        assert PolicyReleasePermission.OBSERVABILITY_EXPORT.value == "policy.observability.export"

    def test_observability_view_default_allowed(self):
        """OBSERVABILITY_VIEW is in the default-allowed permission set."""
        from agent_app.governance.policy_rbac import (
            PolicyReleasePermission,
            _DEFAULT_ALLOWED,
        )

        assert PolicyReleasePermission.OBSERVABILITY_VIEW in _DEFAULT_ALLOWED


class TestObservabilityEventTypes:
    """Tests for observability change event types (Phase 39 Task 4)."""

    def test_observability_event_types_exist(self):
        """Observability event types are valid PolicyChangeEventType members."""
        from agent_app.governance.policy_change_event import PolicyChangeEventType

        assert hasattr(PolicyChangeEventType, "OBSERVABILITY_REPORT_GENERATED")
        assert PolicyChangeEventType.OBSERVABILITY_REPORT_GENERATED.value == "policy.observability.report_generated"
        assert hasattr(PolicyChangeEventType, "OBSERVABILITY_EXPORT_GENERATED")
        assert PolicyChangeEventType.OBSERVABILITY_EXPORT_GENERATED.value == "policy.observability.export_generated"
        assert hasattr(PolicyChangeEventType, "OBSERVABILITY_EXPORT_FAILED")
        assert PolicyChangeEventType.OBSERVABILITY_EXPORT_FAILED.value == "policy.observability.export_failed"
