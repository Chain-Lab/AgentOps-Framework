# DAG Execution Snapshots (Phase 16.0)

DAG execution snapshots are lightweight recovery points that capture the
execution state of a DAG workflow run at key state transitions.  They are
designed to accelerate recovery after process exit, lease expiry, or
infrastructure failure.

## What Snapshots Are

Snapshots are **recovery aids** — they provide a stable point from which to
resume a DAG run, skipping already-completed nodes.  They do **not** provide
exactly-once execution guarantees and are **not** a distributed transaction log.

Key properties:

- **Lightweight** — written at node-level state transitions, not per-token
- **Survive lease expiry** — separate from the lease table; snapshots persist
  even when the lease has been lost
- **Best-effort** — intermediate snapshot failures are logged but do not block
  execution; initial/final snapshot failures raise stable errors
- **Explicit resume** — no automatic recovery daemon; user calls
  `app.resume_workflow_run()` to resume from a snapshot

## What Snapshots Are NOT

- NOT a distributed transaction log (no Celery, Temporal, Redis, etcd)
- NOT an exactly-once execution mechanism
- NOT a replacement for lease renewal or business-level idempotency
- NOT an automatic recovery daemon
- NOT per-stream-delta or per-token persistence

## Configuration

Snapshots are configured via `runtime.dag_snapshot` in `agentapp.yaml`:

```yaml
runtime:
  dag_snapshot:
    enabled: true          # master switch (default: true)
    store: memory          # "memory" or "sqlite" (default: "memory")
    path: .agent_app/snapshots.db  # SQLite path (required when store=sqlite)
    save_on_node_start: true       # snapshot when node begins (default: true)
    save_on_node_complete: true    # snapshot when node completes (default: true)
    save_on_interrupt: true        # snapshot on interrupt (default: true)
    save_on_failure: true          # snapshot on failure (default: true)
```

Flat config is also supported:

```yaml
runtime:
  dag_snapshot_config:
    enabled: true
    store: sqlite
    path: .agent_app/snapshots.db
```

## Snapshot Lifecycle

### During `execute()`

1. **Initial snapshot** — saved as "running" after lease acquire, before any
   node execution
2. **Node-level snapshots** — saved after each node completes or fails
   (controlled by `save_on_node_start`, `save_on_node_complete`,
   `save_on_failure`)
3. **Completion snapshot** — saved as "completed" on successful finish
4. **Failure snapshot** — saved as "failed" in the `finally` block if an
   unhandled exception propagates

### During `resume()`

1. **Load latest snapshot** — `get_latest_run_snapshot(run_id)` reads the most
   recent snapshot
2. **Validate** — checks schema_version (only v1 supported), run_id match,
   and resumability
3. **Idempotent return** — if snapshot status is "completed", returns empty
   result immediately (no re-execution)
4. **Fall through** — if snapshot is "running"/"partial"/"failed"/"interrupted",
   falls through to existing resume logic with persisted node states
5. **Graceful degradation** — if snapshot is corrupted or has unsupported
   version, falls through to existing resume logic (snapshot errors are caught
   and logged)

## Snapshot Status Values

| Status | Resumable | Description |
|--------|-----------|-------------|
| `running` | Yes | Workflow is currently executing |
| `completed` | No | Workflow completed successfully (idempotent return) |
| `failed` | Yes | Workflow or node failed; can resume |
| `partial` | Yes | Some nodes completed, some failed |
| `interrupted` | Yes | Execution was interrupted (e.g., approval wait) |

## Data Models

### DagRunSnapshot

```python
class DagRunSnapshot(BaseModel):
    snapshot_id: str              # unique identifier
    run_id: str                   # workflow run identifier
    workflow_name: str | None     # workflow name
    status: str                   # DagSnapshotStatus value
    schema_version: int           # currently 1
    completed_node_ids: list[str]
    failed_node_ids: list[str]
    current_node_ids: list[str]
    pending_node_ids: list[str]
    nodes: dict[str, DagNodeSnapshot]  # per-node state
    execution_context: dict[str, Any]
    pending_approvals: list[dict[str, Any]]
    compensation_state: dict[str, Any] | None
    created_at: datetime          # timezone-aware UTC
    updated_at: datetime          # timezone-aware UTC
```

### DagNodeSnapshot

```python
class DagNodeSnapshot(BaseModel):
    node_id: str
    status: str                   # "completed", "failed", "running", etc.
    attempts: int                 # number of execution attempts
    output: Any | None
    error: dict[str, Any] | None  # {"type": "...", "message": "..."}
    started_at: datetime | None
    completed_at: datetime | None
```

## Store Interface

### InMemoryWorkflowStateStore

Snapshots stored in `_snapshots: dict[str, list[DagRunSnapshot]]` keyed by
`run_id`.  Overwrite by `snapshot_id` supported.

### SQLiteWorkflowStateStore

Table schema:

```sql
CREATE TABLE IF NOT EXISTS dag_run_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    workflow_name TEXT,
    status TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dag_run_snapshots_run_updated
    ON dag_run_snapshots(run_id, updated_at);
```

## Error Handling

| Error | When | Behavior |
|-------|------|----------|
| `SnapshotWriteError` | Initial/final snapshot write fails | Raised to caller (blocks execution) |
| `SnapshotCorruptionError` | Snapshot JSON is invalid | Caught; falls through to existing resume |
| `SnapshotUnsupportedVersionError` | schema_version != 1 | Raised as DagError |

## API Usage

```python
from agent_app import AgentApp

app = AgentApp(...)

# Execute with snapshots (automatic when state_store is configured)
result = app.run(workflow="refund_dag", input="refund request")

# Resume from latest snapshot
result = app.resume_workflow_run(
    workflow="refund_dag",
    run_id="run-abc123",
)
```

## Limitations

- Snapshots are recovery aids, not exactly-once guarantees
- No automatic recovery daemon — resume is explicit
- SQLite store uses stdlib `sqlite3` — no connection pooling or WAL mode
- Schema version migration is manual (only v1 supported)
- Intermediate snapshots are best-effort (failure logged, not blocking)
- Snapshot persistence adds I/O overhead proportional to frequency
