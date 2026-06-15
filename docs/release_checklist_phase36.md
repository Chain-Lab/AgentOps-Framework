# Phase 36 Release Checklist: Rollout Approval Workflow

## Pre-Release

- [ ] All Phase 36 unit tests passing
  - [ ] `tests/unit/test_policy_rollout_approval.py` — RolloutStepApproval model tests
  - [ ] `tests/unit/test_policy_rollout_approval_store.py` — RolloutStepApprovalStore (InMemory + SQLite) tests
  - [ ] `tests/unit/test_policy_rollout_service.py` — RolloutService approval API tests
  - [ ] `tests/unit/test_policy_rollout_approval_config.py` — RolloutApprovalConfig tests
  - [ ] `tests/unit/test_policy_rollout_approval_cli.py` — CLI approval command tests
  - [ ] `tests/unit/test_policy_rollout_approval_console.py` — Console approval page tests

## Regression

- [ ] No regressions in Phase 35 tests
  - [ ] `tests/unit/test_policy_rollout.py` — RolloutPlan/RolloutStep model tests
  - [ ] `tests/unit/test_policy_rollout_store.py` — RolloutPlanStore tests
  - [ ] `tests/unit/test_policy_rollout_config.py` — Rollout config tests
  - [ ] `tests/unit/test_policy_rollout_cli.py` — Rollout CLI tests
- [ ] No regressions in Phase 31-34 tests (activation, environment, ring, reload, change events)
- [ ] No regressions in Phase 29-30 tests (bundles, gates, promotions, RBAC)

## Architecture Boundaries

- [ ] No FastAPI imports in core/governance/runtime modules
- [ ] No Jinja2 imports in core/governance/runtime modules
- [ ] No Starlette imports in core/governance/runtime modules
- [ ] Approval store uses Protocol pattern — no direct SQLite coupling in service layer
- [ ] Console templates only mount when console is enabled

## CLI Verification

- [ ] `agentapp policy rollout approval list` returns approval records
- [ ] `agentapp policy rollout approval request` creates PENDING approval
- [ ] `agentapp policy rollout approval approve` transitions PENDING → APPROVED
- [ ] `agentapp policy rollout approval reject` transitions PENDING → REJECTED
- [ ] RBAC permission enforcement on all approval commands
- [ ] `--json` output works for all approval commands

## Console Verification

- [ ] Approval list page renders (`/rollouts/{id}/approvals`)
- [ ] Approval detail page renders (`/rollouts/{id}/approvals/{approval_id}`)
- [ ] Request approval form works (POST)
- [ ] Approve action works (POST)
- [ ] Reject action works (POST)
- [ ] Rollout detail page shows approval state for BLOCKED steps

## Documentation

- [ ] `docs/policy_release.md` Phase 36 section complete
- [ ] `CHANGELOG.md` v0.24.0 entry complete
- [ ] `README.md` Phase 36 in roadmap
- [ ] Release checklist created

## Approval Behavior Verification

- [ ] `requires_approval=True` step in `run_next_step()` auto-creates PENDING approval
- [ ] Approved step transitions from BLOCKED → PENDING → executable
- [ ] Rejected approval fails step and plan
- [ ] `require_reason=True` config enforces non-empty reason on approve/reject
- [ ] Cancelled plan cancels pending approvals
- [ ] RolloutStepApprovalStore InMemory CRUD works
- [ ] RolloutStepApprovalStore SQLite CRUD works with cross-instance persistence

## Sign-Off

- [ ] All checklist items passing
- [ ] Ready for merge

## Phase 36.5: Test Isolation Hardening

- [x] Full policy console test suite passes in batch mode (0 failures)
- [x] Console TestClient isolation verified (no shared state between apps)
- [x] `asyncio.get_event_loop()` replaced with `_run_async()` helper in all console tests
- [x] Lease renewer test fixed (removed nested `asyncio.get_event_loop` in async context)
- [x] Regression test `test_console_isolation.py` added (3 tests)
- [x] `_run_async()` helper added to `tests/conftest.py`
- [x] `policy_console_app` fixture added to `tests/conftest.py`
- [x] 2513 tests pass in full batch mode
