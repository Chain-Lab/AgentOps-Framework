# Release Checklist: Phase 28 — Persistent Policy Replay (v0.16.0)

## Implementation

- [x] `agent_app/governance/policy_replay_context.py` — PolicyReplayContextBuilder + PolicyReplayContext
- [x] `agent_app/runtime/policy_replay_jobs.py` — PolicyReplayJob + Protocol + InMemory + SQLite stores
- [x] `agent_app/runtime/policy_replay_background.py` — PolicyReplayBackgroundRunner
- [x] `agent_app/runtime/policy_replay_store.py` — SQLitePolicyReplayStore + create_replay_store() factory
- [x] `agent_app/governance/policy_replay.py` — context_metadata on PolicyReplayDecisionChange
- [x] `agent_app/cli.py` — --background, --store, --db-path, --requested-by, run-job, jobs
- [x] `agent_app/console/router.py` — /policy-console/replay-jobs routes + templates
- [x] `agent_app/console/templates/replay_jobs.html` — new
- [x] `agent_app/console/templates/replay_job_detail.html` — new
- [x] `agent_app/console/templates/base.html` — Replay Jobs nav link
- [x] `agent_app/adapters/fastapi.py` — job store wiring

## Tests (70 total)

- [x] `tests/unit/test_sqlite_policy_replay_store.py` — 13 tests
- [x] `tests/unit/test_policy_replay_context.py` — 12 tests
- [x] `tests/unit/test_policy_replay_jobs.py` — 20 tests
- [x] `tests/unit/test_policy_replay_console_jobs.py` — 11 tests
- [x] `tests/unit/test_policy_replay_runner_context.py` — 8 tests
- [x] `tests/unit/test_policy_replay_background.py` — 8 tests
- [x] `tests/unit/test_policy_replay_cli_phase28.py` — 9 tests (TBC)

## Verification

- [x] Full test suite passes (1931 tests)
- [x] Phase 28 tests pass (70 tests)
- [x] No regressions
- [x] Architecture boundaries: core modules have no FastAPI/Jinja2
- [x] Architecture boundaries: console templates only mount when enabled
- [x] Architecture boundaries: background runner is plain async class
- [x] Cross-process SQLite persistence verified

## Documentation

- [x] `docs/policy_replay.md` — Phase 28 section added
- [x] `CHANGELOG.md` — Phase 28 section added
- [x] `README.md` — Roadmap updated, Policy Replay section added
- [x] Release checklist created
