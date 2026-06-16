"""Tests for SimulationGateInput and simulation_gate_metrics.

Phase 41 Task 1: Simulation gate input model and metrics extraction.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_simulation import (
    PolicySimulationReport,
    PolicySimulationResult,
    PolicySimulationSummary,
)
from agent_app.governance.policy_simulation_gate import (
    SimulationGateInput,
    simulation_gate_metrics,
)
from agent_app.runtime.policy_validation import (
    PolicyValidationIssue,
    PolicyValidationReport,
    PolicyValidationSeverity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_simulation_report(
    total: int = 10,
    unchanged: int = 5,
    would_allow: int = 1,
    would_deny: int = 2,
    would_require_approval: int = 1,
    would_change: int = 0,
    errors: int = 1,
) -> PolicySimulationReport:
    """Create a PolicySimulationReport with the given summary counts."""
    return PolicySimulationReport(
        simulation_id="psim_test001",
        name="test simulation",
        generated_at=datetime.now(timezone.utc),
        summary=PolicySimulationSummary(
            total=total,
            unchanged=unchanged,
            would_allow=would_allow,
            would_deny=would_deny,
            would_require_approval=would_require_approval,
            would_change=would_change,
            errors=errors,
        ),
    )


def _make_validation_report(
    error_count: int = 0,
    warning_count: int = 0,
) -> PolicyValidationReport:
    """Create a PolicyValidationReport with the given issue counts."""
    issues: list[PolicyValidationIssue] = []
    for i in range(error_count):
        issues.append(PolicyValidationIssue(
            severity=PolicyValidationSeverity.ERROR,
            code=f"err_{i}",
            message=f"Error {i}",
        ))
    for i in range(warning_count):
        issues.append(PolicyValidationIssue(
            severity=PolicyValidationSeverity.WARNING,
            code=f"warn_{i}",
            message=f"Warning {i}",
        ))
    return PolicyValidationReport(
        valid=error_count == 0,
        issues=issues,
    )


# ===========================================================================
# TestSimulationGateInput
# ===========================================================================

class TestSimulationGateInput:
    """Tests for the SimulationGateInput model."""

    def test_creation_with_reports(self):
        """SimulationGateInput can be created with both reports."""
        sim_report = _make_simulation_report()
        val_report = _make_validation_report(error_count=1, warning_count=2)
        gate_input = SimulationGateInput(
            simulation_report=sim_report,
            validation_report=val_report,
        )
        assert gate_input.simulation_report is sim_report
        assert gate_input.validation_report is val_report

    def test_validation_report_optional(self):
        """validation_report defaults to None when not provided."""
        sim_report = _make_simulation_report()
        gate_input = SimulationGateInput(simulation_report=sim_report)
        assert gate_input.validation_report is None

    def test_default_metadata(self):
        """metadata defaults to an empty dict."""
        sim_report = _make_simulation_report()
        gate_input = SimulationGateInput(simulation_report=sim_report)
        assert gate_input.metadata == {}
        assert gate_input.candidate_rule_ids == []

    def test_candidate_rule_ids_and_metadata(self):
        """candidate_rule_ids and metadata can be provided."""
        sim_report = _make_simulation_report()
        gate_input = SimulationGateInput(
            simulation_report=sim_report,
            candidate_rule_ids=["rpr_rule_1", "rpr_rule_2"],
            metadata={"source": "cli"},
        )
        assert gate_input.candidate_rule_ids == ["rpr_rule_1", "rpr_rule_2"]
        assert gate_input.metadata == {"source": "cli"}


# ===========================================================================
# TestSimulationGateMetrics
# ===========================================================================

class TestSimulationGateMetrics:
    """Tests for the simulation_gate_metrics function."""

    def test_metrics_from_simulation_report(self):
        """All simulation.* metrics are extracted from the summary."""
        sim_report = _make_simulation_report(
            total=10, unchanged=5, would_allow=1,
            would_deny=2, would_require_approval=1, would_change=0, errors=1,
        )
        gate_input = SimulationGateInput(simulation_report=sim_report)
        metrics = simulation_gate_metrics(gate_input)

        assert metrics["simulation.total"] == 10.0
        assert metrics["simulation.unchanged"] == 5.0
        assert metrics["simulation.would_allow"] == 1.0
        assert metrics["simulation.would_deny"] == 2.0
        assert metrics["simulation.would_require_approval"] == 1.0
        assert metrics["simulation.would_change"] == 0.0
        assert metrics["simulation.errors"] == 1.0

    def test_changed_ratio(self):
        """changed_ratio = (would_allow + would_deny + would_require_approval + would_change) / total."""
        # 1 + 2 + 1 + 0 = 4, total = 10 => 0.4
        sim_report = _make_simulation_report(
            total=10, would_allow=1, would_deny=2,
            would_require_approval=1, would_change=0,
        )
        gate_input = SimulationGateInput(simulation_report=sim_report)
        metrics = simulation_gate_metrics(gate_input)

        assert metrics["simulation.changed_ratio"] == pytest.approx(0.4)

    def test_denied_ratio(self):
        """denied_ratio = would_deny / total."""
        sim_report = _make_simulation_report(total=10, would_deny=2)
        gate_input = SimulationGateInput(simulation_report=sim_report)
        metrics = simulation_gate_metrics(gate_input)

        assert metrics["simulation.denied_ratio"] == pytest.approx(0.2)

    def test_approval_required_ratio(self):
        """approval_required_ratio = would_require_approval / total."""
        sim_report = _make_simulation_report(total=10, would_require_approval=1)
        gate_input = SimulationGateInput(simulation_report=sim_report)
        metrics = simulation_gate_metrics(gate_input)

        assert metrics["simulation.approval_required_ratio"] == pytest.approx(0.1)

    def test_division_by_zero_safe(self):
        """When total=0, ratio metrics return 0.0 instead of raising."""
        sim_report = _make_simulation_report(total=0)
        gate_input = SimulationGateInput(simulation_report=sim_report)
        metrics = simulation_gate_metrics(gate_input)

        assert metrics["simulation.changed_ratio"] == 0.0
        assert metrics["simulation.denied_ratio"] == 0.0
        assert metrics["simulation.approval_required_ratio"] == 0.0

    def test_validation_counts_included(self):
        """validation.errors and validation.warnings are counted from the validation report."""
        sim_report = _make_simulation_report()
        val_report = _make_validation_report(error_count=2, warning_count=3)
        gate_input = SimulationGateInput(
            simulation_report=sim_report,
            validation_report=val_report,
        )
        metrics = simulation_gate_metrics(gate_input)

        assert metrics["validation.errors"] == 2.0
        assert metrics["validation.warnings"] == 3.0

    def test_missing_validation_report_safe(self):
        """When validation_report is None, validation metrics are 0.0."""
        sim_report = _make_simulation_report()
        gate_input = SimulationGateInput(simulation_report=sim_report)
        metrics = simulation_gate_metrics(gate_input)

        assert metrics["validation.errors"] == 0.0
        assert metrics["validation.warnings"] == 0.0
