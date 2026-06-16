"""Tests for Phase 40 export, config, RBAC, and event wiring."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


class TestSimulationExport:
    def test_simulation_json_export(self):
        from agent_app.governance.policy_simulation import (
            PolicySimulationOutcome,
            PolicySimulationReport,
            PolicySimulationResult,
            PolicySimulationSummary,
        )
        from agent_app.runtime.policy_compliance_export import simulation_report_to_json

        report = PolicySimulationReport(
            simulation_id="psim_export1",
            generated_at=datetime.now(timezone.utc),
            summary=PolicySimulationSummary(total=1, would_deny=1),
            results=[
                PolicySimulationResult(
                    case_id="psc_1",
                    baseline_status="allowed",
                    candidate_status="denied",
                    outcome=PolicySimulationOutcome.WOULD_DENY,
                ),
            ],
        )
        json_str = simulation_report_to_json(report)
        assert "psim_export1" in json_str
        assert "would_deny" in json_str

    def test_simulation_csv_rows(self):
        from agent_app.governance.policy_simulation import (
            PolicySimulationOutcome,
            PolicySimulationReport,
            PolicySimulationResult,
            PolicySimulationSummary,
        )
        from agent_app.runtime.policy_compliance_export import simulation_report_to_csv_rows

        report = PolicySimulationReport(
            simulation_id="psim_csv1",
            generated_at=datetime.now(timezone.utc),
            summary=PolicySimulationSummary(total=2, unchanged=1, would_deny=1),
            results=[
                PolicySimulationResult(
                    case_id="psc_a",
                    baseline_status="allowed",
                    candidate_status="allowed",
                    outcome=PolicySimulationOutcome.UNCHANGED,
                ),
                PolicySimulationResult(
                    case_id="psc_b",
                    baseline_status="allowed",
                    candidate_status="denied",
                    outcome=PolicySimulationOutcome.WOULD_DENY,
                ),
            ],
        )
        rows = simulation_report_to_csv_rows(report)
        assert len(rows) == 2

    def test_validation_json_export(self):
        from agent_app.runtime.policy_validation import (
            PolicyValidationIssue,
            PolicyValidationReport,
            PolicyValidationSeverity,
        )
        from agent_app.runtime.policy_compliance_export import validation_report_to_json

        report = PolicyValidationReport(
            valid=False,
            issues=[
                PolicyValidationIssue(
                    severity=PolicyValidationSeverity.WARNING,
                    code="broad_rule",
                    message="Broad rule",
                    rule_id="rpr_1",
                ),
            ],
        )
        json_str = validation_report_to_json(report)
        assert "broad_rule" in json_str


class TestPolicySimulationConfig:
    def test_config_defaults(self):
        from agent_app.config.schema import PolicySimulationConfig
        config = PolicySimulationConfig()
        assert config.enabled is False

    def test_config_enabled(self):
        from agent_app.config.schema import PolicySimulationConfig
        config = PolicySimulationConfig(enabled=True)
        assert config.enabled is True


class TestSimulationRBAC:
    def test_simulation_permissions_exist(self):
        from agent_app.governance.policy_rbac import PolicyReleasePermission
        assert PolicyReleasePermission.SIMULATION_RUN == "policy.simulation.run"
        assert PolicyReleasePermission.SIMULATION_VIEW == "policy.simulation.view"
        assert PolicyReleasePermission.SIMULATION_EXPORT == "policy.simulation.export"

    def test_simulation_view_default_allowed(self):
        from agent_app.governance.policy_rbac import PolicyReleasePermission, _DEFAULT_ALLOWED
        assert PolicyReleasePermission.SIMULATION_VIEW in _DEFAULT_ALLOWED


class TestSimulationEvents:
    def test_simulation_event_types(self):
        from agent_app.governance.policy_change_event import PolicyChangeEventType
        assert PolicyChangeEventType.SIMULATION_VALIDATION_RUN == "policy.simulation.validation_run"
        assert PolicyChangeEventType.SIMULATION_REPLAY_RUN == "policy.simulation.replay_run"
        assert PolicyChangeEventType.SIMULATION_EXPORT_GENERATED == "policy.simulation.export_generated"
        assert PolicyChangeEventType.SIMULATION_PERMISSION_DENIED == "policy.simulation.permission_denied"
