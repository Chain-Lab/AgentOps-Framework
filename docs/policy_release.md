# Policy Release Gates & Versioned Policy Bundles

> **Phase 29:** Implemented — Versioned bundles, gate evaluation, release service
> **Phase 30:** Implemented — Promotion approval, RBAC, console write governance

## Overview

**Phase 29** introduces a policy release safety gate system that upgrades the
Phase 27/28 policy replay system into a controlled release workflow. It provides:

1. **Versioned Policy Bundles** — Snapshots of policy configuration with lifecycle
   management (DRAFT → ACTIVE → ARCHIVED → ROLLED_BACK)
2. **Policy Gate Evaluation** — Configurable threshold rules that evaluate replay
   results before promotion
3. **Policy Release Service** — Orchestrates bundle creation, gate evaluation,
   promote, and rollback
4. **CLI Commands** — Full lifecycle management via `agentapp policy bundle` and
   `agentapp policy gate` subcommands
5. **Console Pages** — Read-only HTML pages for bundle and gate result visibility
6. **Persistent Storage** — SQLite-backed stores survive CLI process restarts

The goal is to help teams answer:

1. Can we safely promote a new policy configuration?
2. What changed between policy versions?
3. Did the gate evaluation pass before promoting?
4. How do we rollback if something goes wrong?

## Architecture

```
PolicyReleaseService
  ├── Input: PolicyBundleStore (versioned bundles)
  ├── Input: PolicyReplayRunner (re-evaluation)
  ├── Input: PolicyGateEvaluator (threshold rules)
  ├── Input: PolicyGateStore (gate results)
  └── Output: Active bundle lifecycle

CLI: agentapp policy bundle create/list/active/promote/rollback
CLI: agentapp policy gate run/list
Console: GET /policy-console/bundles
         GET /policy-console/bundles/{bundle_id}
         GET /policy-console/gates
         GET /policy-console/gates/{gate_result_id}
```

## Bundle Models

| Model | Purpose |
|-------|---------|
| `PolicyBundleStatus` | `draft`, `active`, `archived`, `rolled_back` |
| `PolicyBundle` | Versioned policy config with SHA-256 hash, lifecycle, metadata |

**Bundle lifecycle:**
- **DRAFT** — Created but not yet evaluated or promoted
- **ACTIVE** — Currently promoted and serving policy decisions
- **ARCHIVED** — Previously active, superseded by a newer bundle
- **ROLLED_BACK** — Was active, then rolled back to a previous bundle

## Gate Models

| Model | Purpose |
|-------|---------|
| `PolicyGateRule` | Configurable threshold: `max_changed_decisions`, `max_changed_ratio`, `max_failed_replays`, `max_new_denies`, `fail_on_missing_required_context` |
| `PolicyGateStatus` | `passed`, `warning`, `failed` |
| `PolicyGateResult` | Evaluation outcome: pass/fail, per-rule results, counts |

## Stores

Both stores follow the Protocol + InMemory + SQLite pattern:

| Store | Protocol Methods | Persistence |
|-------|-----------------|-------------|
| `PolicyBundleStore` | `create()`, `get()`, `list()`, `get_active()`, `activate()`, `archive()` | InMemory / SQLite |
| `PolicyGateStore` | `save()`, `get()`, `list(bundle_id=?)` | InMemory / SQLite |

**SQLite persistence:** Stores survive CLI subprocess boundaries, enabling
cross-command workflows (create → gate run → promote across separate invocations).

## PolicyReleaseService

Core orchestrator with four operations:

### `create_bundle(name, version, config_path, ...)`
Reads config content, computes SHA-256 hash, creates a DRAFT bundle.

### `run_gate(bundle_id, limit, ...)`
Executes a replay against the bundle, evaluates against gate rules, stores result.
Returns `PolicyGateResult`.

### `promote(bundle_id, require_passing_gate=True)`
Activates the bundle (archives any previously ACTIVE bundle). By default requires
a passing gate result. Raises `ValueError` if latest gate failed.

### `rollback(target_bundle_id)`
Re-activates a previous bundle, archiving the current ACTIVE bundle.

## Config Schema

```yaml
governance:
  policy_release:
    bundles:
      type: sqlite          # "memory" or "sqlite"
      path: .agent_app/policy_bundles.db
    gates:
      type: sqlite
      path: .agent_app/policy_gates.db
    rules:
      - name: safe_default
        max_changed_ratio: 0.10
        max_failed_replays: 0
```

## CLI Commands

```bash
# Bundle management
agentapp policy bundle create --config <path> --name <name> --version <ver> [--config-path <path>] [--description <desc>] [--created-by <who>]
agentapp policy bundle list --config <path>
agentapp policy bundle active --config <path>
agentapp policy bundle promote --config <path> --bundle-id <id>
agentapp policy bundle rollback --config <path> --bundle-id <id>

# Gate management
agentapp policy gate run --config <path> --bundle-id <id> [--limit <n>] [--tenant-id <id>] [--tool-name <name>]
agentapp policy gate list --config <path> [--bundle-id <id>]
```

## Console Pages

| Route | Template | Description |
|-------|----------|-------------|
| `GET /bundles` | `bundles.html` | List all bundles with status badges |
| `GET /bundles/{bundle_id}` | `bundle_detail.html` | Full bundle details including hash, timestamps, metadata |
| `GET /gates` | `gates.html` | List all gate results with pass/fail indicators |
| `GET /gates/{gate_result_id}` | `gate_detail.html` | Gate result details including per-rule outcomes |

All pages are read-only and only mount when `policy_console.enabled` is set.

## Design Decisions

1. **Separate stores for bundles and gates** — Bundles are versioned snapshots;
   gates are evaluation results. Separate stores enable independent lifecycle
   and query patterns.

2. **SHA-256 config hashing** — Stable hash of JSON-canonicalized config content
   enables change detection between versions.

3. **Gate-before-promote** — Promotion requires a passing gate by default,
   preventing untested policy changes from going live.

4. **SQLite default for CLI** — CLI tests use SQLite stores because in-memory
   stores don't persist across subprocess invocations.

5. **Console integration via release service** — Console pages access stores
   through `PolicyReleaseService.bundle_store` and `.gate_store` properties,
   maintaining clean separation.

## Limitations (Phase 29)

- No bundle diff/comparison view (future: show config changes between versions)
- No gate re-run without creating a new gate result
- No scheduled/automated promotion (manual promote only)
- Console pages are read-only (mutations via CLI only)
- Rollback does not validate gate status (by design — emergency operation)

---

# Phase 30: Policy Promotion Approval, RBAC, and Console Write Governance

## Overview

**Phase 30** upgrades the Phase 29 policy release system from a CLI-executable
workflow to an approvable, authorizable, auditable governance flow. It adds:

1. **Promotion Request Lifecycle** — `pending → approved → rejected → executed / cancelled`
2. **RBAC for Policy Releases** — 8 granular permissions with a permission checker
3. **Gate Bypass Controls** — Triple-gate: config flag + BYPASS_GATE permission + reason
4. **Console Write Actions** — POST routes for creating, approving, rejecting, and executing promotions
5. **Audit Logging** — Every lifecycle transition and permission denial is logged
6. **Promotion Store** — Protocol + InMemory + SQLite persistence for promotion requests

## RBAC Permissions

| Permission | Value | Default |
|-----------|-------|---------|
| `BUNDLE_CREATE` | `policy.bundle.create` | Allowed |
| `GATE_RUN` | `policy.gate.run` | Allowed |
| `PROMOTION_REQUEST` | `policy.promotion.request` | Requires grant |
| `PROMOTION_APPROVE` | `policy.promotion.approve` | Requires grant |
| `PROMOTION_REJECT` | `policy.promotion.reject` | Requires grant |
| `PROMOTION_EXECUTE` | `policy.promotion.execute` | Requires grant |
| `ROLLBACK_EXECUTE` | `policy.rollback.execute` | Requires grant |
| `BYPASS_GATE` | `policy.gate.bypass` | Requires grant |

`BUNDLE_CREATE` and `GATE_RUN` are allowed by default. All promotion-related
permissions require explicit grants in `RunContext.permissions`.

## PromotionRequest Model

| Field | Type | Description |
|-------|------|-------------|
| `promotion_id` | `str` | Unique ID (`pr_` prefix) |
| `bundle_id` | `str` | Target bundle ID |
| `gate_result_id` | `str \| None` | Reference to gate evaluation |
| `requested_by` | `str` | Requester identity |
| `tenant_id` | `str \| None` | Tenant isolation |
| `status` | `str` | `pending`, `approved`, `rejected`, `executed`, `cancelled` |
| `reason` | `str \| None` | Why the promotion is requested |
| `approval_reason` | `str \| None` | Why it was approved |
| `rejection_reason` | `str \| None` | Why it was rejected |
| `resolved_by` | `str \| None` | Who resolved it |
| `executed_by` | `str \| None` | Who executed it |
| `created_at` | `datetime` | Request timestamp |
| `resolved_at` | `datetime \| None` | Resolution timestamp |
| `executed_at` | `datetime \| None` | Execution timestamp |

## PolicyReleaseService Extensions

New methods added to `PolicyReleaseService`:

### `request_promotion(bundle_id, requested_by, context, reason)`
Creates a PENDING promotion request. Requires `PROMOTION_REQUEST` permission.

### `approve_promotion(promotion_id, approved_by, context, reason)`
Transitions to APPROVED. Requires `PROMOTION_APPROVE` permission.

### `reject_promotion(promotion_id, rejected_by, context, reason)`
Transitions to REJECTED. Requires `PROMOTION_REJECT` permission.

### `execute_promotion(promotion_id, executed_by, context, bypass_gate, bypass_reason)`
Transitions to EXECUTED, activates the bundle. Requires `PROMOTION_EXECUTE` permission.
Validates gate status before executing.

## Gate Bypass Rules

Gate bypass requires **all three** conditions:

1. `bypass_gate=True` parameter in the execute call
2. `allow_gate_bypass=True` in config
3. `BYPASS_GATE` permission in `RunContext.permissions`
4. Non-empty `bypass_reason` string

When bypass is used, a `policy.gate.bypass_used` audit event is written.

## Config Schema (Phase 30 additions)

```yaml
governance:
  policy_release:
    bundles:
      type: sqlite
      path: .agent_app/policy_bundles.db
    gates:
      type: sqlite
      path: .agent_app/policy_gates.db
    promotions:
      type: sqlite
      path: .agent_app/policy_promotions.db
    require_promotion_approval: true
    allow_gate_bypass: false
    rules:
      - name: safe_default
        max_changed_ratio: 0.10
        max_failed_replays: 0
```

- `promotions` — Optional promotion store config (default: `None`, backward compatible)
- `require_promotion_approval` — Always `true` in current implementation
- `allow_gate_bypass` — Must be `true` to enable gate bypass (default: `false`)

## CLI Commands (Phase 30 additions)

```bash
# Promotion lifecycle
agentapp policy promotion request --config <path> --bundle-id <id> --requested-by <who> [--reason <text>] [--permissions <list>]
agentapp policy promotion list --config <path> [--status <status>]
agentapp policy promotion approve --config <path> --promotion-id <id> --approved-by <who> [--reason <text>] [--permissions <list>]
agentapp policy promotion reject --config <path> --promotion-id <id> --rejected-by <who> [--reason <text>] [--permissions <list>]
agentapp policy promotion execute --config <path> --promotion-id <id> --executed-by <who> [--bypass-gate] [--bypass-reason <text>] [--permissions <list>]
```

All promotion commands support `--actor-id` and `--permissions` for RBAC testing.

## Console Pages (Phase 30 additions)

| Route | Template | Method | Description |
|-------|----------|--------|-------------|
| `GET /promotions` | `policy_promotions.html` | GET | List all promotion requests |
| `GET /promotions/{id}` | `policy_promotion_detail.html` | GET | Full promotion details with action forms |
| `POST /promotions` | `policy_promotions.html` | POST | Create new promotion request |
| `POST /promotions/{id}/approve` | `policy_promotion_detail.html` | POST | Approve a pending request |
| `POST /promotions/{id}/reject` | `policy_promotion_detail.html` | POST | Reject a pending request |
| `POST /promotions/{id}/execute` | `policy_promotion_detail.html` | POST | Execute an approved request |

Console write actions require a `PolicyReleaseService` with appropriate permissions
passed via form data (`permissions` field, comma-separated).

## Audit Events

| Event Type | Trigger |
|-----------|---------|
| `policy.promotion.requested` | New promotion request created |
| `policy.promotion.approved` | Request approved |
| `policy.promotion.rejected` | Request rejected |
| `policy.promotion.executed` | Request executed, bundle activated |
| `policy.promotion.execute_blocked` | Execute blocked due to failed gate |
| `policy.gate.bypass_used` | Gate bypass successfully used |
| `policy.promotion.permission_denied` | RBAC check failed |

## Design Decisions

1. **Permission error extends `PermissionError`** — Enables CLI `except PermissionError` to catch service permission denials.
2. **Promotion store config defaults to `None`** — Backward compatible: existing configs without `promotions` section continue to work (in-memory promotion store not created).
3. **Console POST handlers catch `PermissionError` separately** — Renders as page messages, never tracebacks.
4. **Gate bypass triple gate** — Three independent controls prevent accidental bypass: config flag, permission, and human-entered reason.
5. **StrEnum for status** — `PromotionRequestStatus` uses `str` enum for JSON serialization compatibility.

## Current Limitations

- No promotion request cancellation from console (CLI supports it via store directly)
- No multi-step approval (single approver per request)
- No promotion request expiry/TTL
- Console pages don't show bundle details inline (link to bundle page)
- No bulk approval/reject operations
