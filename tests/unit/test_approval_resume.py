"""Tests for Phase 20 approval decision and resume service."""

from __future__ import annotations

from typing import Any

import pytest

from agent_app.core.agent_spec import AgentSpec
from agent_app.core.app import AgentApp
from agent_app.core.context import RunContext
from agent_app.core.result import AppRunResult
from agent_app.governance.approval import ApprovalRequest, InMemoryApprovalStore
from agent_app.governance.audit import InMemoryAuditLogger
from agent_app.governance.risk import ApprovalStatus
from agent_app.runtime.run_state import InterruptedRun
from agent_app.runtime.run_state_store import InMemoryRunStateStore


class FakeResumeBackend:
    def __init__(self) -> None:
        self.resume_calls: list[dict[str, Any]] = []

    async def run(self, *args: Any, **kwargs: Any) -> AppRunResult:
        return AppRunResult(run_id="unused", status="completed")

    async def stream(self, *args: Any, **kwargs: Any):
        if False:
            yield None

    async def resume(
        self,
        agent_spec: AgentSpec,
        context: RunContext,
        **kwargs: Any,
    ) -> AppRunResult:
        self.resume_calls.append({"agent_spec": agent_spec, "context": context, **kwargs})
        return AppRunResult(
            run_id=context.run_id,
            status="completed",
            final_output="resumed by fake backend",
        )


class FailingResumeBackend(FakeResumeBackend):
    async def resume(
        self,
        agent_spec: AgentSpec,
        context: RunContext,
        **kwargs: Any,
    ) -> AppRunResult:
        raise RuntimeError("secret backend resume failure")


async def _seed_interrupted_run(
    store: InMemoryRunStateStore,
    approval_id: str = "apv_1",
    run_id: str = "run-1",
    backend_state: dict[str, Any] | None = None,
) -> InterruptedRun:
    run = InterruptedRun(
        run_id=run_id,
        agent_name="bot",
        workflow_name=None,
        workflow_type=None,
        input="please do risky thing",
        context=RunContext(run_id=run_id, user_id="u1", tenant_id="t1"),
        interruptions=[{
            "type": "approval_required",
            "approval_id": approval_id,
            "tool_name": "danger.tool",
            "arguments": {"path": "/tmp/file"},
            "risk_level": "high",
            "sdk_call_id": "call-1",
        }],
        approval_ids=[approval_id],
        backend_name="openai",
        backend_state=backend_state if backend_state is not None else {
            "backend": "openai",
            "serialization": "json",
            "value": {"original_input": "please do risky thing"},
        },
    )
    return await store.save_interrupted(run)


@pytest.mark.asyncio
async def test_approve_and_resume_marks_approval_and_calls_backend_resume() -> None:
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
    await approvals.create(ApprovalRequest(
        approval_id="apv_1",
        run_id="run-1",
        tool_name="danger.tool",
        risk_level="high",
        metadata={"sdk_call_id": "call-1"},
    ))
    await _seed_interrupted_run(run_states)

    result = await app.approve_and_resume("apv_1", decided_by="admin", decision_note="approved")

    assert result.status == "completed"
    assert result.final_output == "resumed by fake backend"
    loaded_approval = await approvals.get("apv_1")
    assert loaded_approval.status == ApprovalStatus.APPROVED
    assert loaded_approval.resolved_by == "admin"
    assert len(backend.resume_calls) == 1
    assert backend.resume_calls[0]["approvals"] == [
        {"approval_id": "apv_1", "status": "approved"}
    ]
    event_types = [event.event_type for event in audit.list_events(run_id="run-1")]
    assert "run.resume_requested" in event_types
    assert "run.resumed" in event_types


@pytest.mark.asyncio
async def test_reject_approval_does_not_call_backend_resume() -> None:
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
    await approvals.create(ApprovalRequest(
        approval_id="apv_1",
        run_id="run-1",
        tool_name="danger.tool",
        risk_level="high",
    ))
    await _seed_interrupted_run(run_states)

    result = await app.reject_approval("apv_1", decided_by="admin", reason="too risky")

    assert result.status == "completed"
    assert "rejected" in result.final_output
    assert backend.resume_calls == []
    loaded_approval = await approvals.get("apv_1")
    assert loaded_approval.status == ApprovalStatus.REJECTED
    interrupted = await run_states.get("run-1")
    assert interrupted.status == "completed"
    event_types = [event.event_type for event in audit.list_events(run_id="run-1")]
    assert "approval.rejected" in event_types


@pytest.mark.asyncio
async def test_approve_and_resume_missing_run_state_returns_recoverable_error() -> None:
    approvals = InMemoryApprovalStore()
    run_states = InMemoryRunStateStore()
    backend = FakeResumeBackend()
    app = AgentApp(approval_store=approvals, run_state_store=run_states, backend=backend)
    app.register_agent(AgentSpec(name="bot", instructions="help"))
    await approvals.create(ApprovalRequest(
        approval_id="apv_missing_state",
        run_id="run-missing",
        tool_name="danger.tool",
        risk_level="high",
    ))

    result = await app.approve_and_resume("apv_missing_state", decided_by="admin")

    assert result.status == "failed"
    assert result.error == {
        "type": "run_state_missing",
        "message": "Run state is missing or no longer resumable.",
    }
    assert backend.resume_calls == []


@pytest.mark.asyncio
async def test_approve_and_resume_missing_backend_state_blocks_resume() -> None:
    approvals = InMemoryApprovalStore()
    run_states = InMemoryRunStateStore()
    backend = FakeResumeBackend()
    app = AgentApp(approval_store=approvals, run_state_store=run_states, backend=backend)
    app.register_agent(AgentSpec(name="bot", instructions="help"))
    await approvals.create(ApprovalRequest(
        approval_id="apv_1",
        run_id="run-1",
        tool_name="danger.tool",
        risk_level="high",
    ))
    await _seed_interrupted_run(run_states, backend_state={})

    result = await app.approve_and_resume("apv_1", decided_by="admin")

    assert result.status == "failed"
    assert result.error == {
        "type": "resume_blocked",
        "message": "Run state is missing or no longer resumable.",
    }
    assert backend.resume_calls == []


@pytest.mark.asyncio
async def test_approve_and_resume_sanitizes_backend_resume_exception() -> None:
    approvals = InMemoryApprovalStore()
    run_states = InMemoryRunStateStore()
    backend = FailingResumeBackend()
    app = AgentApp(approval_store=approvals, run_state_store=run_states, backend=backend)
    app.register_agent(AgentSpec(name="bot", instructions="help"))
    await approvals.create(ApprovalRequest(
        approval_id="apv_1",
        run_id="run-1",
        tool_name="danger.tool",
        risk_level="high",
    ))
    await _seed_interrupted_run(run_states)

    result = await app.approve_and_resume("apv_1", decided_by="admin")

    assert result.status == "failed"
    assert result.error == {
        "type": "backend_resume_failed",
        "message": "Backend resume failed; check server logs for details.",
    }
    assert "secret backend resume failure" not in str(result.error)
