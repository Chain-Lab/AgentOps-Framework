"""Tests for PolicySimulationService.validate_and_gate — Phase 41 Task 3.

Tests the validate → replay → gate pipeline that orchestrates
RuntimePolicyValidator, simulate_from_audit, and SimulationGateEvaluator.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agent_app.governance.audit import InMemoryAuditLogger
from agent_app.governance.policy_gate import PolicyGateResult, PolicyGateRule
from agent_app.governance.policy_simulation import PolicySimulationReport
from agent_app.governance.runtime_policy import RuntimePolicyEffect, RuntimePolicyRule
from agent_app.runtime.policy_simulation_service import PolicySimulationService
from agent_app.runtime.policy_validation import PolicyValidationReport
from agent_app.runtime.runtime_policy_store import InMemoryRuntimePolicyStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_rule(rule_id="r1", name="test", effect="deny"):
    return RuntimePolicyRule(
        rule_id=f"rpr_{rule_id}",
        name=name,
        effect=RuntimePolicyEffect(effect),
        action_type="tool.execute",
        status="enabled",
    )


# ===========================================================================
# TestValidateAndGate
# ===========================================================================

class TestValidateAndGate:
    """Unit tests for PolicySimulationService.validate_and_gate."""

    def test_returns_all_reports(self):
        """validate_and_gate returns (PolicySimulationReport, PolicyValidationReport, PolicyGateResult)."""
        logger = InMemoryAuditLogger()
        store = InMemoryRuntimePolicyStore()
        svc = PolicySimulationService(audit_logger=logger, runtime_policy_store=store)

        candidate_rules = [_make_rule("r1", "deny_all", "deny")]
        gate_rules = [PolicyGateRule(name="default", max_new_denies=10)]

        sim_report, val_report, gate_result = _run_async(
            svc.validate_and_gate(
                candidate_rules=candidate_rules,
                gate_rules=gate_rules,
            )
        )

        assert isinstance(sim_report, PolicySimulationReport)
        assert isinstance(val_report, PolicyValidationReport)
        assert isinstance(gate_result, PolicyGateResult)

    def test_validation_errors_affect_metrics(self):
        """With no audit events, gate should pass since there are no simulation errors."""
        logger = InMemoryAuditLogger()
        store = InMemoryRuntimePolicyStore()
        svc = PolicySimulationService(audit_logger=logger, runtime_policy_store=store)

        candidate_rules = [_make_rule("r1", "deny_all", "deny")]
        gate_rules = [PolicyGateRule(name="strict", max_failed_replays=0)]

        sim_report, val_report, gate_result = _run_async(
            svc.validate_and_gate(
                candidate_rules=candidate_rules,
                gate_rules=gate_rules,
            )
        )

        # No audit events → no simulation errors → gate should pass
        assert gate_result.passed is True
        assert gate_result.failed_replays == 0

    def test_gate_failure_returned(self):
        """With strict gate (max_new_denies=0) and no audit events, should pass (would_deny=0)."""
        logger = InMemoryAuditLogger()
        store = InMemoryRuntimePolicyStore()
        svc = PolicySimulationService(audit_logger=logger, runtime_policy_store=store)

        candidate_rules = [_make_rule("r1", "deny_all", "deny")]
        gate_rules = [PolicyGateRule(name="no_new_denies", max_new_denies=0)]

        sim_report, val_report, gate_result = _run_async(
            svc.validate_and_gate(
                candidate_rules=candidate_rules,
                gate_rules=gate_rules,
            )
        )

        # No audit events → would_deny=0 → gate should pass
        assert gate_result.passed is True
        assert gate_result.new_denies == 0

    def test_gate_pass_returned(self):
        """With lenient gate, should pass."""
        logger = InMemoryAuditLogger()
        store = InMemoryRuntimePolicyStore()
        svc = PolicySimulationService(audit_logger=logger, runtime_policy_store=store)

        candidate_rules = [_make_rule("r1", "deny_all", "deny")]
        gate_rules = [PolicyGateRule(name="lenient", max_new_denies=100, max_changed_decisions=100)]

        sim_report, val_report, gate_result = _run_async(
            svc.validate_and_gate(
                candidate_rules=candidate_rules,
                gate_rules=gate_rules,
            )
        )

        assert gate_result.passed is True
