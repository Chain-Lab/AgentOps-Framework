# Policy Release Gates & Versioned Policy Bundles — Phase 29

> **Status:** Implemented

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

## Limitations

- No bundle diff/comparison view (future: show config changes between versions)
- No gate re-run without creating a new gate result
- No scheduled/automated promotion (manual promote only)
- Console pages are read-only (mutations via CLI only)
- Rollback does not validate gate status (by design — emergency operation)
