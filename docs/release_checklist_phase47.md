# Phase 47 Release Checklist — Policy Rollout Federation Observability and Reporting

## Feature summary

Phase 47 adds federation-focused observability: federation history events, timeline reconstruction, analytics reports, target health summaries, wave outcome summaries, conflict summaries, CLI commands, console pages, and export helpers.

## Verification

- [ ] Federation history model tests pass
- [ ] Federation history store tests pass
- [ ] Federation history recorder tests pass
- [ ] Federation observability service tests pass
- [ ] Federation service integration tests pass
- [ ] Export helper tests pass
- [ ] Config/loader/RBAC/events tests pass
- [ ] Federation history CLI tests pass
- [ ] Federation history console tests pass or skip when optional dependencies unavailable
- [ ] Existing Phase 46 federation tests pass
- [ ] Existing rollout history tests pass
- [ ] Full policy regression test subset passes

## New files

- `agent_app/governance/policy_rollout_federation_history.py`
- `agent_app/runtime/policy_rollout_federation_history_store.py`
- `agent_app/runtime/policy_rollout_federation_history_recorder.py`
- `agent_app/runtime/policy_rollout_federation_observability_service.py`
- `agent_app/console/templates/policy_federation_history.html`
- `agent_app/console/templates/policy_federation_timeline.html`
- `agent_app/console/templates/policy_federation_analytics.html`
- `tests/unit/test_policy_rollout_federation_history_model.py`
- `tests/unit/test_policy_rollout_federation_history_store.py`
- `tests/unit/test_policy_rollout_federation_history_recorder.py`
- `tests/unit/test_policy_rollout_federation_observability_service.py`
- `tests/unit/test_policy_rollout_federation_history_config.py`
- `tests/unit/test_policy_rollout_federation_history_cli.py`
- `tests/unit/test_policy_rollout_federation_history_console.py`
- `docs/release_checklist_phase47.md`

## Modified files

- `agent_app/runtime/policy_rollout_federation_service.py`
- `agent_app/runtime/policy_notification_service.py`
- `agent_app/runtime/policy_compliance_export.py`
- `agent_app/governance/policy_rbac.py`
- `agent_app/governance/policy_change_event.py`
- `agent_app/config/schema.py`
- `agent_app/config/loader.py`
- `agent_app/core/app.py`
- `agent_app/cli.py`
- `agent_app/console/router.py`
- `agent_app/adapters/fastapi.py`
- `docs/policy_release.md`
- `CHANGELOG.md`
- `README.md`

## Known limitations

- Federation observability is framework-level, not distributed tracing
- Analytics depend on recorder/event coverage
- No external BI integration
- No charts beyond console tables
- No OpenTelemetry exporter
- No persisted scheduled reports
- Existing old federations may have partial history if recorder was disabled
- Parallel strategy remains logical/deterministic, not concurrent execution

## Phase 48 recommendation

Phase 48 should add policy rollout federation approval workflows: multi-tenant approval delegation, cross-environment approval gates, approval escalation policies, and federation-level approval dashboards.
