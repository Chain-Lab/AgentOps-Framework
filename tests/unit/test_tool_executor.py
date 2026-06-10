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
    async def test_high_risk_requires_approval_even_without_explicit_flag(self) -> None:
        executor, registry, store, _ = _make_executor()
        _register(registry, "refund.issue", spec_kwargs={"risk_level": "high"})
        ctx = RunContext(run_id="r-high", user_id="u1", tenant_id="t1")

        result = await executor.execute("refund.issue", {"order_id": "123"}, ctx)

        assert result.status == ToolExecutionStatus.INTERRUPTED.value
        assert result.approval_request is not None
        assert result.approval_request.status == ApprovalStatus.PENDING
        assert result.approval_request.risk_level == "high"
        pending = await store.list_pending(tenant_id="t1")
        assert [request.tool_name for request in pending] == ["refund.issue"]

    @pytest.mark.asyncio
    async def test_context_metadata_cannot_bypass_high_risk_approval(self) -> None:
        executor, registry, _, _ = _make_executor()
        _register(registry, "refund.issue", spec_kwargs={"risk_level": "high"})
        ctx = RunContext(
            run_id="r-spoofed",
            user_id="u1",
            tenant_id="t1",
            metadata={"approved_tool_calls": ["refund.issue"]},
        )

        result = await executor.execute("refund.issue", {"order_id": "123"}, ctx)

        assert result.status == ToolExecutionStatus.INTERRUPTED.value
        assert result.approval_request is not None

    @pytest.mark.asyncio
    async def test_critical_risk_requires_approval_even_without_explicit_flag(self) -> None:
        executor, registry, _, _ = _make_executor()
        _register(registry, "system.delete", spec_kwargs={"risk_level": "critical"})
        ctx = RunContext(run_id="r-critical", user_id="u1", tenant_id="t1")

        result = await executor.execute("system.delete", {"path": "/tmp/x"}, ctx)

        assert result.status == ToolExecutionStatus.INTERRUPTED.value
        assert result.approval_request is not None
        assert result.approval_request.risk_level == "critical"

    @pytest.mark.asyncio
    async def test_medium_risk_does_not_require_approval_by_default(self) -> None:
        executor, registry, store, _ = _make_executor()
        _register(registry, "order.update_note", spec_kwargs={"risk_level": "medium"})
        ctx = RunContext(run_id="r-medium", user_id="u1", tenant_id="t1")

        result = await executor.execute("order.update_note", {"note": "safe"}, ctx)

        assert result.status == ToolExecutionStatus.COMPLETED.value
        assert await store.list_pending(tenant_id="t1") == []

    @pytest.mark.asyncio
    async def test_requires_approval_overrides_low_risk(self) -> None:
        executor, registry, _, _ = _make_executor()
        _register(
            registry,
            "account.lookup_sensitive",
            spec_kwargs={"risk_level": "low", "requires_approval": True},
        )
        ctx = RunContext(run_id="r-explicit", user_id="u1", tenant_id="t1")

        result = await executor.execute("account.lookup_sensitive", {"account_id": "a1"}, ctx)

        assert result.status == ToolExecutionStatus.INTERRUPTED.value
        assert result.approval_request is not None
        assert result.approval_request.risk_level == "low"

    @pytest.mark.asyncio
    async def test_approval_request_sanitizes_sensitive_arguments(self) -> None:
        executor, registry, _, logger = _make_executor()
        _register(registry, "billing.charge", spec_kwargs={"risk_level": "high"})
        ctx = RunContext(run_id="r-sensitive", user_id="u1", tenant_id="t1")

        result = await executor.execute(
            "billing.charge",
            {
                "amount": 100,
                "api_token": "secret-token-123",
                "nested": {"password": "pw-123"},
            },
            ctx,
        )

        assert result.status == ToolExecutionStatus.INTERRUPTED.value
        approval = result.approval_request
        assert approval is not None
        assert approval.arguments["amount"] == 100
        assert approval.arguments["api_token"] == "[redacted]"
        assert approval.arguments["nested"]["password"] == "[redacted]"
        assert approval.metadata["argument_keys"] == ["amount", "api_token", "nested"]
        assert approval.metadata["requester_context"] == {
            "user_id": "u1",
            "tenant_id": "t1",
            "trace_id": None,
        }
        approval_events = logger.list_events(event_type="tool.approval_required")
        assert len(approval_events) == 1
        assert "secret-token-123" not in str(approval_events[0].data)
        assert "pw-123" not in str(approval_events[0].data)

    @pytest.mark.asyncio
    async def test_high_risk_interception_audit_event_is_emitted(self) -> None:
        executor, registry, _, logger = _make_executor()
        _register(registry, "system.restart", spec_kwargs={"risk_level": "critical"})
        ctx = RunContext(run_id="r-audit", user_id="u1", tenant_id="t1")

        await executor.execute("system.restart", {"host": "app-1"}, ctx)

        events = logger.list_events(run_id="r-audit", event_type="tool.high_risk_intercepted")
        assert len(events) == 1
        assert events[0].tool_name == "system.restart"
        assert events[0].data["risk_level"] == "critical"

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
