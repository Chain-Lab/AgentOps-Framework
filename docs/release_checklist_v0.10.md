# v0.10.0 Release Checklist

## Tests

- [x] `python -m pytest tests/unit/test_dag.py` — 294 passed, 0 failed (Phase 13.9.1 stabilization complete)
- [x] 30 compensation tests passing (TestCompensationConfigLoading, TestSequentialCompensation, TestParallelCompensation, TestDeadlineCompensation, TestTimeoutRetryCompensation, TestBranchCompensation, TestCompensationEvents)
- [x] No regressions in existing passing tests

## Phase 13.9: Compensation Handlers + Rollback

- [x] `CompensationStatus` enum (NOT_STARTED, RUNNING, COMPLETED, PARTIAL, FAILED, SKIPPED)
- [x] `NodeCompensationResult` model (node_id, status, started_at, completed_at, attempts, error, output)
- [x] `WorkflowCompensationResult` model (status, compensated_nodes, skipped_nodes, failed_nodes, results)
- [x] `CompensationError` exception class
- [x] `DagNode.compensate` field (function, inputs, timeout_seconds, retry)
- [x] `DagWorkflow.compensation` field (enabled, trigger_on, continue_on_failure, timeout_seconds)
- [x] `_should_trigger_compensation()` — gating logic based on status and policy
- [x] `_get_compensation_candidates()` — reverse completion order selection of COMPLETED nodes with compensate config
- [x] `_execute_compensation()` — main orchestration loop with best-effort semantics
- [x] `_execute_compensation_handler()` — individual handler execution with timeout
- [x] `_resolve_compensation_inputs()` — input mapping resolution for handlers
- [x] 7 compensation event types recorded (WORKFLOW/NODE started/completed/failed/skipped)
- [x] Sequential executor integration — compensation after workflow failure
- [x] Parallel executor integration — compensation after workflow failure
- [x] `Workflow.dag()` accepts `compensation` parameter with validation
- [x] Config validation: compensation must be dict; trigger_on values validated
- [x] Backward compatibility: old configs without compensation load unchanged
- [x] 30 new Phase 13.9 tests

## Phase 13.8: Workflow-level Deadline

- [x] `NodeType.FUNCTION` enum value added
- [x] `FunctionRegistry` with `@workflow_function` decorator
- [x] `_execute_function_node()` in DagExecutor
- [x] `_resolve_function_inputs()` for input mapping
- [x] `_resolve_path()` for nested access
- [x] Workflow.dag() handles `function` field for FUNCTION nodes
- [x] Config loader passes `function` field from YAML
- [x] 53 new Phase 13.4 tests

## Phase 13.5: FUNCTION Node Permissions

- [x] Function-level permissions from registry metadata
- [x] Node-level permissions from YAML config
- [x] Permission check against execution_context["permissions"]
- [x] FUNCTION_PERMISSION_DENIED event recording
- [x] DagError with dict args for permission_denied
- [x] 37 new Phase 13.5 tests

## Phase 13.6: Subworkflow Node

- [x] `NodeType.SUBWORKFLOW` enum value added
- [x] `DagNode.subworkflow_name` field
- [x] `_execute_subworkflow_node()` in DagExecutor
- [x] WorkflowRegistry lookup with KeyError handling
- [x] DAG type validation
- [x] Cycle detection via `_subworkflow_chain`
- [x] Input mapping reuse (`_resolve_function_inputs`)
- [x] Permission inheritance
- [x] Output wrapping with metadata
- [x] Status propagation (subworkflow_failed error type)
- [x] SUBWORKFLOW_STARTED/COMPLETED/FAILED events
- [x] Workflow.dag() handles subworkflow nodes
- [x] 19 new Phase 13.6 tests

## Phase 13.7: Conditional Branch DSL Extensions

- [x] `condition.py` extended with IN, NOT IN, STARTS_WITH, ENDS_WITH operators
- [x] `InExpression` AST node added
- [x] Comma support in tokenizer
- [x] `NodeType.IF_ELSE` enum value added
- [x] `NodeType.SWITCH` enum value added
- [x] `DagNode.then`, `DagNode.else_branch`, `DagNode.switch_expr`, `DagNode.cases` fields
- [x] `_execute_if_else_node()` in DagExecutor
- [x] `_execute_switch_node()` in DagExecutor
- [x] `IfElseResult` model with condition_result, then_status, else_status
- [x] `SwitchResult` model with matched_value, matched_case_index
- [x] `resolve_expression_value()` for switch expression evaluation
- [x] `_result:<id>` in execution_context for condition evaluators
- [x] Workflow.dag() handles if_else/switch nodes
- [x] Config loader handles `else` alias → `else_branch`, `default` in input
- [x] 37 new Phase 13.7 tests

## Phase 13.8: Workflow-level Deadline

- [x] `deadline_seconds` field on `DagWorkflow` with `gt=0` validation
- [x] `WorkflowDeadlineExceededError` exception class
- [x] `_DeadlineState` helper with `remaining()`, `is_exceeded()`, `check()`, `effective_timeout()`
- [x] Sequential deadline enforcement — checks before each node; marks remaining as SKIPPED
- [x] Parallel deadline enforcement — `asyncio.wait` with deadline timeout; best-effort cancellation
- [x] Deadline-aware retry — `min(node_timeout, remaining_deadline)` as effective timeout; backoff capped
- [x] Subworkflow deadline inheritance — `min(parent_remaining, child_configured)`
- [x] IF_ELSE/SWITCH deadline inheritance — branches share parent's absolute deadline
- [x] `WORKFLOW_DEADLINE_EXCEEDED` event with metadata (deadline_seconds, elapsed, node IDs)
- [x] `NODE_CANCELLED_BY_DEADLINE` event for cancelled nodes
- [x] `Workflow.dag()` factory accepts `deadline_seconds`; validates > 0
- [x] Config loader passes `deadline_seconds` from YAML
- [x] 34 new Phase 13.8 tests (6 config + 6 state + 7 sequential + 5 parallel + 4 retry + 3 branch + 3 events)
- [x] `refund_deadline_dag` example workflow with 5s deadline

## customer_support Examples

- [x] `refund_dag` — basic sequential DAG (Phase 13.1)
- [x] `refund_parallel_dag` — parallel execution (Phase 13.2)
- [x] `refund_conditional_dag` — conditional node execution (Phase 13.3)
- [x] `refund_function_dag` — function nodes with input mapping (Phase 13.4)
- [x] `refund_subworkflow` — subworkflow definition (Phase 13.6)
- [x] `refund_with_subworkflow` — parent DAG using subworkflow (Phase 13.6)
- [x] `refund_if_else_dag` — if/else conditional branching (Phase 13.7)
- [x] `refund_switch_dag` — switch multi-way routing (Phase 13.7)
- [x] `refund_compensation_dag` — compensation with rollback handlers (Phase 13.9)

## Eval Suites

- [x] `customer_support_dag.yaml` — basic DAG eval
- [x] `customer_support_parallel_dag.yaml` — parallel DAG eval
- [x] `customer_support_conditional_dag.yaml` — conditional DAG eval
- [x] `customer_support_function_dag.yaml` — function DAG eval
- [x] `customer_support_subworkflow.yaml` — subworkflow eval (4 cases)
- [x] `customer_support_branch.yaml` — if_else/switch eval (6 cases)
- [x] `customer_support_compensation.yaml` — compensation eval (3 cases)

## Phase 13.9.1: Regression Stabilization + Release Baseline Recovery

- [x] Fixed 3-value unpacking of `execute()` return (4-tuple) in 3 locations:
  - `tests/unit/test_dag.py` — 62 occurrences
  - `tests/unit/test_dag.py` — 2 occurrences (`_, status, _`)
  - `agent_app/runtime/workflow_executor.py:552` — `AppRunner._run_dag_workflow`
  - `agent_app/workflows/dag.py:1987` — `_execute_subworkflow_node`
- [x] All 294 DAG tests pass (was 215 pass / 79 fail)
- [x] Full test suite: 871 passed, 5 skipped, 0 failed (+82 new Phase 14.1 tests)
- [x] No compensation logic changes — only return value compatibility fixes
- [x] Backward compatibility verified: all callers now handle 4-tuple

## Phase 14.0: Persisted DAG Execution State + Crash Recovery Foundation

### Tests

- [x] `python -m pytest tests/unit/test_dag_run_state.py` — 53 passed, 0 failed (Phase 14.0)
- [x] `python -m pytest tests/unit/test_dag.py` — 294 passed, 0 failed
- [x] Full test suite: 842 passed, 2 warnings, 0 failed (Phase 14.0 baseline)
- [x] No regressions in existing passing tests

### State Models

- [x] `WorkflowRunState` — run_id, workflow_name, status, input, output, error, timestamps, metadata
- [x] `NodeExecutionState` — run_id, node_id, node_type, status, input, output, error, attempts, timestamps
- [x] `WorkflowEventState` — event_id, run_id, node_id, event_type, payload, created_at
- [x] `CompensationExecutionState` — run_id, node_id, handler_name, status, error, timestamps
- [x] `RecoveryPlan` — resumable, completed_nodes, interrupted_nodes, failed_nodes, compensation_started, reason

### Store Implementations

- [x] `WorkflowStateStore` protocol — async CRUD interface for runs, nodes, events, compensations
- [x] `InMemoryWorkflowStateStore` — in-memory dict-based implementation
- [x] `SQLiteWorkflowStateStore` — SQLite-backed with auto-create tables, JSON serialization, ISO datetime
- [x] `create_workflow_state_store()` factory — memory/sqlite types
- [x] `_build_recovery_plan()` shared recovery logic

### DAG Executor Integration

- [x] `DagExecutor.__init__()` — optional `state_store` and `run_id` parameters
- [x] `DagExecutor.execute()` — creates workflow run record, persists final status
- [x] Node state persistence — sequential and parallel modes
- [x] Event persistence — workflow.started, workflow.completed/failed
- [x] Compensation state persistence — started/completed/failed for each handler
- [x] No state_store preserves old behavior (backward compatible)

### Config Support

- [x] `runtime.workflow_state.type` — memory (default) or sqlite
- [x] `runtime.workflow_state.path` — SQLite db path
- [x] Nested dict config: `workflow_state: {type: sqlite, path: ...}`
- [x] Flat string config: `workflow_state: memory`
- [x] Config loader wires store creation and passes to AgentApp

### Call Chain

- [x] `AgentApp.__init__()` — `dag_state_store` parameter
- [x] `AgentApp._ensure_runner()` — passes `dag_state_store` to AppRunner
- [x] `AgentApp._run_workflow()` — passes `dag_state_store` to WorkflowExecutor
- [x] `AppRunner.__init__()` — `dag_state_store` parameter
- [x] `WorkflowExecutor.__init__()` — `dag_state_store` parameter
- [x] `WorkflowExecutor._run_dag()` — passes `state_store` and `run_id` to DagExecutor

## Documentation

- [x] CHANGELOG.md updated with all Phase 13.x features
- [x] README.md DAG Workflows section added
- [x] README.md limitations updated
- [x] README.md roadmap updated (v0.10 DAG ✅)
- [x] `docs/release_checklist_v0.10.md` created

## Known Limitations (v0.10.0)

- Local asyncio concurrency only — not distributed DAG execution
- No Temporal / Celery backend
- Retry not applied to interrupted (approval) nodes
- Compensation timeout is shared across all handlers (not per-handler)
- Compensation is best-effort — no guarantee of completion; handler failures are logged but not retried at workflow level
- Parallel compensation order is based on completion timestamp (may vary between runs)
- Subworkflow compensation delegates to parent (no independent subworkflow compensation yet)
- Condition DSL is safe subset — no arbitrary Python expressions, no function calls
- Deadline cancellation is best-effort — external side effects may have already occurred
- No visual DAG editor
- Switch expression must resolve to a single value (not complex expressions)
- Subworkflow output wrapping may change in future versions
- RecoveryPlan is inspect/planning only — no automatic resumption of interrupted nodes
- No distributed locking or worker lease mechanism
- No exactly-once execution guarantee
- SQLite store uses stdlib sqlite3 — no connection pooling or WAL mode
- State store is DAG-specific — does not cover SINGLE/HANDOFF/ORCHESTRATOR workflow types

## Phase 14.1: DAG Resume Semantics

### Tests

- [x] `python -m pytest tests/unit/test_dag_run_state.py` — 82 passed, 0 failed (53 Phase 14.0 + 29 Phase 14.1)
- [x] `python -m pytest tests/unit/test_dag.py` — 294 passed, 0 failed
- [x] Full test suite: 871 passed, 5 skipped, 0 failed
- [x] No regressions in existing passing tests

### Resume Models

- [x] `ResumePolicy` — retry_failed, retry_interrupted, skip_completed, allow_after_compensation_started
- [x] `NodeResumeDecision` — node_id, action (skip/retry/run/blocked), reason
- [x] `ResumePlan` — resumable, decisions, completed_nodes, skipped_nodes, retry_nodes, blocked_nodes, reason
- [x] `ResumeResult` — run_id, status, resumed, skipped_nodes, retried_nodes, final_output, error

### Store Resume Methods

- [x] `WorkflowStateStore.build_resume_plan(run_id, policy)` — policy-driven per-node decisions
- [x] `WorkflowStateStore.get_node_outputs(run_id)` — dict of node_id → output
- [x] `_build_resume_plan()` — shared logic in dag_state_store.py; handles all node statuses
- [x] `InMemoryWorkflowStateStore.list_runs()` — list all persisted runs
- [x] `SQLiteWorkflowStateStore.list_runs()` — list all persisted runs

### Resume Plan Logic

- [x] Completed nodes with output → "skip" (reuse persisted output)
- [x] Completed nodes without output → "blocked" (cannot safely resume)
- [x] Interrupted nodes (RUNNING without completed_at) → "retry" if policy allows
- [x] Failed nodes → "retry" if retry_failed=True, else "blocked"
- [x] Pending nodes → "run" (never executed)
- [x] Skipped nodes → "skip"
- [x] Compensation started (compensation records exist) → resumable=False
- [x] Blocked nodes don't prevent overall resume (handled downstream)

### DagExecutor.resume()

- [x] Validates state_store and run_id configured
- [x] Loads persisted WorkflowRunState, NodeExecutionState list, CompensationExecutionState list
- [x] Records workflow.resume_started event (trace collector + state store)
- [x] Blocks if compensation started (unless policy allows)
- [x] Injects persisted outputs for completed/skipped nodes into execution_context
- [x] Records node.skipped_completed events
- [x] Executes retry/run nodes in topological order
- [x] Supports condition checking, deadline checking, downstream skipping
- [x] Persists resumed node states after each execution
- [x] Records final workflow status (resume_completed or resume_failed)
- [x] Optionally triggers compensation if workflow fails during resume
- [x] Returns 4-tuple: (results, overall_status, final_output, compensation_result)

### API Wiring

- [x] `AgentApp.resume_workflow_run(workflow, run_id, input, permissions, resume_policy)` — public API
- [x] `AppRunner.resume_workflow_run(workflow, run_id, ...)` — looks up workflow, delegates to WorkflowExecutor
- [x] `WorkflowExecutor.resume_workflow_run(workflow, run_id, ...)` — reconstructs DagWorkflow, creates DagExecutor, calls resume()
- [x] `WorkflowExecutor.__init__()` — new `app_runner` parameter for DAG agent node execution during resume
- [x] `AppRunner.__init__()` — creates WorkflowExecutor with `app_runner=self`
- [x] `AgentApp._run_workflow()` — uses AppRunner's WorkflowExecutor (shares dag_state_store)

### Documentation

- [x] CHANGELOG.md updated with Phase 14.1 section
- [x] README.md limitations updated with resume semantics notes
- [x] `docs/release_checklist_v0.10.md` Phase 14.1 section added

### Known Limitations (v0.10.0 + Phase 14.1)

- Resume is explicit (user calls `app.resume_workflow_run()`); no automatic resume on app restart
- `allow_after_compensation_started` is accepted but not implemented (default False blocks resume)
- Parallel compensation order is based on completion timestamp (may vary between runs)
- Deadline cancellation is best-effort — external side effects may have already occurred
- Subworkflow compensation delegates to parent (no independent subworkflow compensation yet)
- No distributed execution, Temporal/Celery backend, or visual DAG editor

## Phase 15: Distributed Execution Readiness

### Tests

- [x] `python -m pytest tests/unit/test_dag_run_state.py` — 123 passed, 0 failed (Phase 14.0 + 14.1 + 15)
- [x] `python -m pytest tests/unit/test_dag.py` — 294 passed, 0 failed
- [x] Full test suite: 912 passed, 2 warnings, 0 failed (+41 new Phase 15 tests)
- [x] No regressions in existing passing tests

### Phase 15 Models

- [x] `WorkerIdentity` — worker_id, hostname, process_id, app_version, metadata
- [x] `LeaseStatus` — ACQUIRED, DENIED, EXPIRED, RELEASED
- [x] `WorkflowRunLease` — run_id, owner_id, acquired_at, expires_at, renewed_at, released_at, version
- [x] `LeasePolicy` — ttl_seconds, allow_steal_expired, renew_before_seconds
- [x] `LeaseAcquireResult` — acquired, run_id, owner_id, lease, reason, current_owner_id, expires_at
- [x] `IdempotencyRecord` — key, run_id, operation, created_at, result_ref

### WorkflowStateStore Protocol Extension

- [x] `acquire_run_lease(run_id, worker, policy)` — acquire lease on a workflow run
- [x] `renew_run_lease(run_id, worker, policy)` — renew existing lease (owner only)
- [x] `release_run_lease(run_id, worker)` — release held lease (owner only)
- [x] `get_run_lease(run_id)` — get current active lease
- [x] `list_expired_leases(before)` — list expired, unreleased leases
- [x] `put_idempotency_record(record)` — store idempotency record
- [x] `get_idempotency_record(key)` — retrieve idempotency record

### InMemory Store Lease Implementation

- [x] `_leases` dict storage (run_id → WorkflowRunLease)
- [x] `_idempotency` dict storage (key → IdempotencyRecord)
- [x] Acquire: no existing lease → success; released lease → success; active lease by same owner → refresh; active lease by different owner → deny
- [x] Renew: owner only; non-owner → KeyError
- [x] Release: owner only; non-owner → KeyError; already released → KeyError
- [x] Expired steal: allow_steal_expired=True → success; allow_steal_expired=False → deny
- [x] list_expired_leases: returns only expired, unreleased leases

### SQLite Store Lease Implementation

- [x] `workflow_run_leases` table (run_id PK, owner_id, acquired_at, expires_at, renewed_at, released_at, version)
- [x] `workflow_idempotency` table (key PK, run_id, operation, created_at, result_ref)
- [x] `_sync_leases_from_db()` — loads active leases into memory cache on init
- [x] `_sync_idempotency_from_db()` — loads idempotency records into memory cache on init
- [x] acquire persists to SQLite; visible to new store instances
- [x] renew persists to SQLite; version incremented
- [x] release persists to SQLite; released_at set
- [x] list_expired_leases queries SQLite directly (cross-instance)
- [x] Old DB without lease/idempotency tables migrates automatically (OperationalError handling)
- [x] Row-to-model converters: `_row_to_lease()`, `_row_to_idempotency()`

### DagExecutor Lease Integration

- [x] `DagExecutor.__init__()` — new optional `worker` parameter
- [x] `_get_worker()` — returns explicit worker or generates/caches default WorkerIdentity
- [x] `_acquire_lease()` — acquires lease before execute/resume; raises DagError if denied
- [x] `_release_lease()` — releases lease after execute/resume; handles KeyError gracefully
- [x] `DagExecutor.execute()` — wraps execution in try/acquire/finally/release
- [x] `DagExecutor.resume()` — acquires lease after resume plan validation; wraps in try/finally/release
- [x] Lease events persisted: `workflow.lease_acquired`, `workflow.lease_released`

### Worker Identity Plumbing

- [x] `AgentApp.run()` — new optional `worker` parameter; forwarded to WorkflowExecutor
- [x] `AgentApp._run_workflow()` — new optional `worker` parameter; forwarded to WorkflowExecutor
- [x] `AgentApp.resume_workflow_run()` — new optional `worker` parameter; forwarded to AppRunner
- [x] `WorkflowExecutor.run_workflow()` — new optional `worker` parameter; forwarded to `_run_dag()`
- [x] `WorkflowExecutor._run_dag()` — new optional `worker` parameter; passed to DagExecutor
- [x] `WorkflowExecutor.resume_workflow_run()` — new optional `worker` parameter; passed to DagExecutor
- [x] `AppRunner.resume_workflow_run()` — new optional `worker` parameter; forwarded to WorkflowExecutor

### Idempotency

- [x] InMemory store: put/get/overwrite idempotency records
- [x] SQLite store: put/get/persist across instances
- [x] Duplicate key behavior is deterministic (overwrite)

### Documentation

- [x] CHANGELOG.md updated with Phase 15 section
- [x] README.md limitations updated with Phase 15 notes
- [x] README.md roadmap updated (Phase 15 ✅)
- [x] `docs/release_checklist_v0.10.md` Phase 15 section added

### Known Limitations (v0.10.0 + Phase 15)

- Lease is best-effort coordination — does not provide exactly-once guarantee
- No Celery / Temporal / distributed worker backend
- No automatic recovery daemon
- No node-level distributed scheduling
- No cross-process streaming fanout
- SQLite store uses stdlib sqlite3 — no connection pooling or WAL mode
- Lease TTL is in-memory checked; no background renewal daemon
- Idempotency records stored but not enforced at API level (Phase 15.1+)

## Phase 15.1: API-level Idempotency Enforcement

### Tests

- [x] `python -m pytest tests/unit/test_dag_run_state.py` — 157 passed, 0 failed (123 Phase 14.0+14.1+15 + 34 Phase 15.1)
- [x] `python -m pytest tests/unit/test_dag.py` — 294 passed, 0 failed
- [x] Full test suite: 944 passed, 5 skipped, 0 failed (+32 new Phase 15.1 tests)
- [x] No regressions in existing passing tests

### Request Fingerprinting

- [x] `compute_request_fingerprint()` — SHA-256 of deterministic JSON (sorted keys, no whitespace, `default=str`)
- [x] Transient field exclusion — `idempotency_key`, `worker`, `trace_id`, `request_id`, `correlation_id` excluded from fingerprint
- [x] `build_execute_payload()` — minimal payload with semantic fields only (workflow_name, agent_name, input, session_id, tenant_id, user_id, run_id, permissions)
- [x] `build_resume_payload()` — minimal payload with semantic fields only (run_id, input, tenant_id, user_id, approval_id, permissions)
- [x] Same payload produces same fingerprint regardless of dict insertion order
- [x] Different input produces different fingerprint
- [x] Nested dicts recursively filtered for transient fields

### Scope Isolation

- [x] `compute_scope(tenant_id, operation)` — produces `"{tenant_id}:{operation}"` namespace
- [x] Different tenants have different scopes (no cross-tenant key collision)
- [x] Same tenant, different operations have different scopes
- [x] Scope used as composite key component in both InMemory and SQLite stores

### Error Types

- [x] `IdempotencyError` — base error with idempotency_key, scope, operation, existing_run_id, to_dict()
- [x] `DuplicateIdempotencyKeyError` — same key + same fingerprint (true duplicate request)
- [x] `IdempotencyKeyMismatchError` — same key + different fingerprint (replay attack / client error)

### InMemory Atomic Reservation

- [x] Composite key `"{scope}:{record.key}"` for proper scope isolation
- [x] Key does not exist → create record, return it
- [x] Key exists + same fingerprint → raise `DuplicateIdempotencyKeyError`
- [x] Key exists + different fingerprint → raise `IdempotencyKeyMismatchError`
- [x] Error fields populated correctly (key, scope, operation, existing_run_id)

### SQLite Atomic Reservation

- [x] Schema: `PRIMARY KEY (scope, key)` with UNIQUE constraint
- [x] Explicit `BEGIN`/`COMMIT`/`ROLLBACK` transaction for atomicity
- [x] `IntegrityError` caught to determine conflict type (duplicate vs mismatch)
- [x] Cross-instance visibility — duplicate rejected by different SQLite store instances
- [x] Scope isolation verified across instances
- [x] Schema migration: `_add_idempotency_columns()` handles old databases without scope column
- [x] Old schema backward compatible: `_row_to_idempotency()` uses `setdefault` for missing columns

### DagExecutor Integration

- [x] `_enforce_idempotency()` called before lease acquire in `execute()`
- [x] `_enforce_idempotency()` called before lease acquire in `resume()`
- [x] Worker identity cached (`_cached_worker`) for consistent fingerprinting
- [x] Current input cached (`_current_input`) for fingerprinting (RunContext has no input field)
- [x] Duplicate key → DagError wrapping `DuplicateIdempotencyKeyError`
- [x] Key mismatch → DagError wrapping `IdempotencyKeyMismatchError`
- [x] No key → no enforcement (old behavior preserved)
- [x] No state_store → no enforcement (old behavior preserved)

### API Plumbing

- [x] `AgentApp.run()` — optional `idempotency_key` parameter
- [x] `AgentApp.resume_workflow_run()` — optional `idempotency_key` parameter
- [x] `AppRunner.run()` — optional `idempotency_key` parameter
- [x] `AppRunner.resume_workflow_run()` — optional `idempotency_key` parameter
- [x] `WorkflowExecutor.run_workflow()` — optional `idempotency_key` parameter
- [x] `WorkflowExecutor.resume_workflow_run()` — optional `idempotency_key` parameter
- [x] `DagExecutor.__init__()` — optional `idempotency_key` parameter

### FastAPI Integration

- [x] `Idempotency-Key` HTTP header extraction with priority over body
- [x] `RunRequest.idempotency_key` field in JSON body
- [x] `/runs` endpoint supports idempotency enforcement
- [x] `/runs/{run_id}/resume` endpoint supports idempotency enforcement
- [x] `DuplicateIdempotencyKeyError` → HTTP 409 Conflict
- [x] `IdempotencyKeyMismatchError` → HTTP 409 Conflict
- [x] `_is_idempotency_error()` helper identifies idempotency errors in result.error
- [x] `_extract_idempotency_error()` helper handles DagError wrapping and direct IdempotencyError

### Documentation

- [x] CHANGELOG.md updated with Phase 15.1 section
- [x] README.md idempotency section added
- [x] README.md limitations updated with idempotency notes
- [x] `docs/release_checklist_v0.10.md` Phase 15.1 section added

### Known Limitations (v0.10.0 + Phase 15.1)

- Best-effort API-level duplicate prevention — NOT exactly-once execution
- Without `idempotency_key`: old behavior unchanged (no enforcement)
- With `idempotency_key`: single-use enforcement before side-effect-producing operations
- No Celery / Temporal / distributed worker backend
- No automatic recovery daemon
- No node-level distributed scheduling
- No cross-process streaming fanout
- SQLite store uses stdlib sqlite3 — no connection pooling or WAL mode
- Lease TTL is in-memory checked; no background renewal daemon
- Scope defaults to `{tenant_id}:{operation}`; cannot be customized per-request
- Fingerprint is best-effort; semantically identical payloads with different serialization will produce different fingerprints

## Phase 15.2: Background Lease Renewal / Heartbeat

### Implementation

- [x] `LeaseRenewer` class in `agent_app/runtime/lease_renewer.py`
- [x] `LeaseLostError` exception class with `to_dict()` method
- [x] `LeaseRenewer.start()` — creates asyncio background task
- [x] `LeaseRenewer.stop()` — idempotent, waits for task completion
- [x] `LeaseRenewer.__aenter__/__aexit__` — async context manager support
- [x] `_renew_loop()` — background loop with interval sleep, run status check, renewal attempt
- [x] `lease_lost` flag set on renewal failure
- [x] `_last_error` captures the exception that caused lease loss
- [x] Auto-stop on terminal run states (completed/failed/partial)

### Store API

- [x] `renew_run_lease()` added to `WorkflowStateStore` protocol
- [x] `InMemoryWorkflowStateStore.renew_run_lease()` — validates owner, release, expiration
- [x] `SQLiteWorkflowStateStore.renew_run_lease()` — same validation with SQLite persistence
- [x] Expired lease detection: `now >= expires_at` raises KeyError
- [x] Released lease detection: `released_at is not None` raises KeyError
- [x] Non-owner renewal rejection

### DagExecutor Integration

- [x] `DagExecutor.__init__()` — new optional `lease_renewal_config` parameter
- [x] `_make_renewer()` — creates LeaseRenewer or returns None
- [x] `execute()` — starts renewer before DAG, stops in finally, deferred LeaseLostError
- [x] `resume()` — same pattern for resume path
- [x] `_get_worker_sync()` — synchronous worker access for error reporting
- [x] Idempotency ordering preserved (enforce → acquire → renewer start → execute → renewer stop → release)

### Config

- [x] `LeaseRenewalConfig` Pydantic model in `agent_app/config/schema.py`
- [x] `RuntimeConfig.lease_renewal_config` field
- [x] `_normalize_lease_renewal` validator for nested YAML support
- [x] Config loader passes `lease_renewal_config` to AgentApp

### Parameter Plumbing

- [x] `AgentApp.__init__()` → `_ensure_runner()` → AppRunner
- [x] `AppRunner.__init__()` → WorkflowExecutor
- [x] `WorkflowExecutor.__init__()` → DagExecutor
- [x] `WorkflowExecutor._run_dag()` passes config to DagExecutor
- [x] `WorkflowExecutor.resume_workflow_run()` passes config to DagExecutor

### Tests (28 new tests)

- [x] `TestLeaseRenewer` (6): start/stop, context manager, lease_lost on failure, no pending tasks, skip completed runs
- [x] `TestInMemoryLeaseRenewal` (6): renew after acquire, non-owner fails, nonexistent run, expired fails, extends TTL, after release fails
- [x] `TestSQLiteLeaseRenewal` (5): renew succeeds, non-owner fails, persists across instances, expired fails, after release fails
- [x] `TestDagExecutorLeaseRenewal` (5): no renew when disabled, renewer created when enabled, no renew without store, lease_lost error, idempotency ordering
- [x] `TestLeaseRenewalConfig` (5): defaults, custom values, invalid interval, invalid TTL, backward compat

### Acceptance Criteria

- [x] LeaseRenewer starts/stops cleanly
- [x] LeaseRenewer sets lease_lost=True on renewal failure
- [x] Expired leases cannot be renewed
- [x] Released leases cannot be renewed
- [x] Non-owner renewal rejected
- [x] DagExecutor raises LeaseLostError when renewer loses lease
- [x] renew_enabled=False disables auto-renewal
- [x] Default interval = ttl_seconds / 3
- [x] No Celery/Temporal/distributed daemon
- [x] No exactly-once claims

### Known Limitations (Phase 15.2)

- Best-effort in-process renewal — NOT exactly-once
- Only works while process is alive
- No distributed worker backend
- Renewal failure → lease_lost → stable error requiring manual resume

## Phase 16.0: DAG Persistence Snapshots and Enhanced Resume

### Tests

- [x] `python -m pytest tests/unit/test_dag_snapshot.py` — 43 passed, 0 failed (Phase 16.0 models/store/config)
- [x] `python -m pytest tests/unit/test_dag_executor_snapshot.py` — 19 passed, 0 failed (Phase 16.0 DagExecutor integration)
- [x] Full test suite: 1034 passed, 5 skipped, 0 failed (+62 new Phase 16.0 tests)
- [x] No regressions in existing passing tests

### Snapshot Data Models

- [x] `DagSnapshotStatus` — StrEnum: RUNNING, COMPLETED, FAILED, PARTIAL, INTERRUPTED
- [x] `DagNodeSnapshot` — node_id, status, attempts, output, error, started_at, completed_at
- [x] `DagRunSnapshot` — snapshot_id, run_id, workflow_name, status, schema_version, completed/failed/current/pending_node_ids, nodes, execution_context, pending_approvals, compensation_state, created_at, updated_at
- [x] `to_json()` / `from_json()` — timezone-aware ISO datetime serialization; schema_version preserved
- [x] `snapshot_status_is_resumable()` — running/partial/failed/interrupted = True; completed = False

### Snapshot Error Types

- [x] `SnapshotWriteError` — run_id, message, to_dict()
- [x] `SnapshotCorruptionError` — run_id, message, to_dict()
- [x] `SnapshotUnsupportedVersionError` — run_id, version, to_dict()
- [x] All errors have stable `type` field for API error mapping

### Store Snapshot Methods

- [x] `save_run_snapshot(snapshot)` — persist or overwrite by snapshot_id
- [x] `get_latest_run_snapshot(run_id)` — most recent by updated_at
- [x] `list_run_snapshots(run_id)` — ordered by updated_at ascending
- [x] `delete_run_snapshots(run_id)` — remove all snapshots for a run
- [x] InMemory: `_snapshots: dict[str, list[DagRunSnapshot]]`
- [x] SQLite: `dag_run_snapshots` table with `snapshot_json` TEXT column
- [x] SQLite index: `idx_dag_run_snapshots_run_updated` on (run_id, updated_at)
- [x] Snapshot corruption: invalid JSON → `SnapshotCorruptionError`
- [x] Unsupported version: schema_version != 1 → `SnapshotUnsupportedVersionError`

### DagSnapshotConfig

- [x] `enabled` (default True) — master switch for snapshot persistence
- [x] `store` (default "memory") — "memory" or "sqlite"
- [x] `path` (default None) — SQLite db path (required when store="sqlite")
- [x] `save_on_node_start` (default True) — snapshot when node begins
- [x] `save_on_node_complete` (default True) — snapshot when node completes
- [x] `save_on_interrupt` (default True) — snapshot on interrupt (e.g., approval wait)
- [x] `save_on_failure` (default True) — snapshot on node/workflow failure
- [x] Pydantic validation: `store` must be "memory" or "sqlite"

### DagExecutor Snapshot Integration

- [x] `_is_snapshot_enabled()` — checks state_store, run_id, snapshot_config.enabled
- [x] `_build_snapshot()` — constructs DagRunSnapshot from execution state
- [x] `_save_snapshot()` — async save with SnapshotWriteError wrapping
- [x] `_maybe_save_snapshot()` — best-effort save (logs warning on failure)
- [x] `execute()` — saves initial "running" snapshot after lease acquire
- [x] `execute()` — saves "completed" snapshot on successful finish
- [x] `execute()` — saves "failed" snapshot in finally block when exception occurs
- [x] `_execute_sequential()` — calls `_maybe_save_snapshot()` after each node and on failure
- [x] `_execute_parallel()` — calls `_maybe_save_snapshot()` after each node batch
- [x] Snapshot write failure → stable `SnapshotWriteError` for initial/final, warning for intermediate

### DagExecutor Resume with Snapshots

- [x] `resume()` — reads `get_latest_run_snapshot()` when snapshot enabled
- [x] Schema version validation — only v1 supported; unsupported → `SnapshotUnsupportedVersionError`
- [x] Run ID validation — mismatch → `SnapshotRunIdMismatchError`
- [x] Completed snapshot → idempotent return `([], "completed", None, None)`
- [x] Non-resumable snapshot (running/partial/failed/interrupted) → falls through to existing resume logic
- [x] Snapshot errors caught by `except Exception` → fall through to existing resume logic (graceful degradation)
- [x] Snapshot data used to rebuild execution_context for resume

### Config Support

- [x] `RuntimeConfig.dag_snapshot_config` — DagSnapshotConfig field
- [x] `_normalize_dag_snapshot` validator — nested YAML `dag_snapshot: {...}` → `dag_snapshot_config`
- [x] Config loader passes `dag_snapshot_config` to AgentApp
- [x] AgentApp → AppRunner → WorkflowExecutor → DagExecutor plumbing
- [x] Backward compatible: no `dag_snapshot` in config → `dag_snapshot_config=None` → snapshots enabled by default

### Acceptance Criteria

- [x] Snapshots written at key state transitions (running, node start/complete/fail, interrupt, completion)
- [x] Snapshots survive process exit and lease expiry
- [x] Resume reads latest snapshot to skip completed nodes
- [x] Completed snapshot returns idempotent result (no re-execution)
- [x] Snapshot write failure raises stable error (not silently swallowed)
- [x] Corruption/version errors caught and fall through gracefully
- [x] 1034 tests passing, 0 failures
- [x] No Celery / Temporal / Redis / etcd / automatic recovery daemon
- [x] Snapshot is recovery aid, NOT transaction log, NOT exactly-once guarantee

## Phase 16.1: Compensation State Persistence

### Tests

- [x] `python -m pytest tests/unit/test_compensation_state.py` — 40 passed, 0 failed
- [x] `python -m pytest tests/unit/test_compensation_store.py` — 25 passed, 0 failed
- [x] `python -m pytest tests/unit/test_dag_executor_compensation_persistence.py` — 32 passed, 0 failed
- [x] Full test suite: 1131 passed, 5 skipped, 0 failed (+97 new Phase 16.1 tests)
- [x] No regressions in existing passing tests

### Compensation State Models

- [x] `CompensationActionStatus` — StrEnum: PENDING, RUNNING, COMPLETED, FAILED, SKIPPED
- [x] `CompensationRunStatus` — StrEnum: NOT_REQUIRED, PENDING, RUNNING, COMPLETED, PARTIAL_FAILED, FAILED
- [x] `CompensationActionState` — action_id (auto-generated), run_id, workflow_name, node_id, compensating_for_node_id, status, attempts, max_attempts, input, output, error, idempotency_key, started_at, completed_at
- [x] `CompensationExecutionState` — compensation_id (auto-generated), run_id, workflow_name, status, schema_version, actions dict, action_order list, timestamps
- [x] Auto-generated IDs via `default_factory` (Pydantic v2 compatible)
- [x] `model_validator(mode="after")` syncs action_order with actions keys
- [x] State transition methods: mark_running(), mark_completed(), mark_partial_failed(), mark_failed()
- [x] Action transition methods: mark_running(), mark_completed(), mark_failed(), mark_skipped(), can_retry()
- [x] Query methods: get_action(), get_pending_actions(), get_failed_retryable_actions(), get_completed_actions()
- [x] Serialization: `serialize_compensation_state()` / `deserialize_compensation_state()` with json.dumps

### Compensation State Store

- [x] `CompensationStateStore` protocol — async CRUD interface
- [x] `save_compensation_state()` — create or update
- [x] `get_compensation_state(run_id)` — retrieve by run_id
- [x] `update_compensation_action(run_id, action)` — update single action, recompute status
- [x] `list_compensation_states(workflow_name=None)` — list with optional filter
- [x] `delete_compensation_state(run_id)` — remove all state for a run
- [x] `create_compensation_state_store(store_type, db_path)` — factory function
- [x] InMemory: dict keyed by run_id; supports all CRUD operations
- [x] SQLite: `dag_compensation_states` table with compensation_id PK, run_id UNIQUE
- [x] SQLite indexes: idx_dag_compensation_states_run_id, idx_dag_compensation_states_workflow_status
- [x] Corrupted JSON handling: `list_compensation_states()` skips corrupted entries

### DagExecutor Compensation Persistence

- [x] `_init_compensation_store()` — lazy init from config (memory/sqlite)
- [x] `_is_compensation_persistence_enabled()` — checks config + store state
- [x] `_get_max_compensation_attempts()` — reads max_attempts from config
- [x] `_is_resume_incomplete_compensation()` — reads resume_incomplete from config
- [x] `_create_compensation_state()` — builds state from compensation candidates with correct node references
- [x] `_save_compensation_state()` — persists with SnapshotWriteError on failure
- [x] `_update_compensation_action()` — updates single action in store (best-effort, catches exceptions)
- [x] `_get_compensation_state()` — retrieves persisted state for current run
- [x] `_resume_compensation()` — resumes from persisted state: skips completed, retries failed within max_attempts, executes pending
- [x] execute() integration — calls `_init_compensation_store()` after renewer start; creates state before compensation loop
- [x] resume() integration — loads persisted state; calls `_resume_compensation()` when incomplete compensation found

### Config Support

- [x] `DagCompensationConfig` — Pydantic model (enabled=True, store="memory", path=None, max_attempts=1, resume_incomplete=True)
- [x] Store validator rejects unknown types (ValueError → Pydantic ValidationError)
- [x] `_normalize_dag_compensation` — maps `dag_compensation` YAML key to `dag_compensation_config`
- [x] Config loader passes `dag_compensation_config` to AgentApp
- [x] AgentApp → AppRunner → WorkflowExecutor → DagExecutor plumbing

### Documentation

- [x] CHANGELOG.md updated with Phase 16.1 section
- [x] README.md limitations updated with Phase 16.1 notes
- [x] README.md roadmap updated (Phase 16.1 ✅)
- [x] `docs/release_checklist_v0.10.md` Phase 16.1 section added

### Known Limitations (v0.10.0 + Phase 16.1)

- Compensation state is a recovery aid — does NOT guarantee exactly-once execution
- NOT a distributed transaction log (no Celery/Temporal/Redis/etcd)
- No automatic recovery daemon — resume is explicit via `app.resume_workflow_run()`
- External side effect idempotency remains the business tool's responsibility
- SQLite store uses stdlib sqlite3 — no connection pooling or WAL mode
- Compensation state is independent from snapshots and lease state (each has its own persistence layer)
- Does NOT replace lease renewal, snapshot, or business-level idempotency

## Phase 16.2: Lease Backend Abstraction

### Tests

- [x] `python -m pytest tests/unit/test_lease_backend.py` — StateStoreLeaseBackend, InMemory, SQLite, factory, protocol tests
- [x] `python -m pytest tests/unit/test_lease_coordinator.py` — LeaseCoordinator tests
- [x] `python -m pytest tests/unit/test_lease_renewer_phase16_2.py` — LeaseRenewer with lease_backend tests
- [x] `python -m pytest tests/unit/test_dag_executor_lease_backend.py` — DagExecutor lease backend tests
- [x] `python -m pytest tests/unit/test_config_lease.py` — DagLeaseConfig tests
- [x] Full test suite: 1206 passed, 0 failed (+75 new Phase 16.2 tests)
- [x] No regressions in existing passing tests

### Lease Backend Protocol

- [x] `WorkflowLeaseBackend` Protocol — 5 async methods: acquire_run_lease, renew_run_lease, release_run_lease, get_run_lease, list_expired_leases
- [x] Reuses existing models: WorkerIdentity, LeasePolicy, WorkflowRunLease, LeaseAcquireResult
- [x] All datetime fields remain timezone-aware UTC

### StateStoreLeaseBackend Adapter

- [x] `StateStoreLeaseBackend` wraps any WorkflowStateStore as WorkflowLeaseBackend
- [x] Delegates all 5 lease methods to underlying state store
- [x] Preserves denied acquire behavior (returns LeaseAcquireResult with acquired=False)
- [x] Preserves expired steal behavior (depends on policy.allow_steal_expired)

### InMemoryWorkflowLeaseBackend

- [x] Five-path acquire logic: no lease → acquire, released → acquire, expired+steal → steal, same-owner → refresh, different-owner → deny
- [x] renew_run_lease — validates owner, released status, expiration; bumps version
- [x] release_run_lease — sets released_at timestamp; validates owner
- [x] get_run_lease — returns None for released/missing leases
- [x] list_expired_leases — filters by expires_at <= cutoff, released_at IS NULL

### SQLiteWorkflowLeaseBackend

- [x] Auto-creates `workflow_run_leases` table on init (run_id PK, owner_id, timestamps, version)
- [x] Indexes on expires_at and owner_id
- [x] Five-path acquire logic (mirrors InMemory) with SQLite persistence
- [x] renew/release/get/list_expired with DB persistence
- [x] Cross-instance visibility (new backend instance sees leases from other instances)
- [x] In-memory cache with DB re-sync on get_run_lease

### LeaseCoordinator

- [x] Wraps WorkflowLeaseBackend with default LeasePolicy
- [x] acquire() — applies default policy when none provided; explicit overrides default
- [x] renew() — same policy application
- [x] release() — pass-through to backend
- [x] get() — pass-through to backend
- [x] list_expired() — pass-through with cutoff

### LeaseRenewer Phase 16.2 Support

- [x] New optional `lease_backend` parameter in __init__
- [x] Backward compatible with legacy `state_store` parameter (auto-wraps via StateStoreLeaseBackend)
- [x] `lease_backend` takes precedence when both provided
- [x] `_renew_loop` uses `self._lease_backend` for renew calls
- [x] Keeps `self._state_store` for terminal-state check (run status)
- [x] lease_lost behavior unchanged
- [x] stop behavior unchanged
- [x] async context manager behavior unchanged

### DagExecutor Lease Backend Integration

- [x] New optional `lease_backend` and `lease_policy` parameters in __init__
- [x] `_get_lease_backend()` — explicit > state_store > None priority
- [x] `_acquire_lease()` — uses effective lease backend
- [x] `_release_lease()` — uses effective lease backend
- [x] `_make_renewer()` — creates LeaseRenewer with lease_backend for standalone backends, state_store for legacy
- [x] execute() uses explicit lease_backend when provided
- [x] resume() uses explicit lease_backend when provided
- [x] Explicit lease_backend takes precedence over state_store
- [x] No lease_backend and no state_store keeps old behavior (no lease operations)
- [x] Lease acquire denied returns stable DagError

### Config Support

- [x] `DagLeaseConfig` — Pydantic model (backend="state_store", db_path=None, ttl_seconds=300, allow_steal_expired=True, renew_before_seconds=60)
- [x] Backend validator: accepts "state_store", "memory", "sqlite"; rejects others with clear error
- [x] ttl_seconds must be >= 1
- [x] `_normalize_dag_lease` — maps `dag_lease` YAML key to `dag_lease_config`
- [x] Old config without dag_lease remains valid
- [x] Config threaded through config/loader → AgentApp → AppRunner → WorkflowExecutor → WorkflowExecutor → DagExecutor

### Documentation

- [x] CHANGELOG.md updated with Phase 16.2 section
- [x] README.md limitations updated with Phase 16.2 notes
- [x] README.md roadmap updated (Phase 16.2 ✅)
- [x] `docs/release_checklist_v0.10.md` Phase 16.2 section added

### Known Limitations (v0.10.0 + Phase 16.2)

- Lease backend abstraction is a coordination layer — does NOT guarantee exactly-once guarantee
- NOT a distributed lock service (no Redis/etcd distributed lock)
- No Celery / Temporal / distributed worker daemon
- No automatic recovery daemon — resume is explicit via `app.resume_workflow_run()`
- Default lease backend is state_store-backed (delegates to existing WorkflowStateStore)
- Standalone memory/sqlite backends are single-process (memory) or cross-instance (sqlite) only
- Lease renewal only works while the current process is alive
- External side effect idempotency remains the business tool's responsibility
- SQLite store uses stdlib sqlite3 — no connection pooling or WAL mode
- Lease backend does NOT replace lease renewal, snapshot, compensation, or business-level idempotency

## Phase 16.3: Lease Backend Observability & Health Checks

### LeaseMetrics

- [x] `LeaseMetrics` thread-safe collector implemented (`agent_app/runtime/lease_metrics.py`)
- [x] Per-operation counters: acquire, renew, release, get, list_expired
- [x] Counter types: attempts, successes, failures, exceptions, denied
- [x] `threading.Lock` for mutation safety; immutable snapshot returns
- [x] `LeaseOperationMetrics` dataclass with per-counter fields
- [x] `LeaseMetricsSnapshot` immutable dataclass

### MetricsWorkflowLeaseBackend

- [x] `MetricsWorkflowLeaseBackend` wrapper in `agent_app/runtime/lease_backend.py`
- [x] Transparent metrics recording on every backend operation
- [x] Re-raises exceptions after recording (does not swallow errors)
- [x] Denied acquires recorded as failures (not exceptions)
- [x] Preserves underlying backend return values unchanged
- [x] Graceful handling when `metrics=None`

### LeaseBackendHealthChecker

- [x] `LeaseBackendHealthChecker` in `agent_app/runtime/lease_health.py`
- [x] `LeaseHealthStatus` StrEnum: HEALTHY, DEGRADED, UNHEALTHY
- [x] `LeaseHealthCheckResult` Pydantic model with timezone-aware timestamps
- [x] Backend-specific checks: memory (always ok), sqlite (query + active lease count), state_store (delegation test), metrics (inner backend), generic (non-destructive probe)
- [x] Never raises — exceptions captured in result.error
- [x] Propagates inner check errors to top-level error field when UNHEALTHY
- [x] `_detect_backend_type()` for automatic backend type detection

### LeaseCoordinator Observability

- [x] `LeaseCoordinator.__init__()` accepts optional `metrics` parameter
- [x] Auto-wraps backend with `MetricsWorkflowLeaseBackend` when metrics provided
- [x] `metrics_snapshot()` returns snapshot or None (when no metrics)
- [x] `health_check()` returns `LeaseHealthCheckResult` via `LeaseBackendHealthChecker`
- [x] `diagnostics(include_expired_sample, expired_sample_limit)` assembles health + metrics + expired leases
- [x] `LeaseDiagnostics` Pydantic model for operator visibility

### Config Support

- [x] `DagLeaseMetricsConfig` — `enabled: bool = False` (opt-in)
- [x] `DagLeaseHealthConfig` — `enabled: bool = True`
- [x] `DagLeaseConfig` extended with `metrics` and `health` fields
- [x] Config plumbing: `dag_lease.metrics.enabled` and `dag_lease.health.enabled` in YAML

### WorkflowExecutor Integration

- [x] `_build_lease_metrics()` creates collector when metrics enabled
- [x] `get_lease_health_checker()` creates health checker
- [x] `get_lease_diagnostics()` assembles full diagnostic snapshot
- [x] Metrics wrapping integrated into `_build_lease_backend()`

### Tests

- [x] `tests/unit/test_lease_metrics.py` — 14 tests (LeaseMetrics collector)
- [x] `tests/unit/test_lease_observable_backend.py` — 10 tests (MetricsWorkflowLeaseBackend)
- [x] `tests/unit/test_lease_health.py` — 7 tests (LeaseBackendHealthChecker)
- [x] `tests/unit/test_lease_coordinator_phase16_3.py` — 12 tests (Coordinator metrics/health/diagnostics)
- [x] `tests/unit/test_config_lease_phase16_3.py` — 10 tests (DagLeaseMetricsConfig, DagLeaseHealthConfig)
- [x] Full test suite: 1272 passed, 0 failed (+66 new Phase 16.3 tests)

### Documentation

- [x] CHANGELOG.md updated with Phase 16.3 section
- [x] README.md limitations updated with Phase 16.3 notes
- [x] README.md roadmap updated (Phase 16.3 ✅)
- [x] `docs/release_checklist_v0.10.md` Phase 16.3 section added

### Known Limitations (v0.10.0 + Phase 16.3)

- Metrics are in-process only — not exported to Prometheus/OpenTelemetry (no external dependency)
- Health checks are diagnostic only — do NOT guarantee backend availability or provide distributed recovery
- NOT a distributed health protocol or liveness probe
- Metrics are opt-in (`enabled=False` by default) to avoid overhead when not needed
- No background metrics export or collection daemon
- LeaseMetrics uses `threading.Lock` — not async-safe for cross-thread mutation
- Health checks are non-destructive but do not test lease acquire/renew operations
- Does NOT replace lease renewal, snapshot, compensation, or business-level idempotency

## Phase 16.4: Redis Lease Backend

### RedisWorkflowLeaseBackend

- [x] `RedisWorkflowLeaseBackend` implemented in `agent_app/runtime/lease_redis_backend.py`
- [x] Implements `WorkflowLeaseBackend` protocol (acquire, renew, release, get, list_expired)
- [x] Uses atomic Lua scripts for acquire/renew/release (EVALSHA with NOSCRIPT fallback)
- [x] Lease record stored as JSON with timezone-aware UTC ISO timestamps
- [x] `lease_token` used for holder verification on renew/release
- [x] TTL managed via Redis key TTL (EX argument)
- [x] `allow_steal_expired` policy supported in acquire Lua script
- [x] Same-holder refresh reuses existing token (detected via holder_id match)
- [x] `get_run_lease()` returns None for released or missing leases
- [x] `list_expired_leases()` uses SCAN with prefix matching and expiry filtering

### Health Check

- [x] `health_check()` uses Redis PING for connectivity test
- [x] Returns `LeaseHealthStatus.HEALTHY` when PING succeeds
- [x] Returns `LeaseHealthStatus.UNHEALTHY` when PING fails or exception occurs
- [x] `backend_type` is "redis"
- [x] `checked_at` is timezone-aware UTC
- [x] Redis URL sanitized (password hidden) in health details
- [x] Key prefix included in health details
- [x] Never raises — exceptions captured in result.error

### Diagnostics

- [x] `diagnostics()` returns `LeaseDiagnostics` with `details` field
- [x] `details` includes: backend_type, key_prefix, ttl_seconds, allow_steal_expired, redis_url_sanitized, total_lease_keys
- [x] Redis URL sanitized (password hidden with `***`)
- [x] Health check integrated into diagnostics
- [x] Graceful failure handling — scan errors do not raise
- [x] `LeaseDiagnostics` model extended with `details: dict[str, Any] | None` field

### Configuration

- [x] `DagLeaseConfig` extended with `redis_url: str | None` and `key_prefix: str | None`
- [x] Validator accepts "redis" as a valid backend type
- [x] `redis_url` defaults to "redis://localhost:6379/0"
- [x] `key_prefix` defaults to "agent_app:dag_lease" (in backend/factory)
- [x] Old configs (state_store, memory, sqlite) continue to work without changes
- [x] `create_lease_backend()` extended with `redis_url`, `key_prefix`, `ttl_seconds` params
- [x] `WorkflowExecutor._build_lease_backend()` routes "redis" to Redis backend
- [x] Redis extra is optional — `pip install -e ".[redis]"` installs `redis>=5.0`

### Optional Dependency Boundary

- [x] Default `pip install -e .` does NOT install redis
- [x] `pip install -e ".[redis]"` installs redis>=5.0
- [x] `all` extra includes redis
- [x] Top-level import of `agent_app` does not require redis
- [x] `agent_app.config.schema` does not import redis
- [x] `RedisWorkflowLeaseBackend.__init__` raises `RuntimeError` with clear message when redis not installed
- [x] Error message: "Redis lease backend requires the redis extra. Install with: pip install -e '.[redis]'"

### Metrics Integration

- [x] Redis backend works with `MetricsWorkflowLeaseBackend` wrapper (Phase 16.3)
- [x] Acquire success/failure/denied recorded correctly
- [x] No duplicate metrics logic in Redis backend itself

### Key Prefix Isolation

- [x] Configurable `key_prefix` for multi-tenant isolation
- [x] Different prefixes produce different Redis keys
- [x] Default prefix: "agent_app:dag_lease"

### Security

- [x] Redis URL sanitized in health check, diagnostics, and repr
- [x] Password never exposed in logs, errors, or diagnostics output
- [x] `lease_token` used for holder verification (prevents cross-worker lease theft)

### Tests

- [x] **89 new Phase 16.4 tests** — all passing
- [x] Optional dependency boundary (4 tests)
- [x] FakeRedisClient helper (13 tests)
- [x] Helper functions (8 tests): utcnow, token generation, URL sanitization, JSON roundtrip
- [x] Acquire (8 tests): create, deny, same-holder refresh, steal expired, cannot steal when disabled, after release, JSON fields, TTL
- [x] Renew (6 tests): success, wrong holder, missing key, released, expired, TTL extension
- [x] Release (5 tests): success, wrong token, missing key, other worker isolation, double release
- [x] Get (3 tests): active lease, missing, released
- [x] List expired (3 tests): filtering, empty, released exclusion
- [x] Health (7 tests): healthy, unhealthy, backend_type, timezone-aware, error populated, key_prefix, no password leak
- [x] Diagnostics (5 tests): backend_type, URL sanitized, total keys, key_prefix, TTL, graceful failure
- [x] Config (11 tests): redis parse, key_prefix default, old configs valid, invalid backend, metrics, health, redis extra required, URL default
- [x] Factory (9 tests): create redis, default URL, memory/sqlite/state_store still work, custom prefix, custom TTL, unknown backend
- [x] Protocol conformance (4 tests): isinstance check, required methods, health_check, diagnostics
- [x] Metrics integration (2 tests): wrapped with metrics, denied acquire recorded
- [x] Key prefix isolation (1 test)
- [x] Repr (2 tests): no password leak, shows prefix

### Regression Tests

- [x] Full test suite: **1344 passed, 0 failed, 2 warnings**
- [x] All existing Phase 16.0–16.3 tests pass without modification (except 2 test assertions updated to match new "redis" valid backend)
- [x] No changes to InMemory, SQLite, or StateStore lease backend behavior
- [x] `WorkflowLeaseBackend` protocol now has `@runtime_checkable` decorator (no behavior change)

### Documentation

- [x] `CHANGELOG.md` updated with Phase 16.4 section (Added, Current Limitations)
- [x] `README.md` limitations updated with Phase 16.4 notes
- [x] `README.md` roadmap updated (Phase 16.4 ✅)
- [x] `docs/release_checklist_v0.10.md` Phase 16.4 section added (this file)

### Known Limitations (v0.10.0 + Phase 16.4)

- Redis is an optional dependency — not installed by default
- NOT a distributed lock service — best-effort coordination only
- No exactly-once guarantee — application must remain idempotent
- No worker daemon, queue, or scheduler — lease coordination only
- No Redis Streams / PubSub worker distribution
- No automatic distributed recovery or self-healing
- Redis TTL is the only expiry mechanism — clock skew between workers may cause brief double-claim windows
- Redis unavailability causes lease acquire/renew to fail
- Metrics wrapper requires Phase 16.3 metrics opt-in
- Lua scripts use basic ISO timestamp parsing — not full RFC 3339
- `list_expired_leases` uses SCAN which may miss keys added during iteration (eventual consistency)
- No Redis cluster / sentinel support — single Redis instance only

## Phase 16.5: Recovery Scanner & Manual Recovery

- [x] `RecoveryScanConfig` model (stale_after_seconds, running_after_seconds, include_* flags, limit, tenant_id, workflow_name)
- [x] `RecoveryCandidateReason` enum (10 values: RUNNING_TOO_LONG, RUN_STALE, NODE_INTERRUPTED, NODE_FAILED, LEASE_EXPIRED, LEASE_MISSING, COMPENSATION_INCOMPLETE, SNAPSHOT_AVAILABLE, RESUME_PLAN_AVAILABLE, NOT_RESUMABLE)
- [x] `RecoveryRecommendation` enum (5 values: INSPECT_ONLY, RESUME, WAIT_FOR_ACTIVE_LEASE, MANUAL_REVIEW, DO_NOT_RESUME)
- [x] `RecoveryCandidate` model (run_id, status, reasons, recommendation, lease info, resumable, plan summaries, error)
- [x] `RecoveryScanResult` model (scanned_at, total_scanned, candidate_count, candidates, errors)
- [x] `ManualRecoveryResult` model (run_id, attempted, recovered, status, lease_acquired/released, result, error)
- [x] `RecoveryScanner` class — `scan()` and `inspect_run()` methods; read-only, never modifies state
- [x] Scanner candidate eligibility: failed runs (include_failed), stale/long-running runs (include_running), compensating runs (include_compensating), completed runs (include_completed)
- [x] Scanner lease-aware recommendations: active lease → WAIT_FOR_ACTIVE_LEASE, expired lease → RESUME
- [x] Scanner resumability: uses `build_recovery_plan()` for failed/interrupted runs; stale running runs with no nodes treated as resumable
- [x] `RecoveryService` class — `recover_run()` with lease-protected manual recovery flow
- [x] Recovery flow: inspect → check recommendation → acquire lease → audit.started → resume → audit.completed → release lease
- [x] Lease release on failure (best-effort, never blocks recovery result)
- [x] `AgentApp` recovery APIs: `scan_recovery_candidates()`, `inspect_recovery_candidate()`, `recover_workflow_run()`
- [x] `AgentApp.__init__` extended with `dag_lease_backend` and `audit_logger` parameters
- [x] `build_app()` creates lease backend from config and passes to AgentApp
- [x] CLI recovery commands: `agentapp recovery scan`, `agentapp recovery inspect <run_id>`, `agentapp recovery recover <run_id>`
- [x] CLI scan outputs table (Run ID, Status, Age, Lease, Recommendation) or JSON
- [x] CLI recover exits 0 on success, non-zero on blocked/not_resumable/error
- [x] `list_runs()` extended in InMemoryWorkflowStateStore with filter parameters
- [x] `list_runs()` extended in SQLiteWorkflowStateStore with parameterised SQL + LIMIT
- [x] `AuditEvent` import fixed (moved from TYPE_CHECKING to runtime import)
- [x] Recovery config support in `RuntimeConfig` (`recovery_config` dict field)
- [x] **63 new Phase 16.5 tests** — models (20), scanner (24), service (12), CLI (9), state store list_runs (13)
- [x] Full test suite: 1409 passed, 0 failed (+63 new Phase 16.5 tests)
- [x] CHANGELOG.md updated with Phase 16.5 section (Added, Current Limitations)
- [x] README.md limitations updated with Phase 16.5 notes
- [x] README.md roadmap updated (Phase 16.5 ✅)
- [x] `docs/release_checklist_v0.10.md` Phase 16.5 section added (this file)

### Known Limitations (v0.10.0 + Phase 16.5)

- No automatic recovery daemon or background scheduler
- No Redis Streams / Celery / Temporal integration
- No exactly-once guarantee — lease is best-effort only
- Recovery is operator-triggered only (CLI or API)
- Active lease blocks recovery — operator must wait or manually release
- No bulk/batch recovery — one run at a time
- No UI console for recovery management
- Lease release failure is logged but does not block recovery result

## Phase 17: Automatic Recovery Daemon

- [x] `AutoRecoveryPolicy` Pydantic model with conservative defaults (enabled=False, dry_run=True, max_concurrent=1)
- [x] `RecoveryDaemonTickResult` model (scanned/selected/recovered/skipped/failed counts, run IDs, skip/failure details)
- [x] `RecoveryDaemon` class with `run_once()` and `run_forever()` methods
- [x] `run_once()` — scan → select → recover (dry-run or live) → return tick result
- [x] `run_forever()` — cycles at `policy.interval_seconds` with graceful shutdown via `asyncio.Event`
- [x] `stop()` method signals daemon to stop after current cycle
- [x] Dry-run mode: collects `recovered_ids` but never calls `recover_run()`
- [x] No-dry-run mode: calls `RecoveryService.recover_run()` with semaphore-bounded concurrency
- [x] `_STATUS_TO_SCAN_FLAGS` mapping: statuses → scanner include flags
- [x] `_build_scan_config()` — maps policy statuses to RecoveryScanConfig
- [x] `_should_skip()` — selection logic: only RESUME, skips WAIT/DO_NOT_RESUME/active lease, respects policy flags
- [x] 10+ audit events: daemon_started/stopped/tick_started/completed, candidate_selected/skipped, recovery_started/completed/failed, dry_run_selected
- [x] `AgentApp.create_recovery_daemon()` — factory method, not auto-started
- [x] CLI: `agentapp recovery daemon --once --dry-run/--no-dry-run --interval-seconds --max-recoveries-per-scan --max-concurrent-recoveries --workflow-name --tenant-id`
- [x] CLI graceful shutdown: Ctrl+C handler via `asyncio.Event` + `loop.add_signal_handler`
- [x] 57 new Phase 17 tests — policy (29), daemon (22), CLI daemon (6)
- [x] Full test suite: 1468 passed, 12 pre-existing failures (test_cli.py trace/eval, unrelated)
- [x] CHANGELOG.md updated with Phase 17 section
- [x] README.md updated with Phase 17 notes
- [x] `docs/release_checklist_v0.10.md` Phase 17 section added

### Known Limitations (v0.10.0 + Phase 17)

- Daemon is not auto-started; must be explicitly invoked
- Dry-run is the default — no recovery without --no-dry-run
- No exactly-once guarantee
- No distributed coordination
- No UI console

## Phase 18: Recovery Observability + Admin API

- [x] `RecoverySystemStatus` model — enabled, dry_run, daemon_configured, scanner/recovery_service availability, last_tick, policy
- [x] `AgentApp.get_recovery_system_status()` — returns RecoverySystemStatus
- [x] `AgentApp.run_recovery_scan_once()` — single scan cycle (dry-run by default), returns RecoveryDaemonTickResult
- [x] `AgentApp.recover_run()` — thin wrapper with dry_run=True default; dry-run includes candidate inspection info
- [x] `AgentApp.get_recovery_history()` — queries audit events for a run from audit logger
- [x] `_build_scan_config_from_policy()` — static method mapping AutoRecoveryPolicy → RecoveryScanConfig
- [x] `_should_skip_candidate()` — static method matching RecoveryDaemon skip logic
- [x] CLI `recovery status` — shows enabled/dry_run/configured/policy (--json)
- [x] CLI `recovery history <run_id>` — shows audit events (--json, --limit)
- [x] CLI `recovery scan` enhanced — delegates to `run_recovery_scan_once()` (--dry-run default, --no-dry-run)
- [x] CLI `recovery recover <run_id>` enhanced — delegates to `recover_run()` (--dry-run default, --no-dry-run, --workflow)
- [x] Optional FastAPI admin router: `agent_app/adapters/recovery_admin.py` with lazy-import
  - `GET /admin/recovery/status`
  - `GET /admin/recovery/runs/{run_id}/inspect`
  - `GET /admin/recovery/runs/{run_id}/history`
  - `POST /admin/recovery/scan`
  - `POST /admin/recovery/runs/{run_id}/recover`
- [x] FastAPI admin router denies access by default unless an admin authorization dependency is supplied
- [x] FastAPI admin router logs internal exceptions and returns generic HTTP 500 details
- [x] 46 new/updated Phase 18 tests — includes 3 FastAPI security regression tests
- [x] Full test suite: 152 recovery tests passing, 199 key tests passing
- [x] CHANGELOG.md updated with Phase 18 section
- [x] README.md updated with Phase 18 notes
- [x] `docs/release_checklist_v0.10.md` Phase 18 section added (this file)

### Known Limitations (v0.10.0 + Phase 18)

- FastAPI admin router is optional (lazy-import); requires `pip install 'agent-app-framework[api]'`
- Recovery history limited by audit logger capabilities (InMemoryAuditLogger has `list_events()`)
- No UI console yet (Phase 18 is API surface only)
- Admin API does not auto-start daemon
- All mutating operations default to dry-run
- Recovery is best-effort; lease is not exactly-once guarantee

## Phase 18.5: CLI Trace/Eval Test Baseline Cleanup

- [x] Reproduced baseline failure with `python -m pytest tests/unit/test_cli.py -q`
- [x] Classified 12 failures as one root cause: `python -m agent_app.cli` module did not invoke `main()`
- [x] Confirmed symptom: module invocation returned exit code 0 with empty stdout/stderr for help, eval, trace list, and trace show commands
- [x] Added CLI module entrypoint guard: `if __name__ == "__main__": raise SystemExit(main())`
- [x] Verified `python -m agent_app.cli --help` prints help output
- [x] Verified missing eval file now exits non-zero through subprocess path
- [x] Verified trace list/show subprocess tests now produce expected stdout/JSON/exit codes
- [x] `tests/unit/test_cli.py`: 15 passed
- [x] Recovery regression tests continue to pass
- [x] CHANGELOG.md updated with Phase 18.5 fixed section

### Known Limitations (v0.10.0 + Phase 18.5)

- No new CLI features were added in Phase 18.5
- The fix restores module execution behavior only; it does not change trace/eval command semantics

## Phase 19: Recovery Admin Console

- [x] Verify `tests/unit/test_recovery_ui.py` passes.
- [x] Verify importing `agent_app.adapters.recovery_ui` does not require FastAPI until `create_recovery_ui_router()` is called.
- [x] Verify the Recovery Admin Console denies all routes when `admin_dependency` is omitted.
- [x] Verify UI scans remain dry-run and reject `dry_run=false` attempts.
- [x] Verify live recovery requires confirmation token plus `confirm_no_dry_run=true`.
- [x] Verify `docs/recovery_admin_console.md` documents mounting, safety defaults, best-effort recovery, and current limitations.
