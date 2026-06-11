# Policy Replay & Regression Dashboard — Phase 27

> **Status:** Implemented

## Overview

Phase 27 adds a lightweight policy replay and regression analysis capability
built on top of Phase 25's policy decision store and Phase 26's Policy Console.

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

## Limitations

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
