# Release Checklist: Phase 30 — Policy Promotion Approval, RBAC, and Console Write Governance (v0.18.0)

## Implementation

- [x] `agent_app/governance/policy_rbac.py` — PolicyReleasePermission (8 permissions), PolicyReleasePermissionChecker
- [x] `agent_app/governance/policy_promotion.py` — PromotionRequestStatus, PromotionRequest
- [x] `agent_app/runtime/promotion_store.py` — PromotionRequestStore protocol, InMemoryPromotionRequestStore, SQLitePromotionRequestStore, create_promotion_store() factory
- [x] `agent_app/runtime/policy_release.py` — PolicyReleasePermissionError(PermissionError), _check_permission(), _write_audit(), request_promotion(), approve_promotion(), reject_promotion(), execute_promotion() with RBAC + audit
- [x] `agent_app/config/schema.py` — promotions store config, require_promotion_approval, allow_gate_bypass
- [x] `agent_app/config/loader.py` — promotion_store wiring into PolicyReleaseService
- [x] `agent_app/cli.py` — policy promotion request/list/approve/reject/execute subcommands
- [x] `agent_app/console/router.py` — /promotions, /promotions/{id} GET routes + POST create/approve/reject/execute + _promotion_to_row()/_promotion_to_detail() helpers
- [x] `agent_app/console/templates/policy_promotions.html` — new
- [x] `agent_app/console/templates/policy_promotion_detail.html` — new
- [x] `agent_app/console/templates/base.html` — Promotions nav link
- [x] `agent_app/adapters/fastapi.py` — _get_promotion_store() and release_service wiring

## Tests (96 total)

- [x] `tests/unit/test_policy_rbac.py` — 4 RBAC tests
- [x] `tests/unit/test_policy_promotion.py` — 8 PromotionRequest model tests
- [x] `tests/unit/test_policy_promotion_store.py` — 18 store tests (10 InMemory + 5 SQLite + 3 factory)
- [x] `tests/unit/test_policy_release.py` — 13 RBAC + promotion lifecycle tests (+ 1 config schema test)
- [x] `tests/unit/test_policy_release_cli.py` — 14 CLI tests (8 Phase 29 + 6 promotion CLI)
- [x] `tests/unit/test_policy_release_console.py` — 20 console tests (12 Phase 29 + 8 promotion console)

## Verification

- [x] Phase 30 tests pass (96 tests)
- [x] No regressions in existing test suite
- [x] Architecture boundaries: core modules have no FastAPI/Jinja2
- [x] Architecture boundaries: console templates only mount when enabled
- [x] Architecture boundaries: release service uses store protocols
- [x] PolicyReleasePermissionError extends PermissionError for CLI exception handling
- [x] promotions config defaults to None for backward compatibility
- [x] Console POST handlers catch PermissionError separately (renders as page message)
- [x] Gate bypass requires config flag + permission + bypass_reason
- [x] Console pages render correctly with and without stores

## Documentation

- [x] `docs/policy_release.md` — Phase 30 documentation (RBAC, PromotionRequest, gate bypass, CLI, console)
- [x] `CHANGELOG.md` — Phase 30 section (0.18.0)
- [x] `README.md` — v0.18 added to roadmap
- [x] Release checklist created
