# OpenAI Backend — Design Reference

## Overview

The OpenAI backend enables the Agent App Framework to execute agents using the
**real OpenAI Agents SDK** (`openai-agents` package) instead of the default
`DryRunBackend`.

Key design goals:

- **Core remains SDK-agnostic** — only `agent_app/adapters/openai_agents.py`
  imports the `agents` package. All other modules (core, registry, config,
  governance) stay independent.
- **Lazy SDK loading** — `_load_agents_sdk()` imports the SDK only when a
  backend method is called. `import agent_app` works without `openai-agents`
  installed.
- **Governance parity** — OpenAI backend uses the same `ToolExecutor`,
  `ApprovalStore`, `AuditLogger`, and `PermissionChecker` as `DryRunBackend`.

---

## Supported Features

| Feature | Status |
|---------|--------|
| `AgentSpec` → `agents.Agent` compilation | ✅ |
| `ToolSpec` / `@tool` → SDK `function_tool` | ✅ |
| Tool resolution from `ToolRegistry` | ✅ |
| `Runner.run()` execution | ✅ |
| `Runner.run_streamed()` with fallback | ✅ |
| Governance-aware tool wrapper | ✅ |
| Permission denied → structured error | ✅ |
| Approval required → structured response | ✅ |
| Audit logging with run_id / tenant_id | ✅ |
| Config `runtime.backend: openai` | ✅ |
| Context binding (per-run governance) | ✅ |

---

## HITL Modes

The OpenAI backend supports two Human-in-the-Loop (HITL) modes for handling
high-risk tool approvals, configured via `runtime.openai.hitl_mode`:

```yaml
runtime:
  backend: openai
  openai:
    hitl_mode: wrapper   # default — framework governance wrapper
    # hitl_mode: native  # SDK-native RunState pause/resume
```

### Wrapper Mode (default)

```
OpenAI Agent calls function_tool
  │
  ▼
Framework governance wrapper
  │
  ▼
ToolExecutor.execute() → permission check → approval gate
  │
  ├── Permission denied ──► return {"status": "error", ...}
  │
  ├── Approval required ──► create ApprovalRequest
  │                         return {"status": "approval_required", ...}
  │
  └── Authorized ──► execute tool → return output
```

**Characteristics:**

- Tool calls are wrapped by the framework's `_create_governed_tool_wrapper()`
- High-risk tools return `{"status": "approval_required"}` as tool output
- The OpenAI SDK **does not** pause — the model receives the dict as output
- `AppRunResult.status` is set to `"interrupted"`
- `AgentApp.resume()` uses framework-level `RunStateStore` (stub, no re-execution)
- Best for: compatibility, eval testing, simple governance flows

### Native Mode

```
OpenAI Runner.run()
  │
  ▼
SDK tool requires approval (needs_approval=True)
  │
  ▼
SDK returns RunResult with interruptions=[ToolApprovalItem]
  │
  ▼
result.to_state() → RunState
  │
  ▼
_serialize_run_state() → backend_state dict
  │
  ▼
Framework ApprovalRequest created from interruption
  │
  ▼
InterruptedRun saved to RunStateStore with backend_state
  │
  ▼
User approves → RunState.approve(item)
  │
  ▼
AgentApp.resume() → backend.resume()
  │
  ▼
_deserialize_run_state() → RunState
  │
  ▼
Runner.run(agent, state) — resumes from saved state
  │
  ▼
Final AppRunResult (completed)
```

**Characteristics:**

- Tools are compiled with `needs_approval=True` for `requires_approval` specs
- The SDK **natively** interrupts the run and returns `RunResult.interruptions`
- Framework calls `result.to_state()` to capture full SDK RunState
- RunState is serialized to `InterruptedRun.backend_state` via `to_json()`
- SDK interruptions are mapped to framework `ApprovalRequest` objects
- `AgentApp.resume()` dispatches to `OpenAIAgentsBackend.resume()`
- Backend restores RunState via `from_json()`, applies `approve()`/`reject()`
- Calls `Runner.run(agent, state)` to resume from the saved state
- Best for: production HITL, real pause/resume, audit trails

### Mode Comparison

| Aspect | Wrapper | Native |
|--------|---------|--------|
| SDK pause/resume | No | Yes |
| RunState persistence | Framework-level only | SDK RunState → backend_state |
| Resume mechanism | Framework stub | SDK `Runner.run(agent, state)` |
| Approval resolution | Framework `ApprovalStore` | SDK `RunState.approve()` |
| Tool compilation | Governance wrapper | `needs_approval=True` |
| SDK version requirement | Any | ≥0.2.0 with RunState |
| Eval testing | ✅ Mock-friendly | ✅ Mock-friendly |
| Production readiness | Moderate | Higher (real resume) |

### Streaming

Both modes support streaming. In native mode, interruptions are captured after
the stream completes:

```python
async for event in backend.stream(agent_spec, input, context):
    process(event)
# After stream: check backend_state for interruptions
```

---

## Tool Wrapper Execution Flow

When governance components are configured, the OpenAI backend wraps each
compiled tool with a governance-aware wrapper:

```
OpenAI Agent calls function_tool
  │
  ▼
Governance wrapper receives kwargs
  │
  ▼
ToolExecutor.execute(tool_name, arguments, RunContext)
  │
  ▼
PermissionChecker.check(spec.permissions, context)
  │
  ├── Missing permission ──► return {"status": "error",
  │                            "error": {"type": "permission_denied", ...},
  │                            "tool_name": ...}
  │
  ├── requires_approval ──► create ApprovalRequest
  │                         return {"status": "approval_required",
  │                                 "approval_id": ...,
  │                                 "tool_name": ...,
  │                                 "risk_level": ...,
  │                                 "message": ...}
  │
  └── Otherwise ──► execute original function
                      │
                      ▼
                  AuditLogger.log(event)
                      │
                      ▼
                  return output to OpenAI Agent
```

### Key Points

1. **Wrapper is per-run** — `compile_agent(agent_spec, context=context)` binds
   the current `RunContext` into each tool's closure. No shared state between
   concurrent runs.

2. **Original function is NOT called when interrupted** — If permission is
   denied or approval is required, the governance wrapper returns a structured
   dict without calling the original tool function.

3. **Audit events include full context** — Every tool execution (completed,
   interrupted, failed) produces an `AuditEvent` with `run_id`, `user_id`,
   `tenant_id`, and `tool_name`.

4. **Result status detection** — After `Runner.run()` completes, the backend
   scans the SDK result for governance markers:
   - `result.new_items` / `result.items` — SDK result items with
     `{"status": "approval_required"}` output
   - `result.tool_calls` — tool call arguments containing governance metadata
   - `result.interruptions` — direct interruptions attribute

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Adapters  (FastAPI, CLI)                           │
├─────────────────────────────────────────────────────┤
│  AppRunner  — orchestrates runs + governance        │
├─────────────────────────────────────────────────────┤
│  Registry  — AgentRegistry, ToolRegistry, Workflow  │
├─────────────────────────────────────────────────────┤
│  Core  — AgentSpec, ToolSpec, Workflow, RunContext  │
│          AppRunResult, RoutingPolicy, WorkflowTrace │
├─────────────────────────────────────────────────────┤
│  Backend  — AgentBackend Protocol                    │
│            DryRunBackend (default) / OpenAIAgents   │
│            ┌────────────────────────────────────┐   │
│            │ OpenAIAgentsBackend                │   │
│            │  ├─ compile_agent()                │   │
│            │  ├─ compile_tool()                 │   │
│            │  ├─ _create_governed_tool_wrapper() │   │
│            │  ├─ _execute_governed_tool()        │   │
│            │  └─ _extract_governance_interruptions│  │
│            └────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

### Module Responsibilities

| Module | Responsibility |
|--------|---------------|
| `agent_app/adapters/openai_agents.py` | **Only** module that imports `agents`. Contains `OpenAIAgentsBackend`, lazy SDK loader, governance wrapper, result extractors. |
| `agent_app/config/loader.py` | Creates `OpenAIAgentsBackend` with governance components when `runtime.backend: openai`. |
| `agent_app/core/app.py` | Passes `backend` to `AppRunner`. |
| `agent_app/runtime/tool_executor.py` | Shared governance pipeline (permissions → approval → execute → audit). Used by both `DryRunBackend` and `OpenAIAgentsBackend`. |

---

## Relationship with DryRunBackend

Both backends share the same governance components:

| Component | Shared? | Notes |
|-----------|---------|-------|
| `ToolExecutor` | Yes | Same pipeline for both backends |
| `ApprovalStore` | Yes | InMemory or SQLite |
| `AuditLogger` | Yes | InMemory or SQLite |
| `PermissionChecker` | Yes | `DefaultPermissionChecker` |
| `ToolRegistry` | Yes | Same tool definitions |

**When to use which:**

- **DryRunBackend** (default) — Best for eval suites, governance regression
  tests, local development. No real API calls needed.
- **OpenAIAgentsBackend** — Use when you need real LLM execution with
  governance enforcement.

Both backends produce compatible `AppRunResult` objects with the same
governance semantics (status, interruptions, tool_calls, audit events).

---

## Current Limitations

### 1. Native HITL SDK Version Requirement

Native mode requires `openai-agents >= 0.2.0` with `needs_approval` and `RunState`
support. If the SDK version does not support these features, use `wrapper` mode.

### 2. Multi-Agent Not Deeply Integrated

OpenAI backend's `run()` and `stream()` work for single agents. Multi-agent
workflows (handoff, orchestrator) are dispatched through `AppRunner` /
`WorkflowExecutor`, which uses `DryRunBackend` heuristics for routing. Full
OpenAI backend integration for multi-agent is deferred.

### 3. Framework-Level Resume Fallback

In `wrapper` mode, `AgentApp.resume()` uses framework-level `RunStateStore`.
The resumed result is a stub — tools are **not** re-executed. Native mode
provides real SDK RunState resume via `Runner.run(agent, state)`.

### 4. Tool Call Arguments

The OpenAI SDK passes tool arguments as plain kwargs. Complex argument
structures (nested dicts, lists) work as long as the SDK serializes them
correctly. The governance wrapper passes arguments directly to `ToolExecutor`,
which forwards them to the original tool function.

### 5. RunState Version Compatibility

Serialized `RunState` from `to_json()` is SDK-version-dependent. Upgrading the
`openai-agents` SDK may change the RunState schema, making previously saved
`backend_state` values non-deserializable. In that case, `_deserialize_run_state()`
returns an error and the run cannot be resumed via native mode. Framework-level
resume via `RunStateStore` still works.

---

## Configuration Example

```yaml
# agentapp.yaml
app:
  name: my_app

runtime:
  backend: openai

governance:
  approvals:
    type: sqlite
    path: .agent_app/approvals.db
  audit:
    type: sqlite
    path: .agent_app/audit.db
  permissions:
    mode: default

agents:
  - name: assistant
    instructions: "You are a helpful assistant with access to tools."
    model: gpt-4o
    tools:
      - math.add
      - account.delete

tools:
  - name: math.add
    type: function
    risk_level: low
    permissions: []

  - name: account.delete
    type: function
    risk_level: high
    requires_approval: true
    permissions:
      - account:delete
```

---

## API Reference

### OpenAIAgentsBackend

```python
class OpenAIAgentsBackend:
    def __init__(
        self,
        agent_registry: Any | None = None,
        tool_registry: Any | None = None,
        workflow_registry: Any | None = None,
        *,
        raise_on_missing: bool = True,
        default_model: str | None = None,
        tool_executor: ToolExecutor | None = None,
        approval_store: Any | None = None,
        audit_logger: Any | None = None,
        permission_checker: Any | None = None,
        hitl_mode: str = "wrapper",  # "wrapper" or "native"
    ) -> None:
        ...

    def compile_agent(
        self, agent_spec: AgentSpec, context: RunContext | None = None
    ) -> Any: ...

    def compile_tool(
        self, tool_def: Any, context: RunContext | None = None
    ) -> Any: ...

    async def run(
        self,
        agent_spec: AgentSpec,
        input: str,
        context: RunContext,
        tools: list[object] | None = None,
        **kwargs: object,
    ) -> AppRunResult: ...

    async def resume(
        self,
        agent_spec: AgentSpec,
        context: RunContext,
        backend_state: dict[str, Any],
        approvals: list[dict[str, Any]] | None = None,
        **kwargs: object,
    ) -> AppRunResult:
        """Resume an interrupted run using saved RunState.

        In native mode, deserializes the SDK RunState from backend_state,
        applies approval/rejection decisions, and calls Runner.run(agent, state).
        """ ...

    async def stream(
        self,
        agent_spec: AgentSpec,
        input: str,
        context: RunContext,
        tools: list[object] | None = None,
        **kwargs: object,
    ) -> AsyncGenerator[StreamEvent, None]: ...
```

### hitl_mode Parameter

| Value | Behavior |
|-------|----------|
| `"wrapper"` (default) | Framework governance wrapper around tools. No SDK pause/resume. |
| `"native"` | SDK-native `needs_approval` + `RunState` for real pause/resume. |

### Resume Flow

In native mode, `resume()` performs these steps:

1. Deserialize `RunState` from `backend_state["value"]` using `from_json()`
2. Apply approval/rejection to each interruption via `RunState.approve()` / `reject()`
3. Call `Runner.run(compiled_agent, state)` with the updated RunState
4. Return `AppRunResult` with the resumed output

### Governance Result Formats

**Completed (low-risk, authorized):**

```python
{"order_id": "123", "status": "ok"}
```

**Interrupted (requires approval):**

```python
{
    "status": "approval_required",
    "approval_id": "apv_abc123",
    "tool_name": "account.delete",
    "risk_level": "high",
    "message": "Tool 'account.delete' requires approval (approval_id: apv_abc123)."
}
```

**Failed (permission denied):**

```python
{
    "status": "error",
    "error": {
        "type": "permission_denied",
        "message": "Missing permissions: account:delete",
    },
    "tool_name": "account.delete",
}
```

---

## Testing

The OpenAI backend is tested using a **fake SDK** injected via `sys.modules`
monkeypatching. This allows comprehensive testing without real API calls.

Key test patterns:

```python
# Inject fake SDK
_install_fake_sdk(monkeypatch)

# Use FakeRunner / FakeAgent / FakeRunResult
runner = FakeRunner()
_install_fake_sdk(monkeypatch, runner=runner)

# Test governance with FakeToolExecutor
fake_executor = FakeToolExecutor(force_status="interrupted")
backend = OpenAIAgentsBackend(tool_executor=fake_executor)
```

Run tests:

```bash
pytest tests/unit/test_openai_backend.py -v
```

---

## OpenAI Multi-Agent Workflows

Phase 11 extends the OpenAI backend to support the framework's multi-agent
workflow types.

### Handoff Workflow

```yaml
workflows:
  customer_support:
    type: handoff
    entry: triage
    agents:
      - refund
      - billing
      - technical_support
```

**Execution flow:**

```
AgentApp.run(workflow="customer_support")
  → AgentApp._run_workflow()
    → OpenAIAgentsBackend.run_workflow()
      → _run_handoff_workflow()
        → compile_agent(triage, handoffs=[refund, billing, tech_support])
        → Runner.run(triage_agent, input)
        → AppRunResult with workflow_trace
```

**WorkflowTrace structure:**

- `step_type="agent"` for the entry agent execution
- `step_type="handoff_candidates"` recording candidate agent names
- Additional `agent` steps for each executed agent

### Orchestrator Workflow

```yaml
workflows:
  research_assistant:
    type: orchestrator
    entry: manager
    agents_as_tools:
      - researcher
      - analyst
      - writer
```

**Execution flow:**

```
AgentApp.run(workflow="research_assistant")
  → AgentApp._run_workflow()
    → OpenAIAgentsBackend.run_workflow()
      → _run_orchestrator_workflow()
        → compile_agent_as_tool(researcher) → Agent.as_tool()
        → compile_agent_as_tool(analyst)  → Agent.as_tool()
        → compile_agent_as_tool(writer)   → Agent.as_tool()
        → compile_agent(manager, tools=[specialist_tools, manager_tools])
        → Runner.run(manager_agent, input)
        → AppRunResult with agent_calls + workflow_trace
```

**WorkflowTrace structure:**

- `step_type="agent"` for the manager execution
- `step_type="agent_tools"` recording `agents_as_tools` list
- `agent_calls` extracted from SDK `tool_calls`

### Backend Delegation

| Backend | Handoff | Orchestrator |
|---------|---------|--------------|
| `DryRunBackend` | Framework WorkflowExecutor (keyword heuristics) | Framework WorkflowExecutor |
| `OpenAIAgentsBackend` | SDK `Agent.handoffs` + `Runner.run()` | SDK `Agent.as_tool()` + `Runner.run()` |

### compile_agent_as_tool()

```python
def compile_agent_as_tool(
    self,
    compiled_agent: Any,
    agent_name: str,
    input_text: str,
    context: RunContext | None = None,
) -> Any:
    """Compile an agent as an SDK tool.

    Uses SDK ``Agent.as_tool()`` when available. Falls back to a
    ``function_tool`` wrapper when ``as_tool`` is not available.
    """
```

**Native `as_tool()` benefits:**

- SDK handles nested agent execution natively
- Supports streaming, hooks, max_turns
- Supports `needs_approval` at the agent-tool level

**Fallback wrapper:**

- Creates a `function_tool` that returns a delegation placeholder
- Actual nested execution requires async support in the tool call path

### Governance Behavior

| Component | Governance |
|-----------|-----------|
| Entry/triage agent business tools | Framework `ToolExecutor` (permissions, approval, audit) |
| Manager agent business tools | Framework `ToolExecutor` |
| Specialist agents-as-tools | **Not governed** — deferred to future phases |
| HITL for business tools | `wrapper` or `native` mode as configured |

### Limitations

1. **Handoff target extraction** — The actual handoff target is not extracted
   from the SDK result. The `WorkflowTrace` records candidate agents but does
   not confirm which one was selected.
2. **Orchestrator agent_calls** — Extracted from SDK `tool_calls` when
   specialist tool names match. May be incomplete if the SDK does not
   expose tool call details.
3. **Agent-as-tool governance** — Specialist agents-as-tools do not go
   through the framework's `ToolExecutor` permission/approval pipeline.
4. **No parallel execution** — Orchestrator specialists are conceptually
   parallel but executed serially through the SDK.

## Tool approval and native RunState resume

The OpenAI backend compiles framework tools into SDK tools, but execution
still passes through Agent App governance when a tool registry and run
context are available. High-risk and critical tools, plus any tool with
`requires_approval=True`, produce pending framework approval requests
instead of executing immediately.

Native SDK RunState support is isolated in `agent_app.adapters.openai_agents`.
The framework stores backend-specific state in `InterruptedRun.backend_state`
and maps framework approval IDs to SDK call IDs before resuming. Default unit
tests use fake SDK and fake RunState objects; real SDK smoke tests must remain
explicitly marker-gated.
