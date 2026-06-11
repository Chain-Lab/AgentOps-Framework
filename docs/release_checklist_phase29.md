# Release Checklist: Phase 29 — Policy Release Gates & Versioned Policy Bundles (v0.17.0)

## Implementation

- [x] `agent_app/governance/policy_bundle.py` — PolicyBundle, PolicyBundleStatus, compute_config_hash(), PolicyBundleStore protocol, InMemoryPolicyBundleStore, SQLitePolicyBundleStore, create_bundle_store() factory
- [x] `agent_app/governance/policy_gate.py` — PolicyGateRule, PolicyGateStatus, PolicyGateResult, PolicyGateEvaluator
- [x] `agent_app/runtime/policy_gate_store.py` — PolicyGateStore protocol, InMemoryPolicyGateStore, SQLitePolicyGateStore, create_gate_store() factory
- [x] `agent_app/runtime/policy_release.py` — PolicyReleaseService with create_bundle, run_gate, promote, rollback + bundle_store/gate_store properties
- [x] `agent_app/config/schema.py` — PolicyGateRuleConfig, PolicyReleaseStoreConfig, PolicyReleaseConfig
- [x] `agent_app/config/loader.py` — extracts release_config from governance; stores on app._release_config
- [x] `agent_app/cli.py` — bundle create/list/active/promote/rollback; gate run/list; _get_release_service() lazy init
- [x] `agent_app/console/router.py` — /bundles, /bundles/{id}, /gates, /gates/{id} routes + data helpers
- [x] `agent_app/console/templates/bundles.html` — new
- [x] `agent_app/console/templates/bundle_detail.html` — new
- [x] `agent_app/console/templates/gates.html` — new
- [x] `agent_app/console/templates/gate_detail.html` — new
- [x] `agent_app/console/templates/base.html` — Bundles and Gates nav links
- [x] `agent_app/adapters/fastapi.py` — bundle_store and gate_store wiring via _get_bundle_store/_get_gate_store

## Tests (91 total)

- [x] `tests/unit/test_policy_bundle_store.py` — 30 tests (22 InMemory + 8 SQLite)
- [x] `tests/unit/test_policy_gate.py` — 15 tests for models and evaluator
- [x] `tests/unit/test_policy_gate_store.py` — 15 tests (6 InMemory + 6 SQLite + 3 factory)
- [x] `tests/unit/test_policy_release.py` — 11 tests (9 unit + 1 SQLite lifecycle + helpers)
- [x] `tests/unit/test_policy_release_cli.py` — 8 CLI integration tests
- [x] `tests/unit/test_policy_release_console.py` — 12 console page tests

## Verification

- [x] Phase 29 tests pass (91 tests)
- [x] No regressions in existing test suite
- [x] Architecture boundaries: core modules have no FastAPI/Jinja2
- [x] Architecture boundaries: console templates only mount when enabled
- [x] Architecture boundaries: release service uses store protocols
- [x] CLI tests use SQLite for cross-process persistence
- [x] Console pages render correctly with and without stores

## Documentation

- [x] `docs/policy_release.md` — Phase 29 documentation
- [x] `CHANGELOG.md` — Phase 29 section added
- [x] `README.md` — v0.17 added to roadmap
- [x] Release checklist created
