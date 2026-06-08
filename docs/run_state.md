# Run State Persistence — Design Reference

## Overview

The framework provides a run state persistence abstraction that records
interrupted runs, enabling resume capability. Phase 9 introduced the framework-
level `RunStateStore`. Phase 10 extended it with **OpenAI native HITL** — the
framework connects its `RunStateStore` with the OpenAI Agents SDK's native
`RunState` pause/resume mechanism.

**Key distinction:**

- **Framework-level** (`RunStateStore`) — persists `InterruptedRun` records,
  tracks approvals, enables framework-level resume stubs
- **SDK-native** (`backend_state`) — serializes SDK `RunState` via `to_json()`,
  enables real `Runner.run(agent, state)` resume via `OpenAIAgentsBackend.resume()`

---

## What Problem Does This Solve?

Before Phase 9:

- When a run was interrupted (e.g., approval required), the framework returned
  an `AppRunResult` with `status="interrupted"`
- The interruption existed only in memory — if the process restarted, the state
  was lost
- `AgentApp.resume()` was a stub that didn't read persisted state
- There was no way to query "which runs are currently interrupted?"

After Phase 9:

- Interrupted runs are persisted to a `RunStateStore` (memory or SQLite)
- `AgentApp.resume()` reads from the store, checks approval status, and
  returns appropriate results
- Framework-level resume is functional (with framework-level stubs for backend
  execution)
- FastAPI endpoints expose run state for monitoring and management

---

## Components

### RunStateStatus

```python
class RunStateStatus(str, Enum):
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"
    RESUMED = "resumed"
```

### InterruptedRun

```python
class InterruptedRun(BaseModel):
    run_id: str
    status: str  # RunStateStatus value
    agent_name: str | None
    workflow_name: str | None
    workflow_type: str | None
    input: str
    context: RunContext
    interruptions: list[dict[str, Any]]
    approval_ids: list[str]
    backend_name: str  # "dry_run" or "openai"
    backend_state: dict[str, Any]  # Backend-specific state (e.g. OpenAI RunState JSON for native HITL resume)
    result_snapshot: dict[str, Any] | None
    created_at: datetime  # timezone-aware UTC
    updated_at: datetime
    resumed_at: datetime | None
    error: dict[str, Any] | None
```

Key methods:

- `extract_approval_ids()` — pulls approval IDs from interruptions
- `is_resumable()` — returns True if status=INTERRUPTED and has approval IDs

### RunStateStore Protocol

```python
class RunStateStore(Protocol):
    async def save_interrupted(self, run: InterruptedRun) -> InterruptedRun: ...
    async def get(self, run_id: str) -> InterruptedRun: ...
    async def mark_resumed(self, run_id: str) -> InterruptedRun: ...
    async def mark_completed(self, run_id: str) -> InterruptedRun: ...
    async def mark_failed(self, run_id: str, error: dict[str, Any]) -> InterruptedRun: ...
    async def list_interrupted(self, tenant_id: str | None = None) -> list[InterruptedRun]: ...
```

### Implementations

| Store | Persistence | Use Case |
|-------|-------------|----------|
| `InMemoryRunStateStore` | No (process memory) | Development, testing |
| `SQLiteRunStateStore` | Yes (file) | Production, multi-instance |

---

## How It Works

### Saving an Interrupted Run

```
AgentApp.run()
  → AppRunner.run()
    → Backend.run() returns status="interrupted"
      → _save_interrupted_run()
        → RunStateStore.save_interrupted()
        → AuditLogger.log("run.interrupted")
```

The `_save_interrupted_run` method:

1. Extracts approval IDs from `result.interruptions`
2. Creates an `InterruptedRun` with full context
3. Saves to the store
4. Writes an audit event

### Resuming a Run

```
AgentApp.resume(run_id)
  → RunStateStore.get(run_id)
    → Check approval status for all approval_ids
      → All pending → return interrupted
      → Any rejected → mark completed with rejection message
      → All approved → mark resumed → return completed stub
```

### DryRunBackend Resume

For DryRunBackend, resume returns a completed stub with a message:
```
"Run '{run_id}' approved and resumed. (Framework-level resume — native backend resume not implemented.)"
```

### OpenAI Backend Resume

For OpenAI backend with `hitl_mode: native` (Phase 10), resume performs real
SDK RunState resume:

1. `AgentApp.resume()` detects native mode with `backend_state`
2. Calls `OpenAIAgentsBackend.resume()` with the saved state
3. Backend deserializes `RunState` via `from_json()`
4. Applies approval/rejection via `RunState.approve()` / `reject()`
5. Calls `Runner.run(agent, state)` to resume execution
6. Returns `AppRunResult` with the resumed output

For `hitl_mode: wrapper` or DryRunBackend, resume returns a framework-level
stub noting that native resume is not available.

---

## Configuration

```yaml
# Default: in-memory store
runtime:
  backend: dry_run

# SQLite persistence
runtime:
  run_state:
    type: sqlite
    path: .agent_app/run_states.db

# Flat format also works
runtime:
  run_state_type: sqlite
  run_state_path: .agent_app/run_states.db
```

---

## Relationship with Other Stores

| Store | Purpose | Persistence |
|-------|---------|-------------|
| `ApprovalStore` | Tracks individual approval request status | Configurable (memory/sqlite) |
| `AuditLogger` | Records governance events | Configurable (memory/sqlite) |
| `SessionStore` | Conversation history | Configurable (memory/sqlite) |
| `RunStateStore` | Framework-level interrupted run state | Configurable (memory/sqlite) |

`RunStateStore` is the highest-level store. It references:
- `ApprovalStore` — to check if approvals are resolved
- `AuditLogger` — to record run lifecycle events
- `SessionStore` — for conversation context (separate concern)

---

## Current Limitations

1. **Framework-level resume returns stubs** — DryRunBackend and framework-level
   resume return stub results. Native mode (OpenAI backend with `hitl_mode: native`)
   provides real SDK RunState resume via `Runner.run(agent, state)`.
2. **No automatic retry** — Once resumed, the run doesn't automatically
   continue from where it left off.
3. **SDK version coupling** — Serialized `RunState` is tied to the SDK version.
   Upgrading `openai-agents` may change the RunState schema, making previously
   saved `backend_state` non-deserializable.
4. **Multi-agent resume not implemented** — Handoff/orchestrator workflow resume
   uses framework-level stubs.

---

## FastAPI Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/runs/interrupted` | List interrupted runs (supports `?tenant_id=`) |
| GET | `/runs/{run_id}/state` | Get full run state |
| POST | `/runs/{run_id}/resume` | Resume an interrupted run |

These endpoints are available when FastAPI is installed.

---

## backend_state

`InterruptedRun.backend_state` is a `dict[str, Any]` that stores
**backend-specific state needed for resume**. The framework persists it but
does not interpret its contents — each backend is responsible for serializing
and deserializing its own state.

### OpenAI Native Mode

When `runtime.openai.hitl_mode: native`, `backend_state` contains a
serialized SDK `RunState`:

```json
{
  "backend": "openai",
  "hitl_mode": "native",
  "run_state": {
    "$schemaVersion": "1.10",
    "current_agent": {"name": "assistant"},
    "original_input": "delete file X",
    "interruptions_count": 1
  },
  "run_state_serialization": "to_json",
  "approval_map": {
    "call_abc123": "apv_framework_789"
  }
}
```

| Key | Description |
|-----|-------------|
| `backend` | Backend type (`"openai"` or `"dry_run"`) |
| `hitl_mode` | HITL mode (`"native"` or `"wrapper"`) |
| `run_state` | Serialized SDK RunState (native mode only) |
| `run_state_serialization` | Method: `"to_json"`, `"to_dict"`, `"dataclass"`, `"repr"` |
| `approval_map` | Maps SDK `call_id` → framework `approval_id` |

**Serialization methods** (tried in order):

1. `state.to_json()` → dict (preferred)
2. `state.to_dict()` → dict (fallback)
3. `dataclasses.asdict(state)` → dict
4. `repr(state)` → string (non-resumable, flagged with `_non_resumable: true`)

### JSON Serializability

`backend_state` must be JSON-serializable for SQLite persistence. All
serialization methods produce JSON-compatible output.

### SQLite Roundtrip

`SQLiteRunStateStore` stores `backend_state` as a JSON string. On retrieval,
it is parsed back to a dict. Framework-level resume via `RunStateStore` works
regardless of whether native mode backend_state is deserializable.

### SDK Version Compatibility

Serialized `RunState` is SDK-version-dependent. Upgrading `openai-agents` may
change the schema, making old `backend_state` non-deserializable. In that case,
`_deserialize_run_state()` returns an error — the `InterruptedRun` record and
approval history are still preserved for framework-level operations.
