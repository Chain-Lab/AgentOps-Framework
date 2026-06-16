"""Simulation gate input model and metrics extraction.

Phase 41 Task 1: Packages simulation + validation reports for gate evaluation
and extracts metric values for gate condition checking.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agent_app.governance.policy_simulation import PolicySimulationReport
from agent_app.runtime.policy_validation import PolicyValidationReport, PolicyValidationSeverity


class SimulationGateInput(BaseModel):
    """Input for simulation gate evaluation.

    Packages a PolicySimulationReport with an optional PolicyValidationReport
    so that gate evaluators can inspect both simulation outcomes and validation
    issues in a single object.
    """

    simulation_report: PolicySimulationReport
    validation_report: PolicyValidationReport | None = None
    candidate_rule_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def simulation_gate_metrics(gate_input: SimulationGateInput) -> dict[str, float]:
    """Extract metric values from a SimulationGateInput.

    Returns a flat dict mapping metric names to float values.  Ratio metrics
    return 0.0 when the denominator is zero.  Validation metrics return 0.0
    when no validation report is attached.

    Metric keys:
        simulation.total, simulation.unchanged, simulation.would_allow,
        simulation.would_deny, simulation.would_require_approval,
        simulation.would_change, simulation.errors,
        simulation.changed_ratio, simulation.denied_ratio,
        simulation.approval_required_ratio,
        validation.errors, validation.warnings
    """
    summary = gate_input.simulation_report.summary
    total = summary.total

    # Direct simulation counters
    metrics: dict[str, float] = {
        "simulation.total": float(total),
        "simulation.unchanged": float(summary.unchanged),
        "simulation.would_allow": float(summary.would_allow),
        "simulation.would_deny": float(summary.would_deny),
        "simulation.would_require_approval": float(summary.would_require_approval),
        "simulation.would_change": float(summary.would_change),
        "simulation.errors": float(summary.errors),
    }

    # Ratio metrics (safe division)
    changed = summary.would_allow + summary.would_deny + summary.would_require_approval + summary.would_change
    metrics["simulation.changed_ratio"] = float(changed) / total if total else 0.0
    metrics["simulation.denied_ratio"] = float(summary.would_deny) / total if total else 0.0
    metrics["simulation.approval_required_ratio"] = float(summary.would_require_approval) / total if total else 0.0

    # Validation metrics
    if gate_input.validation_report is not None:
        metrics["validation.errors"] = float(sum(
            1 for issue in gate_input.validation_report.issues
            if issue.severity == PolicyValidationSeverity.ERROR
        ))
        metrics["validation.warnings"] = float(sum(
            1 for issue in gate_input.validation_report.issues
            if issue.severity == PolicyValidationSeverity.WARNING
        ))
    else:
        metrics["validation.errors"] = 0.0
        metrics["validation.warnings"] = 0.0

    return metrics
