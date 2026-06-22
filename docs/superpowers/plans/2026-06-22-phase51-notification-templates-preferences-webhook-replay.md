# Phase 51: Federation Notification Templates, Preferences & Webhook Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Add configurable notification templates, notification preference management with opt-in/opt-out, webhook request snapshots with HMAC-SHA256 signing, and original-payload replay from DLQ.

**Architecture:** Template service with safe variable substitution, preference service with priority resolution, webhook signature service with key rotation, nonce store for replay protection. All integrated into existing notification service dispatch flow.

**Tech Stack:** Python 3.12+, Pydantic, stdlib sqlite3, hashlib, hmac

---

## Task 1: Template & Preference Domain Models

**Files:**
- Modify: `agent_app/governance/policy_rollout_federation_notification.py` (add template format enum, add SUPPRESSED/TEMPLATE_FAILED/SIGNATURE_FAILED to status)
- Create: `agent_app/governance/policy_rollout_federation_notification_template.py`
- Create: `agent_app/governance/policy_rollout_federation_notification_preference.py`
- Create: `agent_app/governance/policy_rollout_federation_webhook.py`
- Tests: ~20 tests

Add: FederationNotificationTemplateFormat, FederationNotificationTemplate, FederationNotificationRenderedContent, template errors, FederationNotificationPreferenceDecision, FederationNotificationPreference, FederationWebhookRequestSnapshot, FederationWebhookSignatureResult, FederationWebhookReplayResult.

---

## Task 2: Safe Template Renderer Service

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_notification_template_service.py`
- Tests: ~25 tests

Safe `{{ var.path }}` substitution, template selection priority (federation+event+channel > event+channel > channel > global > builtin), strict/lenient missing variable mode, JSON template validation, no code execution.

---

## Task 3: Template Store

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_notification_template_store.py`
- Tests: ~20 tests

Protocol + InMemory + SQLite + factory. CRUD, find_effective_template, version conflict check, pagination.

---

## Task 4: Notification Preference Store & Service

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_notification_preference_store.py`
- Create: `agent_app/runtime/policy_rollout_federation_notification_preference_service.py`
- Tests: ~25 tests

Preference store Protocol + InMemory + SQLite. Preference service with should_deliver(), resolve_effective_preference(), explain_preference(). Priority resolution, mandatory events, fail-open/closed.

---

## Task 5: Webhook Signature Service

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_webhook_signature.py`
- Create: `agent_app/runtime/policy_rollout_federation_webhook_nonce_store.py`
- Tests: ~25 tests

HMAC-SHA256 signing, sign() and verify(), key rotation, timestamp tolerance, nonce store (InMemory + SQLite), constant-time comparison, deterministic JSON serialization.

---

## Task 6: Notification Service Integration

**Files:**
- Modify: `agent_app/runtime/policy_rollout_federation_notification_service.py`
- Modify: `agent_app/runtime/policy_rollout_federation_notification_adapters.py` (extend WebhookFederationNotificationAdapter)
- Modify: `agent_app/governance/policy_rollout_federation_notification.py` (add SUPPRESSED status to DLQ reason)
- Tests: ~20 tests

Integrate template rendering, preference checks, webhook signing, request snapshots into dispatch flow. Add replay_original() method.

---

## Task 7: Config Schema, Loader, RBAC, Change Events, AgentApp

**Files:**
- Modify: `agent_app/config/schema.py`
- Modify: `agent_app/config/loader.py`
- Modify: `agent_app/governance/policy_rbac.py`
- Modify: `agent_app/governance/policy_change_event.py`
- Modify: `agent_app/governance/policy_rollout_federation_history.py`
- Modify: `agent_app/core/app.py`
- Tests: update enum counts

6 new RBAC permissions, 12 new change events, 3 new history events, template/preference/webhook config models, AgentApp properties.

---

## Task 8: CLI Commands

**Files:**
- Modify: `agent_app/cli.py`
- Tests: ~20 tests

Template list/show/create/update/disable/render, preference list/set/show/delete/explain, DLQ replay-original --dry-run, webhook verify.

---

## Task 9: Console Pages

**Files:**
- Modify: `agent_app/console/router.py`
- Create: 4 new templates
- Modify: `agent_app/adapters/fastapi.py`
- Tests: ~15 tests

Template list/detail, preference list/explain, extended DLQ detail with replay info.

---

## Task 10: Export, Observability, Documentation, Final Verification

**Files:**
- Modify: `agent_app/runtime/policy_compliance_export.py`
- Modify: `agent_app/runtime/policy_rollout_federation_observability_service.py`
- Modify: docs/policy_release.md, CHANGELOG.md, README.md
- Create: docs/release_checklist_phase51.md
- Tests: ~10 tests

Export helpers, observability summaries, documentation, enum count updates, full test suite verification.
