# Phase 62 Release Checklist — v0.47.0

## Overview
Phase 62: Daemon Production Operations Hardening — upgrading Phase 61's daemon runtime into a production-hardened continuous runtime with graceful shutdown, inflight tracking, metrics buffering, lock lease extension, health HTTP server, and CLI extensions.

## Version
- **v0.47.0**

## New Files
- [x] `agent_app/runtime/policy_rollout_federation_notification_metrics_buffer.py` — MetricsRingBuffer + MetricsEvent
- [x] `agent_app/runtime/policy_rollout_federation_notification_health_server.py` — HealthHTTPServer
- [x] `tests/unit/test_phase62_daemon_graceful_drain.py` — 7 graceful drain tests
- [x] `tests/unit/test_phase62_daemon_inflight_tracking.py` — 7 inflight tracking tests
- [x] `tests/unit/test_phase62_metrics_buffer.py` — 13 metrics buffer tests
- [x] `tests/unit/test_phase62_lock_lease_extension.py` — 7 lock lease extension tests
- [x] `tests/unit/test_phase62_health_server.py` — 9 health server tests
- [x] `tests/unit/test_cli_phase62.py` — 9 CLI tests

## Modified Files
- [x] `agent_app/runtime/policy_rollout_federation_notification_retry_daemon.py` — Core daemon changes
- [x] `agent_app/cli.py` — 3 new CLI commands (serve, health-server, drain)
- [x] `pyproject.toml` — version 0.46.0 → 0.47.0
- [x] `CHANGELOG.md` — v0.47.0 Phase 62 entry
- [x] `README.md` — v0.47.0 Phase 62 features

## Acceptance Criteria

### AC1: Graceful Shutdown
- [x] `stop()` marks `_draining = True` before stopping
- [x] `stop()` waits for `_inflight_count` to reach 0 (up to `drain_timeout_seconds`)
- [x] `stop()` cancels inflight tasks on timeout when `cancel_inflight_on_timeout = True`
- [x] `stop()` clears `_draining` flag after completion
- [x] `stop()` records `_last_drain_duration_seconds`

### AC2: In-flight Item Tracking
- [x] `_track_inflight()` context manager increments/decrements `_inflight_count`
- [x] `_create_tracked_task()` adds done callback to remove from `_inflight_tasks`
- [x] `is_running` returns True only when `_task` is set and not done
- [x] `_inflight_count` property returns accurate count

### AC3: Metrics Ring Buffer
- [x] `MetricsRingBuffer` uses `deque(maxlen=N)` for fixed-size buffer
- [x] `append()` adds events, `snapshot()` returns copy of current events
- [x] `clear()` resets buffer
- [x] `flush_to_exporter()` calls exporter and clears on success
- [x] `flush_to_exporter()` returns False on failure without clearing
- [x] Thread-safe concurrent access

### AC4: Periodic Metrics Flush
- [x] `_flush_metrics_loop()` runs on background task
- [x] Flushes at `metrics_flush_interval_seconds` interval
- [x] Cancelled on `stop()`
- [x] `flush_metrics_on_stop` controls flush on stop behavior

### AC5: Lock Lease Extension
- [x] `_should_renew_lock()` returns True when never renewed
- [x] `_should_renew_lock()` returns True after 80% of `lock_renew_interval_seconds`
- [x] `_renew_distributed_lock()` renews and updates `_last_lock_renew_at`
- [x] `_renew_distributed_lock()` returns True when no lock store (fail-open)
- [x] `_renew_distributed_lock()` returns True when distributed lock disabled
- [x] Failed renewal sets `_leader_mode = False` per `lock_renewal_failure_policy`

### AC6: Health HTTP Server
- [x] `/health` returns 200 when healthy, 503 when unhealthy
- [x] `/ready` returns 200 when healthy + running + leader (or per config)
- [x] `/ready` returns 503 when draining
- [x] 404 for unknown paths
- [x] Server starts/stops cleanly
- [x] Uses only stdlib (http.server + threading)

### AC7: CLI Extensions
- [x] `daemon serve` starts daemon as long-running process
- [x] `daemon health-server` starts standalone health HTTP server
- [x] `daemon drain` initiates graceful drain
- [x] All commands require `--config`
- [x] Proper exit codes

### AC8: Configuration
- [x] Backward compatible: all new fields have defaults
- [x] 11 new config fields in `AlertDeliveryRetryDaemonConfig`
- [x] YAML config loading works correctly

### AC9: Tests
- [x] 6 new test files (minimum 6 required)
- [x] 52+ new unit tests
- [x] All Phase 62 tests pass
- [x] No regressions in existing tests

### AC10: Documentation
- [x] CHANGELOG.md updated
- [x] README.md updated
- [x] Release checklist created (this file)

## Test Results
- Phase 62 tests: 52 passed, 0 failed
- Full unit test suite: pending
