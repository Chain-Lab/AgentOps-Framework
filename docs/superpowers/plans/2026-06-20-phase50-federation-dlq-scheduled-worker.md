# Phase 50: Federation Approval Dead-Letter Queue & Scheduled Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add DLQ for failed notifications, per-channel retry policy, and persistent scheduled worker to make federation notifications production-ready.

**Architecture:** DLQ store (Protocol + InMemory + SQLite) captures notifications that exceed retry limits. Retry policy supports default + per-channel overrides. Scheduled worker wraps existing escalation/notification tick with asyncio task lifecycle management.

**Tech Stack:** Python 3.12+, Pydantic, stdlib sqlite3, asyncio

---

## Task 1: DLQ Models

**Files:**
- Modify: `agent_app/governance/policy_rollout_federation_notification.py`
- Test: `tests/unit/test_policy_rollout_federation_notification.py`

Add to existing notification models file:
- `FederationNotificationDLQStatus` (StrEnum: pending, retried, purged, resolved)
- `FederationNotificationDLQReason` (StrEnum: max_retries_exceeded, delivery_failed, adapter_error, invalid_recipient, manual)
- `FederationNotificationDeadLetter` (Pydantic model with fdlq_ prefix, tz-aware datetimes)
- `FederationNotificationRetryPolicy` (Pydantic model: max_attempts, backoff_seconds, send_to_dlq)

Tests: ~15 tests covering enum values, model validation, id prefix, tz-aware datetime, defaults.

---

## Task 2: DLQ Store

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_notification_dlq_store.py`
- Test: `tests/unit/test_policy_rollout_federation_notification_dlq_store.py`

Implement:
- `FederationNotificationDLQStore` Protocol (create, get, list, mark_retried, mark_purged, delete)
- `InMemoryFederationNotificationDLQStore`
- `SQLiteFederationNotificationDLQStore`
- `create_federation_notification_dlq_store()` factory
- SQLite table: federation_notification_dlq with all fields from FederationNotificationDeadLetter
- list() supports filtering by status, federation_id, approval_id, channel + pagination (limit/offset)

Tests: ~25 tests covering CRUD, list by filters, pagination, mark_retried, mark_purged, SQLite persistence.

---

## Task 3: Notification Service DLQ Integration + Retry Policy

**Files:**
- Modify: `agent_app/runtime/policy_rollout_federation_notification_service.py`
- Test: `tests/unit/test_policy_rollout_federation_notification_service.py`

Modify `FederationNotificationService`:
- Add `dlq_store` and `retry_policy` parameters to `__init__`
- In `dispatch_pending`: apply retry policy per channel
  - On failure: check attempt_count vs policy max_attempts for that channel
  - Below max: mark_failed with next_attempt_at (using channel-specific backoff)
  - At/above max + send_to_dlq=True: write to DLQ, mark notification as dead_lettered
  - At/above max + send_to_dlq=False: just mark_failed (no DLQ)
- Add `get_retry_policy_for_channel(channel)` helper that returns channel override or default
- Add `FederationNotificationStatus.DEAD_LETTERED` to the enum (keep backwards compatible)
- Record change events and history events for DLQ creation

Tests: ~15 tests covering retry policy application, channel override, DLQ entry on max retries exceeded, no DLQ when send_to_dlq=False, last_error stored.

---

## Task 4: Scheduled Worker

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_scheduled_worker.py`
- Test: `tests/unit/test_policy_rollout_federation_scheduled_worker.py`

Implement:
- `FederationScheduledWorkerStatus` (StrEnum: stopped, running, stopping, failed)
- `FederationScheduledWorkerState` (Pydantic model: worker_id, status, interval_seconds, started_at, stopped_at, last_tick_at, last_error, tick_count)
- `FederationScheduledWorker.__init__` — escalation_worker, notification_service, distributed_lock, interval_seconds
- `FederationScheduledWorker.start()` — starts asyncio task that loops tick+sleep
- `FederationScheduledWorker.stop()` — sets stopping flag, cancels task
- `FederationScheduledWorker.status()` → FederationScheduledWorkerState
- `FederationScheduledWorker.tick()` — calls escalation_worker.tick() + notification_service.dispatch_pending(), returns state
- Uses asyncio.Event for graceful shutdown
- Same worker_id should not start twice (raise or no-op)
- tick errors recorded in last_error, don't crash the loop
- Acquires distributed lock before each tick cycle

Tests: ~20 tests covering initial status, start/stop lifecycle, tick increments count, tick records time, error handling, double-start safety, lock semantics.

---

## Task 5: Config Schema, Loader, RBAC, Change Events, AgentApp Properties

**Files:**
- Modify: `agent_app/config/schema.py`
- Modify: `agent_app/config/loader.py`
- Modify: `agent_app/governance/policy_rbac.py`
- Modify: `agent_app/governance/policy_change_event.py`
- Modify: `agent_app/governance/policy_rollout_federation_history.py`
- Modify: `agent_app/core/app.py`
- Test: `tests/unit/test_policy_rollout_federation_notification_config.py`

Config additions:
- `RolloutFederationDLQConfig` (enabled, type, path)
- `RolloutFederationRetryPolicyConfig` (max_attempts, backoff_seconds, send_to_dlq)
- `RolloutFederationChannelRetryConfig` (max_attempts, backoff_seconds, send_to_dlq)
- `RolloutFederationScheduledWorkerConfig` (enabled, interval_seconds, lock_type, lock_path, lock_ttl_seconds)
- Add `dlq`, `retry`, and `scheduled_worker` fields to `RolloutFederationConfig`

RBAC additions (3):
- FEDERATION_DLQ_LIST
- FEDERATION_DLQ_MANAGE
- FEDERATION_WORKER_MANAGE

Change event additions (6):
- FEDERATION_NOTIFICATION_DLQ_CREATED
- FEDERATION_NOTIFICATION_DLQ_RETRIED
- FEDERATION_NOTIFICATION_DLQ_PURGED
- FEDERATION_WORKER_STARTED
- FEDERATION_WORKER_STOPPED
- FEDERATION_WORKER_TICK_FAILED

Federation history event additions (3):
- NOTIFICATION_DLQ_CREATED
- NOTIFICATION_DLQ_RETRIED
- SCHEDULED_WORKER_TICK

AgentApp properties:
- federation_dlq_store
- federation_scheduled_worker

Update enum count tests.

---

## Task 6: CLI Commands

**Files:**
- Modify: `agent_app/cli.py`
- Test: `tests/unit/test_policy_rollout_federation_notification_cli.py`

Add commands:
- `federation notification dlq list [--status pending] [--channel webhook] [--limit 100]`
- `federation notification dlq show --dlq-id fdlq_...`
- `federation notification dlq retry --dlq-id fdlq_...`
- `federation notification dlq purge --dlq-id fdlq_...`
- `federation notification dlq export --format json|csv`
- `federation worker status`
- `federation worker tick` (existing, keep)
- `federation worker start --once` (single tick equivalent)

Tests: ~20 tests covering DLQ list/show/retry/purge/export, worker status/start--once.

---

## Task 7: Console Pages

**Files:**
- Modify: `agent_app/console/router.py`
- Create: `agent_app/console/templates/policy_federation_dlq_list.html`
- Create: `agent_app/console/templates/policy_federation_dlq_detail.html`
- Create: `agent_app/console/templates/policy_federation_worker_status.html`
- Modify: `agent_app/adapters/fastapi.py`
- Test: `tests/unit/test_policy_rollout_federation_notification_console.py`

Add routes:
- GET /federation/notifications/dlq — DLQ list
- GET /federation/notifications/dlq/{dlq_id} — DLQ detail
- GET /federation/workers — worker status

Tests: ~12 tests covering page renders.

---

## Task 8: Export Helpers

**Files:**
- Modify: `agent_app/runtime/policy_compliance_export.py`
- Test: `tests/unit/test_policy_compliance_export.py` (extend existing)

Add:
- `export_federation_dlq_summary_json(items)` — JSON export of DLQ entries
- `export_federation_dlq_summary_csv(items)` — CSV export of DLQ entries

Tests: ~8 tests covering JSON/CSV export format.

---

## Task 9: Observability Integration

**Files:**
- Modify: `agent_app/runtime/policy_rollout_federation_observability_service.py`
- Test: Update enum count tests

Extend observability:
- Add `get_dlq_summary()` method
- Add `get_worker_summary()` method
- Extend `generate_report()` with DLQ and worker metadata

---

## Task 10: Documentation and Final Verification

**Files:**
- Modify: `docs/policy_release.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Create: `docs/release_checklist_phase50.md`
- Update all enum count tests to match new counts

Final verification:
- Run full test suite
- Verify 0 failures
- Verify all Phase 49 tests still pass
