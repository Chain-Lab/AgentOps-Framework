# Phase 46 Release Checklist — Policy Rollout Federation and Conflict Detection

## Feature summary

Phase 46 introduces framework-level rollout federation for coordinating child rollout plans across tenants, environments, regions, rings, and target groups. It adds target and plan models, stores, conflict detection, coordinator service, CLI commands, console pages, RBAC, config, audit/change events, and optional notifications.

## Verification

- [ ] Federation model tests pass
- [ ] Federation store tests pass
- [ ] Conflict detector tests pass
- [ ] Federation service tests pass
- [ ] Federation config/loader/RBAC tests pass
- [ ] Federation CLI tests pass
- [ ] Federation console tests pass or skip only when optional dependencies are unavailable
- [ ] Existing Phase 45 rollout history tests pass
- [ ] Existing rollout/gate/promotion tests pass
- [ ] Full policy regression test subset passes

## New files

- `agent_app/governance/policy_rollout_federation.py`
- `agent_app/runtime/policy_rollout_federation_store.py`
- `agent_app/runtime/policy_rollout_conflict_detector.py`
- `agent_app/runtime/policy_rollout_federation_service.py`
- `agent_app/console/templates/policy_federation_targets.html`
- `agent_app/console/templates/policy_federation_target_detail.html`
- `agent_app/console/templates/policy_federation_plans.html`
- `agent_app/console/templates/policy_federation_plan_detail.html`
- `agent_app/console/templates/policy_federation_plan_create.html`
- `agent_app/console/templates/policy_federation_conflicts.html`
- `tests/unit/test_policy_rollout_federation_model.py`
- `tests/unit/test_policy_rollout_federation_store.py`
- `tests/unit/test_policy_rollout_conflict_detector.py`
- `tests/unit/test_policy_rollout_federation_service.py`
- `tests/unit/test_policy_rollout_federation_config.py`
- `tests/unit/test_policy_rollout_federation_cli.py`
- `tests/unit/test_policy_rollout_federation_console.py`

## Modified files

- `agent_app/governance/policy_rbac.py`
- `agent_app/governance/policy_change_event.py`
- `agent_app/config/schema.py`
- `agent_app/config/loader.py`
- `agent_app/core/app.py`
- `agent_app/cli.py`
- `agent_app/console/router.py`
- `agent_app/adapters/fastapi.py`
- `docs/policy_release.md`
- `CHANGELOG.md`
- `README.md`

## Known limitations

- Framework-level coordination only.
- No distributed locks.
- No external deployment engine.
- No Kubernetes/service mesh integration.
- No cross-process scheduler.
- Parallel strategy is logical and deterministic, not concurrent.
- Conflict detection depends on configured stores and current recorded state.
- Child rollout cancellation is deferred to a future phase.

## Phase 47 recommendation

Phase 47 should add policy rollout federation observability: federation-level timeline reconstruction, federation analytics, conflict trend reporting, target health summaries, and export helpers for federation reports. This builds on Phase 45 rollout history and Phase 46 federation state without adding external BI or distributed tracing backends.
