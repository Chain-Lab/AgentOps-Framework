# Changelog

All notable changes to Agent App Framework are documented here.

## 0.10.0

### Added

- **DAG parallel execution** — `DagExecutionMode` enum (sequential/parallel) with asyncio-based ready-queue scheduler
- **Concurrency control** — `max_concurrency` field with `asyncio.Semaphore` for bounded parallelism
- **Node-level retry policy** — `RetryPolicy` model with `max_attempts`, `backoff_seconds`, `backoff_multiplier`, `retry_on_statuses`
- **Workflow-level retry default** — `DagWorkflow.retry` field; node-level retry takes priority
- **Node execution attempts** — `NodeExecutionAttempt` model; `NodeExecutionResult.attempts` records all tries
- **Exponential backoff** — configurable backoff with multiplier between retry attempts
- **Status propagation** — failed/interrupted nodes stop scheduling; downstream marked skipped; overall status preserved
- **DAG trace events** — per-node started/completed/failed/interrupted/skipped events; retry_scheduled/retry_started/retry_exhausted events
- **Invalid mode validation** — Pydantic enum validation for `execution_mode`; `max_concurrency` must be >= 1
- **Parallel DAG example** — `refund_parallel_dag` workflow in customer_support example
- **Parallel DAG eval suite** — `customer_support_parallel_dag.yaml` eval file
- **DAG benchmark** — `benchmarks/bench_dag.py` comparing sequential vs parallel vs concurrency-limited modes
- **FUNCTION node type** — `NodeType.FUNCTION` for executing Python functions from the DAG
- **Function registry** — `FunctionRegistry` with `@workflow_function` decorator for registering callable functions
- **Input mapping** — `_resolve_function_inputs()` supporting `input.*`, `nodes.*.output.*`, `context.*` patterns
- **Nested path resolution** — `_resolve_path()` for deep nested access (e.g., `nodes.a.output.data.amount`)
- **FUNCTION node permission enforcement** — permission checks against `execution_context["permissions"]` + node-level `permissions` field
- **FUNCTION_PERMISSION_DENIED event** — trace event emitted on permission denial
- **Subworkflow node type** — `NodeType.SUBWORKFLOW` for executing child DAG workflows
- **Subworkflow registry lookup** — `workflow_registry.get()` with KeyError handling
- **Subworkflow cycle detection** — `_subworkflow_chain` tracking prevents A→A and A→B→A references
- **Subworkflow input mapping** — reuses `_resolve_function_inputs()` for parent→child data flow
- **Subworkflow output wrapping** — `{"workflow": name, "status": "completed", "output": sub_output, "node_outputs": {...}}`
- **Subworkflow permission inheritance** — child inherits parent's `execution_context["permissions"]`
- **Subworkflow trace events** — `SUBWORKFLOW_STARTED`, `SUBWORKFLOW_COMPLETED`, `SUBWORKFLOW_FAILED`
- **Extended condition DSL** — `IN`, `NOT IN`, `STARTS_WITH`, `ENDS_WITH`, `NOT STARTS_WITH`, `NOT ENDS_WITH` operators
- **IF_ELSE branch node** — `NodeType.IF_ELSE` for conditional branching with `then`/`else_branch` node lists
- **SWITCH branch node** — `NodeType.SWITCH` for multi-way branching with `cases`/`default` routing
- **`IfElseResult` model** — structured output with condition_result, then_status, else_status, then_node_ids, else_node_ids
- **`SwitchResult` model** — structured output with matched_value, matched_case_index, executed_node_ids
- **`resolve_expression_value()`** — evaluates expressions to raw values for switch case matching
- **customer_support branch examples** — `refund_if_else_dag`, `refund_switch_dag` workflows
- **customer_support branch eval suite** — `customer_support_branch.yaml` eval file
- **Workflow-level deadline** — `deadline_seconds` field on `DagWorkflow` for total execution time limit
- **`WorkflowDeadlineExceededError`** — raised when deadline is exceeded; distinguishable from node timeout
- **`_DeadlineState` helper** — tracks absolute deadline, remaining time, effective timeout computation
- **Deadline-aware retry** — `min(node_timeout, remaining_deadline)` as effective timeout; backoff capped to remaining time
- **Parallel deadline enforcement** — `asyncio.wait` with deadline timeout; best-effort cancellation of running tasks
- **Sequential deadline enforcement** — checks deadline before scheduling each node; marks remaining as SKIPPED
- **Subworkflow deadline inheritance** — `min(parent_remaining, child_configured)` for child deadline
- **IF_ELSE/SWITCH deadline inheritance** — branches share parent's absolute deadline
- **`WORKFLOW_DEADLINE_EXCEEDED` event** — recorded when deadline is exceeded with full metadata
- **`NODE_CANCELLED_BY_DEADLINE` event** — recorded when a node is cancelled due to deadline
- **customer_support deadline example** — `refund_deadline_dag` workflow with 5s deadline
- **Compensation handlers** — `DagNode.compensate` and `DagWorkflow.compensation` for best-effort rollback
- **`CompensationStatus`** — NOT_STARTED, RUNNING, COMPLETED, PARTIAL, FAILED, SKIPPED
- **`NodeCompensationResult`** — per-node compensation outcome with status, attempts, error
- **`WorkflowCompensationResult`** — overall compensation outcome with compensated/skipped/failed lists
- **`CompensationError`** — DagError subclass for compensation failures
- **`_execute_compensation()`** — orchestrates candidate selection and handler execution in reverse completion order
- **`_get_compensation_candidates()`** — selects COMPLETED nodes with compensate config, ordered reverse-completion
- **`_resolve_compensation_inputs()`** — resolves compensation input mappings (reuses `_resolve_path`)
- **`_should_trigger_compensation()`** — gating logic based on workflow status and policy
- **7 compensation event types** — WORKFLOW_COMPENSATION_STARTED/COMPLETED/FAILED, NODE_COMPENSATION_STARTED/COMPLETED/FAILED/SKIPPED
- **`execute()` 4-tuple return** — `(results, status, output, compensation_result)` with None when not triggered
- **customer_support compensation example** — `refund_compensation_dag` with `order.revert_extraction` and `refund.revert_calculation` handlers
- **customer_support compensation eval** — `customer_support_compensation.yaml` with 3 regression cases
- **Compensation benchmark** — baseline, configured-not-triggered, and triggered scenarios in `benchmarks/bench_dag.py`
- **30 compensation tests** — config loading, sequential, parallel, deadline, timeout/retry, branch, and event tests

### Changed

- `DagWorkflow` — new fields: `execution_mode`, `max_concurrency`, `retry`, `timeout_seconds`, `deadline_seconds`, `compensation`
- `DagNode` — new fields: `retry`, `condition`, `timeout_seconds`, `permissions`, `subworkflow_name`, `then`, `else_branch`, `switch_expr`, `cases`, `compensate`
- `NodeExecutionResult` — new field: `attempts` (list of `NodeExecutionAttempt`)
- `NodeType` — new values: `FUNCTION`, `SUBWORKFLOW`, `IF_ELSE`, `SWITCH`
- `DagExecutor` — condition checking; timeout wrapping; unified event recording; function/subworkflow/if_else/switch execution; compensation orchestration
- `DagExecutor` — `_subworkflow_chain` parameter for cycle detection; `_result:<id>` in execution_context for condition evaluators
- `DagExecutor.execute()` — now returns 4-tuple `(results, status, output, compensation_result)`; backward-compatible with `_` discard
- `Workflow.dag()` — accepts `execution_mode`, `max_concurrency`, `retry`, `timeout_seconds`, `deadline_seconds`, `compensation`; validates compensation policy
- `condition.py` — extended tokenizer with IN/STARTS_WITH/ENDS_WITH/comma support; added `InExpression` AST node; added `resolve_expression_value()`
- `RunEventType` — new values: `FUNCTION_PERMISSION_DENIED`, `SUBWORKFLOW_STARTED`, `SUBWORKFLOW_COMPLETED`, `SUBWORKFLOW_FAILED`, `WORKFLOW_DEADLINE_EXCEEDED`, `NODE_CANCELLED_BY_DEADLINE`, WORKFLOW_COMPENSATION_STARTED/COMPLETED/FAILED, NODE_COMPENSATION_STARTED/COMPLETED/FAILED/SKIPPED
- `DagWorkflow` — new field: `deadline_seconds` (workflow-level execution deadline)
- `Workflow.dag()` — accepts `deadline_seconds`; validates > 0
- **245 total DAG tests passing** — 215 Phase 13.1–13.8 + 30 Phase 13.9 compensation tests

## 0.10.0 (Phase 14.0: Persisted DAG Execution State)

### Added

- **WorkflowRunState** — Pydantic model for persisted DAG workflow execution state (run_id, status, input, output, error, timestamps, metadata)
- **NodeExecutionState** — Pydantic model for persisted node execution state (run_id, node_id, node_type, status, input, output, error, attempts, timestamps)
- **WorkflowEventState** — Pydantic model for persisted workflow/node events (event_id, run_id, node_id, event_type, payload, created_at)
- **CompensationExecutionState** — Pydantic model for persisted compensation handler execution (run_id, node_id, handler_name, status, error, timestamps)
- **WorkflowStateStore protocol** — async interface for CRUD operations on workflow runs, nodes, events, and compensations
- **InMemoryWorkflowStateStore** — in-memory implementation for development/testing
- **SQLiteWorkflowStateStore** — SQLite-backed implementation using stdlib `sqlite3`; auto-creates tables and directories; survives process restarts
- **create_workflow_state_store()** — factory function for store instantiation
- **RecoveryPlan model** — resumability assessment (completed_nodes, interrupted_nodes, failed_nodes, compensation_started, reason)
- **build_recovery_plan()** — shared recovery plan builder used by both store implementations
- **DagExecutor state_store integration** — optional `state_store` and `run_id` parameters; persists node states and events during execution
- **WorkflowExecutor state_store forwarding** — `dag_state_store` parameter threaded through to DagExecutor
- **AgentApp/AppRunner state_store plumbing** — `_dag_state_store` attribute threaded from config → AgentApp → AppRunner → WorkflowExecutor
- **Config support** — `runtime.workflow_state.type` (memory/sqlite) and `runtime.workflow_state.path` in YAML config; normalized alongside existing session/run_state config
- **53 new Phase 14.0 tests** — store CRUD, SQLite cross-instance, recovery plan, config, DAG executor integration

### Changed

- `DagExecutor.__init__()` — new optional `state_store` and `run_id` parameters (backward compatible; no state persisted when not provided)
- `DagExecutor.execute()` — creates workflow run record and persists final status when state_store is configured
- `DagExecutor._persist_node_state()` — helper for node state persistence; records status, output, error, attempts
- `DagExecutor._persist_event()` — helper for event persistence
- `RuntimeConfig` — new fields: `workflow_state_type`, `workflow_state_path`; `_normalize_workflow_state` validator for nested config
- `config/loader.py` — wires workflow_state store creation and passes to AgentApp
- `agent_app/core/app.py` — `_dag_state_store` attribute and threading through `_ensure_runner()` and `_run_workflow()`
- `agent_app/runtime/app_runner.py` — `dag_state_store` parameter in `__init__`
- `agent_app/runtime/workflow_executor.py` — `dag_state_store` parameter in `__init__`; passed to DagExecutor in `_run_dag()`

### Current Limitations

- RecoveryPlan is inspect/planning only — no automatic resumption of interrupted nodes
- Running nodes without `completed_at` are identified as interrupted; no automatic restart
- No distributed locking or worker lease mechanism
- No exactly-once execution guarantee
- No Temporal/Celery backend
- Subworkflow independent compensation remains a future phase
- SQLite store uses stdlib `sqlite3` — no connection pooling or WAL mode
- State store is DAG-specific; does not cover SINGLE/HANDOFF/ORCHESTRATOR workflow types

## 0.10.0 (Phase 14.1: DAG Resume Semantics)

### Added

- **ResumePolicy** — Pydantic model controlling resume behavior (retry_failed, retry_interrupted, skip_completed, allow_after_compensation_started)
- **NodeResumeDecision** — per-node resume decision (action: skip/retry/run/blocked with reason)
- **ResumePlan** — structured resume plan with per-node decisions, completed/retry/blocked/skipped lists, resumable flag, reason
- **ResumeResult** — model for resume operation outcome (status, resumed, skipped/retried nodes, final_output, error)
- **WorkflowStateStore resume methods** — `build_resume_plan(run_id, policy)` and `get_node_outputs(run_id)` added to protocol and both store implementations
- **`_build_resume_plan()`** — shared policy-driven decision builder; handles completed/skipped (skip), interrupted (retry), failed (retry/blocked), pending (run), compensation started (blocked)
- **`DagExecutor.resume()`** — ~200 line method that loads persisted state, builds resume plan, injects persisted outputs, executes retry/run nodes in topological order, persists resumed states, records resume events, optionally triggers compensation
- **`WorkflowExecutor.resume_workflow_run()`** — reconstructs DagWorkflow from config, creates DagExecutor with state_store/run_id, delegates to `DagExecutor.resume()`
- **`AppRunner.resume_workflow_run()`** — looks up DAG workflow by name, delegates to WorkflowExecutor
- **`AgentApp.resume_workflow_run()`** — public API: `app.resume_workflow_run(workflow, run_id, ...)`
- **WorkflowExecutor.app_runner plumbing** — `app_runner` parameter added to `WorkflowExecutor.__init__()` for DAG agent node execution during resume
- **`list_runs()`** — added to both InMemoryWorkflowStateStore and SQLiteWorkflowStateStore
- **82 new Phase 14.1 tests** — resume plan building (completed/interrupted/failed/compensation/unknown), DagExecutor.resume() (state_store required, unknown run_id, skip completed, retry interrupted, retry_failed policy, blocked downstream, compensation block, skipped nodes, event persistence, parallel DAG), WorkflowExecutor/AgentApp API (no state_store, unknown workflow, end-to-end)

### Changed

- `InMemoryWorkflowStateStore` — added `list_runs()` method
- `SQLiteWorkflowStateStore` — added `list_runs()` method; fixed `NodeRunStatus.INTERRUPTED` → `NodeRunStatus.RUNNING` reference
- `_build_resume_plan()` — run is resumable unless compensation started; blocked nodes (policy-driven) don't prevent resume (handled downstream); PENDING nodes → "run"; COMPENSATING/COMPENSATED → "skip"
- `DagExecutor.resume()` — blocked nodes recorded as FAILED status with downstream skipping; status propagated to overall_status
- `AppRunner.__init__()` — creates WorkflowExecutor with `app_runner=self` for DAG execution support

### Current Limitations

- Resume is explicit (user calls `app.resume_workflow_run()`); no automatic resume on app restart
- `allow_after_compensation_started` is accepted but not implemented (default False blocks resume)
- Parallel compensation order based on completion timestamp (may vary between runs)
- Deadline cancellation is best-effort — external side effects may have already occurred
- Subworkflow compensation delegates to parent (no independent subworkflow compensation yet)
- No distributed execution, Temporal/Celery backend, or visual DAG editor

## 0.10.0 (Phase 15: Distributed Execution Readiness)

### Added

- **WorkerIdentity** — Pydantic model identifying a worker (worker_id, hostname, process_id, app_version, metadata); auto-generated default worker_id
- **WorkflowRunLease** — Pydantic model for workflow run lease (run_id, owner_id, acquired_at, expires_at, renewed_at, released_at, version); requires timezone-aware UTC datetimes
- **LeaseStatus** — enum: ACQUIRED, DENIED, EXPIRED, RELEASED
- **LeasePolicy** — Pydantic model (ttl_seconds=300, allow_steal_expired=True, renew_before_seconds=60)
- **LeaseAcquireResult** — Pydantic model (acquired, run_id, owner_id, lease, reason, current_owner_id, expires_at)
- **IdempotencyRecord** — Pydantic model for idempotency key tracking (key, run_id, operation, created_at, result_ref)
- **WorkflowStateStore lease methods** — `acquire_run_lease()`, `renew_run_lease()`, `release_run_lease()`, `get_run_lease()`, `list_expired_leases()` added to protocol and both store implementations
- **WorkflowStateStore idempotency methods** — `put_idempotency_record()`, `get_idempotency_record()` added to protocol and both store implementations
- **InMemory lease management** — full lease lifecycle (acquire, deny, renew, release, steal expired, list expired)
- **SQLite lease persistence** — `workflow_run_leases` table with auto-create; cross-instance visibility; transaction-based operations
- **SQLite idempotency persistence** — `workflow_idempotency` table with upsert semantics
- **DagExecutor lease integration** — `_acquire_lease()` before execute/resume; `_release_lease()` in finally block; `_get_worker()` with caching
- **DagExecutor.execute()** — wraps execution in try/acquire/finally/release; raises DagError if lease denied
- **DagExecutor.resume()** — acquires lease after building resume plan; releases in finally block
- **Worker plumbing** — `worker` parameter threaded through AgentApp → AppRunner → WorkflowExecutor → DagExecutor
- **Lease lifecycle events** — `workflow.lease_acquired`, `workflow.lease_denied`, `workflow.lease_renewed`, `workflow.lease_released` persisted to state store
- **41 new Phase 15 tests** — lease models (5), InMemory lease (10), SQLite lease (8), idempotency (4), DagExecutor lease integration (7)

### Changed

- `DagExecutor.__init__()` — new optional `worker` parameter
- `DagExecutor` — cached worker identity (`_cached_worker`) ensures acquire/release use same worker_id
- `WorkflowExecutor.run_workflow()` — new optional `worker` parameter; passed to `_run_dag()`
- `WorkflowExecutor._run_dag()` — new optional `worker` parameter; passed to DagExecutor
- `WorkflowExecutor.resume_workflow_run()` — new optional `worker` parameter; passed to DagExecutor
- `AppRunner.resume_workflow_run()` — new optional `worker` parameter
- `AgentApp.run()` — new optional `worker` parameter; forwarded to WorkflowExecutor
- `AgentApp._run_workflow()` — new optional `worker` parameter; forwarded to WorkflowExecutor
- `AgentApp.resume_workflow_run()` — new optional `worker` parameter; forwarded to AppRunner

### Current Limitations

- Lease is best-effort coordination — does not provide exactly-once guarantee
- No Celery / Temporal / distributed worker backend
- No automatic recovery daemon
- No node-level distributed scheduling
- No cross-process streaming fanout
- SQLite store uses stdlib sqlite3 — no connection pooling or WAL mode
- Lease TTL is in-memory checked; no background renewal daemon
- Idempotency records stored but not enforced at API level (Phase 15.1+)

## 0.10.0 (Phase 15.1: API-level Idempotency Enforcement)

### Added

- **Request fingerprinting** — SHA-256 of deterministic JSON (sorted keys, no whitespace, `default=str`) for stable request identification
- **Transient field exclusion** — `idempotency_key`, `worker`, `trace_id`, `request_id`, `correlation_id` excluded from fingerprint computation
- **Scope isolation** — `compute_scope(tenant_id, operation)` produces `"{tenant_id}:{operation}"` namespace preventing cross-tenant key collisions
- **Payload builders** — `build_execute_payload()` and `build_resume_payload()` for stable, minimal fingerprint input
- **IdempotencyRecord extended** — new fields: `scope` (scoped namespace) and `request_fingerprint` (SHA-256 hex digest)
- **`DuplicateIdempotencyKeyError`** — raised when same key is reused with identical fingerprint (true duplicate)
- **`IdempotencyKeyMismatchError`** — raised when same key is reused with different fingerprint (replay attack / client error)
- **Atomic `reserve_idempotency_key()`** — single enforcement point; delegates to store's atomic reservation
- **InMemory atomic reservation** — composite key `"{scope}:{key}"` with atomic check-and-set
- **SQLite atomic reservation** — `PRIMARY KEY (scope, key)` with explicit `BEGIN`/`COMMIT`/`ROLLBACK` transaction; `IntegrityError` determines conflict type
- **SQLite schema migration** — `_add_idempotency_columns()` migrates old tables (no scope column) to new composite-key schema
- **DagExecutor `_enforce_idempotency()`** — called before lease acquire in both `execute()` and `resume()`; builds payload, computes fingerprint, creates record, calls store reservation
- **Worker identity caching** — `_cached_worker` and `_current_input` ensure consistent fingerprinting across enforcement calls
- **AgentApp → AppRunner → WorkflowExecutor → DagExecutor plumbing** — `idempotency_key` parameter threaded through entire call chain for both execute and resume
- **FastAPI `Idempotency-Key` header support** — header takes priority over JSON body `idempotency_key` field
- **HTTP 409 mapping** — `DuplicateIdempotencyKeyError` and `IdempotencyKeyMismatchError` mapped to HTTP 409 Conflict via `_extract_idempotency_error()` helper
- **34 new Phase 15.1 tests** — fingerprint (5), scope (3), errors (2), InMemory (6), SQLite (6), DagExecutor (6), cross-instance (2), backward compatibility (2)

### Changed

- `IdempotencyRecord` — new optional fields: `scope`, `request_fingerprint`
- `WorkflowStateStore` protocol — new method: `reserve_idempotency_key(record)` with atomic semantics
- `InMemoryWorkflowStateStore` — composite key for scope isolation; atomic reservation
- `SQLiteWorkflowStateStore` — composite PRIMARY KEY (scope, key); transaction-based atomic reservation; schema migration
- `DagExecutor.__init__()` — new optional `idempotency_key` parameter; `_current_input` attribute for fingerprinting
- `DagExecutor` — `_enforce_idempotency()` called before lease acquire
- `WorkflowExecutor.run_workflow()` — new optional `idempotency_key` parameter
- `WorkflowExecutor.resume_workflow_run()` — new optional `idempotency_key` parameter
- `AppRunner.run()` — new optional `idempotency_key` parameter
- `AppRunner.resume_workflow_run()` — new optional `idempotency_key` parameter
- `AgentApp.run()` — new optional `idempotency_key` parameter
- `AgentApp.resume_workflow_run()` — new optional `idempotency_key` parameter
- `RunRequest` — new optional `idempotency_key` field (body-level, header takes priority)
- FastAPI `/runs` and `/runs/{run_id}/resume` — idempotency key extraction and HTTP 409 error mapping

### Current Limitations

- Best-effort API-level duplicate prevention only — NOT exactly-once execution
- Without `idempotency_key`: old behavior unchanged (no enforcement)
- With `idempotency_key`: single-use enforcement before side-effect-producing operations
- No background lease renewal daemon
- No distributed worker backend (Celery/Temporal not implemented)
- Scope defaults to `{tenant_id}:{operation}`; cannot be customized per-request
- Fingerprint is best-effort; semantically identical payloads with different serialization will produce different fingerprints

## 0.10.0 (Phase 15.2: Background Lease Renewal / Heartbeat)

### Added

- **`LeaseRenewer`** — asyncio background task that periodically calls `renew_run_lease` on the state store; best-effort in-process renewal (NOT distributed, NOT Celery/Temporal, NOT exactly-once)
- **`LeaseLostError`** — stable error type with `to_dict()` method; raised when renewal fails during execution
- **`renew_run_lease`** — added to `WorkflowStateStore` protocol and both InMemory/SQLite implementations; validates owner, release status, and expiration
- **Lease expiration check** — `renew_run_lease` rejects expired leases (now >= expires_at)
- **`LeaseRenewalConfig`** — Pydantic model (`renew_enabled=True`, `renew_interval_seconds=None`, `ttl_seconds=300`); added to `RuntimeConfig`
- **Config normalization** — `_normalize_lease_renewal` validator supports flat and nested YAML formats
- **`DagExecutor` lease renewal integration** — `_make_renewer()` creates `LeaseRenewer`; `execute()` and `resume()` start/stop renewer with deferred `LeaseLostError` pattern
- **Idempotency ordering preserved** — idempotency enforcement → lease acquire → renewer start → execute → renewer stop → lease release → raise `LeaseLostError` if needed
- **Config plumbing** — `lease_renewal_config` threaded through AgentApp → AppRunner → WorkflowExecutor → DagExecutor
- **28 new Phase 15.2 tests** — LeaseRenewer (6), InMemory lease renewal (6), SQLite lease renewal (5), DagExecutor integration (5), config (5)

### Changed

- `LeaseLostError` — canonical definition in `dag_run_state.py`; re-exported from `lease_renewer.py`
- `renew_run_lease` — now checks lease expiration; expired leases cannot be renewed
- `DagExecutor.__init__()` — new optional `lease_renewal_config` parameter
- `DagExecutor.execute()` — integrates `LeaseRenewer` with start/stop lifecycle and deferred error pattern
- `DagExecutor.resume()` — same lease renewal integration for resume path
- `WorkflowExecutor.__init__()` — new optional `lease_renewal_config` parameter
- `AppRunner.__init__()` — new optional `lease_renewal_config` parameter
- `AgentApp.__init__()` — new optional `lease_renewal_config` parameter
- `config/loader.py` — passes `lease_renewal_config` from RuntimeConfig to AgentApp

### Current Limitations

- Best-effort in-process renewal only — does NOT provide exactly-once guarantee
- Only works while the current process is alive — no distributed worker daemon
- No Celery / Temporal / distributed worker backend
- Renewal failure → `lease_lost=True` → stable error (workflow must be manually resumed)
- Default interval = `ttl_seconds / 3`; configurable via `renew_interval_seconds`

## 0.9.0

### Added

- **Structured RunEvent model** — `RunEventType` enum (22 event types) + `RunEvent` Pydantic model with timezone-aware timestamps
- **TraceCollector protocol** — `record()`, `get_events()`, `list_traces()` interface
- **NoOpTraceCollector** — zero-cost no-op for disabled tracing
- **InMemoryTraceCollector** — in-process event storage with tenant/run filtering; supports optional `max_traces` and `max_events_per_trace` retention limits
- **JSONLTraceCollector** — append-only JSONL file storage for local debugging; supports `count_events()`, `count_traces()`, `compact()` maintenance utilities
- **AppRunner instrumentation** — emits run.started, run.completed, run.failed, run.interrupted, run_state.saved events
- **ToolExecutor instrumentation** — emits tool.started, tool.completed, tool.failed, tool.permission_denied, tool.approval_required, approval.created events
- **WorkflowExecutor instrumentation** — emits workflow.started, workflow.completed, workflow.failed, routing.decision, handoff.occurred, agent.started, agent.completed events
- **AgentApp approve/reject/resume instrumentation** — emits approval.approved, approval.rejected, run_state.resumed events
- **OpenAIAgentsBackend instrumentation** — emits agent.started, agent.completed, agent.failed events
- **AppRunResult.trace_events** — structured events attached to every run result
- **RunContext.trace_id** — observability trace identifier propagated through execution
- **Observability config** — `observability.tracing.type` (noop/memory/jsonl), `max_traces`, `max_events_per_trace` in YAML config
- **Config loader integration** — `build_app()` creates trace collector with retention settings and passes to all components
- **FastAPI trace endpoints** — `GET /traces` (with run_id/tenant_id/event_type/limit filtering) and `GET /traces/{trace_id}` (404 on missing)
- **FastAPI `TraceSummary` model** — structured trace list response
- **CLI trace commands** — `agentapp trace list` (table/JSON, filters) and `agentapp trace show` (human-readable/JSON, non-zero exit on missing)
- **Eval `trace_events` assertion** — assert Tier 1 synchronous events in eval YAML
- **Event reliability tiers** — Tier 1 (synchronous, safe for eval) vs Tier 2 (fire-and-forget, collector-level tests)
- **OpenTelemetry bridge stub** — optional `OpenTelemetryTraceExporter` (experimental, install via `pip install agent-app-framework[otel]`)
- **Tracing benchmark script** — `scripts/benchmark_tracing.py` for local overhead measurement
- **75+ new Phase 12 tests** — Steps 1-6, no regressions
- **`docs/observability.md`** — full observability documentation with reliability tiers, CLI/FastAPI examples, limitations
- **README Observability section** — quick start, eval integration, FastAPI endpoints, Tier 1/Tier 2 table

### Changed

- `AppRunResult` — new `trace_events` field (list of RunEvent)
- `RunContext` — new optional `trace_id` field
- `AppRunner.__init__()` — new optional `trace_collector` parameter
- `AgentApp.__init__()` — new optional `trace_collector` parameter
- `TracingConfig` — new optional `max_traces` and `max_events_per_trace` fields (backward compatible)
- `InMemoryTraceCollector.__init__()` — accepts optional `max_traces` and `max_events_per_trace`
- `JSONLTraceCollector` — new `count_events()`, `count_traces()`, `compact()` methods
- `pyproject.toml` — new optional `otel` extra

### Current Limitations

- Tier 2 events (workflow, tool, approval, run_state) are fire-and-forget — not suitable for eval YAML assertions
- No drain/flush API — intentionally deferred
- No OpenTelemetry OTLP export yet — bridge is experimental stub only

## 0.10.0 (Phase 16.0: DAG Persistence Snapshots and Enhanced Resume)

### Added

- **DagRunSnapshot** — Pydantic model capturing DAG execution state (run_id, status, completed/failed/current/pending node IDs, per-node snapshots, execution context, schema_version, timestamps)
- **DagNodeSnapshot** — per-node execution snapshot (node_id, status, attempts, output, error, started_at, completed_at)
- **DagSnapshotStatus** — StrEnum: RUNNING, COMPLETED, FAILED, PARTIAL, INTERRUPTED
- **Snapshot serialization** — `to_json()` / `from_json()` with timezone-aware ISO datetime; schema_version tracking for migration safety
- **Snapshot error types** — `SnapshotWriteError`, `SnapshotCorruptionError`, `SnapshotUnsupportedVersionError` — all with `to_dict()` for stable error responses
- **WorkflowStateStore snapshot methods** — `save_run_snapshot()`, `get_latest_run_snapshot()`, `list_run_snapshots()`, `delete_run_snapshots()` added to protocol and both store implementations
- **InMemory snapshot store** — `_snapshots: dict[str, list[DagRunSnapshot]]` with CRUD, overwrite-by-snapshot_id, run isolation, ordered listing
- **SQLite snapshot persistence** — `dag_run_snapshots` table (snapshot_id PK, run_id, workflow_name, status, schema_version, snapshot_json, timestamps); `idx_dag_run_snapshots_run_updated` index; auto-create on init; survives process restarts
- **DagSnapshotConfig** — Pydantic model (`enabled=True`, `store=memory`, `path=None`, `save_on_node_start/complete/interrupt/failure=True`); configurable per-transition save flags
- **DagExecutor snapshot integration** — `_is_snapshot_enabled()`, `_build_snapshot()`, `_save_snapshot()`, `_maybe_save_snapshot()` helpers
- **execute() snapshot lifecycle** — initial "running" snapshot after lease acquire; node-level snapshots via `_maybe_save_snapshot()` after each node and on failure; final "completed"/"failed" snapshot; snapshot errors are stable (SnapshotWriteError) for initial/final, best-effort (logged warning) for intermediate
- **resume() snapshot acceleration** — reads latest snapshot via `get_latest_run_snapshot()`; validates schema_version (only v1 supported), run_id match, resumability; completed snapshot returns idempotent empty result; corruption/version errors caught and fall through to existing resume logic
- **Config support** — `runtime.dag_snapshot` (nested) or `runtime.dag_snapshot_config` (flat) in YAML; `_normalize_dag_snapshot` validator; wired through config/loader → AgentApp → AppRunner → WorkflowExecutor → DagExecutor
- **62 new Phase 16.0 tests** — DagRunSnapshot model (8), DagNodeSnapshot (3), serialization (4), error types (3), InMemory store (6), SQLite store (7), DagSnapshotConfig (6), RuntimeConfig normalization (3), DagExecutor snapshot integration (8), resume snapshot (5), error handling (2), _is_snapshot_enabled (5), _build_snapshot (2), config plumbing (2)

### Changed

- `RuntimeConfig` — new optional `dag_snapshot_config: DagSnapshotConfig | None` field
- `RuntimeConfig` — `_normalize_dag_snapshot` model_validator for nested YAML config normalization
- `DagExecutor.__init__()` — new optional `snapshot_config` parameter
- `DagExecutor.execute()` — saves initial/completion/failure snapshots; calls `_maybe_save_snapshot()` after node transitions
- `DagExecutor.resume()` — loads latest snapshot for resume acceleration; validates and falls through on error
- `DagExecutor._execute_sequential()` — calls `_maybe_save_snapshot()` after each node completion and on failure/interruption
- `DagExecutor._execute_parallel()` — calls `_maybe_save_snapshot()` after each node batch completion
- `WorkflowStateStore` protocol — 4 new async methods for snapshot CRUD
- `InMemoryWorkflowStateStore` — snapshot CRUD with in-memory storage
- `SQLiteWorkflowStateStore` — snapshot CRUD with SQLite persistence; auto-creates `dag_run_snapshots` table
- `WorkflowExecutor.__init__()` — new optional `dag_snapshot_config` parameter; passed to DagExecutor
- `AppRunner.__init__()` — new optional `dag_snapshot_config` parameter; passed to WorkflowExecutor
- `AgentApp.__init__()` — new optional `dag_snapshot_config` parameter; passed to AppRunner
- `config/loader.py` — passes `dag_snapshot_config` from RuntimeConfig to AgentApp

### Current Limitations

- Snapshots are recovery aids — do NOT guarantee exactly-once execution
- Snapshots are NOT a distributed transaction log (no Celery/Temporal)
- No automatic recovery daemon — resume is explicit via `app.resume_workflow_run()`
- SQLite store uses stdlib sqlite3 — no connection pooling or WAL mode
- Schema version migration is manual (only v1 supported; future versions require code migration)
- Intermediate snapshots are best-effort (failure logged but does not block execution)
- Snapshot persistence adds I/O overhead proportional to snapshot frequency
- No visual dashboard — trace viewing via CLI, API, or JSONL file
- InMemoryTraceCollector is per-process only — use JSONL for persistence
- Benchmark script is rough measurement, not rigorous performance test
- `ToolExecutor.__init__()` — new optional `trace_collector` parameter
- `WorkflowExecutor.__init__()` — new optional `trace_collector` parameter
- `OpenAIAgentsBackend.__init__()` — new optional `trace_collector` parameter

### Known limitations

- No OpenTelemetry integration (planned for future phase)
- FastAPI trace endpoints not yet implemented
- CLI trace commands not yet implemented
- Eval trace_events assertions not yet implemented
- Pydantic json_encoders deprecation warning (cosmetic, no functional impact)
- ToolExecutor / WorkflowExecutor event emission deferred to Step 2
- FastAPI trace endpoints not yet implemented
- CLI trace commands not yet implemented
- Eval trace_events assertions not yet implemented

## 0.10.0 (Phase 16.1: Compensation State Persistence)

### Added

- **CompensationActionState** — Pydantic model tracking per-action compensation execution (action_id, run_id, node_id, compensating_for_node_id, status, attempts, max_attempts, input, output, error, idempotency_key, timestamps); auto-generated action_id via `default_factory`
- **CompensationExecutionState** — Pydantic model for per-run compensation state (compensation_id, run_id, workflow_name, status, schema_version, actions dict, action_order list, timestamps); auto-generated compensation_id; `model_validator` syncs action_order
- **CompensationActionStatus** — StrEnum: PENDING, RUNNING, COMPLETED, FAILED, SKIPPED
- **CompensationRunStatus** — StrEnum: NOT_REQUIRED, PENDING, RUNNING, COMPLETED, PARTIAL_FAILED, FAILED
- **CompensationStateStore protocol** — async interface: `save_compensation_state()`, `get_compensation_state()`, `update_compensation_action()`, `list_compensation_states()`, `delete_compensation_state()`
- **InMemoryCompensationStateStore** — in-memory implementation keyed by run_id; supports CRUD, filtering by workflow_name
- **SQLiteCompensationStateStore** — SQLite-backed implementation with `dag_compensation_states` table (compensation_id PK, run_id UNIQUE, indexes on run_id and workflow_name+status); auto-creates tables; survives process restarts; handles corrupted JSON gracefully
- **`create_compensation_state_store()`** — factory function ("memory" or "sqlite")
- **DagCompensationConfig** — Pydantic config model (enabled=True, store="memory", path=None, max_attempts=1, resume_incomplete=True); store validator rejects unknown types
- **DagExecutor compensation persistence** — `_init_compensation_store()` lazy init; `_is_compensation_persistence_enabled()` check; `_create_compensation_state()` builds state from compensation candidates; `_save_compensation_state()` with SnapshotWriteError on failure; `_update_compensation_action()` best-effort store update; `_get_compensation_state()` retrieval; `_resume_compensation()` resumes from persisted state
- **Resume integration** — `resume()` loads persisted compensation state via `_get_compensation_state()`; skips completed actions, retries failed actions within max_attempts, executes pending actions; updates store after each action
- **Config plumbing** — `dag_compensation_config` normalized from `dag_compensation` YAML key; threaded through config/loader → AgentApp → AppRunner → WorkflowExecutor → DagExecutor
- **Serialization** — `serialize_compensation_state()` / `deserialize_compensation_state()` with timezone-aware ISO datetime; handles corrupted JSON with ValueError
- **97 new Phase 16.1 tests** — CompensationActionState (12), CompensationExecutionState (14), serialization (7), InMemory store (9), SQLite store (14), DagExecutor integration (25), config plumbing (5), resume compensation (3), error handling (2), factory (4)

### Changed

- `RuntimeConfig` — new optional `dag_compensation_config: DagCompensationConfig | None` field; `_normalize_dag_compensation` validator
- `DagExecutor.__init__()` — new optional `compensation_config` parameter; `_compensation_store` attribute
- `DagExecutor.execute()` — calls `_init_compensation_store()` after renewer start; creates/saves compensation state when compensation triggered
- `DagExecutor.resume()` — checks compensation state for incomplete runs; resumes via `_resume_compensation()`
- `DagExecutor._execute_compensation()` — creates compensation state before handler loop; updates action status after each handler; finalizes state on completion
- `config/loader.py` — passes `dag_compensation_config` from RuntimeConfig to AgentApp
- `agent_app/core/app.py` — `_dag_compensation_config` attribute and threading through `_ensure_runner()`
- `agent_app/runtime/app_runner.py` — `dag_compensation_config` parameter in `__init__`
- `agent_app/runtime/workflow_executor.py` — `dag_compensation_config` parameter in `__init__`; passed to DagExecutor

### Current Limitations

- Compensation state is a recovery aid — does NOT guarantee exactly-once execution
- NOT a distributed transaction log (no Celery/Temporal/Redis/etcd)
- No automatic recovery daemon — resume is explicit via `app.resume_workflow_run()`
- External side effect idempotency remains the business tool's responsibility
- SQLite store uses stdlib sqlite3 — no connection pooling or WAL mode
- Compensation state is independent from snapshots and lease state (each has its own persistence layer)
- Does NOT replace lease renewal, snapshot, or business-level idempotency

## 0.10.0 (Phase 16.2: Lease Backend Abstraction)

### Added

- **`WorkflowLeaseBackend` Protocol** — pluggable interface for lease coordination (`acquire_run_lease`, `renew_run_lease`, `release_run_lease`, `get_run_lease`, `list_expired_leases`); reuses existing models (WorkerIdentity, LeasePolicy, WorkflowRunLease, LeaseAcquireResult)
- **`StateStoreLeaseBackend`** — adapter wrapping `WorkflowStateStore` as a `WorkflowLeaseBackend`; preserves full backward compatibility with existing state store lease methods
- **`InMemoryWorkflowLeaseBackend`** — standalone in-memory lease backend; five-path acquire logic (no lease, released, expired-steal, same-owner refresh, different-owner deny); supports renew, release, get, list_expired
- **`SQLiteWorkflowLeaseBackend`** — standalone SQLite lease backend with `workflow_run_leases` table; cross-instance visibility; auto-creates tables and directories; in-memory cache with DB re-sync on `get_run_lease`
- **`create_lease_backend()`** — factory function supporting "state_store", "memory", "sqlite" backend types
- **`LeaseCoordinator`** — thin coordination layer over `WorkflowLeaseBackend`; applies default `LeasePolicy` when none provided; unified entry point for acquire/renew/release/get/list_expired
- **`LeaseRenewer` Phase 16.2 support** — new optional `lease_backend` parameter; takes precedence over `state_store`; backward compatible with legacy `state_store` parameter (auto-wraps via `StateStoreLeaseBackend`)
- **`DagExecutor` lease backend injection** — new optional `lease_backend` and `lease_policy` parameters; `_get_lease_backend()` returns explicit backend > state_store > None; `_acquire_lease()`, `_release_lease()`, `_make_renewer()` all use effective lease backend
- **`WorkflowExecutor` lease backend helpers** — `_build_lease_backend()` creates backend from `DagLeaseConfig`; `_build_lease_policy()` creates `LeasePolicy` from config; passed to `DagExecutor` in both `run_workflow()` and `resume_workflow_run()`
- **`DagLeaseConfig`** — Pydantic config model (backend="state_store", db_path=None, ttl_seconds=300, allow_steal_expired=True, renew_before_seconds=60); backend validator rejects unknown types
- **Config support** — `runtime.dag_lease` (nested) or `runtime.dag_lease_config` (flat) in YAML; `_normalize_dag_lease` validator; threaded through config/loader → AgentApp → AppRunner → WorkflowExecutor → WorkflowExecutor → DagExecutor
- **75 new Phase 16.2 tests** — StateStoreLeaseBackend (7), InMemory lease backend (11), SQLite lease backend (8), factory (7), protocol typing (3), LeaseCoordinator (10), LeaseRenewer (5), DagExecutor (8), config (6)

### Changed

- `RuntimeConfig` — new optional `dag_lease_config: DagLeaseConfig | None` field; `_normalize_dag_lease` model_validator
- `DagExecutor.__init__()` — new optional `lease_backend` and `lease_policy` parameters
- `DagExecutor._acquire_lease()` — uses `_get_lease_backend()` instead of direct state_store access
- `DagExecutor._release_lease()` — uses `_get_lease_backend()` instead of direct state_store access
- `DagExecutor._make_renewer()` — uses effective lease backend; detects standalone vs state_store backend
- `LeaseRenewer.__init__()` — new optional `lease_backend` parameter; backward compatible with `state_store`
- `LeaseRenewer._renew_loop()` — uses `self._lease_backend` for renew calls; keeps `self._state_store` for terminal-state check
- `WorkflowExecutor.__init__()` — new optional `dag_lease_config` parameter; `_build_lease_backend()` and `_build_lease_policy()` helpers
- `WorkflowExecutor.run_workflow()` — passes `lease_backend` and `lease_policy` to DagExecutor
- `WorkflowExecutor.resume_workflow_run()` — passes `lease_backend` and `lease_policy` to DagExecutor
- `AppRunner.__init__()` — new optional `dag_lease_config` parameter; passed to WorkflowExecutor
- `AgentApp.__init__()` — new optional `dag_lease_config` parameter; passed through `_ensure_runner()`
- `config/loader.py` — passes `dag_lease_config` from RuntimeConfig to AgentApp

### Current Limitations

- Lease backend abstraction is a coordination layer — does NOT provide exactly-once guarantee
- NOT a distributed lock service (no Redis/etcd distributed lock)
- No Celery / Temporal / distributed worker daemon
- No automatic recovery daemon — resume is explicit via `app.resume_workflow_run()`
- Default lease backend is state_store-backed (delegates to existing WorkflowStateStore)
- Standalone memory/sqlite backends are single-process (memory) or cross-instance (sqlite) only
- Lease renewal only works while the current process is alive
- External side effect idempotency remains the business tool's responsibility
- SQLite store uses stdlib sqlite3 — no connection pooling or WAL mode
- Lease backend does NOT replace lease renewal, snapshot, compensation, or business-level idempotency

## 0.10.0 (Phase 16.3: Lease Backend Observability & Health Checks)

### Added

- **`LeaseMetrics`** — thread-safe in-process metrics collector using `threading.Lock`; tracks per-operation counters (attempts, successes, failures, exceptions, denied) for acquire/renew/release/get/list_expired; returns immutable snapshots
- **`LeaseOperationMetrics`** — dataclass for per-operation counters (attempts, successes, failures, exceptions, denied)
- **`LeaseMetricsSnapshot`** — immutable dataclass capturing full metrics state at a point in time
- **`MetricsWorkflowLeaseBackend`** — transparent wrapper around any `WorkflowLeaseBackend`; records metrics on every operation without changing return values or behavior; re-raises exceptions after recording
- **`LeaseHealthStatus`** — StrEnum: HEALTHY, DEGRADED, UNHEALTHY
- **`LeaseHealthCheckResult`** — Pydantic model (status, backend_type, details, checked_at, error); timezone-aware UTC timestamps
- **`LeaseBackendHealthChecker`** — non-destructive health checker; backend-specific checks (memory: always ok; sqlite: lightweight query with active lease count; state_store: delegation test; metrics: inner backend check; generic: non-destructive get_run_lease probe); never raises — exceptions captured in result
- **`LeaseDiagnostics`** — Pydantic model for operator visibility (backend_type, health, metrics, sample_expired_leases, checked_at)
- **`LeaseCoordinator` observability** — optional `metrics` parameter wraps backend with `MetricsWorkflowLeaseBackend`; `metrics_snapshot()` returns snapshot or None; `health_check()` delegates to `LeaseBackendHealthChecker`; `diagnostics()` assembles health + metrics + expired lease sample
- **`DagLeaseMetricsConfig`** — Pydantic config model (`enabled=False`; metrics are opt-in to avoid overhead when not needed)
- **`DagLeaseHealthConfig`** — Pydantic config model (`enabled=True`; health checks enabled by default as they are lightweight)
- **`DagLeaseConfig` extended** — new optional `metrics` and `health` fields
- **`WorkflowExecutor` lease observability** — `_build_lease_metrics()` creates collector when metrics enabled; `get_lease_health_checker()` creates checker; `get_lease_diagnostics()` assembles full diagnostic snapshot
- **Config support** — `runtime.dag_lease.metrics.enabled` and `runtime.dag_lease.health.enabled` in YAML config
- **66 new Phase 16.3 tests** — LeaseMetrics (14), MetricsWorkflowLeaseBackend (10), LeaseBackendHealthChecker (7), LeaseCoordinator metrics/health/diagnostics (12), DagLeaseMetricsConfig (5), DagLeaseHealthConfig (5), config plumbing (8), full integration (5)

### Changed

- `LeaseCoordinator.__init__()` — new optional `metrics` parameter; auto-wraps backend with `MetricsWorkflowLeaseBackend` when provided
- `LeaseCoordinator` — new methods: `metrics_snapshot()`, `health_check()`, `diagnostics(include_expired_sample, expired_sample_limit)`
- `RuntimeConfig` — `DagLeaseConfig` extended with `metrics: DagLeaseMetricsConfig | None` and `health: DagLeaseHealthConfig | None`
- `LeaseBackendHealthChecker.check()` — propagates inner check errors to top-level `error` field when status is UNHEALTHY
- `WorkflowExecutor.__init__()` — new optional `dag_lease_config` parameter; `_build_lease_metrics()`, `get_lease_health_checker()`, `get_lease_diagnostics()` helpers

### Current Limitations

- Metrics are in-process only — not exported to Prometheus/OpenTelemetry (no external dependency)
- Health checks are diagnostic only — do NOT guarantee backend availability or provide distributed recovery
- NOT a distributed health protocol or liveness probe
- Metrics are opt-in (`enabled=False` by default) to avoid overhead when not needed
- No background metrics export or collection daemon
- LeaseMetrics uses `threading.Lock` — not async-safe for cross-thread mutation
- Health checks are non-destructive but do not test lease acquire/renew operations
- Does NOT replace lease renewal, snapshot, compensation, or business-level idempotency

### Added

- **OpenAI backend handoff workflow support** — `OpenAIAgentsBackend.run_workflow()` handles handoff (triage) workflows via SDK `Agent.handoffs`
- **OpenAI backend orchestrator workflow support** — `Agent.as_tool()` for agents-as-tools with fallback wrapper
- **`compile_agent(handoffs=...)`** — explicit handoffs parameter takes priority over `agent_spec.handoffs`
- **`compile_agent_as_tool()`** — compiles specialist agents as SDK tools for orchestrator workflows
- **`AgentApp._run_workflow()` backend delegation** — OpenAIAgentsBackend multi-agent execution; DryRun path unchanged
- **WorkflowTrace for OpenAI workflows** — records handoff_candidates and agent_tools steps
- **23 new Phase 11 tests** — handoff/orchestrator compile, run, dispatch, integration, DryRun regression
- **394 total tests passing**

### Known limitations

- Handoff target extraction — actual handoff target not extracted from SDK result; trace records candidates only
- Orchestrator agent_calls — extracted from tool_calls when available; may be incomplete
- Agent-as-tool governance — specialist agents-as-tools do not go through ToolExecutor governance
- DAG workflows — not yet implemented
- Parallel orchestrator — specialists called serially

## 0.7.0

### Added

- **OpenAI native HITL mode** — uses SDK `needs_approval` and `RunState` for real pause/resume
- **`RunState.to_json()` / `from_json()`** — SDK-native RunState serialization into framework RunStateStore
- **`RunState.approve()` / `reject()`** — native SDK approval resolution integrated with framework resume
- **`OpenAIAgentsBackend.resume()`** — real OpenAI RunState resume using stored `backend_state`
- **`InterruptedRun.backend_state`** — stores serialized SDK RunState for native resume
- **ApprovalRequest mapping** — SDK `ToolApprovalItem` mapped to framework approval dicts
- **`hitl_mode` config** — `wrapper` (default) or `native` in `runtime.openai.hitl_mode`
- **24 new Phase 10 tests** — native HITL, RunState serialization, resume, streaming, integration
- **371 total tests passing**

### Changed

- `AppRunResult.backend_state` — new field for backend-specific state (e.g. OpenAI RunState JSON)
- `AgentBackend` protocol — added optional `resume()` method
- `DryRunBackend` — implements `resume()` stub
- `AppRunner._save_interrupted_run()` — saves `backend_state` to `InterruptedRun`
- `AgentApp.resume()` — dispatches to `backend.resume()` for OpenAI native mode

### Known limitations

- Native HITL requires SDK version with `needs_approval` / `RunState` support
- Streaming resume is minimal support (state captured after stream completes)
- Multi-agent OpenAI backend deep integration deferred to future phases

## 0.6.0

### Added

- **RunStateStore protocol** — framework-level persistence abstraction for interrupted runs
- **InMemoryRunStateStore** — in-memory implementation for development/testing
- **SQLiteRunStateStore** — SQLite-backed implementation for production persistence
- **InterruptedRun model** — captures full run state (context, interruptions, approval IDs, backend state)
- **RunStateStatus enum** — RUNNING, INTERRUPTED, COMPLETED, FAILED, RESUMED
- **Framework-level resume** — AgentApp.resume() reads from RunStateStore, checks approval status
- **AppRunner integration** — automatically saves InterruptedRun when backend returns status=interrupted
- **Audit events** — run.interrupted and run.resumed audit events
- **FastAPI run state endpoints** — GET /runs/interrupted, GET /runs/{run_id}/state, POST /runs/{run_id}/resume
- **Config support** — runtime.run_state.type/path in YAML config
- **40+ new tests** — models, stores, AppRunner integration, resume, config, FastAPI
- **337 total tests passing**

### Known limitations

- Real OpenAI RunState pause/resume not implemented (framework-level only)
- DryRunBackend resume returns stub result, not actual re-execution
- backend_state field reserved for future OpenAI RunState payload
- No automatic retry after resume

## 0.5.0

### Added

- **Governance-aware OpenAI function tool wrapper** — `_create_governed_tool_wrapper()` wraps SDK function tools with ToolExecutor pipeline
- **OpenAI backend ToolExecutor integration** — real SDK tool calls route through permissions, approval, and audit
- **Approval-required tool output** — high-risk tools return structured `approval_required` response to the model, recorded in `AppRunResult.interruptions`
- **Permission-denied tool output** — unauthorized tool calls return structured error response
- **Audit logging** — OpenAI backend tool executions recorded with correct run_id/tenant_id
- **Context binding** — `compile_agent()` and `compile_tool()` accept `RunContext` for per-run governance
- **Interruption detection** — `_extract_governance_interruptions()` scans SDK results for approval_required markers
- **Config loader governance injection** — `build_app()` injects approval_store, audit_logger, permission_checker into OpenAI backend
- **25+ new governance tests** — coverage for governance wrapper, context binding, interruption detection, config loader
- **63 total OpenAI backend tests** — 38 Phase 7 + 25 Phase 8

### Known limitations

- Real OpenAI RunState pause/resume is not implemented; approval_required returned as tool output
- Deep HITL native integration deferred to future phases
- Multi-agent handoff/orchestrator with OpenAI backend not yet deeply integrated
- DryRunBackend remains recommended for eval and governance regression testing

## 0.4.0

### Added

- **OpenAIAgentsBackend**: Real OpenAI Agents SDK execution backend
- **compile_agent()**: Compile `AgentSpec` → `agents.Agent` with tool resolution from ToolRegistry
- **compile_tool()**: Compile framework tools → SDK `function_tool`
- **Backend protocol conformance**: `OpenAIAgentsBackend` satisfies `AgentBackend` runtime_checkable protocol
- **Config backend selection**: `runtime.backend` supports `"dry_run"` (default) and `"openai"`
- **Lazy SDK loading**: `_load_agents_sdk()` imports SDK only when needed; clear RuntimeError if missing
- **Output extraction**: Handles `final_output`, `output`, `content`, `str(result)` from SDK results
- **Tool call extraction**: Extracts tool_calls from SDK RunResult with fallback attribute names
- **Streaming support**: `stream()` delegates to `Runner.run_streamed` with fallback to `run()`
- **openai_basic example**: Single-agent example with math tool and OpenAI backend
- **40+ new tests**: Missing dependency, compile_agent/tool, run/stream, config loader, protocol conformance

### Known limitations

- Framework governance pipeline (permissions, approval, audit) does not intercept real SDK tool execution
- Real OpenAI RunState resume is not implemented
- Multi-agent handoff/orchestrator with OpenAI backend not yet deeply integrated
- DryRunBackend is still the recommended backend for eval and governance regression testing
- DAG workflows not implemented

## 0.3.0

### Added

- **RoutingPolicy**: Declarative YAML-based routing rules for handoff and orchestrator workflows
- **RoutingRule / RoutingPolicy models**: Keyword, regex, and default match types with priority ordering
- **RoutingPolicyExecutor**: `route_one()` for handoff, `route_many()` for orchestrator
- **WorkflowTrace / WorkflowStep**: Structured execution observability recorded in `AppRunResult.workflow_trace`
- **Backward compatibility**: Heuristic fallback when no routing policy is configured
- **Eval assertions**: `routing_decisions` and `workflow_steps` assertion support
- **customer_support example upgraded**: Configurable routing policy with 4 rules (refund, billing, technical, default)
- **research_assistant example upgraded**: Configurable routing policy with 3 specialist rules
- **25 new tests**: Routing models, executor, config loader, workflow trace, eval assertions, backward compat

### Known limitations

- OpenAI backend integration is minimal; real RunState resume is not implemented
- DryRunBackend tool matching uses keyword heuristics, not real LLM reasoning
- DAG workflows are stubs only
- Eval runner validates framework governance logic, not model quality
- SQLite stores are basic; no connection pooling or migration system

## 0.2.0

### Added

- **Workflow.handoff**: Multi-agent handoff (triage) workflow with keyword-based routing
- **Workflow.orchestrator**: Multi-agent orchestrator workflow with specialist delegation
- **WorkflowExecutor**: Dedicated executor dispatching by `WorkflowType`
- **AppRunResult.agent_calls**: New field recording specialist agent invocations
- **Handoff routing**: Keyword-based intent detection (refund, billing, technical_support)
- **Orchestrator routing**: Keyword-based specialist selection (researcher, analyst, writer)
- **Eval assertions**: `handoffs` and `agent_calls` assertion support
- **customer_support example upgraded**: Multi-agent handoff with triage → refund/billing/technical_support
- **research_assistant example**: New orchestrator example with manager/researcher/analyst/writer
- **Config loader**: Support for `type: handoff` and `type: orchestrator` workflow configs
- **26 new tests**: Workflow model, routing, executor integration, eval assertions

### Known limitations

- OpenAI backend integration is minimal; real RunState resume is not implemented
- DryRunBackend tool matching uses keyword heuristics, not real LLM reasoning
- DAG workflows are stubs only
- Eval runner validates framework governance logic, not model quality
- SQLite stores are basic; no connection pooling or migration system

## 0.1.0

### Added

- **Core module**: `AgentSpec`, `ToolSpec`, `Workflow`, `RunContext`, `AppRunResult`
- **Registry system**: `AgentRegistry`, `ToolRegistry`, `WorkflowRegistry`, `PolicyRegistry`
- **Tool decorator**: `@tool()` with auto-registration into global default registry
- **Config loader**: YAML-based `agentapp.yaml` with `load_config()` and `build_app()`
- **DryRunBackend**: Default no-op backend for testing without real API calls
- **Session stores**: `InMemorySessionStore`, `SQLiteSessionStore` with factory
- **Streaming events**: `StreamEventType` (7 types), `StreamEvent`, `stream_events()` helper
- **FastAPI adapter**: `create_fastapi_app()` with `/health`, `/agents`, `/tools`, `/workflows`,
  `/runs`, `/runs/stream`, `/approvals`, `/approvals/{id}/approve`, `/approvals/{id}/reject`,
  `/runs/{run_id}/resume` endpoints
- **Tool governance**: `ToolExecutor` with permission check → approval gate → execute → audit
- **Permission checker**: `DefaultPermissionChecker` with role-based matching
- **Approval store**: `InMemoryApprovalStore`, `SQLiteApprovalStore` (CRUD, tenant filtering)
- **Audit logger**: `InMemoryAuditLogger`, `SQLiteAuditLogger` (multi-dimensional filtering)
- **Eval runner**: YAML-defined suites with assertions for status, output, tools, approvals,
  error types, and approve-and-resume flows
- **CLI**: `agentapp eval run <suite> --config <config>` command
- **Customer support example**: Complete working example with order.query and refund.request tools,
  SQLite session, evals, FastAPI entry point

### Known limitations

- OpenAI backend integration is minimal; real RunState resume is not implemented
- DryRunBackend tool matching uses keyword heuristics, not real LLM reasoning
- Handoff and orchestrator workflow types are stubs only
- Eval runner validates framework governance logic, not model quality
- SQLite stores are basic; no connection pooling or migration system
