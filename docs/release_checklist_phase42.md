# Release Checklist — Phase 42: Policy Release Automation and Simulation Gate Enforcement

**Version:** v0.30.0
**Date:** 2026-06-17
**Phase:** 42

## Pre-Release Checks

### 1. Code Completeness

- [x] ReleaseGateRequirement model (`governance/policy_release_gate_requirement.py`)
- [x] ReleaseGateRequirementStore Protocol + InMemory + SQLite (`governance/policy_release_gate_requirement_store.py`)
- [x] ReleaseGateAutomationService (`runtime/policy_release_gate_automation.py`)
- [x] PromotionRequest extension with simulation gate fields
- [x] RolloutStep extension with simulation gate fields
- [x] PolicyReleaseService enforcement (block execute_promotion when gate required/failed/expired)
- [x] RolloutService step gate blocking
- [x] SimulationGateEnforcementConfig in config schema
- [x] Config loader wiring for requirement store/service/enforcement flags
- [x] CLI commands: policy promotion gate require/run/attach/status
- [x] Console promotion gate pages

### 2. RBAC Permissions

- [x] PROMOTION_GATE_REQUIRE (`policy.promotion.gate.require`) — requires grant
- [x] PROMOTION_GATE_RUN (`policy.promotion.gate.run`) — requires grant
- [x] PROMOTION_GATE_ATTACH (`policy.promotion.gate.attach`) — requires grant
- [x] PROMOTION_GATE_VIEW (`policy.promotion.gate.view`) — default allowed
- [x] ROLLOUT_GATE_ATTACH (`policy.rollout.gate.attach`) — requires grant
- [x] ROLLOUT_GATE_VIEW (`policy.rollout.gate.view`) — default allowed

### 3. Change Events

- [x] PROMOTION_GATE_REQUIRED
- [x] PROMOTION_GATE_SATISFIED
- [x] PROMOTION_GATE_FAILED
- [x] PROMOTION_GATE_EXPIRED
- [x] PROMOTION_GATE_ATTACHED
- [x] PROMOTION_GATE_AUTOMATED_RUN_STARTED
- [x] PROMOTION_GATE_AUTOMATED_RUN_COMPLETED
- [x] PROMOTION_GATE_AUTOMATED_RUN_FAILED
- [x] ROLLOUT_GATE_REQUIRED
- [x] ROLLOUT_GATE_SATISFIED
- [x] ROLLOUT_GATE_FAILED

### 4. Test Coverage

- [x] ReleaseGateRequirement model tests
- [x] ReleaseGateRequirementStore InMemory tests
- [x] ReleaseGateRequirementStore SQLite tests
- [x] ReleaseGateAutomationService tests
- [x] PolicyReleaseService enforcement tests
- [x] RolloutService gate blocking tests
- [x] Config schema/loader tests
- [x] CLI gate command tests
- [x] Console gate page tests

### 5. Documentation

- [x] docs/policy_release.md — Phase 42 section
- [x] CHANGELOG.md — v0.30.0 entry
- [x] README.md — Phase 42 roadmap entry
- [x] docs/release_checklist_phase42.md — this checklist

### 6. Backward Compatibility

- [x] Missing simulation_gate_enforcement config preserves existing behavior
- [x] PromotionRequest without gate fields works unchanged
- [x] RolloutStep without gate fields works unchanged
- [x] All Phase 41 tests pass unchanged
- [x] All Phase 37-40 tests pass unchanged

## Post-Release Verification

- [ ] Run full test suite: `pytest tests/unit/ -k "policy" --timeout=120 -q`
- [ ] Run targeted gate tests: `pytest tests/unit/test_policy_release_gate*.py -v --timeout=60`
- [ ] Verify no regressions in existing policy tests
- [ ] Verify version consistency in pyproject.toml, CHANGELOG.md, README.md
