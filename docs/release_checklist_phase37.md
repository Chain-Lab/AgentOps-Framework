# Phase 37 Release Checklist: Separation of Duties and Multi-Approver Approval Policies

## Pre-Release

- [ ] All Phase 37 unit tests passing
  - [ ] `tests/unit/test_policy_rollout_approval_policy.py` — Policy model, decision model, evaluator tests
  - [ ] `tests/unit/test_policy_rollout_approval_store.py` — Store add_decision and expire_pending tests
  - [ ] `tests/unit/test_policy_rollout_approval_quorum.py` — Quorum integration tests + audit event tests
  - [ ] `tests/unit/test_policy_rollout_approval_config.py` — Policy config and loader tests
  - [ ] `tests/unit/test_policy_rollout_approval_cli.py` — CLI policy-aware approval tests
  - [ ] `tests/unit/test_policy_rollout_approval_console.py` — Console quorum display tests

## Regression

- [ ] No regressions in Phase 36 tests
  - [ ] `tests/unit/test_policy_rollout_approval.py` — RolloutStepApproval model tests
  - [ ] `tests/unit/test_policy_rollout_approval_store.py` — Store backward compat tests
  - [ ] `tests/unit/test_policy_rollout_service.py` — Service approval API tests
  - [ ] `tests/unit/test_policy_rollout_approval_config.py` — Config backward compat tests
  - [ ] `tests/unit/test_policy_rollout_approval_cli.py` — CLI backward compat tests
  - [ ] `tests/unit/test_policy_rollout_approval_console.py` — Console backward compat tests
- [ ] No regressions in Phase 35 tests
- [ ] No regressions in Phase 31-34 tests
- [ ] No regressions in Phase 29-30 tests

## Architecture Boundaries

- [ ] No FastAPI imports in core/governance/runtime modules
- [ ] No Jinja2 imports in core/governance/runtime modules
- [ ] No Starlette imports in core/governance/runtime modules
- [ ] Approval store uses Protocol pattern
- [ ] Console templates only mount when console is enabled

## Policy Model Verification

- [ ] RolloutApprovalPolicy exists with SINGLE and QUORUM types
- [ ] RolloutApprovalDecision exists with APPROVE and REJECT types
- [ ] EXPIRED status exists on RolloutStepApprovalStatus
- [ ] Validation rules enforced (required_approvals, expires_after_seconds, SINGLE must be 1)

## Evaluator Verification

- [ ] Requester self-approval denied when prohibited
- [ ] Creator self-approval denied when prohibited
- [ ] Missing role denied
- [ ] Missing permission denied
- [ ] Reason required denied when required
- [ ] Duplicate actor decision denied
- [ ] Already-resolved approval denied
- [ ] Expired approval denied
- [ ] Quorum status calculation correct
- [ ] Reject immediately rejects

## Store Verification

- [ ] add_decision works in InMemory
- [ ] add_decision works in SQLite
- [ ] expire_pending works in InMemory
- [ ] expire_pending works in SQLite
- [ ] Policy and decisions persisted in SQLite

## Service Verification

- [ ] Quorum approval: first approve keeps step BLOCKED
- [ ] Quorum approval: second approve unblocks step
- [ ] Reject fails step and plan
- [ ] Self-approval blocked by policy
- [ ] Creator approval blocked by policy
- [ ] Role restriction enforced
- [ ] Expiration enforced
- [ ] Audit events emitted for all actions

## CLI Verification

- [ ] `--roles` flag works on approve/reject
- [ ] `_approval_to_dict` includes policy/decisions/expires_at
- [ ] Expire command works
- [ ] Self-approval exits non-zero
- [ ] Missing role exits non-zero

## Console Verification

- [ ] Detail page shows decisions
- [ ] Detail page shows required approvals
- [ ] Detail page shows expires_at
- [ ] Approve with roles works
- [ ] Quorum pending message shows
- [ ] Quorum approved shows correctly

## Documentation

- [ ] `docs/policy_release.md` Phase 37 section complete
- [ ] `CHANGELOG.md` v0.25.0 entry complete
- [ ] `README.md` Phase 37 in roadmap
- [ ] Release checklist created

## Sign-Off

- [ ] All checklist items passing
- [ ] Full test suite passes (0 failures)
- [ ] Ready for merge
