"""Tests for ToolExecutor — governance pipeline for tool execution."""

import pytest

from agent_app.core.context import RunContext
from agent_app.core.tool_spec import ToolSpec
from agent_app.governance.approval import ApprovalStatus
from agent_app.governance.audit import AuditEvent, InMemoryAuditLogger
from agent_app.governance.permission import DefaultPermissionChecker
from agent_app.registry.tool_registry import ToolRegistry
from agent_app.runtime.approval_store import InMemoryApprovalStore
from agent_app.runtime.tool_executor import ToolExecutor, ToolExecutionStatus


def _make_executor(allow_perms=True, approval_store=None, audit_logger=None):
    registry = ToolRegistry()
    store = approval_store or InMemoryApprovalStore()
    checker = DefaultPermissionChecker()
    if not allow_perms:
        checker = _DenyAllChecker()
    logger = audit_logger or InMemoryAuditLogger()
    return ToolExecutor(
        tool_registry=registry,
        approval_store=store,
        permission_checker=checker,
        audit_logger=logger,
    ), registry, store, logger


class _DenyAllChecker:
    async def check(self, required_perms, context):
        return False


def _register(registry, name, spec_kwargs=None):
    if spec_kwargs is None:
        spec_kwargs = {}
    spec = ToolSpec(name=name, description=f"Tool {name}", **spec_kwargs)

    async def _fn(**kwargs):
        return {"result": "ok"}

    registry.register(name, spec, fn=_fn)
    return spec


class TestToolExecutor:
    @pytest.mark.asyncio
    async def test_low_risk_completes(self) -> None:
        executor, registry, *_ = _make_executor()
        _register(registry, "order.query")
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")
        result = await executor.execute("order.query", {"order_id": "123"}, ctx)
        assert result.status == ToolExecutionStatus.COMPLETED.value
        assert result.output == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_high_risk_creates_approval(self) -> None:
        executor, registry, store, _ = _make_executor()
        _register(registry, "refund.request", spec_kwargs={
            "risk_level": "high", "requires_approval": True,
        })
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")
        result = await executor.execute("refund.request", {"order_id": "123"}, ctx)
        assert result.status == ToolExecutionStatus.INTERRUPTED.value
        assert result.approval_request is not None
        assert result.approval_request.status == ApprovalStatus.PENDING

    @pytest.mark.asyncio
    async def test_permission_denied(self) -> None:
        executor, registry, _, _ = _make_executor(allow_perms=False)
        _register(registry, "refund.request", spec_kwargs={"permissions": ["refund:create"]})
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")
        result = await executor.execute("refund.request", {"order_id": "123"}, ctx)
        assert result.status == ToolExecutionStatus.FAILED.value
        assert result.error["type"] == "permission_denied"

    @pytest.mark.asyncio
    async def test_permission_allowed_with_perms(self) -> None:
        executor, registry, _, _ = _make_executor()
        _register(registry, "refund.request", spec_kwargs={"permissions": ["refund:create"]})
        ctx = RunContext(
            run_id="r1", user_id="u1", tenant_id="t1",
            permissions=["refund:create"],
        )
        result = await executor.execute("refund.request", {"order_id": "123"}, ctx)
        assert result.status == ToolExecutionStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_unknown_tool_fails(self) -> None:
        executor, registry, *_ = _make_executor()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")
        result = await executor.execute("nonexistent.tool", {}, ctx)
        assert result.status == ToolExecutionStatus.FAILED.value
        assert result.error["type"] == "tool_not_found"

    @pytest.mark.asyncio
    async def test_audit_logs_execution(self) -> None:
        executor, registry, _, logger = _make_executor()
        _register(registry, "order.query")
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")
        await executor.execute("order.query", {"order_id": "123"}, ctx)
        events = logger.list_events(run_id="r1", event_type="tool.executed")
        assert len(events) == 1
        assert events[0].tool_name == "order.query"

    @pytest.mark.asyncio
    async def test_audit_logs_approval(self) -> None:
        executor, registry, _, logger = _make_executor()
        _register(registry, "refund.request", spec_kwargs={
            "risk_level": "high", "requires_approval": True,
        })
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")
        await executor.execute("refund.request", {"order_id": "123"}, ctx)
        events = logger.list_events(event_type="tool.approval_required")
        assert len(events) == 1
        assert events[0].tool_name == "refund.request"

    @pytest.mark.asyncio
    async def test_sync_tool_executes(self) -> None:
        executor, registry, *_ = _make_executor()
        spec = ToolSpec(name="sync.tool", description="Sync")
        counter = [0]

        def sync_fn(x: str):
            counter[0] += 1
            return {"x": x}

        registry.register("sync.tool", spec, fn=sync_fn)
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")
        result = await executor.execute("sync.tool", {"x": "hello"}, ctx)
        assert result.status == ToolExecutionStatus.COMPLETED.value
        assert counter[0] == 1
