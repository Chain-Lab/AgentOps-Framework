# Policy Decision Store & Reporting — Phase 25

> **Status:** Implemented

## Overview

Phase 25 upgrades policy diagnostics from "visible at runtime" to
"persistent, queryable, statistical, exportable".  Policy decision
traces are now recorded to a pluggable store, queryable via API and
CLI, and aggregatable into reports.

## Architecture

```
PolicyDecisionStore (Protocol)
  ├── InMemoryPolicyDecisionStore  — testing, dev
  └── SQLitePolicyDecisionStore    — production persistence
        └── policy_decisions table
              ├── 5 indexes (run_id, tenant_id, rule_name, action, created_at)
              └── JSON columns for matched_conditions / context_summary

PolicyReportingService
  ├── generate_report()  → PolicyReport (action/rule/tool breakdown)
  ├── export_jsonl()     → JSON Lines file
  └── export_csv()        → CSV file
```

## Configuration

Add to `governance` in `agentapp.yaml`:

```yaml
governance:
  policies:
    enabled: true
    # ... rules ...
  policy_decisions:
    type: sqlite          # memory | sqlite
    path: .agent_app/policy_decisions.db  # sqlite only
```

When `type` is `memory` (default), traces are kept in-process only.
When `type` is `sqlite`, traces persist across restarts.

## PolicyDecisionTrace Model

Every policy evaluation produces a `PolicyDecisionTrace`:

| Field | Type | Description |
|-------|------|-------------|
| `decision_id` | `str` | Unique ID (UUID) |
| `run_id` | `str \| None` | Associated run ID |
| `rule_name` | `str \| None` | Matched rule name (None = default) |
| `action` | `PolicyAction` | allow / deny / require_approval / audit_only |
| `reason` | `str \| None` | Human-readable explanation |
| `tool_name` | `str \| None` | Tool that was evaluated |
| `matched_conditions` | `dict` | Conditions from the rule that matched |
| `context_summary` | `dict` | Safe summary of evaluation context |
| `created_at` | `datetime` | UTC timestamp |

## FastAPI Endpoints

All endpoints require the policy decision store to be configured.

### `GET /policy-decisions`

List policy decisions with filtering and pagination.

**Query parameters:**
- `run_id` — filter by run ID
- `tenant_id` — filter by tenant
- `agent_name` — filter by agent name
- `tool_name` — filter by tool name
- `rule_name` — filter by matched rule name
- `action` — filter by action string
- `limit` — max results (default 50)
- `offset` — skip results (default 0)

Returns list of decision dicts sorted by `created_at` descending.

### `GET /policy-decisions/{decision_id}`

Get a single decision by ID. Returns 404-style dict if not found.

### `GET /policy-report`

Generate aggregated report.

**Query parameters:** Same filters as `/policy-decisions` plus `limit` (default 1000).

Returns:
```json
{
  "total_decisions": 42,
  "action_breakdown": {"allow": 30, "deny": 5, "require_approval": 7},
  "rule_breakdown": {"refund_requires_approval": 7, "deny_dangerous": 5, ...},
  "tool_breakdown": {"refund.request": 7, "dangerous.delete": 5, ...},
  "time_range": {"start": "2024-01-01T00:00:00Z", "end": "2024-01-03T00:00:00Z"}
}
```

## CLI Commands

### `agentapp policy decisions`

Query policy decisions:

```bash
agentapp policy decisions --config agentapp.yaml --tenant-id acme --limit 20
agentapp policy decisions --config agentapp.yaml --tool-name refund.request --json
```

### `agentapp policy report`

Generate aggregated report:

```bash
agentapp policy report --config agentapp.yaml
agentapp policy report --config agentapp.yaml --tenant-id acme --json
```

### `agentapp policy export`

Export decisions to file:

```bash
agentapp policy export --config agentapp.yaml --format jsonl --output report.jsonl
agentapp policy export --config agentapp.yaml --format csv --output report.csv
```

## Eval Runner Integration

When a policy decision store is configured, policy eval cases can
assert on stored decisions:

```yaml
expect:
  policy_decisions:
    - action: deny
      rule_name: deny_dangerous_tools
```

## Customer Support Example

The `examples/customer_support/agentapp.yaml` includes:

```yaml
governance:
  policy_decisions:
    type: sqlite
    path: .agent_app/policy_decisions.db
```

This persists all policy decisions for the customer support workflow,
enabling the admin console to show policy analytics.

## SQLite Schema

```sql
CREATE TABLE policy_decisions (
    decision_id            TEXT PRIMARY KEY,
    run_id                 TEXT,
    tenant_id              TEXT,
    user_id                TEXT,
    agent_name             TEXT,
    tool_name              TEXT,
    workflow_type          TEXT,
    target_agent           TEXT,
    rule_name              TEXT,
    action                 TEXT NOT NULL,
    reason                 TEXT,
    matched_conditions_json TEXT NOT NULL,
    context_summary_json   TEXT NOT NULL,
    created_at             TEXT NOT NULL
);

-- Indexes for common query patterns
CREATE INDEX idx_policy_decisions_run_id ON policy_decisions(run_id);
CREATE INDEX idx_policy_decisions_tenant_id ON policy_decisions(tenant_id);
CREATE INDEX idx_policy_decisions_rule_name ON policy_decisions(rule_name);
CREATE INDEX idx_policy_decisions_action ON policy_decisions(action);
CREATE INDEX idx_policy_decisions_created_at ON policy_decisions(created_at);
```

## Design Decisions

- **Protocol-based store**: `PolicyDecisionStore` is a structural
  subtyping protocol — any object with `record/get/query/count` methods
  satisfies it.
- **tool_name optional**: `tool_name` is `str | None` in both the model
  and the DB, since explain() from DefaultPolicyEngine may not have a
  specific tool context.
- **No ORM**: Uses stdlib `sqlite3` directly, consistent with architecture
  boundaries.
- **Fire-and-forget recording**: Policy store failures are caught and
  ignored so they never block execution.
- **explain() for trace construction**: Uses the policy engine's
  `explain()` method to build rich traces with matched conditions and
  context summaries.
