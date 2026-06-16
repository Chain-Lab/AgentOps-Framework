"""Tests for SimulationGateEvaluator.

Phase 41 Task 2: Verifies that simulation gate evaluation correctly maps
simulation metrics to PolicyGateEvaluator._evaluate_rule() parameters and
assembles PolicyGateResult with proper metadata.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_gate import PolicyGateRule, PolicyGateResult
from agent_app.governance.policy_simulation import (
    PolicySimulationReport,
    PolicySimulationSummary,
)
from agent_app.governance.policy_simulation_gate import SimulationGateInput
from agent_app.runtime.policy_simulation_gate_evaluator import SimulationGateEvaluator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_simulation_report(
    *,
    total: int = 100,
    unchanged: int = 80,
    would_allow: int = 5,
    would_deny: int = 5,
    would_require_approval: int = 3,
    would_change: int = 2,
    errors: int = 0,
    simulation_id: str = "psim_test001",
) -> PolicySimulationReport:
    """Create a PolicySimulationReport with the given summary values."""
    return PolicySimulationReport(
        simulation_id=simulation_id,
        name="test-simulation",
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


def _make_gate_input(
    report: PolicySimulationReport | None = None,
    candidate_rule_ids: list[str] | None = None,
) -> SimulationGateInput:
    """Create a SimulationGateInput wrapping the given report."""
    return SimulationGateInput(
        simulation_report=report or _make_simulation_report(),
        candidate_rule_ids=candidate_rule_ids or ["rule_a", "rule_b"],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSimulationGateEvaluator:

    @pytest.mark.asyncio
    async def test_gate_passes(self) -> None:
        """Rules with generous thresholds should produce passed=True."""
        rules = [
            PolicyGateRule(
                name="allow_changes",
                max_changed_decisions=50,
                max_changed_ratio=0.5,
                max_new_denies=20,
                max_new_approvals=20,
            ),
        ]
        evaluator = SimulationGateEvaluator(rules=rules)
        inp = _make_gate_input()

        result = await evaluator.evaluate(inp)

        assert result.passed is True
        assert result.status == "passed"

    @pytest.mark.asyncio
    async def test_gate_fails_on_would_deny_threshold(self) -> None:
        """max_new_denies=3 with would_deny=5 should fail the gate."""
        rules = [
            PolicyGateRule(name="deny_limit", max_new_denies=3),
        ]
        evaluator = SimulationGateEvaluator(rules=rules)
        inp = _make_gate_input(
            report=_make_simulation_report(would_deny=5),
        )

        result = await evaluator.evaluate(inp)

        assert result.passed is False
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_gate_fails_on_changed_ratio_threshold(self) -> None:
        """max_changed_ratio=0.05 with ratio ~0.09 should fail the gate."""
        rules = [
            PolicyGateRule(name="ratio_limit", max_changed_ratio=0.05),
        ]
        # total=100, changed = would_allow(3) + would_deny(2) + would_require_approval(2) + would_change(2) = 9
        # changed_ratio = 9/100 = 0.09
        evaluator = SimulationGateEvaluator(rules=rules)
        inp = _make_gate_input(
            report=_make_simulation_report(
                total=100,
                unchanged=91,
                would_allow=3,
                would_deny=2,
                would_require_approval=2,
                would_change=2,
            ),
        )

        result = await evaluator.evaluate(inp)

        assert result.passed is False
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_metadata_includes_simulation_id(self) -> None:
        """Result summary should contain simulation_id and source_type."""
        rules = [
            PolicyGateRule(name="lenient", max_changed_decisions=100),
        ]
        evaluator = SimulationGateEvaluator(rules=rules)
        inp = _make_gate_input(
            report=_make_simulation_report(simulation_id="psim_meta001"),
        )

        result = await evaluator.evaluate(inp)

        assert result.summary["source_type"] == "simulation"
        assert result.summary["simulation_id"] == "psim_meta001"

    @pytest.mark.asyncio
    async def test_failed_rules_captured(self) -> None:
        """Two failing rules should both appear in rule_results with status='failed'."""
        rules = [
            PolicyGateRule(name="deny_limit", max_new_denies=1),
            PolicyGateRule(name="ratio_limit", max_changed_ratio=0.01),
        ]
        # would_deny=5 exceeds max_new_denies=1
        # changed = 5+5+3+2 = 15, total=100, ratio=0.15 > 0.01
        evaluator = SimulationGateEvaluator(rules=rules)
        inp = _make_gate_input(
            report=_make_simulation_report(
                total=100,
                unchanged=85,
                would_allow=5,
                would_deny=5,
                would_require_approval=3,
                would_change=2,
            ),
        )

        result = await evaluator.evaluate(inp)

        assert result.passed is False
        assert result.status == "failed"
        failed_rules = [r for r in result.rule_results if r["status"] == "failed"]
        assert len(failed_rules) == 2
        failed_names = {r["rule_name"] for r in failed_rules}
        assert failed_names == {"deny_limit", "ratio_limit"}

    @pytest.mark.asyncio
    async def test_empty_rules_passes(self) -> None:
        """No rules should produce passed=True with no failures."""
        evaluator = SimulationGateEvaluator(rules=[])
        inp = _make_gate_input()

        result = await evaluator.evaluate(inp)

        assert result.passed is True
        assert result.status == "passed"
        assert result.rule_results == []
