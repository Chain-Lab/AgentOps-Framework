# Release Checklist — Phase 40

**Version:** v0.28.0
**Phase:** Policy Testing, Validation, and Historical Replay
**Date:** 2026-06-16

## Implementation Checklist

- [x] Policy simulation models (governance/policy_simulation.py)
- [x] Audit-to-simulation case extraction (runtime/policy_simulation_cases.py)
- [x] Candidate policy store (runtime/policy_candidate_store.py)
- [x] PolicySimulationService (runtime/policy_simulation_service.py)
- [x] RuntimePolicyValidator (runtime/policy_validation.py)
- [x] Export helpers (runtime/policy_compliance_export.py)
- [x] Config schema and loader
- [x] RBAC permissions
- [x] Audit event types
- [x] CLI simulation commands
- [x] Console simulation pages
- [x] Documentation

## Test Coverage

- [x] Simulation models tests (17 tests)
- [x] Case extraction tests (7 tests)
- [x] Candidate store tests (6 tests)
- [x] Simulation service tests (9 tests)
- [x] Validation tests (10 tests)
- [x] Export/config/RBAC/events tests (8 tests)
- [x] CLI tests (29 tests)
- [x] Console tests (11 tests)

## Acceptance Criteria

- [x] PolicySimulationReport model exists
- [x] Audit events can be converted into simulation cases
- [x] Candidate runtime policies can be evaluated without mutating active rules
- [x] Simulation report compares baseline vs candidate decisions
- [x] RuntimePolicyValidator works
- [x] CLI validate/replay/export commands work
- [x] Console simulation pages work
- [x] Export helpers work
- [x] Existing Phase 39 behavior remains backward compatible
- [x] Import boundaries preserved
