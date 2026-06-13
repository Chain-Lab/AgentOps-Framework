# Phase 33: Release Rings and Canary Evaluation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add controlled policy rollout with release rings and canary evaluation on top of Phase 31/32 environment activation.

**Architecture:** Define release rings (stable/canary/internal) per environment. Assign activations to rings. Resolve runtime policy by environment + ring. Support canary eval before promote to stable. All with RBAC, audit, CLI, console.

**Tech Stack:** Python 3.12, Pydantic v2, sqlite3, asyncio, Click, FastAPI/Jinja2

---

## File Structure

| New File | Purpose |
|----------|---------|
| `agent_app/governance/policy_ring.py` | ReleaseRing model, ReleaseRingStatus enum |
| `agent_app/runtime/policy_ring_store.py` | Protocol + InMemory + SQLite + factory |
| `agent_app/governance/policy_ring_assignment.py` | RingActivationAssignment model |
| `agent_app/runtime/policy_ring_assignment_store.py` | Protocol + InMemory + SQLite + factory |
| `agent_app/runtime/policy_ring_router.py` | PolicyRingRouter for request-scoped ring resolution |
| `agent_app/evals/canary.py` | CanaryEvalRunner, CanaryEvalResult |

| Modified File | Change |
|---------------|--------|
| `agent_app/core/context.py` | Add policy_ring field |
| `agent_app/runtime/policy_resolver.py` | Add resolve_active_bundle_for_ring / require_active_bundle_for_ring |
| `agent_app/runtime/policy_release.py` | Add create_ring, assign_activation_to_ring, promote_canary_to_stable, disable_ring, enable_ring |
| `agent_app/governance/policy_rbac.py` | Add 6 ring permissions |
| `agent_app/config/schema.py` | Add rings, ring_assignments config |
| `agent_app/config/loader.py` | Wire ring stores, router, resolver |
| `agent_app/cli.py` | Add ring group (list/create/assign/promote/disable/enable), canary eval |
| `agent_app/console/router.py` | Add ring routes |
| `agent_app/console/templates/policy_rings.html` | Ring list template |
| `agent_app/console/templates/policy_ring_detail.html` | Ring detail template |
| `agent_app/adapters/fastapi.py` | Wire ring stores |

---

### Task 1: ReleaseRing model

**Files:**
- Create: `agent_app/governance/policy_ring.py`
- Test: `tests/unit/test_policy_ring.py`

Create `ReleaseRingStatus` (StrEnum: ENABLED, DISABLED) and `ReleaseRing` (BaseModel):
- ring_id (str, `ring_` prefix)
- environment (str)
- name (str)
- description (str | None)
- status (ReleaseRingStatus, default ENABLED)
- is_default (bool, default False)
- created_at (datetime, tz-aware)
- updated_at (datetime, tz-aware)

Tests: default status enabled, is_default default false, requires ring_id/environment/name, tz-aware timestamps, all statuses valid.

---

### Task 2: ReleaseRingStore

**Files:**
- Create: `agent_app/runtime/policy_ring_store.py`
- Test: `tests/unit/test_policy_ring_store.py`

Protocol with: create, get, get_by_name(environment, name), list(environment=None), set_default(environment, ring_name), disable(environment, ring_name), enable(environment, ring_name).

InMemory + SQLite + factory. SQLite table: policy_release_rings with UNIQUE(environment, name).

Tests (~12): create/get, get_by_name, list by environment, duplicate name fails, set_default clears previous, disable/enable, SQLite cross-instance persistence, factory.

---

### Task 3: RingActivationAssignment model + store

**Files:**
- Create: `agent_app/governance/policy_ring_assignment.py`
- Create: `agent_app/runtime/policy_ring_assignment_store.py`
- Test: `tests/unit/test_policy_ring_assignment.py`

Model: `RingActivationAssignmentStatus` (ACTIVE, SUPERSEDED, DISABLED). `RingActivationAssignment`:
- assignment_id (str, `ra_` prefix)
- environment, ring_name, activation_id, bundle_id, config_hash
- status (default ACTIVE)
- assigned_by, reason (optional)
- created_at, superseded_at, superseded_by_assignment_id

Store Protocol: assign, get, get_active(environment, ring_name), list(environment=None, ring_name=None), disable_active(environment, ring_name, disabled_by, reason=None).

InMemory + SQLite + factory. Only one ACTIVE per environment+ring. Assigning supersedes previous.

Tests (~10): assign first, assign second supersedes first, get_active, list by env/ring, disable_active, SQLite persistence, factory.

---

### Task 4: Extend RunContext + RBAC + PolicyRingRouter

**Files:**
- Modify: `agent_app/core/context.py` — add policy_ring: str | None
- Modify: `agent_app/governance/policy_rbac.py` — add 6 ring permissions
- Create: `agent_app/runtime/policy_ring_router.py`
- Test: `tests/unit/test_policy_ring_router.py`, append to test_policy_rbac.py

RBAC: RING_CREATE, RING_ASSIGN, RING_PROMOTE, RING_DISABLE, RING_ENABLE, RING_VIEW. RING_VIEW default-allowed.

PolicyRingRouter:
```python
class PolicyRingRouter:
    def __init__(self, ring_store, default_ring="stable"): ...
    async def resolve_ring(self, environment: str, context: RunContext) -> str:
        # 1. context.policy_ring if set
        # 2. default ring for environment from store
        # 3. configured default_ring
        # 4. raise if selected ring disabled/missing
```

Tests (~8): explicit ring wins, default ring used, disabled ring raises, missing ring raises, no ring_store falls back to default.

---

### Task 5: ActivePolicyResolver ring-aware resolution

**Files:**
- Modify: `agent_app/runtime/policy_resolver.py`
- Test: `tests/unit/test_policy_resolver_rings.py`

Add to __init__: ring_assignment_store (optional), ring_store (optional).

New methods:
```python
async def resolve_active_bundle_for_ring(self, environment, ring_name) -> Any | None
async def require_active_bundle_for_ring(self, environment, ring_name) -> Any
```

Logic: check environment disabled → check ring disabled → get_active assignment → load activation → load bundle → verify hashes.

Cache key: `(environment, ring_name)` tuple.

Update `resolve_active_bundle(environment)` to delegate to default ring if ring stores configured (backward compat).

Tests (~8): resolves for ring, no assignment returns None, require raises, disabled ring blocks, disabled env blocks, hash mismatch raises, cache with ring key, backward compat without ring stores.

---

### Task 6: PolicyReleaseService ring APIs

**Files:**
- Modify: `agent_app/runtime/policy_release.py`
- Test: `tests/unit/test_policy_release_phase33.py`

Add to __init__: ring_store, ring_assignment_store, ring_router.

New methods:
- `create_ring(environment, name, created_by, context, description=None, is_default=False)` — requires RING_CREATE
- `assign_activation_to_ring(environment, ring_name, activation_id, assigned_by, context, reason=None)` — requires RING_ASSIGN, validates activation env + bundle hash
- `promote_canary_to_stable(environment, canary_ring, stable_ring, promoted_by, context, reason=None)` — requires RING_PROMOTE, assigns canary's activation to stable
- `disable_ring(environment, ring_name, disabled_by, context, reason=None)` — requires RING_DISABLE
- `enable_ring(environment, ring_name, enabled_by, context, reason=None)` — requires RING_ENABLE

All with audit events.

Tests (~12): create ring requires perm, create ring succeeds, assign requires perm, assign validates env, assign validates hash, promote canary works, disable ring blocks resolution, enable restores, audit events, assign wrong env fails.

---

### Task 7: Config schema and loader

**Files:**
- Modify: `agent_app/config/schema.py`
- Modify: `agent_app/config/loader.py`
- Test: `tests/unit/test_policy_release_config_phase33.py`

Schema: add `rings` (PolicyReleaseStoreConfig | None), `ring_assignments` (PolicyReleaseStoreConfig | None) to PolicyReleaseConfig. Add `ring` field to PolicyReleaseRuntimeConfig.

Loader: create ring_store, ring_assignment_store, ring_router. Wire into resolver and PolicyReleaseService. Auto-create default rings (stable, canary, internal) if config flag set.

Tests (~4): rings config, defaults None, backward compat, full config.

---

### Task 8: CLI commands

**Files:**
- Modify: `agent_app/cli.py`
- Test: `tests/unit/test_policy_release_cli_phase33.py`

Add `ring` subgroup under `policy_cli`:
- `ring list --config <path> --environment <env>`
- `ring create --config <path> --environment <env> --name <name> --actor-id <who> --permissions <list> [--description <desc>] [--is-default]`
- `ring assign --config <path> --environment <env> --ring <name> --activation-id <id> --actor-id <who> --permissions <list> [--reason <text>]`
- `ring promote --config <path> --environment <env> --from-ring <name> --to-ring <name> --actor-id <who> --permissions <list> [--reason <text>]`
- `ring disable --config <path> --environment <env> --ring <name> --actor-id <who> --permissions <list> [--reason <text>]`
- `ring enable --config <path> --environment <env> --ring <name> --actor-id <who> --permissions <list>`

Add `canary eval` command:
- `policy canary eval --config <path> --environment <env> --ring <name> --activation-id <id> --suite <path>`

Tests (~8): ring list, ring create, ring assign, ring promote, ring disable, ring enable, canary eval success, permission denied.

---

### Task 9: Console extensions

**Files:**
- Modify: `agent_app/console/router.py`
- Create: `agent_app/console/templates/policy_rings.html`
- Create: `agent_app/console/templates/policy_ring_detail.html`
- Modify: `agent_app/adapters/fastapi.py`
- Test: `tests/unit/test_policy_release_console_phase33.py`

Routes:
- GET /rings — ring list
- GET /rings/{environment}/{ring_name} — ring detail
- POST /rings — create ring
- POST /rings/{environment}/{ring_name}/assign — assign activation
- POST /rings/{environment}/{ring_name}/promote — promote
- POST /rings/{environment}/{ring_name}/disable — disable
- POST /rings/{environment}/{ring_name}/enable — enable

Tests (~7): ring list renders, ring detail renders, create POST, assign POST, promote POST, disable/enable, permission error clean.

---

### Task 10: Canary eval runner

**Files:**
- Create: `agent_app/evals/canary.py`
- Test: `tests/unit/test_canary_eval.py`

```python
class CanaryEvalResult(BaseModel):
    environment: str
    ring_name: str
    activation_id: str
    suite_name: str
    passed: bool
    total: int
    passed_count: int
    failed_count: int
    errors: list[str] = Field(default_factory=list)

class CanaryEvalRunner:
    def __init__(self, app: AgentApp): ...
    async def run_for_activation(self, activation_id, environment, ring_name, suite_path) -> CanaryEvalResult:
        # 1. Load eval suite
        # 2. Set policy_environment + policy_ring on context
        # 3. Run suite via existing EvalRunner
        # 4. Return CanaryEvalResult
```

Approach: Assign to canary ring first, run eval, return result. Assignment persists (caller decides whether to promote or rollback).

Tests (~4): eval passes, eval failure returns failed result, missing suite raises, missing activation raises.

---

### Task 11: Documentation + final verification

**Files:**
- Modify: `docs/policy_release.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Create: `docs/release_checklist_phase33.md`

Add Phase 33 section covering: release rings, ring assignments, canary eval flow, promote flow, resolver with ring, CLI examples, console workflows, audit events, design decisions, known limitations.

Run full test suite, verify Phase 31/32 tests pass, verify import boundaries.
