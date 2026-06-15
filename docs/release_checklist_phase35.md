# Release Checklist — Phase 35: Multi-Environment Rollout Orchestration

## Acceptance Criteria

- [x] Full test suite passes
- [x] RolloutPlan and RolloutStep models exist
- [x] Memory and SQLite rollout stores work
- [x] RolloutService works with all step types
- [x] CLI rollout lifecycle works
- [x] Console rollout pages and actions work
- [x] Audit and change events are emitted
- [x] Existing Phase 31/32/33/34 behavior remains compatible
- [x] Docs and changelog updated
- [x] Import boundaries preserved

## New Files

- agent_app/governance/policy_rollout.py
- agent_app/runtime/policy_rollout_store.py
- agent_app/runtime/policy_rollout_service.py
- agent_app/console/templates/policy_rollouts.html
- agent_app/console/templates/policy_rollout_detail.html
- agent_app/console/templates/policy_rollout_create.html
- tests/unit/test_policy_rollout.py
- tests/unit/test_policy_rollout_store.py
- tests/unit/test_policy_rollout_service.py
- tests/unit/test_policy_rollout_config.py
- tests/unit/test_policy_rollout_cli.py
- tests/unit/test_policy_rollout_console.py

## Modified Files

- agent_app/governance/policy_change_event.py
- agent_app/governance/policy_rbac.py
- agent_app/config/schema.py
- agent_app/config/loader.py
- agent_app/core/app.py
- agent_app/cli.py
- agent_app/console/router.py
- agent_app/adapters/fastapi.py
- agent_app/console/templates/base.html
- docs/policy_release.md
- CHANGELOG.md
- README.md

## Test Results

- Phase 35 tests: 89 passed
- Phase 34 regression tests: 65 passed
- Import boundaries: OK

## Known Limitations

- No background scheduler
- No external CI/CD integration
- No deployment platform integration
- Step approval is MVP/block-only
- No automatic production metric rollback
- No distributed execution lock
- Rollout execution is local command/API driven
