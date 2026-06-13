# Release Checklist: Phase 31 — Policy Runtime Activation, Environment Isolation, and Hot Reload Baseline (v0.19.0)

## Implementation

- [x] `agent_app/governance/policy_activation.py` — PolicyActivation model, PolicyActivationStatus enum (ACTIVE, SUPERSEDED, ROLLED_BACK)
- [x] `agent_app/runtime/policy_activation_store.py` — PolicyActivationStore protocol, InMemoryPolicyActivationStore, SQLitePolicyActivationStore, create_policy_activation_store() factory
- [x] `agent_app/runtime/policy_resolver.py` — ActivePolicyResolver with TTL-aware cache, _CacheEntry, resolve_active_bundle(), require_active_bundle(), refresh(), clear_cache()
- [x] `agent_app/runtime/policy_release.py` — Extended with activation_store, policy_resolver; execute_promotion() now creates activation records; added get_active_policy(), require_active_policy(), list_activations() methods + properties
- [x] `agent_app/config/schema.py` — Added PolicyReleaseRuntimeConfig (environment, require_active_policy, cache_ttl_seconds); PolicyReleaseConfig now has activations and runtime fields
- [x] `agent_app/config/loader.py` — Full wiring of activation_store and policy_resolver into PolicyReleaseService
- [x] `agent_app/cli.py` — Added `agentapp policy activation list` and `agentapp policy activation active` commands; extended promotion execute with --environment and --reason
- [x] `agent_app/console/router.py` — Added /activations, /activations/{id}, /environments routes + helpers
- [x] `agent_app/console/templates/policy_activations.html` — Dual-mode template (list + environments overview)
- [x] `agent_app/console/templates/policy_activation_detail.html` — Detail page
- [x] `agent_app/console/templates/base.html` — Activations nav link already present (from Task 7)
- [x] `agent_app/adapters/fastapi.py` — Pass activation_store to console router
- [x] `agent_app/core/context.py` — Added policy_environment and resolved_policy_bundle fields
- [x] `agent_app/core/app.py` — Added policy_resolver wiring through AgentApp to AppRunner
- [x] `agent_app/runtime/app_runner.py` — Added policy_resolver param, _resolve_active_policy() method, integration into run()

## Tests (38+ total)

- [x] `tests/unit/test_policy_activation.py` — 7 tests (model)
- [x] `tests/unit/test_policy_activation_store.py` — 12 tests (5 InMemory + 4 SQLite + 3 factory)
- [x] `tests/unit/test_policy_resolver.py` — 8 tests (resolve, cache, require, hash mismatch)
- [x] `tests/unit/test_policy_release_phase31.py` — 5 tests (service extensions)
- [x] `tests/unit/test_policy_release_config_phase31.py` — 4 tests (config schema)
- [x] `tests/unit/test_policy_release_cli.py` — 3 CLI tests (Phase 31 portion)
- [x] `tests/unit/test_policy_release_console.py` — 4 console tests (Phase 31 portion)
- [x] `tests/unit/test_context_phase31.py` — 2 tests (RunContext fields)
- [x] `tests/unit/test_apprunner_phase31.py` — 7 tests (resolver wiring)

## Verification

- [x] Phase 31 tests pass (38+ tests)
- [x] No regressions in existing test suite
- [x] Architecture boundaries: core modules have no FastAPI/Jinja2
- [x] Architecture boundaries: console templates only mount when enabled
- [x] Architecture boundaries: release service uses store protocols
- [x] Config hash verification detects bundle config drift at resolve time
- [x] Only one ACTIVE activation per environment (store-level enforcement)
- [x] Cache TTL configurable and defaults to 300 seconds
- [x] Backward compatible: configs without activations section work unchanged
- [x] require_active_policy defaults to False (opt-in enforcement)
- [x] Console pages render correctly with and without stores
- [x] RunContext.policy_environment and resolved_policy_bundle populated during run

## Documentation

- [x] `docs/policy_release.md` — Phase 31 section (PolicyActivation, store, resolver, config, CLI, console, design decisions, limitations)
- [x] `CHANGELOG.md` — Phase 31 section (0.19.0)
- [x] `README.md` — v0.19 added to roadmap
- [x] Release checklist created
