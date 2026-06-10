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
from agent_app.runtime.tool_executor import ToolExecutionStatus


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
        tenant_id="t1",
        metadata={"sdk_call_id": "call-1"},
    ))
    await _seed_interrupted_run(run_states)

    result = await app.approve_and_resume(
        "apv_1",
        decided_by="admin",
        decision_note="approved",
        tenant_id="t1",
    )

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
        tenant_id="t1",
    ))
    await _seed_interrupted_run(run_states)

    result = await app.reject_approval(
        "apv_1",
        decided_by="admin",
        reason="too risky",
        tenant_id="t1",
    )

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
async def test_approve_and_resume_wrong_tenant_does_not_mutate_or_resume() -> None:
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
        tenant_id="t1",
    ))
    await _seed_interrupted_run(run_states)

    result = await app.approve_and_resume("apv_1", decided_by="admin", tenant_id="t2")

    assert result.status == "failed"
    assert result.error == {
        "type": "approval_forbidden",
        "message": "Approval is not available for this tenant.",
    }
    loaded_approval = await approvals.get("apv_1")
    assert loaded_approval.status == ApprovalStatus.PENDING
    assert backend.resume_calls == []


@pytest.mark.asyncio
async def test_approve_and_resume_run_state_mismatch_does_not_mutate_or_resume() -> None:
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
        tenant_id="t1",
    ))
    await _seed_interrupted_run(run_states, approval_id="apv_other", run_id="run-1")

    result = await app.approve_and_resume("apv_1", decided_by="admin", tenant_id="t1")

    assert result.status == "failed"
    assert result.error == {
        "type": "approval_run_mismatch",
        "message": "Approval is not associated with this interrupted run.",
    }
    loaded_approval = await approvals.get("apv_1")
    assert loaded_approval.status == ApprovalStatus.PENDING
    assert backend.resume_calls == []


@pytest.mark.asyncio
async def test_reject_approval_wrong_tenant_does_not_mutate() -> None:
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
        tenant_id="t1",
    ))
    await _seed_interrupted_run(run_states)

    result = await app.reject_approval("apv_1", decided_by="admin", tenant_id="t2")

    assert result.status == "failed"
    assert result.error == {
        "type": "approval_forbidden",
        "message": "Approval is not available for this tenant.",
    }
    loaded_approval = await approvals.get("apv_1")
    assert loaded_approval.status == ApprovalStatus.PENDING


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


# Phase 20 Task 7: Backward compatibility test

@pytest.mark.asyncio
async def test_approve_and_resume_keeps_existing_resume_method_available() -> None:
    """approve_and_resume() must not break the legacy resume() method."""
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
    await _seed_interrupted_run(run_states)

    resumed = await app.approve_and_resume("apv_1", decided_by="admin")
    legacy = await app.resume("run-1", approval_id="apv_1")

    assert resumed.status == "completed"
    assert legacy.status in {"completed", "failed", "interrupted"}


# ---------------------------------------------------------------------------
# Phase 21: Multi-agent metadata round-trip tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_metadata_roundtrip_through_tool_executor_to_store() -> None:
    """Approval metadata from ToolExecutor survives store persistence and retrieval."""
    from agent_app.governance.approval import InMemoryApprovalStore
    from agent_app.governance.audit import InMemoryAuditLogger
    from agent_app.governance.permission import DefaultPermissionChecker
    from agent_app.governance.risk import RiskLevel
    from agent_app.registry.tool_registry import ToolRegistry
    from agent_app.runtime.tool_executor import ToolExecutor
    from agent_app.core.tool_spec import ToolSpec
    from agent_app.core.context import RunContext

    approvals = InMemoryApprovalStore()
    audit = InMemoryAuditLogger()
    registry = ToolRegistry()
    executor = ToolExecutor(
        tool_registry=registry,
        approval_store=approvals,
        permission_checker=DefaultPermissionChecker(),
        audit_logger=audit,
    )
    spec = ToolSpec(
        name="payment.process",
        description="Process payment",
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
    )
    registry.register("payment.process", spec)
    ctx = RunContext(run_id="run-meta-1", user_id="u1", tenant_id="t1", trace_id="trace-1")
    result = await executor.execute(
        "payment.process",
        {"order_id": "ord-1", "amount": 99.99},
        ctx,
    )
    assert result.status == ToolExecutionStatus.INTERRUPTED.value
    approval = result.approval_request
    assert approval is not None
    assert approval.metadata["argument_keys"] == ["amount", "order_id"]
    assert approval.metadata["requester_context"]["user_id"] == "u1"
    assert approval.metadata["requester_context"]["tenant_id"] == "t1"
    assert approval.metadata["requester_context"]["trace_id"] == "trace-1"
    # Retrieve from store and verify metadata intact
    stored = await approvals.get(approval.approval_id)
    assert stored.metadata == approval.metadata
    assert stored.metadata["argument_keys"] == ["amount", "order_id"]
    assert "secret-token" not in str(stored.metadata)


@pytest.mark.asyncio
async def test_multi_agent_metadata_isolation() -> None:
    """Two agents in the same run have independent approval metadata."""
    from agent_app.governance.approval import InMemoryApprovalStore
    from agent_app.governance.permission import DefaultPermissionChecker
    from agent_app.governance.risk import RiskLevel
    from agent_app.registry.tool_registry import ToolRegistry
    from agent_app.runtime.tool_executor import ToolExecutor
    from agent_app.core.tool_spec import ToolSpec
    from agent_app.core.context import RunContext

    approvals = InMemoryApprovalStore()
    registry = ToolRegistry()
    executor = ToolExecutor(
        tool_registry=registry,
        approval_store=approvals,
        permission_checker=DefaultPermissionChecker(),
        audit_logger=InMemoryAuditLogger(),
    )
    # Agent A tools
    registry.register("billing.charge", ToolSpec(
        name="billing.charge", description="Charge",
        risk_level=RiskLevel.CRITICAL, requires_approval=True,
    ))
    # Agent B tools
    registry.register("shipping.dispatch", ToolSpec(
        name="shipping.dispatch", description="Dispatch",
        risk_level=RiskLevel.HIGH, requires_approval=True,
    ))
    ctx_a = RunContext(run_id="run-multi", user_id="u1", tenant_id="t1", trace_id="t-a")
    ctx_b = RunContext(run_id="run-multi", user_id="u2", tenant_id="t2", trace_id="t-b")
    result_a = await executor.execute("billing.charge", {"amount": 100}, ctx_a)
    result_b = await executor.execute("shipping.dispatch", {"order_id": "ord-1"}, ctx_b)
    assert result_a.status == ToolExecutionStatus.INTERRUPTED.value
    assert result_b.status == ToolExecutionStatus.INTERRUPTED.value
    apv_a = result_a.approval_request
    apv_b = result_b.approval_request
    assert apv_a is not None and apv_b is not None
    # Metadata isolation by context
    assert apv_a.metadata["requester_context"]["tenant_id"] == "t1"
    assert apv_b.metadata["requester_context"]["tenant_id"] == "t2"
    assert apv_a.metadata["requester_context"]["trace_id"] == "t-a"
    assert apv_b.metadata["requester_context"]["trace_id"] == "t-b"
    # Stored approvals are independent
    stored_a = await approvals.get(apv_a.approval_id)
    stored_b = await approvals.get(apv_b.approval_id)
    assert stored_a.tool_name == "billing.charge"
    assert stored_b.tool_name == "shipping.dispatch"
    assert stored_a.approval_id != stored_b.approval_id


@pytest.mark.asyncio
async def test_metadata_roundtrip_sqlite_persistence() -> None:
    """Approval metadata survives SQLite save/load round-trip."""
    import tempfile
    from pathlib import Path
    from agent_app.governance.approval import ApprovalRequest
    from agent_app.runtime.approval_store import SQLiteApprovalStore
    from agent_app.governance.risk import RiskLevel, ApprovalStatus

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Path(tmpdir) / "meta_test.db"
        store = SQLiteApprovalStore(db_path=str(db))
        original_meta = {
            "argument_keys": ["order_id", "amount", "nested", "secret"],
            "requester_context": {"user_id": "u1", "tenant_id": "t1", "trace_id": "trace-99"},
            "sdk_call_id": "call-abc-123",
        }
        req = ApprovalRequest(
            approval_id="apv_meta_rt",
            run_id="run-rt",
            tool_name="payment.charge",
            arguments={"order_id": "ord-1", "amount": 50.0},
            risk_level=RiskLevel.CRITICAL,
            tenant_id="t1",
            metadata=original_meta,
        )
        await store.create(req)
        # Load in a new store instance (fresh connection)
        store2 = SQLiteApprovalStore(db_path=str(db))
        loaded = await store2.get("apv_meta_rt")
        assert loaded.metadata == original_meta
        assert loaded.metadata["argument_keys"] == ["order_id", "amount", "nested", "secret"]
        assert loaded.metadata["requester_context"]["user_id"] == "u1"
        assert loaded.metadata["sdk_call_id"] == "call-abc-123"


@pytest.mark.asyncio
async def test_resume_preserves_approval_metadata_through_store() -> None:
    """Approval metadata survives create→get→approve→get cycle in store."""
    from agent_app.governance.approval import InMemoryApprovalStore
    from agent_app.governance.audit import InMemoryAuditLogger
    from agent_app.runtime.run_state_store import InMemoryRunStateStore
    from agent_app.runtime.approval_resume import ApprovalResumeService
    from agent_app.core.app import AgentApp
    from agent_app.core.agent_spec import AgentSpec

    approvals = InMemoryApprovalStore()
    run_states = InMemoryRunStateStore()
    audit = InMemoryAuditLogger()
    run_id = "run-meta-audit"
    meta = {"sdk_call_id": "call-sdk-1", "argument_keys": ["table"]}
    created = await approvals.create(ApprovalRequest(
        approval_id="apv_audit",
        run_id=run_id,
        tool_name="db.drop",
        risk_level="critical",
        tenant_id="t1",
        metadata=meta,
    ))
    # Metadata preserved after creation
    assert created.metadata == meta
    # Metadata preserved after retrieval
    fetched = await approvals.get("apv_audit")
    assert fetched.metadata == meta
    assert fetched.metadata["sdk_call_id"] == "call-sdk-1"
    # Metadata preserved after approval
    await approvals.approve("apv_audit", approved_by="admin")
    approved = await approvals.get("apv_audit")
    assert approved.status == ApprovalStatus.APPROVED
    assert approved.metadata == meta
    assert approved.resolved_by == "admin"
