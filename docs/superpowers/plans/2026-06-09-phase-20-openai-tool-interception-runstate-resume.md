# Phase 20 OpenAI Tool Interception and RunState Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement safe OpenAI backend real tool interception, approval interruption persistence, and approval-driven RunState resume without coupling core modules to the OpenAI Agents SDK.

**Architecture:** Shared governance code decides when tool calls require approval and sanitizes persisted approval/audit payloads. The OpenAI backend remains the only layer that imports the SDK; it maps SDK interruption call IDs to framework approval IDs and delegates approval decisions through a small runtime service. Existing `ApprovalStore`, `RunStateStore`, `InterruptedRun.backend_state`, audit, trace, CLI, and recovery boundaries stay intact.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, fake `agents` SDK modules in unit tests, existing framework approval/run-state/audit abstractions, optional OpenAI Agents SDK isolated in `agent_app/adapters/openai_agents.py`.

---

## File structure

Phase 20 changes are intentionally small and bounded.

- Modify `agent_app/governance/risk.py`
  - Add `requires_tool_approval()` as the shared risk/approval policy.

- Create `agent_app/governance/sanitization.py`
  - Add conservative argument and error sanitizers used by approval and audit paths.

- Modify `agent_app/governance/approval.py`
  - Extend `ApprovalRequest` with `metadata`, `decision_note`, and `expires_at`.

- Modify `agent_app/runtime/approval_store.py`
  - Persist `ApprovalRequest.metadata`, `decision_note`, and `expires_at` in SQLite with migration-safe column creation.
  - Continue exporting `InMemoryApprovalStore` through this module for compatibility.

- Modify `agent_app/runtime/tool_executor.py`
  - Use `requires_tool_approval()` instead of `spec.requires_approval` alone.
  - Sanitize approval arguments and audit data.
  - Emit high-risk interception and approval-created audit events.

- Create `agent_app/runtime/approval_resume.py`
  - Add `ApprovalResumeService` with `approve_and_resume()` and `reject()` methods.
  - Centralize approval decision, run-state lookup, backend resume delegation, safety checks, audit events, and sanitized user-facing errors.

- Modify `agent_app/core/app.py`
  - Add thin `approve_and_resume()` and `reject_approval()` wrappers that instantiate `ApprovalResumeService`.
  - Keep existing `approve()`, `reject()`, and `resume()` behavior compatible.

- Modify `agent_app/adapters/openai_agents.py`
  - Harden `compile_tool()` approval metadata and native HITL behavior.
  - Persist SDK call IDs in framework interruptions and approval metadata.
  - Resume using `sdk_call_id -> decision` mapping instead of assuming framework approval IDs match SDK call IDs.
  - Sanitize backend run/resume errors.

- Modify tests:
  - `tests/unit/test_tool_executor.py`
  - `tests/unit/test_approval.py`
  - `tests/unit/test_sqlite_approval.py`
  - `tests/unit/test_approval_resume.py` (new)
  - `tests/unit/test_openai_backend.py`
  - `tests/unit/test_native_hitl.py`

- Modify docs:
  - `README.md`
  - `CHANGELOG.md`
  - `docs/release_checklist_v0.10.md`
  - `docs/openai_backend.md` if present and still current.

---

## Constraints for every task

- Use TDD: write the failing test first, run it, then implement the minimal code.
- Do not import `agents` outside `agent_app/adapters/openai_agents.py` or test fake SDK setup.
- Do not require `OPENAI_API_KEY` for default tests.
- Do not modify recovery UI behavior.
- Do not change dry-run defaults.
- Do not change daemon default-off behavior.
- Keep commits scoped to the task files listed in that task.
- Use the project venv for tests:

```bash
.venv/bin/python -m pytest <target> -q
```

---

### Task 1: Shared approval policy for tool risk

**Files:**
- Modify: `agent_app/governance/risk.py`
- Modify: `agent_app/runtime/tool_executor.py`
- Test: `tests/unit/test_tool_executor.py`

- [ ] **Step 1: Append failing ToolExecutor policy tests**

Add these tests inside `class TestToolExecutor` in `tests/unit/test_tool_executor.py`:

```python
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
```

- [ ] **Step 2: Run the policy tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_tool_executor.py::TestToolExecutor::test_high_risk_requires_approval_even_without_explicit_flag tests/unit/test_tool_executor.py::TestToolExecutor::test_critical_risk_requires_approval_even_without_explicit_flag tests/unit/test_tool_executor.py::TestToolExecutor::test_medium_risk_does_not_require_approval_by_default tests/unit/test_tool_executor.py::TestToolExecutor::test_requires_approval_overrides_low_risk -q
```

Expected before implementation:

- The high-risk test fails because high risk without `requires_approval=True` currently completes.
- The critical-risk test fails for the same reason.
- The medium and explicit-approval tests should either pass already or remain useful regression checks.

- [ ] **Step 3: Add shared policy helper**

Modify `agent_app/governance/risk.py` to include this function after `ApprovalStatus`:

```python

def requires_tool_approval(
    risk_level: str | RiskLevel,
    requires_approval: bool = False,
) -> bool:
    """Return True when a tool call must pause for human approval."""
    if requires_approval:
        return True
    normalized = str(risk_level).lower()
    return normalized in {RiskLevel.HIGH.value, RiskLevel.CRITICAL.value}
```

- [ ] **Step 4: Use policy in ToolExecutor**

Modify `agent_app/runtime/tool_executor.py` imports near existing governance imports:

```python
from agent_app.governance.risk import requires_tool_approval
```

Replace the approval gate condition:

```python
        if spec.requires_approval:
```

with:

```python
        if requires_tool_approval(spec.risk_level, spec.requires_approval):
```

- [ ] **Step 5: Run ToolExecutor tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_tool_executor.py -q
```

Expected after implementation:

- All `test_tool_executor.py` tests pass.

- [ ] **Step 6: Commit Task 1**

Run:

```bash
git add agent_app/governance/risk.py agent_app/runtime/tool_executor.py tests/unit/test_tool_executor.py
git commit -m "feat: require approval for high-risk tools"
```

---

### Task 2: Approval metadata and sanitization

**Files:**
- Create: `agent_app/governance/sanitization.py`
- Modify: `agent_app/governance/approval.py`
- Modify: `agent_app/runtime/approval_store.py`
- Modify: `agent_app/runtime/tool_executor.py`
- Test: `tests/unit/test_tool_executor.py`
- Test: `tests/unit/test_approval.py`
- Test: `tests/unit/test_sqlite_approval.py`

- [ ] **Step 1: Add failing sanitization and metadata tests for ToolExecutor**

Append these tests inside `class TestToolExecutor` in `tests/unit/test_tool_executor.py`:

```python
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
```

- [ ] **Step 2: Add failing ApprovalRequest model tests**

Append to `tests/unit/test_approval.py`:

```python

def test_approval_request_accepts_metadata_decision_note_and_expiry() -> None:
    from datetime import datetime, timezone

    from agent_app.governance.approval import ApprovalRequest

    expires_at = datetime(2026, 6, 9, 12, 30, tzinfo=timezone.utc)
    request = ApprovalRequest(
        approval_id="apv_meta",
        run_id="run-1",
        tool_name="billing.charge",
        metadata={"sdk_call_id": "call-1"},
        decision_note="checked in admin console",
        expires_at=expires_at,
    )

    assert request.metadata == {"sdk_call_id": "call-1"}
    assert request.decision_note == "checked in admin console"
    assert request.expires_at == expires_at
```

- [ ] **Step 3: Add failing SQLite metadata persistence test**

Append to `tests/unit/test_sqlite_approval.py`:

```python
@pytest.mark.asyncio
async def test_sqlite_approval_store_persists_metadata_decision_note_and_expiry(tmp_path) -> None:
    from datetime import datetime, timezone

    from agent_app.governance.approval import ApprovalRequest
    from agent_app.runtime.approval_store import SQLiteApprovalStore

    db_path = tmp_path / "approvals.db"
    store = SQLiteApprovalStore(str(db_path))
    expires_at = datetime(2026, 6, 9, 12, 30, tzinfo=timezone.utc)
    request = ApprovalRequest(
        approval_id="apv_sql_meta",
        run_id="run-1",
        tool_name="billing.charge",
        arguments={"api_token": "[redacted]"},
        metadata={"sdk_call_id": "call-1", "argument_keys": ["api_token"]},
        decision_note="reviewed",
        expires_at=expires_at,
    )

    await store.create(request)
    loaded = await store.get("apv_sql_meta")

    assert loaded.metadata == {"sdk_call_id": "call-1", "argument_keys": ["api_token"]}
    assert loaded.decision_note == "reviewed"
    assert loaded.expires_at == expires_at
    store.close()
```

- [ ] **Step 4: Run new tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_tool_executor.py::TestToolExecutor::test_approval_request_sanitizes_sensitive_arguments tests/unit/test_tool_executor.py::TestToolExecutor::test_high_risk_interception_audit_event_is_emitted tests/unit/test_approval.py::test_approval_request_accepts_metadata_decision_note_and_expiry tests/unit/test_sqlite_approval.py::test_sqlite_approval_store_persists_metadata_decision_note_and_expiry -q
```

Expected before implementation:

- Tests fail because `ApprovalRequest` lacks metadata fields, sanitizer is missing, SQLite columns are missing, and interception audit event is missing.

- [ ] **Step 5: Create sanitization helper**

Create `agent_app/governance/sanitization.py`:

```python
"""Sanitization helpers for approval and audit payloads."""

from __future__ import annotations

from typing import Any

_SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "authorization",
    "credential",
)


def sanitize_payload(value: Any, *, max_string_length: int = 500) -> Any:
    """Return a copy of value with sensitive fields redacted."""
    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(part in key_text for part in _SENSITIVE_KEY_PARTS):
                sanitized[key] = "[redacted]"
            else:
                sanitized[key] = sanitize_payload(item, max_string_length=max_string_length)
        return sanitized
    if isinstance(value, list):
        return [sanitize_payload(item, max_string_length=max_string_length) for item in value]
    if isinstance(value, tuple):
        return [sanitize_payload(item, max_string_length=max_string_length) for item in value]
    if isinstance(value, str):
        if len(value) > max_string_length:
            return value[:max_string_length] + "...(truncated)"
        return value
    return value


def sanitized_error(error_type: str, message: str) -> dict[str, str]:
    """Build a generic user-facing error detail."""
    return {"type": error_type, "message": message}
```

- [ ] **Step 6: Extend ApprovalRequest**

Modify `agent_app/governance/approval.py`:

1. Import `Any`:

```python
from typing import Any, Protocol
```

2. Add fields after `reason`:

```python
    decision_note: str | None = Field(default=None, description="Approval decision note")
    expires_at: datetime | None = Field(default=None, description="Approval expiry time")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Extra metadata")
```

- [ ] **Step 7: Persist SQLite approval metadata**

Modify `agent_app/runtime/approval_store.py`.

In `_init_db()`, after the existing `CREATE TABLE` block and before indexes, add migration-safe column creation:

```python
        columns = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(approvals)").fetchall()
        }
        if "decision_note" not in columns:
            self._conn.execute("ALTER TABLE approvals ADD COLUMN decision_note TEXT")
        if "expires_at" not in columns:
            self._conn.execute("ALTER TABLE approvals ADD COLUMN expires_at TEXT")
        if "metadata_json" not in columns:
            self._conn.execute("ALTER TABLE approvals ADD COLUMN metadata_json TEXT DEFAULT '{}'")
```

Update `create()` SQL to include the new columns:

```python
            INSERT INTO approvals
                (approval_id, run_id, agent_name, tool_name, arguments_json,
                 risk_level, requested_by, tenant_id, status, reason, created_at,
                 decision_note, expires_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

Update the values tuple:

```python
                request.decision_note,
                request.expires_at.isoformat() if request.expires_at else None,
                json.dumps(request.metadata),
```

Update `approve()` to persist `decision_note` from the `reason` argument for compatibility:

```python
            SET status = ?, resolved_at = ?, resolved_by = ?, reason = ?, decision_note = ?
            WHERE approval_id = ?
```

with tuple:

```python
            (ApprovalStatus.APPROVED, now, approved_by, reason, reason, approval_id),
```

Update `reject()` similarly:

```python
            SET status = ?, resolved_at = ?, resolved_by = ?, reason = ?, decision_note = ?
            WHERE approval_id = ?
```

with tuple:

```python
            (ApprovalStatus.REJECTED, now, rejected_by, reason, reason, approval_id),
```

Update `_row_to_approval()` to pass:

```python
            decision_note=row["decision_note"] if "decision_note" in row.keys() else row["reason"],
            expires_at=(
                datetime.fromisoformat(row["expires_at"])
                if "expires_at" in row.keys() and row["expires_at"]
                else None
            ),
            metadata=(
                json.loads(row["metadata_json"])
                if "metadata_json" in row.keys() and row["metadata_json"]
                else {}
            ),
```

- [ ] **Step 8: Sanitize approval and audit data in ToolExecutor**

Modify `agent_app/runtime/tool_executor.py`.

Import sanitizer:

```python
from agent_app.governance.sanitization import sanitize_payload
```

Before creating `ApprovalRequest`, compute:

```python
            sanitized_arguments = sanitize_payload(arguments)
            metadata = {
                "argument_keys": sorted(arguments.keys()),
                "requester_context": {
                    "user_id": context.user_id,
                    "tenant_id": context.tenant_id,
                    "trace_id": context.trace_id,
                },
            }
```

Use sanitized arguments and metadata in `ApprovalRequest`:

```python
                arguments=sanitized_arguments,
                metadata=metadata,
```

Before the existing `tool.approval_required` audit event, add:

```python
            if str(spec.risk_level).lower() in {"high", "critical"}:
                await self.audit_logger.log(AuditEvent(
                    event_id=str(uuid.uuid4()),
                    run_id=context.run_id,
                    event_type="tool.high_risk_intercepted",
                    user_id=context.user_id,
                    tenant_id=context.tenant_id,
                    tool_name=tool_name,
                    approval_id=approval.approval_id,
                    data={
                        "risk_level": spec.risk_level,
                        "argument_keys": sorted(arguments.keys()),
                    },
                ))
```

Change approval audit data from raw arguments to sanitized arguments:

```python
                data={"arguments": sanitized_arguments, "risk_level": spec.risk_level},
```

Change `tool.executed` audit data to sanitize arguments, output, and error:

```python
                "arguments": _safe_serialize(sanitize_payload(arguments)),
                "output": _safe_serialize(sanitize_payload(output)),
                "error": _safe_serialize(sanitize_payload(error)),
```

- [ ] **Step 9: Run approval and ToolExecutor tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_tool_executor.py tests/unit/test_approval.py tests/unit/test_sqlite_approval.py -q
```

Expected after implementation:

- All targeted tests pass.

- [ ] **Step 10: Commit Task 2**

Run:

```bash
git add agent_app/governance/sanitization.py agent_app/governance/approval.py agent_app/runtime/approval_store.py agent_app/runtime/tool_executor.py tests/unit/test_tool_executor.py tests/unit/test_approval.py tests/unit/test_sqlite_approval.py
git commit -m "feat: sanitize approval metadata"
```

---

### Task 3: Approval resume service

**Files:**
- Create: `agent_app/runtime/approval_resume.py`
- Modify: `agent_app/core/app.py`
- Test: `tests/unit/test_approval_resume.py`

- [ ] **Step 1: Create failing service tests**

Create `tests/unit/test_approval_resume.py`:

```python
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

    async def resume(self, agent_spec: AgentSpec, context: RunContext, **kwargs: Any) -> AppRunResult:
        self.resume_calls.append({"agent_spec": agent_spec, "context": context, **kwargs})
        return AppRunResult(
            run_id=context.run_id,
            status="completed",
            final_output="resumed by fake backend",
        )


class FailingResumeBackend(FakeResumeBackend):
    async def resume(self, agent_spec: AgentSpec, context: RunContext, **kwargs: Any) -> AppRunResult:
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
    app = AgentApp(approval_store=approvals, run_state_store=run_states, backend=backend, audit_logger=audit)
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
    assert backend.resume_calls[0]["approvals"] == [{"approval_id": "apv_1", "status": "approved"}]
    event_types = [event.event_type for event in audit.list_events(run_id="run-1")]
    assert "run.resume_requested" in event_types
    assert "run.resumed" in event_types


@pytest.mark.asyncio
async def test_reject_approval_does_not_call_backend_resume() -> None:
    approvals = InMemoryApprovalStore()
    run_states = InMemoryRunStateStore()
    audit = InMemoryAuditLogger()
    backend = FakeResumeBackend()
    app = AgentApp(approval_store=approvals, run_state_store=run_states, backend=backend, audit_logger=audit)
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
```

- [ ] **Step 2: Run service tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_approval_resume.py -q
```

Expected before implementation:

- Import or attribute errors because `ApprovalResumeService`, `AgentApp.approve_and_resume()`, and `AgentApp.reject_approval()` do not exist.

- [ ] **Step 3: Implement ApprovalResumeService**

Create `agent_app/runtime/approval_resume.py`:

```python
"""Approval decision and run resume service."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from agent_app.core.result import AppRunResult
from agent_app.governance.audit import AuditEvent, AuditLogger
from agent_app.governance.risk import ApprovalStatus

logger = logging.getLogger(__name__)


class ApprovalResumeService:
    """Coordinates approval decisions with persisted run resume."""

    def __init__(
        self,
        *,
        app: Any,
        approval_store: Any,
        run_state_store: Any,
        backend: Any,
        agent_registry: Any,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self.app = app
        self.approval_store = approval_store
        self.run_state_store = run_state_store
        self.backend = backend
        self.agent_registry = agent_registry
        self.audit_logger = audit_logger

    async def approve_and_resume(
        self,
        approval_id: str,
        decided_by: str,
        decision_note: str | None = None,
    ) -> AppRunResult:
        """Approve an approval request and resume its interrupted run."""
        try:
            approval = await self.approval_store.approve(
                approval_id,
                decided_by,
                decision_note,
            )
        except KeyError:
            return AppRunResult(
                run_id="",
                status="failed",
                error={"type": "approval_not_found", "message": f"Approval '{approval_id}' not found."},
            )

        await self._audit(
            event_type="approval.approved",
            run_id=approval.run_id,
            approval_id=approval.approval_id,
            tool_name=approval.tool_name,
            user_id=decided_by,
            tenant_id=approval.tenant_id,
            data={"decision_note": decision_note, "risk_level": approval.risk_level},
        )
        await self._audit(
            event_type="run.resume_requested",
            run_id=approval.run_id,
            approval_id=approval.approval_id,
            tool_name=approval.tool_name,
            user_id=decided_by,
            tenant_id=approval.tenant_id,
            data={"decision": ApprovalStatus.APPROVED.value},
        )

        try:
            interrupted = await self.run_state_store.get(approval.run_id)
        except KeyError:
            await self._audit(
                event_type="run.resume_blocked",
                run_id=approval.run_id,
                approval_id=approval.approval_id,
                tool_name=approval.tool_name,
                user_id=decided_by,
                tenant_id=approval.tenant_id,
                data={"reason": "run_state_missing"},
            )
            return AppRunResult(
                run_id=approval.run_id,
                status="failed",
                error={
                    "type": "run_state_missing",
                    "message": "Run state is missing or no longer resumable.",
                },
            )

        pending = await self._pending_approvals(interrupted.approval_ids)
        if pending:
            return AppRunResult(
                run_id=approval.run_id,
                status="interrupted",
                interruptions=interrupted.interruptions,
                latency_ms=0,
            )

        rejected = await self._has_rejection(interrupted.approval_ids)
        if rejected:
            await self.run_state_store.mark_completed(approval.run_id)
            return AppRunResult(
                run_id=approval.run_id,
                status="completed",
                final_output=f"Run '{approval.run_id}' was rejected. Reason: No reason provided.",
                latency_ms=0,
            )

        if not interrupted.backend_state:
            await self._audit(
                event_type="run.resume_blocked",
                run_id=approval.run_id,
                approval_id=approval.approval_id,
                tool_name=approval.tool_name,
                user_id=decided_by,
                tenant_id=approval.tenant_id,
                data={"reason": "backend_state_missing"},
            )
            return AppRunResult(
                run_id=approval.run_id,
                status="failed",
                error={
                    "type": "resume_blocked",
                    "message": "Run state is missing or no longer resumable.",
                },
            )

        try:
            agent_spec = self.agent_registry.get(interrupted.agent_name)
            approvals = await self._approval_decisions(interrupted.approval_ids)
            result = await self.backend.resume(
                agent_spec=agent_spec,
                context=interrupted.context,
                backend_state=interrupted.backend_state,
                approvals=approvals,
                interruptions=interrupted.interruptions,
                rejection_message=None,
            )
        except Exception:
            logger.exception("Approval resume backend call failed")
            await self._audit(
                event_type="run.resume_failed",
                run_id=approval.run_id,
                approval_id=approval.approval_id,
                tool_name=approval.tool_name,
                user_id=decided_by,
                tenant_id=approval.tenant_id,
                data={"error_type": "backend_resume_failed"},
            )
            return AppRunResult(
                run_id=approval.run_id,
                status="failed",
                error={
                    "type": "backend_resume_failed",
                    "message": "Backend resume failed; check server logs for details.",
                },
            )

        await self.run_state_store.mark_resumed(approval.run_id)
        await self._audit(
            event_type="run.resumed",
            run_id=approval.run_id,
            approval_id=approval.approval_id,
            tool_name=approval.tool_name,
            user_id=decided_by,
            tenant_id=approval.tenant_id,
            data={"status": result.status},
        )
        return result

    async def reject(
        self,
        approval_id: str,
        decided_by: str,
        reason: str | None = None,
    ) -> AppRunResult:
        """Reject an approval request without resuming backend execution."""
        try:
            approval = await self.approval_store.reject(approval_id, decided_by, reason)
        except KeyError:
            return AppRunResult(
                run_id="",
                status="failed",
                error={"type": "approval_not_found", "message": f"Approval '{approval_id}' not found."},
            )

        await self._audit(
            event_type="approval.rejected",
            run_id=approval.run_id,
            approval_id=approval.approval_id,
            tool_name=approval.tool_name,
            user_id=decided_by,
            tenant_id=approval.tenant_id,
            data={"reason": reason, "risk_level": approval.risk_level},
        )
        try:
            await self.run_state_store.mark_completed(approval.run_id)
        except KeyError:
            pass
        return AppRunResult(
            run_id=approval.run_id,
            status="completed",
            final_output=(
                f"Run '{approval.run_id}' was rejected. "
                f"Reason: {reason or 'No reason provided.'}"
            ),
            latency_ms=0,
        )

    async def _approval_decisions(self, approval_ids: list[str]) -> list[dict[str, str]]:
        decisions: list[dict[str, str]] = []
        for item_id in approval_ids:
            try:
                request = await self.approval_store.get(item_id)
            except KeyError:
                continue
            decisions.append({"approval_id": item_id, "status": str(request.status)})
        return decisions

    async def _pending_approvals(self, approval_ids: list[str]) -> bool:
        for item_id in approval_ids:
            try:
                request = await self.approval_store.get(item_id)
            except KeyError:
                continue
            if str(request.status) == ApprovalStatus.PENDING.value:
                return True
        return False

    async def _has_rejection(self, approval_ids: list[str]) -> bool:
        for item_id in approval_ids:
            try:
                request = await self.approval_store.get(item_id)
            except KeyError:
                continue
            if str(request.status) == ApprovalStatus.REJECTED.value:
                return True
        return False

    async def _audit(
        self,
        *,
        event_type: str,
        run_id: str | None,
        approval_id: str | None,
        tool_name: str | None,
        user_id: str | None,
        tenant_id: str | None,
        data: dict[str, Any],
    ) -> None:
        if self.audit_logger is None:
            return
        await self.audit_logger.log(AuditEvent(
            event_id=str(uuid.uuid4()),
            run_id=run_id,
            event_type=event_type,
            user_id=user_id,
            tenant_id=tenant_id,
            tool_name=tool_name,
            approval_id=approval_id,
            data=data,
        ))
```

- [ ] **Step 4: Add AgentApp wrappers**

Modify `agent_app/core/app.py` after `list_pending_approvals()` and before existing `resume()`:

```python
    async def approve_and_resume(
        self,
        approval_id: str,
        decided_by: str,
        decision_note: str | None = None,
    ) -> Any:
        """Approve a pending approval and resume its interrupted run."""
        if self.approval_store is None:
            raise RuntimeError(
                "No approval_store configured on this AgentApp. "
                "Pass approval_store=... when creating the app."
            )
        if self._run_state_store is None:
            from agent_app.core.result import AppRunResult
            return AppRunResult(
                run_id="",
                status="failed",
                error={
                    "type": "no_run_state_store",
                    "message": "Run state is missing or no longer resumable.",
                },
            )
        self._ensure_runner()
        from agent_app.runtime.approval_resume import ApprovalResumeService

        service = ApprovalResumeService(
            app=self,
            approval_store=self.approval_store,
            run_state_store=self._run_state_store,
            backend=self._runner.backend,
            agent_registry=self.agent_registry,
            audit_logger=getattr(self, "_audit_logger", None),
        )
        return await service.approve_and_resume(
            approval_id=approval_id,
            decided_by=decided_by,
            decision_note=decision_note,
        )

    async def reject_approval(
        self,
        approval_id: str,
        decided_by: str,
        reason: str | None = None,
    ) -> Any:
        """Reject a pending approval without resuming backend execution."""
        if self.approval_store is None:
            raise RuntimeError(
                "No approval_store configured on this AgentApp. "
                "Pass approval_store=... when creating the app."
            )
        if self._run_state_store is None:
            from agent_app.core.result import AppRunResult
            return AppRunResult(
                run_id="",
                status="failed",
                error={
                    "type": "no_run_state_store",
                    "message": "Run state is missing or no longer resumable.",
                },
            )
        self._ensure_runner()
        from agent_app.runtime.approval_resume import ApprovalResumeService

        service = ApprovalResumeService(
            app=self,
            approval_store=self.approval_store,
            run_state_store=self._run_state_store,
            backend=self._runner.backend,
            agent_registry=self.agent_registry,
            audit_logger=getattr(self, "_audit_logger", None),
        )
        return await service.reject(
            approval_id=approval_id,
            decided_by=decided_by,
            reason=reason,
        )
```

- [ ] **Step 5: Run service tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_approval_resume.py -q
```

Expected after implementation:

- All approval resume service tests pass.

- [ ] **Step 6: Run existing app/approval tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_approval.py tests/unit/test_run_state.py -q
```

Expected:

- Existing approval and run-state tests pass.

- [ ] **Step 7: Commit Task 3**

Run:

```bash
git add agent_app/runtime/approval_resume.py agent_app/core/app.py tests/unit/test_approval_resume.py
git commit -m "feat: add approval resume service"
```

---

### Task 4: OpenAI backend SDK call mapping and fake resume

**Files:**
- Modify: `agent_app/adapters/openai_agents.py`
- Test: `tests/unit/test_native_hitl.py`

- [ ] **Step 1: Add failing native HITL call-id mapping tests**

Append to `tests/unit/test_native_hitl.py`:

```python
@pytest.mark.asyncio
async def test_native_interruption_preserves_sdk_call_id(monkeypatch: Any, agent_spec: AgentSpec, run_context: RunContext, tool_registry: ToolRegistry) -> None:
    interruption = FakeToolApprovalItem(
        call_id="call-delete-1",
        tool_name="delete_file",
        arguments={"path": "/tmp/test", "api_token": "secret-token"},
    )
    runner = FakeRunnerNative(interruptions=[interruption])
    _install_fake_native_sdk(monkeypatch, runner=runner)
    from agent_app.adapters.openai_agents import OpenAIAgentsBackend

    backend = OpenAIAgentsBackend(tool_registry=tool_registry, hitl_mode="native")
    result = await backend.run(agent_spec, "delete file", run_context)

    assert result.status == "interrupted"
    assert result.interruptions[0]["sdk_call_id"] == "call-delete-1"
    assert result.interruptions[0]["tool_name"] == "delete_file"
    assert result.interruptions[0]["arguments"]["api_token"] == "[redacted]"
    assert result.backend_state["metadata"]["sdk_interruptions"][0]["sdk_call_id"] == "call-delete-1"


@pytest.mark.asyncio
async def test_native_resume_uses_sdk_call_id_mapping(monkeypatch: Any, agent_spec: AgentSpec, run_context: RunContext, tool_registry: ToolRegistry) -> None:
    interruption = FakeToolApprovalItem(
        call_id="call-delete-1",
        tool_name="delete_file",
        arguments={"path": "/tmp/test"},
    )
    runner = FakeRunnerNative(interruptions=[interruption])
    _install_fake_native_sdk(monkeypatch, runner=runner)
    from agent_app.adapters.openai_agents import OpenAIAgentsBackend

    backend = OpenAIAgentsBackend(tool_registry=tool_registry, hitl_mode="native")
    first = await backend.run(agent_spec, "delete file", run_context)
    resumed = await backend.resume(
        agent_spec=agent_spec,
        context=run_context,
        backend_state=first.backend_state,
        approvals=[{"approval_id": first.interruptions[0]["approval_id"], "status": "approved"}],
        interruptions=first.interruptions,
    )

    assert resumed.status == "completed"
    assert resumed.final_output == "resumed: file deleted"
    resume_input = runner.run_calls[-1]["input"]
    assert hasattr(resume_input, "get_interruptions")
    assert resume_input.get_interruptions() == []


@pytest.mark.asyncio
async def test_native_resume_rejection_does_not_leave_pending_interruption(monkeypatch: Any, agent_spec: AgentSpec, run_context: RunContext, tool_registry: ToolRegistry) -> None:
    interruption = FakeToolApprovalItem(
        call_id="call-delete-1",
        tool_name="delete_file",
        arguments={"path": "/tmp/test"},
    )
    runner = FakeRunnerNative(interruptions=[interruption])
    _install_fake_native_sdk(monkeypatch, runner=runner)
    from agent_app.adapters.openai_agents import OpenAIAgentsBackend

    backend = OpenAIAgentsBackend(tool_registry=tool_registry, hitl_mode="native")
    first = await backend.run(agent_spec, "delete file", run_context)
    resumed = await backend.resume(
        agent_spec=agent_spec,
        context=run_context,
        backend_state=first.backend_state,
        approvals=[{"approval_id": first.interruptions[0]["approval_id"], "status": "rejected"}],
        interruptions=first.interruptions,
        rejection_message="not allowed",
    )

    assert resumed.status == "completed"
    resume_input = runner.run_calls[-1]["input"]
    assert resume_input.get_interruptions() == []
```

- [ ] **Step 2: Run mapping tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_native_hitl.py::test_native_interruption_preserves_sdk_call_id tests/unit/test_native_hitl.py::test_native_resume_uses_sdk_call_id_mapping tests/unit/test_native_hitl.py::test_native_resume_rejection_does_not_leave_pending_interruption -q
```

Expected before implementation:

- Tests fail because `sdk_call_id`, sanitized arguments, backend metadata, and approval-id-to-sdk-call mapping are incomplete.

- [ ] **Step 3: Add helper functions in OpenAI backend**

Modify `agent_app/adapters/openai_agents.py` near existing helper functions to add:

```python
def _sdk_interruption_call_id(item: Any) -> str:
    """Return a stable SDK interruption identifier."""
    value = getattr(item, "call_id", None) or getattr(item, "tool_lookup_key", None)
    return str(value or "")


def _build_sdk_decision_map(
    approvals: list[dict[str, Any]],
    interruptions: list[dict[str, Any]],
) -> dict[str, str]:
    """Map SDK call IDs to approval decisions."""
    approval_status = {
        str(item.get("approval_id", "")): str(item.get("status", "pending"))
        for item in approvals
    }
    decision_map: dict[str, str] = {}
    for interruption in interruptions:
        approval_id = str(interruption.get("approval_id", ""))
        sdk_call_id = str(interruption.get("sdk_call_id", ""))
        if sdk_call_id and approval_id in approval_status:
            decision_map[sdk_call_id] = approval_status[approval_id]
    return decision_map
```

- [ ] **Step 4: Sanitize native SDK interruptions and metadata**

In `OpenAIAgentsBackend.run()`, import sanitizer locally or at module top without importing SDK:

```python
from agent_app.governance.sanitization import sanitize_payload
```

Replace the native SDK interruptions list construction with code shaped like:

```python
                framework_interruptions: list[dict[str, Any]] = []
                sdk_metadata: list[dict[str, Any]] = []
                for item in sdk_interruptions:
                    sdk_call_id = _sdk_interruption_call_id(item)
                    approval_id = f"apv_{uuid.uuid4().hex[:12]}"
                    tool_name = getattr(item, "tool_name", getattr(item, "name", "unknown"))
                    arguments = sanitize_payload(getattr(item, "arguments", {}) or {})
                    framework_interruptions.append({
                        "type": "approval_required",
                        "approval_id": approval_id,
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "risk_level": "high",
                        "sdk_call_id": sdk_call_id,
                        "_sdk_interruption": True,
                    })
                    sdk_metadata.append({
                        "approval_id": approval_id,
                        "sdk_call_id": sdk_call_id,
                        "tool_name": tool_name,
                    })
                interruptions = framework_interruptions
```

After `_serialize_run_state(run_state)`, ensure metadata is present:

```python
                    backend_state.setdefault("metadata", {})
                    backend_state["metadata"]["sdk_interruptions"] = sdk_metadata
```

If serialization fails and fallback backend state is created, include metadata there too:

```python
                    backend_state = {
                        "hitl_mode": "native",
                        "backend": "openai",
                        "metadata": {"sdk_interruptions": sdk_metadata, "resumable": False},
                    }
```

- [ ] **Step 5: Use SDK call-id decision mapping in resume**

In `OpenAIAgentsBackend.resume()`, read interruption metadata:

```python
        framework_interruptions: list[dict[str, Any]] = kwargs.get("interruptions", [])
        if not framework_interruptions:
            framework_interruptions = backend_state.get("metadata", {}).get("sdk_interruptions", [])
        decision_map = _build_sdk_decision_map(approvals, framework_interruptions)
```

Replace current approval id decision lookup:

```python
            call_id = getattr(item, "call_id", None) or getattr(item, "tool_lookup_key", "")
            decision = decision_map.get(str(call_id), "pending")
```

with:

```python
            sdk_call_id = _sdk_interruption_call_id(item)
            decision = decision_map.get(sdk_call_id, "pending")
```

- [ ] **Step 6: Run native HITL tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_native_hitl.py -q
```

Expected after implementation:

- All native HITL tests pass.

- [ ] **Step 7: Commit Task 4**

Run:

```bash
git add agent_app/adapters/openai_agents.py tests/unit/test_native_hitl.py
git commit -m "feat: map OpenAI approvals to SDK call ids"
```

---

### Task 5: OpenAI backend error sanitization

**Files:**
- Modify: `agent_app/adapters/openai_agents.py`
- Modify: `agent_app/runtime/app_runner.py`
- Test: `tests/unit/test_openai_backend.py`
- Test: `tests/unit/test_native_hitl.py`

- [ ] **Step 1: Add failing backend run error sanitization test**

Append to `tests/unit/test_openai_backend.py`:

```python
@pytest.mark.asyncio
async def test_openai_backend_run_error_is_sanitized(monkeypatch: Any, agent_spec: AgentSpec, run_context: RunContext) -> None:
    runner = FakeRunner()
    runner._force_run_exception = RuntimeError("secret OpenAI backend token")
    _install_fake_sdk(monkeypatch, runner=runner)
    from agent_app.adapters.openai_agents import OpenAIAgentsBackend

    backend = OpenAIAgentsBackend()
    result = await backend.run(agent_spec, "hello", run_context)

    assert result.status == "failed"
    assert result.error == {
        "type": "backend_execution_failed",
        "message": "Backend execution failed; check server logs for details.",
    }
    assert "secret OpenAI backend token" not in str(result.error)
```

- [ ] **Step 2: Add failing backend resume error sanitization test**

Append to `tests/unit/test_native_hitl.py`:

```python
class FakeRunnerResumeFailure(FakeRunnerNative):
    async def run(self, native_agent: Any, input: Any = "", **kwargs: Any) -> Any:
        if hasattr(input, "get_interruptions"):
            raise RuntimeError("secret resume backend token")
        return await super().run(native_agent, input=input, **kwargs)


@pytest.mark.asyncio
async def test_native_resume_error_is_sanitized(monkeypatch: Any, agent_spec: AgentSpec, run_context: RunContext, tool_registry: ToolRegistry) -> None:
    runner = FakeRunnerResumeFailure()
    _install_fake_native_sdk(monkeypatch, runner=runner)
    from agent_app.adapters.openai_agents import OpenAIAgentsBackend

    backend = OpenAIAgentsBackend(tool_registry=tool_registry, hitl_mode="native")
    first = await backend.run(agent_spec, "delete file", run_context)
    result = await backend.resume(
        agent_spec=agent_spec,
        context=run_context,
        backend_state=first.backend_state,
        approvals=[{"approval_id": first.interruptions[0]["approval_id"], "status": "approved"}],
        interruptions=first.interruptions,
    )

    assert result.status == "failed"
    assert result.error == {
        "type": "backend_resume_failed",
        "message": "Backend resume failed; check server logs for details.",
    }
    assert "secret resume backend token" not in str(result.error)
```

- [ ] **Step 3: Run sanitization tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_openai_backend.py::test_openai_backend_run_error_is_sanitized tests/unit/test_native_hitl.py::test_native_resume_error_is_sanitized -q
```

Expected before implementation:

- Tests fail because raw exception messages currently appear in `AppRunResult.error`.

- [ ] **Step 4: Sanitize OpenAI backend run errors**

In `OpenAIAgentsBackend.run()`, update the `except Exception as exc:` block around `Runner.run()`.

Record internal event with error type only or sanitized message, then return:

```python
            return AppRunResult(
                run_id=context.run_id,
                status="failed",
                error={
                    "type": "backend_execution_failed",
                    "message": "Backend execution failed; check server logs for details.",
                },
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )
```

Do not include `str(exc)` in user-facing result.

- [ ] **Step 5: Sanitize OpenAI backend resume errors**

In `OpenAIAgentsBackend.resume()`, update the `except Exception as exc:` block around resume `Runner.run()` to return:

```python
            return AppRunResult(
                run_id=context.run_id,
                status="failed",
                error={
                    "type": "backend_resume_failed",
                    "message": "Backend resume failed; check server logs for details.",
                },
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )
```

- [ ] **Step 6: Sanitize AppRunner backend exception fallback**

In `agent_app/runtime/app_runner.py`, update the catch block around `self.backend.run(...)` so `error_detail` is:

```python
            error_detail = {
                "type": "backend_execution_failed",
                "message": "Backend execution failed; check server logs for details.",
            }
```

This is a defense-in-depth fallback for backends that raise instead of returning failed `AppRunResult`.

- [ ] **Step 7: Run backend tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_openai_backend.py tests/unit/test_native_hitl.py -q
```

Expected after implementation:

- OpenAI backend and native HITL tests pass.

- [ ] **Step 8: Run CLI baseline because AppRunner error shape changed**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_cli.py -q
```

Expected:

- `15 passed`.

- [ ] **Step 9: Commit Task 5**

Run:

```bash
git add agent_app/adapters/openai_agents.py agent_app/runtime/app_runner.py tests/unit/test_openai_backend.py tests/unit/test_native_hitl.py
git commit -m "fix: sanitize OpenAI backend errors"
```

---

### Task 6: OpenAI backend wrapper governance coverage

**Files:**
- Modify: `agent_app/adapters/openai_agents.py`
- Test: `tests/unit/test_openai_backend.py`

- [ ] **Step 1: Add failing fake SDK wrapper tests**

Append to `tests/unit/test_openai_backend.py`:

```python
@pytest.mark.asyncio
async def test_compile_tool_high_risk_wrapper_returns_approval_required(monkeypatch: Any, run_context: RunContext) -> None:
    _install_fake_sdk(monkeypatch)
    from agent_app.adapters.openai_agents import OpenAIAgentsBackend
    from agent_app.governance.approval import InMemoryApprovalStore
    from agent_app.governance.audit import InMemoryAuditLogger
    from agent_app.governance.permission import DefaultPermissionChecker
    from agent_app.runtime.tool_executor import ToolExecutor

    registry = ToolRegistry()

    async def dangerous_tool(path: str) -> dict[str, str]:
        return {"deleted": path}

    registry.register(
        "file.delete",
        ToolSpec(name="file.delete", description="Delete file", risk_level="high"),
        fn=dangerous_tool,
    )
    approvals = InMemoryApprovalStore()
    executor = ToolExecutor(
        tool_registry=registry,
        approval_store=approvals,
        permission_checker=DefaultPermissionChecker(),
        audit_logger=InMemoryAuditLogger(),
    )
    backend = OpenAIAgentsBackend(tool_registry=registry, tool_executor=executor)
    sdk_tool = backend.compile_tool(registry.get_entry("file.delete"), context=run_context)

    output = await sdk_tool(path="/tmp/important")

    assert output["status"] == "approval_required"
    assert output["tool_name"] == "file.delete"
    assert output["approval_id"].startswith("apv_")
    pending = await approvals.list_pending(tenant_id="t1")
    assert [request.tool_name for request in pending] == ["file.delete"]


@pytest.mark.asyncio
async def test_compile_tool_low_risk_wrapper_executes(monkeypatch: Any, run_context: RunContext) -> None:
    _install_fake_sdk(monkeypatch)
    from agent_app.adapters.openai_agents import OpenAIAgentsBackend
    from agent_app.governance.approval import InMemoryApprovalStore
    from agent_app.governance.audit import InMemoryAuditLogger
    from agent_app.governance.permission import DefaultPermissionChecker
    from agent_app.runtime.tool_executor import ToolExecutor

    registry = ToolRegistry()

    async def lookup_order(order_id: str) -> dict[str, str]:
        return {"order_id": order_id, "status": "paid"}

    registry.register(
        "order.lookup",
        ToolSpec(name="order.lookup", description="Lookup order", risk_level="low"),
        fn=lookup_order,
    )
    executor = ToolExecutor(
        tool_registry=registry,
        approval_store=InMemoryApprovalStore(),
        permission_checker=DefaultPermissionChecker(),
        audit_logger=InMemoryAuditLogger(),
    )
    backend = OpenAIAgentsBackend(tool_registry=registry, tool_executor=executor)
    sdk_tool = backend.compile_tool(registry.get_entry("order.lookup"), context=run_context)

    output = await sdk_tool(order_id="ord-1")

    assert output == {"order_id": "ord-1", "status": "paid"}
```

- [ ] **Step 2: Run wrapper tests and verify RED or regression state**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_openai_backend.py::test_compile_tool_high_risk_wrapper_returns_approval_required tests/unit/test_openai_backend.py::test_compile_tool_low_risk_wrapper_executes -q
```

Expected:

- If Task 1 and existing wrapper behavior already satisfy these tests, they pass and document the integration.
- If they fail, failure identifies the missing wrapper behavior to fix in Step 3.

- [ ] **Step 3: Fix wrapper return shape if needed**

If the high-risk wrapper output does not include direct `approval_id`, modify `_execute_governed_tool()` interrupted branch in `agent_app/adapters/openai_agents.py` to return:

```python
            return {
                "status": "approval_required",
                "approval_id": approval.approval_id if approval else None,
                "tool_name": result.tool_name,
                "risk_level": approval.risk_level if approval else "unknown",
                "message": (
                    f"Tool '{result.tool_name}' requires approval "
                    f"(approval_id: {approval.approval_id if approval else 'N/A'})."
                ),
            }
```

If the low-risk wrapper does not execute, ensure `compile_tool()` keeps wrapping when `self._tool_executor is not None and context is not None` regardless of `hitl_mode`.

- [ ] **Step 4: Run OpenAI backend tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_openai_backend.py -q
```

Expected:

- All OpenAI backend tests pass.

- [ ] **Step 5: Commit Task 6**

Run:

```bash
git add agent_app/adapters/openai_agents.py tests/unit/test_openai_backend.py
git commit -m "test: cover OpenAI tool governance wrapper"
```

If no production code changed in this task, the same commit message is still acceptable with only test changes.

---

### Task 7: Service integration with existing AgentApp resume path

**Files:**
- Modify: `agent_app/core/app.py`
- Test: `tests/unit/test_native_hitl.py`
- Test: `tests/unit/test_approval_resume.py`

- [ ] **Step 1: Add integration test for app-level approve and resume with fake backend**

Append to `tests/unit/test_approval_resume.py`:

```python
@pytest.mark.asyncio
async def test_approve_and_resume_keeps_existing_resume_method_available() -> None:
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
```

- [ ] **Step 2: Run integration test**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_approval_resume.py::test_approve_and_resume_keeps_existing_resume_method_available -q
```

Expected:

- Test passes once Task 3 is implemented.

- [ ] **Step 3: If needed, keep `AgentApp.resume()` backward-compatible**

If the test reveals that `approve_and_resume()` broke existing `resume()`, adjust `AgentApp.approve_and_resume()` only. Do not change the signature or return contract of existing `AgentApp.resume(run_id, approval_id=None)`.

- [ ] **Step 4: Run relevant resume tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_approval_resume.py tests/unit/test_native_hitl.py tests/unit/test_run_state.py -q
```

Expected:

- All tests pass.

- [ ] **Step 5: Commit Task 7**

Run:

```bash
git add agent_app/core/app.py tests/unit/test_approval_resume.py tests/unit/test_native_hitl.py
git commit -m "test: preserve app resume compatibility"
```

If only tests changed, keep the commit scoped to the test file.

---

### Task 8: Documentation and release notes

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/release_checklist_v0.10.md`
- Modify: `docs/openai_backend.md` if present

- [ ] **Step 1: Check whether `docs/openai_backend.md` exists**

Run:

```bash
test -f docs/openai_backend.md && echo exists || echo missing
```

Expected:

- Output is either `exists` or `missing`.

- [ ] **Step 2: Update README**

Add a concise Phase 20 note near existing OpenAI backend / governance sections in `README.md`:

```markdown
### OpenAI backend tool approval and resume safety

When using the OpenAI Agents SDK backend, registered framework tools still pass through Agent App governance before executing. Low-risk tools execute when permissions allow them. Medium-risk tools remain permission-checked and audited. High-risk and critical tools, and any tool with `requires_approval=True`, create pending approval requests instead of executing immediately.

Approval decisions should be applied through `await app.approve_and_resume(...)` or `await app.reject_approval(...)`. The OpenAI SDK dependency remains isolated to the adapter layer, and default tests use fake SDK objects rather than a real OpenAI API key.
```

- [ ] **Step 3: Update CHANGELOG**

Add under the current unreleased or 0.10.0 section:

```markdown
## Phase 20: OpenAI Tool Interception and RunState Resume (0.10.0)

### Added

- Shared governance approval policy: `requires_approval=True` and high/critical-risk tools now pause for approval before execution.
- Approval resume service for approving, rejecting, and resuming interrupted backend runs through one runtime boundary.
- OpenAI backend SDK interruption mapping from framework approval IDs to SDK call IDs for safer fake RunState resume tests.
- Conservative sanitization for approval arguments, audit payloads, and user-facing backend errors.

### Safety

- Default tests do not require a real OpenAI API key.
- Core modules do not import the OpenAI Agents SDK.
- Dry-run defaults and recovery daemon default-off behavior are unchanged.
```

- [ ] **Step 4: Update release checklist**

Add to `docs/release_checklist_v0.10.md`:

```markdown
## Phase 20: OpenAI Tool Interception and RunState Resume

- [ ] Verify `tests/unit/test_tool_executor.py` passes.
- [ ] Verify `tests/unit/test_approval_resume.py` passes.
- [ ] Verify `tests/unit/test_openai_backend.py` passes with fake SDK only.
- [ ] Verify `tests/unit/test_native_hitl.py` passes with fake RunState only.
- [ ] Verify `tests/unit/test_cli.py -q` passes.
- [ ] Verify recovery admin/CLI/daemon baseline passes.
- [ ] Verify full pytest suite passes.
- [ ] Confirm dry-run defaults were not changed.
- [ ] Confirm recovery daemon default-off behavior was not changed.
- [ ] Confirm no real OpenAI API key is required by default tests.
```

- [ ] **Step 5: Update `docs/openai_backend.md` if present**

If `docs/openai_backend.md` exists, add:

```markdown
## Tool approval and native RunState resume

The OpenAI backend compiles framework tools into SDK tools, but execution still passes through Agent App governance when a tool registry and run context are available. High-risk and critical tools, plus any tool with `requires_approval=True`, produce pending framework approval requests instead of executing immediately.

Native SDK RunState support is isolated in `agent_app.adapters.openai_agents`. The framework stores backend-specific state in `InterruptedRun.backend_state` and maps framework approval IDs to SDK call IDs before resuming. Default unit tests use fake SDK and fake RunState objects; real SDK smoke tests must remain explicitly marker-gated.
```

- [ ] **Step 6: Run docs-related sanity tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_openai_backend.py tests/unit/test_native_hitl.py -q
```

Expected:

- OpenAI backend tests still pass after docs-only changes.

- [ ] **Step 7: Commit Task 8**

Run:

```bash
git add README.md CHANGELOG.md docs/release_checklist_v0.10.md docs/openai_backend.md
git commit -m "docs: document Phase 20 OpenAI approval resume"
```

If `docs/openai_backend.md` is missing, remove it from `git add`:

```bash
git add README.md CHANGELOG.md docs/release_checklist_v0.10.md
git commit -m "docs: document Phase 20 OpenAI approval resume"
```

---

### Task 9: Final regression verification

**Files:**
- No production file changes expected.
- Test verification only.

- [ ] **Step 1: Verify git status before final test run**

Run:

```bash
git status --short
```

Expected:

- Working tree is clean before final verification, or only expected docs/test edits remain from the current task before its commit.

- [ ] **Step 2: Run Phase 20 targeted tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_tool_executor.py tests/unit/test_approval.py tests/unit/test_sqlite_approval.py tests/unit/test_approval_resume.py tests/unit/test_openai_backend.py tests/unit/test_native_hitl.py -q
```

Expected:

- All targeted tests pass.

- [ ] **Step 3: Run CLI baseline**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_cli.py -q
```

Expected:

- `15 passed`.

- [ ] **Step 4: Run recovery admin/CLI/daemon baseline**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_recovery_admin.py tests/unit/test_recovery_cli.py tests/unit/test_recovery_daemon.py -q
```

Expected:

- `78 passed` or higher if tests were added outside this plan.

- [ ] **Step 5: Run full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected:

- Full suite passes with zero failures.
- At Phase 20 start the baseline was `1548 passed, 5 skipped, 2 warnings`; the final count should be at least that many passed tests plus the new Phase 20 tests.

- [ ] **Step 6: Verify no OpenAI SDK hard dependency leaked into core modules**

Run:

```bash
grep -R "from agents\|import agents" agent_app --exclude='openai_agents.py'
```

Expected:

- No output.

- [ ] **Step 7: Verify dry-run and daemon defaults were not edited accidentally**

Run:

```bash
git diff HEAD~8 -- agent_app/runtime/recovery_models.py agent_app/runtime/recovery_daemon.py agent_app/core/app.py | grep -E "dry_run|enabled" || true
```

Expected:

- No unexpected changes to recovery dry-run defaults or daemon enabled defaults. Any output must be reviewed before completion.

- [ ] **Step 8: Commit final verification marker if docs checklist changed after verification**

If release checklist boxes are updated after final verification, run:

```bash
git add docs/release_checklist_v0.10.md
git commit -m "chore: mark Phase 20 verification complete"
```

If no files changed, do not create an empty commit.

- [ ] **Step 9: Final completion summary**

Report:

1. Modified files.
2. New data structures.
3. New service/backend APIs.
4. New tests.
5. Full test results.
6. Whether dry-run default changed.
7. Whether daemon default-off behavior changed.
8. Real OpenAI SDK integration limitations.
9. Remaining limitations.
10. Phase 21 recommendations.

---

## Self-review checklist for implementers

Before marking Phase 20 complete, verify each statement is true:

- [ ] High-risk and critical tools create pending approvals without needing explicit `requires_approval=True`.
- [ ] `requires_approval=True` still creates pending approvals for low-risk tools.
- [ ] Low-risk tools execute normally when permissions allow them.
- [ ] Approval arguments are sanitized before persistence and audit logging.
- [ ] Approval metadata can store SDK call IDs.
- [ ] SQLite approval rows persist metadata fields.
- [ ] Approval resume service approves and resumes through one service path.
- [ ] Rejection never calls backend resume.
- [ ] OpenAI backend resume maps framework approval IDs to SDK call IDs.
- [ ] User-facing backend errors do not include raw backend messages.
- [ ] Core modules do not import `agents`.
- [ ] Default tests use fake SDK and fake RunState objects.
- [ ] Recovery CLI/admin/daemon baseline passes.
- [ ] CLI baseline passes.
- [ ] Full suite passes.
- [ ] Dry-run defaults are unchanged.
- [ ] Daemon default-off behavior is unchanged.
