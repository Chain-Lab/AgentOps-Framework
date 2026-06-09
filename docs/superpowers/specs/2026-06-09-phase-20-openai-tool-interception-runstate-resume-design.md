# Phase 20: OpenAI Tool Interception and RunState Resume Design

**Goal:** Make real OpenAI Agents SDK backend tool execution honor framework governance boundaries, persist resumable interrupted RunState, and resume or terminate safely after human approval decisions.

**Status:** Approved for design. Implementation must be test-first and must not begin until this spec is reviewed.

**Date:** 2026-06-09

---

## Summary

Phase 20 adds a minimal, production-oriented bridge between the framework's existing governance/approval/run-state model and real OpenAI Agents SDK tool execution. The implementation keeps the SDK dependency isolated inside `agent_app/adapters/openai_agents.py`, reuses existing approval and run-state stores, and avoids rebuilding the OpenAI agent loop.

The key behavior is:

- Low-risk tools execute normally when permissions allow them.
- Medium-risk tools continue through permissions and audit hooks; they do not require approval by default.
- High-risk and critical tools require approval by default.
- `requires_approval=True` always requires approval regardless of risk level.
- Interrupted OpenAI backend runs persist framework-level interruption metadata plus backend-specific serialized RunState.
- Approval decisions are applied through one service-level path, then delegated back to the backend resume API.
- Rejection never executes the intercepted high-risk tool.
- User-visible backend errors are sanitized; raw backend details stay out of public results.

---

## Non-goals

Phase 20 must not:

1. Reimplement the OpenAI Agents SDK agent loop.
2. Import `agents` outside the OpenAI adapter/backend layer.
3. Make core, registry, config, governance, approval, or recovery modules depend on `openai-agents`.
4. Require a real OpenAI API key in default tests.
5. Add or expand a large UI.
6. Expand the Recovery Admin Console interaction model.
7. Change dry-run defaults.
8. Change daemon default-off behavior.
9. Redesign DAG workflow execution or recovery scheduling.
10. Mix unrelated Phase 18.5/security edits into Phase 20 commits.

---

## Existing context

### OpenAI backend

`agent_app/adapters/openai_agents.py` already contains:

- dynamic SDK loading through `_load_agents_sdk()`;
- `OpenAIAgentsBackend.compile_agent()`;
- `OpenAIAgentsBackend.compile_tool()`;
- governance wrapper hooks through `_create_governed_tool_wrapper()` and `_execute_governed_tool()`;
- `hitl_mode="wrapper" | "native"`;
- `run()` and `resume()` methods;
- fake-SDK unit test coverage in `tests/unit/test_openai_backend.py` and `tests/unit/test_native_hitl.py`.

The current backend reads `ToolSpec.requires_approval` for native SDK HITL, but it does not consistently treat high/critical risk tools as approval-required. Native RunState resume currently risks mismatching framework approval ids with SDK interruption call ids.

### Governance and approval

Existing reusable structures:

- `agent_app/core/tool_spec.py::ToolSpec`
- `agent_app/runtime/tool_executor.py::ToolExecutor`
- `agent_app/governance/approval.py::ApprovalRequest`
- `agent_app/governance/approval.py::ApprovalStore`
- `agent_app/governance/approval.py::InMemoryApprovalStore`
- `agent_app/runtime/approval_store.py::SQLiteApprovalStore`
- `agent_app/governance/audit.py::AuditEvent`
- `agent_app/governance/audit.py::AuditLogger`

`ToolSpec` already has the fields Phase 20 needs:

- `risk_level`
- `requires_approval`
- `permissions`
- `timeout_seconds`
- `audit_enabled`

`ToolExecutor` already resolves tools, checks permissions, creates approval requests for `requires_approval=True`, executes tools, records audit events, and records trace events. Phase 20 should strengthen this shared path rather than duplicating approval logic inside the OpenAI adapter.

### Run state

Existing reusable structures:

- `agent_app/runtime/run_state.py::InterruptedRun`
- `agent_app/runtime/run_state.py::RunStateStore`
- `InterruptedRun.backend_state`
- `AgentApp.resume()`

`InterruptedRun.backend_state` is already designed to hold backend-specific resume payloads. Phase 20 should use this existing field for serialized OpenAI RunState instead of introducing a competing top-level `RunStateStore` concept with the same name.

---

## Design approach

Use a shared governance policy plus adapter-only SDK boundary:

1. Add a single shared approval policy for tools.
2. Route OpenAI SDK tool calls through `ToolExecutor` when a framework tool registry and run context are available.
3. Persist framework approval records and framework interrupted run records through existing stores.
4. Store SDK-specific RunState data only inside `InterruptedRun.backend_state`.
5. Add a small service-level approval resume coordinator so CLI/API/backend do not each implement their own decision/resume flow.
6. Harden error sanitization at backend and service boundaries.

This approach keeps cross-backend governance behavior consistent and keeps `openai-agents` isolated to the adapter.

---

## Components

### 1. Shared tool approval policy

Add a small helper that answers whether a tool must pause for approval.

Recommended location:

- `agent_app/governance/risk.py`

Recommended API:

```python
def requires_tool_approval(risk_level: str, requires_approval: bool = False) -> bool:
    """Return True when a tool call must pause for human approval."""
```

Behavior:

- returns `True` when `requires_approval` is true;
- returns `True` when normalized `risk_level` is `"high"` or `"critical"`;
- returns `False` for `"low"` and `"medium"` unless `requires_approval=True`;
- treats unknown risk conservatively only if the existing project risk model already does so; otherwise preserve current validation behavior and test the explicit supported values.

`ToolExecutor.execute()` should use this helper instead of checking only `spec.requires_approval`.

### 2. Approval request metadata and sanitization

Extend `ApprovalRequest` without replacing it.

Recommended compatible additions:

```python
metadata: dict[str, Any] = Field(default_factory=dict)
decision_note: str | None = None
expires_at: datetime | None = None
```

`metadata` is the main extension point. It should carry adapter-specific details without coupling the model to OpenAI SDK classes:

```python
{
    "sdk_call_id": "...",
    "backend": "openai",
    "state_id": "run-id-or-state-ref",
    "requester_context": {
        "user_id": "...",
        "tenant_id": "...",
        "trace_id": "..."
    },
    "argument_keys": ["..."],
}
```

SQLite approval storage should add a `metadata_json` column in a migration-safe way. Existing rows should read as `{}` when the column is absent or empty.

Tool arguments stored on approval requests and audit events must be sanitized before persistence. Recommended sanitizer:

- recursively handles dict/list/scalar values;
- redacts values for keys containing case-insensitive sensitive tokens:
  - `password`
  - `secret`
  - `token`
  - `api_key`
  - `authorization`
  - `credential`
- truncates long string values;
- never raises during sanitization.

The sanitizer should live outside the OpenAI adapter, for example:

- `agent_app/governance/sanitization.py`

The OpenAI adapter should not rely on sanitized approval arguments for actual tool execution. Execution after resume should use the SDK RunState or backend-owned state.

### 3. OpenAI backend tool interception

`OpenAIAgentsBackend.compile_tool()` should continue to wrap registered framework tools with `_create_governed_tool_wrapper()` whenever `ToolExecutor` and `RunContext` are available.

The wrapper path must ensure:

- permissions are checked before real tool execution;
- approval-required tools return an interruption signal rather than executing;
- high/critical tools are treated as approval-required through shared policy;
- low-risk tools execute directly via `ToolExecutor`;
- audit and trace events are recorded according to framework policy;
- SDK dependency remains isolated in the adapter.

`hitl_mode="native"` may continue passing `needs_approval=True` to `function_tool()` when the SDK supports it, but framework governance remains authoritative. Native SDK HITL should be treated as an adapter-level optimization or compatibility layer, not as a replacement for framework approval records.

### 4. SDK interruption and approval id mapping

Phase 20 must preserve a reliable mapping between framework approval decisions and SDK interruption items.

When the OpenAI SDK returns interruptions, the adapter should produce framework interruption records shaped like:

```python
{
    "type": "approval_required",
    "approval_id": "apv_...",
    "tool_name": "tool.name",
    "arguments": sanitized_arguments,
    "risk_level": "high",
    "sdk_call_id": "sdk-call-or-tool-lookup-key",
    "_sdk_interruption": True,
}
```

The corresponding `ApprovalRequest.metadata` should also include `sdk_call_id`.

On resume, the backend should not assume `approval_id == sdk_call_id`. It must build a mapping from persisted interruption metadata or approval metadata:

```python
sdk_call_id -> approval status
```

Then for each SDK RunState interruption item:

- if mapped decision is approved, call `run_state.approve(item)`;
- if mapped decision is rejected, call `run_state.reject(item, rejection_message=...)`;
- if still pending or missing, do not execute the tool; return an interrupted or recoverable failed result.

### 5. Approval resume service

Add a small service layer to centralize approval decisions and resume behavior.

Recommended file:

- `agent_app/runtime/approval_resume.py`

Recommended class:

```python
class ApprovalResumeService:
    async def approve_and_resume(
        self,
        approval_id: str,
        decided_by: str,
        decision_note: str | None = None,
    ) -> AppRunResult: ...

    async def reject(
        self,
        approval_id: str,
        decided_by: str,
        reason: str | None = None,
    ) -> AppRunResult: ...
```

Responsibilities:

1. Load the approval request.
2. Mark it approved or rejected through `ApprovalStore`.
3. Load the interrupted run from `RunStateStore` by `approval.run_id`.
4. For rejection:
   - mark the run completed or failed according to existing semantics;
   - return a user-facing result explaining rejection;
   - never call backend resume.
5. For approval:
   - verify all approvals for the run are resolved;
   - block unsafe resume if required state is missing or inconsistent;
   - delegate to backend `resume()` when backend state exists and backend supports resume;
   - return sanitized failed result if resume cannot proceed.
6. Emit audit and trace events.

`AgentApp` should expose this through a thin wrapper, preferably without breaking existing `resume(run_id, approval_id=None)` semantics:

```python
async def approve_and_resume(
    self,
    approval_id: str,
    decided_by: str,
    decision_note: str | None = None,
) -> AppRunResult: ...

async def reject_approval(
    self,
    approval_id: str,
    decided_by: str,
    reason: str | None = None,
) -> AppRunResult: ...
```

Existing `AgentApp.approve()`, `AgentApp.reject()`, and `AgentApp.resume()` can remain for backward compatibility.

### 6. Backend RunState persistence

Use existing `InterruptedRun.backend_state` for OpenAI-specific serialized state.

Expected shape:

```python
{
    "backend": "openai",
    "hitl_mode": "native",
    "serialization": "model_dump" | "dict" | "pickle-unavailable" | "unknown",
    "value": {...},
    "metadata": {
        "sdk_interruptions": [...],
        "resumable": true
    }
}
```

Tests must not require real SDK RunState objects. Fake RunState objects should support the minimal interface used by the adapter:

- `get_interruptions()`
- `approve(item)`
- `reject(item, rejection_message=...)`
- serialization method exercised by `_serialize_run_state()` / `_deserialize_run_state()`

If the installed OpenAI Agents SDK RunState API is unstable or missing required methods, the adapter must return a clear recoverable failed result without crashing core tests.

### 7. Error hygiene

User-visible results should not expose raw backend exception messages.

Recommended user-facing messages:

- backend run failure: `"Backend execution failed; check server logs for details."`
- backend resume failure: `"Backend resume failed; check server logs for details."`
- missing backend state: `"Run state is missing or no longer resumable."`
- unsafe resume blocked: `"Resume is blocked because the saved state does not match the approval decision."`

Internal logging may include exception type and stack trace. Audit events should avoid raw sensitive arguments and raw backend messages; they can include stable error types and sanitized summaries.

### 8. Audit and trace events

Required audit events:

- `tool.high_risk_intercepted`
- `approval.created`
- `approval.approved`
- `approval.rejected`
- `run.resume_requested`
- `run.resumed`
- `run.resume_failed`
- `run.resume_blocked`

Existing event types such as `tool.approval_required`, `approval.approved`, and `approval.rejected` may be reused if already established, but tests should assert the Phase 20 critical actions are observable.

Audit data should include:

- `run_id`
- `approval_id`
- `tool_name`
- `risk_level`
- `tenant_id`
- sanitized `argument_keys` or sanitized arguments
- status / decision

Audit data should not include raw backend errors or unredacted sensitive tool arguments.

---

## Runtime flows

### Low-risk tool flow

1. SDK asks to call registered tool.
2. OpenAI backend wrapper calls `ToolExecutor.execute()`.
3. `ToolExecutor` resolves `ToolSpec`.
4. Permissions pass.
5. `requires_tool_approval("low", False)` returns false.
6. Tool function executes.
7. Audit/trace records execution.
8. SDK receives tool output.

### High-risk or explicit approval flow

1. SDK asks to call registered tool.
2. OpenAI backend wrapper calls `ToolExecutor.execute()`.
3. `ToolExecutor` resolves `ToolSpec`.
4. Permissions pass.
5. Shared approval policy returns true.
6. Sanitized approval request is created.
7. Audit records interception and approval creation.
8. Tool function is not executed.
9. App result becomes `status="interrupted"` with approval interruption metadata.
10. `InterruptedRun` is saved with backend state when available.

### Approval resume flow

1. Caller invokes `app.approve_and_resume(approval_id=..., decided_by=...)`.
2. Service approves the request in `ApprovalStore`.
3. Service loads `InterruptedRun` by `approval.run_id`.
4. Service verifies no approvals remain pending and none are rejected.
5. Service verifies backend state exists for real backend resume.
6. Service delegates to `backend.resume()` with:
   - agent spec;
   - run context;
   - backend state;
   - approval decisions;
   - interruption metadata containing SDK call ids.
7. Backend applies approvals to SDK RunState by `sdk_call_id` mapping.
8. Backend resumes via SDK runner.
9. Service records audit/trace events and returns result.

### Rejection flow

1. Caller invokes `app.reject_approval(approval_id=..., decided_by=..., reason=...)`.
2. Service rejects the request in `ApprovalStore`.
3. Service loads `InterruptedRun` if available.
4. Service marks the run completed or failed according to existing interruption semantics.
5. Service does not call backend resume.
6. User-facing result explains that the request was rejected, without exposing backend details.

---

## Tests

All new tests must be written before implementation and must use fake SDK / fake RunState objects.

### Governance tests

Add or extend `tests/unit/test_tool_executor.py`:

1. Low-risk tool does not require approval and executes.
2. `requires_approval=True` creates a pending approval.
3. High-risk tool creates a pending approval even when `requires_approval=False`.
4. Critical-risk tool creates a pending approval.
5. Medium-risk tool does not require approval by default.
6. Approval request stores sanitized tool arguments.
7. Audit events do not include raw sensitive argument values.

### OpenAI backend tests

Add or extend `tests/unit/test_openai_backend.py` and `tests/unit/test_native_hitl.py`:

1. Fake OpenAI backend can simulate low-risk real tool execution.
2. Fake OpenAI backend can simulate high-risk tool interruption.
3. Framework approval id and SDK call id mapping is persisted.
4. Approved decision resumes fake RunState.
5. Rejected decision does not execute the tool.
6. Missing RunState returns clear recoverable/sanitized error.
7. Backend raw run error is not exposed in `AppRunResult.error.message`.
8. Backend raw resume error is not exposed in `AppRunResult.error.message`.
9. No `openai-agents` installed still allows core tests to pass.
10. Real SDK integration tests remain skipped or marker-gated by default.

### Service tests

Add `tests/unit/test_approval_resume.py`:

1. `approve_and_resume()` approves request, loads run state, and calls backend resume.
2. `reject_approval()` rejects request and does not call backend resume.
3. Pending sibling approvals keep the run interrupted.
4. Missing run state returns a clear recoverable error.
5. Unsafe resume is blocked when state/interruption metadata does not match approval.
6. Audit events are emitted for approve, reject, resume success, resume failure, and resume blocked.

### Regression baselines

Must continue passing:

```bash
.venv/bin/python -m pytest tests/unit/test_cli.py -q
.venv/bin/python -m pytest tests/unit/test_recovery_admin.py tests/unit/test_recovery_cli.py tests/unit/test_recovery_daemon.py -q
.venv/bin/python -m pytest -q
```

---

## Documentation updates

Update:

- `README.md`
- `CHANGELOG.md`
- `docs/release_checklist_v0.10.md`
- optionally `docs/openai_backend.md` if it exists and is current for OpenAI backend behavior.

Document:

- high/critical risk approval defaults;
- `requires_approval=True` precedence;
- fake/mocked tests vs real SDK limitations;
- no API-key requirement in default test suite;
- no dry-run default change;
- no daemon default change;
- no Recovery UI expansion.

---

## Acceptance criteria

Phase 20 is complete when:

1. High-risk and critical tools are intercepted and create pending approvals.
2. `requires_approval=True` creates pending approval for any risk level.
3. Low-risk tools execute without approval when permissions allow.
4. Pending approvals can be queried through existing approval store paths.
5. Backend RunState can be saved and loaded through existing interrupted run state persistence.
6. Approved decisions can resume a fake OpenAI backend run.
7. Rejected decisions do not execute the intercepted tool.
8. Missing RunState returns a clear recoverable sanitized error.
9. Backend raw errors are not exposed to user-facing result messages.
10. Core tests do not require `openai-agents`.
11. OpenAI backend tests use fake/mocked SDK by default.
12. Real SDK integration tests are skipped or marker-gated by default.
13. Recovery admin/CLI/daemon baseline remains passing.
14. CLI baseline remains passing.
15. Full pytest suite has zero failures.
16. Dry-run default behavior is unchanged.
17. Daemon default-off behavior is unchanged.
18. Phase 20 commits do not include unrelated Phase 18.5/security edits.

---

## Known limitations after Phase 20

1. Real OpenAI Agents SDK RunState behavior depends on SDK API stability. Phase 20 isolates that risk in the adapter and tests with fake RunState objects.
2. This phase does not add a large approval dashboard.
3. This phase does not implement full handoff/orchestrator recovery semantics beyond existing backend resume hooks.
4. Persistent approval metadata migration is intentionally minimal.
5. Audit sanitization is conservative and key-name based; deeper secret detection can be improved later.

---

## Phase 21 candidates

1. Persistent approval review APIs with stricter admin authorization boundaries.
2. Optional lightweight approval UI separate from Recovery Admin Console.
3. Stronger secret classification and redaction policy for nested tool payloads.
4. Real SDK integration smoke tests gated by `OPENAI_API_KEY` and explicit pytest marker.
5. More complete multi-agent/handoff resume coverage once SDK RunState APIs stabilize.
