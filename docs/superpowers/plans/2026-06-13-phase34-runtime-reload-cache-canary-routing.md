# Phase 34: Runtime Reload Hooks, Cache Invalidation, and Deterministic Canary Routing

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make runtime policy resolution more operationally usable by adding policy change events, runtime reload notifications, cache invalidation hooks, resolver refresh by environment/ring, deterministic percentage-based canary routing, CLI reload/status/routing commands, console reload/events/routing pages, tests and documentation.

**Architecture:** Define a PolicyChangeEvent model and append-only store. Build a PolicyReloadManager that emits reload events and refreshes resolver cache. Extend ActivePolicyResolver with cache_status/refresh/clear_cache. Add deterministic canary percentage routing to PolicyRingRouter. Emit change events from PolicyReleaseService. Wire everything through config and loader. Add CLI commands and console pages.

**Tech Stack:** Python 3.12, Pydantic v2, sqlite3, asyncio, Click, FastAPI/Jinja2, hashlib (stdlib)

---

## File Structure

| New File | Purpose |
|----------|---------|
| `agent_app/governance/policy_change_event.py` | PolicyChangeEventType enum, PolicyChangeEvent model |
| `agent_app/runtime/policy_change_event_store.py` | Protocol + InMemory + SQLite + factory |
| `agent_app/runtime/policy_reload.py` | PolicyReloadTarget, PolicyReloadResult, PolicyReloadHook protocol, PolicyReloadManager |
| `agent_app/console/templates/policy_events.html` | Events list template |
| `agent_app/console/templates/policy_reload.html` | Reload/status template |
| `agent_app/console/templates/policy_routing_simulate.html` | Routing simulator template |

| Modified File | Change |
|---------------|--------|
| `agent_app/runtime/policy_resolver.py` | Add cache_status(), refresh(env, ring), clear_cache(env, ring) |
| `agent_app/runtime/policy_ring_router.py` | Add RingRoutingConfig, deterministic canary percentage routing |
| `agent_app/runtime/policy_release.py` | Inject event_store + reload_manager; emit change events from each state change method |
| `agent_app/governance/policy_rbac.py` | Add RELOAD_REQUEST, RELOAD_VIEW, EVENT_VIEW, ROUTING_SIMULATE permissions |
| `agent_app/core/context.py` | No changes needed (policy_environment + policy_ring already exist) |
| `agent_app/runtime/app_runner.py` | Use PolicyRingRouter; record policy env/ring/bundle metadata in result |
| `agent_app/config/schema.py` | Add PolicyChangeEventsConfig, PolicyReloadConfig, RingRoutingConfig |
| `agent_app/config/loader.py` | Wire event_store, reload_manager, resolver, ring_router with routing |
| `agent_app/cli.py` | Add reload request/status, events list, routing simulate commands |
| `agent_app/console/router.py` | Add events, reload, routing routes |
| `agent_app/adapters/fastapi.py` | Wire event_store, reload_manager to console router |
| `docs/policy_release.md` | Phase 34 section |
| `CHANGELOG.md` | v0.22.0 entry |
| `README.md` | v0.22 roadmap entry |
| `docs/release_checklist_phase34.md` | Release checklist |

---

### Task 1: PolicyChangeEvent model

**Files:**
- Create: `agent_app/governance/policy_change_event.py`
- Test: `tests/unit/test_policy_change_event.py`

Create `PolicyChangeEventType` (StrEnum with 12 values matching the spec) and `PolicyChangeEvent` (BaseModel):
- event_id (str, `pce_` prefix)
- event_type (PolicyChangeEventType)
- environment (str | None)
- ring_name (str | None)
- bundle_id (str | None)
- activation_id (str | None)
- assignment_id (str | None)
- actor_id (str | None)
- reason (str | None)
- data (dict[str, Any], default_factory=dict)
- created_at (datetime, tz-aware)

Tests (~6): creates event, event_id prefix, timezone-aware datetime, data default empty dict, all event types valid, optional fields default None.

---

### Task 2: PolicyChangeEventStore

**Files:**
- Create: `agent_app/runtime/policy_change_event_store.py`
- Test: `tests/unit/test_policy_change_event_store.py`

Protocol with: append, get, list(environment, ring_name, since, limit), latest(environment, ring_name).

InMemory + SQLite + factory. SQLite table: `policy_change_events` with `data_json TEXT NOT NULL` for the data dict.

Behavior:
- Events are append-only (no update/delete)
- `list()` returns chronological order (oldest first)
- `latest()` returns most recent event matching filters
- SQLite persists across instances
- No event mutation API

Tests (~8): append/get, list by environment, list by ring, list with since, latest, SQLite persistence, chronological order, factory.

---

### Task 3: PolicyReloadManager

**Files:**
- Create: `agent_app/runtime/policy_reload.py`
- Test: `tests/unit/test_policy_reload.py`

Models:
- `PolicyReloadTarget(BaseModel)`: environment (str | None), ring_name (str | None)
- `PolicyReloadResult(BaseModel)`: target (PolicyReloadTarget), refreshed (bool), event_id (str | None), error (str | None), refreshed_at (datetime)

Protocol:
- `PolicyReloadHook(Protocol)`: async reload_policy(target) -> PolicyReloadResult

Manager:
- `PolicyReloadManager.__init__(resolver, event_store=None)`
- `register_hook(name, hook)`: store named hook
- `async request_reload(environment, ring_name, requested_by, reason) -> list[PolicyReloadResult]`:
  1. Append `MANUAL_RELOAD_REQUESTED` event to event_store
  2. Call `refresh_resolver(environment, ring_name)`
  3. Call all registered hooks
  4. Collect results; hook failures captured in error field, not raised
  5. Return all results
- `async refresh_resolver(environment, ring_name) -> PolicyReloadResult`:
  1. Call `resolver.refresh(environment, ring_name)`
  2. Return PolicyReloadResult with refreshed=True

Tests (~6): request_reload appends event, refresh_resolver clears cache, hook called, hook failure captured not raised, multiple hooks all invoked, no event_store still works.

---

### Task 4: ActivePolicyResolver cache improvements

**Files:**
- Modify: `agent_app/runtime/policy_resolver.py`
- Test: `tests/unit/test_policy_resolver_phase34.py`

Add methods:
- `async refresh(environment=None, ring_name=None)`:
  - If both None: clear entire cache
  - If environment provided: clear env key + ring tuple keys for that env
  - If environment + ring_name: clear (env, ring) tuple key
- `clear_cache(environment=None, ring_name=None)`:
  - Same logic as refresh but synchronous
- `cache_status() -> dict[str, Any]`:
  - Returns `{"entries": len, "keys": list_of_keys, "ttl": self._cache_ttl}`

Test cache key changes: ensure disabled env/ring doesn't serve stale cache by checking in `resolve_active_bundle` and `resolve_active_bundle_for_ring` — if the resolved result is None (disabled), cache should store None, not return a stale bundle.

Tests (~6): cache_status reports entries, clear specific environment, clear all, refresh specific target, disabled env doesn't serve stale cache, cache key includes ring tuple.

---

### Task 5: Emit policy change events from PolicyReleaseService

**Files:**
- Modify: `agent_app/runtime/policy_release.py`
- Test: `tests/unit/test_policy_release_phase34.py`

Add to `__init__`: `event_store=None`, `reload_manager=None`

Emit change events after each state change:
- `create_bundle` → BUNDLE_CREATED
- `run_gate` → GATE_COMPLETED (after gate succeeds)
- `promote` → PROMOTION_EXECUTED
- `execute_promotion` → ACTIVATION_CREATED
- `rollback` / `rollback_environment` → ACTIVATION_ROLLED_BACK
- `disable_policy_environment` → ENVIRONMENT_DISABLED
- `enable_policy_environment` → ENVIRONMENT_ENABLED
- `assign_activation_to_ring` → RING_ASSIGNED
- `promote_canary_to_stable` → RING_PROMOTED
- `disable_ring` → RING_DISABLED
- `enable_ring` → RING_ENABLED

Rules:
- Emit only after state change succeeds
- If event_store is None, skip emission (backward compat)
- If emission fails and strict mode is not enabled, log warning and continue
- If reload_manager is configured and auto_refresh is enabled, call reload_manager.refresh_resolver after emission

Add `strict` flag to `__init__` (default False).

Tests (~8): activation creates change event, rollback creates change event, ring assign creates change event, ring promote creates change event, environment disable creates change event, no event_store skips emission, auto refresh called if configured, emission failure non-strict continues.

---

### Task 6: Deterministic canary percentage routing

**Files:**
- Modify: `agent_app/runtime/policy_ring_router.py`
- Test: `tests/unit/test_policy_ring_router_phase34.py`

Add config model (in schema.py or router file — put in router file for simplicity, importable):
```python
class RingRoutingConfig(BaseModel):
    enabled: bool = False
    canary_percentage: int = Field(default=0, ge=0, le=100)
    canary_ring: str = "canary"
    stable_ring: str = "stable"
    hash_key: Literal["actor_id", "user_id", "tenant_id"] = "actor_id"
```

Modify `PolicyRingRouter.__init__` to accept optional `routing_config: RingRoutingConfig | None = None`.

Modify `resolve_ring`:
1. If `context.policy_ring` is explicitly set, use it (current behavior)
2. Else if routing_config is enabled:
   a. Get hash key value from context (context.user_id for user_id, context.actor_id mapped from context.user_id or metadata, context.tenant_id for tenant_id)
   b. If hash key value is None/empty, return stable_ring
   c. Compute SHA-256 hash of f"{environment}:{hash_key_value}"
   d. Take first 8 hex chars, convert to int, modulo 100
   e. If bucket < canary_percentage, return canary_ring
   f. Else return stable_ring
   g. Validate ring exists and is enabled (current validation)
3. Else use store default (current behavior)
4. Else use configured fallback (current behavior)

Tests (~9): explicit policy_ring wins, canary 0 routes stable, canary 100 routes canary, same actor routes consistently, different actors distribute deterministically, missing hash key routes stable, disabled selected ring raises, missing selected ring raises, invalid percentage rejected (config validation).

---

### Task 7: AppRunner integration

**Files:**
- Modify: `agent_app/runtime/app_runner.py`
- Test: `tests/unit/test_apprunner_phase34.py`

Changes:
1. Add `ring_router: Any = None` param to `__init__`
2. In `run()`, after resolving active policy via `_resolve_active_policy`:
   a. If `ring_router` is available and `context.policy_ring` is not set:
      - `ring_name = await ring_router.resolve_ring(context.policy_environment or "dev", context)`
      - Set `context.policy_ring = ring_name`
      - If resolver has ring support, resolve for ring: `bundle = await resolver.resolve_active_bundle_for_ring(env, ring_name)`
      - Set `context.resolved_policy_bundle = bundle`
3. Record policy metadata in result:
   ```python
   result.metadata["policy_environment"] = context.policy_environment
   result.metadata["policy_ring"] = context.policy_ring
   if context.resolved_policy_bundle is not None:
       result.metadata["policy_bundle_id"] = context.resolved_policy_bundle.bundle_id
       result.metadata["policy_config_hash"] = context.resolved_policy_bundle.config_hash
   ```

Also update `AgentApp.__init__` and `_ensure_runner()` to pass `ring_router`.

Tests (~5): result metadata includes environment, metadata includes selected ring, metadata includes bundle id when resolved, require_active_policy fails on disabled ring, ring_router used when policy_ring not set.

---

### Task 8: Config schema and loader

**Files:**
- Modify: `agent_app/config/schema.py`
- Modify: `agent_app/config/loader.py`
- Test: `tests/unit/test_policy_release_config_phase34.py`

Schema additions:
```python
class PolicyChangeEventsConfig(BaseModel):
    type: str = "memory"
    path: str | None = None
    strict: bool = False

class PolicyReloadConfig(BaseModel):
    auto_refresh: bool = True

class RingRoutingConfig(BaseModel):
    enabled: bool = False
    canary_percentage: int = Field(default=0, ge=0, le=100)
    canary_ring: str = "canary"
    stable_ring: str = "stable"
    hash_key: Literal["actor_id", "user_id", "tenant_id"] = "actor_id"
```

Add to PolicyReleaseConfig:
- `change_events: PolicyChangeEventsConfig | None = None`
- `reload: PolicyReloadConfig | None = None`
- Add `routing` field to PolicyReleaseRuntimeConfig or as sibling to rings

Loader changes:
1. Create event_store from change_events config
2. Create reload_manager if event_store configured, wire with resolver
3. Create RingRoutingConfig from runtime.routing if present
4. Pass routing_config to PolicyRingRouter constructor
5. Pass event_store and reload_manager to PolicyReleaseService
6. Pass ring_router to AppRunner via AgentApp
7. Attach event_store, reload_manager to app for console/CLI access

Tests (~5): change_events config, reload config, routing config, backward compat (all None), full config.

---

### Task 9: CLI commands

**Files:**
- Modify: `agent_app/cli.py`
- Test: `tests/unit/test_policy_release_cli_phase34.py`

Add commands under `policy_cli`:
- `reload request --config <path> --environment <env> --ring <name> --actor-id <who> --reason <text>`
- `reload status --config <path>`
- `events list --config <path> --environment <env> --ring <name> --limit <n>`
- `routing simulate --config <path> --environment <env> --actor-id <id> --user-id <id> --tenant-id <id>`

Reload request:
1. Build app, get reload_manager from app
2. Call `await reload_manager.request_reload(environment, ring_name, requested_by=actor_id, reason=reason)`
3. Print results as JSON

Reload status:
1. Build app, get resolver from app
2. Call `resolver.cache_status()`
3. Print as JSON

Events list:
1. Build app, get event_store from app
2. Call `await event_store.list(environment, ring_name, limit=limit)`
3. Print as JSON list

Routing simulate:
1. Build app, get ring_router from app
2. Build a RunContext with provided identity fields
3. Call `await ring_router.resolve_ring(environment, context)`
4. Print result with environment, selected_ring, routing_mode, hash_key, bucket, canary_percentage, reason

Tests (~6): reload request, reload status, events list, routing simulate, invalid percentage fails, missing config fails.

---

### Task 10: Console extensions

**Files:**
- Modify: `agent_app/console/router.py`
- Create: `agent_app/console/templates/policy_events.html`
- Create: `agent_app/console/templates/policy_reload.html`
- Create: `agent_app/console/templates/policy_routing_simulate.html`
- Modify: `agent_app/adapters/fastapi.py`
- Test: `tests/unit/test_policy_release_console_phase34.py`

Routes:
- `GET /policy-console/events` — events list
- `GET /policy-console/reload` — reload status (shows cache_status)
- `POST /policy-console/reload` — request reload
- `GET /policy-console/routing/simulate` — routing simulator form
- `POST /policy-console/routing/simulate` — run simulation

Events page: shows recent change events in a table with event_type, environment, ring_name, actor_id, created_at.

Reload page: shows resolver cache_status, form for environment/ring/reason/actor_id to request reload.

Routing simulator: form with environment, actor_id/user_id/tenant_id inputs; result shows selected ring, bucket, canary percentage.

POST routes catch PermissionError and render as page message.

Update `build_policy_console_router` signature to accept `event_store`, `reload_manager`, `ring_router`.

Update `_mount_policy_console` in fastapi.py to pass new stores.

Tests (~6): events page renders, reload page renders, reload POST works, routing simulator renders, routing simulator POST works, permission error clean.

---

### Task 11: RBAC + audit events

**Files:**
- Modify: `agent_app/governance/policy_rbac.py`
- Test: append to `tests/unit/test_policy_rbac.py`

Add permissions:
- `RELOAD_REQUEST = "policy.reload.request"`
- `RELOAD_VIEW = "policy.reload.view"`
- `EVENT_VIEW = "policy.event.view"`
- `ROUTING_SIMULATE = "policy.routing.simulate"`

Add RELOAD_VIEW, EVENT_VIEW to _DEFAULT_ALLOWED.

Audit events from PolicyReloadManager:
- `policy.reload.requested`
- `policy.reload.completed`
- `policy.reload.failed`

Audit events from router simulation:
- `policy.routing.simulated`
- `policy.routing.failed`

Audit events from cache operations:
- `policy.runtime.cache_cleared`
- `policy.runtime.cache_refreshed`

Audit events from change event emission:
- `policy.change_event.appended`
- `policy.change_event.append_failed`

Tests (~4): new permissions exist, default-allowed permissions, RELOAD_REQUEST requires context, EVENT_VIEW default-allowed.

---

### Task 12: Documentation + final verification

**Files:**
- Modify: `docs/policy_release.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Create: `docs/release_checklist_phase34.md`

Add Phase 34 section covering: policy change events, reload manager and hooks, resolver cache status/refresh, deterministic canary percentage routing, runtime metadata, CLI examples, console workflows, audit events, design decisions, known limitations.

Run full test suite, verify Phase 31/32/33 tests pass, verify import boundaries.
