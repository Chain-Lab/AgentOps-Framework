# Agent App Framework

A production-oriented Python application framework for building agentic applications.
Provides declarative agent definitions, tool governance, workflow orchestration,
session management, guardrails, observability, and deployment adapters.

## What is this?

Agent App Framework (AgentOps) is the application layer that sits between your
business logic and LLM agent runtimes. It gives you:

- **Declarative configuration** — define agents, tools, and workflows in YAML or Python
- **Tool governance** — risk levels, permissions, human approval gates, audit logging
- **Policy engine** — configurable YAML-driven policy rules with explainable decision traces
- **Policy decision store** — persistent, queryable policy decision storage with reporting and export (JSONL/CSV)
- **Policy console** — read-only HTML console for browsing policy decisions, reports, and replay results (Phase 26/27)
- **Session management** — in-memory or SQLite-backed conversation history
- **Streaming events** — real-time token delta, tool call, and run lifecycle events
- **Eval runner** — YAML-defined regression suites with assertions for status, output, approvals, routing
- **Deployment adapters** — FastAPI REST API and CLI out of the box
- **Backend abstraction** — swap execution backends without changing application code
- **Idempotency** — optional `idempotency_key` for best-effort duplicate request prevention via HTTP header or body
- **Lease renewal** — automatic background lease heartbeat for long-running DAG workflows; detects lease loss and raises stable error

## Why this framework?

Building agentic applications requires more than just calling an LLM API. You need
structured tool access, permission controls, human-in-the-loop approval flows,
conversation history, and a way to test that your agents behave correctly over time.
Agent App Framework provides all of this as a cohesive, layered architecture so you
can focus on your domain logic instead of reinventing infrastructure.

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
└─────────────────────────────────────────────────────┘
```

**Key design principles:**

- **Core is pure Python + Pydantic** — no external LLM SDK required
- **Optional dependencies** — `openai-agents` and `fastapi` are extras, not required
- **DryRunBackend by default** — test governance and eval logic without real API calls
- **Protocol-based abstractions** — swap stores, backends, and loggers freely

## Installation

### Core only

```bash
pip install -e .
```

Installs the minimal dependency set: `pydantic`, `pyyaml`, `typing-extensions`.
No LLM SDK required. Uses `DryRunBackend` for testing.

### With API support

```bash
pip install -e ".[api]"
```

Adds `fastapi` and `uvicorn` for the REST API adapter.

### With OpenAI Agents SDK support

```bash
pip install -e ".[openai]"
```

Adds `openai-agents` to use the real OpenAI Agents SDK backend.

### Development

```bash
pip install -e ".[dev]"
```

Adds `pytest`, `pytest-asyncio`, `ruff`, `mypy`.

### All extras

```bash
pip install -e ".[all]"
```

## Quickstart

### Define a tool

```python
from agent_app import tool, AgentApp, AgentSpec, Workflow

@tool(
    name="greet",
    description="Greet a user by name.",
    risk_level="low",
    permissions=[],
)
async def greet(**kwargs) -> dict:
    name = kwargs.get("name", "world")
    return {"message": f"Hello, {name}!"}
```

### Define an agent

```python
agent = AgentSpec(
    name="assistant",
    instructions="You are a helpful assistant.",
    tools=["greet"],
)
```

### Run with DryRunBackend

```python
app = AgentApp()
app.register_tool(spec, fn=greet)
app.register_agent(agent)

result = await app.run(
    agent="assistant",
    input="Say hello to Alice",
)
print(result.final_output)
# [dry-run] Agent 'assistant' received: Say hello to Alice
```

### Run with FastAPI

```python
# api.py
from agent_app.adapters.fastapi import create_fastapi_app
api = create_fastapi_app(app)

# Then:
# uvicorn api:api --reload
```

### Run evals

```bash
agentapp eval run evals/my_suite.yaml --config agentapp.yaml
```

## Example: customer_support

See `examples/customer_support/` for a complete working example.

### Run the example

```bash
python examples/customer_support/main.py
```

### Run evals

```bash
agentapp eval run examples/customer_support/evals/customer_support.yaml \
  --config examples/customer_support/agentapp.yaml
```

### Start FastAPI server

```bash
uvicorn examples.customer_support.api:api --reload
```

## Multi-Agent Workflows

### Handoff (Triage)

Route users to specialist agents based on intent:

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

The triage agent receives all input. Based on keyword matching, the framework
automatically routes the request to the appropriate specialist. The handoff
chain is recorded in `AppRunResult.handoffs`.

### Orchestrator (Agents-as-Tools)

A manager agent delegates to specialist agents based on task type:

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

The manager analyzes the input and calls relevant specialists. Specialist
invocations are recorded in `AppRunResult.agent_calls`.

### Routing Policy

Both handoff and orchestrator workflows support declarative routing policies
defined in YAML. Without a routing policy, the framework falls back to
built-in keyword heuristics.

```yaml
workflows:
  customer_support:
    type: handoff
    entry: triage
    agents:
      - refund
      - billing
    routing:
      rules:
        - name: refund_intent
          target: refund
          match_type: keyword
          keywords: ["refund", "退款"]
          priority: 10
          reason: matched refund intent
        - name: billing_intent
          target: billing
          match_type: keyword
          keywords: ["invoice", "billing"]
          priority: 20
        - name: default_triage
          target: triage
          match_type: default
          priority: 999
```

**Match types:**

- `keyword` — substring match against user input (case-insensitive)
- `regex` — PCRE pattern match against user input
- `default` — fallback rule used only when no other rule matches

**Priority:** Lower number = higher priority. The first matching rule wins
for handoff (`route_one`). For orchestrator (`route_many`), all non-default
matching rules are collected and their targets are called in priority order.

**Backward compatibility:** If no `routing` block is defined, the framework
uses built-in heuristic keyword matching (same behavior as v0.2).

**Observability:** Every routing decision is recorded in
`AppRunResult.workflow_trace` as a `WorkflowStep` with `step_type="routing"`,
including the matched rule name in `metadata["rule"]`.

### Example: research_assistant

See `examples/research_assistant/` for a complete orchestrator example.

```bash
python examples/research_assistant/main.py
agentapp eval run examples/research_assistant/evals/research_assistant.yaml \
  --config examples/research_assistant/agentapp.yaml
```

## Workflow Trace

Every multi-agent workflow run produces a structured execution trace
accessible via `AppRunResult.workflow_trace`.

```python
result = await app.run(workflow="customer_support", input="I want a refund")
trace = result.workflow_trace
```

**WorkflowTrace fields:**

| Field | Description |
|-------|-------------|
| `workflow_name` | Name of the executed workflow |
| `workflow_type` | `"handoff"` or `"orchestrator"` |
| `entry_agent` | Entry agent name (e.g. `"triage"`) |
| `steps` | Ordered list of execution steps |

**WorkflowStep fields:**

| Field | Description |
|-------|-------------|
| `step_id` | Unique step identifier |
| `step_type` | `"agent"`, `"routing"`, `"tool"`, or `"error"` |
| `agent_name` | Agent that performed this step |
| `input_summary` | Truncated input text |
| `output_summary` | Truncated output text |
| `status` | `"completed"`, `"failed"`, or `"skipped"` |
| `metadata` | Extra data — includes `rule` for routing steps |

**Example trace structure:**

```json
{
  "workflow_name": "customer_support",
  "workflow_type": "handoff",
  "entry_agent": "triage",
  "steps": [
    {
      "step_type": "agent",
      "agent_name": "triage",
      "input_summary": "I want a refund for order 123",
      "status": "completed"
    },
    {
      "step_type": "routing",
      "agent_name": "triage",
      "output_summary": "→ refund",
      "status": "completed",
      "metadata": {"rule": "refund_intent", "reason": "matched refund intent"}
    },
    {
      "step_type": "agent",
      "agent_name": "refund",
      "input_summary": "I want a refund for order 123",
      "output_summary": "[dry-run] Agent 'refund' received: ...",
      "status": "interrupted"
    }
  ]
}
```

**Eval assertions:** Use `workflow_steps` and `routing_decisions` in eval
expect blocks to verify trace content:

```yaml
expect:
  status: completed
  handoffs:
    - from_agent: triage
      to_agent: refund
  routing_decisions:
    - refund_intent
  workflow_steps:
    - routing
    - agent
```

## Governance

The framework provides a governance pipeline that runs before every tool call:

1. **Permission check** — does the current user/tenant have the required permissions?
2. **Approval gate** — for high-risk tools, create an approval request and pause execution
3. **Audit logging** — record every tool execution and approval event

Risk levels: `low` (execute directly), `medium` (log + execute), `high` (requires approval).

```python
# Approve a pending request
await app.approve(approval_id, approver="manager")

# Reject
await app.reject(approval_id, rejected_by="manager", reason="Policy violation")

# Resume after approval
result = await app.resume(run_id, approval_id)
```

## Eval Runner

Define regression tests as YAML:

```yaml
name: my_eval_suite
defaults:
  agent: support
  permissions: [order:read]

cases:
  - id: order_query
    input: "check order 123"
    expect:
      status: completed
      output_contains: ["order", "123"]
```

The eval runner checks `status`, `output_contains`, `tools_called`,
`approvals_required`, `error_type`, `handoffs`, `agent_calls`,
`routing_decisions`, `workflow_steps`, and `approve_and_resume` flows.

See `docs/evals.md` for the full eval reference.

## OpenAI Backend

When `openai-agents` is installed, you can use the real OpenAI Agents SDK:

```python
from agent_app.adapters.openai_agents import OpenAIAgentsBackend

backend = OpenAIAgentsBackend()
app = AgentApp(backend=backend)
```

### Installation

```bash
pip install -e ".[openai]"
```

### Configuration

Set the backend in `agentapp.yaml`:

```yaml
runtime:
  backend: openai
```

Supported values: `"dry_run"` (default) and `"openai"`.

Governance configuration (optional):

```yaml
governance:
  approvals:
    type: sqlite
    path: .agent_app/approvals.db
  audit:
    type: sqlite
    path: .agent_app/audit.db
  permissions:
    mode: default
```

When governance is configured, the OpenAI backend's compiled function tools
route through the framework's `ToolExecutor` for permission checks, approval
gates, and audit logging.

### What works

- Single-agent `run()` — real SDK `Runner.run()` execution
- `function_tool` compilation — framework tools wrapped as SDK function tools
- Basic streaming — `stream()` delegates to `Runner.run_streamed` with fallback
- Tool resolution — tools resolved from ToolRegistry during agent compilation
- **Governance-aware tool wrapper** — real SDK function tools route through
  `ToolExecutor` for permissions, approval, and audit
- **Permission denied** — returns structured error response to the model
- **Approval required** — returns structured `approval_required` response to
  the model, recorded in `AppRunResult.interruptions`
- **Audit logging** — all tool executions recorded with correct run_id/tenant_id

### How governance wrapping works

When `openai` backend is configured with governance components (approval store,
audit logger, permission checker), the backend wraps each compiled tool with a
governance-aware wrapper:

1. SDK invokes the tool (via `function_tool`)
2. Wrapper calls `ToolExecutor.execute(tool_name, arguments, context)`
3. `ToolExecutor` runs: permission check → approval gate → execute → audit
4. Result returned to the SDK:
   - `completed` → raw tool output
   - `interrupted` → `{"status": "approval_required", "approval_id": ..., ...}`
   - `failed` → `{"status": "error", "error": ..., "tool_name": ...}`

The `AppRunResult` status is set to `"interrupted"` when governance
interruptions are detected in the SDK result.

### Current limitations

- **Multi-agent** — handoff/orchestrator workflows with OpenAI backend are not
  yet deeply integrated
- **DryRunBackend recommended** — for eval and governance regression testing,
  `dry_run` remains the recommended backend
- **Native HITL** — requires `openai-agents >= 0.2.0` with `needs_approval` /
  `RunState` support; use `wrapper` mode for older SDK versions

### OpenAI backend tool approval and resume safety

When using the OpenAI Agents SDK backend, registered framework tools still pass
through Agent App governance before executing. Low-risk tools execute when
permissions allow them. Medium-risk tools remain permission-checked and audited.
High-risk and critical tools, and any tool with `requires_approval=True`, create
pending approval requests instead of executing immediately.

Approval decisions should be applied through `await app.approve_and_resume(...)`
or `await app.reject_approval(...)`. The OpenAI SDK dependency remains isolated to
the adapter layer, and default tests use fake SDK objects rather than a real OpenAI
API key.

### Example

See `examples/openai_basic/` for a complete working example.

## Run State Persistence

Phase 9 introduces framework-level run state persistence for interrupted runs.

### What it does

When a run is interrupted (e.g., approval required), the framework persists the
full run state to a `RunStateStore`:

- **InMemoryRunStateStore** — default, for development/testing
- **SQLiteRunStateStore** — persistent, for production

The persisted state includes:
- Run context (user, tenant, permissions)
- Interruption details (approval IDs, risk levels)
- Backend name and state (OpenAI RunState JSON for native HITL resume)
- Result snapshot

### Configuration

```yaml
runtime:
  backend: dry_run
  run_state:
    type: sqlite
    path: .agent_app/run_states.db
```

Or flat format:
```yaml
runtime:
  run_state_type: sqlite
  run_state_path: .agent_app/run_states.db
```

### Resume

```python
# Resume an interrupted run
result = await app.resume(run_id="run-abc123")
```

The resume checks all pending approvals:
- **All pending** → returns `status="interrupted"`
- **Any rejected** → returns completed with rejection message
- **All approved** → returns completed stub

### FastAPI Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/runs/interrupted` | List interrupted runs |
| GET | `/runs/{run_id}/state` | Get full run state |
| POST | `/runs/{run_id}/resume` | Resume an interrupted run |

### Current limitations

- Framework-level resume returns stubs for non-OpenAI backends
- Native mode (OpenAI backend `hitl_mode: native`) provides real SDK RunState resume via `Runner.run(agent, state)`
- SDK version upgrades may make previously saved `backend_state` non-deserializable
- No automatic retry after resume
- Multi-agent resume not yet implemented

See `docs/run_state.md` for full documentation.

## OpenAI Native HITL Mode

Phase 10 introduces native HITL mode that uses the OpenAI Agents SDK's built-in
`needs_approval` and `RunState` mechanism for real pause/resume.

### Configuration

```yaml
runtime:
  backend: openai
  openai:
    hitl_mode: native  # "wrapper" (default) or "native"
```

### How it works

**Wrapper mode** (default, Phase 8):
- Framework wraps tools with governance-aware wrappers
- Approval required returns `{"status": "approval_required"}` to the model
- RunState is NOT used

**Native mode** (Phase 10):
- Framework sets `needs_approval=True` on SDK `function_tool` for high-risk tools
- SDK natively interrupts the run when approval is needed
- `RunResult.to_state()` captures the full run state
- `RunState.approve()` / `RunState.reject()` resolves the interruption
- `Runner.run(agent, state)` resumes from the saved state

### Resume flow

```python
# 1. Run — gets interrupted
result = await app.run(agent="assistant", input="delete file X")
# result.status == "interrupted"
# result.backend_state contains serialized RunState

# 2. Approve
await app.approve(result.interruptions[0]["approval_id"], approver="manager")

# 3. Resume — calls backend.resume() which uses SDK RunState
resumed = await app.resume(result.run_id)
# resumed.status == "completed"
```

### Current limitations

- Native HITL requires SDK version with `needs_approval` / `RunState` support
- Streaming resume is minimal (state captured after stream completes)

## OpenAI Multi-Agent Workflows

Phase 11 extends the OpenAI backend to support the framework's multi-agent
workflow types: **handoff** (triage) and **orchestrator** (agents-as-tools).

### Handoff Workflow

```yaml
runtime:
  backend: openai
  openai:
    hitl_mode: wrapper  # or "native"

workflows:
  customer_support:
    type: handoff
    entry: triage
    agents:
      - refund
      - billing
      - technical_support
```

**How it works:**

1. Framework compiles the entry agent (`triage`) with `handoffs=[refund_agent, billing_agent, ...]`
2. SDK's `Agent.handoffs` enables the LLM to hand off to specialist agents
3. `Runner.run(entry_agent, input)` executes the handoff flow
4. `WorkflowTrace` records `handoff_candidates` step with the candidate list

**Trace structure:**

```python
WorkflowTrace(
    workflow_name="customer_support",
    workflow_type="handoff",
    entry_agent="triage",
    steps=[
        WorkflowStep(step_type="agent", agent_name="triage", ...),
        WorkflowStep(step_type="handoff_candidates", metadata={"agents": ["refund", "billing", ...]}),
        WorkflowStep(step_type="agent", agent_name="refund", ...),
    ]
)
```

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

**How it works:**

1. Framework compiles each specialist agent using `Agent.as_tool()` (SDK native)
2. Manager agent is compiled with specialist tools + its own tools
3. `Runner.run(manager_agent, input)` executes the orchestrator flow
4. `agent_calls` are extracted from SDK tool_calls
5. `WorkflowTrace` records `agent_tools` step

**Trace structure:**

```python
WorkflowTrace(
    workflow_name="research_assistant",
    workflow_type="orchestrator",
    entry_agent="manager",
    steps=[
        WorkflowStep(step_type="agent", agent_name="manager", ...),
        WorkflowStep(step_type="agent_tools", metadata={"agents_as_tools": ["researcher", "analyst", "writer"]}),
        WorkflowStep(step_type="agent", agent_name="researcher", ...),
    ]
)
```

### Backend Delegation

`AgentApp._run_workflow()` now checks if the backend supports `run_workflow()`:

- **OpenAIAgentsBackend** — delegates to `backend.run_workflow()` for handoff/orchestrator
- **DryRunBackend** — continues using framework `WorkflowExecutor` with heuristic routing

### Governance

- Business tools (low/high risk) continue to go through the framework's `ToolExecutor` governance pipeline
- Specialist agents-as-tools do **not** go through `ToolExecutor` — this is deferred to future phases
- HITL modes (`wrapper` / `native`) work as before for business tool calls

## DAG Workflows

DAG workflows provide a directed acyclic graph execution engine for building
complex multi-step pipelines with dependencies, branching, and subworkflows.

### Node Types

| Type | Description | Ref |
|------|-------------|-----|
| `agent` | Execute an agent via AppRunner | Agent name |
| `tool` | Execute a tool via ToolExecutor | Tool name |
| `function` | Execute a registered Python function | Function name |
| `subworkflow` | Execute a child DAG workflow | Workflow name |
| `if_else` | Conditional branch (then/else) | Condition expr |
| `switch` | Multi-way branch (cases/default) | Switch expr |

### Quick Example

```yaml
workflows:
  refund_flow:
    type: dag
    execution_mode: sequential
    nodes:
      - id: extract_order
        type: function
        function: order.extract_order_id
        inputs:
          text: input.message

      - id: query_order
        type: tool
        ref: order.query
        depends_on:
          - extract_order
        inputs:
          order_id: nodes.extract_order.output.order_id

      - id: route
        type: if_else
        depends_on:
          - query_order
        inputs:
          condition: "nodes.query_order.output.status == 'paid'"
        then:
          - calculate_refund
        else_branch:
          - send_rejection

      - id: calculate_refund
        type: function
        function: refund.calculate_amount
        depends_on:
          - route
        inputs:
          order_total: nodes.query_order.output.amount
```

### Features

- **Sequential/parallel execution** — `execution_mode: sequential` or `parallel`
- **Concurrency control** — `max_concurrency` limits parallel node execution
- **Retry policies** — per-node or workflow-level with exponential backoff
- **Conditions** — boolean expressions gating node execution
- **Timeouts** — per-node or workflow-level execution timeouts
- **Workflow deadline** — `deadline_seconds` limits total DAG execution time; enforced via `min(node_timeout, remaining_deadline)`
- **Deadline inheritance** — subworkflows inherit `min(parent_remaining, child_configured)` deadline
- **Deadline cancellation** — running tasks cancelled on deadline; pending nodes marked SKIPPED
- **Input mapping** — `input.*`, `nodes.*.output.*`, `context.*` patterns
- **Nested path access** — `nodes.a.output.data.amount` for deep structures
- **Function permissions** — permission checks against execution context
- **Subworkflow inheritance** — child DAGs inherit parent permissions
- **Cycle detection** — prevents recursive subworkflow references
- **Trace events** — per-node lifecycle events for observability
- **Compensation handlers** — best-effort rollback on failure, timeout, or deadline; executed in reverse completion order

### Compensation / Rollback

When a DAG workflow fails, times out, or exceeds its deadline, compensation
handlers provide best-effort rollback for nodes that completed successfully.
Only **completed** nodes with a `compensate` configuration are eligible.

#### Configuration

```yaml
workflows:
  refund_flow:
    type: dag
    execution_mode: sequential
    compensation:
      enabled: true                    # Required to activate compensation
      trigger_on:                      # Optional: default is ["failure", "timeout", "deadline"]
        - failure
        - timeout
        - deadline
      continue_on_failure: true        # Continue compensating even if a handler fails
      timeout_seconds: 5.0             # Max time for all compensation combined

    nodes:
      - id: extract_order
        type: function
        function: order.extract_order_id
        compensate:                    # Per-node compensation config
          function: order.revert_extraction
          inputs: {}
          timeout_seconds: 2.0         # Optional: handler-specific timeout
          retry:                       # Optional: retry policy for the handler
            max_attempts: 2
            backoff_seconds: 0.5

      - id: query_order
        type: tool
        ref: order.query
        depends_on: [extract_order]

      - id: process_refund
        type: function
        function: refund.process
        depends_on: [query_order]
        # No compensate block → not eligible for compensation
```

#### Compensation Rules

| Rule | Description |
|------|-------------|
| **Reverse completion order** | Handlers run in reverse topological order of completed nodes |
| **Completed nodes only** | Failed, skipped, or never-started nodes are never compensated |
| **Best-effort** | Handler failures are logged but don't re-trigger the workflow |
| **Independent timeout** | Compensation has its own timeout, separate from node execution timeout |
| **Backward compatible** | Omitting `compensate` or `compensation` preserves original behavior |

#### Return Value

When compensation is triggered, `execute()` returns a 4-tuple:

```python
results, status, output, compensation_result = await executor.execute(dag, input, context)

# compensation_result is None if:
#   - compensation is disabled
#   - workflow completed successfully
#   - no nodes had compensate handlers

if compensation_result:
    print(f"Status: {compensation_result.status}")  # completed | partial | failed | skipped
    print(f"Compensated: {compensation_result.compensated_nodes}")
    print(f"Skipped: {compensation_result.skipped_nodes}")
    print(f"Failed: {compensation_result.failed_nodes}")
```

#### Compensation Result Statuses

| Status | Meaning |
|--------|---------|
| `completed` | All eligible handlers succeeded |
| `partial` | Some handlers succeeded, others failed |
| `failed` | No handlers succeeded |
| `skipped` | Compensation was disabled or no candidates found |

#### Events

Seven compensation lifecycle events are emitted for observability:

- `workflow.compensation_started` — rollback phase begins
- `workflow.compensation_completed` — all handlers succeeded
- `workflow.compensation_failed` — rollback ended with failures
- `node.compensation_started` — handler begins executing
- `node.compensation_completed` — handler succeeded
- `node.compensation_failed` — handler raised an exception
- `node.compensation_skipped` — node had no handler or wasn't completed

### Condition DSL

Safe expression evaluator (never calls `eval()`):

```
nodes.<id>.status == "completed"
nodes.<id>.output.<field> == "value"
nodes.<id>.output.<field> > number
nodes.<id>.output.<field> IN ["a", "b", "c"]
nodes.<id>.output.<field> STARTS_WITH "prefix"
nodes.<id>.output.<field> ENDS_WITH ".txt"
<expr> AND <expr>
<expr> OR <expr>
NOT <expr>
```

## Idempotency

The framework supports optional idempotency keys for best-effort duplicate request
prevention at the API level. This is **not** an exactly-once guarantee — it is a
single-use enforcement that prevents the same operation from being executed twice
with identical parameters.

### How it works

1. **Optional key** — pass `idempotency_key` when calling `app.run()` or via the
   `Idempotency-Key` HTTP header / JSON body when using FastAPI. Without a key,
   behavior is unchanged.
2. **Fingerprint** — the framework computes a SHA-256 fingerprint of the request
   payload (excluding transient fields like `worker`, `trace_id`).
3. **Scope** — keys are scoped to `{tenant_id}:{operation}` to prevent
   cross-tenant collisions.
4. **Atomic reservation** — the key is atomically reserved before any side-effect
   operation. A duplicate returns a conflict error (HTTP 409 via FastAPI).
5. **Mismatch rejection** — reusing a key with different parameters is rejected
   as a potential replay attack.

### Usage

```python
# Python API
result = await app.run(
    agent="assistant",
    input="Process order #123",
    idempotency_key="order-123-process",
    tenant_id="acme",
)
```

```bash
# FastAPI — HTTP header (preferred)
curl -X POST http://localhost:8000/runs \
  -H "Idempotency-Key: order-123-process" \
  -H "Content-Type: application/json" \
  -d '{"agent": "assistant", "input": "Process order #123", "tenant_id": "acme"}'

# FastAPI — JSON body
curl -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{"agent": "assistant", "input": "Process order #123", "tenant_id": "acme", "idempotency_key": "order-123-process"}'
```

### Error responses

| Scenario | Error type | HTTP status |
|----------|-----------|-------------|
| Same key, same fingerprint | `DuplicateIdempotencyKeyError` | 409 Conflict |
| Same key, different fingerprint | `IdempotencyKeyMismatchError` | 409 Conflict |

### Current limitations

- Best-effort only — concurrent requests with the same key may both succeed if
  they arrive simultaneously before either is registered (SQLite UNIQUE constraint
  prevents this within a single process, but network-level races are possible).
- No distributed worker backend — enforcement is local to the process handling
  the request.
- Lease renewal is best-effort — does NOT provide exactly-once guarantee; only
  works while the current process is alive; renewal failure raises `LeaseLostError`.
- Scope is fixed to `{tenant_id}:{operation}` — cannot be customized per-request.

## Recovery Admin Console

The optional Recovery Admin Console is a server-rendered FastAPI UI for recovery
status, dry-run candidate scans, run-scoped history, and explicit two-step live
recovery confirmation. Install API support before mounting it:

```bash
pip install 'agent-app-framework[api]'
```

Mount it explicitly and always provide an admin authorization dependency:

```python
from agent_app.adapters.recovery_ui import create_recovery_ui_router

api.include_router(
    create_recovery_ui_router(app, admin_dependency=require_recovery_admin)
)
```

If `admin_dependency` is omitted, all UI routes return HTTP 403. The UI does not
start the recovery daemon, GET routes are read-only, scans remain dry-run, and
live recovery requires a confirmation token plus `confirm_no_dry_run=true`.

See [`docs/recovery_admin_console.md`](docs/recovery_admin_console.md) for the
full route list, safety defaults, HMAC token details, and current limitations.

## Policy Replay & Regression

The Policy Replay system re-evaluates historical policy decisions against the
current policy configuration to detect regressions before deployment.

```bash
# Synchronous replay
agentapp policy replay --config examples/customer_support/agentapp.yaml

# Background job (persisted with SQLite)
agentapp policy replay --config examples/customer_support/agentapp.yaml \
  --background --store sqlite --db-path .agent_app/policy_replays.db

# Run a background job
agentapp policy run-job job_abc123... --config examples/customer_support/agentapp.yaml

# List recent jobs
agentapp policy jobs --config examples/customer_support/agentapp.yaml
```

Console pages (`/policy-console/replays` and `/policy-console/replay-jobs`) are
available when the console is enabled. Replay results persist across restarts when
using the SQLite store.

See [`docs/policy_replay.md`](docs/policy_replay.md) for full documentation.

## Current limitations

- **OpenAI backend multi-agent** — handoff targets and orchestrator agent_calls are traced but not fully extracted from SDK results
- **Agent-as-tool governance** — specialist agents-as-tools do not go through framework ToolExecutor
- **Run state** — framework-level resume returns stubs; native mode (Phase 10) provides real SDK RunState resume
- **DryRunBackend tool matching** — uses keyword heuristics, not real LLM reasoning
- **DAG workflows** — fully implemented (Phase 13.1–15); supports sequential/parallel execution, retry policies, conditions, timeouts, function nodes, subworkflow nodes, if/else branching, switch routing, compensation handlers, persisted execution state, explicit DAG resume, and workflow-run lease management
- **DAG execution state** — explicit resume via `app.resume_workflow_run()`; completed nodes skipped with output reuse; interrupted nodes retried; failed nodes configurable via ResumePolicy; compensation started blocks resume by default; workflow-run lease provides best-effort ownership; idempotency key provides best-effort duplicate prevention; no distributed locking; no automatic resume on app restart; Phase 16.5 adds recovery scanner and manual recovery with lease protection
- **Compensation** — best-effort rollback only; handler failures are logged but not retried at workflow level; compensation timeout is shared across all handlers
- **Routing** — keyword/regex/default matching, not semantic LLM routing
- **Parallel orchestrator** — specialists are called serially, not in parallel
- **Eval runner** — validates framework governance logic, not model quality
- **SQLite stores** — basic implementation without connection pooling or migration
- **Observability** — Tier 2 (tool/workflow/approval) events are fire-and-forget; use Tier 1 events (`run.started`, `run.interrupted`) for eval assertions
- **Benchmark** — `scripts/benchmark_tracing.py` is a rough measurement, not a rigorous performance test
- **Distributed execution** — Phase 15 provides lease and idempotency foundations; Phase 15.1 adds API-level idempotency enforcement; Phase 15.2 adds background lease renewal heartbeat; Phase 16.0 adds DAG execution snapshots for crash recovery; Phase 16.1 adds compensation state persistence for recovery of interrupted compensation; Phase 16.2 adds pluggable lease backend abstraction for future Redis/etcd integration; Phase 16.3 adds lease backend observability (metrics, health checks, diagnostics); Phase 16.4 adds Redis lease backend for cross-process coordination; Phase 16.5 adds recovery scanner and manual recovery with lease protection; Phase 17 adds automatic recovery daemon (conservative, dry-run by default, not auto-started); Phase 18 adds recovery observability and admin API (status, history, scan, recover); no Celery/Temporal backend; no worker pool; Redis backend is best-effort only — not exactly-once

## Observability

The framework includes a structured tracing system that records events across the
entire execution lifecycle: runs, workflows, agents, tools, approvals, and
run-state transitions.

### Quick Start

Add tracing to your config:

```yaml
# agentapp.yaml
observability:
  tracing:
    type: memory   # noop | memory | jsonl
```

View traces via CLI:

```bash
# List all traces (table format)
agentapp trace list --config examples/customer_support/agentapp.yaml

# Filter by tenant and event type
agentapp trace list --config examples/customer_support/agentapp.yaml \
  --tenant-id eval_tenant --event-type run.interrupted --limit 20

# JSON output (for scripts)
agentapp trace list --config examples/customer_support/agentapp.yaml --json

# Show a specific trace (human-readable)
agentapp trace show tr_abc123 --config examples/customer_support/agentapp.yaml

# Show a specific trace (JSON)
agentapp trace show tr_abc123 --config examples/customer_support/agentapp.yaml --json
```

### Eval Integration

Assert trace events in eval suites. **Only Tier 1 (synchronous) events** are
reliable for eval YAML assertions — these are the events captured by
`AppRunner`'s local buffer before the run method returns:

```yaml
expect:
  status: interrupted
  trace_events:
    - run.started
    - run.interrupted
```

**Tier 2 events** (workflow, tool, approval, run_state) are emitted via
fire-and-forget and are not guaranteed to appear in `result.trace_events` at
assertion time. Verify Tier 2 events through collector-level unit tests
instead.

### FastAPI Endpoints

```http
GET /traces?run_id=&tenant_id=&event_type=&limit=50
GET /traces/{trace_id}
```

**Response — `GET /traces`:**

```json
[
  {
    "trace_id": "tr-abc123",
    "run_id": "run-xyz",
    "event_count": 3,
    "first_event_at": "2024-01-01T10:00:00+00:00",
    "last_event_at": "2024-01-01T10:00:05+00:00",
    "status": "completed"
  }
]
```

**Response — `GET /traces/{trace_id}`:**

```json
{
  "trace_id": "tr-abc123",
  "run_id": "run-xyz",
  "events": [
    {
      "event_id": "ev_001",
      "event_type": "run.started",
      "timestamp": "2024-01-01T10:00:00+00:00",
      "run_id": "run-xyz",
      "user_id": "u1",
      "tenant_id": "t1"
    }
  ]
}
```

### Event Reliability

| Tier | Events | Eval YAML safe? |
|------|--------|-----------------|
| 1 — Synchronous | `run.started`, `run.completed`, `run.failed`, `run.interrupted` | Yes |
| 2 — Fire-and-forget | workflow, tool, approval, run_state events | No — use collector tests |

See [docs/observability.md](docs/observability.md) for full documentation.

### Memory Retention

Prevent unbounded memory growth in long-running processes:

```yaml
observability:
  tracing:
    type: memory
    max_traces: 1000           # Keep at most 1000 traces (oldest evicted)
    max_events_per_trace: 500  # Keep at most 500 events per trace
```

### JSONL Maintenance

For JSONL trace files, use the built-in maintenance utilities:

```python
from agent_app.observability.exporters import JSONLTraceCollector

collector = JSONLTraceCollector(".agent_app/traces.jsonl")
print(await collector.count_events())   # Total events
print(await collector.count_traces())   # Distinct traces

# Compact: keep only the 100 most recent events per trace
# Invalid JSON lines are skipped silently; atomic in-place when no output_path
await collector.compact(max_events_per_trace=100)
```

### OpenTelemetry Bridge (Experimental)

Optional OpenTelemetry span export:

```bash
pip install 'agent-app-framework[otel]'
```

```python
from agent_app.observability.otel import OpenTelemetryTraceExporter
exporter = OpenTelemetryTraceExporter(service_name="my-app")
await exporter.export_events(trace_events)
```

Currently maps RunEvents to in-memory spans. OTLP export and distributed
propagation are planned for a future phase.

### Benchmark

Measure trace recording overhead locally:

```bash
python scripts/benchmark_tracing.py --runs 100
python scripts/benchmark_tracing.py --runs 1000 --collector noop
```

This is a rough benchmark using `DryRunBackend` (no real API calls).

### Event Types

| Category | Events |
|----------|--------|
| Run | `run.started`, `run.completed`, `run.failed`, `run.interrupted` |
| Workflow | `workflow.started`, `workflow.completed`, `workflow.failed`, `routing.decision`, `handoff.occurred` |
| Agent | `agent.started`, `agent.completed`, `agent.failed` |
| Tool | `tool.started`, `tool.completed`, `tool.failed`, `tool.permission_denied`, `tool.approval_required` |
| Approval | `approval.created`, `approval.approved`, `approval.rejected` |
| Run State | `run_state.saved`, `run_state.resumed` |

See [docs/observability.md](docs/observability.md) for full documentation.

## Roadmap

- v0.4 — Plugin system for custom backends and stores
- v0.4 — Multi-turn conversation management with memory
- v0.4 — Observability: structured tracing, metrics export
- v0.5 — OpenAI backend governance integration ✅ (Phase 8)
- v0.6 — Run state persistence and framework-level resume ✅ (Phase 9)
- v0.7 — Real OpenAI RunState resume with native HITL ✅ (Phase 10)
- v0.8 — Multi-agent handoff/orchestrator with OpenAI backend ✅ (Phase 11)
- v0.9 — Structured observability and tracing ✅ (Phase 12)
- v0.10 — DAG workflow support ✅ (Phase 13.1–13.9)
- v0.10 — Persisted DAG execution state and crash recovery foundation ✅ (Phase 14.0)
- v0.10 — Explicit DAG resume semantics ✅ (Phase 14.1)
- v0.10 — Distributed execution readiness (lease, idempotency) ✅ (Phase 15)
- v0.10 — API-level idempotency enforcement (request fingerprinting, scope isolation, atomic SQLite reservation, HTTP 409 mapping) ✅ (Phase 15.1)
- v0.10 — Background lease renewal / heartbeat for long-running DAG workflows ✅ (Phase 15.2)
- v0.10 — DAG persistence snapshots for recovery after process exit / lease expiry ✅ (Phase 16.0)
- v0.10 — Compensation state persistence for recovery of interrupted compensation runs ✅ (Phase 16.1)
- v0.10 — Pluggable lease backend abstraction for future Redis/etcd integration ✅ (Phase 16.2)
- v0.10 — Lease backend observability: metrics, health checks, diagnostics for production operations ✅ (Phase 16.3)
- v0.10 — Redis lease backend for cross-process / cross-worker lease coordination (optional extra) ✅ (Phase 16.4)
- v0.10 — Recovery scanner and manual recovery with lease protection for DAG workflow runs ✅ (Phase 16.5)
- v0.10 — Automatic recovery daemon with policy-driven scan/recover cycles (dry-run by default, conservative) ✅ (Phase 17)
- v0.10 — Recovery observability and admin API: status, inspect, history, scan-once, recover, optional FastAPI router ✅ (Phase 18)
- v0.14 — Recovery Admin Console: server-rendered UI for recovery management with dry-run/live safety boundaries ✅ (Phase 19)
- v0.14 — OpenAI tool interception and RunState resume with approval governance ✅ (Phase 20)
- v0.14 — Multi-agent handoff/orchestrator with governance propagation ✅ (Phase 22)
- v0.15 — Policy engine with rule-based tool allow/deny/condition enforcement ✅ (Phase 23)
- v0.15 — Policy Decision Store: trace-level decision recording with InMemory + SQLite persistence ✅ (Phase 25)
- v0.15 — Policy Console Lite: read-only governance dashboard with decision history and report pages ✅ (Phase 26)
- v0.15 — Policy Replay & Regression Dashboard: re-evaluate historical decisions against current policy ✅ (Phase 27)
- v0.16 — Persistent policy replay, background jobs, context reconstruction with SQLite stores ✅ (Phase 28)
- v0.17 — Policy release gates & versioned policy bundles with lifecycle management ✅ (Phase 29)
- v0.18 — Policy promotion approval, RBAC, and console write governance ✅ (Phase 30)
- v0.19 — Policy runtime activation, environment isolation, and hot reload baseline ✅ (Phase 31)
- v0.20 — Policy rollback, emergency disable, and activation safety controls ✅ (Phase 32)
- v0.21 — Release rings, canary evaluation, and ring-aware policy resolution ✅ (Phase 33)
- v0.22 — Runtime Reload Hooks, Cache Invalidation, and Deterministic Canary Routing ✅ (Phase 34)
- **Phase 35**: Multi-Environment Rollout Orchestration — rollout plans, step-by-step execution, gate/eval checks, approval blocking
- **Phase 36** — Rollout Approval Workflow
- **Phase 37**: Separation of Duties and Multi-Approver Approval Policies — quorum approvals, separation-of-duties checks, role/permission constraints, approval expiration
- **Phase 38**: Runtime Policy Enforcement Points and Unified Approval Governance — tool execution enforcement, resume enforcement, runtime policy rules, CLI/console management
- **Phase 39**: Policy Observability, Analytics, and Compliance Reporting — enforcement decision analytics, approval latency, JSON/CSV export, console dashboard
- **Phase 40**: Policy Testing, Validation, and Historical Replay — simulation framework, audit-to-case extraction, candidate policy stores, policy validation, CLI/console simulation
- **Phase 41**: Policy Gate Integration and Automated Safeguards — simulation gate evaluator, configurable threshold rules, blocking CLI exit codes, gate reports, CI/CD integration
- **Phase 42**: Policy Release Automation and Simulation Gate Enforcement — gate requirement lifecycle, promotion/rollout enforcement, release gate automation service, CLI gate commands, console gate pages
- **Phase 43**: Policy Rollout Automation with Simulation Gates — rollout gate modes (DISABLED/MANUAL/AUTO), failure actions (BLOCK/FAIL/SKIP), RolloutGateAutomationService, AUTO step gate execution, CLI rollout gate commands, console rollout gate pages
- **Phase 44**: Notification Hooks and Expiration Workers — notification rules and channels, expiration sweep service, optional in-process worker, CLI notification/expiration commands, console notification/expiration pages
- [x] **Phase 45**: Policy Rollout Analytics, History, and Gate Outcome Reporting
- [x] **Phase 46**: Policy Rollout Federation and Conflict Detection
- [x] **Phase 47**: Federation Observability and Reporting
- [x] **Phase 48**: Federation Approval Workflows
- **Phase 49**: Federation Approval Notification & Escalation Workers — notification adapters, outbox store, dispatch service, escalation worker, distributed lock, CLI commands, console pages
- **Phase 50**: Federation Approval Dead-Letter Queue & Scheduled Worker — DLQ for failed notifications, retry policies with per-channel overrides, persistent scheduled worker for notification dispatch and escalation
- **Phase 51**: Federation Notification Templates, Preferences & Webhook Replay — configurable templates with safe variable substitution, preference management with opt-in/opt-out, HMAC-SHA256 webhook signing, original-payload DLQ replay
- **v0.40.0** — Federation notification observability: delivery event tracking, metrics aggregation, channel health, SLA monitoring, alert rules, CLI/console observability commands, and report exports
- **v0.41.0** — Federation notification external alert delivery: alert delivery service with target matching and retry, Prometheus metrics export, JSONL structured export, retention/archive/purge service, metrics rollup, CLI commands, and console pages
- **v0.42.0** — Alert delivery productionization: retry scheduler, DLQ replay, alert deduplication, incremental rollup with checkpoints, HMAC-SHA256 webhook signing, archive cleanup, Phase 54 CLI commands and console pages
- **v0.35.0** — Alert delivery closed loop: retry daemon with configurable interval/jitter, priority queue with severity-to-priority mapping, resumable archive cleanup with checkpoints, 10 new PolicyChangeEventType values, daemon/priority/archive-cleanup CLI commands, change event wiring across all Phase 55 services
