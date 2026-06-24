# Phase 57 Release Checklist

## v0.43.0 — Alert Delivery Operations Chain: Atomic Priority Queue & Daemon Deep Integration

### Phase 57 New Features

- [x] Atomic priority queue lifecycle: `claim_next` → `acknowledge`/`fail`/`requeue` with worker-id and lease-ttl
- [x] InMemory and SQLite priority queue stores with priority ordering and status filtering
- [x] Retry daemon deep integration: claims from priority queue, delivers via adapter, acks/requeues/fails atomically
- [x] Persistent daemon state store (`AlertDeliveryRetryDaemonState`) with InMemory and SQLite backends
- [x] Daemon state persistence across restarts (started_at, last_run_at, consecutive_failures, last_error)
- [x] Webhook signing key rotation with configurable `rotation_interval_hours` and AuditLogStore persistence
- [x] SQLite concurrency safety: WAL mode, busy_timeout, retry logic for concurrent writers
- [x] Batch DLQ replay with `batch_replay_dlq` CLI command and `replay_batch` API endpoint
- [x] Batch replay confirmation support (confirm=True skips dry-run check)
- [x] Structured error messages for replay entries (enum + detail + reason)
- [x] 6 new PolicyChangeEventType values for daemon lifecycle and priority queue operations
- [x] Daemon health/readiness/liveness FastAPI endpoints
- [x] Daemon state persistence CLI commands (daemon-state list/show/reset)
- [x] `claim_lease_seconds` and `reset_expired_leases_on_run` daemon config options
- [x] `available_at` field on `AlertPriorityQueueItem` for scheduled delivery
- [x] Config schema extensions (daemon_id, worker_id, claim_lease_seconds, state_store, webhook_rotation_interval_hours, batch_replay_enqueue_default)

### Phase 57 Tests

- [x] 93 atomic claim/ack/fail/requeue tests
- [x] 15 daemon + priority queue integration tests
- [x] 25 daemon state persistence tests
- [x] SQLite concurrency safety tests
- [x] Webhook signing key rotation tests
- [x] 202 config tests pass (event type count: 156)
- [x] 216 total Phase 57 + Phase 56 compatibility tests pass

### Pre-Release Verification

- [x] Run full test suite
- [x] Verify no regressions in earlier phases
- [x] Check version consistency (pyproject.toml, CHANGELOG.md, README.md)
- [x] Verify CLI commands work (help text, argument parsing)
- [x] Verify change event count matches PolicyChangeEventType enum

### Documentation

- [x] `CHANGELOG.md` — v0.43.0 entry added
- [x] `README.md` — v0.43.0 roadmap entry added
- [x] `pyproject.toml` — version updated to 0.43.0
