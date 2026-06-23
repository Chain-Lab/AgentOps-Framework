# Changelog

All notable changes to Agent App Framework are documented here.

## v0.41.0 — Phase 53: Federation Notification External Alert Delivery, Prometheus Export, Retention & Rollup

### Added
- Alert delivery models (targets, attempts, retry policy) with InMemory and SQLite stores
- Alert delivery service with target matching, severity/channel/federation filters, dry-run mode
- Alert delivery adapters: Memory, Webhook (dry-run only), Console
- Prometheus metrics text export with HELP/TYPE comments, label escaping, no secrets
- JSONL structured export for delivery events, alerts, and delivery attempts
- Retention service with archive-before-purge, dry-run support, per-type retention days
- Metrics rollup service with hourly/daily granularity and upsert semantics
- Alert delivery CLI commands (deliver, targets list, attempts list)
- Prometheus export, JSONL export, retention cleanup, rollup build/list CLI commands
- Console pages for alert delivery, prometheus, JSONL, retention, rollup
- 9 new PolicyChangeEventType values for Phase 53 notification lifecycle events
- 9 new FederationHistoryEventType values for Phase 53 notification history

### Changed
- PolicyChangeEventType count: 124 → 133
- FederationHistoryEventType count: 42 → 51

## v0.42.0 — Phase 54: Alert Delivery Productionization — Retry, DLQ Replay, Dedup, Incremental Rollup, Webhook Signing

### Added
- Retry scheduler: `run_once()` scans and retries RETRY_SCHEDULED attempts past next_retry_at
- DLQ replay: `replay_dlq_attempt()` creates new delivery attempt from DLQ record
- Alert deduplication service with configurable merge window and key fields
- Incremental rollup with checkpoints (`build_incremental_rollup`, `list_checkpoints`, `record_checkpoint`)
- Webhook HMAC-SHA256 signing with X-Signature and X-Timestamp headers
- Real HTTP POST webhook adapter (stdlib urllib, opt-in via webhook_secret)
- Archive file auto-cleanup for framework-generated notification archives
- 7 new PolicyChangeEventType values for Phase 54 notification lifecycle events
- CLI commands: retry-run, dlq list/replay, dedup explain, rollup incremental/checkpoint, retention archives cleanup
- 34 comprehensive Phase 54 tests covering change events, dedup, signing, DLQ replay, rollup, retry scheduler

### Changed
- WebhookAlertDeliveryAdapter: real HTTP POST when dry_run=False and endpoint is configured
- PolicyChangeEventType count: 133 → 140
- AlertDeliveryTarget: added optional `webhook_secret` field for HMAC signing

## v0.40.0 — Phase 52: Federation Notification Observability

### Added
- Federation notification delivery event observability with InMemory and SQLite stores
- Notification metrics aggregation (success rate, failure rate, DLQ rate, latency, p95)
- Channel health snapshots with healthy/degraded/unhealthy status
- Notification SLA policy with per-channel overrides and violation detection
- Notification alert rules with cooldown, acknowledge, and resolve lifecycle
- Notification observability CLI commands (events, metrics, health, sla, alerts, report)
- Notification observability console pages
- Notification metrics/events/alerts JSON and CSV exports

### Changed
- PolicyChangeEventType count: 118 → 123

## v0.39.0 — Phase 51: Federation Notification Templates, Preferences & Webhook Replay

### Added
- Federation notification template models and safe renderer
- Template store with InMemory and SQLite backends
- Template selection priority (federation+event+channel > event+channel > channel > global > builtin)
- Notification preference models with opt-in/opt-out/inherit
- Preference store and service with priority resolution
- Mandatory notification event types (override opt-out)
- Preference explanation with specificity and reason codes
- Webhook HMAC-SHA256 signature service with key rotation
- Webhook nonce store for replay protection
- Webhook request snapshot model for audit
- Original-payload replay from DLQ (distinct from retry)
- Template CLI commands (list/show/create/update/disable/render)
- Preference CLI commands (list/set/show/delete/explain)
- Webhook replay CLI command (dlq replay-original --dry-run)
- Webhook verify CLI command
- Template and preference console pages
- Extended DLQ detail with replay info
- Template and preference JSON/CSV export
- 6 new RBAC permissions
- 12 new policy change event types
- 3 new federation history event types
- 3 new notification statuses (SUPPRESSED, TEMPLATE_FAILED, SIGNATURE_FAILED)

### Changed
- PolicyChangeEventType count: 106 → 118
- FederationHistoryEventType count: 33 → 36
- PolicyReleasePermission count: 82 → 88
- FederationNotificationStatus count: 6 → 9

## v0.38.0 — Phase 50: Federation Approval Dead-Letter Queue & Scheduled Worker

### Added
- Federation notification dead-letter queue (DLQ) — FederationNotificationDeadLetter model with fdlq_ prefix
- DLQ store — InMemory and SQLite backends with filtering and pagination
- Notification retry policy — FederationNotificationRetryPolicy with per-channel overrides
- DLQ entry creation on max retries exceeded
- Scheduled federation worker — FederationScheduledWorker with start/stop/status/tick lifecycle
- Worker state model — FederationScheduledWorkerState
- DLQ CLI commands — list, show, retry, purge, export (JSON/CSV)
- Worker CLI commands — status, start --once
- DLQ console pages — list and detail
- Worker status console page
- DLQ JSON and CSV export helpers
- DLQ and worker observability integration
- 3 new RBAC permissions: FEDERATION_DLQ_LIST, FEDERATION_DLQ_MANAGE, FEDERATION_WORKER_MANAGE
- 6 new policy change event types
- 3 new federation history event types
- DEAD_LETTERED notification status

### Changed
- PolicyChangeEventType count: 100 → 106
- FederationHistoryEventType count: 30 → 33
- PolicyReleasePermission count: 79 → 82
- FederationNotificationStatus count: 5 → 6

## v0.37.0 — Phase 49: Federation Approval Notification & Escalation Workers

### Added
- Federation notification models (FederationNotificationChannel, Status, EventType, Message, Delivery, Policy, Target, DispatchResult)
- Federation notification store (InMemory + SQLite + factory)
- Federation notification adapters (noop, console, fake, webhook)
- Federation notification service (enqueue for approval lifecycle events + dispatch)
- Federation approval escalation worker (single-tick, dry-run, distributed lock)
- Distributed lock (InMemory + SQLite + factory)
- CLI commands: notification list/dispatch/by-approval, escalate-due, worker tick
- Console pages: notification list/detail, approval notifications, escalation dashboard
- 6 new PolicyChangeEventType values (FEDERATION_NOTIFICATION_*, ESCALATION_*)
- 2 new FederationHistoryEventType values (ESCALATION_*)
- 3 new PolicyReleasePermission values (FEDERATION_NOTIFICATION_LIST/DISPATCH, FEDERATION_ESCALATION_RUN)
- Observability notification summary and export helpers
- Approval service notification integration (best-effort)

### Changed
- PolicyChangeEventType count: 94 → 100
- FederationHistoryEventType count: 28 → 30
- PolicyReleasePermission count: 76 → 79

## [0.36.0] - Phase 48: Policy Rollout Federation Approval Workflows

### Added
- FederationApprovalStatus enum (pending, approved, rejected, expired, escalated, cancelled)
- FederationApprovalRequest model with fap_ prefix
- FederationApprovalPolicy model
- FederationApprovalDecision model
- FederationApprovalEscalation model
- FederationApprovalDashboardSummary model
- FederationApprovalStore (Protocol + InMemory + SQLite)
- FederationApprovalService with approval check, delegation, escalation
- RolloutFederationApprovalConfig in schema.py
- Federation approval wiring in config loader
- 4 RBAC permissions (federation.approval.list/approve/reject/escalate)
- 6 PolicyChangeEventType values (FEDERATION_APPROVAL_*)
- 5 FederationHistoryEventType values (APPROVAL_*)
- 3 AgentApp properties (federation_approval_store/policy/service)
- CLI federation approval commands (list, approve, reject, escalate)
- Console federation approval pages (list, detail, plan approvals, approve/reject actions)
- FederationObservabilityService approval summary integration
- Federation approval JSON/CSV export helpers
- RolloutFederationService approval integration (blocks sensitive actions)

### Changed
- PolicyChangeEventType: 88 → 94 event types
- FederationHistoryEventType: 23 → 28 event types
- Fixed Phase 47 console batch-mode asyncio test failures

## v0.35.0 — Phase 47: Policy Rollout Federation Observability and Reporting

### Added

- FederationHistoryEventType and FederationHistoryEvent models (23 event types)
- FederationTargetTimeline, FederationWaveTimeline, FederationTimeline models
- FederationTargetHealthSummary, FederationWaveOutcomeSummary, FederationConflictSummary models
- FederationAnalyticsReport model
- FederationHistoryStore (InMemory + SQLite) with append-only semantics
- FederationHistoryRecorder for normalized event recording
- FederationObservabilityService (get_timeline, generate_report, list_history_events)
- RolloutFederationService integration with federation recorder
- PolicyNotificationService integration for federation-related notification events
- Federation export helpers (JSON timeline/report, CSV analytics rows)
- 3 RBAC permissions: FEDERATION_HISTORY_VIEW, FEDERATION_ANALYTICS_VIEW, FEDERATION_ANALYTICS_EXPORT
- 7 change event types (81 → 88 total)
- CLI commands: federation history, timeline, analytics, analytics export
- Console pages: federation history, timeline, analytics

## v0.34.0 — Phase 46: Policy Rollout Federation and Conflict Detection

### Added

- FederatedRolloutTarget, FederatedRolloutPlan, FederatedRolloutTargetExecution, FederatedRolloutWave, and RolloutConflict models
- InMemory and SQLite federation target and plan stores
- RolloutConflictDetector with deterministic, non-mutating conflict checks
- RolloutFederationService for target creation, federated plan creation/start/execution/cancel, child rollout creation
- Federation RBAC permissions (10 permissions), config schema, loader wiring, and AgentApp properties
- CLI commands for federation targets (create/list/enable/disable) and plans (create/list/show/start/run-next/run-all/cancel/conflicts)
- Console pages for federation targets, plans, plan details, plan creation, and conflicts
- Audit and policy change events for federation lifecycle (9 new event types)

## v0.33.0 — Phase 45: Policy Rollout Analytics, History, and Gate Outcome Reporting

### Added

- RolloutHistoryEventType enum (24 event types for rollout, step, approval, gate, notification events)
- RolloutHistoryEvent model with `rhe_` prefix and tz-aware timestamps
- RolloutStepTimeline, RolloutTimeline models for structured rollout timelines
- RolloutGateOutcomeSummary, RolloutApprovalOutcomeSummary analytics models
- RolloutAnalyticsReport model with `rar_` prefix for aggregated analytics
- RolloutHistoryStore Protocol with InMemory and SQLite implementations
- RolloutHistoryRecorder for creating normalized rollout history events
- RolloutHistoryService for timeline generation and analytics reporting
- RolloutService, RolloutGateAutomationService, PolicyExpirationService, PolicyNotificationService integration with history recorder
- Export helpers: rollout_timeline_to_json, rollout_analytics_report_to_json, rollout_analytics_report_to_csv_rows
- 3 RBAC permissions: ROLLOUT_HISTORY_VIEW, ROLLOUT_ANALYTICS_VIEW, ROLLOUT_ANALYTICS_EXPORT
- 7 PolicyChangeEventType values (total now 72)
- CLI commands: rollout history, rollout timeline --json, rollout analytics, rollout analytics export
- Console pages: rollout history, rollout timeline, rollout analytics dashboard
- RolloutHistoryConfig in config schema
- Loader wiring for rollout history store, recorder, and service

## [v0.32.0] - 2026-06-17

### Phase 44: Notification Hooks and Expiration Workers

**Added:**
- `PolicyNotificationMessage` and `PolicyNotificationRule` models
- `PolicyNotificationSeverity` and `PolicyNotificationStatus` enums
- `PolicyNotificationStore` (InMemory + SQLite) for notification delivery persistence
- `PolicyNotificationRuleStore` (InMemory + SQLite) for rule persistence
- `LogNotificationChannel` and `InMemoryNotificationChannel` built-in channels
- `PolicyNotificationService` with `notify_event`, `send_pending`, `list_notifications`
- `PolicyExpirationResult` and `PolicyExpirationSweepReport` models
- `PolicyExpirationService` with `sweep`, `expire_rollout_approvals`, `expire_gate_requirements`
- `PolicyExpirationWorker` with `start`/`stop`/`run_once` (does not auto-start)
- 7 new RBAC permissions for notification and expiration operations
- 10 new change event types for notification and expiration lifecycle
- `NotificationConfig` and `ExpirationConfig` in schema
- Config loader wiring for notification and expiration services
- CLI commands: `policy notification list/send-pending/rule list/enable/disable`
- CLI commands: `policy expiration sweep/run-once`
- Console pages for notifications, notification rules, and expiration

## v0.31.0 — Phase 43: Policy Rollout Automation with Simulation Gates

- RolloutGateMode enum (DISABLED/MANUAL/AUTO) and RolloutGateFailureAction enum (BLOCK/FAIL/SKIP)
- RolloutStep extension with 8 new Phase 43 fields (simulation_gate_mode, simulation_gate_failure_action, simulation_candidate_rules, simulation_gate_rules, simulation_window_start/end, simulation_limit, simulation_include_base, simulation_gate_max_age_seconds)
- RolloutGateExecutionStatus enum and RolloutGateExecutionResult model
- RolloutGateAutomationService (ensure_step_gate, run_step_gate, check_step_gate)
- RolloutService integration: AUTO steps automatically run simulation gates before execution
- Failure actions: BLOCK marks step BLOCKED, FAIL marks step FAILED, SKIP marks step SKIPPED
- run_all_available() continues past SKIPPED steps
- RolloutGateAutomationConfig and SimulationGateRuleConfig in schema
- Config loader wiring for RolloutGateAutomationService
- CLI commands: policy rollout gate run/status/attach
- Console rollout gate pages
- RBAC: ROLLOUT_GATE_RUN, ROLLOUT_GATE_ATTACH, ROLLOUT_GATE_VIEW
- Change events: ROLLOUT_GATE_RUN, ROLLOUT_GATE_SATISFIED, ROLLOUT_GATE_BLOCKED, ROLLOUT_GATE_FAILED, ROLLOUT_GATE_SKIPPED, ROLLOUT_GATE_ATTACHED, ROLLOUT_GATE_PERMISSION_DENIED

## v0.30.0 — Phase 42: Policy Release Automation and Simulation Gate Enforcement

- ReleaseGateRequirement model and store (InMemory + SQLite)
- ReleaseGateAutomationService (require, attach, run+attach, check)
- PromotionRequest extension with simulation gate fields
- RolloutStep extension with simulation gate fields
- PolicyReleaseService enforcement (block execution when gate required/failed/expired)
- RolloutService step gate blocking
- SimulationGateEnforcementConfig in schema
- Config loader wiring for requirement store/service/enforcement flags
- CLI commands: policy promotion gate require/run/attach/status
- Console promotion gate pages
- RBAC: PROMOTION_GATE_REQUIRE, PROMOTION_GATE_RUN, PROMOTION_GATE_ATTACH, PROMOTION_GATE_VIEW, ROLLOUT_GATE_ATTACH, ROLLOUT_GATE_VIEW
- Change events: PROMOTION_GATE_REQUIRED, PROMOTION_GATE_SATISFIED, PROMOTION_GATE_FAILED, PROMOTION_GATE_EXPIRED, etc.

## v0.29.0 (2026-06-16)

### Phase 41: Policy Gate Integration and Automated Safeguards

- **New:** SimulationGateInput model — combines simulation summary with validation report for gate evaluation
- **New:** simulation_gate_metrics() — extracts 12 supported metrics from simulation input
- **New:** SimulationGateEvaluator — evaluates metrics against configurable threshold rules (lt/lte/gt/gte/eq/neq operators)
- **New:** SimulationGateRule, SimulationGateResult, SimulationGateReport models
- **New:** PolicySimulationService.validate_and_gate() — chains validation, simulation, and gate evaluation
- **New:** RBAC permissions: SIMULATION_GATE_RUN (requires grant), SIMULATION_GATE_VIEW (default allowed)
- **New:** Change event types: SIMULATION_GATE_PASSED, SIMULATION_GATE_FAILED, SIMULATION_GATE_WARNING, SIMULATION_GATE_ERROR
- **New:** Audit events: gate_passed, gate_failed, gate_error, gate_permission_denied
- **New:** CLI command: policy simulation gate --config --rules-file --gate-rules-file [--json] [--output]
- **New:** Console pages: gate form (/simulation-gate) and gate report (/simulation-gate/report)
- **New:** Gate rules YAML support — separate file with configurable required/non-required rules
- **New:** Blocking behavior — CLI exits non-zero on gate failure for CI/CD integration
- **Changed:** config/schema.py — added gates list to PolicySimulationConfig
- **Changed:** config/loader.py — wired SimulationGateEvaluator
- **Changed:** governance/policy_rbac.py — added SIMULATION_GATE_RUN, SIMULATION_GATE_VIEW
- **Changed:** governance/policy_change_event.py — added 4 simulation gate event types
- **Changed:** runtime/policy_simulation_service.py — added validate_and_gate() method
- **Changed:** cli.py — added policy simulation gate command
- **Changed:** console/router.py — added simulation gate routes
- **Changed:** adapters/fastapi.py — wired simulation_gate_evaluator

### Backward Compatible

- Missing gates config preserves existing Phase 40 behavior
- SimulationGateEvaluator is optional; gate commands require explicit --gate-rules-file
- All Phase 40 tests pass unchanged

## v0.28.0 (2026-06-16)

### Phase 40: Policy Testing, Validation, and Historical Replay

- **New:** Policy simulation models (PolicySimulationOutcome, PolicySimulationCase, PolicySimulationResult, PolicySimulationSummary, PolicySimulationReport)
- **New:** Audit-to-simulation case extraction (audit_event_to_simulation_case)
- **New:** Candidate policy store builder (build_candidate_policy_store)
- **New:** PolicySimulationService with collect_cases_from_audit, simulate_cases, simulate_from_audit
- **New:** RuntimePolicyValidator with duplicate name, broad rule, conflicting rule, approval policy checks
- **New:** Simulation export helpers (simulation_report_to_json, simulation_report_to_csv_rows, validation_report_to_json)
- **New:** CLI commands: policy simulation validate/replay/export
- **New:** Console simulation pages with validation and replay forms
- **New:** RBAC permissions: SIMULATION_RUN, SIMULATION_VIEW, SIMULATION_EXPORT
- **New:** Audit event types: SIMULATION_VALIDATION_RUN, SIMULATION_REPLAY_RUN, SIMULATION_EXPORT_GENERATED, SIMULATION_PERMISSION_DENIED
- **New:** PolicySimulationConfig (governance.policy_simulation.enabled)

## v0.27.0 — Phase 39: Policy Observability and Analytics

### Added
- **PolicyObservabilityReport model** — aggregated governance analytics with por_ prefix
- **PolicyObservabilityService** — generates reports from audit events and stores
- **Policy compliance export** — JSON and CSV export helpers
- **CLI observability commands** — report (with --since/--until/--json) and export (--format/--output)
- **Console observability dashboard** — live dashboard with summary cards, action/actor/tool tables, approval latency, top denials
- **Console report page** — filtered report with since/until inputs
- **RBAC permissions** — OBSERVABILITY_VIEW (default-allowed), OBSERVABILITY_EXPORT
- **Event types** — OBSERVABILITY_REPORT_GENERATED, OBSERVABILITY_EXPORT_GENERATED, EXPORT_FAILED

### Backward Compatible
- Missing policy_observability config preserves behavior
- Service is optional; missing stores produce partial reports

## v0.26.0 — Phase 38: Runtime Policy Enforcement Points

### Added
- **PolicyEnforcementDecision model** — runtime enforcement decision with ped_ prefix
- **RuntimePolicyRule model** — configurable enforcement rules with rpr_ prefix
- **RuntimePolicyStore** — InMemory + SQLite implementations for runtime policy rules
- **RuntimePolicyEvaluator** — rule matching with deny > require_approval > allow priority
- **PolicyEnforcementService** — wraps evaluator with audit logging
- **ToolExecutor enforcement** — runtime policy check before tool execution
- **Resume enforcement** — runtime policy re-check before approval resume
- **Runtime approval extension** — ApprovalRequest with optional policy/decisions/subject/action_type fields
- **Runtime policies config** — YAML config for inline rules
- **RBAC permissions** — RUNTIME_POLICY_CREATE/VIEW/ENABLE/DISABLE/EVALUATE
- **Change event types** — RUNTIME_POLICY_RULE_CREATED/ENABLED/DISABLED/EVALUATED
- **CLI runtime policy commands** — list, create, enable, disable, evaluate
- **Console runtime policy pages** — rule list, detail, evaluate, enable/disable

### Changed
- **ToolExecutor.execute()** — optional enforcement check after permission check
- **ApprovalResumeService** — optional policy re-check on resume

### Backward Compatible
- All new parameters default to None — existing behavior unchanged
- Missing runtime_policies config preserves current behavior
- Existing ToolSpec.requires_approval and Phase 37 approvals work unchanged

## v0.25.0 — Phase 37: Separation of Duties and Multi-Approver Approval Policies

### Added
- **RolloutApprovalPolicy model** — configurable SINGLE or QUORUM approval policies
- **RolloutApprovalDecision model** — individual approve/reject decisions by actors
- **RolloutApprovalPolicyEvaluator** — validates decisions against policy constraints (separation of duties, role/permission checks, expiration, duplicate prevention)
- **Quorum approvals** — approvals requiring N independent approvers before unblocking steps
- **Separation of duties** — prohibit_requester_approval, prohibit_creator_approval, prohibit_step_actor_approval
- **Role and permission constraints** — allowed_approver_roles and allowed_approver_permissions on policies
- **Approval expiration** — expires_after_seconds on policies, expire_pending() store method, expire CLI command
- **EXPIRED status** — new RolloutStepApprovalStatus.EXPIRED for expired approvals
- **Store add_decision/expire_pending** — new store methods for decision-based approval flow
- **CLI --roles flag** — approve and reject commands accept --roles for role-based policy checks
- **CLI expire command** — new `agentapp policy rollout approval expire` subcommand
- **Console quorum display** — approval detail shows decisions table, progress, policy, expiration
- **Audit events** — decision_recorded, quorum_reached, expired, policy_denied event types

### Changed
- **RolloutService approve_step/reject_step** — now use decision-based flow instead of direct status mutation
- **_approval_to_dict** — extended with policy, decisions, expires_at, required_approvals, current_approvals
- **_build_context** — accepts optional roles parameter

### Backward Compatible
- Default SINGLE policy preserves Phase 36 single-approval behavior
- All existing Phase 36 approval tests pass unchanged
- RolloutApprovalConfig without policy field still loads correctly

## v0.24.0 — Phase 36: Rollout Approval Workflow

### Added

- RolloutStepApproval model with PENDING/APPROVED/REJECTED/CANCELLED status lifecycle
- RolloutStepApprovalStore (Protocol + InMemory + SQLite + factory)
- RolloutService approval APIs: request_step_approval, approve_step, reject_step, list_step_approvals
- Automatic approval creation for requires_approval steps in run_next_step
- Approved steps unblock and execute normally; rejected approvals fail step/plan
- ROLLOUT_APPROVAL_REQUEST/APPROVE/REJECT/VIEW RBAC permissions
- ROLLOUT_APPROVAL_REQUESTED/APPROVED/REJECTED change event types
- RolloutApprovalConfig with require_reason support
- CLI commands: policy rollout approval list/request/approve/reject
- Console pages: approval list, detail, request, approve, reject
- Rollout detail page shows approval state for blocked steps
- Approval reason policy enforcement (require_reason config)

### Known limitations

- No multi-party approval
- No separation-of-duties enforcement
- No external identity integration
- No notification system
- No approval expiration
- No cryptographic signing
- Step approval is rollout-local only

## v0.23.0 — Phase 35: Multi-Environment Rollout Orchestration

### Added

- RolloutPlan and RolloutStep models (governance/policy_rollout.py)
- RolloutPlanStore protocol with InMemory and SQLite implementations
- RolloutService for orchestrating multi-environment rollout plans
- Step types: ACTIVATE, ASSIGN_RING, CANARY_EVAL, PROMOTE_RING
- Step dependency enforcement (require_previous_step)
- Approval blocking (requires_approval marks step BLOCKED)
- Plan lifecycle: DRAFT → ACTIVE → COMPLETED/FAILED/CANCELLED
- Rollout RBAC permissions: ROLLOUT_CREATE, ROLLOUT_START, ROLLOUT_EXECUTE, ROLLOUT_CANCEL, ROLLOUT_VIEW
- Rollout change event types: ROLLOUT_CREATED, ROLLOUT_STARTED, STEP_SUCCEEDED, COMPLETED, FAILED, CANCELLED
- CLI rollout commands: create, list, show, start, run-next, run-all, cancel
- Console rollout pages: list, detail, create, start, run-next, run-all, cancel
- Config: governance.policy_release.rollouts (type, path)
- Audit events for all rollout operations

### Known Limitations

- No background scheduler
- No external CI/CD integration
- Step approval is MVP/block-only
- No automatic rollback based on live metrics
- No distributed execution lock
- Rollout execution is local command/API driven

## [v0.22.0] - 2026-06-13

### Added

- PolicyChangeEvent model with 12 event types (policy_change_event.py)
- PolicyChangeEventStore (InMemory + SQLite) for append-only event persistence
- PolicyReloadManager for runtime reload notifications and hook management
- ActivePolicyResolver cache improvements: cache_status(), refresh(env, ring), clear_cache(env, ring)
- PolicyReleaseService change event emission for 11 state change types
- Deterministic canary percentage routing via RingRoutingConfig and SHA-256 hash
- AppRunner ring router integration with policy metadata in AppRunResult
- PolicyChangeEventsConfig, PolicyReloadConfig, RingRoutingConfig in config schema
- CLI commands: reload request/status, events list, routing simulate
- Console pages: events list, reload status, routing simulator
- RBAC permissions: RELOAD_REQUEST, RELOAD_VIEW, EVENT_VIEW, ROUTING_SIMULATE

## Phase 33: Release Rings, Canary Evaluation, and Ring-Aware Policy Resolution (0.21.0)

### Added

- **ReleaseRing model** — Named deployment targets per environment (stable, canary, internal, custom) with ENABLED/DISABLED status and is_default flag
- **ReleaseRingStore** — Protocol + InMemory + SQLite persistence; create(), get(), get_by_name(), list(), set_default(), disable(), enable() methods
- **RingActivationAssignment model** — Assigns a specific activation to a ring with ACTIVE/SUPERSEDED/DISABLED lifecycle and supersession tracking
- **RingActivationAssignmentStore** — Protocol + InMemory + SQLite persistence; assign() auto-supersedes previous ACTIVE assignment; get_active(), list(), disable_active() methods
- **PolicyRingRouter** — Request-scoped ring resolution: explicit override via RunContext.policy_ring, default ring from store, configured fallback
- **Ring-aware resolver** — ActivePolicyResolver.resolve_active_bundle_for_ring() and require_active_bundle_for_ring() with triple config_hash integrity check across assignment, activation, and bundle
- **CanaryEvalRunner** — Runs eval suites against a specific activation for canary validation before stable promotion
- **CanaryEvalResult** — Model capturing eval outcome: passed, total, passed_count, failed_count, per-case errors
- **RBAC permissions** — RING_CREATE, RING_ASSIGN, RING_PROMOTE, RING_DISABLE, RING_ENABLE (require grant); RING_VIEW (default-allowed)
- **RunContext.policy_ring** — Request-scoped ring targeting field
- **Config extensions** — rings and ring_assignments store configs; runtime.ring default ring override
- **CLI ring commands** — `agentapp policy ring list/create/assign/promote/disable/enable`
- **CLI canary commands** — `agentapp policy canary eval`
- **Console ring pages** — Ring list (GET /rings), ring detail (GET /rings/{env}/{name}), create/assign/promote/disable/enable POST actions
- **Console templates** — policy_rings.html, policy_ring_detail.html with active assignment display and action forms
- **Audit events** — policy.ring.created, policy.ring.disabled, policy.ring.enabled, policy.ring.assignment.created, policy.ring.promoted, policy.ring.permission_denied, policy.canary.eval_started/eval_completed/eval_failed
- **70+ tests** — Ring model (7), ring store (13), ring assignment model (11), ring assignment router (6), RBAC (3), context (2), ring-aware resolver (7), release service (14), config (6), CLI (9), console (7), canary eval (4)

### Changed

- PolicyReleaseService gains create_ring(), assign_activation_to_ring(), promote_canary_to_stable(), disable_ring(), enable_ring() methods; accepts ring_store, ring_assignment_store, ring_router parameters
- ActivePolicyResolver gains resolve_active_bundle_for_ring(), require_active_bundle_for_ring() methods; accepts ring_assignment_store, ring_store parameters; refresh() clears ring-specific cache entries
- PolicyReleasePermission gains RING_CREATE, RING_ASSIGN, RING_PROMOTE, RING_DISABLE, RING_ENABLE, RING_VIEW values
- PolicyReleasePermissionChecker default-allowed set includes RING_VIEW
- RunContext gains policy_ring: str | None field
- PolicyReleaseConfig gains rings and ring_assignments store config fields
- PolicyReleaseRuntimeConfig gains ring: str | None default ring override field
- CLI gains `policy ring` and `policy canary` subcommand groups
- Console router gains ring list/detail/create/assign/promote/disable/enable routes; accepts ring_store, ring_assignment_store parameters
- FastAPI adapter passes ring stores to console router
- Base console template gains Rings nav link

### Architecture Boundaries Maintained

- Core modules (policy_ring, policy_ring_store, policy_ring_assignment, policy_ring_assignment_store, policy_ring_router, canary) have no FastAPI/Jinja2 imports
- Console templates only mount when console is enabled
- Ring router uses store protocols — no direct SQLite coupling
- CLI uses lazy service initialization to avoid import cycles

## Phase 32: Policy Rollback, Emergency Disable, and Activation Safety Controls (0.20.0)

### Added

- **PolicyEnvironmentState model** — ENABLED/DISABLED status per environment with disabled_reason, disabled_by, disabled_at, enabled_by, enabled_at, updated_at timestamps
- **PolicyEnvironmentStore** — Protocol + InMemory + SQLite persistence for environment states; get(), disable(), enable(), list() methods
- **Activation rollback** — rollback_to_activation() creates new activation pointing to previous bundle, supersedes current active
- **PolicyActivation rollback fields** — rollback_of_activation_id (the superseded activation) and rollback_target_activation_id (the target activation being rolled back to)
- **PolicyActivationStore rollback methods** — get_previous_activation() and rollback_to_activation() on both InMemory and SQLite stores; SQLite ALTER TABLE migration for new columns
- **ActivePolicyResolver safety** — Disabled environments return None for resolve_active_bundle(), raise RuntimeError with disabled reason for require_active_bundle()
- **RBAC permissions** — ENVIRONMENT_DISABLE, ENVIRONMENT_ENABLE (require explicit grant), ENVIRONMENT_VIEW (default-allowed)
- **Service APIs** — rollback_environment(), disable_policy_environment(), enable_policy_environment() with RBAC + audit + resolver cache clearing
- **Config extensions** — environments store config (type + path) wired through loader into PolicyReleaseService and ActivePolicyResolver
- **CLI commands** — `agentapp policy environment list/disable/enable`, `agentapp policy activation rollback --environment <env>`
- **Console pages** — Environment detail page (GET /environments/{environment}), disable POST, enable POST, rollback POST
- **Console template** — `policy_environment_detail.html` with status badge, action forms, and activation history
- **Audit events** — policy.environment.disabled/enabled, policy.environment.disable_denied/enable_denied, policy.activation.rollback_completed/failed/rollback_denied, policy.runtime.policy_resolution_blocked
- **75+ tests** — Model (7), store (11), rollback (12), RBAC (6), resolver safety (6), service (14), config (4), CLI (8), console (7)

### Changed

- PolicyReleaseService gains rollback_environment(), disable_policy_environment(), enable_policy_environment(), environment_store property
- PolicyReleaseService.__init__() accepts environment_store parameter
- ActivePolicyResolver.__init__() accepts environment_store parameter; resolve and require methods check environment state
- PolicyActivation model gains rollback_of_activation_id and rollback_target_activation_id fields
- PolicyActivationStore protocol gains get_previous_activation() and rollback_to_activation() methods
- PolicyReleasePermission gains ENVIRONMENT_DISABLE, ENVIRONMENT_ENABLE, ENVIRONMENT_VIEW values
- PolicyReleasePermissionChecker default-allowed set includes ENVIRONMENT_VIEW
- Console router accepts environment_store parameter; adds environment detail, disable, enable, rollback routes
- FastAPI adapter passes environment_store to console router

## Phase 31: Policy Runtime Activation, Environment Isolation, and Hot Reload Baseline (0.19.0)

### Added

- **PolicyActivation model** — Environment-specific activation records with ACTIVE → SUPERSEDED → ROLLED_BACK lifecycle
- **PolicyActivationStore** — Protocol + InMemory + SQLite persistence for activation records
- **ActivePolicyResolver** — Runtime bundle resolution with config hash verification and TTL-aware caching
- **Environment isolation** — Only one ACTIVE activation per environment at any time
- **Config hash verification** — Activation config_hash must match bundle config_hash at resolve time
- **Environment-aware promotion** — execute_promotion() accepts environment parameter
- **Runtime config** — PolicyReleaseRuntimeConfig with environment, require_active_policy, cache_ttl_seconds
- **CLI activation commands** — `agentapp policy activation list` and `agentapp policy activation active`
- **Console activation pages** — /activations, /activations/{id}, /environments
- **RunContext.policy_environment** — Request-scoped environment targeting
- **RunContext.resolved_policy_bundle** — Resolved bundle attached to run context
- **AppRunner policy resolver integration** — _resolve_active_policy() method wires resolver into run lifecycle
- **38+ tests** — Model, store, resolver, service, config, CLI, console, context, app runner tests

### Changed

- PolicyReleaseService.execute_promotion() now creates PolicyActivation records when activation_store is configured
- PolicyReleaseService gains get_active_policy(), require_active_policy(), list_activations() methods

## Phase 30: Policy Promotion Approval, RBAC, and Console Write Governance (0.18.0)

### Added

- **PolicyReleasePermission** — 8 granular permissions for policy release operations (BUNDLE_CREATE, GATE_RUN, PROMOTION_REQUEST, PROMOTION_APPROVE, PROMOTION_REJECT, PROMOTION_EXECUTE, ROLLBACK_EXECUTE, BYPASS_GATE)
- **PolicyReleasePermissionChecker** — RBAC checker; BUNDLE_CREATE and GATE_RUN allowed by default; all others require explicit permission grant
- **PromotionRequest model** — Full lifecycle model with PENDING → APPROVED → REJECTED → EXECUTED / CANCELLED status
- **PromotionRequestStore** — Protocol + InMemory + SQLite implementation for promotion request persistence
- **PolicyReleaseService extensions** — `request_promotion()`, `approve_promotion()`, `reject_promotion()`, `execute_promotion()` with RBAC checks and audit logging
- **Gate bypass controls** — Triple gate: `bypass_gate=True` param AND `allow_gate_bypass=True` config AND `BYPASS_GATE` permission AND non-empty `bypass_reason`
- **Audit events** — `policy.promotion.requested`, `approved`, `rejected`, `executed`, `execute_blocked`, `gate.bypass_used`, `permission_denied`
- **Config extensions** — `promotions` store config, `require_promotion_approval`, `allow_gate_bypass` fields
- **CLI promotion subcommands** — `agentapp policy promotion request/list/approve/reject/execute` with `--actor-id`/`--permissions`
- **Console promotion pages** — GET list/detail + POST create/approve/reject/execute with permission-aware form handling
- **68+ tests** — RBAC, promotion model, promotion store, release service, CLI, console tests

### Changed

- `PolicyReleasePermissionError` now extends `PermissionError` for proper CLI exception handling

## Phase 29: Policy Release Gates & Versioned Policy Bundles (0.17.0)

### Added

- **DAG parallel execution** — `DagExecutionMode` enum (sequential/parallel) with asyncio-based ready-queue scheduler
- **Concurrency control** — `max_concurrency` field with `asyncio.Semaphore` for bounded parallelism
- **Node-level retry policy** — `RetryPolicy` model with `max_attempts`, `backoff_seconds`, `backoff_multiplier`, `retry_on_statuses`
- **Workflow-level retry default** — `DagWorkflow.retry` field; node-level retry takes priority
- **Node execution attempts** — `NodeExecutionAttempt` model; `NodeExecutionResult.attempts` records all tries
- **Exponential backoff** — configurable backoff with multiplier between retry attempts
- **Status propagation** — failed/interrupted nodes stop scheduling; downstream marked skipped; overall status preserved
- **DAG trace events** — per-node started/completed/failed/interrupted/skipped events; retry_scheduled/retry_started/retry_exhausted events
- **Invalid mode validation** — Pydantic enum validation for `execution_mode`; `max_concurrency` must be >= 1
- **Parallel DAG example** — `refund_parallel_dag` workflow in customer_support example
- **Parallel DAG eval suite** — `customer_support_parallel_dag.yaml` eval file
- **DAG benchmark** — `benchmarks/bench_dag.py` comparing sequential vs parallel vs concurrency-limited modes
- **FUNCTION node type** — `NodeType.FUNCTION` for executing Python functions from the DAG
- **Function registry** — `FunctionRegistry` with `@workflow_function` decorator for registering callable functions
- **Input mapping** — `_resolve_function_inputs()` supporting `input.*`, `nodes.*.output.*`, `context.*` patterns
- **Nested path resolution** — `_resolve_path()` for deep nested access (e.g., `nodes.a.output.data.amount`)
- **FUNCTION node permission enforcement** — permission checks against `execution_context["permissions"]` + node-level `permissions` field
- **FUNCTION_PERMISSION_DENIED event** — trace event emitted on permission denial
- **Subworkflow node type** — `NodeType.SUBWORKFLOW` for executing child DAG workflows
- **Subworkflow registry lookup** — `workflow_registry.get()` with KeyError handling
- **Subworkflow cycle detection** — `_subworkflow_chain` tracking prevents A→A and A→B→A references
- **Subworkflow input mapping** — reuses `_resolve_function_inputs()` for parent→child data flow
- **Subworkflow output wrapping** — `{"workflow": name, "status": "completed", "output": sub_output, "node_outputs": {...}}`
- **Subworkflow permission inheritance** — child inherits parent's `execution_context["permissions"]`
- **Subworkflow trace events** — `SUBWORKFLOW_STARTED`, `SUBWORKFLOW_COMPLETED`, `SUBWORKFLOW_FAILED`
- **Extended condition DSL** — `IN`, `NOT IN`, `STARTS_WITH`, `ENDS_WITH`, `NOT STARTS_WITH`, `NOT ENDS_WITH` operators
- **IF_ELSE branch node** — `NodeType.IF_ELSE` for conditional branching with `then`/`else_branch` node lists
- **SWITCH branch node** — `NodeType.SWITCH` for multi-way branching with `cases`/`default` routing
- **`IfElseResult` model** — structured output with condition_result, then_status, else_status, then_node_ids, else_node_ids
- **`SwitchResult` model** — structured output with matched_value, matched_case_index, executed_node_ids
- **`resolve_expression_value()`** — evaluates expressions to raw values for switch case matching
- **customer_support branch examples** — `refund_if_else_dag`, `refund_switch_dag` workflows
- **customer_support branch eval suite** — `customer_support_branch.yaml` eval file
- **Workflow-level deadline** — `deadline_seconds` field on `DagWorkflow` for total execution time limit
- **`WorkflowDeadlineExceededError`** — raised when deadline is exceeded; distinguishable from node timeout
- **`_DeadlineState` helper** — tracks absolute deadline, remaining time, effective timeout computation
- **Deadline-aware retry** — `min(node_timeout, remaining_deadline)` as effective timeout; backoff capped to remaining time
- **Parallel deadline enforcement** — `asyncio.wait` with deadline timeout; best-effort cancellation of running tasks
- **Sequential deadline enforcement** — checks deadline before scheduling each node; marks remaining as SKIPPED
- **Subworkflow deadline inheritance** — `min(parent_remaining, child_configured)` for child deadline
- **IF_ELSE/SWITCH deadline inheritance** — branches share parent's absolute deadline
- **`WORKFLOW_DEADLINE_EXCEEDED` event** — recorded when deadline is exceeded with full metadata
- **`NODE_CANCELLED_BY_DEADLINE` event** — recorded when a node is cancelled due to deadline
- **customer_support deadline example** — `refund_deadline_dag` workflow with 5s deadline
- **Compensation handlers** — `DagNode.compensate` and `DagWorkflow.compensation` for best-effort rollback
- **`CompensationStatus`** — NOT_STARTED, RUNNING, COMPLETED, PARTIAL, FAILED, SKIPPED
- **`NodeCompensationResult`** — per-node compensation outcome with status, attempts, error
- **`WorkflowCompensationResult`** — overall compensation outcome with compensated/skipped/failed lists
- **`CompensationError`** — DagError subclass for compensation failures
- **`_execute_compensation()`** — orchestrates candidate selection and handler execution in reverse completion order
- **`_get_compensation_candidates()`** — selects COMPLETED nodes with compensate config, ordered reverse-completion
- **`_resolve_compensation_inputs()`** — resolves compensation input mappings (reuses `_resolve_path`)
- **`_should_trigger_compensation()`** — gating logic based on workflow status and policy
- **7 compensation event types** — WORKFLOW_COMPENSATION_STARTED/COMPLETED/FAILED, NODE_COMPENSATION_STARTED/COMPLETED/FAILED/SKIPPED
- **`execute()` 4-tuple return** — `(results, status, output, compensation_result)` with None when not triggered
- **customer_support compensation example** — `refund_compensation_dag` with `order.revert_extraction` and `refund.revert_calculation` handlers
- **customer_support compensation eval** — `customer_support_compensation.yaml` with 3 regression cases
- **Compensation benchmark** — baseline, configured-not-triggered, and triggered scenarios in `benchmarks/bench_dag.py`
- **30 compensation tests** — config loading, sequential, parallel, deadline, timeout/retry, branch, and event tests

### Changed

- `DagWorkflow` — new fields: `execution_mode`, `max_concurrency`, `retry`, `timeout_seconds`, `deadline_seconds`, `compensation`
- `DagNode` — new fields: `retry`, `condition`, `timeout_seconds`, `permissions`, `subworkflow_name`, `then`, `else_branch`, `switch_expr`, `cases`, `compensate`
- `NodeExecutionResult` — new field: `attempts` (list of `NodeExecutionAttempt`)
- `NodeType` — new values: `FUNCTION`, `SUBWORKFLOW`, `IF_ELSE`, `SWITCH`
- `DagExecutor` — condition checking; timeout wrapping; unified event recording; function/subworkflow/if_else/switch execution; compensation orchestration
- `DagExecutor` — `_subworkflow_chain` parameter for cycle detection; `_result:<id>` in execution_context for condition evaluators
- `DagExecutor.execute()` — now returns 4-tuple `(results, status, output, compensation_result)`; backward-compatible with `_` discard
- `Workflow.dag()` — accepts `execution_mode`, `max_concurrency`, `retry`, `timeout_seconds`, `deadline_seconds`, `compensation`; validates compensation policy
- `condition.py` — extended tokenizer with IN/STARTS_WITH/ENDS_WITH/comma support; added `InExpression` AST node; added `resolve_expression_value()`
- `RunEventType` — new values: `FUNCTION_PERMISSION_DENIED`, `SUBWORKFLOW_STARTED`, `SUBWORKFLOW_COMPLETED`, `SUBWORKFLOW_FAILED`, `WORKFLOW_DEADLINE_EXCEEDED`, `NODE_CANCELLED_BY_DEADLINE`, WORKFLOW_COMPENSATION_STARTED/COMPLETED/FAILED, NODE_COMPENSATION_STARTED/COMPLETED/FAILED/SKIPPED
- `DagWorkflow` — new field: `deadline_seconds` (workflow-level execution deadline)
- `Workflow.dag()` — accepts `deadline_seconds`; validates > 0
- **245 total DAG tests passing** — 215 Phase 13.1–13.8 + 30 Phase 13.9 compensation tests

## 0.10.0 (Phase 14.0: Persisted DAG Execution State)

### Added

- **WorkflowRunState** — Pydantic model for persisted DAG workflow execution state (run_id, status, input, output, error, timestamps, metadata)
- **NodeExecutionState** — Pydantic model for persisted node execution state (run_id, node_id, node_type, status, input, output, error, attempts, timestamps)
- **WorkflowEventState** — Pydantic model for persisted workflow/node events (event_id, run_id, node_id, event_type, payload, created_at)
- **CompensationExecutionState** — Pydantic model for persisted compensation handler execution (run_id, node_id, handler_name, status, error, timestamps)
- **WorkflowStateStore protocol** — async interface for CRUD operations on workflow runs, nodes, events, and compensations
- **InMemoryWorkflowStateStore** — in-memory implementation for development/testing
- **SQLiteWorkflowStateStore** — SQLite-backed implementation using stdlib `sqlite3`; auto-creates tables and directories; survives process restarts
- **create_workflow_state_store()** — factory function for store instantiation
- **RecoveryPlan model** — resumability assessment (completed_nodes, interrupted_nodes, failed_nodes, compensation_started, reason)
- **build_recovery_plan()** — shared recovery plan builder used by both store implementations
- **DagExecutor state_store integration** — optional `state_store` and `run_id` parameters; persists node states and events during execution
- **WorkflowExecutor state_store forwarding** — `dag_state_store` parameter threaded through to DagExecutor
- **AgentApp/AppRunner state_store plumbing** — `_dag_state_store` attribute threaded from config → AgentApp → AppRunner → WorkflowExecutor
- **Config support** — `runtime.workflow_state.type` (memory/sqlite) and `runtime.workflow_state.path` in YAML config; normalized alongside existing session/run_state config
- **53 new Phase 14.0 tests** — store CRUD, SQLite cross-instance, recovery plan, config, DAG executor integration

### Changed

- `DagExecutor.__init__()` — new optional `state_store` and `run_id` parameters (backward compatible; no state persisted when not provided)
- `DagExecutor.execute()` — creates workflow run record and persists final status when state_store is configured
- `DagExecutor._persist_node_state()` — helper for node state persistence; records status, output, error, attempts
- `DagExecutor._persist_event()` — helper for event persistence
- `RuntimeConfig` — new fields: `workflow_state_type`, `workflow_state_path`; `_normalize_workflow_state` validator for nested config
- `config/loader.py` — wires workflow_state store creation and passes to AgentApp
- `agent_app/core/app.py` — `_dag_state_store` attribute and threading through `_ensure_runner()` and `_run_workflow()`
- `agent_app/runtime/app_runner.py` — `dag_state_store` parameter in `__init__`
- `agent_app/runtime/workflow_executor.py` — `dag_state_store` parameter in `__init__`; passed to DagExecutor in `_run_dag()`

### Current Limitations

- RecoveryPlan is inspect/planning only — no automatic resumption of interrupted nodes
- Running nodes without `completed_at` are identified as interrupted; no automatic restart
- No distributed locking or worker lease mechanism
- No exactly-once execution guarantee
- No Temporal/Celery backend
- Subworkflow independent compensation remains a future phase
- SQLite store uses stdlib `sqlite3` — no connection pooling or WAL mode
- State store is DAG-specific; does not cover SINGLE/HANDOFF/ORCHESTRATOR workflow types

## 0.10.0 (Phase 14.1: DAG Resume Semantics)

### Added

- **ResumePolicy** — Pydantic model controlling resume behavior (retry_failed, retry_interrupted, skip_completed, allow_after_compensation_started)
- **NodeResumeDecision** — per-node resume decision (action: skip/retry/run/blocked with reason)
- **ResumePlan** — structured resume plan with per-node decisions, completed/retry/blocked/skipped lists, resumable flag, reason
- **ResumeResult** — model for resume operation outcome (status, resumed, skipped/retried nodes, final_output, error)
- **WorkflowStateStore resume methods** — `build_resume_plan(run_id, policy)` and `get_node_outputs(run_id)` added to protocol and both store implementations
- **`_build_resume_plan()`** — shared policy-driven decision builder; handles completed/skipped (skip), interrupted (retry), failed (retry/blocked), pending (run), compensation started (blocked)
- **`DagExecutor.resume()`** — ~200 line method that loads persisted state, builds resume plan, injects persisted outputs, executes retry/run nodes in topological order, persists resumed states, records resume events, optionally triggers compensation
- **`WorkflowExecutor.resume_workflow_run()`** — reconstructs DagWorkflow from config, creates DagExecutor with state_store/run_id, delegates to `DagExecutor.resume()`
- **`AppRunner.resume_workflow_run()`** — looks up DAG workflow by name, delegates to WorkflowExecutor
- **`AgentApp.resume_workflow_run()`** — public API: `app.resume_workflow_run(workflow, run_id, ...)`
- **WorkflowExecutor.app_runner plumbing** — `app_runner` parameter added to `WorkflowExecutor.__init__()` for DAG agent node execution during resume
- **`list_runs()`** — added to both InMemoryWorkflowStateStore and SQLiteWorkflowStateStore
- **82 new Phase 14.1 tests** — resume plan building (completed/interrupted/failed/compensation/unknown), DagExecutor.resume() (state_store required, unknown run_id, skip completed, retry interrupted, retry_failed policy, blocked downstream, compensation block, skipped nodes, event persistence, parallel DAG), WorkflowExecutor/AgentApp API (no state_store, unknown workflow, end-to-end)

### Changed

- `InMemoryWorkflowStateStore` — added `list_runs()` method
- `SQLiteWorkflowStateStore` — added `list_runs()` method; fixed `NodeRunStatus.INTERRUPTED` → `NodeRunStatus.RUNNING` reference
- `_build_resume_plan()` — run is resumable unless compensation started; blocked nodes (policy-driven) don't prevent resume (handled downstream); PENDING nodes → "run"; COMPENSATING/COMPENSATED → "skip"
- `DagExecutor.resume()` — blocked nodes recorded as FAILED status with downstream skipping; status propagated to overall_status
- `AppRunner.__init__()` — creates WorkflowExecutor with `app_runner=self` for DAG execution support

### Current Limitations

- Resume is explicit (user calls `app.resume_workflow_run()`); no automatic resume on app restart
- `allow_after_compensation_started` is accepted but not implemented (default False blocks resume)
- Parallel compensation order based on completion timestamp (may vary between runs)
- Deadline cancellation is best-effort — external side effects may have already occurred
- Subworkflow compensation delegates to parent (no independent subworkflow compensation yet)
- No distributed execution, Temporal/Celery backend, or visual DAG editor

## 0.10.0 (Phase 15: Distributed Execution Readiness)

### Added

- **WorkerIdentity** — Pydantic model identifying a worker (worker_id, hostname, process_id, app_version, metadata); auto-generated default worker_id
- **WorkflowRunLease** — Pydantic model for workflow run lease (run_id, owner_id, acquired_at, expires_at, renewed_at, released_at, version); requires timezone-aware UTC datetimes
- **LeaseStatus** — enum: ACQUIRED, DENIED, EXPIRED, RELEASED
- **LeasePolicy** — Pydantic model (ttl_seconds=300, allow_steal_expired=True, renew_before_seconds=60)
- **LeaseAcquireResult** — Pydantic model (acquired, run_id, owner_id, lease, reason, current_owner_id, expires_at)
- **IdempotencyRecord** — Pydantic model for idempotency key tracking (key, run_id, operation, created_at, result_ref)
- **WorkflowStateStore lease methods** — `acquire_run_lease()`, `renew_run_lease()`, `release_run_lease()`, `get_run_lease()`, `list_expired_leases()` added to protocol and both store implementations
- **WorkflowStateStore idempotency methods** — `put_idempotency_record()`, `get_idempotency_record()` added to protocol and both store implementations
- **InMemory lease management** — full lease lifecycle (acquire, deny, renew, release, steal expired, list expired)
- **SQLite lease persistence** — `workflow_run_leases` table with auto-create; cross-instance visibility; transaction-based operations
- **SQLite idempotency persistence** — `workflow_idempotency` table with upsert semantics
- **DagExecutor lease integration** — `_acquire_lease()` before execute/resume; `_release_lease()` in finally block; `_get_worker()` with caching
- **DagExecutor.execute()** — wraps execution in try/acquire/finally/release; raises DagError if lease denied
- **DagExecutor.resume()** — acquires lease after building resume plan; releases in finally block
- **Worker plumbing** — `worker` parameter threaded through AgentApp → AppRunner → WorkflowExecutor → DagExecutor
- **Lease lifecycle events** — `workflow.lease_acquired`, `workflow.lease_denied`, `workflow.lease_renewed`, `workflow.lease_released` persisted to state store
- **41 new Phase 15 tests** — lease models (5), InMemory lease (10), SQLite lease (8), idempotency (4), DagExecutor lease integration (7)

### Changed

- `DagExecutor.__init__()` — new optional `worker` parameter
- `DagExecutor` — cached worker identity (`_cached_worker`) ensures acquire/release use same worker_id
- `WorkflowExecutor.run_workflow()` — new optional `worker` parameter; passed to `_run_dag()`
- `WorkflowExecutor._run_dag()` — new optional `worker` parameter; passed to DagExecutor
- `WorkflowExecutor.resume_workflow_run()` — new optional `worker` parameter; passed to DagExecutor
- `AppRunner.resume_workflow_run()` — new optional `worker` parameter
- `AgentApp.run()` — new optional `worker` parameter; forwarded to WorkflowExecutor
- `AgentApp._run_workflow()` — new optional `worker` parameter; forwarded to WorkflowExecutor
- `AgentApp.resume_workflow_run()` — new optional `worker` parameter; forwarded to AppRunner

### Current Limitations

- Lease is best-effort coordination — does not provide exactly-once guarantee
- No Celery / Temporal / distributed worker backend
- No automatic recovery daemon
- No node-level distributed scheduling
- No cross-process streaming fanout
- SQLite store uses stdlib sqlite3 — no connection pooling or WAL mode
- Lease TTL is in-memory checked; no background renewal daemon
- Idempotency records stored but not enforced at API level (Phase 15.1+)

## 0.10.0 (Phase 15.1: API-level Idempotency Enforcement)

### Added

- **Request fingerprinting** — SHA-256 of deterministic JSON (sorted keys, no whitespace, `default=str`) for stable request identification
- **Transient field exclusion** — `idempotency_key`, `worker`, `trace_id`, `request_id`, `correlation_id` excluded from fingerprint computation
- **Scope isolation** — `compute_scope(tenant_id, operation)` produces `"{tenant_id}:{operation}"` namespace preventing cross-tenant key collisions
- **Payload builders** — `build_execute_payload()` and `build_resume_payload()` for stable, minimal fingerprint input
- **IdempotencyRecord extended** — new fields: `scope` (scoped namespace) and `request_fingerprint` (SHA-256 hex digest)
- **`DuplicateIdempotencyKeyError`** — raised when same key is reused with identical fingerprint (true duplicate)
- **`IdempotencyKeyMismatchError`** — raised when same key is reused with different fingerprint (replay attack / client error)
- **Atomic `reserve_idempotency_key()`** — single enforcement point; delegates to store's atomic reservation
- **InMemory atomic reservation** — composite key `"{scope}:{key}"` with atomic check-and-set
- **SQLite atomic reservation** — `PRIMARY KEY (scope, key)` with explicit `BEGIN`/`COMMIT`/`ROLLBACK` transaction; `IntegrityError` determines conflict type
- **SQLite schema migration** — `_add_idempotency_columns()` migrates old tables (no scope column) to new composite-key schema
- **DagExecutor `_enforce_idempotency()`** — called before lease acquire in both `execute()` and `resume()`; builds payload, computes fingerprint, creates record, calls store reservation
- **Worker identity caching** — `_cached_worker` and `_current_input` ensure consistent fingerprinting across enforcement calls
- **AgentApp → AppRunner → WorkflowExecutor → DagExecutor plumbing** — `idempotency_key` parameter threaded through entire call chain for both execute and resume
- **FastAPI `Idempotency-Key` header support** — header takes priority over JSON body `idempotency_key` field
- **HTTP 409 mapping** — `DuplicateIdempotencyKeyError` and `IdempotencyKeyMismatchError` mapped to HTTP 409 Conflict via `_extract_idempotency_error()` helper
- **34 new Phase 15.1 tests** — fingerprint (5), scope (3), errors (2), InMemory (6), SQLite (6), DagExecutor (6), cross-instance (2), backward compatibility (2)

### Changed

- `IdempotencyRecord` — new optional fields: `scope`, `request_fingerprint`
- `WorkflowStateStore` protocol — new method: `reserve_idempotency_key(record)` with atomic semantics
- `InMemoryWorkflowStateStore` — composite key for scope isolation; atomic reservation
- `SQLiteWorkflowStateStore` — composite PRIMARY KEY (scope, key); transaction-based atomic reservation; schema migration
- `DagExecutor.__init__()` — new optional `idempotency_key` parameter; `_current_input` attribute for fingerprinting
- `DagExecutor` — `_enforce_idempotency()` called before lease acquire
- `WorkflowExecutor.run_workflow()` — new optional `idempotency_key` parameter
- `WorkflowExecutor.resume_workflow_run()` — new optional `idempotency_key` parameter
- `AppRunner.run()` — new optional `idempotency_key` parameter
- `AppRunner.resume_workflow_run()` — new optional `idempotency_key` parameter
- `AgentApp.run()` — new optional `idempotency_key` parameter
- `AgentApp.resume_workflow_run()` — new optional `idempotency_key` parameter
- `RunRequest` — new optional `idempotency_key` field (body-level, header takes priority)
- FastAPI `/runs` and `/runs/{run_id}/resume` — idempotency key extraction and HTTP 409 error mapping

### Current Limitations

- Best-effort API-level duplicate prevention only — NOT exactly-once execution
- Without `idempotency_key`: old behavior unchanged (no enforcement)
- With `idempotency_key`: single-use enforcement before side-effect-producing operations
- No background lease renewal daemon
- No distributed worker backend (Celery/Temporal not implemented)
- Scope defaults to `{tenant_id}:{operation}`; cannot be customized per-request
- Fingerprint is best-effort; semantically identical payloads with different serialization will produce different fingerprints

## 0.10.0 (Phase 15.2: Background Lease Renewal / Heartbeat)

### Added

- **`LeaseRenewer`** — asyncio background task that periodically calls `renew_run_lease` on the state store; best-effort in-process renewal (NOT distributed, NOT Celery/Temporal, NOT exactly-once)
- **`LeaseLostError`** — stable error type with `to_dict()` method; raised when renewal fails during execution
- **`renew_run_lease`** — added to `WorkflowStateStore` protocol and both InMemory/SQLite implementations; validates owner, release status, and expiration
- **Lease expiration check** — `renew_run_lease` rejects expired leases (now >= expires_at)
- **`LeaseRenewalConfig`** — Pydantic model (`renew_enabled=True`, `renew_interval_seconds=None`, `ttl_seconds=300`); added to `RuntimeConfig`
- **Config normalization** — `_normalize_lease_renewal` validator supports flat and nested YAML formats
- **`DagExecutor` lease renewal integration** — `_make_renewer()` creates `LeaseRenewer`; `execute()` and `resume()` start/stop renewer with deferred `LeaseLostError` pattern
- **Idempotency ordering preserved** — idempotency enforcement → lease acquire → renewer start → execute → renewer stop → lease release → raise `LeaseLostError` if needed
- **Config plumbing** — `lease_renewal_config` threaded through AgentApp → AppRunner → WorkflowExecutor → DagExecutor
- **28 new Phase 15.2 tests** — LeaseRenewer (6), InMemory lease renewal (6), SQLite lease renewal (5), DagExecutor integration (5), config (5)

### Changed

- `LeaseLostError` — canonical definition in `dag_run_state.py`; re-exported from `lease_renewer.py`
- `renew_run_lease` — now checks lease expiration; expired leases cannot be renewed
- `DagExecutor.__init__()` — new optional `lease_renewal_config` parameter
- `DagExecutor.execute()` — integrates `LeaseRenewer` with start/stop lifecycle and deferred error pattern
- `DagExecutor.resume()` — same lease renewal integration for resume path
- `WorkflowExecutor.__init__()` — new optional `lease_renewal_config` parameter
- `AppRunner.__init__()` — new optional `lease_renewal_config` parameter
- `AgentApp.__init__()` — new optional `lease_renewal_config` parameter
- `config/loader.py` — passes `lease_renewal_config` from RuntimeConfig to AgentApp

### Current Limitations

- Best-effort in-process renewal only — does NOT provide exactly-once guarantee
- Only works while the current process is alive — no distributed worker daemon
- No Celery / Temporal / distributed worker backend
- Renewal failure → `lease_lost=True` → stable error (workflow must be manually resumed)
- Default interval = `ttl_seconds / 3`; configurable via `renew_interval_seconds`

## 0.9.0

### Added

- **Structured RunEvent model** — `RunEventType` enum (22 event types) + `RunEvent` Pydantic model with timezone-aware timestamps
- **TraceCollector protocol** — `record()`, `get_events()`, `list_traces()` interface
- **NoOpTraceCollector** — zero-cost no-op for disabled tracing
- **InMemoryTraceCollector** — in-process event storage with tenant/run filtering; supports optional `max_traces` and `max_events_per_trace` retention limits
- **JSONLTraceCollector** — append-only JSONL file storage for local debugging; supports `count_events()`, `count_traces()`, `compact()` maintenance utilities
- **AppRunner instrumentation** — emits run.started, run.completed, run.failed, run.interrupted, run_state.saved events
- **ToolExecutor instrumentation** — emits tool.started, tool.completed, tool.failed, tool.permission_denied, tool.approval_required, approval.created events
- **WorkflowExecutor instrumentation** — emits workflow.started, workflow.completed, workflow.failed, routing.decision, handoff.occurred, agent.started, agent.completed events
- **AgentApp approve/reject/resume instrumentation** — emits approval.approved, approval.rejected, run_state.resumed events
- **OpenAIAgentsBackend instrumentation** — emits agent.started, agent.completed, agent.failed events
- **AppRunResult.trace_events** — structured events attached to every run result
- **RunContext.trace_id** — observability trace identifier propagated through execution
- **Observability config** — `observability.tracing.type` (noop/memory/jsonl), `max_traces`, `max_events_per_trace` in YAML config
- **Config loader integration** — `build_app()` creates trace collector with retention settings and passes to all components
- **FastAPI trace endpoints** — `GET /traces` (with run_id/tenant_id/event_type/limit filtering) and `GET /traces/{trace_id}` (404 on missing)
- **FastAPI `TraceSummary` model** — structured trace list response
- **CLI trace commands** — `agentapp trace list` (table/JSON, filters) and `agentapp trace show` (human-readable/JSON, non-zero exit on missing)
- **Eval `trace_events` assertion** — assert Tier 1 synchronous events in eval YAML
- **Event reliability tiers** — Tier 1 (synchronous, safe for eval) vs Tier 2 (fire-and-forget, collector-level tests)
- **OpenTelemetry bridge stub** — optional `OpenTelemetryTraceExporter` (experimental, install via `pip install agent-app-framework[otel]`)
- **Tracing benchmark script** — `scripts/benchmark_tracing.py` for local overhead measurement
- **75+ new Phase 12 tests** — Steps 1-6, no regressions
- **`docs/observability.md`** — full observability documentation with reliability tiers, CLI/FastAPI examples, limitations
- **README Observability section** — quick start, eval integration, FastAPI endpoints, Tier 1/Tier 2 table

### Changed

- `AppRunResult` — new `trace_events` field (list of RunEvent)
- `RunContext` — new optional `trace_id` field
- `AppRunner.__init__()` — new optional `trace_collector` parameter
- `AgentApp.__init__()` — new optional `trace_collector` parameter
- `TracingConfig` — new optional `max_traces` and `max_events_per_trace` fields (backward compatible)
- `InMemoryTraceCollector.__init__()` — accepts optional `max_traces` and `max_events_per_trace`
- `JSONLTraceCollector` — new `count_events()`, `count_traces()`, `compact()` methods
- `pyproject.toml` — new optional `otel` extra

### Current Limitations

- Tier 2 events (workflow, tool, approval, run_state) are fire-and-forget — not suitable for eval YAML assertions
- No drain/flush API — intentionally deferred
- No OpenTelemetry OTLP export yet — bridge is experimental stub only

## 0.10.0 (Phase 16.0: DAG Persistence Snapshots and Enhanced Resume)

### Added

- **DagRunSnapshot** — Pydantic model capturing DAG execution state (run_id, status, completed/failed/current/pending node IDs, per-node snapshots, execution context, schema_version, timestamps)
- **DagNodeSnapshot** — per-node execution snapshot (node_id, status, attempts, output, error, started_at, completed_at)
- **DagSnapshotStatus** — StrEnum: RUNNING, COMPLETED, FAILED, PARTIAL, INTERRUPTED
- **Snapshot serialization** — `to_json()` / `from_json()` with timezone-aware ISO datetime; schema_version tracking for migration safety
- **Snapshot error types** — `SnapshotWriteError`, `SnapshotCorruptionError`, `SnapshotUnsupportedVersionError` — all with `to_dict()` for stable error responses
- **WorkflowStateStore snapshot methods** — `save_run_snapshot()`, `get_latest_run_snapshot()`, `list_run_snapshots()`, `delete_run_snapshots()` added to protocol and both store implementations
- **InMemory snapshot store** — `_snapshots: dict[str, list[DagRunSnapshot]]` with CRUD, overwrite-by-snapshot_id, run isolation, ordered listing
- **SQLite snapshot persistence** — `dag_run_snapshots` table (snapshot_id PK, run_id, workflow_name, status, schema_version, snapshot_json, timestamps); `idx_dag_run_snapshots_run_updated` index; auto-create on init; survives process restarts
- **DagSnapshotConfig** — Pydantic model (`enabled=True`, `store=memory`, `path=None`, `save_on_node_start/complete/interrupt/failure=True`); configurable per-transition save flags
- **DagExecutor snapshot integration** — `_is_snapshot_enabled()`, `_build_snapshot()`, `_save_snapshot()`, `_maybe_save_snapshot()` helpers
- **execute() snapshot lifecycle** — initial "running" snapshot after lease acquire; node-level snapshots via `_maybe_save_snapshot()` after each node and on failure; final "completed"/"failed" snapshot; snapshot errors are stable (SnapshotWriteError) for initial/final, best-effort (logged warning) for intermediate
- **resume() snapshot acceleration** — reads latest snapshot via `get_latest_run_snapshot()`; validates schema_version (only v1 supported), run_id match, resumability; completed snapshot returns idempotent empty result; corruption/version errors caught and fall through to existing resume logic
- **Config support** — `runtime.dag_snapshot` (nested) or `runtime.dag_snapshot_config` (flat) in YAML; `_normalize_dag_snapshot` validator; wired through config/loader → AgentApp → AppRunner → WorkflowExecutor → DagExecutor
- **62 new Phase 16.0 tests** — DagRunSnapshot model (8), DagNodeSnapshot (3), serialization (4), error types (3), InMemory store (6), SQLite store (7), DagSnapshotConfig (6), RuntimeConfig normalization (3), DagExecutor snapshot integration (8), resume snapshot (5), error handling (2), _is_snapshot_enabled (5), _build_snapshot (2), config plumbing (2)

### Changed

- `RuntimeConfig` — new optional `dag_snapshot_config: DagSnapshotConfig | None` field
- `RuntimeConfig` — `_normalize_dag_snapshot` model_validator for nested YAML config normalization
- `DagExecutor.__init__()` — new optional `snapshot_config` parameter
- `DagExecutor.execute()` — saves initial/completion/failure snapshots; calls `_maybe_save_snapshot()` after node transitions
- `DagExecutor.resume()` — loads latest snapshot for resume acceleration; validates and falls through on error
- `DagExecutor._execute_sequential()` — calls `_maybe_save_snapshot()` after each node completion and on failure/interruption
- `DagExecutor._execute_parallel()` — calls `_maybe_save_snapshot()` after each node batch completion
- `WorkflowStateStore` protocol — 4 new async methods for snapshot CRUD
- `InMemoryWorkflowStateStore` — snapshot CRUD with in-memory storage
- `SQLiteWorkflowStateStore` — snapshot CRUD with SQLite persistence; auto-creates `dag_run_snapshots` table
- `WorkflowExecutor.__init__()` — new optional `dag_snapshot_config` parameter; passed to DagExecutor
- `AppRunner.__init__()` — new optional `dag_snapshot_config` parameter; passed to WorkflowExecutor
- `AgentApp.__init__()` — new optional `dag_snapshot_config` parameter; passed to AppRunner
- `config/loader.py` — passes `dag_snapshot_config` from RuntimeConfig to AgentApp

### Current Limitations

- Snapshots are recovery aids — do NOT guarantee exactly-once execution
- Snapshots are NOT a distributed transaction log (no Celery/Temporal)
- No automatic recovery daemon — resume is explicit via `app.resume_workflow_run()`
- SQLite store uses stdlib sqlite3 — no connection pooling or WAL mode
- Schema version migration is manual (only v1 supported; future versions require code migration)
- Intermediate snapshots are best-effort (failure logged but does not block execution)
- Snapshot persistence adds I/O overhead proportional to snapshot frequency
- No visual dashboard — trace viewing via CLI, API, or JSONL file
- InMemoryTraceCollector is per-process only — use JSONL for persistence
- Benchmark script is rough measurement, not rigorous performance test
- `ToolExecutor.__init__()` — new optional `trace_collector` parameter
- `WorkflowExecutor.__init__()` — new optional `trace_collector` parameter
- `OpenAIAgentsBackend.__init__()` — new optional `trace_collector` parameter

### Known limitations

- No OpenTelemetry integration (planned for future phase)
- FastAPI trace endpoints not yet implemented
- CLI trace commands not yet implemented
- Eval trace_events assertions not yet implemented
- Pydantic json_encoders deprecation warning (cosmetic, no functional impact)
- ToolExecutor / WorkflowExecutor event emission deferred to Step 2
- FastAPI trace endpoints not yet implemented
- CLI trace commands not yet implemented
- Eval trace_events assertions not yet implemented

## 0.10.0 (Phase 16.1: Compensation State Persistence)

### Added

- **CompensationActionState** — Pydantic model tracking per-action compensation execution (action_id, run_id, node_id, compensating_for_node_id, status, attempts, max_attempts, input, output, error, idempotency_key, timestamps); auto-generated action_id via `default_factory`
- **CompensationExecutionState** — Pydantic model for per-run compensation state (compensation_id, run_id, workflow_name, status, schema_version, actions dict, action_order list, timestamps); auto-generated compensation_id; `model_validator` syncs action_order
- **CompensationActionStatus** — StrEnum: PENDING, RUNNING, COMPLETED, FAILED, SKIPPED
- **CompensationRunStatus** — StrEnum: NOT_REQUIRED, PENDING, RUNNING, COMPLETED, PARTIAL_FAILED, FAILED
- **CompensationStateStore protocol** — async interface: `save_compensation_state()`, `get_compensation_state()`, `update_compensation_action()`, `list_compensation_states()`, `delete_compensation_state()`
- **InMemoryCompensationStateStore** — in-memory implementation keyed by run_id; supports CRUD, filtering by workflow_name
- **SQLiteCompensationStateStore** — SQLite-backed implementation with `dag_compensation_states` table (compensation_id PK, run_id UNIQUE, indexes on run_id and workflow_name+status); auto-creates tables; survives process restarts; handles corrupted JSON gracefully
- **`create_compensation_state_store()`** — factory function ("memory" or "sqlite")
- **DagCompensationConfig** — Pydantic config model (enabled=True, store="memory", path=None, max_attempts=1, resume_incomplete=True); store validator rejects unknown types
- **DagExecutor compensation persistence** — `_init_compensation_store()` lazy init; `_is_compensation_persistence_enabled()` check; `_create_compensation_state()` builds state from compensation candidates; `_save_compensation_state()` with SnapshotWriteError on failure; `_update_compensation_action()` best-effort store update; `_get_compensation_state()` retrieval; `_resume_compensation()` resumes from persisted state
- **Resume integration** — `resume()` loads persisted compensation state via `_get_compensation_state()`; skips completed actions, retries failed actions within max_attempts, executes pending actions; updates store after each action
- **Config plumbing** — `dag_compensation_config` normalized from `dag_compensation` YAML key; threaded through config/loader → AgentApp → AppRunner → WorkflowExecutor → DagExecutor
- **Serialization** — `serialize_compensation_state()` / `deserialize_compensation_state()` with timezone-aware ISO datetime; handles corrupted JSON with ValueError
- **97 new Phase 16.1 tests** — CompensationActionState (12), CompensationExecutionState (14), serialization (7), InMemory store (9), SQLite store (14), DagExecutor integration (25), config plumbing (5), resume compensation (3), error handling (2), factory (4)

### Changed

- `RuntimeConfig` — new optional `dag_compensation_config: DagCompensationConfig | None` field; `_normalize_dag_compensation` validator
- `DagExecutor.__init__()` — new optional `compensation_config` parameter; `_compensation_store` attribute
- `DagExecutor.execute()` — calls `_init_compensation_store()` after renewer start; creates/saves compensation state when compensation triggered
- `DagExecutor.resume()` — checks compensation state for incomplete runs; resumes via `_resume_compensation()`
- `DagExecutor._execute_compensation()` — creates compensation state before handler loop; updates action status after each handler; finalizes state on completion
- `config/loader.py` — passes `dag_compensation_config` from RuntimeConfig to AgentApp
- `agent_app/core/app.py` — `_dag_compensation_config` attribute and threading through `_ensure_runner()`
- `agent_app/runtime/app_runner.py` — `dag_compensation_config` parameter in `__init__`
- `agent_app/runtime/workflow_executor.py` — `dag_compensation_config` parameter in `__init__`; passed to DagExecutor

### Current Limitations

- Compensation state is a recovery aid — does NOT guarantee exactly-once execution
- NOT a distributed transaction log (no Celery/Temporal/Redis/etcd)
- No automatic recovery daemon — resume is explicit via `app.resume_workflow_run()`
- External side effect idempotency remains the business tool's responsibility
- SQLite store uses stdlib sqlite3 — no connection pooling or WAL mode
- Compensation state is independent from snapshots and lease state (each has its own persistence layer)
- Does NOT replace lease renewal, snapshot, or business-level idempotency

## 0.10.0 (Phase 16.2: Lease Backend Abstraction)

### Added

- **`WorkflowLeaseBackend` Protocol** — pluggable interface for lease coordination (`acquire_run_lease`, `renew_run_lease`, `release_run_lease`, `get_run_lease`, `list_expired_leases`); reuses existing models (WorkerIdentity, LeasePolicy, WorkflowRunLease, LeaseAcquireResult)
- **`StateStoreLeaseBackend`** — adapter wrapping `WorkflowStateStore` as a `WorkflowLeaseBackend`; preserves full backward compatibility with existing state store lease methods
- **`InMemoryWorkflowLeaseBackend`** — standalone in-memory lease backend; five-path acquire logic (no lease, released, expired-steal, same-owner refresh, different-owner deny); supports renew, release, get, list_expired
- **`SQLiteWorkflowLeaseBackend`** — standalone SQLite lease backend with `workflow_run_leases` table; cross-instance visibility; auto-creates tables and directories; in-memory cache with DB re-sync on `get_run_lease`
- **`create_lease_backend()`** — factory function supporting "state_store", "memory", "sqlite" backend types
- **`LeaseCoordinator`** — thin coordination layer over `WorkflowLeaseBackend`; applies default `LeasePolicy` when none provided; unified entry point for acquire/renew/release/get/list_expired
- **`LeaseRenewer` Phase 16.2 support** — new optional `lease_backend` parameter; takes precedence over `state_store`; backward compatible with legacy `state_store` parameter (auto-wraps via `StateStoreLeaseBackend`)
- **`DagExecutor` lease backend injection** — new optional `lease_backend` and `lease_policy` parameters; `_get_lease_backend()` returns explicit backend > state_store > None; `_acquire_lease()`, `_release_lease()`, `_make_renewer()` all use effective lease backend
- **`WorkflowExecutor` lease backend helpers** — `_build_lease_backend()` creates backend from `DagLeaseConfig`; `_build_lease_policy()` creates `LeasePolicy` from config; passed to `DagExecutor` in both `run_workflow()` and `resume_workflow_run()`
- **`DagLeaseConfig`** — Pydantic config model (backend="state_store", db_path=None, ttl_seconds=300, allow_steal_expired=True, renew_before_seconds=60); backend validator rejects unknown types
- **Config support** — `runtime.dag_lease` (nested) or `runtime.dag_lease_config` (flat) in YAML; `_normalize_dag_lease` validator; threaded through config/loader → AgentApp → AppRunner → WorkflowExecutor → WorkflowExecutor → DagExecutor
- **75 new Phase 16.2 tests** — StateStoreLeaseBackend (7), InMemory lease backend (11), SQLite lease backend (8), factory (7), protocol typing (3), LeaseCoordinator (10), LeaseRenewer (5), DagExecutor (8), config (6)

### Changed

- `RuntimeConfig` — new optional `dag_lease_config: DagLeaseConfig | None` field; `_normalize_dag_lease` model_validator
- `DagExecutor.__init__()` — new optional `lease_backend` and `lease_policy` parameters
- `DagExecutor._acquire_lease()` — uses `_get_lease_backend()` instead of direct state_store access
- `DagExecutor._release_lease()` — uses `_get_lease_backend()` instead of direct state_store access
- `DagExecutor._make_renewer()` — uses effective lease backend; detects standalone vs state_store backend
- `LeaseRenewer.__init__()` — new optional `lease_backend` parameter; backward compatible with `state_store`
- `LeaseRenewer._renew_loop()` — uses `self._lease_backend` for renew calls; keeps `self._state_store` for terminal-state check
- `WorkflowExecutor.__init__()` — new optional `dag_lease_config` parameter; `_build_lease_backend()` and `_build_lease_policy()` helpers
- `WorkflowExecutor.run_workflow()` — passes `lease_backend` and `lease_policy` to DagExecutor
- `WorkflowExecutor.resume_workflow_run()` — passes `lease_backend` and `lease_policy` to DagExecutor
- `AppRunner.__init__()` — new optional `dag_lease_config` parameter; passed to WorkflowExecutor
- `AgentApp.__init__()` — new optional `dag_lease_config` parameter; passed through `_ensure_runner()`
- `config/loader.py` — passes `dag_lease_config` from RuntimeConfig to AgentApp

### Current Limitations

- Lease backend abstraction is a coordination layer — does NOT provide exactly-once guarantee
- NOT a distributed lock service (no Redis/etcd distributed lock)
- No Celery / Temporal / distributed worker daemon
- No automatic recovery daemon — resume is explicit via `app.resume_workflow_run()`
- Default lease backend is state_store-backed (delegates to existing WorkflowStateStore)
- Standalone memory/sqlite backends are single-process (memory) or cross-instance (sqlite) only
- Lease renewal only works while the current process is alive
- External side effect idempotency remains the business tool's responsibility
- SQLite store uses stdlib sqlite3 — no connection pooling or WAL mode
- Lease backend does NOT replace lease renewal, snapshot, compensation, or business-level idempotency

## 0.10.0 (Phase 16.3: Lease Backend Observability & Health Checks)

### Added

- **`LeaseMetrics`** — thread-safe in-process metrics collector using `threading.Lock`; tracks per-operation counters (attempts, successes, failures, exceptions, denied) for acquire/renew/release/get/list_expired; returns immutable snapshots
- **`LeaseOperationMetrics`** — dataclass for per-operation counters (attempts, successes, failures, exceptions, denied)
- **`LeaseMetricsSnapshot`** — immutable dataclass capturing full metrics state at a point in time
- **`MetricsWorkflowLeaseBackend`** — transparent wrapper around any `WorkflowLeaseBackend`; records metrics on every operation without changing return values or behavior; re-raises exceptions after recording
- **`LeaseHealthStatus`** — StrEnum: HEALTHY, DEGRADED, UNHEALTHY
- **`LeaseHealthCheckResult`** — Pydantic model (status, backend_type, details, checked_at, error); timezone-aware UTC timestamps
- **`LeaseBackendHealthChecker`** — non-destructive health checker; backend-specific checks (memory: always ok; sqlite: lightweight query with active lease count; state_store: delegation test; metrics: inner backend check; generic: non-destructive get_run_lease probe); never raises — exceptions captured in result
- **`LeaseDiagnostics`** — Pydantic model for operator visibility (backend_type, health, metrics, sample_expired_leases, checked_at)
- **`LeaseCoordinator` observability** — optional `metrics` parameter wraps backend with `MetricsWorkflowLeaseBackend`; `metrics_snapshot()` returns snapshot or None; `health_check()` delegates to `LeaseBackendHealthChecker`; `diagnostics()` assembles health + metrics + expired lease sample
- **`DagLeaseMetricsConfig`** — Pydantic config model (`enabled=False`; metrics are opt-in to avoid overhead when not needed)
- **`DagLeaseHealthConfig`** — Pydantic config model (`enabled=True`; health checks enabled by default as they are lightweight)
- **`DagLeaseConfig` extended** — new optional `metrics` and `health` fields
- **`WorkflowExecutor` lease observability** — `_build_lease_metrics()` creates collector when metrics enabled; `get_lease_health_checker()` creates checker; `get_lease_diagnostics()` assembles full diagnostic snapshot
- **Config support** — `runtime.dag_lease.metrics.enabled` and `runtime.dag_lease.health.enabled` in YAML config
- **66 new Phase 16.3 tests** — LeaseMetrics (14), MetricsWorkflowLeaseBackend (10), LeaseBackendHealthChecker (7), LeaseCoordinator metrics/health/diagnostics (12), DagLeaseMetricsConfig (5), DagLeaseHealthConfig (5), config plumbing (8), full integration (5)

### Changed

- `LeaseCoordinator.__init__()` — new optional `metrics` parameter; auto-wraps backend with `MetricsWorkflowLeaseBackend` when provided
- `LeaseCoordinator` — new methods: `metrics_snapshot()`, `health_check()`, `diagnostics(include_expired_sample, expired_sample_limit)`
- `RuntimeConfig` — `DagLeaseConfig` extended with `metrics: DagLeaseMetricsConfig | None` and `health: DagLeaseHealthConfig | None`
- `LeaseBackendHealthChecker.check()` — propagates inner check errors to top-level `error` field when status is UNHEALTHY
- `WorkflowExecutor.__init__()` — new optional `dag_lease_config` parameter; `_build_lease_metrics()`, `get_lease_health_checker()`, `get_lease_diagnostics()` helpers

### Current Limitations

- Metrics are in-process only — not exported to Prometheus/OpenTelemetry (no external dependency)
- Health checks are diagnostic only — do NOT guarantee backend availability or provide distributed recovery
- NOT a distributed health protocol or liveness probe
- Metrics are opt-in (`enabled=False` by default) to avoid overhead when not needed
- No background metrics export or collection daemon
- LeaseMetrics uses `threading.Lock` — not async-safe for cross-thread mutation
- Health checks are non-destructive but do not test lease acquire/renew operations
- Does NOT replace lease renewal, snapshot, compensation, or business-level idempotency

### Added

- **OpenAI backend handoff workflow support** — `OpenAIAgentsBackend.run_workflow()` handles handoff (triage) workflows via SDK `Agent.handoffs`
- **OpenAI backend orchestrator workflow support** — `Agent.as_tool()` for agents-as-tools with fallback wrapper
- **`compile_agent(handoffs=...)`** — explicit handoffs parameter takes priority over `agent_spec.handoffs`
- **`compile_agent_as_tool()`** — compiles specialist agents as SDK tools for orchestrator workflows
- **`AgentApp._run_workflow()` backend delegation** — OpenAIAgentsBackend multi-agent execution; DryRun path unchanged
- **WorkflowTrace for OpenAI workflows** — records handoff_candidates and agent_tools steps
- **23 new Phase 11 tests** — handoff/orchestrator compile, run, dispatch, integration, DryRun regression
- **394 total tests passing**

### Known limitations

- Handoff target extraction — actual handoff target not extracted from SDK result; trace records candidates only
- Orchestrator agent_calls — extracted from tool_calls when available; may be incomplete
- Agent-as-tool governance — specialist agents-as-tools do not go through ToolExecutor governance
- DAG workflows — not yet implemented
- Parallel orchestrator — specialists called serially

## 0.10.0 (Phase 16.4: Redis Lease Backend)

### Added

- **`RedisWorkflowLeaseBackend`** — Redis-backed `WorkflowLeaseBackend` implementation for cross-process / cross-worker lease coordination; uses atomic Lua scripts for acquire/renew/release
- **Redis Lua scripts** — `_ACQUIRE_SCRIPT`, `_RENEW_SCRIPT`, `_RELEASE_SCRIPT` for atomic compare-and-set operations; loaded via `SCRIPT LOAD` / `EVALSHA`
- **`RedisWorkflowLeaseBackend.health_check()`** — lightweight PING-based health check; sanitizes Redis URL; never raises
- **`RedisWorkflowLeaseBackend.diagnostics()`** — collects backend_type, key_prefix, TTL, allow_steal_expired, sanitized URL, and total lease key count
- **`DagLeaseConfig` extended** — new optional `redis_url`, `key_prefix` fields; validator accepts "redis" backend
- **`create_lease_backend()` extended** — supports `backend_type="redis"` with `redis_url` and `key_prefix` parameters
- **`WorkflowExecutor._build_lease_backend()` extended** — routes "redis" backend to `create_lease_backend(backend_type="redis", ...)`
- **Optional dependency** — Redis is an optional extra (`pip install -e ".[redis]"`); default install does not require redis-py
- **89 new Phase 16.4 tests** — RedisWorkflowLeaseBackend acquire (8), renew (6), release (5), get (3), list_expired (3), health (7), diagnostics (5), config (11), factory (9), protocol (4), metrics integration (2), key prefix isolation (1), repr (2), FakeRedisClient (13), helpers (8), optional dependency boundary (4)

### Current Limitations

- Redis is an optional dependency — not installed by default
- NOT a distributed lock service — best-effort coordination only
- No exactly-once guarantee — application must remain idempotent
- No worker daemon, queue, or scheduler — lease coordination only
- No Redis Streams / PubSub worker distribution
- No automatic distributed recovery or self-healing
- Redis TTL is the only expiry mechanism — clock skew between workers may cause brief double-claim windows
- Redis unavailability causes lease acquire/renew to fail
- Metrics wrapper requires Phase 16.3 metrics opt-in

## 0.10.0 (Phase 16.5: Recovery Scanner & Manual Recovery)

### Added

- **`RecoveryScanner`** — read-only scanner that inspects persisted DAG workflow runs and identifies recovery candidates (stale, failed, interrupted, lease-expired, compensation-incomplete)
- **`RecoveryCandidate` model** — run_id, status, reasons, recommendation, lease info, resumability, resume/recovery plan summaries
- **`RecoveryScanResult`** — scanned_at, total_scanned, candidate_count, candidates list, non-fatal errors
- **`RecoveryScanConfig`** — stale_after_seconds, running_after_seconds, include_completed/failed/running/compensating, limit, tenant_id, workflow_name filters
- **`ManualRecoveryResult`** — run_id, attempted, recovered, status, lease_acquired/released, result, error
- **`RecoveryService`** — lease-protected manual recovery; acquires lease before resume, releases after (success or failure)
- **Lease-protected recovery flow** — inspect → check recommendation → acquire lease → audit.started → resume → audit.completed → release lease
- **AgentApp recovery APIs** — `scan_recovery_candidates()`, `inspect_recovery_candidate()`, `recover_workflow_run()`
- **CLI recovery commands** — `agentapp recovery scan`, `agentapp recovery inspect <run_id>`, `agentapp recovery recover <run_id>`
- **`list_runs()` extended** — both InMemory and SQLite stores now accept `statuses`, `updated_before`, `workflow_name`, `limit` parameters
- **`WorkflowStateStore` recovery integration** — scanner uses `list_runs()`, `list_nodes()`, `list_compensations()`, `build_recovery_plan()`, `build_resume_plan()`
- **Redis lease compatibility** — scanner reads Redis lease via `backend.get_run_lease()`; expired Redis lease treated as recoverable
- **Recovery audit events** — recovery.scan_started, recovery.scan_completed, recovery.inspect, recovery.started, recovery.completed, recovery.failed, recovery.skipped_active_lease, recovery.skipped_not_resumable
- **63 new Phase 16.5 tests** — models (20), scanner (24), service (12), CLI (9), state store list_runs (13)

### Current Limitations

- No automatic recovery daemon or background scheduler
- No Redis Streams / Celery / Temporal integration
- No exactly-once guarantee — lease is best-effort only
- Recovery is operator-triggered only (CLI or API)
- Active lease blocks recovery — operator must wait or manually release
- No bulk/batch recovery — one run at a time
- No UI console for recovery management
- Lease release failure is logged but does not block recovery result

## Phase 17: Automatic Recovery Daemon (0.10.0)

### Added

- **`RecoveryDaemon`** — policy-driven automatic recovery with `run_once()` and `run_forever()` methods
- **`AutoRecoveryPolicy`** — Pydantic model with conservative defaults: `enabled=False`, `dry_run=True`, `max_concurrent_recoveries=1`
- **`RecoveryDaemonTickResult`** — structured result model: scanned/selected/recovered/skipped/failed counts + run IDs + skip/failure details
- **Dry-run by default** — daemon logs would-be-recovered runs but never calls `recover_run()` unless explicitly configured
- **Candidate selection rules** — only auto-recovers RESUME recommendations; skips WAIT_FOR_ACTIVE_LEASE, DO_NOT_RESUME, completed (unless enabled)
- **Policy flags** — `recover_failed`, `recover_stale_running`, `recover_compensating` for fine-grained control
- **Concurrency limiting** — `asyncio.Semaphore` for `max_concurrent_recoveries`
- **Per-scan limits** — `max_candidates_per_scan`, `max_recoveries_per_scan`
- **Audit events** — daemon_started/stopped/tick_started/completed, candidate_selected/skipped, recovery_started/completed/failed, dry_run_selected
- **`AgentApp.create_recovery_daemon()`** — programmatic daemon factory (not auto-started)
- **CLI** — `agentapp recovery daemon --once --dry-run/--no-dry-run` with graceful Ctrl+C shutdown
- **`_build_scan_config()`** — maps policy statuses to scanner include flags
- **`_should_skip()`** — selection logic based on recommendation + policy flags + reason matching
- **57 new Phase 17 tests** — policy (29), daemon (22), CLI daemon (6)
- **152 total recovery tests passing**

### Current Limitations

- Daemon is not auto-started; must be explicitly invoked
- Dry-run is the default — no recovery without `--no-dry-run`
- No exactly-once guarantee
- No distributed coordination
- No UI console

## Phase 18: Recovery Observability + Admin API (0.10.0)

## Phase 20: OpenAI Tool Interception and RunState Resume (0.10.0)

### Added

- Shared governance approval policy: `requires_approval=True` and high/critical-risk tools now pause for approval before execution.
- Approval resume service for approving, rejecting, and resuming interrupted backend runs through one runtime boundary.
- OpenAI backend SDK interruption mapping from framework approval IDs to SDK call IDs for safer fake RunState resume tests.
- Conservative sanitization for approval arguments, audit payloads, and user-facing backend errors.

### Safety

- Default tests do not require a real OpenAI API key.
- Core modules do not import the OpenAI Agents SDK.
- Dry-run defaults and recovery daemon default-off behavior are unchanged.

## Phase 22: Multi-agent Workflow Runtime v1 (0.10.0)

### Added

- **`max_handoffs` on handoff workflows** — `Workflow.handoff()` accepts `max_handoffs` (default 3); `_run_handoff()` enforces the limit and returns `MaxHandoffsExceeded` error when exceeded.
- **`max_agent_calls` on orchestrator workflows** — `Workflow.orchestrator()` accepts `max_agent_calls` (default 5); `_run_orchestrator()` caps specialist dispatch and returns `MaxAgentCallsExceeded` error when exceeded.
- **Orchestrator interruption propagation** — specialist interruptions (approval gates, rate limits) are collected and propagated to the workflow-level `AppRunResult.interruptions`; orchestrator status set to `interrupted` when specialists are interrupted.
- **Handoff depth tracking** — `_handoff_depth` in `RunContext.metadata` incremented per handoff hop; propagates to child runs.
- **`MaxHandoffsExceeded` and `MaxAgentCallsExceeded` errors** — structured error types for multi-agent limit violations.
- **36 multi-agent governance propagation tests** — 22 unit tests + 13 customer_support eval tests + 4 SDK compile/trace tests covering approval, permission, rate-limit, TTL, config loader, metadata, and execution trace.
- **customer_support multi-agent eval suite** — `customer_support_multi_agent_governance.yaml` with 12 eval cases for handoff and orchestrator governance.
- **Marker-gated SDK integration test** — `test_real_openai_agents_multi_agent.py` verifies SDK module loads and backend compiles multi-agent structures (opt-in via `RUN_OPENAI_AGENTS_INTEGRATION=1`).
- **Security: non-negative validation** — config loader coerces `max_handoffs`/`max_agent_calls` via `max(0, int(...))`; Pydantic `field_validator` rejects negative values (defense in depth against fail-open bypass).

### Changed

- `_run_orchestrator()` collects `sub_result.interruptions` from each specialist call and sets `all_interruptions` on the workflow result.
- `AppRunResult` carries `interruptions` list from specialist runs through orchestrator dispatch.

## Phase 21: Production-grade Approval Governance Enhancement (0.10.0)

### Added

- **Approval TTL/expiration** — `default_ttl_seconds` config field; `InMemoryApprovalStore` and `SQLiteApprovalStore` check expiry before approve/reject, auto-transition expired approvals to EXPIRED status, and filter expired from `list_pending()`.
- **Approval rate limiting** — `InMemoryApprovalRateLimiter` with sliding-window per (tenant, user, tool) key; `ToolExecutor` blocks rate-limited approvals with `approval_rate_limited` error and emits `approval.rate_limited` audit event.
- **Enhanced audit trail** — `approval.expired`, `approval.rate_limited`, and `run.resume_blocked` (with reason) audit events; full context (user_id, tenant_id, tool_name, risk_level).
- **Real SDK integration test** — marker-gated test (`RUN_OPENAI_AGENTS_INTEGRATION=1`) verifying SDK module loads, backend compiles without API key, and HITL flow with fake SDK injection.
- **Multi-agent metadata round-trip** — tests verifying approval metadata (argument_keys, requester_context) survives ToolExecutor→store→get→approve cycle; SQLite persistence round-trip; per-agent metadata isolation.
- **Security: metadata bypass prevention** — `RunContext.metadata` cannot bypass high/critical risk approval gates; only internal `_NATIVE_HITL_APPROVAL_TOKEN` markers are accepted.

### Changed

- `ToolExecutor` accepts `rate_limiter` and `default_ttl_seconds` parameters.
- `ApprovalConfig` gains `default_ttl_seconds` field.
- `GovernanceConfig` gains `rate_limit` (max_requests, window_seconds) field.
- `ApprovalResumeService.approve_and_resume()` checks TTL expiry before resuming.

## Phase 18: Recovery Observability + Admin API (0.10.0)

### Added

- **`RecoverySystemStatus`** — snapshot of recovery subsystem health: enabled, dry_run, daemon_configured, scanner/recovery_service availability, last tick, policy
- **`AgentApp.get_recovery_system_status()`** — returns RecoverySystemStatus for admin dashboards and CLI
- **`AgentApp.run_recovery_scan_once()`** — executes a single scan cycle (dry-run by default), returns RecoveryDaemonTickResult
- **`AgentApp.recover_run()`** — thin wrapper around `recover_workflow_run` with `dry_run=True` default; dry-run includes candidate inspection info
- **`AgentApp.get_recovery_history()`** — queries audit events for a specific run ID from the audit logger
- **`_build_scan_config_from_policy()`** — maps AutoRecoveryPolicy to RecoveryScanConfig
- **`_should_skip_candidate()`** — static skip evaluation matching RecoveryDaemon behavior
- **CLI `recovery status`** — shows recovery system configuration and policy
- **CLI `recovery history <run_id>`** — shows audit events for a run (--json, --limit)
- **CLI `recovery scan` enhanced** — now delegates to `run_recovery_scan_once()` (dry-run by default, `--no-dry-run` for live)
- **CLI `recovery recover` enhanced** — now delegates to `recover_run()` with `--dry-run` (default) / `--no-dry-run` support
- **Optional FastAPI admin router** — `agent_app/adapters/recovery_admin.py` with lazy-import FastAPI (not a hard dependency)
  - `GET /admin/recovery/status`
  - `GET /admin/recovery/runs/{run_id}/inspect`
  - `GET /admin/recovery/runs/{run_id}/history`
  - `POST /admin/recovery/scan`
  - `POST /admin/recovery/runs/{run_id}/recover`
- **43 new Phase 18 tests** — status (4), scan_once (5), recover_run (5), history (4), scan_config (6), skip_candidate (6), CLI (10)
- **152 total recovery tests passing**

## Phase 19: Recovery Admin Console (0.10.0)

### Added

- **Recovery Admin Console** — an optional server-rendered FastAPI UI router with secure-by-default admin dependency handling, dry-run candidate scans, run-scoped history views, and a two-step HMAC confirmation flow for live recovery.

## Phase 18.5: CLI Trace/Eval Test Baseline Cleanup (0.10.0)

### Fixed

- **CLI module entrypoint** — `python -m agent_app.cli` now invokes `main()` and exits via `SystemExit(main())`
- **Pre-existing CLI trace/eval test failures** — restored subprocess-based CLI tests that expect actual stdout/stderr/exit-code behavior
- **CLI exit-code behavior** — missing eval/config files and missing traces now return non-zero through the module entrypoint instead of silently exiting 0
- **CLI output behavior** — `--help`, `trace list`, `trace show`, and trace JSON output now work when invoked as `python -m agent_app.cli`
- **Recovery admin router security** — optional FastAPI recovery admin router now denies access by default unless an admin authorization dependency is supplied
- **Recovery admin error handling** — internal exception details are logged server-side and no longer returned in HTTP 500 response bodies

## Phase 23: Governance Policy Engine v1 (0.11.0)

### Added

- **`PolicyAction` StrEnum** — ALLOW, DENY, REQUIRE_APPROVAL, SET_TTL, RATE_LIMIT, AUDIT_ONLY
- **`PolicyDecision` model** — action, allowed, requires_approval, reason, ttl_seconds, rate_limit, metadata
- **`PolicyEvaluationContext` model** — run_id, tool_name, risk_level, user_id, tenant_id, roles, permissions, metadata
- **`PolicyEngine` Protocol** — `evaluate_tool_call()` + `evaluate_approval_resume()` async methods
- **`DefaultPolicyEngine`** — replicates Phase 22 behavior (missing perms → DENY, high/critical risk → REQUIRE_APPROVAL, else ALLOW)
- **`ConfigurablePolicyEngine`** — YAML-driven rule matching with 14 condition types (tool_name, tool_name_prefix, risk_level, roles, permissions, etc.)
- **Policy evaluation in `ToolExecutor`** — runs BEFORE existing governance checks; DENY/REQUIRE_APPROVAL short-circuits execution
- **Policy evaluation in `ApprovalResumeService`** — runs AFTER existing safety checks
- **Config schema support** — `PolicyRuleConfig`, `PolicyEngineConfig` in `GovernanceConfig.policies`
- **Policy audit events** — policy.evaluated, policy.denied, policy.approval_required, policy.audit_only
- **customer_support policy upgrade** — 3 rules: refund_requires_approval, billing_audit_only, deny_dangerous_tools
- **13 new unit tests** for policy engine integration in ToolExecutor
- **7 new unit tests** for policy integration in ApprovalResumeService
- **1715 total tests passing**

### Backward Compatibility

- Policy engine disabled by default (Phase 22 behavior unchanged)
- No hard dependency on OpenAI SDK in governance modules
- All existing 1715 tests pass without modification

## Phase 24: Policy Ops & Diagnostics v1 (0.12.0)

### Added

- **`PolicyValidationResult` / `PolicyValidationIssue`** — validates policy config for duplicate rules, invalid actions, conflicting conditions, type errors, TTL issues, empty rule warnings
- **`validate_policy_config()`** — standalone validation function accepting PolicyEngineConfig or raw dict
- **`PolicyDecisionTrace` model** — decision_id, rule_name, action, reason, matched_conditions, context_summary, created_at
- **`PolicyEngine.explain()` method** — returns explainable trace for any policy decision
- **`_build_context_summary()`** — safe context extraction excluding sensitive arguments
- **`PolicySimulationInput` / `PolicySimulationResult`** — structured I/O for offline simulation
- **`PolicySimulator`** — evaluates policy without side effects (no tool execution, no approval creation, no session writes)
- **CLI `agentapp policy validate`** — validates config, reports errors/warnings, exit 0 for valid
- **CLI `agentapp policy simulate`** — offline simulation with action/rule/reason output
- **CLI `agentapp policy explain`** — detailed decision trace with matched conditions
- **FastAPI `/policies` GET** — returns policy config summary (no sensitive data)
- **FastAPI `/policies/validate` POST** — validates current policy config
- **FastAPI `/policies/simulate` POST** — offline simulation endpoint
- **FastAPI `/policies/explain` POST** — explain endpoint with matched conditions
- **FastAPI `/policy-decisions` GET** — queries audit log for policy events
- **Eval `policy_decisions` assertion** — matches rule_name, action, reason_contains in eval YAML
- **customer_support policy eval cases** — refund_with_role_requires_approval, refund_without_role_denied_by_policy, policy_audit_only_allows_execution
- **customer_support `policy_examples.md`** — usage examples for validate/simulate/explain

### Changed

- `PolicyEngine` Protocol — added `explain()` method
- `DefaultPolicyEngine` — added `explain()` implementation
- `ConfigurablePolicyEngine` — added `explain()` implementation with matched conditions
- Eval schema — `EvalExpect.policy_decisions` field added
- Eval assertions — `_assert_policy_decisions()` function added
- ToolExecutor audit — policy.evaluated event emitted for all evaluations
- ApprovalResumeService audit — policy.evaluated event emitted for all evaluations

### Test Coverage

- 22 policy validation tests (valid, duplicate names, invalid actions, conflicting conditions, TTL, warnings)
- 12 policy explain/trace tests (default engine, configurable engine, context safety)
- 15 policy simulator tests (allow, deny, require_approval, audit_only, missing role/permission, no side effects)
- 7 CLI policy tests (validate success/failure, simulate output, explain output)
- 11 FastAPI policy endpoint tests (all 5 endpoints)
- 8 eval policy assertion tests (match by rule, action, reason, multiple checks)
- 4 customer_support policy eval cases

## 0.7.0

### Added

- **OpenAI native HITL mode** — uses SDK `needs_approval` and `RunState` for real pause/resume
- **`RunState.to_json()` / `from_json()`** — SDK-native RunState serialization into framework RunStateStore
- **`RunState.approve()` / `reject()`** — native SDK approval resolution integrated with framework resume
- **`OpenAIAgentsBackend.resume()`** — real OpenAI RunState resume using stored `backend_state`
- **`InterruptedRun.backend_state`** — stores serialized SDK RunState for native resume
- **ApprovalRequest mapping** — SDK `ToolApprovalItem` mapped to framework approval dicts
- **`hitl_mode` config** — `wrapper` (default) or `native` in `runtime.openai.hitl_mode`
- **24 new Phase 10 tests** — native HITL, RunState serialization, resume, streaming, integration
- **371 total tests passing**

### Changed

- `AppRunResult.backend_state` — new field for backend-specific state (e.g. OpenAI RunState JSON)
- `AgentBackend` protocol — added optional `resume()` method
- `DryRunBackend` — implements `resume()` stub
- `AppRunner._save_interrupted_run()` — saves `backend_state` to `InterruptedRun`
- `AgentApp.resume()` — dispatches to `backend.resume()` for OpenAI native mode

### Known limitations

- Native HITL requires SDK version with `needs_approval` / `RunState` support
- Streaming resume is minimal support (state captured after stream completes)
- Multi-agent OpenAI backend deep integration deferred to future phases

## 0.6.0

### Added

- **RunStateStore protocol** — framework-level persistence abstraction for interrupted runs
- **InMemoryRunStateStore** — in-memory implementation for development/testing
- **SQLiteRunStateStore** — SQLite-backed implementation for production persistence
- **InterruptedRun model** — captures full run state (context, interruptions, approval IDs, backend state)
- **RunStateStatus enum** — RUNNING, INTERRUPTED, COMPLETED, FAILED, RESUMED
- **Framework-level resume** — AgentApp.resume() reads from RunStateStore, checks approval status
- **AppRunner integration** — automatically saves InterruptedRun when backend returns status=interrupted
- **Audit events** — run.interrupted and run.resumed audit events
- **FastAPI run state endpoints** — GET /runs/interrupted, GET /runs/{run_id}/state, POST /runs/{run_id}/resume
- **Config support** — runtime.run_state.type/path in YAML config
- **40+ new tests** — models, stores, AppRunner integration, resume, config, FastAPI
- **337 total tests passing**

### Known limitations

- Real OpenAI RunState pause/resume not implemented (framework-level only)
- DryRunBackend resume returns stub result, not actual re-execution
- backend_state field reserved for future OpenAI RunState payload
- No automatic retry after resume

## 0.5.0

### Added

- **Governance-aware OpenAI function tool wrapper** — `_create_governed_tool_wrapper()` wraps SDK function tools with ToolExecutor pipeline
- **OpenAI backend ToolExecutor integration** — real SDK tool calls route through permissions, approval, and audit
- **Approval-required tool output** — high-risk tools return structured `approval_required` response to the model, recorded in `AppRunResult.interruptions`
- **Permission-denied tool output** — unauthorized tool calls return structured error response
- **Audit logging** — OpenAI backend tool executions recorded with correct run_id/tenant_id
- **Context binding** — `compile_agent()` and `compile_tool()` accept `RunContext` for per-run governance
- **Interruption detection** — `_extract_governance_interruptions()` scans SDK results for approval_required markers
- **Config loader governance injection** — `build_app()` injects approval_store, audit_logger, permission_checker into OpenAI backend
- **25+ new governance tests** — coverage for governance wrapper, context binding, interruption detection, config loader
- **63 total OpenAI backend tests** — 38 Phase 7 + 25 Phase 8

### Known limitations

- Real OpenAI RunState pause/resume is not implemented; approval_required returned as tool output
- Deep HITL native integration deferred to future phases
- Multi-agent handoff/orchestrator with OpenAI backend not yet deeply integrated
- DryRunBackend remains recommended for eval and governance regression testing

## 0.4.0

### Added

- **OpenAIAgentsBackend**: Real OpenAI Agents SDK execution backend
- **compile_agent()**: Compile `AgentSpec` → `agents.Agent` with tool resolution from ToolRegistry
- **compile_tool()**: Compile framework tools → SDK `function_tool`
- **Backend protocol conformance**: `OpenAIAgentsBackend` satisfies `AgentBackend` runtime_checkable protocol
- **Config backend selection**: `runtime.backend` supports `"dry_run"` (default) and `"openai"`
- **Lazy SDK loading**: `_load_agents_sdk()` imports SDK only when needed; clear RuntimeError if missing
- **Output extraction**: Handles `final_output`, `output`, `content`, `str(result)` from SDK results
- **Tool call extraction**: Extracts tool_calls from SDK RunResult with fallback attribute names
- **Streaming support**: `stream()` delegates to `Runner.run_streamed` with fallback to `run()`
- **openai_basic example**: Single-agent example with math tool and OpenAI backend
- **40+ new tests**: Missing dependency, compile_agent/tool, run/stream, config loader, protocol conformance

### Known limitations

- Framework governance pipeline (permissions, approval, audit) does not intercept real SDK tool execution
- Real OpenAI RunState resume is not implemented
- Multi-agent handoff/orchestrator with OpenAI backend not yet deeply integrated
- DryRunBackend is still the recommended backend for eval and governance regression testing
- DAG workflows not implemented

## 0.3.0

### Added

- **RoutingPolicy**: Declarative YAML-based routing rules for handoff and orchestrator workflows
- **RoutingRule / RoutingPolicy models**: Keyword, regex, and default match types with priority ordering
- **RoutingPolicyExecutor**: `route_one()` for handoff, `route_many()` for orchestrator
- **WorkflowTrace / WorkflowStep**: Structured execution observability recorded in `AppRunResult.workflow_trace`
- **Backward compatibility**: Heuristic fallback when no routing policy is configured
- **Eval assertions**: `routing_decisions` and `workflow_steps` assertion support
- **customer_support example upgraded**: Configurable routing policy with 4 rules (refund, billing, technical, default)
- **research_assistant example upgraded**: Configurable routing policy with 3 specialist rules
- **25 new tests**: Routing models, executor, config loader, workflow trace, eval assertions, backward compat

### Known limitations

- OpenAI backend integration is minimal; real RunState resume is not implemented
- DryRunBackend tool matching uses keyword heuristics, not real LLM reasoning
- DAG workflows are stubs only
- Eval runner validates framework governance logic, not model quality
- SQLite stores are basic; no connection pooling or migration system

## 0.2.0

### Added

- **Workflow.handoff**: Multi-agent handoff (triage) workflow with keyword-based routing
- **Workflow.orchestrator**: Multi-agent orchestrator workflow with specialist delegation
- **WorkflowExecutor**: Dedicated executor dispatching by `WorkflowType`
- **AppRunResult.agent_calls**: New field recording specialist agent invocations
- **Handoff routing**: Keyword-based intent detection (refund, billing, technical_support)
- **Orchestrator routing**: Keyword-based specialist selection (researcher, analyst, writer)
- **Eval assertions**: `handoffs` and `agent_calls` assertion support
- **customer_support example upgraded**: Multi-agent handoff with triage → refund/billing/technical_support
- **research_assistant example**: New orchestrator example with manager/researcher/analyst/writer
- **Config loader**: Support for `type: handoff` and `type: orchestrator` workflow configs
- **26 new tests**: Workflow model, routing, executor integration, eval assertions

### Known limitations

- OpenAI backend integration is minimal; real RunState resume is not implemented
- DryRunBackend tool matching uses keyword heuristics, not real LLM reasoning
- DAG workflows are stubs only
- Eval runner validates framework governance logic, not model quality
- SQLite stores are basic; no connection pooling or migration system

## 0.1.0

### Added

- **Core module**: `AgentSpec`, `ToolSpec`, `Workflow`, `RunContext`, `AppRunResult`
- **Registry system**: `AgentRegistry`, `ToolRegistry`, `WorkflowRegistry`, `PolicyRegistry`
- **Tool decorator**: `@tool()` with auto-registration into global default registry
- **Config loader**: YAML-based `agentapp.yaml` with `load_config()` and `build_app()`
- **DryRunBackend**: Default no-op backend for testing without real API calls
- **Session stores**: `InMemorySessionStore`, `SQLiteSessionStore` with factory
- **Streaming events**: `StreamEventType` (7 types), `StreamEvent`, `stream_events()` helper
- **FastAPI adapter**: `create_fastapi_app()` with `/health`, `/agents`, `/tools`, `/workflows`,
  `/runs`, `/runs/stream`, `/approvals`, `/approvals/{id}/approve`, `/approvals/{id}/reject`,
  `/runs/{run_id}/resume` endpoints
- **Tool governance**: `ToolExecutor` with permission check → approval gate → execute → audit
- **Permission checker**: `DefaultPermissionChecker` with role-based matching
- **Approval store**: `InMemoryApprovalStore`, `SQLiteApprovalStore` (CRUD, tenant filtering)
- **Audit logger**: `InMemoryAuditLogger`, `SQLiteAuditLogger` (multi-dimensional filtering)
- **Eval runner**: YAML-defined suites with assertions for status, output, tools, approvals,
  error types, and approve-and-resume flows
- **CLI**: `agentapp eval run <suite> --config <config>` command
- **Customer support example**: Complete working example with order.query and refund.request tools,
  SQLite session, evals, FastAPI entry point

### Known limitations

- OpenAI backend integration is minimal; real RunState resume is not implemented
- DryRunBackend tool matching uses keyword heuristics, not real LLM reasoning
- Handoff and orchestrator workflow types are stubs only
- Eval runner validates framework governance logic, not model quality
- SQLite stores are basic; no connection pooling or migration system

## Phase 25: Policy Decision Store & Ops Reporting v1 (0.13.0)

### Added

- **`PolicyDecisionStore` Protocol** — structural subtyping with `record`/`get`/`query`/`count` methods
- **`InMemoryPolicyDecisionStore`** — list-based store with filtering, sorted newest-first, limit/offset pagination
- **`SQLitePolicyDecisionStore`** — persistent SQLite store with 5 indexes (run_id, tenant_id, rule_name, action, created_at)
- **`PolicyDecisionTrace` model** — added `tool_name` field for tool-level analytics
- **`PolicyReport` model** — aggregated statistics (action/rule/tool breakdown + time range)
- **`PolicyReportingService`** — generate reports, export JSONL and CSV
- **Config integration** — `PolicyDecisionStoreConfig` in `GovernanceConfig.policy_decisions` (type + path)
- **ToolExecutor wiring** — records `PolicyDecisionTrace` after every policy evaluation via `explain()`
- **AppRunner/AgentApp wiring** — `policy_decision_store` parameter threaded through all layers
- **Enhanced `/policy-decisions` endpoint** — full filtering (run_id, tenant_id, agent_name, tool_name, rule_name, action) + pagination
- **New `/policy-decisions/{decision_id}` endpoint** — single decision lookup
- **New `/policy-report` endpoint** — aggregated policy analytics
- **CLI commands** — `policy decisions`, `policy report`, `policy export` (JSONL/CSV)
- **customer_support example upgrade** — SQLite policy decision store configured
- **32 new unit tests** for PolicyDecisionStore + PolicyReportingService

### Architecture Boundaries Maintained

- Core modules have no FastAPI dependency
- Core modules have no openai-agents dependency
- SQLite via stdlib `sqlite3` only (no ORM)

## Phase 26: Policy Console Lite v1 (0.14.0)

### Added

- **`PolicyConsoleConfig`** — `enabled`, `base_path`, `title`, `page_size` fields in `GovernanceConfig`
- **Policy Console HTML pages** — Dashboard, Decisions List, Decision Detail, Report
- **Jinja2 templates** — `base.html` + 4 page templates in `agent_app/console/templates/`
- **CSS styling** — `console.css` with responsive layout, badges, pagination
- **FastAPI integration** — console router conditionally mounted in `create_fastapi_app()`
- **Static file serving** — CSS/JS served from `/policy-console/static/`
- **Jinja2 optional** — graceful error page when jinja2 not installed
- **customer_support example** — policy_console config added
- **11 new unit tests** for PolicyConsoleConfig + router registration

## Phase 27: Policy Replay & Regression Dashboard (0.15.0)

### Added

- **`PolicyReplayRunner`** — re-evaluates historical decisions against current policy engine
- **Replay models** — `PolicyReplayStatus`, `PolicyReplayDecisionChange`, `PolicyReplayRun`, `PolicyReplayResult`
- **`InMemoryPolicyReplayStore`** — in-memory persistence for replay results (save/get/list)
- **Context reconstruction** — rebuilds `PolicyEvaluationContext` from `PolicyDecisionTrace.context_summary`
- **Failed replay handling** — decisions missing `tool_name` are marked failed with clear reason
- **CLI command** — `agentapp policy replay` with filters (--tenant-id, --tool-name, --rule-id, --limit, --json)
- **Console pages** — Replay Index (`/policy-console/replays`) and Replay Detail (`/policy-console/replays/{id}`)
- **Console nav** — Replays link added to base layout
- **`docs/policy_replay.md`** — full documentation with limitations and security notes

### New Files

- `agent_app/governance/policy_replay.py` — models + runner
- `agent_app/runtime/policy_replay_store.py` — store protocol + in-memory impl
- `tests/unit/test_policy_replay.py` — 12 tests
- `tests/unit/test_policy_replay_store.py` — 6 tests
- `tests/unit/test_policy_replay_cli.py` — 4 tests
- `tests/unit/test_policy_replay_console.py` — 6 tests
- `docs/policy_replay.md` — documentation

### Modified Files

- `agent_app/cli.py` — added `policy replay` subcommand
- `agent_app/console/router.py` — added replay routes + templates
- `agent_app/console/templates/replay_index.html` — new
- `agent_app/console/templates/replay_detail.html` — new
- `agent_app/console/templates/base.html` — added Replays nav link
- `agent_app/adapters/fastapi.py` — passes replay_store to console router

### Architecture Boundaries Maintained

- Core modules have no FastAPI dependency
- Core modules have no Jinja2 dependency
- Replay logic lives in governance/runtime modules, not templates
- Console remains disabled by default
- No duplicate policy reporting/query logic in console layer

## Phase 28: Persistent Policy Replay, Background Jobs, Context Reconstruction (0.16.0)

### Added

- **`SQLitePolicyReplayStore`** — persistent replay result storage with `policy_replay_runs` and `policy_replay_changes` tables
- **`SQLitePolicyReplayJobStore`** — persistent job storage with QUEUED/RUNNING/COMPLETED/FAILED/CANCELLED lifecycle
- **`PolicyReplayJob`** — background job model with metadata, filters, and error tracking
- **`PolicyReplayJobStore`** protocol — `create()`, `get()`, `update()`, `list()` async interface
- **`PolicyReplayContextBuilder`** — enhanced context reconstruction with missing field tracking
- **`PolicyReplayContext`** — model with `missing_fields` list for transparent replay quality reporting
- **`PolicyReplayBackgroundRunner`** — lightweight submit/run_job execution without external task queues
- **`create_replay_store()` factory** — unified factory for memory or sqlite store types
- **`create_replay_job_store()` factory** — unified factory for memory or sqlite job stores
- **CLI extensions** — `--background`, `--store`, `--db-path`, `--requested-by`, `run-job`, `jobs` subcommands
- **Console pages** — Replay Jobs Index (`/policy-console/replay-jobs`) and Job Detail (`/policy-console/replay-jobs/{id}`)
- **Console nav** — Replay Jobs link added to base layout
- **`context_metadata`** on decision changes — tracks context reconstruction quality per decision
- **Missing context handling** — required fields missing → replay fails; optional fields → tracked and continue
- **Cross-process persistence** — SQLite stores survive CLI process boundaries for background jobs
- **`docs/policy_replay.md`** — updated with Phase 28 features, limitations, and architecture

### New Files

- `agent_app/governance/policy_replay_context.py` — context builder + replay context model (12 tests)
- `agent_app/runtime/policy_replay_jobs.py` — job model + store protocol + memory + sqlite impl (20 tests)
- `agent_app/runtime/policy_replay_background.py` — background runner (8 tests)
- `tests/unit/test_sqlite_policy_replay_store.py` — 13 tests for SQLite replay store
- `tests/unit/test_policy_replay_context.py` — 12 tests for context builder
- `tests/unit/test_policy_replay_jobs.py` — 20 tests for job stores + background runner
- `tests/unit/test_policy_replay_console_jobs.py` — 11 tests for console job pages
- `tests/unit/test_policy_replay_cli_jobs.py` — CLI tests for background/jobs subcommands

### Modified Files

- `agent_app/governance/policy_replay.py` — extended `PolicyReplayDecisionChange` with `context_metadata`; `PolicyReplayRunner` accepts `context_builder`
- `agent_app/runtime/policy_replay_store.py` — extended `PolicyReplayStore` protocol; added `SQLitePolicyReplayStore` + `create_replay_store()` factory
- `agent_app/cli.py` — added `--background`, `--store`, `--db-path`, `--requested-by`; new `run-job` and `jobs` subcommands
- `agent_app/console/router.py` — added replay job routes + templates
- `agent_app/console/templates/replay_jobs.html` — new
- `agent_app/console/templates/replay_job_detail.html` — new
- `agent_app/console/templates/base.html` — added Replay Jobs nav link
- `agent_app/adapters/fastapi.py` — passes job store to console router
- `docs/policy_replay.md` — Phase 28 documentation added

### Architecture Boundaries Maintained

- Core modules (`policy_replay_context`, `policy_replay_jobs`, `policy_replay_background`) have no FastAPI/Jinja2 imports
- Console templates only mount when console is enabled
- Background runner is a plain async class — no framework coupling
- Job and replay stores are independent protocols — no shared state leakage

## Phase 29: Policy Release Gates & Versioned Policy Bundles (0.17.0)

### Added

- **`PolicyBundle`** — versioned policy config with `pb_` prefix IDs, SHA-256 config hashing, lifecycle status
- **`PolicyBundleStatus`** — `draft`, `active`, `archived`, `rolled_back` lifecycle enum
- **`compute_config_hash()`** — stable SHA-256 hash of JSON-canonicalized config content
- **`PolicyBundleStore`** protocol — `create()`, `get()`, `list()`, `get_active()`, `activate()`, `archive()` async interface
- **`InMemoryPolicyBundleStore`** — in-memory bundle store for testing
- **`SQLitePolicyBundleStore`** — persistent bundle storage; activate archives previous ACTIVE bundle
- **`create_bundle_store()` factory** — unified factory for memory or sqlite store types
- **`PolicyGateRule`** — configurable thresholds: `max_changed_decisions`, `max_changed_ratio`, `max_failed_replays`, `max_new_denies`, `fail_on_missing_required_context`
- **`PolicyGateStatus`** — `passed`, `warning`, `failed` evaluation outcomes
- **`PolicyGateResult`** — evaluation outcome with per-rule results, counts, changed ratio
- **`PolicyGateEvaluator`** — evaluates replay results against rules, produces per-rule pass/fail
- **`PolicyGateStore`** protocol — `save()`, `get()`, `list(bundle_id=?)` async interface
- **`InMemoryPolicyGateStore`** — in-memory gate result store
- **`SQLitePolicyGateStore`** — persistent gate result storage
- **`create_gate_store()` factory** — unified factory for memory or sqlite store types
- **`PolicyReleaseService`** — orchestrates `create_bundle()`, `run_gate()`, `promote()`, `rollback()`
- **Bundle lifecycle management** — DRAFT → ACTIVE → ARCHIVED → ROLLED_BACK with automatic archival on promote
- **Gate-before-promote** — promotion requires passing gate by default; raises ValueError if latest gate failed
- **Config schema** — `PolicyGateRuleConfig`, `PolicyReleaseStoreConfig`, `PolicyReleaseConfig` in governance section
- **CLI commands** — `bundle create/list/active/promote/rollback` and `gate run/list` subcommands
- **Console pages** — Bundles list/detail and Gates list/detail pages with nav links
- **`docs/policy_release.md`** — Phase 29 documentation added

### New Files

- `agent_app/governance/policy_bundle.py` — PolicyBundle model, hash helper, stores + factory (30 tests)
- `agent_app/governance/policy_gate.py` — PolicyGateRule, PolicyGateResult, PolicyGateEvaluator (15 tests)
- `agent_app/runtime/policy_gate_store.py` — PolicyGateStore protocol + InMemory + SQLite + factory (15 tests)
- `agent_app/runtime/policy_release.py` — PolicyReleaseService orchestrator (11 tests)
- `agent_app/console/templates/bundles.html` — bundles list page
- `agent_app/console/templates/bundle_detail.html` — bundle detail page
- `agent_app/console/templates/gates.html` — gate results list page
- `agent_app/console/templates/gate_detail.html` — gate result detail page
- `tests/unit/test_policy_bundle_store.py` — 30 tests for bundle stores
- `tests/unit/test_policy_gate.py` — 15 tests for gate models and evaluator
- `tests/unit/test_policy_gate_store.py` — 15 tests for gate stores
- `tests/unit/test_policy_release.py` — 11 tests for release service
- `tests/unit/test_policy_release_cli.py` — 8 CLI integration tests
- `tests/unit/test_policy_release_console.py` — 12 console page tests

### Modified Files

- `agent_app/config/schema.py` — added `PolicyGateRuleConfig`, `PolicyReleaseStoreConfig`, `PolicyReleaseConfig` to `GovernanceConfig`
- `agent_app/config/loader.py` — extracts `release_config` from governance; stores on `app._release_config`
- `agent_app/cli.py` — added bundle/gate subcommands with lazy service initialization
- `agent_app/console/router.py` — added bundle/gate routes + data helpers
- `agent_app/console/templates/base.html` — added Bundles and Gates nav links
- `agent_app/adapters/fastapi.py` — passes bundle_store and gate_store to console router

### Architecture Boundaries Maintained

- Core modules (`policy_bundle`, `policy_gate`, `policy_gate_store`, `policy_release`) have no FastAPI/Jinja2 imports
- Console templates only mount when console is enabled
- Release service uses store protocols — no direct SQLite coupling in business logic
- CLI uses lazy service initialization to avoid import cycles
