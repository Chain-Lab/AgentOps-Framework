# Phase 34 Release Checklist: Runtime Reload Hooks, Cache Invalidation, and Deterministic Canary Routing

**Version:** v0.22.0
**Date:** 2026-06-13

## Pre-release Verification

### Unit Tests

- [ ] All Phase 34 unit tests pass:
  ```bash
  .venv/bin/python -m pytest tests/unit/test_policy_change_event.py tests/unit/test_policy_change_event_store.py tests/unit/test_policy_reload.py tests/unit/test_policy_resolver_phase34.py tests/unit/test_policy_ring_router_phase34.py tests/unit/test_policy_release_phase34.py tests/unit/test_policy_release_config_phase34.py tests/unit/test_policy_release_cli_phase34.py tests/unit/test_policy_release_console_phase34.py tests/unit/test_apprunner_phase34.py -q
  ```
- [ ] All Phase 31/32/33 regression tests pass:
  ```bash
  .venv/bin/python -m pytest tests/unit/test_policy_release.py tests/unit/test_policy_release_phase31.py tests/unit/test_policy_release_phase32.py tests/unit/test_policy_resolver.py tests/unit/test_policy_resolver_safety.py tests/unit/test_policy_rbac.py tests/unit/test_context_phase31.py tests/unit/test_apprunner_phase31.py tests/unit/test_policy_ring.py tests/unit/test_policy_ring_store.py tests/unit/test_policy_ring_assignment.py tests/unit/test_policy_ring_router.py tests/unit/test_policy_resolver_rings.py tests/unit/test_policy_release_phase33.py tests/unit/test_policy_release_config_phase33.py tests/unit/test_policy_release_cli_phase33.py tests/unit/test_policy_release_console_phase33.py tests/unit/test_canary_eval.py -q
  ```

### Import Boundary Check

- [ ] No FastAPI imports in core modules:
  - `agent_app/governance/policy_change_event.py`
  - `agent_app/runtime/policy_change_event_store.py`
  - `agent_app/runtime/policy_reload.py`
- [ ] No Jinja2 imports in core modules
- [ ] Console templates only mount when `policy_console.enabled` is set

### Documentation

- [ ] `docs/policy_release.md` — Phase 34 section added with all subsections
- [ ] `CHANGELOG.md` — v0.22.0 entry at top with Added section
- [ ] `README.md` — v0.22 entry added to roadmap
- [ ] `docs/release_checklist_phase34.md` — This file

## New Files

| File | Purpose | Tests |
|------|---------|-------|
| `agent_app/governance/policy_change_event.py` | PolicyChangeEventType enum (12 types), PolicyChangeEvent model | test_policy_change_event.py |
| `agent_app/runtime/policy_change_event_store.py` | PolicyChangeEventStore protocol, InMemory, SQLite, factory | test_policy_change_event_store.py |
| `agent_app/runtime/policy_reload.py` | PolicyReloadManager, ReloadResult, HookResult | test_policy_reload.py |
| `agent_app/console/templates/policy_events.html` | Events list template | test_policy_release_console_phase34.py |
| `agent_app/console/templates/policy_reload.html` | Reload status and request template | test_policy_release_console_phase34.py |
| `agent_app/console/templates/policy_routing.html` | Routing simulator template | test_policy_release_console_phase34.py |

## Modified Files

| File | Changes |
|------|---------|
| `agent_app/runtime/policy_resolver.py` | Added cache_status(), refresh(env, ring), clear_cache(env, ring) methods |
| `agent_app/runtime/policy_ring_router.py` | Added RingRoutingConfig, deterministic routing, simulate_routing() method |
| `agent_app/runtime/policy_release.py` | Added change_event_store, reload_manager, event emission after state changes |
| `agent_app/runtime/app_runner.py` | Added ring_router param, ring-aware resolution, policy metadata in AppRunResult |
| `agent_app/core/context.py` | Added policy metadata fields for ring routing |
| `agent_app/governance/policy_rbac.py` | Added RELOAD_REQUEST, RELOAD_VIEW, EVENT_VIEW, ROUTING_SIMULATE permissions |
| `agent_app/config/schema.py` | Added PolicyChangeEventsConfig, PolicyReloadConfig, RingRoutingConfig fields |
| `agent_app/config/loader.py` | Wired change_event_store, reload_manager, routing config |
| `agent_app/cli.py` | Added reload request/status, events list, routing simulate commands |
| `agent_app/console/router.py` | Added events, reload, routing simulator routes |
| `agent_app/adapters/fastapi.py` | Passes change_event_store and reload_manager to console router |

## RBAC Permissions

| Permission | Value | Default |
|-----------|-------|---------|
| `RELOAD_REQUEST` | `policy.reload.request` | Requires grant |
| `RELOAD_VIEW` | `policy.reload.view` | Allowed |
| `EVENT_VIEW` | `policy.event.view` | Allowed |
| `ROUTING_SIMULATE` | `policy.routing.simulate` | Allowed |

## Audit Events

| Event | Trigger |
|-------|---------|
| `policy.reload.requested` | Manual reload requested |
| `policy.reload.hook_succeeded` | Reload hook executed successfully |
| `policy.reload.hook_failed` | Reload hook raised an exception |
| `policy.event.emission_failed` | Change event emission failed (non-strict) |
| `policy.event.emission_strict_failed` | Change event emission failed (strict mode) |
| `policy.routing.simulated` | Routing simulation performed |

## Change Event Types

| Event Type | Trigger |
|-----------|---------|
| `BUNDLE_CREATED` | New bundle created |
| `BUNDLE_ACTIVATED` | Bundle promoted to ACTIVE |
| `BUNDLE_ARCHIVED` | Bundle archived |
| `BUNDLE_ROLLED_BACK` | Bundle rolled back |
| `GATE_PASSED` | Gate evaluation passed |
| `GATE_FAILED` | Gate evaluation failed |
| `PROMOTION_REQUESTED` | Promotion request created |
| `PROMOTION_APPROVED` | Promotion approved |
| `PROMOTION_REJECTED` | Promotion rejected |
| `PROMOTION_EXECUTED` | Promotion executed |
| `ACTIVATION_CHANGED` | Activation changed |
| `MANUAL_RELOAD_REQUESTED` | Manual reload requested |

## Known Limitations

1. Reload manager is local-process only (no distributed pub/sub)
2. SQLite event store is not a distributed event bus
3. No background polling daemon for resolver cache
4. No websocket push reload
5. No service mesh traffic splitting (canary routing is framework-level deterministic routing)
6. No automatic rollback based on live metrics
7. No multi-region rollout coordination

## Post-release

- [ ] Verify SQLite schema creation with fresh database for change_events table
- [ ] Verify backward compatibility: existing Phase 33 configs without change_events/reload/routing sections continue to work
- [ ] Verify console pages load correctly with change_event_store and reload_manager configured
- [ ] Verify CLI commands produce expected output (table and JSON formats)
- [ ] Verify deterministic routing produces consistent results across process restarts
