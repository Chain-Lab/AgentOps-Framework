# Release Checklist: Phase 32 — Policy Rollback, Emergency Disable, and Activation Safety Controls (v0.20.0)

## Implementation

- [x] `agent_app/governance/policy_environment.py` — PolicyEnvironmentStatus enum (ENABLED, DISABLED), PolicyEnvironmentState model with reason/actor/timestamp tracking
- [x] `agent_app/runtime/policy_environment_store.py` — PolicyEnvironmentStore protocol, InMemoryPolicyEnvironmentStore, SQLitePolicyEnvironmentStore, create_policy_environment_store() factory
- [x] `agent_app/governance/policy_activation.py` — Added rollback_of_activation_id and rollback_target_activation_id fields to PolicyActivation
- [x] `agent_app/runtime/policy_activation_store.py` — Added get_previous_activation() and rollback_to_activation() methods to protocol and both implementations; SQLite ALTER TABLE migration for rollback columns
- [x] `agent_app/governance/policy_rbac.py` — Added ENVIRONMENT_DISABLE, ENVIRONMENT_ENABLE, ENVIRONMENT_VIEW permissions; ENVIRONMENT_VIEW added to default-allowed set
- [x] `agent_app/runtime/policy_resolver.py` — Added environment_store param; disabled environments return None for resolve, RuntimeError for require with disabled reason
- [x] `agent_app/runtime/policy_release.py` — Added rollback_environment(), disable_policy_environment(), enable_policy_environment() with RBAC + audit; environment_store property
- [x] `agent_app/config/schema.py` — Added environments field to PolicyReleaseConfig (PolicyReleaseStoreConfig type)
- [x] `agent_app/config/loader.py` — Wired environment_store into PolicyReleaseService and ActivePolicyResolver
- [x] `agent_app/cli.py` — Added environment list/disable/enable and activation rollback commands with RBAC support
- [x] `agent_app/console/router.py` — Added environment_store param, environment detail page, disable/enable/rollback POST routes
- [x] `agent_app/console/templates/policy_environment_detail.html` — New template for environment detail page
- [x] `agent_app/adapters/fastapi.py` — Passes environment_store to console router

## Tests (75+ total)

- [x] `tests/unit/test_policy_environment.py` — 7 tests (model)
- [x] `tests/unit/test_policy_environment_store.py` — 11 tests (5 InMemory + 3 SQLite + 3 factory)
- [x] `tests/unit/test_policy_activation_rollback.py` — 12 tests (InMemory rollback, SQLite rollback, get_previous_activation, rollback fields, environment mismatch, missing target)
- [x] `tests/unit/test_policy_rbac.py` — 6 new Phase 32 tests (3 new permissions, default-allowed check, checker integration)
- [x] `tests/unit/test_policy_resolver_safety.py` — 6 tests (disabled resolve returns None, disabled require raises RuntimeError, enabled passes through, no environment store)
- [x] `tests/unit/test_policy_release_phase32.py` — 14 tests (rollback_environment, disable/enable, RBAC checks, audit events, resolver cache clearing)
- [x] `tests/unit/test_policy_release_config_phase32.py` — 4 tests (environments config, loader wiring)
- [x] `tests/unit/test_policy_release_cli_phase32.py` — 8 tests (environment list/disable/enable, activation rollback)
- [x] `tests/unit/test_policy_release_console_phase32.py` — 7 tests (environment detail page, disable/enable POST, rollback POST)

## Verification

- [x] Phase 32 tests pass (75+ tests)
- [x] No regressions in existing test suite
- [x] Architecture boundaries: core modules have no FastAPI/Jinja2
- [x] Architecture boundaries: console templates only mount when enabled
- [x] Architecture boundaries: release service uses store protocols
- [x] Disabled environment returns None for resolve, RuntimeError for require with reason
- [x] Rollback creates new activation (not modifying existing records)
- [x] Rollback fields (rollback_of_activation_id, rollback_target_activation_id) populated correctly
- [x] RBAC: ENVIRONMENT_DISABLE and ENVIRONMENT_ENABLE require explicit grant
- [x] RBAC: ENVIRONMENT_VIEW is default-allowed
- [x] Disable requires non-empty reason (validated by service)
- [x] Environment defaults to ENABLED when no state stored
- [x] Resolver cache cleared on disable/enable/rollback
- [x] SQLite ALTER TABLE migration for rollback columns works on existing databases
- [x] Console environment detail page renders with and without environment_store
- [x] Config backward compatible: configs without environments section work unchanged

## Documentation

- [x] `docs/policy_release.md` — Phase 32 section (PolicyEnvironmentState, store, rollback lifecycle, RBAC, resolver safety, CLI, console, audit events, design decisions, limitations)
- [x] `CHANGELOG.md` — Phase 32 section (0.20.0)
- [x] `README.md` — v0.20 added to roadmap
- [x] Release checklist created
