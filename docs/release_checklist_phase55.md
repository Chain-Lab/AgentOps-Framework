# Phase 55 Release Checklist

## v0.35.0 — Alert Delivery Closed Loop

### Phase 55 New Features

- [x] Retry daemon: `AlertDeliveryRetryDaemon` with start/stop/run_once
- [x] Priority queue: `AlertPriorityQueue` with severity-to-priority mapping
- [x] Archive cleanup: `ResumableArchiveCleanup` with checkpoint support
- [x] 10 new PolicyChangeEventType enum values
- [x] Change event wiring in all Phase 55 services
- [x] 7 CLI commands (daemon start/stop/status, priority list/update, archive-cleanup)
- [x] Console read-only pages for daemon status and archive cleanup
- [x] Config schema extensions (retry_daemon, write_actions, archive_cleanup)
- [x] Documentation updates

### Phase 55 Tests

- [x] 15 retry daemon tests (7 existing + 5 new change event tests + 3 edge cases)
- [x] 28 priority queue tests (22 existing + 3 new change event tests + 3 SQLite tests)
- [x] 21 archive cleanup tests (15 existing + 3 new change event tests + 3 SQLite tests)
- [x] 50 config tests pass (event type count: 150)
- [x] Total Phase 55: 69 tests pass

### Pre-Release Verification

- [ ] Run full test suite
- [ ] Verify no regressions in earlier phases
- [ ] Check version consistency (pyproject.toml, CHANGELOG.md, README.md)
- [ ] Verify CLI commands work (help text, argument parsing)
- [ ] Verify console pages render correctly
- [ ] Verify change event count matches PolicyChangeEventType enum
- [ ] Run smoke tests if applicable

### Documentation

- [x] `docs/policy_release.md` — Phase 55 section added
- [x] `CHANGELOG.md` — v0.35.0 entry added
- [x] `README.md` — v0.35.0 roadmap entry added
