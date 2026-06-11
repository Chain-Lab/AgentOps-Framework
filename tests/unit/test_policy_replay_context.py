"""Tests for policy replay context builder."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from agent_app.governance.policy import (
    PolicyAction,
    PolicyDecisionTrace,
)
from agent_app.governance.policy_replay_context import (
    PolicyReplayContext,
    PolicyReplayContextBuilder,
)


def _make_trace(
    decision_id: str = "dec_1",
    tool_name: str | None = "refund.request",
    user_id: str | None = None,
    tenant_id: str | None = None,
    context: dict | None = None,
) -> PolicyDecisionTrace:
    """Create a test decision trace."""
    return PolicyDecisionTrace(
        decision_id=decision_id,
        run_id="run_1",
        rule_name=None,
        action=PolicyAction.ALLOW,
        reason="test",
        tool_name=tool_name,
        matched_conditions={},
        context_summary=context or {
            "tool_name": tool_name,
            "agent_name": "refund",
            "tenant_id": tenant_id,
            "user_id": user_id,
            "roles": ["support"],
            "permissions": ["refund.read"],
            "tool_arguments": {"amount": 100},
        },
        created_at=datetime.now(timezone.utc),
    )


class TestPolicyReplayContextBuilder:
    """Tests for PolicyReplayContextBuilder."""

    def _make_builder(self) -> PolicyReplayContextBuilder:
        return PolicyReplayContextBuilder()

    def test_builds_context_from_decision_record(self):
        """Builds context from a complete decision record."""
        builder = self._make_builder()
        trace = _make_trace(
            tool_name="refund.request",
            user_id="user_1",
            tenant_id="tenant_a",
            context={
                "tool_name": "refund.request",
                "agent_name": "refund",
                "tenant_id": "tenant_a",
                "user_id": "user_1",
                "roles": ["support"],
                "permissions": ["refund.read", "refund.write"],
                "tool_arguments": {"amount": 100},
            },
        )
        ctx = builder.build(trace)

        assert ctx.decision_id == "dec_1"
        assert ctx.tool_name == "refund.request"
        assert ctx.user_id == "user_1"
        assert ctx.tenant_id == "tenant_a"
        assert ctx.roles == ["support"]
        assert ctx.permissions == ["refund.read", "refund.write"]
        assert ctx.tool_arguments == {"amount": 100}
        assert ctx.source == "decision_record"

    def test_records_missing_permissions(self):
        """Records missing permissions in missing_fields."""
        builder = self._make_builder()
        trace = _make_trace(
            tool_name="refund.request",
            context={
                "tool_name": "refund.request",
                "agent_name": "refund",
                # No permissions key
            },
        )
        ctx = builder.build(trace)
        assert "permissions" in ctx.missing_fields
        assert ctx.permissions == []

    def test_records_missing_tool_name(self):
        """Records missing tool_name as critical missing field."""
        builder = self._make_builder()
        trace = _make_trace(
            tool_name=None,
            context={
                "agent_name": "refund",
                # No tool_name
            },
        )
        ctx = builder.build(trace)
        assert "tool_name" in ctx.missing_fields
        assert ctx.tool_name is None

    def test_records_missing_user_id(self):
        """Records missing user_id."""
        builder = self._make_builder()
        trace = _make_trace(
            user_id=None,
            context={
                "tool_name": "refund.request",
                "agent_name": "refund",
                # No user_id
            },
        )
        ctx = builder.build(trace)
        assert "user_id" in ctx.missing_fields

    def test_records_missing_tenant_id(self):
        """Records missing tenant_id."""
        builder = self._make_builder()
        trace = _make_trace(
            tenant_id=None,
            context={
                "tool_name": "refund.request",
                "agent_name": "refund",
                # No tenant_id
            },
        )
        ctx = builder.build(trace)
        assert "tenant_id" in ctx.missing_fields

    def test_does_not_invent_missing_fields(self):
        """Does not guess or invent missing field values."""
        builder = self._make_builder()
        trace = _make_trace(
            tool_name=None,
            context={
                "agent_name": "refund",
                # Intentionally sparse context
            },
        )
        ctx = builder.build(trace)
        assert ctx.user_id is None
        assert ctx.tenant_id is None
        assert ctx.permissions == []
        assert "tool_name" in ctx.missing_fields
        assert "permissions" in ctx.missing_fields
        assert "user_id" in ctx.missing_fields
        assert "tenant_id" in ctx.missing_fields

    def test_prioritizes_trace_fields_over_context_summary(self):
        """Uses trace field values when both trace and context_summary have values."""
        builder = self._make_builder()
        trace = PolicyDecisionTrace(
            decision_id="dec_pri",
            run_id="run_1",
            rule_name=None,
            action=PolicyAction.ALLOW,
            reason="test",
            tool_name="tool.from_trace",
            matched_conditions={},
            context_summary={
                "tool_name": "tool.from_summary",
                "agent_name": "test",
            },
            created_at=datetime.now(timezone.utc),
        )
        ctx = builder.build(trace)
        # Trace field takes priority
        assert ctx.tool_name == "tool.from_trace"

    def test_falls_back_to_context_summary(self):
        """Uses context_summary when trace fields are None."""
        builder = self._make_builder()
        trace = PolicyDecisionTrace(
            decision_id="dec_fallback",
            run_id="run_1",
            rule_name=None,
            action=PolicyAction.ALLOW,
            reason="test",
            tool_name=None,
            matched_conditions={},
            context_summary={
                "tool_name": "tool.from_summary",
                "agent_name": "test",
                "tenant_id": "tenant_x",
                "roles": ["admin"],
            },
            created_at=datetime.now(timezone.utc),
        )
        ctx = builder.build(trace)
        assert ctx.tool_name == "tool.from_summary"
        assert ctx.tenant_id == "tenant_x"
        assert ctx.roles == ["admin"]

    def test_records_source_metadata(self):
        """Records source metadata in the context."""
        builder = self._make_builder()
        trace = _make_trace(context={
            "tool_name": "refund.request",
            "agent_name": "refund",
            "workflow_name": "customer_support",
            "workflow_type": "sequential",
            "source_agent": "triage",
            "target_agent": "refund",
        })
        ctx = builder.build(trace)
        assert ctx.metadata["agent_name"] == "refund"
        assert ctx.metadata["workflow_name"] == "customer_support"
        assert ctx.metadata["workflow_type"] == "sequential"
        assert ctx.metadata["source_agent"] == "triage"
        assert ctx.metadata["target_agent"] == "refund"

    def test_build_evaluation_context_success(self):
        """build_evaluation_context returns PolicyEvaluationContext when tool_name present."""
        from agent_app.governance.policy import PolicyEvaluationContext

        builder = self._make_builder()
        trace = _make_trace(
            tool_name="refund.request",
            context={
                "tool_name": "refund.request",
                "agent_name": "refund",
                "tenant_id": "t1",
                "roles": ["support"],
                "permissions": ["refund.read"],
            },
        )
        eval_ctx = builder.build_evaluation_context(trace)
        assert eval_ctx is not None
        assert isinstance(eval_ctx, PolicyEvaluationContext)
        assert eval_ctx.tool_name == "refund.request"
        assert eval_ctx.tenant_id == "t1"
        assert eval_ctx.permissions == ["refund.read"]

    def test_build_evaluation_context_returns_none_when_tool_name_missing(self):
        """build_evaluation_context returns None when tool_name is missing."""
        builder = self._make_builder()
        trace = _make_trace(
            tool_name=None,
            context={
                "agent_name": "refund",
            },
        )
        eval_ctx = builder.build_evaluation_context(trace)
        assert eval_ctx is None

    def test_empty_roles_and_permissions(self):
        """Empty roles/permissions are handled gracefully."""
        builder = self._make_builder()
        trace = _make_trace(context={
            "tool_name": "refund.request",
            "agent_name": "refund",
            "roles": [],
            "permissions": [],
        })
        ctx = builder.build(trace)
        assert ctx.roles == []
        assert ctx.permissions == []
        assert "permissions" in ctx.missing_fields
