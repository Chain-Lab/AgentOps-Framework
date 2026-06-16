"""Simulation gate evaluator — evaluates simulation outcomes against gate rules.

Phase 41 Task 2: Maps simulation metrics to PolicyGateEvaluator._evaluate_rule()
parameters and assembles a PolicyGateResult, reusing the existing rule evaluation
logic without requiring a PolicyBundle or replay result.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from agent_app.governance.policy_gate import (
    PolicyGateEvaluator,
    PolicyGateResult,
    PolicyGateRule,
    PolicyGateStatus,
)
from agent_app.governance.policy_simulation_gate import (
    SimulationGateInput,
    simulation_gate_metrics,
)


class SimulationGateEvaluator:
    """Evaluates simulation outcomes against configurable gate rules.

    Reuses :class:`PolicyGateEvaluator` rule evaluation logic by calling
    ``_evaluate_rule()`` directly with simulation-derived metrics, then
    assembles a :class:`PolicyGateResult` with simulation-specific metadata.

    Args:
        rules: List of gate rules to evaluate against.
    """

    def __init__(self, rules: list[PolicyGateRule]) -> None:
        self._gate_evaluator = PolicyGateEvaluator(rules=rules)
        self._rules = rules

    async def evaluate(
        self,
        inp: SimulationGateInput,
        name: str | None = None,
        created_by: str | None = None,
    ) -> PolicyGateResult:
        """Evaluate simulation metrics against gate rules.

        Args:
            inp: Simulation gate input containing the simulation report
                and optional validation report.
            name: Optional name for this evaluation (included in summary).
            created_by: Identity of who triggered the evaluation.

        Returns:
            PolicyGateResult with overall status and per-rule results.
        """
        # Step 1: Extract metrics
        metrics = simulation_gate_metrics(inp)

        # Step 2: Map metrics to _evaluate_rule parameters
        total = int(metrics["simulation.total"])
        changed = int(
            metrics["simulation.would_allow"]
            + metrics["simulation.would_deny"]
            + metrics["simulation.would_require_approval"]
            + metrics["simulation.would_change"]
        )
        failed = int(metrics["simulation.errors"])
        changed_ratio = metrics["simulation.changed_ratio"]
        new_denies = int(metrics["simulation.would_deny"])
        new_approvals = int(metrics["simulation.would_require_approval"])
        missing_context_count = int(metrics["validation.errors"])

        # Step 3: Evaluate each rule
        rule_results: list[dict[str, Any]] = []
        overall_failed = False

        for rule in self._rules:
            rule_result = self._gate_evaluator._evaluate_rule(
                rule=rule,
                total=total,
                changed=changed,
                failed=failed,
                changed_ratio=changed_ratio,
                new_denies=new_denies,
                new_approvals=new_approvals,
                missing_context_count=missing_context_count,
            )
            rule_results.append(rule_result)
            if rule_result["status"] == "failed":
                overall_failed = True

        # Step 4: Determine overall status
        if overall_failed:
            status = PolicyGateStatus.FAILED
            passed = False
        elif self._gate_evaluator._has_warnings(rule_results):
            status = PolicyGateStatus.WARNING
            passed = True
        else:
            status = PolicyGateStatus.PASSED
            passed = True

        # Step 5: Assemble result
        simulation_id = inp.simulation_report.simulation_id

        return PolicyGateResult(
            gate_result_id=f"gr_{uuid.uuid4().hex[:12]}",
            bundle_id=f"simulation:{simulation_id}",
            replay_id=simulation_id,
            status=status.value,
            passed=passed,
            total_decisions=total,
            changed_decisions=changed,
            failed_replays=failed,
            changed_ratio=round(changed_ratio, 4),
            new_denies=new_denies,
            new_approvals=new_approvals,
            missing_context_count=missing_context_count,
            rule_results=rule_results,
            summary={
                "source_type": "simulation",
                "simulation_id": simulation_id,
                "candidate_rule_ids": inp.candidate_rule_ids,
                "name": name,
                "validation_report_present": inp.validation_report is not None,
            },
            created_by=created_by,
        )
