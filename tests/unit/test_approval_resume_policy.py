"""Phase 23: Tests for ApprovalResumeService policy engine integration."""

from __future__ import annotations

import pytest

from agent_app.core.app import AgentApp
from agent_app.core.agent_spec import AgentSpec
from agent_app.core.context import RunContext
from agent_app.core.result import AppRunResult
from agent_app.governance.approval import ApprovalRequest, InMemoryApprovalStore
from agent_app.governance.audit import InMemoryAuditLogger
from agent_app.governance.policy import (
    ConfigurablePolicyEngine,
    DefaultPolicyEngine,
    PolicyAction,
    PolicyEvaluationContext,
)
from agent_app.runtime.approval_resume import ApprovalResumeService
from agent_app.runtime.run_state import InterruptedRun
from agent_app.runtime.run_state_store import InMemoryRunStateStore


class FakeResumeBackend:
    def __init__(self) -> None:
        self.resume_calls: list[dict] = []

    async def run(self, *args, **kwargs):
        from agent_app.core.result import AppRunResult
        return AppRunResult(run_id="unused", status="completed")

    async def stream(self, *args, **kwargs):
        if False:
            yield None

    async def resume(self, agent_spec, context, **kwargs):
        self.resume_calls.append({"agent_spec": agent_spec, "context": context, **kwargs})
        from agent_app.core.result import AppRunResult
        return AppRunResult(run_id=context.run_id, status="completed")


async def _seed_interrupted_run(
    store: InMemoryRunStateStore,
    approval_id: str = "apv_1",
    run_id: str = "run-1",
    tenant_id: str = "t1",
    backend_state: dict | None = None,
) -> InterruptedRun:
    run = InterruptedRun(
        run_id=run_id,
        agent_name="bot",
        workflow_name=None,
        workflow_type=None,
        input="test",
        context=RunContext(run_id=run_id, user_id="u1", tenant_id=tenant_id),
        interruptions=[{
            "type": "approval_required",
            "approval_id": approval_id,
            "tool_name": "danger.tool",
            "arguments": {"path": "/tmp"},
            "risk_level": "high",
            "sdk_call_id": "call-1",
        }],
        approval_ids=[approval_id],
        backend_name="openai",
        backend_state=backend_state if backend_state is not None else {
            "backend": "openai",
            "serialization": "json",
            "value": {"original_input": "test"},
        },
    )
    return await store.save_interrupted(run)


def _make_service(
    policy_engine=None,
    tenant_id="t1",
):
    approvals = InMemoryApprovalStore()
    run_states = InMemoryRunStateStore()
    audit = InMemoryAuditLogger()
    backend = FakeResumeBackend()
    app = AgentApp(
        approval_store=approvals,
        run_state_store=run_states,
        backend=backend,
        audit_logger=audit,
    )
    app.register_agent(AgentSpec(name="bot", instructions="help"))

    service = ApprovalResumeService(
        app=app,
        approval_store=approvals,
        run_state_store=run_states,
        backend=backend,
        agent_registry=app.agent_registry,
        audit_logger=audit,
        policy_engine=policy_engine,
    )
    return service, approvals, run_states, audit, backend, tenant_id


# ---------------------------------------------------------------------------
# Tests: policy deny on resume
# ---------------------------------------------------------------------------


class TestPolicyDenyResume:
    @pytest.mark.asyncio
    async def test_policy_deny_returns_failed(self):
        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "deny_resume",
                "when": {"tool_name": "danger.tool"},
                "then": {"action": "deny", "reason": "No resume allowed"},
            }
        ])
        service, approvals, run_states, audit, backend, tid = _make_service(policy_engine=engine)
        await approvals.create(ApprovalRequest(
            approval_id="apv_1",
            run_id="run-1",
            tool_name="danger.tool",
            risk_level="high",
            tenant_id=tid,
        ))
        await _seed_interrupted_run(run_states, tenant_id=tid)

        result = await service.approve_and_resume(
            "apv_1", decided_by="admin", tenant_id=tid,
        )
        assert result.status == "failed"
        assert result.error is not None
        assert result.error["type"] == "policy_denied"
        assert backend.resume_calls == []

    @pytest.mark.asyncio
    async def test_policy_deny_does_not_call_backend(self):
        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "deny_all_resume",
                "when": {},
                "then": {"action": "deny", "reason": "Blocked"},
            }
        ])
        service, approvals, run_states, audit, backend, tid = _make_service(policy_engine=engine)
        await approvals.create(ApprovalRequest(
            approval_id="apv_1",
            run_id="run-1",
            tool_name="danger.tool",
            risk_level="high",
            tenant_id=tid,
        ))
        await _seed_interrupted_run(run_states, tenant_id=tid)

        result = await service.approve_and_resume(
            "apv_1", decided_by="admin", tenant_id=tid,
        )
        assert result.status == "failed"
        assert backend.resume_calls == []

    @pytest.mark.asyncio
    async def test_policy_deny_writes_audit_event(self):
        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "deny_resume",
                "when": {"tool_name": "danger.tool"},
                "then": {"action": "deny"},
            }
        ])
        service, approvals, run_states, audit, backend, tid = _make_service(policy_engine=engine)
        await approvals.create(ApprovalRequest(
            approval_id="apv_1",
            run_id="run-1",
            tool_name="danger.tool",
            risk_level="high",
            tenant_id=tid,
        ))
        await _seed_interrupted_run(run_states, tenant_id=tid)

        await service.approve_and_resume(
            "apv_1", decided_by="admin", tenant_id=tid,
        )
        events = audit.list_events(event_type="policy.denied")
        assert len(events) >= 1
        assert events[0].approval_id == "apv_1"


# ---------------------------------------------------------------------------
# Tests: policy allow on resume
# ---------------------------------------------------------------------------


class TestPolicyAllowResume:
    @pytest.mark.asyncio
    async def test_policy_allow_resumes_normally(self):
        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "allow_resume",
                "when": {},
                "then": {"action": "allow"},
            }
        ])
        service, approvals, run_states, audit, backend, tid = _make_service(policy_engine=engine)
        await approvals.create(ApprovalRequest(
            approval_id="apv_1",
            run_id="run-1",
            tool_name="danger.tool",
            risk_level="high",
            tenant_id=tid,
        ))
        await _seed_interrupted_run(run_states, tenant_id=tid)

        result = await service.approve_and_resume(
            "apv_1", decided_by="admin", tenant_id=tid,
        )
        assert result.status == "completed"
        assert len(backend.resume_calls) == 1

    @pytest.mark.asyncio
    async def test_policy_evaluated_audit_event_written(self):
        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "allow_resume",
                "when": {},
                "then": {"action": "allow"},
            }
        ])
        service, approvals, run_states, audit, backend, tid = _make_service(policy_engine=engine)
        await approvals.create(ApprovalRequest(
            approval_id="apv_1",
            run_id="run-1",
            tool_name="danger.tool",
            risk_level="high",
            tenant_id=tid,
        ))
        await _seed_interrupted_run(run_states, tenant_id=tid)

        await service.approve_and_resume(
            "apv_1", decided_by="admin", tenant_id=tid,
        )
        events = audit.list_events(event_type="policy.evaluated")
        assert len(events) >= 1


# ---------------------------------------------------------------------------
# Tests: backward compatibility (no policy engine)
# ---------------------------------------------------------------------------


class TestNoPolicyEngineResume:
    @pytest.mark.asyncio
    async def test_no_engine_resumes_normally(self):
        service, approvals, run_states, audit, backend, tid = _make_service(policy_engine=None)
        await approvals.create(ApprovalRequest(
            approval_id="apv_1",
            run_id="run-1",
            tool_name="danger.tool",
            risk_level="high",
            tenant_id=tid,
        ))
        await _seed_interrupted_run(run_states, tenant_id=tid)

        result = await service.approve_and_resume(
            "apv_1", decided_by="admin", tenant_id=tid,
        )
        assert result.status == "completed"
        assert len(backend.resume_calls) == 1

    @pytest.mark.asyncio
    async def test_no_engine_preserves_existing_safety_checks(self):
        """Without policy engine, existing checks (tenant, expiry) still work."""
        service, approvals, run_states, audit, backend, tid = _make_service(policy_engine=None)
        await approvals.create(ApprovalRequest(
            approval_id="apv_1",
            run_id="run-1",
            tool_name="danger.tool",
            risk_level="high",
            tenant_id="t1",
        ))
        await _seed_interrupted_run(run_states, tenant_id="t1")

        # Wrong tenant → blocked
        result = await service.approve_and_resume(
            "apv_1", decided_by="admin", tenant_id="t2",
        )
        assert result.status == "failed"
        assert result.error["type"] == "approval_forbidden"
