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
