# Phase 39: Policy Observability, Analytics, and Compliance Reporting — Implementation Plan

**Goal:** Make the unified governance model visible through analytics, reports, exports, and dashboards using existing audit events and stores.

**Architecture:** `PolicyObservabilityService` aggregates `AuditEvent` data (from `InMemoryAuditLogger`) and rollout approval data (from approval stores) into `PolicyObservabilityReport` models. Export helpers serialize to JSON/CSV. CLI and console provide human and machine interfaces. All new modules live in governance/ and runtime/ layers — no FastAPI/OpenAI imports.

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `agent_app/governance/policy_observability.py` | Summary models + PolicyObservabilityReport |
| `agent_app/runtime/policy_observability_service.py` | Aggregation service from audit events + stores |
| `agent_app/runtime/policy_compliance_export.py` | JSON and CSV export helpers |
| `tests/unit/test_policy_observability.py` | Models, service, export, normalizer tests |
| `tests/unit/test_policy_observability_console.py` | Console dashboard tests |

### Modified Files
| File | Changes |
|------|---------|
| `agent_app/governance/policy_rbac.py` | Add OBSERVABILITY_VIEW, OBSERVABILITY_EXPORT permissions |
| `agent_app/governance/policy_change_event.py` | Add observability event types |
| `agent_app/config/schema.py` | Add PolicyObservabilityConfig |
| `agent_app/config/loader.py` | Wire PolicyObservabilityService |
| `agent_app/cli.py` | Add observability report/export commands |
| `agent_app/console/router.py` | Add observability dashboard routes |
| `agent_app/console/templates/policy_observability.html` | Dashboard template |
| `agent_app/console/templates/policy_observability_report.html` | Report template |
| `docs/policy_release.md` | Phase 39 section |
| `CHANGELOG.md` | v0.27.0 entry |
| `README.md` | Phase 39 roadmap |

---

### Task 1: Observability Models

**Files:** `agent_app/governance/policy_observability.py`, `tests/unit/test_policy_observability.py`

- [ ] Create models: PolicyDecisionCount, PolicyActionSummary, PolicyActorSummary, PolicyToolSummary, ApprovalLatencySummary, PolicyObservabilityReport (report_id with por_ prefix, timezone-aware datetimes, JSON-serializable)
- [ ] Write tests for model validation, prefix, defaults, serialization
- [ ] Run tests (GREEN), commit

### Task 2: Observability Service + Normalizer

**Files:** `agent_app/runtime/policy_observability_service.py`, `tests/unit/test_policy_observability.py` (extend)

- [ ] Implement PolicyObservabilityService with generate_report(), summarize_enforcement_decisions(), summarize_actors(), summarize_tools(), approval_latency_summary()
- [ ] Parse audit events with event_type matching policy.runtime.enforcement.{allowed,denied,approval_required}
- [ ] Extract action_type, user_id, tool_name from AuditEvent.data dict
- [ ] Window filtering on created_at
- [ ] Missing stores → partial report
- [ ] Write tests: empty sources, allowed/denied/approval_required counting, action/actor/tool summaries, window filters, partial report
- [ ] Run tests (GREEN), commit

### Task 3: Compliance Export

**Files:** `agent_app/runtime/policy_compliance_export.py`, `tests/unit/test_policy_observability.py` (extend)

- [ ] Implement report_to_json() and report_to_csv_rows()
- [ ] CSV rows include action/actor/tool summaries
- [ ] Write tests for JSON and CSV export
- [ ] Run tests (GREEN), commit

### Task 4: Config, Loader, RBAC, Events

**Files:** `agent_app/config/schema.py`, `agent_app/config/loader.py`, `agent_app/governance/policy_rbac.py`, `agent_app/governance/policy_change_event.py`, `tests/unit/test_policy_observability.py` (extend)

- [ ] Add PolicyObservabilityConfig (enabled: bool) to schema
- [ ] Add OBSERVABILITY_VIEW (default-allowed), OBSERVABILITY_EXPORT to RBAC
- [ ] Add observability event types to PolicyChangeEventType
- [ ] Wire service in loader
- [ ] Write config tests
- [ ] Run tests (GREEN), commit

### Task 5: CLI Commands

**Files:** `agent_app/cli.py`, `tests/unit/test_policy_observability.py` (extend)

- [ ] Add `policy observability report` with --since, --until, --json
- [ ] Add `policy observability export` with --format, --output
- [ ] Invalid datetime → non-zero exit; unsupported format → non-zero exit
- [ ] Write CLI tests
- [ ] Run tests (GREEN), commit

### Task 6: Console Dashboard

**Files:** `agent_app/console/router.py`, templates, `tests/unit/test_policy_observability_console.py`

- [ ] Add GET /observability and GET/POST /observability/report routes
- [ ] Create templates with summary cards, tables, filters
- [ ] Wire service in fastapi.py adapter
- [ ] Write console tests
- [ ] Run tests (GREEN), commit

### Task 7: Documentation

**Files:** docs/policy_release.md, CHANGELOG.md, README.md, docs/release_checklist_phase39.md

- [ ] Phase 39 section in policy_release.md
- [ ] v0.27.0 in CHANGELOG
- [ ] Phase 39 in README roadmap
- [ ] Create release checklist
- [ ] Commit
