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

---

# Phase 31: Policy Runtime Activation, Environment Isolation, and Hot Reload Baseline

## Overview

**Phase 31** transforms policy promotion from a persisted governance workflow into an actual runtime policy activation mechanism. It adds:

1. **Policy Environments** — Named environments (dev, staging, prod, custom) for environment-specific policy deployment
2. **PolicyActivation Model** — Records environment-specific bundle activation with lifecycle (ACTIVE → SUPERSEDED → ROLLED_BACK)
3. **PolicyActivationStore** — Protocol + InMemory + SQLite persistence for activation records
4. **ActivePolicyResolver** — Resolves active bundle for environment with config hash verification and TTL-aware caching
5. **Environment-Aware Promotion Execution** — `execute_promotion()` now accepts `environment` and `reason` parameters
6. **Request-Scoped Policy Environment** — `RunContext.policy_environment` for per-request environment targeting
7. **Runtime Config** — `PolicyReleaseRuntimeConfig` with environment, require_active_policy, cache_ttl_seconds
8. **CLI Commands** — `agentapp policy activation list/active`
9. **Console Pages** — `/activations`, `/activations/{id}`, `/environments`
10. **Audit Events** — policy.activation.created, superseded, rollback_marked

## PolicyActivation Model

| Field | Type | Description |
|-------|------|-------------|
| `activation_id` | `str` | Unique ID (`pa_` prefix) |
| `bundle_id` | `str` | Reference to the activated bundle |
| `environment` | `str` | Target environment (dev, staging, prod, custom) |
| `status` | `PolicyActivationStatus` | `ACTIVE`, `SUPERSEDED`, or `ROLLED_BACK` |
| `config_hash` | `str` | SHA-256 hash of the bundle config at activation time |
| `activated_by` | `str` | Who activated the bundle |
| `reason` | `str \| None` | Why this activation was created |
| `created_at` | `datetime` | Activation timestamp |
| `superseded_at` | `datetime \| None` | When superseded by a newer activation |
| `rolled_back_at` | `datetime \| None` | When rolled back |

**Activation lifecycle:**
- **ACTIVE** — Currently serving as the active policy for its environment
- **SUPERSEDED** — Replaced by a newer activation for the same environment
- **ROLLED_BACK** — Manually rolled back (environment returns to previous ACTIVE)

Only one ACTIVE activation per environment at any time. Promoting a new bundle
to an environment automatically supersedes the current ACTIVE activation.

## PolicyActivationStore

Protocol + InMemory + SQLite persistence, following the same pattern as
`PolicyBundleStore` and `PolicyGateStore`:

| Store | Protocol Methods | Persistence |
|-------|-----------------|-------------|
| `PolicyActivationStore` | `create()`, `get()`, `list()`, `get_active(environment)`, `supersede()`, `mark_rolled_back()` | InMemory / SQLite |

**Key behavior:**
- `create()` validates that no other ACTIVE activation exists for the same environment
- `get_active(environment)` returns the current ACTIVE activation for a given environment
- `supersede(activation_id)` transitions an activation to SUPERSEDED
- `mark_rolled_back(activation_id)` transitions to ROLLED_BACK

## ActivePolicyResolver

Runtime component that resolves the active bundle for a given environment:

### `resolve_active_bundle(environment) → PolicyBundle | None`
Returns the active bundle for the environment, or None if no activation exists.
Uses TTL-aware cache to avoid repeated store lookups.

### `require_active_bundle(environment) → PolicyBundle`
Like `resolve_active_bundle()` but raises `ValueError` when no active bundle exists
or when `require_active_policy=True` is configured.

### `refresh(environment) → None`
Invalidates the cache entry for a specific environment.

### `clear_cache() → None`
Clears the entire resolver cache.

**Cache behavior:**
- TTL configured via `cache_ttl_seconds` (default: 300 seconds)
- `_CacheEntry` stores bundle + timestamp; stale entries are re-fetched from store
- Config hash verification: the activation's `config_hash` must match the bundle's
  current `config_hash`; mismatches raise `ValueError` (detects config drift)

## Config Schema

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
    activations:
      type: sqlite
      path: .agent_app/policy_activations.db
    require_promotion_approval: true
    allow_gate_bypass: false
    runtime:
      environment: prod          # default environment for promotions
      require_active_policy: false # raise if no active bundle at runtime
      cache_ttl_seconds: 300     # resolver cache TTL
    rules:
      - name: safe_default
        max_changed_ratio: 0.10
        max_failed_replays: 0
```

New config sections:
- `activations` — Optional activation store config (default: `None`, backward compatible)
- `runtime` — `PolicyReleaseRuntimeConfig` with `environment`, `require_active_policy`, `cache_ttl_seconds`

## CLI Commands

```bash
# List all activations
agentapp policy activation list --config <path> [--environment <env>] [--status <status>]

# Show the currently active activation for an environment
agentapp policy activation active --config <path> --environment <env>
```

## Console Pages

| Route | Template | Description |
|-------|----------|-------------|
| `GET /activations` | `policy_activations.html` | List all activations with environment and status filters |
| `GET /activations/{id}` | `policy_activation_detail.html` | Full activation detail including bundle hash, timestamps, reason |
| `GET /environments` | `policy_activations.html` (mode=environments) | Overview of all environments with their current ACTIVE activation |

The activations page supports a dual-mode view: full list or environment overview.

## RunContext Extensions

Two new fields on `RunContext`:

- `policy_environment: str | None` — The environment this run targets. Set by the
  caller or defaults to the runtime config environment.
- `resolved_policy_bundle: PolicyBundle | None` — The bundle resolved by
  `ActivePolicyResolver` for the run's environment. Attached during `AppRunner.run()`
  before agent/tool execution.

## AppRunner Integration

`AppRunner` gains a `policy_resolver` parameter. During `run()`:

1. If `policy_resolver` is configured, calls `_resolve_active_policy(context)` before
   executing the agent/tool/workflow
2. `_resolve_active_policy()` checks `context.policy_environment`, resolves the active
   bundle via `policy_resolver.require_active_bundle()`, and attaches it to
   `context.resolved_policy_bundle`
3. Downstream `ToolExecutor` and governance components can inspect
   `context.resolved_policy_bundle` for environment-aware decisions

## Audit Events

| Event Type | Trigger |
|-----------|---------|
| `policy.activation.created` | New activation created during promotion |
| `policy.activation.superseded` | Existing activation superseded by newer promotion |
| `policy.activation.rollback_marked` | Activation manually marked as rolled back |

## Design Decisions

1. **Separate activation store** — Activations are lifecycle records distinct from
   bundles. A bundle can be activated multiple times across environments; activations
   track which environment got which version and when.

2. **Environment isolation via unique constraint** — The store enforces one ACTIVE
   activation per environment at the database level, preventing accidental dual-activation.

3. **Config hash verification at resolve time** — Detects config drift between the
   activation record and the actual bundle. If a bundle's config is modified after
   activation, the resolver raises `ValueError` rather than silently serving stale policy.

4. **TTL-aware caching** — Resolver caches bundle lookups to avoid repeated store
   queries during high-throughput runs. TTL defaults to 300 seconds; configurable.

5. **Backward-compatible optional stores** — `activations` store config defaults to
   `None`. Existing Phase 29/30 configs without an `activations` section continue to
   work; promotion execution simply skips activation record creation.

6. **require_active_policy defaults to False** — Opt-in enforcement. When disabled,
   runs proceed normally even if no bundle is active for the environment.

7. **StrEnum for activation status** — `PolicyActivationStatus` uses `str` enum for
   JSON serialization compatibility, consistent with other status enums in the codebase.

## Current Limitations

- No activation rollback that re-activates a previous bundle (ROLLED_BACK is a
  lifecycle marker; re-activation requires a new promotion)
- No activation expiry/TTL (activations persist until superseded or rolled back)
- No multi-environment bulk promotion (promote one environment at a time)
- Console pages are read-only (activations created via promotion execution)
- No activation diff view (future: show config changes between superseding activations)

---

# Phase 32: Policy Rollback, Emergency Disable, and Activation Safety Controls

## Overview

**Phase 32** adds emergency controls and safety mechanisms to the Phase 31
activation system. It provides:

1. **PolicyEnvironmentState** — ENABLED/DISABLED status per environment with reason tracking
2. **PolicyEnvironmentStore** — Protocol + InMemory + SQLite persistence for environment states
3. **Activation Rollback** — `rollback_to_activation()` creates a new activation pointing to a previous bundle, superseding the current active
4. **Rollback Fields on PolicyActivation** — `rollback_of_activation_id` and `rollback_target_activation_id` for rollback lineage tracking
5. **ActivePolicyResolver Safety** — Disabled environments return `None` for `resolve_active_bundle()`, raise `RuntimeError` for `require_active_bundle()`
6. **RBAC Permissions** — 3 new permissions: `ENVIRONMENT_DISABLE`, `ENVIRONMENT_ENABLE` (require grant), `ENVIRONMENT_VIEW` (default-allowed)
7. **Service APIs** — `rollback_environment()`, `disable_policy_environment()`, `enable_policy_environment()` with RBAC + audit
8. **CLI Commands** — `agentapp policy environment list/disable/enable`, `agentapp policy activation rollback`
9. **Console Pages** — Environment detail page with disable/enable/rollback forms

## PolicyEnvironmentState Model

| Field | Type | Description |
|-------|------|-------------|
| `environment` | `str` | Environment name |
| `status` | `PolicyEnvironmentStatus` | `ENABLED` or `DISABLED` (default: `ENABLED`) |
| `disabled_reason` | `str \| None` | Why the environment was disabled |
| `disabled_by` | `str \| None` | Who disabled the environment |
| `disabled_at` | `datetime \| None` | When the environment was disabled |
| `enabled_by` | `str \| None` | Who last enabled the environment |
| `enabled_at` | `datetime \| None` | When the environment was last enabled |
| `updated_at` | `datetime` | Last update timestamp |

**Default state:** When an environment has no stored state, `get()` returns a
`PolicyEnvironmentState` with `status=ENABLED` and all tracking fields as `None`.
This means new environments are enabled by default without requiring explicit
initialization.

## PolicyEnvironmentStore

Protocol + InMemory + SQLite persistence, following the same pattern as
`PolicyBundleStore` and `PolicyActivationStore`:

| Method | Signature | Description |
|--------|-----------|-------------|
| `get()` | `get(environment: str) -> PolicyEnvironmentState` | Returns current state (defaults to ENABLED) |
| `disable()` | `disable(environment: str, disabled_by: str, reason: str) -> PolicyEnvironmentState` | Sets status to DISABLED with reason and actor |
| `enable()` | `enable(environment: str, enabled_by: str, reason: str \| None = None) -> PolicyEnvironmentState` | Sets status to ENABLED with actor |
| `list()` | `list() -> list[PolicyEnvironmentState]` | Lists all stored environment states |

**SQLite schema:** `policy_environment_states` table with `environment` as primary key.
Uses `INSERT OR REPLACE` for disable/enable operations.

**Factory:** `create_policy_environment_store(store_type, db_path)` supports `"memory"`
and `"sqlite"` types.

## Activation Rollback Lifecycle

Rollback creates a **new** activation record pointing to the target bundle rather
than modifying existing records. This preserves a complete audit trail.

### Steps

1. **Validate** — Check ROLLBACK_EXECUTE permission; verify target activation exists
   and belongs to the same environment; verify target bundle still exists in bundle store
2. **Supersede current** — Current ACTIVE activation transitions to SUPERSEDED with
   `superseded_by_activation_id` pointing to the new rollback activation
3. **Create new activation** — New `PolicyActivation` record with:
   - `bundle_id` = target activation's bundle_id
   - `config_hash` = target activation's config_hash
   - `status` = ACTIVE
   - `rollback_of_activation_id` = the current activation that was superseded
   - `rollback_target_activation_id` = the target activation being rolled back to
4. **Clear cache** — Resolver cache entry for the environment is invalidated

### Rollback Fields on PolicyActivation

| Field | Type | Description |
|-------|------|-------------|
| `rollback_of_activation_id` | `str \| None` | The activation that was superseded by this rollback |
| `rollback_target_activation_id` | `str \| None` | The activation this rollback targets (points to its bundle) |

Both fields are `None` for activations created via normal promotion execution.

### `rollback_to_activation()` on PolicyActivationStore

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_previous_activation()` | `get_previous_activation(environment, before_activation_id=None) -> PolicyActivation \| None` | Find the most recent non-ACTIVE activation |
| `rollback_to_activation()` | `rollback_to_activation(environment, target_activation_id, rolled_back_by, reason=None) -> PolicyActivation` | Create new ACTIVE activation pointing to target's bundle |

**SQLite migration:** Existing databases automatically get `rollback_of_activation_id` and
`rollback_target_activation_id` columns added via `ALTER TABLE`.

## Environment Disable/Enable Lifecycle

### Disable

1. Check `ENVIRONMENT_DISABLE` permission
2. Require non-empty `reason` (mandatory for audit trail)
3. Store sets status to DISABLED with `disabled_reason`, `disabled_by`, `disabled_at`
4. Clear resolver cache for the environment
5. Audit event: `policy.environment.disabled`

### Enable

1. Check `ENVIRONMENT_ENABLE` permission
2. Store sets status to ENABLED with `enabled_by`, `enabled_at`
3. Clear resolver cache for the environment
4. Audit event: `policy.environment.enabled`

Both operations refresh the resolver cache to ensure the safety check immediately
reflects the new state.

## RBAC Permissions (Phase 32 additions)

| Permission | Value | Default |
|-----------|-------|---------|
| `ENVIRONMENT_DISABLE` | `policy.environment.disable` | Requires grant |
| `ENVIRONMENT_ENABLE` | `policy.environment.enable` | Requires grant |
| `ENVIRONMENT_VIEW` | `policy.environment.view` | Allowed |

`ENVIRONMENT_VIEW` is default-allowed alongside `BUNDLE_CREATE` and `GATE_RUN`.
`ENVIRONMENT_DISABLE` and `ENVIRONMENT_ENABLE` require explicit grants in
`RunContext.permissions`, consistent with other destructive operations.

`ROLLBACK_EXECUTE` (`policy.rollback.execute`) already existed from Phase 30 and
governs the new `rollback_environment()` method.

## ActivePolicyResolver Safety Behavior

The resolver checks environment state before attempting bundle resolution:

### `resolve_active_bundle(environment) → PolicyBundle | None`

If environment is DISABLED:
- Returns `None` immediately (no store lookup, no bundle fetch)
- Caches the `None` result if TTL caching is enabled

If environment is ENABLED (or no environment store configured):
- Proceeds with normal resolution (activation lookup, bundle fetch, hash verification)

### `require_active_bundle(environment) → PolicyBundle`

If environment is DISABLED:
- Raises `RuntimeError` with message including the disabled reason:
  `"Policy environment '<env>' is disabled: <reason>. Enable the environment before requiring active policy."`

If environment is ENABLED but no active bundle exists:
- Raises `KeyError` (unchanged from Phase 31)

This two-tier error behavior allows callers to gracefully handle disabled environments
(via `resolve_active_bundle`) while still getting a clear error for strict enforcement
(via `require_active_bundle`).

## Config Schema (Phase 32 additions)

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
    activations:
      type: sqlite
      path: .agent_app/policy_activations.db
    environments:
      type: sqlite
      path: .agent_app/policy_environments.db
    require_promotion_approval: true
    allow_gate_bypass: false
    runtime:
      environment: prod
      require_active_policy: false
      cache_ttl_seconds: 300
    rules:
      - name: safe_default
        max_changed_ratio: 0.10
        max_failed_replays: 0
```

New config section:
- `environments` — Optional environment store config (default: `None`, backward compatible)

The config loader wires `environment_store` into both `PolicyReleaseService` and
`ActivePolicyResolver` when the `environments` section is present.

## CLI Commands (Phase 32 additions)

```bash
# List environment states
agentapp policy environment list --config <path>

# Disable an environment (requires --reason)
agentapp policy environment disable --config <path> --environment <env> --disabled-by <who> --reason <text> [--actor-id <id>] [--permissions <list>]

# Re-enable a disabled environment
agentapp policy environment enable --config <path> --environment <env> --enabled-by <who> [--reason <text>] [--actor-id <id>] [--permissions <list>]

# Roll back an environment to a previous activation
agentapp policy activation rollback --config <path> --environment <env> --rolled-back-by <who> [--target-activation-id <id>] [--reason <text>] [--actor-id <id>] [--permissions <list>]
```

All Phase 32 commands support `--actor-id` and `--permissions` for RBAC testing,
consistent with Phase 30 promotion commands.

The `activation rollback` command creates a new activation pointing to the previous
bundle. If `--target-activation-id` is omitted, it rolls back to the most recent
non-ACTIVE activation for the environment.

## Console Pages (Phase 32 additions)

| Route | Template | Method | Description |
|-------|----------|--------|-------------|
| `GET /environments/{environment}` | `policy_environment_detail.html` | GET | Environment detail page with status, activations, and action forms |
| `POST /environments/{environment}/disable` | `policy_environment_detail.html` | POST | Disable the environment |
| `POST /environments/{environment}/enable` | `policy_environment_detail.html` | POST | Re-enable the environment |
| `POST /environments/{environment}/rollback` | `policy_environment_detail.html` | POST | Roll back to a previous activation |

The environment detail page shows:
- Current environment status (ENABLED/DISABLED badge)
- Disabled reason, disabled_by, and disabled_at if applicable
- Current active activation details
- List of previous activations for the environment
- Action forms for disable/enable/rollback (respecting RBAC permissions)

Console actions require a `PolicyReleaseService` with appropriate permissions
passed via form data.

## Audit Events

| Event Type | Trigger |
|-----------|---------|
| `policy.environment.disabled` | Environment successfully disabled |
| `policy.environment.enabled` | Environment successfully re-enabled |
| `policy.environment.disable_denied` | ENVIRONMENT_DISABLE permission denied |
| `policy.environment.enable_denied` | ENVIRONMENT_ENABLE permission denied |
| `policy.activation.rollback_completed` | Rollback successfully created new activation |
| `policy.activation.rollback_failed` | Rollback failed (validation or store error) |
| `policy.activation.rollback_denied` | ROLLBACK_EXECUTE permission denied |
| `policy.runtime.policy_resolution_blocked` | Resolver returned None for disabled environment |

## Design Decisions

1. **Rollback creates a new activation** — Instead of reactivating an old record,
   rollback creates a fresh `PolicyActivation` pointing to the target's bundle.
   This preserves the complete activation lineage and audit trail, enabling
   operators to trace the full history of which bundle was active at any time.

2. **Disabled environment returns None for resolve, RuntimeError for require** —
   The two-tier behavior lets callers choose their safety level. Callers using
   `resolve_active_bundle()` can gracefully degrade (e.g., fall back to a default
   policy), while `require_active_bundle()` provides a hard stop with a clear
   error message including the disabled reason.

3. **Disable requires a non-empty reason** — Unlike other operations where reason
   is optional, disabling a policy environment mandates a reason string. This
   ensures the audit trail always captures why production policy was blocked.

4. **Environment defaults to ENABLED** — When no state is stored for an environment,
   `get()` returns `PolicyEnvironmentStatus.ENABLED`. This avoids requiring explicit
   initialization for every environment and maintains backward compatibility with
   Phase 31 configs.

5. **ENVIRONMENT_VIEW is default-allowed** — Viewing environment state is a read-only
   operation with no side effects, consistent with `BUNDLE_CREATE` and `GATE_RUN`
   being default-allowed. Disable and enable are destructive and require grants.

6. **Resolver cache cleared on state change** — Both disable/enable and rollback
   clear the resolver cache for the affected environment. This ensures the safety
   check and bundle resolution immediately reflect the new state without waiting
   for TTL expiry.

7. **Target bundle validation in rollback** — `rollback_environment()` verifies the
   target bundle still exists in the bundle store before creating the rollback
   activation. This prevents rolling back to a bundle that was deleted or lost.

8. **SQLite schema migration for rollback columns** — Existing `policy_activations`
   tables automatically get `rollback_of_activation_id` and
   `rollback_target_activation_id` columns added via `ALTER TABLE` on store init.
   This ensures Phase 31 databases work without manual migration.

## Known Limitations

1. **No automatic rollback trigger** — Rollback is operator-initiated only. There
   is no automatic rollback on policy failure or error rate threshold.

2. **No rollback preview** — No diff or preview of what the rollback will change
   before executing it. Operators must inspect the target activation manually.

3. **No multi-environment rollback** — Rollback operates on one environment at a
   time. Bulk rollback across environments is not supported.

4. **Resolver cache uses clear, not targeted invalidation** — When the resolver
   lacks a `refresh()` method, `clear_cache()` is called instead, invalidating
   cache entries for all environments rather than just the affected one.

5. **Environment disable does not interrupt in-flight runs** — Disabling an
   environment only affects future resolution calls. Currently executing runs
   continue with their already-resolved policy bundle.

6. **No environment-level policy override** — Disabling an environment blocks
   resolution entirely; there is no mechanism to serve a fallback or default
   policy for a disabled environment.
