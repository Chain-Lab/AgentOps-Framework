# Phase 33 Release Checklist: Release Rings, Canary Evaluation, and Ring-Aware Policy Resolution

**Version:** v0.21.0
**Date:** 2026-06-13

## Pre-release Verification

### Unit Tests

- [ ] All Phase 33 unit tests pass:
  ```bash
  .venv/bin/python -m pytest tests/unit/test_policy_ring.py tests/unit/test_policy_ring_store.py tests/unit/test_policy_ring_assignment.py tests/unit/test_policy_ring_router.py tests/unit/test_policy_resolver_rings.py tests/unit/test_policy_release_phase33.py tests/unit/test_policy_release_config_phase33.py tests/unit/test_policy_release_cli_phase33.py tests/unit/test_policy_release_console_phase33.py tests/unit/test_canary_eval.py -q
  ```
- [ ] All Phase 31/32 regression tests pass:
  ```bash
  .venv/bin/python -m pytest tests/unit/test_policy_release.py tests/unit/test_policy_release_phase31.py tests/unit/test_policy_release_phase32.py tests/unit/test_policy_resolver.py tests/unit/test_policy_resolver_safety.py tests/unit/test_policy_rbac.py tests/unit/test_context_phase31.py tests/unit/test_apprunner_phase31.py -q
  ```

### Import Boundary Check

- [ ] No FastAPI imports in core modules:
  - `agent_app/governance/policy_ring.py`
  - `agent_app/runtime/policy_ring_store.py`
  - `agent_app/governance/policy_ring_assignment.py`
  - `agent_app/runtime/policy_ring_assignment_store.py`
  - `agent_app/runtime/policy_ring_router.py`
  - `agent_app/evals/canary.py`
- [ ] No Jinja2 imports in core modules
- [ ] Console templates only mount when `policy_console.enabled` is set

### Documentation

- [ ] `docs/policy_release.md` — Phase 33 section added with all subsections
- [ ] `CHANGELOG.md` — Phase 33 section at top with Added/Changed/Architecture Boundaries
- [ ] `README.md` — v0.21 entry added to roadmap
- [ ] `docs/release_checklist_phase33.md` — This file

## New Files

| File | Purpose | Tests |
|------|---------|-------|
| `agent_app/governance/policy_ring.py` | ReleaseRing model, ReleaseRingStatus enum | 7 |
| `agent_app/runtime/policy_ring_store.py` | ReleaseRingStore protocol, InMemory, SQLite, factory | 13 |
| `agent_app/governance/policy_ring_assignment.py` | RingActivationAssignment model, RingActivationAssignmentStatus enum | 11 |
| `agent_app/runtime/policy_ring_assignment_store.py` | RingActivationAssignmentStore protocol, InMemory, SQLite, factory | 6 |
| `agent_app/runtime/policy_ring_router.py` | PolicyRingRouter for request-scoped ring resolution | 6 |
| `agent_app/evals/canary.py` | CanaryEvalRunner, CanaryEvalResult | 4 |
| `agent_app/console/templates/policy_rings.html` | Ring list template | 7 (console) |
| `agent_app/console/templates/policy_ring_detail.html` | Ring detail template | (console) |

## Modified Files

| File | Changes |
|------|---------|
| `agent_app/core/context.py` | Added `policy_ring: str \| None` field to RunContext |
| `agent_app/governance/policy_rbac.py` | Added RING_CREATE, RING_ASSIGN, RING_PROMOTE, RING_DISABLE, RING_ENABLE, RING_VIEW permissions; RING_VIEW default-allowed |
| `agent_app/runtime/policy_resolver.py` | Added ring_assignment_store, ring_store params; resolve_active_bundle_for_ring, require_active_bundle_for_ring methods |
| `agent_app/runtime/policy_release.py` | Added ring_store, ring_assignment_store, ring_router params; create_ring, assign_activation_to_ring, promote_canary_to_stable, disable_ring, enable_ring methods |
| `agent_app/config/schema.py` | Added rings, ring_assignments fields to PolicyReleaseConfig; ring field to PolicyReleaseRuntimeConfig |
| `agent_app/config/loader.py` | Wired ring stores, router, resolver |
| `agent_app/cli.py` | Added ring list/create/assign/promote/disable/enable and canary eval commands |
| `agent_app/console/router.py` | Added ring list/detail/create/assign/promote/disable/enable routes |
| `agent_app/adapters/fastapi.py` | Passes ring stores to console router |

## RBAC Permissions

| Permission | Value | Default |
|-----------|-------|---------|
| `RING_CREATE` | `policy.ring.create` | Requires grant |
| `RING_ASSIGN` | `policy.ring.assign` | Requires grant |
| `RING_PROMOTE` | `policy.ring.promote` | Requires grant |
| `RING_DISABLE` | `policy.ring.disable` | Requires grant |
| `RING_ENABLE` | `policy.ring.enable` | Requires grant |
| `RING_VIEW` | `policy.ring.view` | Allowed |

## Audit Events

| Event | Trigger |
|-------|---------|
| `policy.ring.created` | Ring created |
| `policy.ring.disabled` | Ring disabled |
| `policy.ring.enabled` | Ring re-enabled |
| `policy.ring.assignment.created` | Activation assigned to ring |
| `policy.ring.promoted` | Canary promoted to stable |
| `policy.ring.permission_denied` | RBAC check failed |
| `policy.canary.eval_started` | Canary eval started |
| `policy.canary.eval_completed` | Canary eval completed |
| `policy.canary.eval_failed` | Canary eval failed |

## Known Limitations

1. Canary eval does not inject ring/environment metadata into the eval runner
2. No automatic canary promotion based on eval results
3. No ring-level traffic splitting (percentage-based)
4. No ring diff/comparison view in console
5. Ring disable does not cascade to assignments
6. No ring promotion history linking canary and stable
7. No cross-environment promotion

## Post-release

- [ ] Verify SQLite schema creation with fresh database
- [ ] Verify backward compatibility: existing Phase 32 configs without ring sections continue to work
- [ ] Verify console pages load correctly with ring stores configured
- [ ] Verify CLI commands produce expected output (table and JSON formats)
