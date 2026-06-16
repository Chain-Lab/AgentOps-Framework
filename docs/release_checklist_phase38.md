# Phase 38 Release Checklist: Runtime Policy Enforcement Points

## Pre-Release

- [ ] All Phase 38 unit tests passing
  - [ ] `tests/unit/test_runtime_policy.py` — models, store, evaluator, service, config tests
  - [ ] `tests/unit/test_runtime_policy_executor_integration.py` — ToolExecutor + resume integration
  - [ ] `tests/unit/test_runtime_policy_cli.py` — CLI serialization + command tests
  - [ ] `tests/unit/test_runtime_policy_console.py` — console page tests

## Regression

- [ ] No regressions in Phase 37 tests
- [ ] No regressions in Phase 36 tests
- [ ] No regressions in existing ToolExecutor tests
- [ ] No regressions in existing approval resume tests
- [ ] No regressions in existing config/loader tests

## Architecture Boundaries

- [ ] No FastAPI imports in core/governance/runtime modules
- [ ] No Jinja2 imports in core/governance/runtime modules
- [ ] Runtime policy store uses Protocol pattern
- [ ] Console templates only mount when console is enabled

## Verification

- [ ] Runtime deny blocks tool execution
- [ ] Runtime require_approval interrupts tool execution
- [ ] Resume blocked if policy changed to deny
- [ ] No duplicate approval when both ToolSpec and runtime policy require
- [ ] Existing low-risk tools still execute normally
- [ ] Inline rules loaded from config
- [ ] CLI list/create/enable/disable/evaluate work
- [ ] Console pages render correctly

## Documentation

- [ ] `docs/policy_release.md` Phase 38 section complete
- [ ] `CHANGELOG.md` v0.26.0 entry complete
- [ ] `README.md` Phase 38 in roadmap
- [ ] Release checklist created

## Sign-Off

- [ ] All checklist items passing
- [ ] Full test suite passes (0 failures)
- [ ] Ready for merge
