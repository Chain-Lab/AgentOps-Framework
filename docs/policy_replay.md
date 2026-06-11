# Policy Replay & Regression Dashboard — Phases 27 & 28

> **Status:** Implemented

## Overview

**Phase 27** adds a lightweight policy replay and regression analysis capability
built on top of Phase 25's policy decision store and Phase 26's Policy Console.

**Phase 28** upgrades the system with persistent storage, background job execution,
and enhanced context reconstruction.

The goal is to help developers answer:

1. What would happen if the current policy config were applied to past decisions?
2. Which decisions would change?
3. Which rules are unstable or high-impact?
4. Can we detect policy regressions before deploying policy changes?

## Architecture

```
PolicyReplayRunner
  ├── Input: PolicyDecisionStore (historical decisions)
  ├── Input: PolicyEngine (current rules)
  ├── Output: PolicyReplayResult (changes + summary)
  └── Persistence: PolicyReplayStore (in-memory)

CLI: agentapp policy replay --config <path> [filters]
Console: GET /policy-console/replays
         GET /policy-console/replays/{replay_id}
```

## Replay Models

| Model | Purpose |
|-------|---------|
| `PolicyReplayStatus` | `completed` or `failed` |
| `PolicyReplayDecisionChange` | Per-decision change record (original vs replayed) |
| `PolicyReplayRun` | Replay run summary (counts, timestamps, metadata) |
| `PolicyReplayResult` | Full result: run summary + list of changes |

## Replay Runner

`PolicyReplayRunner` is the core service:

1. Queries historical decisions from the store (with optional filters)
2. Reconstructs a `PolicyEvaluationContext` from each trace's `context_summary`
3. Re-evaluates using the current policy engine
4. Compares original action vs replayed action
5. Produces a `PolicyReplayResult`
6. Optionally persists to a `PolicyReplayStore`

**Missing context handling:** If a decision record lacks `tool_name` in its
context, the replay is marked as `failed` with a clear reason rather than guessing.

## CLI

```bash
agentapp policy replay --config examples/customer_support/agentapp.yaml
```

With filters:

```bash
agentapp policy replay \
  --config examples/customer_support/agentapp.yaml \
  --tenant-id eval_tenant \
  --tool-name refund.request \
  --limit 100
```

Output:

```
Policy replay completed

Replay ID:     replay_abc123...
Source decisions: 100
Changed:       4
Unchanged:     96
Failed:        0
```

With `--json`:

```json
{
  "replay_id": "replay_abc123...",
  "status": "completed",
  "source_decision_count": 100,
  "changed_count": 4,
  "unchanged_count": 96,
  "failed_count": 0,
  "changes": [...]
}
```

Exit code: 0 on success, non-zero on error.

## Console Pages

### Replay Index (`GET /policy-console/replays`)

| Column | Description |
|--------|-------------|
| Replay ID | Link to detail |
| Status | `completed` or `failed` badge |
| Created | Timestamp |
| Source | Total decisions replayed |
| Changed | Decisions with different action (highlighted) |
| Unchanged | Decisions with same action |
| Failed | Unreplayable decisions (highlighted) |

### Replay Detail (`GET /policy-console/replays/{replay_id}`)

- Summary cards: Source, Changed, Unchanged, Failed
- Changed decisions table with original → replayed action, rule names
- Failed replays table (if any)
- Links back to original decision detail pages
- Success message when no regressions detected

## Store

`InMemoryPolicyReplayStore` — in-memory, supports save/get/list.

SQLite persistence can be added later if needed.

## Limitations (Phase 27)

1. **Replay quality depends on context availability.** Historical decisions
   must have enough `context_summary` data (tool_name, agent_name, etc.)
   to reconstruct a `PolicyEvaluationContext`. Missing data → failed replay.

2. **No background replay jobs.** Replay runs synchronously in the CLI.

3. **No multi-tenant admin console.** Single-tenant in-memory store.

4. **No policy editing UI.** Replay is read-only analysis.

5. **Permissions not fully reconstructed.** The replay context uses empty
   permissions by default. Rules that check `permissions` or `missing_permissions`
   may produce different results than the original evaluation.

## Security Notes

- Console remains disabled by default
- No built-in authentication (documented)
- Replay data is read-only analysis
- All console output is Jinja2-escaped

## Requirements

- `jinja2>=3.0` (for console pages)
- Existing Phase 25 policy decision store
- Existing Phase 23 policy engine

---

# Phase 28: Persistent Policy Replay, Background Jobs, Context Reconstruction

> **Status:** Implemented

## Overview

Phase 28 upgrades policy replay from a synchronous, in-memory analysis tool
to a persistent, background-executable governance infrastructure.

## New Architecture

```
PolicyReplayRunner (with PolicyReplayContextBuilder)
  ├── Input: PolicyDecisionStore (historical decisions)
  ├── Input: PolicyEngine (current rules)
  ├── Input: PolicyReplayContextBuilder (rich context reconstruction)
  ├── Output: PolicyReplayResult (changes + summary + context metadata)
  └── Persistence: PolicyReplayStore (memory or SQLite)

PolicyReplayBackgroundRunner
  ├── Input: PolicyReplayRunner
  ├── Input: PolicyReplayJobStore (memory or SQLite)
  └── Output: PolicyReplayJob (queued → running → completed/failed)

CLI: agentapp policy replay --config <path> [filters] [--background]
     agentapp policy run-job <job_id> --config <path>
     agentapp policy jobs --config <path>
Console: GET /policy-console/replays
         GET /policy-console/replays/{replay_id}
         GET /policy-console/replay-jobs       (Phase 28)
         GET /policy-console/replay-jobs/{job_id} (Phase 28)
```

## SQLitePolicyReplayStore

`SQLitePolicyReplayStore` provides persistent replay result storage:

```python
from agent_app.runtime.policy_replay_store import SQLitePolicyReplayStore

store = SQLitePolicyReplayStore(db_path=".agent_app/policy_replays.db")
```

### Tables

- `policy_replay_runs` — replay run summaries
- `policy_replay_changes` — per-decision change records

### Features

- Auto-creates parent directories
- Auto-creates tables on init
- `list_changes()` with `changed_only` and `failed_only` filters
- Timezone-aware ISO datetime serialization
- Cross-instance persistence

### Factory

```python
from agent_app.runtime.policy_replay_store import create_replay_store

store = create_replay_store(store_type="sqlite", db_path=".agent_app/policy_replays.db")
# or: store = create_replay_store(store_type="memory")
```

## PolicyReplayJob

Replay jobs can be queued and executed later:

```python
from agent_app.runtime.policy_replay_jobs import (
    PolicyReplayJob,
    PolicyReplayJobStatus,
    PolicyReplayJobStore,
    SQLitePolicyReplayJobStore,
    create_replay_job_store,
)
```

### Job Lifecycle

```
QUEUED → RUNNING → COMPLETED
               → FAILED
               → CANCELLED
```

### Job Store Factory

```python
store = create_replay_job_store(store_type="sqlite", db_path=".agent_app/policy_replay_jobs.db")
```

## PolicyReplayContextBuilder

Enhanced context reconstruction from decision records:

```python
from agent_app.governance.policy_replay_context import (
    PolicyReplayContext,
    PolicyReplayContextBuilder,
)

builder = PolicyReplayContextBuilder()

# Build replay context
ctx = builder.build(trace)
# → PolicyReplayContext with missing_fields tracking

# Build evaluation context (for policy engine)
eval_ctx = builder.build_evaluation_context(trace)
# → PolicyEvaluationContext or None if tool_name missing
```

### Reconstruction Priority

1. Structured fields on the decision record (tool_name)
2. Trace metadata (`context_summary` dict)
3. Missing fields are tracked explicitly — never guessed

### Missing Field Behavior

- `tool_name` missing → replay fails (required for evaluation)
- `permissions` missing → empty list, recorded in `missing_fields`
- `user_id`/`tenant_id` missing → None, recorded in `missing_fields`

## PolicyReplayBackgroundRunner

Lightweight background execution without external task queues:

```python
from agent_app.runtime.policy_replay_background import PolicyReplayBackgroundRunner

runner = PolicyReplayBackgroundRunner(
    replay_runner=replay_runner,
    job_store=job_store,
    replay_store=replay_store,
)

# Submit a job
job = await runner.submit(limit=50, tenant_id="t1", requested_by="admin")

# Execute a job
completed = await runner.run_job(job.job_id)
```

## CLI Commands (Phase 28)

### Synchronous Replay (unchanged)

```bash
agentapp policy replay --config examples/customer_support/agentapp.yaml
```

### Background Submit

```bash
agentapp policy replay --config examples/customer_support/agentapp.yaml --background
```

Output:
```
Policy replay job queued

Job ID:       job_abc123...
Status:       queued
Requested by: admin

Run with: agentapp policy run-job job_abc123... --config examples/customer_support/agentapp.yaml
```

With `--json`:
```json
{
  "job_id": "job_abc123...",
  "status": "queued",
  "limit": 100,
  "tenant_id": null,
  "tool_name": null,
  "rule_id": null,
  "requested_by": "admin",
  "created_at": "2024-01-15T10:30:00.000000"
}
```

### Run a Job

```bash
agentapp policy run-job job_abc123... --config examples/customer_support/agentapp.yaml
```

Output:
```
Policy replay job completed

Job ID:       job_abc123...
Replay ID:    replay_...
Status:       completed
```

### List Jobs

```bash
agentapp policy jobs --config examples/customer_support/agentapp.yaml
```

Output:
```
Job ID                Status     Replay ID              Tenant           Created
-------------------------------------------------------------------------------------
job_abc123...         queued     —                      tenant_a         2024-01-15T10:30
```

### CLI Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--store` | Replay store type (`memory` or `sqlite`) | `memory` |
| `--db-path` | SQLite database path | `.agent_app/policy_replays.db` |
| `--background` | Submit as background job | `false` |
| `--requested-by` | Who requested the replay | `anonymous` |

## Console Pages (Phase 28)

### Replay Jobs Index

`GET /policy-console/replay-jobs`

Shows all replay jobs with status, replay ID, tenant, tool, and requester.

### Replay Job Detail

`GET /policy-console/replay-jobs/{job_id}`

Shows job details including status, associated replay, error (if failed),
and timestamps.

### Replay Detail Enhancement

`/policy-console/replays/{replay_id}` now shows:

- Context reconstruction summary
- Missing fields per decision
- Permissions used during replay
- Tool arguments used during replay

## Configuration

```yaml
governance:
  policy_replay:
    store:
      type: sqlite          # "memory" (default) or "sqlite"
      path: .agent_app/policy_replays.db

    jobs:
      store:
        type: sqlite        # "memory" (default) or "sqlite"
        path: .agent_app/policy_replay_jobs.db

    context_reconstruction:
      include_trace_metadata: true
      fail_on_missing_required_context: false
```

## Limitations (Phase 28)

1. **Replay quality depends on context availability.** Same as Phase 27.
2. **No background daemon.** Jobs must be run explicitly via `run-job` or console.
3. **No multi-tenant admin console.** Single-tenant in-memory store.
4. **Permissions not fully reconstructed.** Empty permissions recorded as missing.
5. **No policy editing UI.** Replay is read-only analysis.

## Requirements

- `jinja2>=3.0` (for console pages)
- Phase 27 policy replay infrastructure
- Phase 25 policy decision store
- Phase 23 policy engine
1. **Replay quality depends on context availability.** Historical decisions
   must have enough `context_summary` data (tool_name, agent_name, etc.)
   to reconstruct a `PolicyEvaluationContext`. Missing data → failed replay.

2. **No background replay jobs.** Replay runs synchronously in the CLI.

3. **No multi-tenant admin console.** Single-tenant in-memory store.

4. **No policy editing UI.** Replay is read-only analysis.

5. **Permissions not fully reconstructed.** The replay context uses empty
   permissions by default. Rules that check `permissions` or `missing_permissions`
   may produce different results than the original evaluation.

## Security Notes

- Console remains disabled by default
- No built-in authentication (documented)
- Replay data is read-only analysis
- All console output is Jinja2-escaped

## Requirements

- `jinja2>=3.0` (for console pages)
- Existing Phase 25 policy decision store
- Existing Phase 23 policy engine
