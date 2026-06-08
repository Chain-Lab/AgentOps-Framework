# Observability — Structured Tracing

## Overview

The framework provides a lightweight, extensible observability layer based on
structured **RunEvents**. Every significant action in the execution lifecycle
emits a typed event that can be recorded, queried, and exported.

```
RunEvent
  ├── event_id        — unique per event
  ├── trace_id        — groups events for a single run
  ├── run_id          — logical run identifier
  ├── event_type      — dot-separated category
  ├── timestamp       — timezone-aware UTC datetime
  ├── user_id / tenant_id
  ├── workflow_name / workflow_type / agent_name / tool_name / approval_id
  ├── status          — short outcome string
  ├── duration_ms     — wall-clock duration
  ├── error           — structured error dict
  └── data            — arbitrary JSON-serializable extra data
```

## Trace Lifecycle

```
run.started
  ├── workflow.started  (if multi-agent workflow)
  │     ├── routing.decision  (if routing policy present)
  │     ├── handoff.occurred  (handoff workflow only)
  │     └── agent.started
  │           └── tool.started
  │                 ├── tool.completed    (success)
  │                 ├── tool.failed       (error)
  │                 ├── tool.permission_denied
  │                 └── tool.approval_required
  │                       └── approval.created
  │                             ├── approval.approved
  │                             └── approval.rejected
  └── run.completed
       ├── run.interrupted   (approval required)
       └── run.failed        (error)
```

## Built-in Event Types

### Run Events

| Event | When |
|-------|------|
| `run.started` | A run begins |
| `run.completed` | Run finishes successfully |
| `run.failed` | Run ends with error |
| `run.interrupted` | Run paused for approval |

### Workflow Events

| Event | When |
|-------|------|
| `workflow.started` | Workflow execution begins |
| `workflow.completed` | Workflow finishes |
| `workflow.failed` | Workflow errors |
| `routing.decision` | Router selects a target agent |
| `handoff.occurred` | Agent hands off to another |

### Agent Events

| Event | When |
|-------|------|
| `agent.started` | Agent execution begins |
| `agent.completed` | Agent finishes |
| `agent.failed` | Agent errors |

### Tool Events

| Event | When |
|-------|------|
| `tool.started` | Tool call begins |
| `tool.completed` | Tool finishes successfully |
| `tool.failed` | Tool errors or not found |
| `tool.permission_denied` | Permission check failed |
| `tool.approval_required` | Tool needs human approval |

### Approval Events

| Event | When |
|-------|------|
| `approval.created` | Approval request created |
| `approval.approved` | Human approved |
| `approval.rejected` | Human rejected |

### Run State Events

| Event | When |
|-------|------|
| `run_state.saved` | Interrupted run persisted |
| `run_state.resumed` | Run resumed after approval |

## Configuration

Add tracing configuration to `agentapp.yaml`:

```yaml
observability:
  tracing:
    type: memory     # noop | memory | jsonl
    path: .agent_app/traces.jsonl   # used when type=jsonl
    include_inputs: false
    include_outputs: false
```

| Type | Description |
|------|-------------|
| `noop` | Tracing disabled (zero overhead) |
| `memory` | In-process storage (default, good for tests) |
| `jsonl` | Append-only JSONL file (good for local debugging) |

## FastAPI Trace Endpoints

When using the FastAPI adapter:

```http
GET /traces?run_id=&tenant_id=&event_type=&limit=50
GET /traces/{trace_id}
```

- `/traces` — list `TraceSummary` objects with optional filtering by `run_id`, `tenant_id`, `event_type`, and `limit` (default 50). Returns `[]` when no collector is configured.
- `/traces/{trace_id}` — full event list for a specific trace. Returns 404 if trace not found or collector not configured.

**Response example — `GET /traces`:**

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

**Response example — `GET /traces/tr-abc123`:**

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
      "tenant_id": "t1",
      "status": null,
      "data": {}
    }
  ]
}
```

## CLI Trace Commands

```bash
# List traces (table output)
agentapp trace list --config examples/customer_support/agentapp.yaml

# List traces with filters
agentapp trace list --config examples/customer_support/agentapp.yaml \
  --tenant-id eval_tenant --event-type run.interrupted --limit 20

# List traces as JSON (for scripts)
agentapp trace list --config examples/customer_support/agentapp.yaml --json

# Show trace details (human-readable)
agentapp trace show tr_abc123 --config examples/customer_support/agentapp.yaml

# Show trace details as JSON
agentapp trace show tr_abc123 --config examples/customer_support/agentapp.yaml --json
```

**Table output example:**

```
Trace ID           Run ID             Events  Status     Last Event
---------------------------------------------------------------------------
tr-abc123          run-xyz               3   completed  2024-01-01T10:00:05
tr-def456          run-uvw               1   interrupted 2024-01-01T10:01:00
```

**JSON list output example:**

```json
{
  "traces": [
    {
      "trace_id": "tr-abc123",
      "run_id": "run-xyz",
      "event_count": 3,
      "status": "completed",
      "last_event_at": "2024-01-01T10:00:05"
    }
  ],
  "total": 1
}
```

**Exit codes:** `0` on success, `1` on error (missing config, trace not found, collector not configured).

## Eval Trace Events Assertion

Add `trace_events` to eval expectations to assert on **Tier 1 (synchronous)**
events that are captured in `AppRunResult.trace_events` before the run method returns.

```yaml
expect:
  status: interrupted
  trace_events:
    - run.started
    - run.interrupted
```

**Why only Tier 1 events in eval YAML?**

Eval suites run via `agentapp eval run` (subprocess) or `EvalRunner.run_suite()`
(in-process). In both paths, `result.trace_events` only contains events from
`AppRunner`'s local buffer — `run.started`, `run.completed`, `run.failed`,
`run.interrupted`. Tier 2 events (workflow, tool, approval, run_state) are
emitted via `asyncio.create_task()` and are not guaranteed to be captured in
`result.trace_events` before assertions run.

To verify Tier 2 events, use collector-level tests (unit tests with
`InMemoryTraceCollector`) where the event loop can flush before assertions.

**Assertion behavior:**
- All listed event types must appear in the recorded trace (substring match)
- Order is not required
- Failure messages list the actually recorded events for debugging
- Empty `trace_events` list means no assertion (always passes)

## Programmatic Access

```python
from agent_app.config.loader import build_app

app = build_app("examples/customer_support/agentapp.yaml")

# After a run, access trace events from the result
result = await app.run(workflow="customer_support", input="I need a refund")
print(result.trace_id)
for event in result.trace_events:
    print(event.event_type, event.timestamp)

# Or query via the collector directly
collector = app.trace_collector
events = await collector.get_events(result.trace_id)
traces = await collector.list_traces(tenant_id="my-tenant")
```

## Event Reliability Levels

Not all trace events are equally available at all points in the execution lifecycle.
Understanding the two reliability tiers prevents flaky assertions and incorrect assumptions.

### Tier 1 — Synchronous (stable for immediate assertions)

Events appended to `AppRunner`'s local buffer via `_record_event()` are copied
onto `AppRunResult.trace_events` by `_attach_trace()` **before the run method
returns**. These events are synchronously available and safe to assert on
immediately after `app.run()` or `app.stream()` completes.

| Event types | Emitted by | Availability |
|-------------|-----------|--------------|
| `run.started`, `run.completed`, `run.failed`, `run.interrupted` | `AppRunner` | **Immediate** — in `result.trace_events` at return time |

**Eval YAML usage:** Safe to list in `trace_events` expectations.
```yaml
expect:
  trace_events:
    - run.started
    - run.interrupted
```

### Tier 2 — Fire-and-forget (eventually recorded)

Events emitted by `ToolExecutor`, `WorkflowExecutor`, and `AgentApp` are sent
directly to the `TraceCollector` via `await collector.record(event)`. In most
call paths this is wrapped in `asyncio.create_task()`, meaning the event is
scheduled on the event loop but **not awaited** before the caller proceeds.

| Event types | Emitted by | Availability |
|-------------|-----------|--------------|
| `workflow.started`, `workflow.completed`, `workflow.failed` | `WorkflowExecutor` | After event loop processes pending tasks |
| `routing.decision`, `handoff.occurred` | `WorkflowExecutor` | After event loop processes pending tasks |
| `agent.started`, `agent.completed`, `agent.failed` | `WorkflowExecutor` | After event loop processes pending tasks |
| `tool.started`, `tool.completed`, `tool.failed` | `ToolExecutor` | After event loop processes pending tasks |
| `tool.permission_denied`, `tool.approval_required` | `ToolExecutor` | After event loop processes pending tasks |
| `approval.created`, `approval.approved`, `approval.rejected` | `ToolExecutor` / `AgentApp` | After event loop processes pending tasks |
| `run_state.saved`, `run_state.resumed` | `AgentApp` | After event loop processes pending tasks |

**Eval YAML usage:** Do NOT list in `trace_events` expectations. These events
are verified through collector-level tests or in-process unit tests where the
event loop can be flushed before assertions.

### Why the two tiers?

The local buffer pattern keeps the hot path (every tool call, every agent step)
free of `await` points. Removing it would require threading `await` through
synchronous call sites in `AppRunner`, degrading throughput. The collector path
accepts this eventual consistency because production consumers (JSONL export,
FastAPI endpoints, CLI) read from the collector after the run has fully completed.

### Verifying Tier 2 events in tests

```python
# Correct: query the collector after async work completes
events = await collector.get_events(trace_id)
assert RunEventType.TOOL_STARTED in [e.event_type for e in events]

# Incorrect: checking result.trace_events for tool-level events
# (tool events are not in the AppRunner local buffer)
assert RunEventType.TOOL_STARTED in [e.event_type for e in result.trace_events]  # may fail
```

## Current Limitations

- **Fire-and-forget recording** — Tier 2 events are recorded via `asyncio.create_task()`
  in some paths; extreme load may drop events before they reach the collector.
  Use Tier 1 events (`result.trace_events`) for immediate assertions.
- **No drain/flush API** — There is no collector-level `drain()` method to wait
  for pending async tasks. For tests that need Tier 2 events, query the collector
  directly after the async call chain completes (the event loop will have
  processed pending tasks by then). A drain API is deferred; see
  [docs/observability.md#event-reliability-levels](docs/observability.md#event-reliability-levels).
- **No OpenTelemetry** — OpenTelemetry adapter is planned for a future phase.
- **No visual dashboard** — Trace viewing is via CLI, API, or JSONL file.
- **Pydantic json_encoders** — Uses deprecated `json_encoders` config; will
  migrate to `field_serializer` in a future update.
- **Memory collector is per-process** — `InMemoryTraceCollector` does not
  survive process restarts; use `JSONLTraceCollector` for persistence.
- **Retention only for memory collector** — `max_traces` and `max_events_per_trace`
  are applied by `InMemoryTraceCollector` only; JSONL collector ignores them.
  Use `compact()` for JSONL file maintenance instead.
- **OpenTelemetry bridge is experimental** — The optional OTel exporter maps
  RunEvents to spans in-memory. No OTLP export, no distributed propagation,
  no running collector service yet.

## Trace Retention Policy

### InMemoryTraceCollector

The in-memory collector supports optional retention limits to prevent unbounded
memory growth in long-running processes:

```python
from agent_app.observability.collector import InMemoryTraceCollector

collector = InMemoryTraceCollector(
    max_traces=1000,           # Max traces to retain (oldest evicted first)
    max_events_per_trace=500,  # Max events per trace (oldest events dropped first)
)
```

**Behavior:**
- `max_traces=None` (default) — unlimited traces
- `max_events_per_trace=None` (default) — unlimited events per trace
- When `max_traces` is exceeded, the trace with the oldest first-event timestamp is removed
- When `max_events_per_trace` is exceeded, the oldest events within that trace are removed (keeps newest N)
- `get_events()` always returns events sorted by timestamp ascending, even after retention

**Configuration:**

```yaml
observability:
  tracing:
    type: memory
    max_traces: 1000
    max_events_per_trace: 500
```

**Note:** `max_traces` / `max_events_per_trace` only affect `InMemoryTraceCollector`.
JSONL collector ignores these settings — use `compact()` for JSONL maintenance.

## JSONL Trace Maintenance

`JSONLTraceCollector` provides utility methods for file maintenance:

```python
collector = JSONLTraceCollector(".agent_app/traces.jsonl")

# Count events and traces
total = await collector.count_events()      # Total event lines
traces = await collector.count_traces()     # Distinct trace_ids

# Compact: keep only the most recent N events per trace
await collector.compact(max_events_per_trace=100)

# Compact to a separate file (no in-place modification)
await collector.compact(output_path=".agent_app/traces_compact.jsonl", max_events_per_trace=100)

# Atomic compact (in-place): writes to temp file then replaces original
await collector.compact(max_events_per_trace=100)
```

**Behavior:**
- `compact()` with no `output_path` → atomic in-place replacement
- `compact()` with `output_path` → writes to specified path, original untouched
- Invalid JSON lines are skipped silently
- Events are sorted by timestamp before trimming; newest N are kept

## OpenTelemetry Bridge (Experimental)

An optional bridge maps `RunEvent` instances to OpenTelemetry spans:

```python
from agent_app.observability.otel import OpenTelemetryTraceExporter

exporter = OpenTelemetryTraceExporter(service_name="my-agent-app")
await exporter.export_events(trace_events)
spans = exporter.get_spans()  # In-memory spans for testing
```

**Installation:**

```bash
pip install 'agent-app-framework[otel]'
```

**Current limitations:**
- No OTLP export — spans are stored in an in-memory exporter only
- No distributed trace propagation
- No running collector service
- Each `RunEvent` becomes a span named after `event_type`
- Error events are recorded as span exceptions

**Why experimental:** The OpenTelemetry integration is a minimal first step.
Future phases may add OTLP export, context propagation, and configurable
exporters. The bridge is stable enough for local debugging but not yet
suitable for production observability pipelines.

## Tracing Benchmark

A lightweight benchmark script measures trace recording overhead:

```bash
# Default: 100 runs with memory collector
python scripts/benchmark_tracing.py --runs 100

# No-op collector (zero overhead baseline)
python scripts/benchmark_tracing.py --runs 1000 --collector noop

# JSONL collector
python scripts/benchmark_tracing.py --runs 1000 --collector jsonl --path .agent_app/bench_traces.jsonl
```

**Output:**

```
collector: memory
runs: 1000
total_ms: 1234.5
avg_ms_per_run: 1.23
events_recorded: 2000
```

**Notes:**
- Uses `DryRunBackend` — no real OpenAI API calls
- Measures Tier 1 event recording (AppRunner local buffer + collector)
- This is a rough benchmark, not a rigorous performance test
- Results vary by hardware, event loop load, and collector type

## Why No Dashboard?

The framework intentionally avoids a web-based trace dashboard. Reasons:
1. **Unix philosophy** — traces are consumable via CLI (`agentapp trace`) and
   HTTP API (`GET /traces`) — existing tools (curl, jq, browsers) already work
2. **Surface area** — a dashboard adds frontend dependencies, auth, real-time
   subscriptions, and deployment complexity
3. **Composability** — users can build their own dashboards on top of the
   JSONL files or the FastAPI API if needed (Grafana, custom web UI, etc.)

## Why No Drain/Flush API?

Adding a `drain()` or `flush()` method to `TraceCollector` would require:
1. Tracking pending `asyncio.create_task()` references in `AppRunner`
2. Awaiting those tasks on demand — adding complexity to the hot path
3. Deciding timeout behavior — what if a task never completes?

The current design accepts eventual consistency for Tier 2 events. The
correct pattern for tests is to query the collector directly after the async
call chain completes (the event loop processes pending tasks naturally).

## Phase 13 Suggestions

- **DAG workflows** — directed acyclic graph execution for complex multi-agent pipelines
- **Parallel orchestrator** — concurrent specialist agent execution
- **OpenTelemetry OTLP export** — connect the bridge to a real collector
- **Trace retention for JSONL** — automatic compaction based on file size or age
- **Session memory** — persistent conversation memory with summarization
- **Plugin system** — extensible backend and store registration
