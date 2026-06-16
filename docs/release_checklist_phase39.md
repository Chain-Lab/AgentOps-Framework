# Phase 39 Release Checklist: Policy Observability and Analytics

## Pre-Release

- [ ] All Phase 39 tests passing
  - [ ] `tests/unit/test_policy_observability.py` — models, service, export, config, RBAC, events
  - [ ] `tests/unit/test_policy_observability_cli.py` — CLI report/export commands
  - [ ] `tests/unit/test_policy_observability_console.py` — console dashboard pages

## Regression

- [ ] No regressions in Phase 38 tests
- [ ] No regressions in existing config/loader tests

## Architecture

- [ ] No FastAPI imports in core/governance/runtime
- [ ] Service gracefully handles missing stores
- [ ] Empty report renders without crashing

## Verification

- [ ] Report command produces valid output
- [ ] Export JSON writes valid file
- [ ] Export CSV writes valid file
- [ ] Console dashboard renders
- [ ] Console report form works
- [ ] Window filtering works

## Documentation

- [ ] `docs/policy_release.md` Phase 39 section complete
- [ ] `CHANGELOG.md` v0.27.0 entry complete
- [ ] `README.md` Phase 39 in roadmap
- [ ] Release checklist created

## Sign-Off

- [ ] All checklist items passing
- [ ] Ready for merge
