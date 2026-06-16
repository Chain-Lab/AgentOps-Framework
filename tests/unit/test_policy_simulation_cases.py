"""Tests for audit-to-simulation case extraction."""
from __future__ import annotations

import pytest

from agent_app.governance.audit import AuditEvent
from agent_app.runtime.policy_simulation_cases import audit_event_to_simulation_case


class TestAuditEventToSimulationCase:
    def test_allowed_event(self):
        event = AuditEvent(
            event_id="evt_1",
            event_type="policy.runtime.enforcement.allowed",
            data={
                "action_type": "tool.execute",
                "tool_name": "refund.request",
                "risk_level": "high",
                "user_id": "user_1",
                "actor_id": "actor_1",
                "tenant_id": "tenant_1",
                "roles": ["admin"],
                "permissions": ["refund:create"],
                "subject": "user_1",
            },
        )
        case = audit_event_to_simulation_case(event)
        assert case is not None
        assert case.action_type == "tool.execute"
        assert case.baseline_status == "allowed"
        assert case.tool_name == "refund.request"
        assert case.risk_level == "high"
        assert case.user_id == "user_1"
        assert case.actor_id == "actor_1"
        assert case.tenant_id == "tenant_1"
        assert case.roles == ["admin"]
        assert case.permissions == ["refund:create"]
        assert case.subject == "user_1"

    def test_denied_event(self):
        event = AuditEvent(
            event_id="evt_2",
            event_type="policy.runtime.enforcement.denied",
            data={"action_type": "tool.execute", "tool_name": "refund.request"},
        )
        case = audit_event_to_simulation_case(event)
        assert case is not None
        assert case.baseline_status == "denied"

    def test_approval_required_event(self):
        event = AuditEvent(
            event_id="evt_3",
            event_type="policy.runtime.enforcement.approval_required",
            data={"action_type": "tool.execute"},
        )
        case = audit_event_to_simulation_case(event)
        assert case is not None
        assert case.baseline_status == "approval_required"

    def test_evaluated_event(self):
        event = AuditEvent(
            event_id="evt_4",
            event_type="policy.runtime.evaluated",
            data={
                "action_type": "tool.execute",
                "status": "allowed",
                "tool_name": "some.tool",
            },
        )
        case = audit_event_to_simulation_case(event)
        assert case is not None
        assert case.baseline_status == "allowed"
        assert case.tool_name == "some.tool"

    def test_unsupported_event_returns_none(self):
        event = AuditEvent(
            event_id="evt_5",
            event_type="recovery.daemon_tick_started",
            data={},
        )
        case = audit_event_to_simulation_case(event)
        assert case is None

    def test_missing_fields_tolerated(self):
        event = AuditEvent(
            event_id="evt_6",
            event_type="policy.runtime.enforcement.allowed",
            data={"action_type": "tool.execute"},
        )
        case = audit_event_to_simulation_case(event)
        assert case is not None
        assert case.tool_name is None
        assert case.risk_level is None
        assert case.user_id is None
        assert case.roles == []
        assert case.permissions == []

    def test_case_id_has_psc_prefix(self):
        event = AuditEvent(
            event_id="evt_7",
            event_type="policy.runtime.enforcement.allowed",
            data={"action_type": "tool.execute"},
        )
        case = audit_event_to_simulation_case(event)
        assert case is not None
        assert case.case_id.startswith("psc_")
