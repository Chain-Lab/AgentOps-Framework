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

---

# Phase 33: Release Rings, Canary Evaluation, and Ring-Aware Policy Resolution

## Overview

**Phase 33** adds release rings to the Phase 32 policy release system, enabling
canary deployments and graduated rollouts of policy bundles. It provides:

1. **Release Rings** — Named deployment targets per environment (stable, canary, internal, custom)
2. **RingActivationAssignment** — Assigns a specific activation (bundle version) to a ring
3. **PolicyRingRouter** — Request-scoped ring resolution with explicit override and default ring fallback
4. **Ring-Aware Resolver** — `resolve_active_bundle_for_ring()` and `require_active_bundle_for_ring()` with triple integrity verification
5. **Canary Eval Flow** — Assign activation to canary ring, run eval suite, promote to stable
6. **RBAC Permissions** — 6 new ring permissions (RING_CREATE, RING_ASSIGN, RING_PROMOTE, RING_DISABLE, RING_ENABLE, RING_VIEW)
7. **CLI Commands** — `agentapp policy ring` and `agentapp policy canary` subcommands
8. **Console Pages** — Ring list and detail pages with create/assign/promote/disable/enable actions
9. **Audit Events** — 4 new ring lifecycle events

## Architecture

```
RunContext.policy_ring ──→ PolicyRingRouter.resolve_ring()
                              │
                              ▼
                       ring_name (e.g. "canary")
                              │
                              ▼
                   ActivePolicyResolver
                   .resolve_active_bundle_for_ring(environment, ring_name)
                              │
                              ▼
                   RingActivationAssignmentStore
                   → PolicyActivationStore → PolicyBundleStore
                              │
                              ▼
                        PolicyBundle (active for ring)

CLI: agentapp policy ring list/create/assign/promote/disable/enable
CLI: agentapp policy canary eval
Console: GET  /rings, /rings/{env}/{name}
         POST /rings, /rings/{env}/{name}/assign, .../promote, .../disable, .../enable
```

## ReleaseRing Model

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ring_id` | `str` | required | Unique ID (`ring_` prefix) |
| `environment` | `str` | required | Owning environment |
| `name` | `str` | required | Ring name (stable, canary, internal, etc.) |
| `description` | `str \| None` | `None` | Human-readable description |
| `status` | `ReleaseRingStatus` | `ENABLED` | `ENABLED` or `DISABLED` |
| `is_default` | `bool` | `False` | Whether this is the default ring for the environment |
| `created_at` | `datetime` | `datetime.now(timezone.utc)` | Creation timestamp |
| `updated_at` | `datetime` | `datetime.now(timezone.utc)` | Last update timestamp |

**Constraints:** `UNIQUE(environment, name)` — each ring name is unique per environment.

## ReleaseRingStore

Protocol + InMemory + SQLite persistence, following the same pattern as
`PolicyBundleStore` and `PolicyActivationStore`:

| Method | Signature | Description |
|--------|-----------|-------------|
| `create()` | `create(ring: ReleaseRing) -> ReleaseRing` | Create a new ring |
| `get()` | `get(ring_id: str) -> ReleaseRing \| None` | Get by ring ID |
| `get_by_name()` | `get_by_name(environment: str, name: str) -> ReleaseRing \| None` | Get by environment + name |
| `list()` | `list(environment: str \| None = None) -> list[ReleaseRing]` | List rings, optionally filtered by environment |
| `set_default()` | `set_default(environment: str, ring_name: str) -> ReleaseRing` | Set ring as default (clears previous default) |
| `disable()` | `disable(environment: str, ring_name: str) -> ReleaseRing` | Set ring status to DISABLED |
| `enable()` | `enable(environment: str, ring_name: str) -> ReleaseRing` | Set ring status to ENABLED |

**Factory:** `create_release_ring_store(store_type, db_path)` supports `"memory"` and `"sqlite"` types.

**SQLite schema:** `policy_release_rings` table with `ring_id` as primary key and
`UNIQUE(environment, name)` constraint.

## RingActivationAssignment Model

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `assignment_id` | `str` | required | Unique ID (`ra_` prefix) |
| `environment` | `str` | required | Target environment |
| `ring_name` | `str` | required | Target ring name |
| `activation_id` | `str` | required | Assigned activation ID |
| `bundle_id` | `str` | required | Bundle ID (convenience copy) |
| `config_hash` | `str` | required | Config hash (integrity check) |
| `status` | `RingActivationAssignmentStatus` | `ACTIVE` | `ACTIVE`, `SUPERSEDED`, or `DISABLED` |
| `assigned_by` | `str` | required | Who assigned this activation |
| `reason` | `str \| None` | `None` | Assignment reason |
| `created_at` | `datetime` | `datetime.now(timezone.utc)` | Creation timestamp |
| `superseded_at` | `datetime \| None` | `None` | Supersession timestamp |
| `superseded_by_assignment_id` | `str \| None` | `None` | Assignment that superseded this one |

**Key behavior:** Only one ACTIVE assignment per environment+ring at a time.
Assigning a new activation to a ring automatically supersedes the previous
ACTIVE assignment.

## RingActivationAssignmentStore

| Method | Signature | Description |
|--------|-----------|-------------|
| `assign()` | `assign(assignment: RingActivationAssignment) -> RingActivationAssignment` | Assign activation to ring (auto-supersedes previous) |
| `get()` | `get(assignment_id: str) -> RingActivationAssignment \| None` | Get by assignment ID |
| `get_active()` | `get_active(environment: str, ring_name: str) -> RingActivationAssignment \| None` | Get current ACTIVE assignment |
| `list()` | `list(environment=None, ring_name=None) -> list[RingActivationAssignment]` | List with optional filters |
| `disable_active()` | `disable_active(environment, ring_name, disabled_by, reason=None) -> RingActivationAssignment \| None` | Disable the active assignment |

**Factory:** `create_ring_assignment_store(store_type, db_path)` supports `"memory"` and `"sqlite"` types.

**SQLite schema:** `policy_ring_activation_assignments` table with `assignment_id` as primary key.

## PolicyRingRouter

Resolves which ring applies to a given request.

### `resolve_ring(environment, context) -> str`

**Resolution order:**

1. **Explicit override** — If `context.policy_ring` is set, use it directly
2. **Default ring from store** — If `ring_store` is configured, look up the ring
   with `is_default=True` and `status=ENABLED` for the environment
3. **Configured fallback** — Fall back to `default_ring` parameter (default: `"stable"`)
4. **Disabled ring check** — Raises `RuntimeError` if the selected ring is DISABLED
5. **Existence check** — Raises `KeyError` if the ring does not exist (unless no `ring_store`)

### Constructor

```python
PolicyRingRouter(ring_store=None, default_ring="stable")
```

## Ring-Aware Policy Resolver

`ActivePolicyResolver` gains two new methods for ring-scoped bundle resolution:

### `resolve_active_bundle_for_ring(environment, ring_name) -> PolicyBundle | None`

Resolution chain:

1. Check cache with `(environment, ring_name)` tuple key
2. Check environment state — returns `None` if environment is DISABLED
3. Check ring state — returns `None` if ring is DISABLED
4. Get active `RingActivationAssignment` from assignment store
5. Load `PolicyActivation` from activation store
6. Load `PolicyBundle` from bundle store
7. **Triple integrity check** — Verify `config_hash` matches across assignment, activation, and bundle
8. Cache result with TTL

### `require_active_bundle_for_ring(environment, ring_name) -> PolicyBundle`

Like `resolve_active_bundle_for_ring()` but raises:
- `RuntimeError` if environment or ring is DISABLED
- `KeyError` if no active assignment or bundle found

### Cache Structure

The resolver cache supports both plain string keys (environment-only from Phase 31)
and tuple keys `(environment, ring_name)` for ring-scoped entries. The `refresh()`
method clears both types for the given environment.

## Canary Eval Flow

The recommended workflow for safely promoting a policy bundle to production:

### 1. Create Rings

```bash
agentapp policy ring create --config <path> --environment prod --name canary --actor-id admin
agentapp policy ring create --config <path> --environment prod --name stable --actor-id admin --is-default
```

### 2. Assign Activation to Canary

```bash
agentapp policy ring assign --config <path> --environment prod \
  --ring canary --activation-id pa_abc123 --actor-id admin \
  --reason "Testing new policy bundle v2"
```

### 3. Run Canary Evaluation

```bash
agentapp policy canary eval --config <path> --environment prod \
  --ring canary --activation-id pa_abc123 --suite evals/canary_regression.yaml
```

### 4. Promote to Stable

```bash
agentapp policy ring promote --config <path> --environment prod \
  --from-ring canary --to-ring stable --actor-id admin \
  --reason "Canary eval passed, promoting to stable"
```

### CanaryEvalRunner

```python
from agent_app.evals.canary import CanaryEvalRunner

runner = CanaryEvalRunner(app)
result = await runner.run_for_activation(
    activation_id="pa_abc123",
    environment="prod",
    ring_name="canary",
    suite_path="evals/canary_regression.yaml",
)
# result.passed, result.total, result.passed_count, result.failed_count, result.errors
```

**CanaryEvalResult fields:**

| Field | Type | Description |
|-------|------|-------------|
| `environment` | `str` | Target environment |
| `ring_name` | `str` | Ring being evaluated |
| `activation_id` | `str` | Activation being evaluated |
| `suite_name` | `str` | Eval suite name |
| `passed` | `bool` | Whether all cases passed |
| `total` | `int` | Total number of eval cases |
| `passed_count` | `int` | Number of passed cases |
| `failed_count` | `int` | Number of failed cases |
| `errors` | `list[str]` | Per-case error messages |

## RBAC Permissions (Phase 33 additions)

| Permission | Value | Default |
|-----------|-------|---------|
| `RING_CREATE` | `policy.ring.create` | Requires grant |
| `RING_ASSIGN` | `policy.ring.assign` | Requires grant |
| `RING_PROMOTE` | `policy.ring.promote` | Requires grant |
| `RING_DISABLE` | `policy.ring.disable` | Requires grant |
| `RING_ENABLE` | `policy.ring.enable` | Requires grant |
| `RING_VIEW` | `policy.ring.view` | Allowed |

`RING_VIEW` is default-allowed alongside `BUNDLE_CREATE`, `GATE_RUN`, and
`ENVIRONMENT_VIEW`. All other ring permissions require explicit grants in
`RunContext.permissions`.

## RunContext Extensions

New field on `RunContext`:

- `policy_ring: str | None` — The ring this run targets. Set by the caller or
  resolved by `PolicyRingRouter.resolve_ring()` during runtime initialization.

## Config Schema (Phase 33 additions)

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
    rings:                              # Phase 33
      type: sqlite
      path: .agent_app/policy_release_rings.db
    ring_assignments:                   # Phase 33
      type: sqlite
      path: .agent_app/policy_ring_activation_assignments.db
    require_promotion_approval: true
    allow_gate_bypass: false
    runtime:
      environment: prod
      require_active_policy: false
      cache_ttl_seconds: 300
      ring: null                        # Phase 33: default ring override
    rules:
      - name: safe_default
        max_changed_ratio: 0.10
        max_failed_replays: 0
```

New config fields:
- `rings` — Optional release ring store config (default: `None`, backward compatible)
- `ring_assignments` — Optional ring assignment store config (default: `None`, backward compatible)
- `runtime.ring` — Optional default ring override for runtime resolution

## CLI Commands

### Ring Management

```bash
# List release rings for an environment
agentapp policy ring list --config <path> --environment <env> [--json]

# Create a release ring
agentapp policy ring create --config <path> --environment <env> --name <name> \
  --actor-id <id> [--description <text>] [--is-default] [--permissions <list>]

# Assign an activation to a ring
agentapp policy ring assign --config <path> --environment <env> --ring <name> \
  --activation-id <id> --actor-id <id> [--reason <text>] [--permissions <list>]

# Promote canary activation to stable ring
agentapp policy ring promote --config <path> --environment <env> \
  --from-ring <canary> --to-ring <stable> --actor-id <id> \
  [--reason <text>] [--permissions <list>]

# Disable a release ring
agentapp policy ring disable --config <path> --environment <env> --ring <name> \
  --actor-id <id> [--reason <text>] [--permissions <list>]

# Enable a disabled ring
agentapp policy ring enable --config <path> --environment <env> --ring <name> \
  --actor-id <id> [--permissions <list>]
```

### Canary Evaluation

```bash
# Run canary eval suite against an activation
agentapp policy canary eval --config <path> --environment <env> --ring <name> \
  --activation-id <id> --suite <path> [--json]
```

All ring commands support `--actor-id` and `--permissions` for RBAC testing,
consistent with Phase 30 promotion commands.

## Console Pages

| Route | Template | Method | Description |
|-------|----------|--------|-------------|
| `GET /rings` | `policy_rings.html` | GET | List all rings with environment, status, and active bundle |
| `GET /rings/{env}/{name}` | `policy_ring_detail.html` | GET | Ring detail with status, active assignment, and history |
| `POST /rings` | `policy_rings.html` | POST | Create a new release ring |
| `POST /rings/{env}/{name}/assign` | `policy_ring_detail.html` | POST | Assign an activation to the ring |
| `POST /rings/{env}/{name}/promote` | `policy_ring_detail.html` | POST | Promote activation to another ring |
| `POST /rings/{env}/{name}/disable` | `policy_ring_detail.html` | POST | Disable the ring |
| `POST /rings/{env}/{name}/enable` | `policy_ring_detail.html` | POST | Re-enable the ring |

Console write actions require a `PolicyReleaseService` with appropriate permissions
passed via form data (`permissions` field, comma-separated).

## Audit Events

| Event Type | Trigger |
|-----------|---------|
| `policy.ring.created` | New release ring created |
| `policy.ring.disabled` | Ring disabled |
| `policy.ring.enabled` | Ring re-enabled |
| `policy.ring.assignment.created` | Activation assigned to ring |
| `policy.ring.promoted` | Canary activation promoted to stable (via assign to stable) |
| `policy.ring.permission_denied` | RBAC check failed for ring operation |
| `policy.canary.eval_started` | Canary evaluation started |
| `policy.canary.eval_completed` | Canary evaluation completed |
| `policy.canary.eval_failed` | Canary evaluation failed |

## Design Decisions

1. **One ACTIVE assignment per environment+ring** — Only one activation can be
   ACTIVE for a given environment+ring pair. Assigning a new activation
   automatically supersedes the previous one, preserving a complete assignment
   history.

2. **RING_VIEW is default-allowed** — Viewing ring state is a read-only operation
   with no side effects, consistent with `BUNDLE_CREATE`, `GATE_RUN`, and
   `ENVIRONMENT_VIEW`. All mutation operations require explicit grants.

3. **Tuple cache key for ring resolution** — Ring-scoped cache entries use
   `(environment, ring_name)` tuples to avoid collisions with environment-only
   cache entries from Phase 31. The `refresh()` method clears both key types.

4. **Triple integrity verification** — `resolve_active_bundle_for_ring()` verifies
   `config_hash` across three levels: ring assignment, policy activation, and
   bundle. Any mismatch raises `ValueError`, detecting config drift at any layer.

5. **Canary eval does not inject metadata** — `EvalRunner.run_suite()` does not
   accept a metadata parameter, so the caller must pre-configure the app for the
   target ring/environment before invoking canary eval. This keeps the eval runner
   decoupled from ring concepts.

6. **Explicit override via `context.policy_ring`** — The `PolicyRingRouter`
   checks `context.policy_ring` first, allowing callers to explicitly target a
   ring without modifying store state. This supports testing and one-off runs.

7. **Store factory pattern** — Both `create_release_ring_store()` and
   `create_ring_assignment_store()` support `"memory"` and `"sqlite"` backends,
   consistent with all other stores in the framework.

8. **Promote is ring-to-ring assignment** — `promote_canary_to_stable()` reads
   the canary ring's active assignment and assigns the same activation to the
   stable ring. This reuses the existing assignment mechanism rather than
   introducing a separate promotion concept.

9. **SQLite UNIQUE constraint on (environment, name)** — The ring store enforces
   unique ring names per environment at the database level, preventing accidental
   duplicate rings.

10. **Backward-compatible optional stores** — `rings` and `ring_assignments` store
    configs default to `None`. Existing Phase 31/32 configs without ring sections
    continue to work; ring-aware resolution only activates when ring stores are
    configured.

## Known Limitations

1. **Canary eval metadata injection** — `EvalRunner.run_suite()` does not accept
   a metadata parameter, so environment/ring context is not injected into the eval
   run itself. The caller must ensure the app is configured for the target
   ring/environment before invoking canary eval.

2. **No automatic canary promotion** — Promotion from canary to stable is manual.
   There is no automatic promotion based on eval pass rate or time-based rollout.

3. **No ring-level traffic splitting** — All requests for a given environment
   resolve to a single ring (via `context.policy_ring` or default). There is no
   percentage-based traffic splitting between rings.

4. **No ring diff/comparison view** — Console pages show ring status and
   assignments but do not provide a diff between the canary and stable bundle
   configurations.

5. **Ring disable does not disable assignments** — Disabling a ring prevents new
   resolution but does not change the status of existing assignments. The
   `disable_active()` method on the assignment store handles assignment-level
   disabling separately.

6. **No ring promotion history** — The promote operation creates a new assignment
   but does not record a separate "promotion" event linking canary and stable
   assignments. Operators must inspect assignment history manually.

7. **No cross-environment promotion** — Promotion operates within a single
   environment. Promoting a canary ring from staging to prod requires separate
   operations in each environment.

---

# Phase 34: Runtime Reload Hooks, Cache Invalidation, and Deterministic Canary Routing

## Overview

**Phase 34** adds runtime reload notifications, structured change events, resolver
cache introspection, and deterministic canary percentage routing to the Phase 33
policy release system. It provides:

1. **Policy Change Events** — Structured event model with 12 event types, append-only event store
2. **PolicyReloadManager** — Runtime reload notifications with hook management
3. **Resolver Cache Status** — Introspection and targeted invalidation for resolver cache
4. **Deterministic Canary Percentage Routing** — SHA-256 hash-based routing with configurable canary percentage
5. **AppRunner Ring Router Integration** — Policy metadata in AppRunResult
6. **Config Schema** — PolicyChangeEventsConfig, PolicyReloadConfig, RingRoutingConfig
7. **CLI Commands** — reload request/status, events list, routing simulate
8. **Console Pages** — Events, reload, routing simulator
9. **RBAC Permissions** — RELOAD_REQUEST, RELOAD_VIEW, EVENT_VIEW, ROUTING_SIMULATE

## Policy Change Events

### PolicyChangeEventType Enum

12 event types covering all policy lifecycle changes:

| Event Type | Description |
|-----------|-------------|
| `BUNDLE_CREATED` | New bundle created (DRAFT) |
| `BUNDLE_ACTIVATED` | Bundle promoted to ACTIVE |
| `BUNDLE_ARCHIVED` | Bundle archived (superseded) |
| `BUNDLE_ROLLED_BACK` | Bundle rolled back |
| `GATE_PASSED` | Gate evaluation passed |
| `GATE_FAILED` | Gate evaluation failed |
| `PROMOTION_REQUESTED` | Promotion request created |
| `PROMOTION_APPROVED` | Promotion request approved |
| `PROMOTION_REJECTED` | Promotion request rejected |
| `PROMOTION_EXECUTED` | Promotion executed, bundle activated |
| `ACTIVATION_CHANGED` | Environment activation changed (created, superseded, or rolled back) |
| `MANUAL_RELOAD_REQUESTED` | Manual reload requested via reload manager |

### PolicyChangeEvent Model

| Field | Type | Description |
|-------|------|-------------|
| `event_id` | `str` | Unique ID (`pce_` prefix) |
| `event_type` | `PolicyChangeEventType` | Event type from the enum above |
| `environment` | `str \| None` | Target environment (if applicable) |
| `ring_name` | `str \| None` | Target ring name (if applicable) |
| `bundle_id` | `str \| None` | Affected bundle ID |
| `actor_id` | `str \| None` | Who triggered the change |
| `reason` | `str \| None` | Why the change was made |
| `metadata` | `dict[str, Any]` | Arbitrary event metadata |
| `created_at` | `datetime` | Event timestamp (UTC) |

### PolicyChangeEventStore

Append-only event store following the Protocol + InMemory + SQLite pattern:

| Method | Signature | Description |
|--------|-----------|-------------|
| `append()` | `append(event: PolicyChangeEvent) -> PolicyChangeEvent` | Append a new event |
| `get()` | `get(event_id: str) -> PolicyChangeEvent \| None` | Get by event ID |
| `list()` | `list(environment=None, event_type=None, limit=100) -> list[PolicyChangeEvent]` | List with optional filters |
| `count()` | `count(environment=None) -> int` | Count events, optionally filtered by environment |

**Factory:** `create_policy_change_event_store(store_type, db_path)` supports `"memory"` and `"sqlite"` types.

**Append-only guarantee:** Events can only be appended, never modified or deleted. This
ensures a complete audit trail for compliance and debugging.

### Event Emission from PolicyReleaseService

PolicyReleaseService emits change events after each state change. 11 event types
are emitted automatically:

| State Change | Event Type Emitted |
|-------------|-------------------|
| Bundle created | `BUNDLE_CREATED` |
| Bundle activated (promoted) | `BUNDLE_ACTIVATED` |
| Bundle archived | `BUNDLE_ARCHIVED` |
| Bundle rolled back | `BUNDLE_ROLLED_BACK` |
| Gate passed | `GATE_PASSED` |
| Gate failed | `GATE_FAILED` |
| Promotion requested | `PROMOTION_REQUESTED` |
| Promotion approved | `PROMOTION_APPROVED` |
| Promotion rejected | `PROMOTION_REJECTED` |
| Promotion executed | `PROMOTION_EXECUTED` |
| Activation changed | `ACTIVATION_CHANGED` |

**Non-strict mode (default):** Event emission failures are logged but do not corrupt
the main state transition. If the event store is unavailable, the policy operation
still succeeds.

**Strict mode:** Event emission failures are propagated as exceptions. Use this when
event auditability is more important than availability.

## Reload Manager and Hooks

### PolicyReloadManager

Runtime component for coordinating reload notifications and hook execution.

#### `request_reload(environment, actor_id, reason) -> ReloadResult`

1. Creates a `MANUAL_RELOAD_REQUESTED` change event
2. Calls `refresh_resolver()` to invalidate cached policy
3. Calls all registered hooks in sequence
4. Returns `ReloadResult` with per-hook outcomes

#### `refresh_resolver(environment, ring_name) -> None`

Clears resolver cache for the specified environment/ring (or all if not specified).

#### `register_hook(name, hook_fn) -> None`

Registers a hook function to be called during reload. Multiple hooks are called
in sequence. Hook failures are captured in `ReloadResult` per-hook, not raised
as exceptions.

### Hook Protocol

```python
async def hook_fn(environment: str, reason: str | None) -> None:
    """Called during reload. Raise to report failure."""
    ...
```

Hooks are called with the target environment and the reload reason. If a hook
raises an exception, it is captured in the `ReloadResult.hook_results` list
with `success=False` and the error message. Other hooks continue executing.

### ReloadResult Model

| Field | Type | Description |
|-------|------|-------------|
| `environment` | `str` | Target environment |
| `cache_cleared` | `bool` | Whether resolver cache was cleared |
| `hooks_called` | `int` | Number of hooks invoked |
| `hook_results` | `list[HookResult]` | Per-hook outcomes |
| `event_id` | `str \| None` | Change event ID for the reload request |
| `error` | `str \| None` | Overall error message if reload failed |

### HookResult Model

| Field | Type | Description |
|-------|------|-------------|
| `hook_name` | `str` | Name of the hook |
| `success` | `bool` | Whether the hook succeeded |
| `error` | `str \| None` | Error message if the hook failed |

## Resolver Cache Status

ActivePolicyResolver gains introspection methods for cache visibility:

### `cache_status() -> CacheStatus`

Returns current cache state:

| Field | Type | Description |
|-------|------|-------------|
| `entries` | `int` | Number of cached entries |
| `keys` | `list[str]` | Human-readable cache keys |
| `ttl_seconds` | `float \| None` | Configured TTL (None if caching disabled) |

### `refresh(environment=None, ring_name=None) -> None`

Clears cache entries. With no arguments, clears all entries. With `environment`,
clears entries for that environment (both plain and ring-scoped). With
`environment` and `ring_name`, clears only ring-scoped entries for that
environment+ring combination.

### `clear_cache(environment=None, ring_name=None) -> None`

Synchronous cache clearing with the same behavior as `refresh()`.

**Disabled environment/ring behavior:** When an environment or ring is disabled,
the resolver does not serve stale cache. Cache entries for disabled targets
are removed immediately on state change.

## Deterministic Canary Percentage Routing

### RingRoutingConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool` | `False` | Enable percentage-based routing |
| `canary_percentage` | `int` | `0` | Percentage of traffic routed to canary (0-100) |
| `canary_ring` | `str` | `"canary"` | Name of the canary ring |
| `stable_ring` | `str` | `"stable"` | Name of the stable ring |
| `hash_key` | `str` | `"actor_id"` | Key to hash for routing: `actor_id`, `user_id`, or `tenant_id` |

### Resolution Order

1. **Explicit context override** — `context.policy_ring` takes priority if set
2. **Deterministic routing** — If `RingRoutingConfig.enabled`:
   - Compute SHA-256 hash of `environment:hash_key_value`
   - Convert first 8 hex chars to integer (0-4294967295)
   - Normalize to 0-100 percentage: `(hash_int / 4294967295) * 100`
   - If percentage < `canary_percentage`, route to `canary_ring`; else route to `stable_ring`
3. **Store default ring** — If ring store configured, look up the ring with `is_default=True`
4. **Configured fallback** — Fall back to `default_ring` parameter (default: `"stable"`)

### `simulate_routing(environment, actor_id, user_id, tenant_id) -> RoutingSimulationResult`

Returns full routing info for debugging without side effects:

| Field | Type | Description |
|-------|------|-------------|
| `environment` | `str` | Target environment |
| `actor_id` | `str \| None` | Actor ID used for hashing |
| `user_id` | `str \| None` | User ID used for hashing |
| `tenant_id` | `str \| None` | Tenant ID used for hashing |
| `hash_value` | `str` | SHA-256 hex digest of the hash input |
| `hash_percentage` | `float` | Derived percentage (0-100) |
| `routed_ring` | `str` | Which ring the request routes to |
| `canary_percentage` | `int` | Configured canary percentage |
| `canary_ring` | `str` | Canary ring name |
| `stable_ring` | `str` | Stable ring name |

**Deterministic guarantee:** The same `environment:key` pair always routes to
the same ring, even across process restarts. This is achieved by using SHA-256
hashing rather than random assignment.

## Runtime Metadata

AppRunner records policy metadata in `AppRunResult.metadata`:

| Key | Source | Description |
|-----|--------|-------------|
| `policy_environment` | `RunContext.policy_environment` | Environment used for resolution |
| `policy_ring` | Resolved ring name | Ring the request routed to |
| `policy_bundle_id` | `PolicyBundle.bundle_id` | ID of the resolved bundle |
| `policy_config_hash` | `PolicyBundle.config_hash` | SHA-256 hash of the bundle config |

This metadata is available for logging, metrics, and debugging without
requiring access to the resolver or stores.

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
    environments:
      type: sqlite
      path: .agent_app/policy_environments.db
    rings:
      type: sqlite
      path: .agent_app/policy_release_rings.db
    ring_assignments:
      type: sqlite
      path: .agent_app/policy_ring_activation_assignments.db
    change_events:                      # Phase 34
      type: sqlite
      path: .agent_app/policy_change_events.db
      strict: false                     # strict mode: event failures propagate
    reload:                             # Phase 34
      enabled: true
    routing:                            # Phase 34
      enabled: false
      canary_percentage: 0
      canary_ring: canary
      stable_ring: stable
      hash_key: actor_id                # actor_id | user_id | tenant_id
    require_promotion_approval: true
    allow_gate_bypass: false
    runtime:
      environment: prod
      require_active_policy: false
      cache_ttl_seconds: 300
      ring: null
    rules:
      - name: safe_default
        max_changed_ratio: 0.10
        max_failed_replays: 0
```

New config sections:
- `change_events` — PolicyChangeEventsConfig with `type`, `path`, and `strict` (default: `None`, backward compatible)
- `reload` — PolicyReloadConfig with `enabled` (default: `None`, backward compatible)
- `routing` — RingRoutingConfig with `enabled`, `canary_percentage`, `canary_ring`, `stable_ring`, `hash_key`

## CLI Commands

```bash
# Request reload
agentapp policy reload request --config agentapp.yaml --environment prod --ring stable --actor-id ops_admin --reason "Refresh after activation"

# Check cache status
agentapp policy reload status --config agentapp.yaml

# List change events
agentapp policy events list --config agentapp.yaml --environment prod --limit 20

# Simulate routing
agentapp policy routing simulate --config agentapp.yaml --environment prod --actor-id user_123
```

All Phase 34 commands support `--config`, `--json` for JSON output, and
`--actor-id` / `--permissions` for RBAC testing.

## Console Pages

| Route | Template | Method | Description |
|-------|----------|--------|-------------|
| `GET /events` | `policy_events.html` | GET | List recent change events with type and environment filters |
| `GET /reload` | `policy_reload.html` | GET | Show cache status and allow reload requests |
| `POST /reload` | `policy_reload.html` | POST | Submit reload request |
| `GET /routing` | `policy_routing.html` | GET | Routing simulator form |
| `POST /routing` | `policy_routing.html` | POST | Simulate routing for given parameters |

The events page supports filtering by environment and event type. The reload
page shows resolver cache status (entry count, keys, TTL) and provides a form
for requesting reloads. The routing simulator shows which ring a request would
route to based on the configured canary percentage and hash key.

## RBAC Permissions (Phase 34 additions)

| Permission | Value | Default |
|-----------|-------|---------|
| `RELOAD_REQUEST` | `policy.reload.request` | Requires grant |
| `RELOAD_VIEW` | `policy.reload.view` | Allowed |
| `EVENT_VIEW` | `policy.event.view` | Allowed |
| `ROUTING_SIMULATE` | `policy.routing.simulate` | Allowed |

`RELOAD_VIEW`, `EVENT_VIEW`, and `ROUTING_SIMULATE` are default-allowed
alongside `BUNDLE_CREATE`, `GATE_RUN`, `ENVIRONMENT_VIEW`, and `RING_VIEW`.
`RELOAD_REQUEST` requires explicit grants in `RunContext.permissions`, consistent
with other mutation operations.

## Audit Events

| Event Type | Trigger |
|-----------|---------|
| `policy.reload.requested` | Manual reload requested via PolicyReloadManager |
| `policy.reload.hook_succeeded` | A reload hook executed successfully |
| `policy.reload.hook_failed` | A reload hook raised an exception |
| `policy.event.emission_failed` | Change event emission failed (non-strict mode) |
| `policy.event.emission_strict_failed` | Change event emission failed (strict mode, propagated) |
| `policy.routing.simulated` | Routing simulation performed |

## Design Decisions

1. **Change events emitted AFTER state changes succeed** — Events record what
   happened, not what is about to happen. This means event emission failures
   cannot corrupt the main state transition (in non-strict mode).

2. **Non-strict event emission default** — Event emission failures are logged
   but do not prevent the policy operation from completing. This prioritizes
   availability over perfect auditability. Strict mode is available when
   auditability is more important.

3. **Resolver cache uses monotonic time for TTL expiration** — `time.monotonic()`
   is used instead of `time.time()` for cache TTL checks, preventing issues
   with system clock adjustments (NTP corrections, leap seconds, manual changes).

4. **Deterministic routing uses SHA-256** — Hash-based routing ensures the same
   `environment:key` pair always routes to the same ring across process restarts.
   This is critical for canary deployments where users must see consistent behavior.

5. **Hook failures captured per-hook, not propagated** — When a hook raises an
   exception, the error is captured in `HookResult` and other hooks continue
   executing. This prevents a single misbehaving hook from blocking the entire
   reload process.

6. **RELOAD_VIEW, EVENT_VIEW, ROUTING_SIMULATE are default-allowed** — These
   are read-only/diagnostic operations with no side effects. Only
   `RELOAD_REQUEST` (which triggers cache invalidation and hook execution)
   requires explicit permission.

7. **Append-only event store** — Events can only be appended, never modified or
   deleted. This ensures a complete audit trail for compliance and debugging.

## Known Limitations

1. **Reload manager is local-process only** — No distributed pub/sub for reload
   notifications across multiple process instances.

2. **SQLite event store is not a distributed event bus** — Events are stored
   locally; no Kafka/RabbitMQ integration.

3. **No background polling daemon** — Resolver cache is not automatically
   refreshed; relies on TTL expiration or explicit reload requests.

4. **No websocket push reload** — No real-time notification to running processes
   when a reload occurs.

5. **No service mesh traffic splitting** — Canary routing is framework-level
   deterministic routing, not infrastructure-level traffic splitting (no Istio
   or Envoy integration).

6. **No automatic rollback based on live metrics** — Canary percentage is
   manually configured; no automatic rollback on error rate spikes.

7. **No multi-region rollout coordination** — Routing configuration is
   per-environment; no coordination across geographic regions.

---

# Phase 35: Multi-Environment Rollout Orchestration

## Overview

**Phase 35** adds multi-environment rollout orchestration to the Phase 34
policy release system, enabling step-by-step rollout plans with dependency
enforcement, gate/eval checks, and approval blocking. It provides:

1. **RolloutPlan Model** — Ordered rollout plans with DRAFT → ACTIVE → COMPLETED/FAILED/CANCELLED lifecycle
2. **RolloutStep Model** — Individual rollout steps with ACTIVATE, ASSIGN_RING, CANARY_EVAL, PROMOTE_RING types
3. **Step Dependencies** — `require_previous_step` enforces sequential execution
4. **Approval Blocking** — `requires_approval` marks step BLOCKED in MVP (no approval resolution flow)
5. **RolloutService** — Orchestrates plan creation, execution, and cancellation
6. **RolloutPlanStore** — Protocol + InMemory + SQLite persistence with factory
7. **Config** — `governance.policy_release.rollouts` for store type and path
8. **CLI Commands** — Full rollout lifecycle via `agentapp policy rollout` subcommands
9. **Console Pages** — Rollout list, detail, create, and action pages
10. **Audit Events** — All rollout operations produce audit events
11. **RBAC Permissions** — 5 rollout-specific permissions

## RolloutPlan Model

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `rollout_id` | `str` | required | Unique ID (`ro_` prefix) |
| `name` | `str` | required | Human-readable plan name |
| `environment` | `str` | required | Target environment |
| `status` | `RolloutPlanStatus` | `DRAFT` | Plan lifecycle status |
| `steps` | `list[RolloutStep]` | required | Ordered list of rollout steps |
| `created_by` | `str` | required | Who created the plan |
| `reason` | `str \| None` | `None` | Why the rollout was created |
| `created_at` | `datetime` | `datetime.now(timezone.utc)` | Creation timestamp |
| `updated_at` | `datetime` | `datetime.now(timezone.utc)` | Last update timestamp |

**Plan lifecycle:**
- **DRAFT** — Created but not yet started; steps can be added or modified
- **ACTIVE** — Currently executing; steps transition through their lifecycle
- **COMPLETED** — All steps succeeded
- **FAILED** — A step failed, halting the rollout
- **CANCELLED** — Manually cancelled by an operator

## RolloutStep Model

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `step_id` | `str` | required | Unique ID (`ros_` prefix) |
| `rollout_id` | `str` | required | Parent rollout plan ID |
| `step_type` | `RolloutStepType` | required | Type of rollout step |
| `environment` | `str` | required | Target environment |
| `ring_name` | `str \| None` | `None` | Target ring (for ASSIGN_RING, CANARY_EVAL, PROMOTE_RING) |
| `bundle_id` | `str \| None` | `None` | Target bundle (for ACTIVATE) |
| `activation_id` | `str \| None` | `None` | Target activation (for ASSIGN_RING, CANARY_EVAL, PROMOTE_RING) |
| `status` | `RolloutStepStatus` | `PENDING` | Step lifecycle status |
| `require_previous_step` | `bool` | `True` | Whether this step depends on the previous step completing first |
| `requires_approval` | `bool` | `False` | Whether this step requires approval before execution |
| `reason` | `str \| None` | `None` | Step-specific reason |
| `error` | `str \| None` | `None` | Error message if the step failed |
| `created_at` | `datetime` | `datetime.now(timezone.utc)` | Creation timestamp |
| `updated_at` | `datetime` | `datetime.now(timezone.utc)` | Last update timestamp |

**Step lifecycle:**
- **PENDING** — Not yet started
- **RUNNING** — Currently executing
- **SUCCEEDED** — Completed successfully
- **FAILED** — Execution failed (error field populated)
- **BLOCKED** — Waiting for approval (MVP: no resolution flow)
- **SKIPPED** — Skipped due to plan failure or cancellation

## RolloutStepType

| Type | Description | Required Fields |
|------|-------------|-----------------|
| `ACTIVATE` | Activate a bundle in an environment | `bundle_id` |
| `ASSIGN_RING` | Assign an activation to a ring | `activation_id`, `ring_name` |
| `CANARY_EVAL` | Run canary evaluation against an activation | `activation_id`, `ring_name` |
| `PROMOTE_RING` | Promote canary ring activation to stable | `ring_name` (from-ring implied as canary) |

## Step Dependencies

Steps can declare `require_previous_step=True` (the default), which enforces
that the previous step must be in `SUCCEEDED` status before this step can run.
If the previous step is `FAILED`, `BLOCKED`, or `SKIPPED`, the dependent step
cannot execute and the plan transitions to `FAILED`.

When `require_previous_step=False`, the step can run in parallel with the
previous step (though the current implementation runs steps sequentially).

## Approval Blocking

Steps marked with `requires_approval=True` transition to `BLOCKED` status
when `run_next_step()` encounters them. In the MVP, there is no approval
resolution flow — a BLOCKED step cannot be unblocked through the API or CLI.
The plan effectively stalls at the blocked step. This is by design: the
approval gate forces manual intervention (e.g., external review, manual
promotion) before proceeding.

To unblock, operators must cancel the plan and create a new one without
the approval-required step, or modify the step before starting the plan.

## RolloutService

Core orchestrator that manages rollout plan lifecycle.

### `create_plan(name, environment, steps, created_by, reason=None, context=None)`

Creates a new DRAFT rollout plan with the specified steps. Requires
`ROLLOUT_CREATE` permission. Emits `policy.rollout.created` audit event
and `ROLLOUT_CREATED` change event.

### `start_plan(rollout_id, context=None)`

Transitions a DRAFT plan to ACTIVE. Requires `ROLLOUT_START` permission.
Emits `policy.rollout.started` audit event and `ROLLOUT_STARTED` change event.

### `run_next_step(rollout_id, context=None)`

Executes the next available step in an ACTIVE plan. Requires `ROLLOUT_EXECUTE`
permission. Steps with `require_previous_step=True` wait for the previous step
to succeed. Steps with `requires_approval=True` transition to BLOCKED.

Emits `policy.rollout.step_succeeded` or `policy.rollout.step_failed` audit
events, and `STEP_SUCCEEDED` change event on success.

### `run_all_available(rollout_id, context=None)`

Runs all available steps in sequence until a step fails, is blocked, or no
more steps are available. Returns the list of executed step results.

### `cancel_plan(rollout_id, context=None)`

Transitions an ACTIVE plan to CANCELLED. Remaining PENDING steps are set to
SKIPPED. Requires `ROLLOUT_CANCEL` permission. Emits
`policy.rollout.cancelled` audit event and `CANCELLED` change event.

## Step Execution Behavior

### ACTIVATE

Activates a bundle in the specified environment by calling
`PolicyReleaseService.execute_promotion()`. The `bundle_id` and
`created_by` are passed as parameters.

### ASSIGN_RING

Assigns an activation to a ring by calling
`PolicyReleaseService.assign_activation_to_ring()`. The `activation_id`,
`ring_name`, `environment`, and `created_by` are passed as parameters.

### CANARY_EVAL

Runs a canary evaluation by invoking `CanaryEvalRunner.run_for_activation()`.
The `activation_id`, `environment`, and `ring_name` are passed. If the eval
fails (not passed), the step is marked FAILED.

### PROMOTE_RING

Promotes the canary ring activation to the stable ring by calling
`PolicyReleaseService.promote_canary_to_stable()`. The `ring_name` is used
as the `from_ring`, and the default stable ring is used as `to_ring`.

## RolloutPlanStore

Protocol + InMemory + SQLite persistence, following the same pattern as
all other stores in the framework:

| Method | Signature | Description |
|--------|-----------|-------------|
| `create()` | `create(plan: RolloutPlan) -> RolloutPlan` | Create a new rollout plan |
| `get()` | `get(rollout_id: str) -> RolloutPlan \| None` | Get by rollout ID |
| `list()` | `list(environment: str \| None = None, status: str \| None = None) -> list[RolloutPlan]` | List with optional filters |
| `update()` | `update(plan: RolloutPlan) -> RolloutPlan` | Update an existing plan |
| `delete()` | `delete(rollout_id: str) -> None` | Delete a plan |

**Factory:** `create_rollout_plan_store(store_type, db_path)` supports `"memory"`
and `"sqlite"` types.

**SQLite schema:** `policy_rollout_plans` table with `rollout_id` as primary key.

## Config Schema (Phase 35 additions)

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
    rings:
      type: sqlite
      path: .agent_app/policy_release_rings.db
    ring_assignments:
      type: sqlite
      path: .agent_app/policy_ring_activation_assignments.db
    change_events:
      type: sqlite
      path: .agent_app/policy_change_events.db
      strict: false
    reload:
      enabled: true
    routing:
      enabled: false
      canary_percentage: 0
      canary_ring: canary
      stable_ring: stable
      hash_key: actor_id
    rollouts:                            # Phase 35
      type: sqlite
      path: .agent_app/policy_rollouts.db
    require_promotion_approval: true
    allow_gate_bypass: false
    runtime:
      environment: prod
      require_active_policy: false
      cache_ttl_seconds: 300
      ring: null
    rules:
      - name: safe_default
        max_changed_ratio: 0.10
        max_failed_replays: 0
```

New config section:
- `rollouts` — Optional rollout plan store config (default: `None`, backward compatible)

## CLI Commands

```bash
# Create a rollout plan
agentapp policy rollout create --config <path> --name <name> --environment <env> \
  --steps <json> --created-by <who> [--reason <text>] [--actor-id <id>] [--permissions <list>]

# List rollout plans
agentapp policy rollout list --config <path> [--environment <env>] [--status <status>] [--json]

# Show rollout plan details
agentapp policy rollout show --config <path> --rollout-id <id> [--json]

# Start a rollout plan (DRAFT → ACTIVE)
agentapp policy rollout start --config <path> --rollout-id <id> \
  --actor-id <id> [--permissions <list>]

# Run the next available step
agentapp policy rollout run-next --config <path> --rollout-id <id> \
  --actor-id <id> [--permissions <list>]

# Run all available steps
agentapp policy rollout run-all --config <path> --rollout-id <id> \
  --actor-id <id> [--permissions <list>]

# Cancel a rollout plan
agentapp policy rollout cancel --config <path> --rollout-id <id> \
  --actor-id <id> [--permissions <list>]
```

The `--steps` parameter accepts a JSON array of step definitions:

```json
[
  {"step_type": "ACTIVATE", "bundle_id": "pb_abc123", "require_previous_step": true},
  {"step_type": "ASSIGN_RING", "ring_name": "canary", "activation_id": "pa_def456", "requires_approval": true},
  {"step_type": "CANARY_EVAL", "ring_name": "canary", "activation_id": "pa_def456"},
  {"step_type": "PROMOTE_RING", "ring_name": "canary"}
]
```

All rollout commands support `--actor-id` and `--permissions` for RBAC testing,
consistent with Phase 30+ commands.

## Console Workflow

| Route | Template | Method | Description |
|-------|----------|--------|-------------|
| `GET /rollouts` | `policy_rollouts.html` | GET | List all rollout plans with status badges |
| `GET /rollouts/{rollout_id}` | `policy_rollout_detail.html` | GET | Rollout detail with step list and status |
| `GET /rollouts/create` | `policy_rollout_create.html` | GET | Create rollout plan form |
| `POST /rollouts` | `policy_rollouts.html` | POST | Create a new rollout plan |
| `POST /rollouts/{rollout_id}/start` | `policy_rollout_detail.html` | POST | Start a rollout plan |
| `POST /rollouts/{rollout_id}/run-next` | `policy_rollout_detail.html` | POST | Run the next step |
| `POST /rollouts/{rollout_id}/run-all` | `policy_rollout_detail.html` | POST | Run all available steps |
| `POST /rollouts/{rollout_id}/cancel` | `policy_rollout_detail.html` | POST | Cancel a rollout plan |

Console actions require a `RolloutService` with appropriate permissions
passed via form data.

## Failure and Blocked Behavior

### Step Failure

When a step fails during execution:
1. The step status is set to `FAILED` with the error message
2. The plan status transitions to `FAILED`
3. All remaining PENDING steps are set to `SKIPPED`
4. `policy.rollout.step_failed` and `policy.rollout.failed` audit events are emitted
5. No further steps can be executed on the plan

### Step Blocked

When a step requires approval (`requires_approval=True`):
1. The step status is set to `BLOCKED`
2. The plan remains `ACTIVE` but cannot progress past the blocked step
3. `policy.rollout.step_blocked` audit event is emitted
4. In the MVP, there is no approval resolution flow — the plan stalls

### Plan Cancellation

When a plan is cancelled:
1. The plan status transitions to `CANCELLED`
2. All remaining PENDING steps are set to `SKIPPED`
3. Running steps continue to completion (no interruption)
4. `policy.rollout.cancelled` audit event is emitted

## Audit Events

| Event Type | Trigger |
|-----------|---------|
| `policy.rollout.created` | New rollout plan created |
| `policy.rollout.started` | Plan transitioned from DRAFT to ACTIVE |
| `policy.rollout.step_succeeded` | A rollout step completed successfully |
| `policy.rollout.step_failed` | A rollout step failed |
| `policy.rollout.step_blocked` | A step requires approval (BLOCKED) |
| `policy.rollout.completed` | All steps succeeded, plan COMPLETED |
| `policy.rollout.failed` | A step failed, plan FAILED |
| `policy.rollout.cancelled` | Plan manually cancelled |

## Change Events

| Event Type | Trigger |
|-----------|---------|
| `ROLLOUT_CREATED` | New rollout plan created |
| `ROLLOUT_STARTED` | Plan started (DRAFT → ACTIVE) |
| `STEP_SUCCEEDED` | A rollout step succeeded |
| `COMPLETED` | Plan completed successfully |
| `FAILED` | Plan failed due to step failure |
| `CANCELLED` | Plan cancelled by operator |

## RBAC Permissions (Phase 35 additions)

| Permission | Value | Default |
|-----------|-------|---------|
| `ROLLOUT_CREATE` | `policy.rollout.create` | Requires grant |
| `ROLLOUT_START` | `policy.rollout.start` | Requires grant |
| `ROLLOUT_EXECUTE` | `policy.rollout.execute` | Requires grant |
| `ROLLOUT_CANCEL` | `policy.rollout.cancel` | Requires grant |
| `ROLLOUT_VIEW` | `policy.rollout.view` | Allowed |

`ROLLOUT_VIEW` is default-allowed alongside `BUNDLE_CREATE`, `GATE_RUN`,
`ENVIRONMENT_VIEW`, `RING_VIEW`, `RELOAD_VIEW`, `EVENT_VIEW`, and
`ROUTING_SIMULATE`. All other rollout permissions require explicit grants
in `RunContext.permissions`, consistent with other destructive operations.

## Known Limitations

1. **No background scheduler** — Rollout steps are executed on-demand via CLI or
   API calls. There is no automatic step scheduling or timer-based progression.

2. **No external CI/CD integration** — Rollout steps execute within the
   framework. There is no integration with Jenkins, GitHub Actions, or other
   CI/CD platforms.

3. **Step approval is MVP/block-only** — The `requires_approval` flag marks a
   step as BLOCKED, but there is no approval resolution flow. Operators must
   cancel and recreate the plan without the approval step.

4. **No automatic rollback based on live metrics** — If a canary eval fails,
   the step is marked FAILED but there is no automatic rollback of previous
   steps. Rollback must be performed manually.

5. **No distributed execution lock** — Rollout execution is local to a single
   process. Concurrent execution of the same plan from multiple processes is
   not protected by a distributed lock.

6. **Rollout execution is local command/API driven** — Steps execute
   synchronously within the calling process. There is no background worker,
   task queue, or distributed execution engine.

---

# Phase 36: Rollout Approval Workflow

## Overview

**Phase 36** upgrades the Phase 35 rollout system from MVP approval blocking
(stall-only) to a full approval resolution workflow. It provides:

1. **RolloutStepApproval Model** — Approval records with PENDING → APPROVED/REJECTED/CANCELLED lifecycle
2. **RolloutStepApprovalStore** — Protocol + InMemory + SQLite persistence with factory
3. **RolloutService Approval APIs** — `request_step_approval()`, `approve_step()`, `reject_step()`, `list_step_approvals()`
4. **Automatic Approval Creation** — `requires_approval` steps in `run_next_step()` automatically create PENDING approvals and set step to BLOCKED
5. **Approval Resolution Flow** — Approved steps unblock and execute normally; rejected approvals fail the step and plan
6. **RBAC Permissions** — 4 rollout approval permissions (ROLLOUT_APPROVAL_REQUEST, ROLLOUT_APPROVAL_APPROVE, ROLLOUT_APPROVAL_REJECT, ROLLOUT_APPROVAL_VIEW)
7. **Change Events** — ROLLOUT_APPROVAL_REQUESTED, ROLLOUT_APPROVAL_APPROVED, ROLLOUT_APPROVAL_REJECTED
8. **Approval Reason Policy** — `RolloutApprovalConfig.require_reason` enforces non-empty reason on approve/reject
9. **CLI Commands** — `agentapp policy rollout approval list/request/approve/reject`
10. **Console Pages** — Approval list, detail, request, approve, reject; rollout detail shows approval state

## RolloutStepApproval Model

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `approval_id` | `str` | required | Unique ID (`rsa_` prefix) |
| `rollout_id` | `str` | required | Parent rollout plan ID |
| `step_id` | `str` | required | Target rollout step ID |
| `status` | `RolloutStepApprovalStatus` | `PENDING` | Approval lifecycle status |
| `requested_by` | `str` | required | Who requested the approval |
| `reason` | `str \| None` | `None` | Why approval was requested |
| `approved_by` | `str \| None` | `None` | Who approved |
| `approved_reason` | `str \| None` | `None` | Why it was approved |
| `rejected_by` | `str \| None` | `None` | Who rejected |
| `rejected_reason` | `str \| None` | `None` | Why it was rejected |
| `created_at` | `datetime` | `datetime.now(timezone.utc)` | Creation timestamp |
| `resolved_at` | `datetime \| None` | `None` | Resolution timestamp |

**Approval lifecycle:**
- **PENDING** — Awaiting approval; step is BLOCKED
- **APPROVED** — Approval granted; step unblocks and can execute
- **REJECTED** — Approval denied; step and plan transition to FAILED
- **CANCELLED** — Plan cancelled; pending approvals are cancelled

## RolloutStepApprovalStore

Protocol + InMemory + SQLite persistence, following the same pattern as
all other stores in the framework:

| Method | Signature | Description |
|--------|-----------|-------------|
| `create()` | `create(approval: RolloutStepApproval) -> RolloutStepApproval` | Create a new approval record |
| `get()` | `get(approval_id: str) -> RolloutStepApproval \| None` | Get by approval ID |
| `list()` | `list(rollout_id=None, step_id=None, status=None) -> list[RolloutStepApproval]` | List with optional filters |
| `update()` | `update(approval: RolloutStepApproval) -> RolloutStepApproval` | Update an existing approval |
| `delete()` | `delete(approval_id: str) -> None` | Delete an approval record |

**Factory:** `create_rollout_step_approval_store(store_type, db_path)` supports
`"memory"` and `"sqlite"` types.

**SQLite schema:** `policy_rollout_step_approvals` table with `approval_id`
as primary key and indexes on `rollout_id` and `step_id`.

## Approval Lifecycle Flow

### Blocked → Approved → Pending → Executed

When a rollout step has `requires_approval=True`:

1. **`run_next_step()` encounters the step** — Automatically creates a
   `RolloutStepApproval` with status PENDING via `request_step_approval()`
2. **Step transitions to BLOCKED** — The step status is set to BLOCKED and
   the plan remains ACTIVE but cannot progress past this step
3. **Approver reviews** — Via CLI or console, the approver calls
   `approve_step()` or `reject_step()`
4. **If approved** — Approval status transitions to APPROVED; step status
   transitions back to PENDING; `run_next_step()` can now execute the step
5. **If rejected** — Approval status transitions to REJECTED; step status
   transitions to FAILED; plan status transitions to FAILED; remaining
   PENDING steps are set to SKIPPED

### Rejection Failure Behavior

When an approval is rejected:

1. The approval record is updated with `rejected_by`, `rejected_reason`,
   and `resolved_at`
2. The step status transitions to FAILED with error message:
   `"Step approval rejected by {rejected_by}: {rejected_reason}"`
3. The plan status transitions to FAILED
4. All remaining PENDING steps are set to SKIPPED
5. Audit events are emitted: `policy.rollout.approval_rejected` and
   `policy.rollout.step_failed`
6. No further steps can be executed on the plan

## RolloutService Approval APIs

### `request_step_approval(rollout_id, step_id, requested_by, context=None, reason=None)`

Creates a PENDING approval for a rollout step. Requires
`ROLLOUT_APPROVAL_REQUEST` permission. Validates that the step exists
and belongs to the rollout. Emits `ROLLOUT_APPROVAL_REQUESTED` change
event and `policy.rollout.approval_requested` audit event.

### `approve_step(approval_id, approved_by, context=None, reason=None)`

Transitions an approval from PENDING to APPROVED. Requires
`ROLLOUT_APPROVAL_APPROVE` permission. If `require_reason=True` in
`RolloutApprovalConfig`, a non-empty reason is mandatory. Sets the step
status back to PENDING so `run_next_step()` can execute it. Emits
`ROLLOUT_APPROVAL_APPROVED` change event and `policy.rollout.approval_approved`
audit event.

### `reject_step(approval_id, rejected_by, context=None, reason=None)`

Transitions an approval from PENDING to REJECTED. Requires
`ROLLOUT_APPROVAL_REJECT` permission. If `require_reason=True` in
`RolloutApprovalConfig`, a non-empty reason is mandatory. Sets the step
status to FAILED and the plan status to FAILED. Emits
`ROLLOUT_APPROVAL_REJECTED` change event and `policy.rollout.approval_rejected`
audit event.

### `list_step_approvals(rollout_id=None, step_id=None, status=None)`

Lists approval records with optional filters. Requires
`ROLLOUT_APPROVAL_VIEW` permission (default-allowed).

## RolloutApprovalConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `require_reason` | `bool` | `False` | Whether approve/reject require a non-empty reason |

When `require_reason=True`, calling `approve_step()` or `reject_step()`
without a non-empty `reason` parameter raises `ValueError`. This ensures
audit trails capture justification for every approval decision.

## RBAC Permissions (Phase 36 additions)

| Permission | Value | Default |
|-----------|-------|---------|
| `ROLLOUT_APPROVAL_REQUEST` | `policy.rollout.approval.request` | Requires grant |
| `ROLLOUT_APPROVAL_APPROVE` | `policy.rollout.approval.approve` | Requires grant |
| `ROLLOUT_APPROVAL_REJECT` | `policy.rollout.approval.reject` | Requires grant |
| `ROLLOUT_APPROVAL_VIEW` | `policy.rollout.approval.view` | Allowed |

`ROLLOUT_APPROVAL_VIEW` is default-allowed alongside `ROLLOUT_VIEW` and
other read-only permissions. All other approval permissions require explicit
grants in `RunContext.permissions`, consistent with other mutation operations.

## Change Events (Phase 36 additions)

| Event Type | Trigger |
|-----------|---------|
| `ROLLOUT_APPROVAL_REQUESTED` | Approval requested for a rollout step |
| `ROLLOUT_APPROVAL_APPROVED` | Step approval granted |
| `ROLLOUT_APPROVAL_REJECTED` | Step approval denied |

## Config Schema (Phase 36 additions)

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
    rings:
      type: sqlite
      path: .agent_app/policy_release_rings.db
    ring_assignments:
      type: sqlite
      path: .agent_app/policy_ring_activation_assignments.db
    change_events:
      type: sqlite
      path: .agent_app/policy_change_events.db
      strict: false
    reload:
      enabled: true
    routing:
      enabled: false
      canary_percentage: 0
      canary_ring: canary
      stable_ring: stable
      hash_key: actor_id
    rollouts:
      type: sqlite
      path: .agent_app/policy_rollouts.db
    rollout_approvals:                    # Phase 36
      type: sqlite
      path: .agent_app/policy_rollout_approvals.db
    rollout_approval_config:              # Phase 36
      require_reason: false
    require_promotion_approval: true
    allow_gate_bypass: false
    runtime:
      environment: prod
      require_active_policy: false
      cache_ttl_seconds: 300
      ring: null
    rules:
      - name: safe_default
        max_changed_ratio: 0.10
        max_failed_replays: 0
```

New config sections:
- `rollout_approvals` — Optional rollout step approval store config (default: `None`, backward compatible)
- `rollout_approval_config` — `RolloutApprovalConfig` with `require_reason` flag

## CLI Commands (Phase 36 additions)

```bash
# List approvals for a rollout
agentapp policy rollout approval list --config <path> --rollout-id <id> [--status <status>] [--json]

# Request approval for a specific step
agentapp policy rollout approval request --config <path> --rollout-id <id> \
  --step-id <id> --requested-by <who> [--reason <text>] \
  [--actor-id <id>] [--permissions <list>]

# Approve a pending approval
agentapp policy rollout approval approve --config <path> --approval-id <id> \
  --approved-by <who> [--reason <text>] \
  [--actor-id <id>] [--permissions <list>]

# Reject a pending approval
agentapp policy rollout approval reject --config <path> --approval-id <id> \
  --rejected-by <who> [--reason <text>] \
  [--actor-id <id>] [--permissions <list>]
```

All approval commands support `--actor-id` and `--permissions` for RBAC
testing, consistent with Phase 30+ commands.

## Console Pages (Phase 36 additions)

| Route | Template | Method | Description |
|-------|----------|--------|-------------|
| `GET /rollouts/{rollout_id}/approvals` | `policy_rollout_approvals.html` | GET | List approvals for a rollout |
| `GET /rollouts/{rollout_id}/approvals/{approval_id}` | `policy_rollout_approval_detail.html` | GET | Approval detail with action forms |
| `POST /rollouts/{rollout_id}/approvals` | `policy_rollout_approvals.html` | POST | Request new approval |
| `POST /rollouts/{rollout_id}/approvals/{approval_id}/approve` | `policy_rollout_approval_detail.html` | POST | Approve a pending approval |
| `POST /rollouts/{rollout_id}/approvals/{approval_id}/reject` | `policy_rollout_approval_detail.html` | POST | Reject a pending approval |

The rollout detail page (`/rollouts/{rollout_id}`) now shows approval
state for BLOCKED steps, including the approval ID, requested_by, and
status. This provides visibility into which steps are blocked and why.

Console actions require a `RolloutService` with appropriate permissions
passed via form data.

## Audit Events (Phase 36 additions)

| Event Type | Trigger |
|-----------|---------|
| `policy.rollout.approval_requested` | Approval requested for a rollout step |
| `policy.rollout.approval_approved` | Step approval granted |
| `policy.rollout.approval_rejected` | Step approval denied |
| `policy.rollout.approval_require_reason` | Approve/reject called without required reason |

## Design Decisions

1. **Automatic approval creation in run_next_step** — When `run_next_step()`
   encounters a step with `requires_approval=True`, it automatically creates
   a PENDING approval and sets the step to BLOCKED. This replaces the Phase 35
   MVP behavior where BLOCKED steps stalled with no resolution path.

2. **Approved steps return to PENDING** — When an approval is approved, the
   step status transitions from BLOCKED back to PENDING, making it eligible
   for normal execution by `run_next_step()`. This reuses the existing step
   execution logic rather than introducing a separate approved-execution path.

3. **Rejection fails the plan** — Rejected approvals cause the step to fail
   and the plan to transition to FAILED, consistent with Phase 35 step failure
   behavior. This prevents silent continuation after denial.

4. **Separate approval store** — Approvals are stored in a dedicated
   `RolloutStepApprovalStore` rather than embedded in the rollout plan. This
   enables independent lifecycle management, querying, and persistence.

5. **ROLLOUT_APPROVAL_VIEW is default-allowed** — Viewing approval state is
   a read-only operation, consistent with `ROLLOUT_VIEW` and other view
   permissions. Request, approve, and reject require explicit grants.

6. **require_reason is opt-in** — The `require_reason` config defaults to
   `False` for backward compatibility. When enabled, it enforces non-empty
   reason strings on approve/reject, ensuring audit trail completeness.

7. **Backward-compatible optional store** — `rollout_approvals` store config
   defaults to `None`. Existing Phase 35 configs without an approval store
   section continue to work; steps with `requires_approval=True` fall back
   to the Phase 35 MVP block-only behavior.

## Known Limitations

1. **No multi-party approval** — Each step requires only a single approval.
   There is no support for requiring N-of-M approvers or approval chains.

2. **No separation-of-duties enforcement** — The same user who requested an
   approval can also approve it. There is no constraint preventing
   self-approval.

3. **No external identity integration** — Approval identities are simple
   strings. There is no integration with LDAP, SAML, OIDC, or other
   identity providers for approval authorization.

4. **No notification system** — When an approval is requested, there is no
   email, Slack, webhook, or other notification to approvers. Operators
   must poll the approval list or check the console.

5. **No approval expiration** — Pending approvals remain PENDING indefinitely.
   There is no TTL or auto-expiration mechanism for stale approvals.

6. **No cryptographic signing** — Approval decisions are not cryptographically
   signed. Audit trail integrity relies on store-level access controls.

7. **Step approval is rollout-local only** — Approvals are scoped to a single
   rollout plan. There is no cross-rollout approval sharing or templating.

---

## Phase 37: Separation of Duties and Multi-Approver Approval Policies

Phase 37 extends the Phase 36 single-approval lifecycle with configurable approval policies:

### Approval Policies

Rollout approvals can now use **SINGLE** (default, one approver) or **QUORUM** (multiple approvers) policies:

```yaml
governance:
  policy_release:
    rollouts:
      approvals:
        type: sqlite
        path: .agent_app/policy_rollout_approvals.db
        policy:
          policy_type: quorum
          required_approvals: 2
          allowed_approver_roles:
            - release_reviewer
          allowed_approver_permissions:
            - policy.rollout.approval.approve
          prohibit_requester_approval: true
          prohibit_creator_approval: true
          expires_after_seconds: 86400
```

### Multi-Approver Quorum

With `policy_type: quorum` and `required_approvals: N`:
- An approval stays PENDING until N independent approve decisions are recorded
- Any reject immediately rejects the approval
- The step only unblocks from BLOCKED → PENDING when the approval is fully APPROVED

### Separation of Duties

- **prohibit_requester_approval**: The person who requested the approval cannot approve it themselves
- **prohibit_creator_approval**: The person who created the rollout plan cannot approve steps in it
- **prohibit_step_actor_approval**: The person who would execute the step cannot approve it

### Role and Permission Constraints

- **allowed_approver_roles**: Only actors with at least one of these roles can approve
- **allowed_approver_permissions**: Only actors with at least one of these permissions can approve
- Roles and permissions are supplied via `RunContext.roles` and `RunContext.permissions`, or via the CLI `--roles` flag

### Approval Expiration

- **expires_after_seconds**: If set, approvals automatically expire after this duration
- Expired approvals cannot receive decisions
- Use `agentapp policy rollout approval expire --config agentapp.yaml` to manually expire past-due approvals
- Expiration is checked at decision time and via the explicit expire command

### CLI Support

```bash
# Approve with roles
agentapp policy rollout approval approve \
  --config agentapp.yaml \
  --approval-id rsa_xxx \
  --actor-id reviewer1 \
  --roles release_reviewer \
  --permissions policy.rollout.approval.approve \
  --reason "Looks good"

# Expire past-due approvals
agentapp policy rollout approval expire \
  --config agentapp.yaml \
  --actor-id admin
```

### Console Support

The approval detail page now shows:
- Policy type and required approvals
- Current approval progress (X/Y)
- Decisions table with actor, type, reason, roles, timestamp
- Expiration time
- Pending message with remaining approval count

### Audit Events

New event types:
- `policy.rollout.approval.decision_recorded` — a decision was recorded (approve or reject)
- `policy.rollout.approval.quorum_reached` — quorum threshold was met
- `policy.rollout.approval.expired` — an approval expired
- `policy.rollout.approval.policy_denied` — a policy constraint blocked a decision

### Known Limitations

- Roles are supplied via RunContext / CLI, not from external identity providers
- No external directory integration (LDAP, OIDC, etc.)
- No Slack/Jira notifications
- No cryptographic signatures on decisions
- No delegated approval or approval groups
- No recurring scheduled expiration worker; expiration is checked during action or explicit expire command

---

## Phase 38: Runtime Policy Enforcement Points and Unified Approval Governance

Phase 38 extends approval policy enforcement into runtime execution paths so that approval and separation-of-duties controls are enforced consistently across tool execution, approval resume, and rollout approvals.

### Runtime Policy Rules

Runtime policy rules are configurable enforcement rules that are evaluated before tool execution and approval resume. Each rule specifies:

- **action_type**: The action being governed (e.g., `tool.execute`, `tool.resume`)
- **effect**: What happens when the rule matches (`allow`, `deny`, `require_approval`)
- **tool_name/risk_level**: Optional matching criteria
- **required_permissions/required_roles**: RBAC constraints
- **approval_policy**: Optional quorum/separation-of-duties policy for `require_approval` effect

```yaml
governance:
  runtime_policies:
    type: memory
    rules:
      - name: require_quorum_for_refunds
        action_type: tool.execute
        effect: require_approval
        tool_name: refund.request
        required_permissions:
          - refund:create
        approval_policy:
          policy_type: quorum
          required_approvals: 2
          allowed_approver_roles:
            - finance_reviewer
          prohibit_requester_approval: true
          expires_after_seconds: 3600

      - name: deny_dangerous_delete
        action_type: tool.execute
        effect: deny
        tool_name: data.delete
        reason: "Deletion is disabled in this environment"
```

### Policy Enforcement Points

When a runtime policy enforcement service is configured, it is checked:

1. **Before tool execution** (ToolExecutor.execute) — after permission check, before approval gate
2. **Before approval resume** (ApprovalResumeService.approve_and_resume) — after TTL check, before policy engine

If no matching rule exists, the action is ALLOWED (preserves existing behavior).

### ToolExecutor Enforcement

When a runtime policy rule matches:
- **DENY** → tool execution returns FAILED with `policy_enforcement_denied`
- **REQUIRE_APPROVAL** → tool execution returns INTERRUPTED with an approval request
- **ALLOW** → continues to existing approval gate

If both ToolSpec.requires_approval and a runtime policy REQUIRE_APPROVAL trigger, only one approval is created (ToolSpec takes precedence to avoid duplicates).

### Resume Enforcement

Before resuming an approved tool/action:
- If policy now DENIES → resume returns failed
- If policy now requires APPROVAL → resume returns interrupted
- If policy still ALLOWS → resume continues

### CLI Commands

```bash
# List runtime policy rules
agentapp policy runtime list --config agentapp.yaml

# Create a rule
agentapp policy runtime create \
  --config agentapp.yaml \
  --name require_quorum_for_refunds \
  --action-type tool.execute \
  --effect require_approval \
  --tool-name refund.request \
  --actor-id admin \
  --permissions policy.runtime.create

# Enable/disable rules
agentapp policy runtime enable --config agentapp.yaml --rule-id rpr_xxx
agentapp policy runtime disable --config agentapp.yaml --rule-id rpr_xxx

# Evaluate a policy decision
agentapp policy runtime evaluate \
  --config agentapp.yaml \
  --action-type tool.execute \
  --tool-name refund.request \
  --actor-id user_123 \
  --roles finance_reviewer \
  --permissions refund:create
```

### Console Pages

- **Runtime Rules** (`/policy-console/runtime-rules`) — list all rules with enable/disable
- **Rule Detail** (`/policy-console/runtime-rules/{rule_id}`) — full rule details
- **Runtime Evaluate** (`/policy-console/runtime-evaluate`) — interactive policy evaluation

### Audit Events

New event types:
- `policy.runtime.enforcement.allowed` — action allowed by runtime policy
- `policy.runtime.enforcement.denied` — action denied by runtime policy
- `policy.runtime.enforcement.approval_required` — action requires approval per runtime policy
- `policy.runtime.enforcement.error` — evaluator error during enforcement
- `policy.runtime.rule.created/created/enabled/disabled` — rule lifecycle events

### Known Limitations

- Runtime policies are framework-level, not external IAM
- Roles and permissions are supplied through RunContext / CLI, not from external identity providers
- No OPA/Rego integration
- No external identity provider
- No distributed enforcement engine
- No cryptographic signing
- No real OpenAI RunState resume
- Runtime approval quorum may be limited depending on generic approval model chosen

---

## Phase 39: Policy Observability, Analytics, and Compliance Reporting

Phase 39 makes the unified governance model visible through analytics, reports, exports, and dashboards using existing audit events and stores.

### Observability Report

The `PolicyObservabilityService` aggregates enforcement decisions from audit events into a structured report:

```bash
# Generate report
agentapp policy observability report --config agentapp.yaml

# With time window
agentapp policy observability report \
  --config agentapp.yaml \
  --since 2026-06-01T00:00:00Z \
  --until 2026-06-15T23:59:59Z

# JSON output
agentapp policy observability report --config agentapp.yaml --json
```

### Report Contents

- **Total decisions**: Count of all enforcement decisions in the window
- **By status**: allowed, denied, approval_required counts
- **By action type**: Per-action-type breakdown (e.g., tool.execute)
- **By actor**: Per-actor breakdown (who triggered decisions)
- **By tool**: Per-tool breakdown
- **Approval latency**: min/max/average resolution time from rollout approvals
- **Top denials**: Most frequent denial reasons

### Export

```bash
# JSON export
agentapp policy observability export \
  --config agentapp.yaml \
  --format json \
  --output policy_report.json

# CSV export
agentapp policy observability export \
  --config agentapp.yaml \
  --format csv \
  --output policy_report.csv
```

### Console Dashboard

- **Observability** (`/policy-console/observability`) — live dashboard with summary cards and tables
- **Report** (`/policy-console/observability/report`) — filtered report with since/until inputs

### Data Sources

The service reads from:
- **Audit events**: `policy.runtime.enforcement.allowed/denied/approval_required` events from InMemoryAuditLogger or SQLiteAuditLogger
- **Rollout approval store**: For approval latency computation (resolved_at - created_at)
- All interactions are best-effort; missing stores produce partial reports

### Configuration

```yaml
governance:
  policy_observability:
    enabled: true  # default
```

### RBAC

- `policy.observability.view` — default-allowed (view dashboard and reports)
- `policy.observability.export` — requires explicit permission

### Known Limitations

- Reports are generated on demand, not scheduled or persisted
- No external BI integration
- No Prometheus/OpenTelemetry exporter
- Analytics depend on audit event completeness
- CSV export is MVP-level (flat rows with section/key columns)
- No charts beyond basic console tables

---

## Phase 40: Policy Testing, Validation, and Historical Replay

**Version:** v0.28.0
**Status:** Complete

### Overview

Phase 40 adds a policy simulation and validation framework that allows teams to test runtime policy rule changes against historical audit events before deploying them. Teams can answer: "If we enable this new runtime policy rule, what would have changed historically?"

### New Models

| Model | Prefix | Purpose |
|-------|--------|---------|
| PolicySimulationOutcome | — | Enum: UNCHANGED, WOULD_ALLOW, WOULD_DENY, WOULD_REQUIRE_APPROVAL, WOULD_CHANGE, ERROR |
| PolicySimulationCase | psc_ | Single case extracted from audit history |
| PolicySimulationResult | — | Per-case simulation outcome |
| PolicySimulationSummary | — | Aggregate counts |
| PolicySimulationReport | psim_ | Full simulation report |
| PolicyValidationSeverity | — | ERROR, WARNING, INFO |
| PolicyValidationIssue | — | Single validation issue |
| PolicyValidationReport | — | Validation report |

### New Modules

| Module | Responsibility |
|--------|---------------|
| `governance/policy_simulation.py` | Simulation models |
| `runtime/policy_simulation_cases.py` | Audit-to-case extraction |
| `runtime/policy_candidate_store.py` | Isolated candidate policy store builder |
| `runtime/policy_simulation_service.py` | Simulation service (collect, evaluate, report) |
| `runtime/policy_validation.py` | Runtime policy rule validation |

### Key Features

1. **Audit-to-case extraction**: Convert runtime enforcement audit events into simulation cases
2. **Candidate policy stores**: Build isolated InMemoryRuntimePolicyStore for simulation without mutating active rules
3. **Simulation service**: Collect cases from audit, evaluate against candidate rules, produce impact reports
4. **Policy validation**: Check candidate rules for duplicate names, broad rules, conflicting rules, missing approval policies
5. **Export helpers**: JSON and CSV export for simulation and validation reports
6. **CLI commands**: `policy simulation validate`, `policy simulation replay`, `policy simulation export`
7. **Console pages**: Simulation dashboard with validation and replay forms

### Validation Checks

- Duplicate rule names (warning)
- DENY rule with approval_policy (warning)
- REQUIRE_APPROVAL rule without approval_policy (warning)
- Broad rule with no tool_name or risk_level (warning)
- Conflicting rules with same scope but different effects (warning)

### RBAC Permissions

| Permission | Default |
|-----------|---------|
| policy.simulation.run | Requires explicit permission |
| policy.simulation.view | Default allowed |
| policy.simulation.export | Requires explicit permission |

### Audit Events

- `policy.simulation.validation_run`
- `policy.simulation.replay_run`
- `policy.simulation.export_generated`
- `policy.simulation.permission_denied`

### Configuration

```yaml
governance:
  policy_simulation:
    enabled: true
```

### CLI Examples

```bash
# Validate candidate rules
agentapp policy simulation validate \
  --config agentapp.yaml \
  --rules-file candidate_rules.yaml

# Replay historical audit against candidate rules
agentapp policy simulation replay \
  --config agentapp.yaml \
  --rules-file candidate_rules.yaml \
  --since 2026-06-01T00:00:00Z \
  --limit 1000

# Export simulation report
agentapp policy simulation export \
  --config agentapp.yaml \
  --rules-file candidate_rules.yaml \
  --format csv \
  --output simulation_report.csv
```

### Known Limitations

- Simulation uses historical audit events only
- Does not shadow live traffic
- Does not call tools or models
- Depends on audit event completeness
- No external SIEM integration
- Gate integration may be basic or deferred
- Not a formal proof of policy correctness

---

## Phase 41: Policy Gate Integration and Automated Safeguards

**Version:** v0.29.0
**Status:** Complete

### Purpose

Phase 41 builds on the Phase 40 policy simulation framework by adding a
**simulation gate** — a configurable threshold system that automatically
evaluates simulation results against gate rules and blocks policy promotion
when the gate fails. Teams can answer: "Do the simulation metrics pass our
safety thresholds before we promote this policy?"

The simulation gate enables:

1. **Automated safety checks** — Evaluate simulation metrics against configurable
   threshold rules (max denied ratio, max changed ratio, max errors, etc.)
2. **Blocking behavior** — CLI exits non-zero when gate fails, enabling CI/CD
   integration where gate failure blocks deployment
3. **Gate report** — Structured report showing which rules passed/failed with
   detailed metrics
4. **Console integration** — HTML form to run gates and view reports

### New Models

| Model | Purpose |
|-------|---------|
| `SimulationGateInput` | Input model: simulation summary + validation report |
| `SimulationGateRule` | Threshold rule: metric, operator, threshold, required |
| `SimulationGateResult` | Per-rule evaluation: pass/fail, actual value, message |
| `SimulationGateReport` | Full gate report: pass/fail, all rule results |

### Supported Simulation Metrics

The gate evaluator checks the following 12 metrics:

| Metric | Description |
|--------|-------------|
| `simulation.total` | Total simulation cases |
| `simulation.unchanged` | Cases with UNCHANGED outcome |
| `simulation.would_allow` | Cases that would be ALLOWED |
| `simulation.would_deny` | Cases that would be DENIED |
| `simulation.would_require_approval` | Cases requiring approval |
| `simulation.would_change` | Cases with any change |
| `simulation.errors` | Cases that resulted in ERROR |
| `simulation.changed_ratio` | Ratio of changed to total |
| `simulation.denied_ratio` | Ratio of denied to total |
| `simulation.approval_required_ratio` | Ratio of approval-required to total |
| `validation.errors` | Validation errors in candidate rules |
| `validation.warnings` | Validation warnings in candidate rules |

### Gate Rules YAML

Gate rules are defined in a separate YAML file and passed to the gate command
via `--gate-rules-file`:

```yaml
gates:
  - name: max_denied_ratio
    metric: simulation.denied_ratio
    operator: lt
    threshold: 0.05
    required: true

  - name: max_changed_ratio
    metric: simulation.changed_ratio
    operator: lt
    threshold: 0.20
    required: true

  - name: no_errors
    metric: simulation.errors
    operator: eq
    threshold: 0
    required: true

  - name: max_approval_required_ratio
    metric: simulation.approval_required_ratio
    operator: lt
    threshold: 0.10
    required: false

  - name: no_validation_errors
    metric: validation.errors
    operator: eq
    threshold: 0
    required: true
```

**Rule fields:**

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Human-readable rule name |
| `metric` | `str` | One of the 12 supported metrics |
| `operator` | `str` | `lt`, `lte`, `gt`, `gte`, `eq`, `neq` |
| `threshold` | `float` | Threshold value to compare against |
| `required` | `bool` | If true, failure blocks the gate; if false, failure is a warning |

### New Modules

| Module | Responsibility |
|--------|---------------|
| `governance/policy_simulation_gate.py` | SimulationGateInput, simulation_gate_metrics() |
| `runtime/policy_simulation_gate_evaluator.py` | SimulationGateEvaluator |

### Modified Modules

| Module | Change |
|--------|--------|
| `runtime/policy_simulation_service.py` | Added `validate_and_gate()` method |
| `governance/policy_rbac.py` | Added SIMULATION_GATE_RUN, SIMULATION_GATE_VIEW permissions |
| `governance/policy_change_event.py` | Added 4 simulation gate event types |
| `config/schema.py` | Added `gates` list to PolicySimulationConfig |
| `config/loader.py` | Wired SimulationGateEvaluator |
| `cli.py` | Added `policy simulation gate` command |
| `console/router.py` | Added simulation gate routes |
| `adapters/fastapi.py` | Wired simulation_gate_evaluator |

### New Console Templates

| Template | Description |
|----------|-------------|
| `policy_simulation_gate.html` | Gate form page |
| `policy_simulation_gate_report.html` | Gate report page |

### RBAC Permissions

| Permission | Default |
|-----------|---------|
| `policy.simulation.gate.run` | Requires explicit permission |
| `policy.simulation.gate.view` | Default allowed |

### Change Events

| Event Type | Trigger |
|-----------|---------|
| `SIMULATION_GATE_PASSED` | Gate evaluation passed all required rules |
| `SIMULATION_GATE_FAILED` | Gate evaluation failed one or more required rules |
| `SIMULATION_GATE_WARNING` | Gate evaluation passed but with warnings |
| `SIMULATION_GATE_ERROR` | Gate evaluation encountered an error |

### Audit Events

| Event Type | Trigger |
|-----------|---------|
| `policy.simulation.gate_passed` | Gate evaluation passed |
| `policy.simulation.gate_failed` | Gate evaluation failed |
| `policy.simulation.gate_error` | Gate evaluation error |
| `policy.simulation.gate_permission_denied` | RBAC permission denied |

### CLI Examples

```bash
# Gate passes — exit 0
agentapp policy simulation gate \
  --config agentapp.yaml \
  --rules-file candidate.yaml \
  --gate-rules-file gates.yaml

# Gate fails — exit non-zero, shows failed rules
agentapp policy simulation gate \
  --config agentapp.yaml \
  --rules-file candidate.yaml \
  --gate-rules-file strict_gates.yaml

# JSON output
agentapp policy simulation gate \
  --config agentapp.yaml \
  --rules-file candidate.yaml \
  --gate-rules-file gates.yaml \
  --json

# Write report to file
agentapp policy simulation gate \
  --config agentapp.yaml \
  --rules-file candidate.yaml \
  --gate-rules-file gates.yaml \
  --output result.json
```

### Console Workflow

1. Navigate to `/policy-console/simulation-gate`
2. Select candidate rules file and gate rules file
3. Submit the gate evaluation form
4. View the gate report at `/policy-console/simulation-gate/report`
5. Report shows pass/fail status, per-rule results, and metric values

### Blocking Behavior

The `policy simulation gate` CLI command uses process exit codes:

- **Exit 0** — Gate passed (all required rules satisfied)
- **Exit non-zero** — Gate failed (one or more required rules failed)

This enables CI/CD integration where gate failure blocks deployment
pipelines. The `--json` flag outputs a machine-readable report, and
`--output` writes the report to a file for archival or further processing.

### Configuration

```yaml
governance:
  policy_simulation:
    enabled: true
    gates:                         # Phase 41
      - name: max_denied_ratio
        metric: simulation.denied_ratio
        operator: lt
        threshold: 0.05
        required: true
```

The `gates` list in `PolicySimulationConfig` provides default gate rules.
These can be overridden at the CLI level via `--gate-rules-file`.

### Design Decisions

1. **Separate gate rules file** — Gate rules are defined in a separate YAML
   file rather than embedded in the main config. This allows teams to maintain
   different gate strictness levels (e.g., `gates.yaml` for standard,
   `strict_gates.yaml` for production) and swap them at the CLI level.

2. **Required vs non-required rules** — Rules with `required: true` block the
   gate on failure; rules with `required: false` produce warnings. This allows
   teams to track soft thresholds without blocking promotion.

3. **Exit code as blocking mechanism** — Using process exit codes for pass/fail
   enables seamless CI/CD integration. A non-zero exit code naturally blocks
   deployment pipelines without requiring custom integration code.

4. **Gate evaluator is decoupled from simulation service** — The
   `SimulationGateEvaluator` operates on `SimulationGateInput` (summary +
   validation report) rather than directly on the simulation service. This
   allows gate evaluation to be tested independently and reused with different
   simulation sources.

5. **validate_and_gate combines validation + simulation + gate** — The
   `PolicySimulationService.validate_and_gate()` method chains validation,
   simulation, and gate evaluation in a single call, providing a convenient
   one-step safety check for CLI and console workflows.

6. **Default gate rules from config** — The `gates` list in
   `PolicySimulationConfig` provides default gate rules that can be used
   without a separate file. The `--gate-rules-file` flag overrides these
   defaults when provided.

### Known Limitations

1. **Simulation gate uses historical audit, not live shadow traffic** — Gate
   evaluation is based on historical audit events, not real-time traffic
   mirroring. The gate quality depends on the completeness and recency of
   audit coverage.

2. **Gate quality depends on audit coverage** — If the audit log has gaps or
   missing events, the simulation metrics may not accurately reflect the
   impact of the proposed policy change.

3. **Promotion integration is CLI-level (Option A)** — The gate blocks
   promotion at the CLI level (exit code). Users must pass the gate before
   manually promoting. There is no automatic enforcement at the promotion
   service level.

4. **No automatic production rollback** — Gate failure does not trigger
   automatic rollback. Operators must manually roll back if a promoted policy
   causes issues.

5. **No distributed gate execution** — Gate evaluation runs in a single
   process. There is no support for distributed or parallel gate execution
   across multiple instances.

6. **No external risk engine** — Gate rules are simple threshold comparisons.
   There is no integration with external risk scoring engines or ML-based
   anomaly detection.

---

## Phase 42: Policy Release Automation and Simulation Gate Enforcement

### Purpose

Phase 41 added simulation gate evaluation, but enforcement was CLI-level only — users had to manually run the simulation gate before promotion. Phase 42 integrates simulation gate results into the release workflow so promotion and rollout execution can require a passing simulation gate before proceeding.

### Promotion Gate Requirement Lifecycle

A `ReleaseGateRequirement` tracks whether a promotion (or rollout step) needs a passing simulation gate:

1. **REQUIRED** — A requirement is created when `simulation_gate_enforcement.require_for_promotion=true` in config, or manually via CLI/console
2. **SATISFIED** — A passing gate result is attached (via `attach_gate_result` or `run_and_attach_simulation_gate_for_promotion`)
3. **FAILED** — A failing gate result is attached
4. **EXPIRED** — The gate result is older than `max_age_seconds`

### Enforcement Behavior

When `require_simulation_gate_for_promotion=true`:
- `request_promotion()` auto-creates a gate requirement
- `execute_promotion()` checks the requirement before proceeding:
  - REQUIRED → blocked (no gate result attached)
  - FAILED → blocked (gate failed)
  - EXPIRED → blocked (gate result too old)
  - SATISFIED → allowed

### CLI Flow

```bash
# Create a gate requirement
agentapp policy promotion gate require \
  --promotion-id pr_abc123 \
  --max-age-seconds 86400

# Run simulation + gate and attach result
agentapp policy promotion gate run \
  --promotion-id pr_abc123 \
  --rules-file candidate_rules.yaml \
  --gate-rules-file simulation_gates.yaml

# Or attach an existing gate result
agentapp policy promotion gate attach \
  --promotion-id pr_abc123 \
  --gate-result-id pg_xyz789 \
  --simulation-id psim_def456

# Check gate status
agentapp policy promotion gate status \
  --promotion-id pr_abc123

# Execute promotion (blocked if gate not satisfied)
agentapp policy promotion execute pr_abc123 --executed-by admin
```

### Console Flow

Navigate to `/policy-console/promotions/{promotion_id}/gate` to:
- View current gate requirement status
- Require a gate
- Run simulation + gate
- Attach an existing gate result

### Configuration

```yaml
governance:
  policy_release:
    simulation_gate_enforcement:
      require_for_promotion: true
      max_age_seconds: 86400
      requirement_store:
        type: sqlite
        path: .agent_app/policy_release_gate_requirements.db
```

### Relationship to Phase 41

Phase 41 created the `SimulationGateEvaluator` and `PolicySimulationService.validate_and_gate()` for evaluating simulation gates. Phase 42 wraps that evaluation into the `ReleaseGateAutomationService` and enforces the result in the promotion/rollout workflow.

### Known Limitations

- Enforcement is framework-level, not CI/CD-native
- Simulation still uses historical audit, not live traffic
- Gate freshness uses `max_age_seconds`, not external attestation
- No distributed execution lock
- No automatic production rollback
- Rollout integration is MVP-level (step blocking only)
- External CI pipelines must call CLI/API explicitly

---

## Phase 43: Policy Rollout Automation with Simulation Gates

> **Phase 43:** Implemented — Automated simulation gate evaluation per rollout step

### Purpose

Phase 42 added promotion-level simulation gate enforcement and MVP-level rollout step blocking. Phase 43 upgrades rollout execution from:

```text
Run rollout step → block if simulation gate missing
```

to:

```text
Run rollout step → automatically run simulation gate if configured → attach result → decide block/fail/skip/continue → execute step
```

### Gate Modes

Each rollout step can configure its gate automation mode:

| Mode | Behavior |
|------|----------|
| `DISABLED` | No gate automation. Step executes normally. |
| `MANUAL` | Gate must be explicitly satisfied before step can execute. Missing/failed/expired gates block the step. |
| `AUTO` | Simulation gate is automatically run before step execution. On failure, the configured failure action is applied. |

### Failure Actions

When a simulation gate fails in AUTO mode, the step's `simulation_gate_failure_action` determines what happens:

| Action | Result |
|--------|--------|
| `BLOCK` | Step is marked BLOCKED. Rollout stops. Operator must resolve the gate. |
| `FAIL` | Step is marked FAILED. Rollout plan transitions to FAILED status. |
| `SKIP` | Step is marked SKIPPED. Rollout continues to the next step. |

### Rollout Step Configuration

Steps in a rollout plan can configure simulation gate automation:

```yaml
steps:
  - step_id: prod_canary
    step_type: assign_ring
    environment: prod
    ring_name: canary
    requires_simulation_gate: true
    simulation_gate_mode: auto
    simulation_gate_failure_action: fail
    simulation_limit: 1000
    simulation_include_base: true
    simulation_gate_max_age_seconds: 86400
    simulation_candidate_rules:
      - name: require_quorum_for_refunds
        action_type: tool.execute
        effect: require_approval
        tool_name: refund.request
        approval_policy:
          policy_type: quorum
          required_approvals: 2
    simulation_gate_rules:
      - name: no_errors
        metric: simulation.errors
        operator: eq
        threshold: 0
      - name: deny_limit
        metric: simulation.would_deny
        operator: lte
        threshold: 10
```

### Configuration

```yaml
governance:
  policy_release:
    rollouts:
      gate_automation:
        enabled: true
        default_mode: manual
        default_failure_action: block
        default_max_age_seconds: 86400
        default_gate_rules:
          - name: no_simulation_errors
            metric: simulation.errors
            operator: eq
            threshold: 0
          - name: changed_ratio_limit
            metric: simulation.changed_ratio
            operator: lte
            threshold: 0.05
```

### CLI Commands

```bash
# Run simulation gate for a rollout step
agentapp policy rollout gate run \
  --rollout-id ro_abc123 \
  --step-id prod_canary \
  --actor-id release_manager \
  --permissions policy.rollout.gate.run

# Check gate status for a rollout step
agentapp policy rollout gate status \
  --rollout-id ro_abc123 \
  --step-id prod_canary

# Check gate status (JSON output)
agentapp policy rollout gate status \
  --rollout-id ro_abc123 \
  --step-id prod_canary --json

# Attach an existing gate result to a rollout step
agentapp policy rollout gate attach \
  --rollout-id ro_abc123 \
  --step-id prod_canary \
  --gate-result-id pg_xyz789 \
  --simulation-id psim_abc456 \
  --actor-id release_manager \
  --permissions policy.rollout.gate.attach
```

### Console Workflow

The policy console provides rollout step gate pages:

- `GET /policy-console/rollouts/{rollout_id}/steps/{step_id}/gate` — Gate form/status page
- `POST /policy-console/rollouts/{rollout_id}/steps/{step_id}/gate/run` — Run gate
- `POST /policy-console/rollouts/{rollout_id}/steps/{step_id}/gate/attach` — Attach gate result

### Relationship to Phase 42

Phase 42 created `ReleaseGateAutomationService` and added MVP-level step blocking in `RolloutService.run_next_step()`. Phase 43 builds on this by:

1. Adding `RolloutGateAutomationService` that orchestrates per-step gate evaluation
2. Adding `RolloutGateMode` (DISABLED/MANUAL/AUTO) for step-level gate configuration
3. Adding `RolloutGateFailureAction` (BLOCK/FAIL/SKIP) for failure handling
4. Extending `RolloutStep` with simulation parameters for AUTO mode
5. Integrating gate automation into `run_next_step()` with proper status handling

Phase 42's manual blocking remains fully backward compatible — steps with `requires_simulation_gate=True` and no mode set default to MANUAL behavior.

### RBAC Permissions

| Permission | Description |
|-----------|-------------|
| `policy.rollout.gate.run` | Run simulation gate for a rollout step |
| `policy.rollout.gate.attach` | Attach a gate result to a rollout step |
| `policy.rollout.gate.view` | View rollout step gate status (default-allowed) |

### Known Limitations

- No background scheduler — execution is explicit command/API driven
- No external CI/CD integration — external pipelines must call CLI/API explicitly
- No live traffic shadowing — simulation uses historical audit data
- No distributed execution lock
- No automatic production rollback
- Candidate rule YAML parsing remains MVP-level

---

## Phase 44: Notification Hooks and Expiration Workers

### Purpose

Make governance states actionable by providing framework-level notification hooks and expiration sweep services. Pending approvals, blocked/failed/expired gate requirements, and other governance states that require operator attention now trigger notifications and can be swept for expiration.

### Notification Architecture

The notification system follows a rule-based, channel-driven model:

1. **PolicyNotificationRule** — Matches policy events by event_type and source_type. Each rule specifies severity, delivery channels, and optional title/body templates.
2. **PolicyNotificationService** — Matches enabled rules against incoming events, creates notification messages, delivers through configured channels, and tracks delivery status.
3. **PolicyNotificationChannel** — Protocol for delivery. Built-in channels: `log` (stdlib logging), `memory` (in-memory for testing).

### Notification Rules

Rules are configured in YAML or created programmatically:

```yaml
governance:
  policy_release:
    notifications:
      enabled: true
      rules:
        - name: rollout_gate_failed
          event_types:
            - policy.rollout.gate.failed
          severity: error
          channels:
            - log
          title_template: "Rollout gate failed: {rollout_id}/{step_id}"
```

### Built-in Channels

| Channel | Description |
|---------|-------------|
| `log` | Writes to standard library logging |
| `memory` | Stores in memory for testing |

### Expiration Sweep Service

The `PolicyExpirationService` sweeps two target types:

1. **Rollout approvals** — Calls `RolloutStepApprovalStore.expire_pending()` to mark past-due approvals as EXPIRED
2. **Gate requirements** — Checks `max_age_seconds` against satisfied_at/created_at for REQUIRED requirements

Sweeps are explicit (CLI or API). No background scheduler is started automatically.

### Optional In-Process Worker

`PolicyExpirationWorker` provides start/stop/run_once lifecycle:

```python
worker = PolicyExpirationWorker(expiration_service, interval_seconds=300)
await worker.start()   # starts background loop
await worker.run_once()  # single sweep (preferred for tests)
await worker.stop()    # safe to call multiple times
```

The worker does NOT start automatically on import or instantiation.

### CLI Commands

```bash
# Notifications
agentapp policy notification list --config agentapp.yaml
agentapp policy notification list --status failed --limit 20 --config agentapp.yaml
agentapp policy notification send-pending --config agentapp.yaml
agentapp policy notification rule list --config agentapp.yaml
agentapp policy notification rule enable --rule-id pnr_... --config agentapp.yaml
agentapp policy notification rule disable --rule-id pnr_... --config agentapp.yaml

# Expiration
agentapp policy expiration sweep --config agentapp.yaml
agentapp policy expiration run-once --config agentapp.yaml
```

### Console Workflow

- `/policy-console/notifications` — List and send notifications
- `/policy-console/notification-rules` — List and enable/disable rules
- `/policy-console/expiration` — Run and view sweep results

### RBAC Permissions

| Permission | Description | Default |
|-----------|-------------|---------|
| `policy.notification.view` | View notifications | Yes |
| `policy.notification.send` | Send notifications | No |
| `policy.notification.rule.view` | View notification rules | No |
| `policy.notification.rule.enable` | Enable rules | No |
| `policy.notification.rule.disable` | Disable rules | No |
| `policy.expiration.sweep` | Run expiration sweep | No |
| `policy.expiration.view` | View expiration status | Yes |

### Known Limitations

- No Slack/Jira/email integration
- No external webhook delivery
- No distributed queue
- No durable retry backoff beyond stored failed status
- No production scheduler
- Worker is local-process only
- Notifications depend on rules and event coverage

---

## Phase 45: Policy Rollout Analytics, History, and Gate Outcome Reporting

### Purpose

Make rollout execution history explainable and measurable. Every rollout lifecycle event — step transitions, approval decisions, gate evaluations, notification deliveries — is recorded in a structured history store and exposed through timeline views, analytics reports, and export helpers.

### Architecture

The rollout history system follows a recorder-service pattern:

1. **RolloutHistoryEvent model** — Normalized event record with `rhe_` prefix, tz-aware timestamps, and typed event categories
2. **RolloutHistoryStore** — Protocol + InMemory + SQLite persistence for history events
3. **RolloutHistoryRecorder** — Creates normalized history events from service operations
4. **RolloutHistoryService** — Generates timelines and analytics reports from recorded history

### History Event Types

24 event types across five categories:

| Category | Event Types |
|----------|-------------|
| Rollout | ROLLOUT_CREATED, ROLLOUT_STARTED, ROLLOUT_COMPLETED, ROLLOUT_FAILED, ROLLOUT_CANCELLED |
| Step | STEP_STARTED, STEP_SUCCEEDED, STEP_FAILED, STEP_BLOCKED, STEP_SKIPPED |
| Approval | APPROVAL_REQUESTED, APPROVAL_APPROVED, APPROVAL_REJECTED, APPROVAL_EXPIRED |
| Gate | GATE_RUN, GATE_PASSED, GATE_FAILED, GATE_BLOCKED, GATE_SKIPPED, GATE_ATTACHED |
| Notification | NOTIFICATION_SENT, NOTIFICATION_DELIVERED, NOTIFICATION_FAILED, NOTIFICATION_RULE_MATCHED, NOTIFICATION_RULE_DISABLED |

### Timeline Model

The `RolloutTimeline` provides a structured view of a rollout's execution history:

- **RolloutTimeline** — Top-level model with rollout metadata and list of step timelines
- **RolloutStepTimeline** — Per-step timeline with step metadata and ordered history events

Timelines are enriched from multiple stores:
- Rollout plan and step data from `RolloutPlanStore`
- History events from `RolloutHistoryStore`
- Approval data from `RolloutStepApprovalStore` (if available)

### Analytics Report

The `RolloutAnalyticsReport` (with `rar_` prefix) provides aggregated analytics:

- **Rollout counts** — Total, by status (completed/failed/cancelled)
- **Gate outcome summaries** — Pass/fail/block/skip counts per gate type
- **Approval outcome summaries** — Approved/rejected/expired counts
- **Top blocked steps** — Most frequently blocked step types
- **Top failed gates** — Most frequently failed gate rules
- **Environment summaries** — Per-environment rollout counts and outcomes
- **Ring summaries** — Per-ring assignment and promotion counts

### Service Integrations

Four existing services now record history events via `RolloutHistoryRecorder`:

1. **RolloutService** — Records rollout lifecycle events (created, started, completed, failed, cancelled) and step events (started, succeeded, failed, blocked, skipped)
2. **RolloutGateAutomationService** — Records gate events (run, passed, failed, blocked, skipped, attached)
3. **PolicyExpirationService** — Records approval expiration events
4. **PolicyNotificationService** — Records notification events (sent, delivered, failed, rule matched)

All integrations are best-effort: history recording failures are logged but do not corrupt the main service operation.

### Export

Three export helpers in `policy_compliance_export.py`:

| Helper | Output |
|--------|--------|
| `rollout_timeline_to_json` | JSON string of a RolloutTimeline |
| `rollout_analytics_report_to_json` | JSON string of a RolloutAnalyticsReport |
| `rollout_analytics_report_to_csv_rows` | List of flat dicts for CSV export |

### CLI Commands

```bash
# View rollout history events
agentapp rollout history --config agentapp.yaml --rollout-id ro_abc123

# View rollout timeline (structured)
agentapp rollout timeline --config agentapp.yaml --rollout-id ro_abc123
agentapp rollout timeline --config agentapp.yaml --rollout-id ro_abc123 --json

# View rollout analytics
agentapp rollout analytics --config agentapp.yaml
agentapp rollout analytics --config agentapp.yaml --environment prod --since 2026-06-01T00:00:00Z

# Export analytics
agentapp rollout analytics export --config agentapp.yaml --format json --output analytics.json
agentapp rollout analytics export --config agentapp.yaml --format csv --output analytics.csv
```

### Console Pages

| Page | Route | Description |
|------|-------|-------------|
| History | `/policy-console/rollouts/{rollout_id}/history` | Event list with type and timestamp filters |
| Timeline | `/policy-console/rollouts/{rollout_id}/timeline` | Structured step-by-step timeline view |
| Analytics | `/policy-console/rollout-analytics` | Dashboard with rollout counts, gate/approval outcomes, top blocked/failed |

### RBAC Permissions

| Permission | Value | Default |
|-----------|-------|---------|
| `ROLLOUT_HISTORY_VIEW` | `policy.rollout.history.view` | Allowed |
| `ROLLOUT_ANALYTICS_VIEW` | `policy.rollout.analytics.view` | Allowed |
| `ROLLOUT_ANALYTICS_EXPORT` | `policy.rollout.analytics.export` | Requires grant |

`ROLLOUT_HISTORY_VIEW` and `ROLLOUT_ANALYTICS_VIEW` are default-allowed, consistent with other view permissions. `ROLLOUT_ANALYTICS_EXPORT` requires explicit grants, consistent with other export operations.

### Change Events

7 new `PolicyChangeEventType` values (total now 72):

| Event Type | Trigger |
|-----------|---------|
| `ROLLOUT_HISTORY_RECORDED` | History event recorded for a rollout |
| `ROLLOUT_TIMELINE_GENERATED` | Timeline view generated |
| `ROLLOUT_ANALYTICS_REPORT_GENERATED` | Analytics report generated |
| `ROLLOUT_ANALYTICS_EXPORTED` | Analytics data exported |
| `ROLLOUT_ANALYTICS_EXPORT_FAILED` | Analytics export failed |
| `ROLLOUT_HISTORY_PERMISSION_DENIED` | RBAC check failed for history/analytics |
| `ROLLOUT_HISTORY_RECORDING_FAILED` | History recording failed (best-effort) |

### Configuration

```yaml
governance:
  policy_release:
    rollout_history:                          # Phase 45
      type: sqlite
      path: .agent_app/policy_rollout_history.db
```

`RolloutHistoryConfig` with `type` (memory/sqlite) and `path` fields. Defaults to `None` for backward compatibility.

### Known Limitations

- History is framework-level, not distributed tracing — no OpenTelemetry spans or trace correlation
- Analytics depend on recorder/event coverage — missing recorder calls produce incomplete analytics
- No external BI integration — no Tableau/PowerBI/Looker connectors
- No charts beyond console tables — no visualization library integration
- No OpenTelemetry exporter — history events are not exported as OTLP spans
- No persisted scheduled reports — analytics are generated on-demand only
- No auto-start worker for recording — history recorder is called inline by services
- Existing old rollouts may have partial history — events before Phase 45 deployment are not retroactively recorded

## Phase 46: Policy Rollout Federation and Conflict Detection

Phase 46 adds a framework-level federation layer for coordinating child rollout plans across tenants, environments, regions, rings, and target groups. It does not implement distributed locks, external deployment engines, Kubernetes/service mesh rollouts, cloud control planes, or cross-process schedulers.

### Federation targets

A `FederatedRolloutTarget` describes where a child rollout can run. Targets include `target_id`, `name`, optional `tenant_id`, required `environment`, optional `ring_name`, optional `region`, labels, status, metadata, and `created_at`. Disabled targets remain visible in stores and console pages but are ignored by automatic execution.

### Federated rollout plans

A `FederatedRolloutPlan` coordinates a policy bundle across target IDs and optional waves. The plan stores target executions, rollout template steps, strategy, status, creator, reason, and timestamps. Each target execution can reference the child `RolloutPlan` created for that target.

### Execution strategies

- `sequential`: `run_next_target()` executes the first pending target.
- `parallel`: logical parallelism; `run_next_target()` still executes one deterministic target and `run_all_available()` loops through all available targets.
- `wave`: targets execute in wave order; the next wave starts only after the current wave succeeds or skips according to `require_all_successful`.

### Conflict detection

`RolloutConflictDetector` detects duplicate targets, missing targets, disabled targets, active federated rollouts targeting the same target, active rollout plans for the same environment/ring, and active different-bundle overlaps. Duplicate, missing, disabled, same-target active federation, and environment/ring conflicts are errors. Bundle conflicts are warnings.

### CLI workflow

```bash
agentapp policy federation target create --config agentapp.yaml --name prod-us-canary --environment prod --ring canary --region us-east --tenant-id tenant_a --actor-id admin --permissions policy.federation.target.create
agentapp policy federation target list --config agentapp.yaml
agentapp policy federation plan create --config agentapp.yaml --name prod-global-rollout --bundle-id pb_123 --targets-file targets.yaml --steps-file rollout_steps.yaml --strategy wave --actor-id release_manager --permissions policy.federation.plan.create
agentapp policy federation plan conflicts --config agentapp.yaml --federation-id frp_123
agentapp policy federation plan start --config agentapp.yaml --federation-id frp_123 --actor-id release_manager --permissions policy.federation.plan.start
agentapp policy federation plan run-all --config agentapp.yaml --federation-id frp_123 --actor-id release_manager --permissions policy.federation.plan.execute
```

### Console workflow

Operators can use `/policy-console/federation/targets` to create, list, enable, and disable targets. They can use `/policy-console/federation/plans` and `/policy-console/federation/plans/new` to create and operate federated rollout plans. Plan detail pages show target execution status, child rollout IDs, and action forms. Conflict pages show conflict severity, type, target, existing rollout/federation, and message.

### Relationship to rollout history and analytics

Federation emits audit and policy change events for target and plan lifecycle operations. Child rollout plans created by the federation service use the existing `RolloutService`, so Phase 45 rollout history and analytics continue to apply to each child rollout.

### Known limitations

- Framework-level coordination only.
- No distributed lock.
- No external deployment engine.
- No Kubernetes or service mesh integration.
- No cross-process scheduler.
- Parallel strategy is logical, not concurrent execution.
- Conflict detection depends on configured stores and recorded state.
- Child rollout cancellation is deferred; federation cancellation marks federation state and pending/running executions only.

## Phase 47: Policy Rollout Federation Observability and Reporting

Phase 47 adds federation-focused observability to make federated rollout execution explainable and measurable. It builds on Phase 45 rollout history and Phase 46 federation state.

### Federation History Events

The `FederationHistoryEventType` enum defines 23 event types covering:
- Federation lifecycle: CREATED, STARTED, COMPLETED, FAILED, CANCELLED, BLOCKED
- Target lifecycle: TARGET_CREATED, TARGET_ENABLED, TARGET_DISABLED
- Target execution: STARTED, SUCCEEDED, FAILED, BLOCKED, SKIPPED, CANCELLED
- Wave execution: WAVE_STARTED, WAVE_SUCCEEDED, WAVE_FAILED, WAVE_BLOCKED
- Conflicts: CONFLICT_DETECTED
- Notifications: NOTIFICATION_CREATED, NOTIFICATION_SENT, NOTIFICATION_FAILED

Events are recorded via `FederationHistoryRecorder` and stored in `FederationHistoryStore` (InMemory or SQLite).

### Federation Timeline

The `FederationTimeline` model reconstructs the full history of a federated rollout:
- Federation-level metadata (name, bundle, strategy, status, timing)
- Wave timelines with status and duration
- Target timelines with status, duration, and child rollout references
- All events in chronological order
- Conflict records

### Federation Analytics Report

The `FederationAnalyticsReport` model provides aggregated analytics:
- Federation counts (total, active, completed, failed, cancelled, blocked)
- Target health summary (total, enabled, disabled, succeeded, failed, blocked, skipped)
- Wave outcome summary (total, succeeded, failed, blocked, pending)
- Conflict summary (total, error, warning, by type)
- Top failed and blocked targets
- Environment, region, and tenant summaries

### CLI Commands

```bash
# View federation history events
agentapp policy federation history --config agentapp.yaml --federation-id frp_abc123

# View federation timeline
agentapp policy federation timeline --config agentapp.yaml --federation-id frp_abc123

# View federation timeline as JSON
agentapp policy federation timeline --config agentapp.yaml --federation-id frp_abc123 --json

# View federation analytics
agentapp policy federation analytics --config agentapp.yaml

# View analytics with time window
agentapp policy federation analytics --config agentapp.yaml --since 2026-06-01T00:00:00Z

# Export analytics as JSON
agentapp policy federation analytics export --config agentapp.yaml --format json --output report.json

# Export analytics as CSV
agentapp policy federation analytics export --config agentapp.yaml --format csv --output report.csv
```

### Console Pages

- `/policy-console/federation/plans/{federation_id}/history` — Federation history events
- `/policy-console/federation/plans/{federation_id}/timeline` — Federation timeline view
- `/policy-console/federation/analytics` — Federation analytics with time window form and export

### Export Formats

- **JSON**: Full timeline or analytics report via `federation_timeline_to_json()` / `federation_analytics_report_to_json()`
- **CSV**: Flat rows with sections (summary, target_health, wave_outcomes, conflicts, environment_summary, region_summary, tenant_summary)

### RBAC Permissions

| Permission | Description |
|-----------|-------------|
| `policy.federation.history.view` | View federation history events (default-allowed) |
| `policy.federation.analytics.view` | View federation analytics (default-allowed) |
| `policy.federation.analytics.export` | Export federation analytics reports |

### Configuration

```yaml
governance:
  rollout_federation_history:
    enabled: true
    store:
      type: sqlite
      path: .agent_app/policy_federation_history.db
```

### Known Limitations

- Federation observability is framework-level, not distributed tracing
- Analytics depend on recorder/event coverage
- No external BI integration
- No charts beyond console tables
- No OpenTelemetry exporter
- No persisted scheduled reports
- Existing old federations may have partial history if recorder was disabled
- Parallel strategy remains logical/deterministic, not concurrent execution

## Phase 48: Federation Approval Workflows

### Purpose

Phase 48 adds approval workflows to federation rollout operations. Sensitive
federation actions — starting a plan, running targets, running all available
targets, and cancelling — can be configured to require explicit approval before
proceeding. This gives operators a safety gate before federated rollout actions
affect multiple environments, regions, or tenants simultaneously.

### Approval Policy Config

Federation approval policies are configured under `rollout_federation_approval`:

```yaml
governance:
  rollout_federation_approval:
    enabled: true
    require_approval_for:
      - start
      - run_next
      - run_all
      - cancel
    auto_approve_roles:
      - federation_admin
    escalation_timeout_seconds: 3600
    store:
      type: sqlite
      path: .agent_app/policy_federation_approvals.db
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool` | `False` | Enable federation approval checks |
| `require_approval_for` | `list[str]` | `[]` | Actions requiring approval: `start`, `run_next`, `run_all`, `cancel` |
| `auto_approve_roles` | `list[str]` | `[]` | Roles that bypass approval (auto-approved) |
| `escalation_timeout_seconds` | `int \| None` | `None` | Seconds before pending approval escalates |
| `store` | `StoreConfig \| None` | `None` | Approval store config (memory/sqlite) |

### Approval Lifecycle

```
create → approve/reject/escalate → execute
```

1. **Create** — When a sensitive action is attempted, `FederationApprovalService`
   checks if approval is required. If so, a `FederationApprovalRequest` is
   created with status `pending`.
2. **Approve** — An authorized actor approves the request. Status transitions
   to `approved`. The original action can then proceed.
3. **Reject** — An authorized actor rejects the request. Status transitions to
   `rejected`. The original action is blocked.
4. **Escalate** — If `escalation_timeout_seconds` is configured and the request
   remains pending past the timeout, the request is escalated. Status
   transitions to `escalated`.
5. **Expire** — Requests that are not resolved within their TTL transition to
   `expired`.
6. **Cancel** — The requester or an admin can cancel a pending request.

### FederationApprovalStatus Enum

| Status | Description |
|--------|-------------|
| `pending` | Awaiting approval |
| `approved` | Approved by authorized actor |
| `rejected` | Rejected by authorized actor |
| `expired` | TTL exceeded without resolution |
| `escalated` | Escalated due to timeout |
| `cancelled` | Cancelled by requester or admin |

### FederationApprovalRequest Model

| Field | Type | Description |
|-------|------|-------------|
| `approval_id` | `str` | Unique ID (`fap_` prefix) |
| `federation_id` | `str` | Target federated rollout plan |
| `action` | `str` | Requested action (start, run_next, run_all, cancel) |
| `status` | `FederationApprovalStatus` | Current lifecycle status |
| `requested_by` | `str` | Who requested the action |
| `reason` | `str \| None` | Why the action is requested |
| `approved_by` | `str \| None` | Who approved |
| `approved_reason` | `str \| None` | Why it was approved |
| `rejected_by` | `str \| None` | Who rejected |
| `rejected_reason` | `str \| None` | Why it was rejected |
| `escalated_by` | `str \| None` | Who escalated (system or actor) |
| `delegated_to` | `str \| None` | Delegated approver |
| `created_at` | `datetime` | Request timestamp |
| `resolved_at` | `datetime \| None` | Resolution timestamp |

### FederationApprovalPolicy Model

| Field | Type | Description |
|-------|------|-------------|
| `require_approval_for` | `list[str]` | Actions requiring approval |
| `auto_approve_roles` | `list[str]` | Roles that bypass approval |
| `escalation_timeout_seconds` | `int \| None` | Escalation timeout |
| `require_reason` | `bool` | Whether approve/reject require reason |

### FederationApprovalDecision Model

| Field | Type | Description |
|-------|------|-------------|
| `decision_id` | `str` | Unique ID |
| `approval_id` | `str` | Parent approval request |
| `decision_type` | `str` | `approve`, `reject`, `escalate`, `cancel` |
| `decided_by` | `str` | Who made the decision |
| `reason` | `str \| None` | Why the decision was made |
| `created_at` | `datetime` | Decision timestamp |

### FederationApprovalEscalation Model

| Field | Type | Description |
|-------|------|-------------|
| `escalation_id` | `str` | Unique ID |
| `approval_id` | `str` | Parent approval request |
| `escalated_by` | `str` | Who triggered escalation |
| `reason` | `str \| None` | Why it was escalated |
| `delegated_to` | `str \| None` | New approver after delegation |
| `created_at` | `datetime` | Escalation timestamp |

### FederationApprovalDashboardSummary Model

| Field | Type | Description |
|-------|------|-------------|
| `total` | `int` | Total approval requests |
| `pending` | `int` | Pending requests |
| `approved` | `int` | Approved requests |
| `rejected` | `int` | Rejected requests |
| `expired` | `int` | Expired requests |
| `escalated` | `int` | Escalated requests |
| `cancelled` | `int` | Cancelled requests |

### Delegated Approval

Approval requests can be delegated to another actor via the `delegate_approval()`
method. Delegation sets `delegated_to` on the approval request, indicating the
new responsible approver. The original requester is not notified (no notification
adapter integration yet).

### Escalation

If `escalation_timeout_seconds` is configured, pending requests that exceed the
timeout can be escalated via the `escalate()` method. Escalation transitions the
request status to `escalated` and records a `FederationApprovalEscalation` record.
Escalation is checked at action time, not by a background worker.

### FederationApprovalStore

Protocol + InMemory + SQLite persistence:

| Method | Signature | Description |
|--------|-----------|-------------|
| `create()` | `create(request) -> FederationApprovalRequest` | Create a new approval request |
| `get()` | `get(approval_id) -> FederationApprovalRequest \| None` | Get by approval ID |
| `list()` | `list(federation_id=None, status=None, action=None) -> list` | List with optional filters |
| `update()` | `update(request) -> FederationApprovalRequest` | Update an existing request |
| `delete()` | `delete(approval_id) -> None` | Delete an approval request |

Factory: `create_federation_approval_store(store_type, db_path)` supports
`"memory"` and `"sqlite"` types.

### FederationApprovalService

Core service with the following methods:

| Method | Description |
|--------|-------------|
| `requires_approval(federation_id, action, context)` | Check if approval is required for the action |
| `create_approval_request(federation_id, action, requested_by, ...)` | Create a pending approval request |
| `approve(approval_id, approved_by, context, reason)` | Approve a pending request |
| `reject(approval_id, rejected_by, context, reason)` | Reject a pending request |
| `escalate(approval_id, escalated_by, reason)` | Escalate a pending request |
| `cancel(approval_id, cancelled_by, reason)` | Cancel a pending request |
| `delegate_approval(approval_id, delegated_to, delegated_by, reason)` | Delegate to another approver |
| `check_approval_status(approval_id)` | Check current approval status |
| `is_action_approved(federation_id, action)` | Check if a specific action is approved |
| `get_dashboard_summary()` | Get approval counts summary |

### RolloutFederationService Integration

The `RolloutFederationService` checks approval before executing sensitive
actions. When `federation_approval_service` is configured:

- `start_federated_rollout()` — blocks if approval required and not approved
- `run_next_target()` — blocks if approval required and not approved
- `run_all_available()` — blocks if approval required and not approved
- `cancel_federated_rollout()` — blocks if approval required and not approved

When an action is blocked, the service returns an approval-required result
instead of executing the action.

### CLI Workflows

```bash
# List federation approval requests
agentapp policy federation approval list --config agentapp.yaml \
  [--federation-id frp_abc123] [--status pending] [--json]

# Approve a federation approval request
agentapp policy federation approval approve --config agentapp.yaml \
  --approval-id fap_xxx --approved-by admin [--reason "Approved after review"]

# Reject a federation approval request
agentapp policy federation approval reject --config agentapp.yaml \
  --approval-id fap_xxx --rejected-by admin [--reason "Risk too high"]

# Escalate a federation approval request
agentapp policy federation approval escalate --config agentapp.yaml \
  --approval-id fap_xxx --escalated-by admin [--reason "Needs senior review"]
```

The `run-all` command displays approval-required status when actions are
blocked by pending approvals.

### Console Workflow

| Route | Description |
|-------|-------------|
| `GET /policy-console/federation/approvals` | List all federation approval requests |
| `GET /policy-console/federation/approvals/{approval_id}` | Approval detail with action forms |
| `GET /policy-console/federation/plans/{federation_id}/approvals` | Plan-specific approval list |
| `POST /policy-console/federation/approvals/{approval_id}/approve` | Approve a pending request |
| `POST /policy-console/federation/approvals/{approval_id}/reject` | Reject a pending request |

### Observability Fields

The `FederationObservabilityService` now includes approval summary data:

- Pending approval count in analytics reports
- Approval outcome breakdown (approved/rejected/expired/escalated/cancelled)
- Approval export helpers (JSON, CSV) include approval fields

### RBAC Permissions (Phase 48 additions)

| Permission | Value | Default |
|-----------|-------|---------|
| `FEDERATION_APPROVAL_LIST` | `federation.approval.list` | Allowed |
| `FEDERATION_APPROVAL_APPROVE` | `federation.approval.approve` | Requires grant |
| `FEDERATION_APPROVAL_REJECT` | `federation.approval.reject` | Requires grant |
| `FEDERATION_APPROVAL_ESCALATE` | `federation.approval.escalate` | Requires grant |

### Change Events (Phase 48 additions)

6 new `PolicyChangeEventType` values (88 → 94 total):

| Event Type | Trigger |
|-----------|---------|
| `FEDERATION_APPROVAL_REQUESTED` | Approval requested for federation action |
| `FEDERATION_APPROVAL_APPROVED` | Federation approval granted |
| `FEDERATION_APPROVAL_REJECTED` | Federation approval denied |
| `FEDERATION_APPROVAL_ESCALATED` | Federation approval escalated |
| `FEDERATION_APPROVAL_EXPIRED` | Federation approval expired |
| `FEDERATION_APPROVAL_CANCELLED` | Federation approval cancelled |

### Federation History Events (Phase 48 additions)

5 new `FederationHistoryEventType` values (23 → 28 total):

| Event Type | Trigger |
|-----------|---------|
| `APPROVAL_REQUESTED` | Federation approval requested |
| `APPROVAL_APPROVED` | Federation approval granted |
| `APPROVAL_REJECTED` | Federation approval denied |
| `APPROVAL_ESCALATED` | Federation approval escalated |
| `APPROVAL_EXPIRED` | Federation approval expired |

### AgentApp Properties (Phase 48 additions)

3 new properties on `AgentApp`:

| Property | Type | Description |
|----------|------|-------------|
| `federation_approval_store` | `FederationApprovalStore \| None` | Approval store instance |
| `federation_approval_policy` | `FederationApprovalPolicy \| None` | Active approval policy |
| `federation_approval_service` | `FederationApprovalService \| None` | Approval service instance |

### Known Limitations

1. **Approval workflows are framework-level** — No integration with external
   identity providers. Approval identities are simple strings supplied via
   RunContext or CLI flags.

2. **No external identity provider integration yet** — Roles and permissions
   are supplied through RunContext / CLI, not from LDAP, SAML, OIDC, or other
   identity providers.

3. **No notification adapter yet** — When an approval is requested, there is
   no email, Slack, webhook, or other notification to approvers. Operators
   must poll the approval list or check the console.

4. **No persisted scheduled escalation worker** — Escalation timeout is checked
   at action time, not by a background worker. A pending approval that exceeds
   the timeout will only transition to `escalated` when explicitly escalated
   or when the next approval-related action is taken, unless a worker is
   implemented explicitly.

5. **No distributed lock** — Approval operations are local to a single process.
   Concurrent approval decisions from multiple processes are not protected by
   a distributed lock.

6. **Approval resume is deterministic service-level resume** — When a federation
   action is blocked by a pending approval and the approval is later granted,
   the action does not automatically resume. Operators must re-invoke the
   original action (start, run-next, run-all, cancel) after approval.

---

## Phase 49: Federation Approval Notification & Escalation Workers

Phase 49 adds federation-level notification adapters, notification outbox, escalation worker, and distributed lock to make Phase 48's approval workflows production-ready.

### New Modules

| Module | Description |
|--------|-------------|
| `governance/policy_rollout_federation_notification` | Federation notification models (7 models: channel, status, event type enums; message, delivery, policy, target, dispatch result) |
| `runtime/policy_rollout_federation_notification_store` | Federation notification store (Protocol + InMemory + SQLite) |
| `runtime/policy_rollout_federation_notification_adapters` | Notification adapters (noop, console, fake, webhook) |
| `runtime/policy_rollout_federation_notification_service` | Federation notification service (enqueue + dispatch) |
| `runtime/policy_rollout_federation_escalation_worker` | Escalation worker (single-tick pattern) |
| `runtime/distributed_lock` | Distributed lock (Protocol + InMemory + SQLite) |

### Notification Architecture

- **Outbox pattern**: Notification messages are written to an outbox store, then dispatched by a separate `dispatch_pending()` call
- **Channel abstraction**: Adapters implement `FederationNotificationAdapter` protocol (noop, console, fake, webhook)
- **Retry**: Failed notifications are retried with configurable backoff
- **Best-effort**: Notification failures never break approval state transitions

### Escalation Worker

- **Single-tick pattern**: `tick()` runs once and returns, no infinite loop
- **Distributed lock**: SQLite lock prevents duplicate worker execution
- **Dry-run mode**: Scan without mutating approvals
- **Configurable timeout**: Escalation after configurable minutes

### CLI Commands

```bash
agentapp policy federation notification list --status pending
agentapp policy federation notification dispatch --limit 100
agentapp policy federation notification by-approval --approval-id fap_...
agentapp policy federation approval escalate-due --dry-run
agentapp policy federation worker tick
```

### Console Pages

- `/policy-console/federation/notifications` — Notification list
- `/policy-console/federation/notifications/{id}` — Notification detail
- `/policy-console/federation/approvals/{id}/notifications` — Approval notifications
- `/policy-console/federation/escalations` — Escalation dashboard

### Event Count Changes

- PolicyChangeEventType: 94 → 100 (6 new FEDERATION_NOTIFICATION_* and ESCALATION_* events)
- FederationHistoryEventType: 28 → 30 (2 new ESCALATION_* events)
- PolicyReleasePermission: 76 → 79 (3 new FEDERATION_NOTIFICATION_* and ESCALATION_* permissions)

---

## Phase 50: Federation Approval Dead-Letter Queue & Scheduled Worker

### Overview
Phase 50 adds a dead-letter queue (DLQ) for federation notifications that exceed retry limits, configurable retry policies with per-channel overrides, and a persistent scheduled worker that orchestrates notification dispatch and escalation on a configurable interval.

### Models
- `FederationNotificationDLQStatus` (4 values: pending, retried, purged, resolved)
- `FederationNotificationDLQReason` (5 values: max_retries_exceeded, delivery_failed, adapter_error, invalid_recipient, manual)
- `FederationNotificationDeadLetter` — DLQ entry with fdlq_ prefix, tz-aware datetimes
- `FederationNotificationRetryPolicy` — max_attempts, backoff_seconds, send_to_dlq
- `FederationScheduledWorkerStatus` (4 values: stopped, running, stopping, failed)
- `FederationScheduledWorkerState` — worker lifecycle state

### DLQ Store
- `FederationNotificationDLQStore` Protocol with create, get, list, mark_retried, mark_purged, delete
- InMemory and SQLite implementations
- Factory function: `create_federation_notification_dlq_store()`

### Retry Policy
- Default retry policy with max_attempts=3, backoff_seconds=60, send_to_dlq=True
- Per-channel override via `by_channel_retry` config
- Applied during `FederationNotificationService.dispatch_pending()`
- Failed notifications exceeding max_attempts enter DLQ if send_to_dlq=True

### Scheduled Worker
- `FederationScheduledWorker` with start/stop/status/tick lifecycle
- Based on asyncio task with configurable interval
- Acquires distributed lock before each tick
- Calls notification_service.dispatch_pending() + escalation_worker.tick()
- Graceful shutdown via asyncio.Event

### CLI Commands
- `agentapp policy federation notification dlq list [--status] [--channel] [--limit] [--offset]`
- `agentapp policy federation notification dlq show --dlq-id fdlq_...`
- `agentapp policy federation notification dlq retry --dlq-id fdlq_...`
- `agentapp policy federation notification dlq purge --dlq-id fdlq_...`
- `agentapp policy federation notification dlq export --format json|csv`
- `agentapp policy federation worker status`
- `agentapp policy federation worker start --once`

### Console Pages
- `/policy-console/federation/notifications/dlq` — DLQ list
- `/policy-console/federation/notifications/dlq/{dlq_id}` — DLQ detail
- `/policy-console/federation/workers` — Worker status

### Configuration
```yaml
governance:
  policy_rollout:
    federation:
      notifications:
        dlq:
          enabled: true
          type: sqlite
          path: .agent_app/federation_notification_dlq.db
        retry:
          max_attempts: 3
          backoff_seconds: 60
          send_to_dlq: true
        by_channel_retry:
          webhook:
            max_attempts: 5
            backoff_seconds: 30
            send_to_dlq: true
      scheduled_worker:
        enabled: true
        interval_seconds: 60
        lock_type: sqlite
        lock_path: .agent_app/federation_scheduled_worker_locks.db
```

### Event Count Changes

- PolicyChangeEventType: 100 → 106 (6 new DLQ and worker event types)
- FederationHistoryEventType: 30 → 33 (3 new DLQ and worker history event types)
- PolicyReleasePermission: 79 → 82 (3 new FEDERATION_DLQ_* and FEDERATION_WORKER_* permissions)
- FederationNotificationStatus: 5 → 6 (DEAD_LETTERED added)

## Phase 51: Federation Notification Templates, Preferences & Webhook Replay

### Overview
Phase 51 adds configurable notification templates with safe variable substitution, notification preference management with opt-in/opt-out, webhook request snapshots with HMAC-SHA256 signing, and original-payload replay from DLQ.

### Template Selection Priority
1. Federation + event_type + channel explicit template
2. Event_type + channel template
3. Channel default template
4. Global default template
5. Built-in fallback template

### Preference Resolution Priority
1. Approval + event_type + channel
2. Federation + event_type + channel
3. Event_type + channel
4. Channel only
5. Subject global preference
6. System default (deliver)

### Webhook Signing
- HMAC-SHA256 with `{timestamp}.{nonce}.{body}` signing input
- Headers: X-AgentApp-Signature, X-AgentApp-Signature-Timestamp, X-AgentApp-Signature-Nonce, X-AgentApp-Signature-Version, X-AgentApp-Delivery-ID
- Key rotation with active_key_id + verification keys
- Timestamp tolerance configurable (default 300s)
- Nonce store prevents replay attacks

### retry vs replay-original
- **retry**: Re-enters full notification cycle, re-applies preferences and retry policy, uses original rendered snapshot by default
- **replay-original**: Uses original body bytes unchanged, generates new signature/timestamp/nonce, creates replay audit trail, requires FEDERATION_WEBHOOK_REPLAY permission

### CLI Commands
- `agentapp policy federation notification template list/show/create/update/disable/render`
- `agentapp policy federation notification preference list/set/show/delete/explain`
- `agentapp policy federation notification dlq replay-original --dlq-id ... [--dry-run]`
- `agentapp policy federation webhook verify --body-file ... --signature ... --timestamp ... --nonce ...`

### Console Pages
- /policy-console/federation/notifications/templates
- /policy-console/federation/notifications/templates/{template_id}
- /policy-console/federation/notifications/preferences
- /policy-console/federation/notifications/preferences/explain

### Configuration
```yaml
governance:
  policy_rollout:
    federation:
      notifications:
        templates:
          enabled: true
          strict_variables: true
          store_backend: sqlite
        preferences:
          enabled: true
          default_delivery: true
          failure_mode: open
          mandatory_event_types: []
        webhook_signing:
          enabled: true
          active_key_id: default
          keys:
            default: ${WEBHOOK_SIGNING_KEY}
        webhook_replay:
          enabled: true
          max_replays_per_entry: 10
```

### Event Counts
- PolicyChangeEventType: 106 → 118
- FederationHistoryEventType: 33 → 36
- PolicyReleasePermission: 82 → 88
- FederationNotificationStatus: 6 → 9

---

# Phase 52: Federation Notification Observability

## Overview

**Phase 52** adds observability to the federation notification system introduced in
Phase 49–51. It provides delivery event tracking, metrics aggregation, channel health
monitoring, SLA policy enforcement, and alert lifecycle management for federation
notifications. It provides:

1. **Delivery Event Tracking** — 12 event types covering the full notification lifecycle
2. **Metrics Aggregation** — Success rate, failure rate, DLQ rate, latency, p95 over configurable windows
3. **Channel Health Snapshots** — HEALTHY / DEGRADED / UNHEALTHY / UNKNOWN status per channel
4. **SLA Policy** — Configurable thresholds with per-channel overrides and violation detection
5. **Alert Rules** — Metric-based alerting with cooldown, acknowledge, and resolve lifecycle
6. **CLI Commands** — Full observability CLI under `federation notification` subcommand
7. **Console Pages** — Dashboard, events, metrics, health, SLA, alerts, alert detail
8. **Report Export** — JSON and CSV export for events, metrics, and alerts

## Delivery Event Lifecycle

### Event Types

12 event types covering the full notification delivery lifecycle:

| Event Type | Description |
|-----------|-------------|
| `created` | Notification record created |
| `queued` | Notification entered the dispatch queue |
| `rendered` | Template successfully rendered |
| `suppressed` | Delivery suppressed by preference |
| `send_attempted` | Dispatch attempted (adapter called) |
| `sent` | Notification successfully delivered |
| `failed` | Delivery failed |
| `retry_scheduled` | Retry scheduled by retry policy |
| `dlq_created` | Entry moved to dead-letter queue (max retries exceeded) |
| `dlq_replayed` | DLQ entry replayed via replay-original |
| `webhook_signature_failed` | Webhook HMAC signature verification failed |
| `template_failed` | Template rendering failed |

### Event Recording

Events are recorded by `FederationNotificationService._record_delivery_event()`:
- **Best-effort** — exceptions are caught and logged; a recording failure never breaks the notification flow
- **Sensitive data redaction** — API keys, tokens, secrets, and signatures are redacted before storage using a 38-key sensitive key set
- **Store** — `NotificationObservabilityStore` with InMemory and SQLite backends
- **Default path** — `.agent_app/federation_notification_observability.db`

## Metrics Aggregation

### NotificationMetricWindow

| Field | Type | Description |
|-------|------|-------------|
| `window_start` | `datetime` | Start of the aggregation window |
| `window_end` | `datetime` | End of the aggregation window |
| `total` | `int` | Total delivery events in window |
| `sent` | `int` | Events with `event_type == SENT` |
| `failed` | `int` | Events with `event_type == FAILED` |
| `suppressed` | `int` | Events with `event_type == SUPPRESSED` |
| `dlq` | `int` | Events with `event_type == DLQ_CREATED` |
| `retry_scheduled` | `int` | Events with `event_type == RETRY_SCHEDULED` |
| `success_rate` | `float` | `sent / total` (0.0 if total == 0) |
| `failure_rate` | `float` | `failed / total` (0.0 if total == 0) |
| `dlq_rate` | `float` | `dlq / total` (0.0 if total == 0) |
| `avg_latency_ms` | `float \| None` | Arithmetic mean of non-null latency values |
| `p95_latency_ms` | `float \| None` | 95th percentile of non-null latency values |

### Computation Details

- **Window**: Configurable via `window_minutes` (default: 60 minutes). Events are filtered by `created_at` within `[now - window_minutes, now]`.
- **Filtering**: Both `federation_id` and `channel` are optional filters applied before aggregation.
- **success_rate**: `sent / total` — measures successful deliveries as a fraction of all events.
- **failure_rate**: `failed / total` — measures hard failures as a fraction of all events.
- **dlq_rate**: `dlq / total` — measures dead-lettered notifications as a fraction of all events.
- **avg_latency_ms**: Arithmetic mean of all non-null `latency_ms` values in the window. Returns `None` if no latency data exists.
- **p95_latency_ms**: All non-null latencies are sorted; the 95th percentile is computed as `sorted_latencies[ceil(N * 0.95) - 1]`. Returns `None` if fewer than 20 latency samples exist.

## Channel Health

### ChannelHealthStatus

| Status | Description |
|--------|-------------|
| `HEALTHY` | Channel is operating normally |
| `DEGRADED` | Channel is experiencing elevated failure rates |
| `UNHEALTHY` | Channel is failing significantly |
| `UNKNOWN` | No delivery events recorded in the window |

### Health Determination Logic

Health is computed from aggregated metrics per channel using the following rules
(evaluated in order):

1. **UNKNOWN** — If `total == 0` (no events in the window)
2. **UNHEALTHY** — If `failure_rate > 0.5`
3. **DEGRADED** — If `failure_rate > 0.1` OR `dlq_rate > 0.05`
4. **HEALTHY** — If `success_rate >= 0.95` AND `failure_rate <= 0.05` AND `dlq_rate <= 0.01`
5. **DEGRADED** — All other cases with data (fallback)

### Console Health Page

The health page evaluates all three channels (`email`, `webhook`, `console`) and
displays a status badge for each. The CLI `health` command uses a simplified
assessment: HEALTHY if `success_rate >= 0.95`, DEGRADED if `success_rate >= 0.8`,
UNHEALTHY if `success_rate < 0.8`, UNKNOWN if no data.

## SLA Policy

### NotificationSlaPolicy

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool` | `True` | Enable/disable SLA monitoring |
| `max_delivery_latency_ms` | `int` | `30000` | Max delivery latency threshold (ms) |
| `min_success_rate` | `float` | `0.95` | Minimum acceptable success rate (0.0–1.0) |
| `max_failure_rate` | `float` | `0.05` | Maximum acceptable failure rate (0.0–1.0) |
| `max_dlq_rate` | `float` | `0.01` | Maximum acceptable DLQ rate (0.0–1.0) |
| `window_minutes` | `int` | `60` | Evaluation window in minutes |
| `channels` | `dict[str, NotificationChannelSlaOverride]` | `{}` | Per-channel SLA overrides |

### Per-Channel Override

`NotificationChannelSlaOverride` allows channel-specific thresholds:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_delivery_latency_ms` | `int \| None` | `None` | Channel-specific max latency |
| `min_success_rate` | `float \| None` | `None` | Channel-specific min success rate |
| `max_failure_rate` | `float \| None` | `None` | Channel-specific max failure rate |
| `max_dlq_rate` | `float \| None` | `None` | Channel-specific max DLQ rate |
| `window_minutes` | `int \| None` | `None` | Channel-specific evaluation window |

Channel overrides are merged with the base policy — unset fields fall back to the
base policy values.

### Severity Determination

`NotificationSlaService.evaluate()` checks each metric and assigns severity:

| Metric | Critical Threshold | Warning Threshold |
|--------|-------------------|-------------------|
| `avg_latency_ms` | Observed > 2x `max_delivery_latency_ms` | Between threshold and 2x |
| `success_rate` | Observed < 0.5x `min_success_rate` | Between 0.5x and threshold |
| `failure_rate` | Observed > 2x `max_failure_rate` | Between threshold and 2x |
| `dlq_rate` | Observed > 2x `max_dlq_rate` | Between threshold and 2x |

### NotificationSlaViolation

| Field | Type | Description |
|-------|------|-------------|
| `violation_id` | `str` | Unique ID (`nsv_` prefix) |
| `sla_policy` | `NotificationSlaPolicy` | The policy that was violated |
| `channel` | `str \| None` | Channel that violated SLA |
| `metric` | `str` | Metric that violated the threshold |
| `observed_value` | `float` | Actual observed value |
| `threshold_value` | `float` | Configured threshold |
| `severity` | `str` | `warning` or `critical` |
| `federation_id` | `str \| None` | Federation that triggered the violation |
| `created_at` | `datetime` | Violation timestamp |

## Alert Rules

### NotificationAlertRule

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `rule_id` | `str` | *(required)* | Unique rule ID (must start with `nar_`) |
| `name` | `str` | *(required)* | Human-readable rule name |
| `enabled` | `bool` | `True` | Whether the rule is active |
| `metric` | `str` | *(required)* | Metric to monitor (e.g. `failure_rate`, `success_rate`, `dlq_rate`, `avg_latency_ms`) |
| `operator` | `Literal[">", ">=", "<", "<=", "=="]` | *(required)* | Comparison operator |
| `threshold` | `float` | *(required)* | Threshold value |
| `severity` | `Literal["info", "warning", "critical"]` | `"warning"` | Alert severity |
| `channel` | `str \| None` | `None` | Filter to specific channel |
| `federation_id` | `str \| None` | `None` | Filter to specific federation |
| `window_minutes` | `int` | `60` | Evaluation window |
| `cooldown_minutes` | `int` | `30` | Minimum minutes between alerts for the same rule |

### Alert Lifecycle

`NotificationAlertEvent` status lifecycle:

| Status | Description |
|--------|-------------|
| `open` | Alert fired, awaiting action |
| `acknowledged` | Operator acknowledged the alert |
| `resolved` | Alert resolved (root cause addressed) |

### NotificationAlertEvent

| Field | Type | Description |
|-------|------|-------------|
| `alert_id` | `str` | Unique ID (`nal_` prefix) |
| `rule_id` | `str` | Reference to the triggering rule |
| `rule_name` | `str` | Human-readable rule name |
| `status` | `str` | `open`, `acknowledged`, or `resolved` |
| `severity` | `str` | `info`, `warning`, or `critical` |
| `metric` | `str` | Metric that triggered the alert |
| `observed_value` | `float` | Actual observed value |
| `threshold_value` | `float` | Configured threshold |
| `operator` | `str` | Comparison operator |
| `channel` | `str \| None` | Channel that triggered the alert |
| `federation_id` | `str \| None` | Federation that triggered the alert |
| `acknowledged_at` | `datetime \| None` | When the alert was acknowledged |
| `acknowledged_by` | `str \| None` | Who acknowledged the alert |
| `resolved_at` | `datetime \| None` | When the alert was resolved |
| `resolved_by` | `str \| None` | Who resolved the alert |
| `created_at` | `datetime` | Alert creation timestamp |

## Config Schema

```yaml
governance:
  policy_rollout:
    federation:
      notifications:
        observability:                          # Phase 52
          enabled: true
          store:
            type: sqlite
            path: .agent_app/federation_notification_observability.db
          window_minutes: 60
        sla:                                     # Phase 52
          enabled: true
          max_delivery_latency_ms: 30000
          min_success_rate: 0.95
          max_failure_rate: 0.05
          max_dlq_rate: 0.01
          window_minutes: 60
          channels:
            webhook:
              max_delivery_latency_ms: 10000
              min_success_rate: 0.98
        alerts:                                  # Phase 52
          enabled: true
          store:
            type: sqlite
            path: .agent_app/federation_notification_alerts.db
          rules:
            - rule_id: nar_high_failure_rate
              name: High failure rate
              enabled: true
              metric: failure_rate
              operator: ">"
              threshold: 0.1
              severity: warning
              cooldown_minutes: 30
            - rule_id: nar_webhook_critical
              name: Webhook channel critical
              enabled: true
              metric: failure_rate
              operator: ">"
              threshold: 0.5
              severity: critical
              channel: webhook
              cooldown_minutes: 15
```

## CLI Commands

```bash
# List delivery events
agentapp policy federation notification events list \
  --config agentapp.yaml \
  [--federation-id <id>] [--channel <ch>] [--event-type <type>] \
  [--since <iso>] [--until <iso>] [--limit <n>] [--format table|json]

# Show aggregated metrics
agentapp policy federation notification metrics \
  --config agentapp.yaml \
  [--federation-id <id>] [--channel <ch>] [--window-minutes <n>] \
  [--format table|json]

# Show channel health status
agentapp policy federation notification health \
  --config agentapp.yaml \
  [--format table|json]

# Check SLA compliance
agentapp policy federation notification sla check \
  --config agentapp.yaml \
  [--federation-id <id>] [--channel <ch>] [--format table|json]

# List alerts
agentapp policy federation notification alerts list \
  --config agentapp.yaml \
  [--status <status>] [--severity <sev>] [--channel <ch>] \
  [--federation-id <id>] [--limit <n>] [--format table|json]

# Acknowledge an alert
agentapp policy federation notification alerts ack \
  --config agentapp.yaml --alert-id <id> --by <who>

# Resolve an alert
agentapp policy federation notification alerts resolve \
  --config agentapp.yaml --alert-id <id> --by <who>

# Export report
agentapp policy federation notification report export \
  --config agentapp.yaml \
  --type <events|metrics|alerts> \
  --format <json|csv> \
  [--federation-id <id>] [--channel <ch>] [--window-minutes <n>] \
  [--output <path>]
```

## Console Pages

| Route | Template | Description |
|-------|----------|-------------|
| `GET /federation/notifications/observability` | `policy_federation_notification_observability.html` | Dashboard with aggregated metrics and alert summary |
| `GET /federation/notifications/events` | `policy_federation_notification_events.html` | Delivery events list with filters (event_type, channel) |
| `GET /federation/notifications/metrics` | `policy_federation_notification_metrics.html` | Delivery metrics detail page |
| `GET /federation/notifications/health` | `policy_federation_notification_health.html` | Channel health status for email, webhook, console |
| `GET /federation/notifications/sla` | `policy_federation_notification_sla.html` | SLA violations list |
| `GET /federation/notifications/alerts` | `policy_federation_notification_alerts.html` | Alert list with filters (status, severity) |
| `GET /federation/notifications/alerts/{alert_id}` | `policy_federation_notification_alert_detail.html` | Single alert detail with ack/resolve actions |

All pages are read-only except for alert acknowledge and resolve actions.

## Report Export

### Formats

| Data Type | JSON | CSV |
|-----------|------|-----|
| Delivery Events | `export_notification_events_json()` | `export_notification_events_csv()` |
| Metrics | `export_notification_metrics_json()` | `export_notification_metrics_csv()` |
| Alerts | `export_notification_alerts_json()` | `export_notification_alerts_csv()` |

### Sensitive Data Handling

All exports redact sensitive values (API keys, tokens, secrets, signatures, passwords,
cookies) using a 38-key sensitive key set applied to `error_message` and `metadata`
fields before serialization.

## RBAC Permissions

Phase 52 notification observability relies on existing general permissions:

| Permission | Value | Default |
|-----------|-------|---------|
| `OBSERVABILITY_VIEW` | `policy.observability.view` | Allowed |
| `OBSERVABILITY_EXPORT` | `policy.observability.export` | Allowed |
| `FEDERATION_NOTIFICATION_LIST` | `policy.federation.notification.list` | Allowed |

## Change Events (Phase 52 additions)

| Event Type | Trigger |
|-----------|---------|
| `policy.federation.notification.sla.violation_detected` | SLA violation detected |
| `policy.federation.notification.alert.created` | Alert rule fired |
| `policy.federation.notification.alert.acknowledged` | Alert acknowledged |
| `policy.federation.notification.alert.resolved` | Alert resolved |
| `policy.federation.notification.report.exported` | Report exported |

## Design Decisions

1. **Best-effort event recording** — `_record_delivery_event()` catches all exceptions; a failure to record observability data never breaks the notification dispatch flow. This prioritizes notification delivery over observability completeness.

2. **Sensitive data redaction at the model level** — The `NotificationDeliveryEvent` model uses a `@model_validator` to redact `error_message` and `metadata` fields before storage. A shared `_SENSITIVE_KEYS` set (38 key patterns) handles common secret names.

3. **Metrics computed at query time** — Aggregates are computed on demand from raw delivery events rather than pre-computed. This ensures accuracy and avoids stale metric data, at the cost of query performance for large event volumes.

4. **SLA severity is proportional** — Violation severity is `critical` when the observed value exceeds 2x the threshold (or is below 0.5x for rates), and `warning` otherwise. This provides graduated alerting rather than binary pass/fail.

5. **Alert cooldown prevents noise** — Each alert rule has a `cooldown_minutes` parameter. After an alert fires, the same rule will not fire again for that federation+channel combination until the cooldown expires.

6. **SQLite default for stores** — Both the observability store and alert store default to SQLite for persistence across CLI subprocess invocations. InMemory stores are available for testing.

## Current Limitations

1. **No external Prometheus/Grafana integration** — Metrics are available via CLI, console, and export only. There is no push-based metrics export to external monitoring systems.

2. **No external alert delivery mechanism** — Alerts are recorded in the store and visible in the console/CLI. There is no email, Slack, or PagerDuty integration for alert notifications.

3. **SQLite not suitable as large-scale time-series database** — The SQLite observability store works for moderate notification volumes. High-throughput systems (thousands of notifications per minute) will need a dedicated time-series database or external observability platform.

4. **Health/SLA computed from framework-recorded events only** — Metrics reflect only events recorded by the Agent App framework. Gaps in event recording (e.g., network failures before the framework records `send_attempted`) may result in under-counting.

5. **Production monitoring still requires external tools** — Phase 52 provides framework-internal observability for debugging and operational awareness. Production-grade monitoring, alerting, and dashboards require integration with external observability platforms (Datadog, New Relic, Grafana, etc.).

## Phase 53: Federation Notification External Alert Delivery, Prometheus Export, Retention & Rollup

### Alert Delivery

#### Models

| Model | Prefix | Description |
|-------|--------|-------------|
| `NotificationAlertDeliveryTarget` | `ndt_` | Target endpoint for alert delivery (URL, channel, severity filter, dry-run flag) |
| `NotificationAlertDeliveryAttempt` | `nda_` | Record of a single delivery attempt with status, response code, latency, error |
| `AlertDeliveryRetryPolicy` | — | Retry configuration: max attempts, backoff seconds, retryable status codes |

#### Store

| Component | Type |
|-----------|------|
| `NotificationAlertDeliveryStore` Protocol | Interface |
| InMemory implementation | Testing |
| SQLite implementation | Persistent storage (alert_delivery_targets, alert_delivery_attempts tables) |
| Factory: `create_alert_delivery_store()` | Config-driven |

#### Service

`NotificationAlertDeliveryService` provides:

- `create_target(federation_id, channel, url, severity_filter, dry_run)` — Register a delivery target
- `deliver_alert(alert_event, targets=None)` — Match targets by federation_id, channel, and severity, then deliver
- `list_targets(federation_id, channel)` — List active targets with filtering
- `list_attempts(alert_id)` — List delivery attempts for a specific alert
- Dry-run mode: records attempts with `dry_run=True` status without making HTTP calls

#### Adapters

| Adapter | Mode | Description |
|---------|------|-------------|
| `MemoryAlertDeliveryAdapter` | Live | In-process delivery (for testing) |
| `WebhookAlertDeliveryAdapter` | Dry-run only | HTTP POST with timeout, respects `_SENSITIVE_KEYS` redaction |
| `ConsoleAlertDeliveryAdapter` | Live | Prints alert payload to stdout (structured JSON) |

#### CLI Commands

```bash
agentapp policy federation notification alert deliver <alert_id> [--target-id <id>] [--dry-run]
agentapp policy federation notification alert targets list [--federation-id <id>] [--channel <ch>]
agentapp policy federation notification alert attempts list [--alert-id <id>]
```

### Prometheus Export

`NotificationPrometheusExporter` generates Prometheus text-format metrics:

- HELP/TYPE comment blocks for each metric family
- Label escaping per Prometheus spec (backslash, quote, newline)
- No secrets in metric labels or values (sensitive key redaction)
- Metrics exported: `notification_delivery_total`, `notification_delivery_duration_seconds`, `notification_channel_health`

```bash
agentapp policy federation notification prometheus export [--federation-id <id>] [--channel <ch>] [--output <path>]
```

### JSONL Export

`NotificationJsonlExporter` produces structured JSONL files:

- One JSON object per line for stream processing
- Export types: delivery events, alerts, delivery attempts
- Sensitive data redaction before serialization

```bash
agentapp policy federation notification jsonl export <events|alerts|attempts> \
  [--federation-id <id>] [--channel <ch>] [--window-minutes <n>] [--output <path>]
```

### Retention Service

`NotificationRetentionService` manages data lifecycle:

- Per-type retention days: events, alerts, attempts, targets
- Archive-before-purge: moves expired records to archive before deletion
- Dry-run mode: reports what would be purged without deleting
- Runs via CLI or programmatic trigger

```bash
agentapp policy federation notification retention cleanup [--dry-run] [--type <events|alerts|attempts|targets>]
```

### Metrics Rollup Service

`NotificationRollupService` aggregates delivery metrics:

- Granularity: hourly or daily
- Upsert semantics: re-running rollup replaces existing aggregated data
- Dimensions: federation_id, channel, event_type, status
- Stored in SQLite for query efficiency

```bash
agentapp policy federation notification rollup build [--granularity hourly|daily] [--window-days <n>]
agentapp policy federation notification rollup list [--federation-id <id>] [--channel <ch>]
```

### Console Pages

| Route | Template | Description |
|-------|----------|-------------|
| `GET /federation/notifications/alert-delivery` | `policy_federation_notification_alert_delivery.html` | Alert delivery dashboard with target list and recent attempts |
| `GET /federation/notifications/alert-delivery/targets` | `policy_federation_notification_alert_delivery_targets.html` | Target management: create, list, toggle dry-run |
| `GET /federation/notifications/alert-delivery/attempts` | `policy_federation_notification_alert_delivery_attempts.html` | Delivery attempt history with status and latency |
| `GET /federation/notifications/prometheus` | `policy_federation_notification_prometheus.html` | Prometheus metrics display with copy-to-clipboard |
| `GET /federation/notifications/jsonl` | `policy_federation_notification_jsonl.html` | JSONL export interface with type and date range selection |
| `GET /federation/notifications/retention` | `policy_federation_notification_retention.html` | Retention policy configuration and dry-run preview |
| `GET /federation/notifications/rollup` | `policy_federation_notification_rollup.html` | Rollup dashboard with hourly/daily toggle and result table |

### RBAC Permissions

| Permission | Value | Default |
|-----------|-------|---------|
| `ALERT_DELIVERY_VIEW` | `policy.federation.notification.alert_delivery.view` | Allowed |
| `ALERT_DELIVERY_MANAGE` | `policy.federation.notification.alert_delivery.manage` | Allowed |
| `PROMETHEUS_EXPORT` | `policy.federation.notification.prometheus.export` | Allowed |
| `JSONL_EXPORT` | `policy.federation.notification.jsonl.export` | Allowed |
| `RETENTION_MANAGE` | `policy.federation.notification.retention.manage` | Allowed |
| `ROLLUP_BUILD` | `policy.federation.notification.rollup.build` | Allowed |

### Change Events (Phase 53 additions)

| Event Type | Trigger |
|-----------|---------|
| `policy.federation.notification.alert_delivery.target_created` | Alert delivery target created |
| `policy.federation.notification.alert_delivery.target_updated` | Alert delivery target updated |
| `policy.federation.notification.alert_delivery.target_disabled` | Alert delivery target disabled |
| `policy.federation.notification.alert_delivery.attempt_recorded` | Delivery attempt recorded |
| `policy.federation.notification.alert_delivery.dlq_created` | Delivery added to dead-letter queue |
| `policy.federation.notification.prometheus.exported` | Prometheus metrics exported |
| `policy.federation.notification.jsonl.exported` | JSONL export completed |
| `policy.federation.notification.retention.cleanup_ran` | Retention cleanup executed |
| `policy.federation.notification.rollup.built` | Metrics rollup built |

### Federation History Events (Phase 53 additions)

| Event Type | Trigger |
|-----------|---------|
| `notification.alert_delivery.target_created` | Alert delivery target created |
| `notification.alert_delivery.target_updated` | Alert delivery target updated |
| `notification.alert_delivery.target_disabled` | Alert delivery target disabled |
| `notification.alert_delivery.attempt_recorded` | Delivery attempt recorded |
| `notification.alert_delivery.dlq_created` | Delivery added to dead-letter queue |
| `notification.prometheus.metrics_exported` | Prometheus metrics exported |
| `notification.jsonl.exported` | JSONL export completed |
| `notification.retention.cleanup_ran` | Retention cleanup executed |
| `notification.rollup.built` | Metrics rollup built |

### Config

| Config Class | Description |
|-------------|-------------|
| `RolloutFederationNotificationAlertDeliveryConfig` | Alert delivery service toggle (`enabled`), target store config |
| `RolloutFederationNotificationPrometheusExportConfig` | Prometheus export toggle |
| `RolloutFederationNotificationJsonlExportConfig` | JSONL export toggle |
| `RolloutFederationNotificationRetentionConfig` | Retention toggle, per-type retention days, archive path |
| `RolloutFederationNotificationRollupConfig` | Rollup toggle, default granularity |

### Design Decisions

1. **Webhook adapter dry-run only** — The `WebhookAlertDeliveryAdapter` does not perform real HTTP calls in this phase. It records the attempt with status `dry_run` for safety. Real webhook delivery is a future enhancement requiring explicit opt-in and signature verification.

2. **Best-effort delivery recording** — All delivery attempt recording uses try/except with logger.debug. A failure to record a delivery attempt never breaks the alert dispatch flow.

3. **Sensitive data redaction at export time** — Prometheus and JSONL exports apply `_SENSITIVE_KEYS` redaction to `error_message` and `metadata` fields before serialization. No keys, signatures, or sensitive headers appear in any export format.

4. **Archive-before-purge for retention** — Expired records are moved to an archive (separate SQLite file) before deletion from the primary store. This preserves audit trail compliance while managing storage growth.

5. **Rollup upsert semantics** — Building a rollup for an existing time bucket replaces the prior data rather than creating duplicates. Re-running rollup with corrected data produces consistent results.

6. **SQLite default for stores** — Alert delivery, rollup, and retention stores default to SQLite for persistence. InMemory implementations are available for testing.

### Current Limitations

1. **Webhook delivery is dry-run only** — Real HTTP delivery to external endpoints is not implemented. The webhook adapter records simulated attempts only.

2. **No alert delivery retry scheduling** — Failed deliveries are recorded but not automatically retried. Retry logic exists in the model (`AlertDeliveryRetryPolicy`) but is not yet wired into the dispatch loop.

3. **Rollup query performance** — Rollup queries join aggregated data with raw delivery events. Large event volumes may experience slower rollup builds; partitioning by date range is recommended.

4. **Retention archive management** — Archive files accumulate over time and are not automatically purged. Operators should monitor archive directory size.

5. **Prometheus export is pull-only** — Metrics are available via CLI and console export. There is no push-based integration with Prometheus scraping or external monitoring platforms.

6. **JSONL export requires post-processing for SIEM** — JSONL format is suitable for log aggregation pipelines but requires external tooling (Splunk, Elastic, etc.) for alerting and dashboarding.

## Phase 54: Alert Delivery Productionization — Retry, DLQ Replay, Dedup, Incremental Rollup, Webhook Signing & Archive Cleanup

### Overview

**Phase 54** upgrades Phase 53's alert delivery from "configurable, dry-run capable" to a production operations closed loop with real delivery, signing, retry, DLQ replay, deduplication, incremental aggregation, and archive cleanup.

### Retry Scheduler

`NotificationAlertDeliveryService.run_once()` scans `RETRY_SCHEDULED` attempts past their `next_retry_at` and retries them:

```bash
agentapp policy federation notification alert delivery retry-run [--dry-run] [--limit <n>]
```

- Dry-run reports which attempts would be retried without making adapter calls
- Respects `AlertDeliveryRetryPolicy` (max_attempts, base_delay_seconds, max_delay_seconds)
- Exhausted retries automatically move attempts to DLQ status

### DLQ Replay

Replay a dead-letter queue attempt by ID:

```bash
agentapp policy federation notification alert delivery dlq replay <attempt_id> [--dry-run]
```

- Creates a new delivery attempt (does not overwrite the original DLQ record)
- Only replays attempts with `DLQ` status
- Records `FEDERATION_NOTIFICATION_ALERT_DELIVERY_DLQ_REPLAYED` change event

### Alert Deduplication

`NotificationAlertDedupService` suppresses or merges duplicate alert delivery events within a configurable time window:

- Key fields: `alert_id`, `target_id` (configurable)
- Merge window: default 300 seconds
- Prune expired entries to prevent unbounded memory growth

```bash
agentapp policy federation notification alert dedup explain <alert_id> <target_id>
```

### Incremental Rollup with Checkpoints

`NotificationRollupStore` extended with incremental rollup and checkpoint tracking:

- `build_incremental_rollup(since)` — only processes rollups newer than the given timestamp
- `list_checkpoints()` — lists recorded rollup checkpoints
- `record_checkpoint(checkpoint)` — records a new checkpoint after rollup completion
- Checkpoints enable efficient incremental processing without scanning full history

```bash
agentapp policy federation notification rollup incremental [--since <ISO8601>]
agentapp policy federation notification rollup checkpoint list
```

### Webhook Signing (HMAC-SHA256)

`WebhookAlertDeliveryAdapter` now supports real HTTP POST with optional HMAC-SHA256 signing:

- When `webhook_secret` is configured on the target, signs payload with `X-Signature: v1=<hex>` and `X-Timestamp` headers
- Uses stdlib `hmac` + `hashlib` only (no external dependencies)
- Sensitive headers (`X-Signature`, `X-Timestamp`) are never logged or exported

```python
from agent_app.runtime.policy_rollout_federation_notification_webhook_signing import (
    sign_payload, make_signed_headers, redact_sensitive,
)
```

### Archive Cleanup

```bash
agentapp policy federation notification retention archives cleanup [--older-than-days <n>]
```

- Deletes framework-generated archive files matching `notification_*` pattern
- Only deletes files older than the specified threshold
- Dry-run mode available for preview

### Change Events (Phase 54 additions)

| Event Type | Trigger |
|-----------|---------|
| `policy.federation.notification.alert_delivery.retry_ran` | Retry scheduler completed a run |
| `policy.federation.notification.alert_delivery.dlq_replayed` | DLQ attempt was replayed |
| `policy.federation.notification.alert_delivery.webhook_signed` | Webhook payload was HMAC-signed |
| `policy.federation.notification.alert.dedup.processed` | Dedup decision was made for an alert |
| `policy.federation.notification.rollup.incremental_built` | Incremental rollup completed |
| `policy.federation.notification.rollup.checkpoint_recorded` | Rollup checkpoint recorded |
| `policy.federation.notification.retention.archives_cleaned` | Archive cleanup completed |

### RBAC Permissions (Phase 54 additions)

| Permission | Value | Default |
|-----------|-------|---------|
| `ALERT_DELIVERY_RETRY_RUN` | `policy.federation.notification.alert_delivery.retry_run` | Allowed |
| `ALERT_DELIVERY_DLQ_REPLAY` | `policy.federation.notification.alert_delivery.dlq_replay` | Allowed |
| `ALERT_DELIVERY_DEDUP_VIEW` | `policy.federation.notification.alert_dedup.view` | Allowed |
| `ROLLUP_INCREMENTAL_BUILD` | `policy.federation.notification.rollup.incremental_build` | Allowed |
| `ROLLUP_CHECKPOINT_VIEW` | `policy.federation.notification.rollup.checkpoint.view` | Allowed |
| `RETENTION_ARCHIVES_CLEANUP` | `policy.federation.notification.retention.archives_cleanup` | Allowed |

### CLI Commands (Phase 54 additions)

```bash
# Retry scheduler
agentapp policy federation notification alert delivery retry-run [--dry-run] [--limit <n>]

# DLQ replay
agentapp policy federation notification alert delivery dlq list [--alert-id <id>]
agentapp policy federation notification alert delivery dlq replay <attempt_id> [--dry-run]

# Dedup
agentapp policy federation notification alert dedup explain <alert_id> <target_id>

# Rollup incremental + checkpoints
agentapp policy federation notification rollup incremental [--since <ISO8601>]
agentapp policy federation notification rollup checkpoint list

# Archive cleanup
agentapp policy federation notification retention archives cleanup [--older-than-days <n>]
```

### Security Constraints

1. **No keys/signatures/sensitive headers in logs/console/exports** — `_SENSITIVE_KEYS` set used for redaction across all adapters, exports, and payload previews. HMAC secrets and signatures are never emitted.
2. **No external network in tests** — All adapter tests use `dry_run=True`. Webhook signing is tested at the function level only.
3. **Stdlib urllib only** — Webhook HTTP POST uses `urllib.request.Request`/`urlopen` from Python stdlib.
4. **Backward-compatible config defaults** — `webhook_secret` is optional on `AlertDeliveryTarget`; signing is only activated when explicitly configured.

### Design Decisions

1. **Change events are best-effort** — Recording failures never break delivery flows. Logged at debug level only.
2. **DLQ replay creates new attempts** — Original DLQ records are preserved for audit. Replay creates a new attempt with incremented attempt number.
3. **Incremental rollup uses checkpoints** — Checkpoints track the last processed window_end, enabling efficient incremental builds.
4. **Archive cleanup is pattern-scoped** — Only deletes files matching `notification_*` to avoid accidental deletion of unrelated data.

## Phase 55: Alert Delivery Closed Loop (v0.35)

Phase 55 upgrades Phase 54's alert delivery from "manually executable production ops" to "production-grade closed loop" with 5 new capabilities: retry daemon, priority queue, archive cleanup, change event wiring, and CLI commands.

### Retry Daemon (`AlertDeliveryRetryDaemon`)

The retry daemon runs `NotificationAlertDeliveryService.run_once` on a configurable interval with optional jitter to avoid thundering herd.

- **Location**: `agent_app/runtime/policy_rollout_federation_notification_retry_daemon.py`
- **Config**: `RolloutFederationNotificationAlertDeliveryConfig.retry_daemon`
  - `enabled` (bool, default False)
  - `interval_seconds` (float, default 60.0)
  - `jitter_seconds` (float, default 5.0)
  - `batch_limit` (int, default 100)
  - `stop_on_error` (bool, default False)
  - `run_immediately` (bool, default True)
- **Change events**:
  - `FEDERATION_NOTIFICATION_RETRY_DAEMON_STARTED` — daemon loop started
  - `FEDERATION_NOTIFICATION_RETRY_DAEMON_STOPPED` — daemon loop stopped
  - `FEDERATION_NOTIFICATION_RETRY_DAEMON_RUN_COMPLETED` — single run completed successfully
  - `FEDERATION_NOTIFICATION_RETRY_DAEMON_RUN_FAILED` — single run failed with error
- **CLI commands**:
  - `agentapp policy federation notification alert delivery daemon start --config <path> [--interval <s>] [--jitter <s>] [--batch-limit <n>]`
  - `agentapp policy federation notification alert delivery daemon stop`
  - `agentapp policy federation notification alert delivery daemon status --config <path>`

### Priority Queue (`AlertPriorityQueue`)

The priority queue wraps an alert delivery store and exposes priority-aware dequeue semantics sorted by priority (higher value = more urgent), then by creation timestamp.

- **Location**: `agent_app/runtime/policy_rollout_federation_notification_alert_priority_queue.py`
- **Priority mapping**: `severity_to_priority()` maps severity strings to numeric priorities:
  - `critical` → 100, `error` → 75, `warning` → 50, `info` → 25, unknown → 0
- **Change events**:
  - `FEDERATION_NOTIFICATION_PRIORITY_UPDATED` — attempt priority was set/updated
  - `FEDERATION_NOTIFICATION_PRIORITY_LISTED` — priority listing was performed (from CLI)
- **CLI commands**:
  - `agentapp policy federation notification alert delivery priority list --config <path> [--limit <n>]`
  - `agentapp policy federation notification alert delivery priority update <attempt_id> <priority> --config <path> --yes`

### Archive Cleanup (`ResumableArchiveCleanup`)

Resumable archive cleanup for old rollup data with checkpoint support. Can resume from the last checkpoint if interrupted.

- **Location**: `agent_app/runtime/policy_rollout_federation_notification_archive_cleanup_service.py`
- **Config**: `RolloutFederationNotificationConfig.archive_cleanup` (`RolloutFederationNotificationArchiveCleanupConfig`)
  - `enabled` (bool, default False)
  - `rollup_retention_days` (int, default 30)
  - `checkpoint_retention_days` (int, default 90)
  - `archive_dir` (str, default "archives")
  - `archive_format` (str, default "jsonl")
  - `batch_size` (int, default 500)
- **Change events**:
  - `FEDERATION_NOTIFICATION_ARCHIVE_CLEANUP_STARTED` — batch processing started
  - `FEDERATION_NOTIFICATION_ARCHIVE_CLEANUP_COMPLETED` — cleanup completed successfully
  - `FEDERATION_NOTIFICATION_ARCHIVE_CLEANUP_FAILED` — cleanup failed with error
- **CLI commands**:
  - `agentapp policy federation notification archive-cleanup --config <path> [--dry-run] [--yes]`

### Phase 55 Change Events

| Event Type | When Emitted |
|---|---|
| `FEDERATION_NOTIFICATION_RETRY_DAEMON_STARTED` | Retry daemon loop started |
| `FEDERATION_NOTIFICATION_RETRY_DAEMON_STOPPED` | Retry daemon loop stopped |
| `FEDERATION_NOTIFICATION_RETRY_DAEMON_RUN_COMPLETED` | Single retry run completed |
| `FEDERATION_NOTIFICATION_RETRY_DAEMON_RUN_FAILED` | Single retry run failed |
| `FEDERATION_NOTIFICATION_PRIORITY_UPDATED` | Attempt priority updated |
| `FEDERATION_NOTIFICATION_PRIORITY_LISTED` | Priority listing performed |
| `FEDERATION_NOTIFICATION_ARCHIVE_CLEANUP_STARTED` | Archive cleanup batch started |
| `FEDERATION_NOTIFICATION_ARCHIVE_CLEANUP_COMPLETED` | Archive cleanup completed |
| `FEDERATION_NOTIFICATION_ARCHIVE_CLEANUP_FAILED` | Archive cleanup failed |
| `FEDERATION_NOTIFICATION_WRITE_ACTION_PERFORMED` | Console write action performed |

### Design Decisions

1. **Change events are best-effort** — Recording failures never break daemon/cleanup/priority flows. Logged at debug level only.
2. **Both audit_logger and change_event_store** — Services maintain backward compatibility with `audit_logger` (raw strings) while adding `change_event_store` (PolicyChangeEventType enum) for Phase 55 audit/history integration.
3. **Daemon is process-local** — The retry daemon runs in-process and must be explicitly started. No external process management.
4. **Archive cleanup is resumable** — Checkpoints track progress and enable resumption from the last processed record after interruption.

## Phase 57: Alert Delivery Operations Chain — Atomic Priority Queue & Daemon Deep Integration (v0.43)

### Overview

Phase 57 fixes production-critical gaps in the alert delivery operations chain identified after Phase 55/56. The focus is on atomic queue semantics, retry daemon deep integration, state persistence, webhook signing key rotation, SQLite concurrency safety, and batch DLQ replay.

### Architecture

1. **Atomic Priority Queue**: The `AlertPriorityQueueStore` protocol gains `claim_next`, `acknowledge`, `fail`, `requeue`, and `reset_expired_leases` methods. The `AlertPriorityQueueItem` model gains an `available_at` field for scheduled delivery. Both InMemory and SQLite implementations support atomic claim/ack/fail/requeue semantics with worker-id and lease-ttl.

2. **Retry Daemon Deep Integration**: The `AlertDeliveryRetryDaemon.run_once()` method now claims items from the priority queue first, delivers via the appropriate adapter, and acknowledges/requeues/fails atomically. Remaining budget falls back to `scheduler.run_once()` for backward compatibility.

3. **Daemon State Persistence**: The `AlertDeliveryRetryDaemonState` model persists daemon runtime state (started_at, last_run_at, consecutive_failures, last_error, last_result) to InMemory or SQLite stores. After restart, `get_health_status()` uses persisted state for visibility.

4. **Webhook Signing Key Rotation**: The `WebhookSigningService` gains `rotate_key()` method with configurable `rotation_interval_hours`. Rotation records are written to the AuditLogStore.

5. **SQLite Concurrency Safety**: SQLite stores use WAL mode, busy_timeout, and retry logic to handle concurrent writers safely.

6. **Batch DLQ Replay**: The `replay_batch` method replays multiple DLQ entries with confirmation support. Structured error messages include enum + detail + reason for each entry.

### New Models

- `AlertDeliveryRetryDaemonState` — persistent daemon runtime state
- `AlertDeliveryRetryDaemonStateStore` — protocol with InMemory + SQLite
- `BatchReplayResult` — batch replay outcome with entry-level errors
- `BatchReplayEntryResult` — individual replay entry result
- `ReplayErrorCode` — enum for structured replay errors

### New PolicyChangeEventType Values

| Value | Description |
|-------|-------------|
| `FEDERATION_NOTIFICATION_DAEMON_STARTED` | Retry daemon started |
| `FEDERATION_NOTIFICATION_DAEMON_STOPPED` | Retry daemon stopped |
| `FEDERATION_NOTIFICATION_DAEMON_RUN_STARTED` | Daemon run cycle started |
| `FEDERATION_NOTIFICATION_DAEMON_RUN_COMPLETED` | Daemon run cycle completed |
| `FEDERATION_NOTIFICATION_DAEMON_RUN_FAILED` | Daemon run cycle failed |
| `FEDERATION_NOTIFICATION_QUEUE_CLAIMED` | Item claimed from priority queue |
| `FEDERATION_NOTIFICATION_QUEUE_ACKED` | Item acknowledged |
| `FEDERATION_NOTIFICATION_QUEUE_FAILED` | Item failed |
| `FEDERATION_NOTIFICATION_QUEUE_REQUEUED` | Item requeued |
| `FEDERATION_NOTIFICATION_SIGNING_KEY_ROTATED` | Webhook signing key rotated |
| `FEDERATION_NOTIFICATION_BATCH_REPLAY_STARTED` | Batch replay started |
| `FEDERATION_NOTIFICATION_BATCH_REPLAY_COMPLETED` | Batch replay completed |

### Design Decisions

1. **Atomic claim/ack/fail/requeue** — Each priority queue operation is atomic at the store level. Worker-id and lease-ttl prevent duplicate processing.
2. **Priority queue first, fallback to scheduler** — The daemon claims from the priority queue first (high-priority items), then falls back to the scheduler's `run_once()` for remaining budget.
3. **State persistence across restarts** — Daemon state is persisted after each run cycle. After restart, health status reflects the last known state from the store.
4. **Key rotation with AuditLogStore** — Webhook signing key rotation writes to the AuditLogStore for audit trail, not the notification history store.
5. **SQLite WAL mode** — WAL mode enables concurrent readers and a single writer without database-locked errors. busy_timeout provides additional safety.
6. **Batch replay with confirmation** — Batch replay requires explicit confirmation in live mode to prevent accidental mass replay.
