# Phase 49: Federation Approval Notification & Escalation Workers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add federation-level notification adapters, notification outbox, escalation worker, and distributed lock to make Phase 48's approval workflows production-ready.

**Architecture:** Federation-specific notification layer (separate from Phase 44's general policy notifications) with its own models, store, adapters, and service. Escalation worker uses single-tick pattern for testability. Distributed lock via SQLite prevents duplicate worker execution.

**Tech Stack:** Python 3.12+, Pydantic, stdlib sqlite3, asyncio, httpx (optional for webhook)

---

## Task 1: Federation Notification Models

**Files:**
- Create: `agent_app/governance/policy_rollout_federation_notification.py`
- Test: `tests/unit/test_policy_rollout_federation_notification.py`

Define models:
- `FederationNotificationChannel` (StrEnum: email, slack, webhook, console, noop)
- `FederationNotificationStatus` (StrEnum: pending, sent, failed, cancelled, skipped)
- `FederationNotificationEventType` (StrEnum: approval.created, approval.approved, approval.rejected, approval.escalated, approval.cancelled, approval.expired)
- `FederationNotificationMessage` (Pydantic model with fn_ prefix validation, tz-aware datetimes, all fields from spec)
- `FederationNotificationDelivery` (Pydantic model — result of adapter send)
- `FederationNotificationPolicy` (Pydantic model — channel routing config)
- `FederationNotificationTarget` (Pydantic model — recipient config per channel)
- `FederationNotificationDispatchResult` (Pydantic model — dispatch batch result)

Tests: ~25 tests covering model validation, enum values, id prefix, tz-aware datetime, defaults.

---

## Task 2: Federation Notification Store

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_notification_store.py`
- Test: `tests/unit/test_policy_rollout_federation_notification_store.py`

Implement:
- `FederationNotificationStore` Protocol (create, get, list_pending, mark_sent, mark_failed, cancel, list_by_approval)
- `InMemoryFederationNotificationStore`
- `SQLiteFederationNotificationStore`
- `create_federation_notification_store()` factory

Tests: ~30 tests covering CRUD, pending list, mark sent/failed, cancel, list by approval, retry next_attempt_at, SQLite persistence.

---

## Task 3: Federation Notification Adapters

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_notification_adapters.py`
- Test: `tests/unit/test_policy_rollout_federation_notification_adapters.py`

Implement:
- `FederationNotificationAdapter` Protocol (async send → FederationNotificationDelivery)
- `NoopFederationNotificationAdapter` — succeeds silently
- `ConsoleFederationNotificationAdapter` — logs to stdout/logger
- `FakeFederationNotificationAdapter` — captures messages for testing
- `WebhookFederationNotificationAdapter` — optional httpx-based, timeout configurable, graceful failure

Tests: ~20 tests covering noop, console, fake, webhook success/failure/timeout.

---

## Task 4: Federation Notification Service

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_notification_service.py`
- Test: `tests/unit/test_policy_rollout_federation_notification_service.py`

Implement `FederationNotificationService`:
- `__init__` — store, adapters dict, notification_policy, audit_logger, change_event_store, history_recorder
- `enqueue_for_approval_created(...)` — creates pending notification messages
- `enqueue_for_approval_approved(...)`
- `enqueue_for_approval_rejected(...)`
- `enqueue_for_approval_escalated(...)`
- `dispatch_pending(limit=100)` → FederationNotificationDispatchResult — single tick, no infinite loop
- Private helpers: `_build_message`, `_record_audit`, `_record_change_event`, `_record_history`

Tests: ~25 tests covering enqueue for each event type, dispatch marks sent, dispatch failure schedules retry, notification failure does not break approval transition.

---

## Task 5: Escalation Worker

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_escalation_worker.py`
- Test: `tests/unit/test_policy_rollout_federation_escalation_worker.py`

Implement:
- `FederationApprovalEscalationWorkerResult` (Pydantic model: scanned_count, escalated_count, skipped_count, errors)
- `FederationApprovalEscalationWorker.__init__` — approval_store, approval_service, notification_service, distributed_lock, config
- `tick(now=None)` → FederationApprovalEscalationWorkerResult — single tick
- Support dry_run mode
- Support tenant/federation filter
- Acquire lock before ticking, skip if unavailable
- Create escalation notifications

Tests: ~25 tests covering no pending, before timeout not escalated, after timeout escalated, escalation creates notification, dry-run, lock acquired/unavailable, worker result counts.

---

## Task 6: Distributed Lock

**Files:**
- Create: `agent_app/runtime/distributed_lock.py`
- Test: `tests/unit/test_distributed_lock.py`

Implement:
- `DistributedLock` Protocol (acquire, release, refresh)
- `InMemoryDistributedLock`
- `SQLiteDistributedLock`
- Factory function

Tests: ~20 tests covering acquire new, cannot acquire held, expired lock can be acquired, owner can release, non-owner cannot release, refresh, SQLite persistence.

---

## Task 7: Config Schema, Loader, RBAC, Change Events, AgentApp Properties

**Files:**
- Modify: `agent_app/config/schema.py`
- Modify: `agent_app/config/loader.py`
- Modify: `agent_app/governance/policy_rbac.py`
- Modify: `agent_app/governance/policy_change_event.py`
- Modify: `agent_app/governance/policy_rollout_federation_history.py`
- Modify: `agent_app/core/app.py`
- Test: `tests/unit/test_policy_rollout_federation_notification_config.py`

Config additions:
- `RolloutFederationNotificationConfig` (enabled, type, path, default_channels, channels config, retry config)
- `RolloutFederationEscalationWorkerConfig` (enabled, lock type, lock path, ttl_seconds)
- Add `notifications` and `worker` fields to `RolloutFederationConfig`

RBAC additions:
- `FEDERATION_NOTIFICATION_LIST`
- `FEDERATION_NOTIFICATION_DISPATCH`
- `FEDERATION_ESCALATION_RUN`

Change event additions (6):
- FEDERATION_NOTIFICATION_CREATED
- FEDERATION_NOTIFICATION_SENT
- FEDERATION_NOTIFICATION_FAILED
- FEDERATION_APPROVAL_ESCALATION_WORKER_TICKED
- FEDERATION_APPROVAL_ESCALATION_DUE
- FEDERATION_APPROVAL_ESCALATION_LOCK_SKIPPED

Federation history event additions (5):
- NOTIFICATION_CREATED
- NOTIFICATION_SENT
- NOTIFICATION_FAILED
- ESCALATION_WORKER_TICKED
- ESCALATION_LOCK_SKIPPED

AgentApp properties:
- federation_notification_store
- federation_notification_service
- federation_escalation_worker
- distributed_lock

Tests: ~20 tests covering config defaults, RBAC, event counts, loader wiring.

---

## Task 8: Approval Service Notification Integration

**Files:**
- Modify: `agent_app/runtime/policy_rollout_federation_approval_service.py`
- Test: `tests/unit/test_policy_rollout_federation_notification_integration.py`

Extend `FederationApprovalService`:
- Add `notification_service` parameter (optional)
- In `create_approval_request`: call `notification_service.enqueue_for_approval_created(...)`
- In `approve`: call `notification_service.enqueue_for_approval_approved(...)`
- In `reject`: call `notification_service.enqueue_for_approval_rejected(...)`
- In `escalate`: call `notification_service.enqueue_for_approval_escalated(...)`
- Notification failure does NOT break approval state transition (best-effort, audit error)

Tests: ~20 integration tests covering notification enqueue on each lifecycle event, notification failure does not break approval.

---

## Task 9: CLI Commands

**Files:**
- Modify: `agent_app/cli.py`
- Test: `tests/unit/test_policy_rollout_federation_notification_cli.py`

Add commands:
- `federation notification list --status pending`
- `federation notification dispatch --limit 100`
- `federation notification by-approval --approval-id fap_...`
- `federation approval escalate-due`
- `federation worker tick`

Tests: ~20 tests covering each command output.

---

## Task 10: Console Pages

**Files:**
- Modify: `agent_app/console/router.py`
- Create: `agent_app/console/templates/policy_federation_notification_list.html`
- Create: `agent_app/console/templates/policy_federation_notification_detail.html`
- Create: `agent_app/console/templates/policy_federation_escalation.html`
- Modify: `agent_app/adapters/fastapi.py`
- Test: `tests/unit/test_policy_rollout_federation_notification_console.py`

Add routes:
- GET /federation/notifications
- GET /federation/notifications/{id}
- GET /federation/approvals/{id}/notifications
- GET /federation/escalations

Tests: ~15 tests covering page renders.

---

## Task 11: Observability Integration

**Files:**
- Modify: `agent_app/runtime/policy_rollout_federation_observability_service.py`
- Modify: `agent_app/runtime/policy_compliance_export.py`
- Test: Update existing test files for event counts

Extend observability with notification and escalation metrics.

---

## Task 12: Documentation and Final Verification

**Files:**
- Modify: `docs/policy_release.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Create: `docs/release_checklist_phase49.md`
- Update enum count tests

Update all documentation, verify full test suite passes.
