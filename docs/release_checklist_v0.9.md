# v0.9.0 Release Checklist

## Tests

- [x] `python -m pytest` — 495 passed, 5 skipped, 0 failed
- [x] `python scripts/benchmark_tracing.py --runs 100`
- [x] `python scripts/benchmark_tracing.py --runs 1000 --collector noop`
- [x] `python scripts/benchmark_tracing.py --runs 1000 --collector memory`
- [x] `python scripts/benchmark_tracing.py --runs 1000 --collector jsonl --path .agent_app/bench_traces.jsonl`

## Observability

- [x] InMemoryTraceCollector retention (`max_traces`, `max_events_per_trace`) verified
- [x] JSONLTraceCollector `count_events()` verified
- [x] JSONLTraceCollector `count_traces()` verified
- [x] JSONLTraceCollector `compact()` verified (atomic in-place + separate output_path)
- [x] OpenTelemetry bridge missing dependency error verified
- [x] `docs/observability.md` reviewed and consistent

## Package

- [x] `pip install -e ".[dev]"` works
- [x] `pip install -e ".[otel]"` works (optional extra)
- [x] `import agent_app` works without otel extra
- [x] OpenTelemetry stub imports without opentelemetry installed
- [x] Optional otel dependency behavior verified
- [x] No OpenTelemetry in base `dependencies`
- [x] Benchmark script has no extra production dependencies

## Documentation

- [x] README observability section reviewed
- [x] CHANGELOG 0.9.0 reviewed
- [x] Current limitations documented (Tier 2 fire-and-forget, no drain/flush, no dashboard, no OTLP, OTel experimental, benchmark rough)
- [x] Phase 13 suggestions documented

## Version Consistency

- [x] `pyproject.toml` version = `0.9.0`
- [x] `CHANGELOG.md` latest section = `## 0.9.0`
- [x] README references consistent with v0.9.0 features
- [x] `docs/observability.md` aligned with v0.9.0 features

## Known Limitations (v0.9.0)

- Tier 2 events (workflow, tool, approval, run_state) are fire-and-forget
- No drain/flush API — intentionally deferred
- No visual dashboard — trace viewing via CLI, API, or JSONL
- No OpenTelemetry OTLP export — bridge is experimental stub only
- Benchmark is a rough measurement, not a rigorous performance test
- InMemoryTraceCollector is per-process only — use JSONL for persistence
- JSONL retention limits (`max_traces`, `max_events_per_trace`) apply to InMemoryTraceCollector only

## Phase 13.1: DAG Workflow Engine (post-v0.9.0)

- [x] `agent_app/workflows/dag.py` — DAG models, executor, errors
- [x] `agent_app/workflows/__init__.py` — module exports
- [x] `Workflow.dag()` factory implemented (was NotImplementedError)
- [x] `WorkflowExecutor._run_dag()` dispatches to DagExecutor
- [x] Config loader parses DAG YAML (`type: dag`, `nodes:` list)
- [x] `AppRunResult.node_results` field added
- [x] Cycle detection via Kahn's algorithm
- [x] Topological sort with multi-dependency and diamond support
- [x] Sequential execution with upstream output propagation
- [x] Node failure stops DAG; node interruption stops DAG
- [x] Tool governance (permissions, approval) works in DAG
- [x] Agent execution via AppRunner in DAG
- [x] EvalRunner supports DAG workflow cases
- [x] 30 new DAG tests all passing
- [x] customer_support DAG example (`refund_dag` workflow)
- [x] customer_support DAG eval suite
- [x] Original 525 tests still passing (0 regressions)

## Phase 13.2: DAG Concurrency, Retry, Status Enhancement

- [x] `DagExecutionMode` enum (sequential/parallel)
- [x] `RetryPolicy` model with max_attempts, backoff, multiplier
- [x] `NodeExecutionAttempt` model for per-attempt recording
- [x] `DagWorkflow` extended with execution_mode, max_concurrency, retry
- [x] `DagNode` extended with optional retry field
- [x] `NodeExecutionResult` extended with attempts list
- [x] `DagExecutor._execute_parallel()` — ready-queue + asyncio.gather + semaphore
- [x] `DagExecutor._execute_sequential()` — backward-compatible sequential path
- [x] `DagExecutor._execute_node_with_retry()` — retry loop with backoff
- [x] `DagExecutor._mark_downstream_skipped()` — cascading skip after failure/interruption
- [x] Status propagation: failed→failed, interrupted→interrupted, completed only if no worse status
- [x] `Workflow.dag()` accepts execution_mode, max_concurrency, retry params
- [x] Config loader passes new DAG fields from YAML
- [x] max_concurrency validation (must be >= 1)
- [x] retry_on_statuses validation (must not include interrupted)
- [x] 30 new Phase 13.2 tests (parallel, retry, status propagation)
- [x] 60 total DAG tests passing
- [x] 555 total tests passing, 0 regressions
- [x] customer_support parallel DAG example (`refund_parallel_dag`)
- [x] `customer.lookup` tool added to example
- [x] Parallel DAG eval suite (`customer_support_parallel_dag.yaml`)
- [x] DAG benchmark (`benchmarks/bench_dag.py`)
- [x] Original customer_support eval still passes (4/4)
- [x] DAG eval still passes (1/1)
- [x] Parallel DAG eval passes (1/1)

## Phase 13.3: DAG Condition DSL + Node Timeout

- [x] `agent_app/workflows/condition.py` — safe expression evaluator, DagCondition model
- [x] `DagCondition` model added to `agent_app/workflows/dag.py` (imported from condition.py)
- [x] `DagNode.condition` field (optional `DagCondition`)
- [x] `DagNode.timeout_seconds` field (optional float, >= 0)
- [x] `DagWorkflow.timeout_seconds` field (optional float, >= 0)
- [x] `DagWorkflow.get_effective_timeout()` — node overrides workflow
- [x] `DagExecutor._should_skip_node()` — upstream interrupted/failed/skipped priority
- [x] `DagExecutor._evaluate_condition()` — async condition check with event recording
- [x] `DagExecutor._record_node_event()` — unified node lifecycle event helper
- [x] Condition false → SKIPPED (reason: condition_false)
- [x] Condition error → FAILED (error type: condition_error)
- [x] Condition evaluation events recorded (`node.condition_evaluated`)
- [x] Timeout via `asyncio.wait_for` wraps node execution
- [x] Timeout → status=failed, error.type=timeout
- [x] Timeout triggers retry when retry policy allows
- [x] Timeout event recorded (`node.timeout`)
- [x] Config loader passes `timeout_seconds` from YAML
- [x] Config loader passes node `condition` from YAML
- [x] Old DAG config remains valid (no condition/timeout)
- [x] Parallel DAG config remains valid
- [x] 41 new Phase 13.3 tests (condition evaluator, condition execution, timeout, timeout+retry, config)
- [x] 101 total DAG tests passing
- [x] 596 total tests passing, 0 regressions
- [x] Conditional DAG example (`refund_conditional_dag`) in customer_support
- [x] Conditional DAG eval (`customer_support_conditional_dag.yaml`) — 2/2 passed
- [x] `order.query` tool updated to extract order_id from input text
- [x] DAG benchmark extended with conditional and timeout configurations
- [x] Original customer_support eval still passes (4/4)
- [x] DAG eval still passes (1/1)
- [x] Parallel DAG eval passes (1/1)
- [x] Conditional DAG eval passes (2/2)

## Known Limitations (post-Phase 13.3)

- Local asyncio concurrency only — not distributed DAG execution
- No Temporal / Celery backend
- retry not applied to interrupted (approval) nodes
- No compensation / rollback
- FUNCTION node still stub
- Condition DSL is safe subset — no arbitrary Python expressions, no function calls
- Timeout is node-level execution timeout — not a full workflow deadline
- No visual DAG editor
