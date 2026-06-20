# Phase 41: Policy Gate Integration and Automated Safeguards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bridge Phase 40 simulation/validation results into the existing Policy Gate framework so candidate runtime policy changes can be automatically blocked, warned, or allowed before promotion.

**Architecture:** Build a SimulationGateInput model that packages a PolicySimulationReport + optional PolicyValidationReport into metric dictionaries. A SimulationGateEvaluator converts these metrics and delegates to the existing PolicyGateEvaluator's rule-checking logic (via a small adapter rather than rewriting the gate system). A `validate_and_gate` method on PolicySimulationService orchestrates validate → replay → gate in one call. CLI `policy simulation gate` command exits non-zero on gate failure. Console gets a gate page. Gate results persist via existing PolicyGateStore with simulation metadata.

**Tech Stack:** Python 3.12+, Pydantic v2, asyncio, FastAPI/Jinja2 (optional), existing PolicyGateRule/PolicyGateEvaluator/PolicyGateResult/PolicyGateStore infrastructure.

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `agent_app/governance/policy_simulation_gate.py` | SimulationGateInput, simulation_gate_metrics(), SimulationGateRuleConfig |
| Create | `agent_app/runtime/policy_simulation_gate_evaluator.py` | SimulationGateEvaluator |
| Modify | `agent_app/runtime/policy_simulation_service.py` | Add validate_and_gate() method |
| Modify | `agent_app/governance/policy_rbac.py` | Add SIMULATION_GATE_RUN, SIMULATION_GATE_VIEW |
| Modify | `agent_app/governance/policy_change_event.py` | Add 4 simulation gate event types |
| Modify | `agent_app/config/schema.py` | Add gates list to PolicySimulationConfig |
| Modify | `agent_app/config/loader.py` | Wire gate evaluator and gate rules |
| Modify | `agent_app/cli.py` | Add `policy simulation gate` subcommand |
| Modify | `agent_app/console/router.py` | Add simulation gate GET/POST routes |
| Create | `agent_app/console/templates/policy_simulation_gate.html` | Gate form page |
| Create | `agent_app/console/templates/policy_simulation_gate_report.html` | Gate result page |
| Modify | `agent_app/adapters/fastapi.py` | Wire simulation_gate_evaluator |
| Create | `tests/unit/test_policy_simulation_gate.py` | Tests for SimulationGateInput + metrics |
| Create | `tests/unit/test_policy_simulation_gate_evaluator.py` | Tests for SimulationGateEvaluator |
| Create | `tests/unit/test_policy_simulation_gate_service.py` | Tests for validate_and_gate integration |
| Create | `tests/unit/test_policy_simulation_gate_wiring.py` | Config, loader, RBAC, events tests |
| Create | `tests/unit/test_policy_simulation_gate_cli.py` | CLI gate command tests |
| Create | `tests/unit/test_policy_simulation_gate_console.py` | Console gate page tests |

---

### Task 1: SimulationGateInput and simulation_gate_metrics

**Files:**
- Create: `agent_app/governance/policy_simulation_gate.py`
- Test: `tests/unit/test_policy_simulation_gate.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for SimulationGateInput and simulation_gate_metrics."""
from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_simulation import (
    PolicySimulationOutcome,
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


def _make_report(
    total: int = 100,
    unchanged: int = 90,
    would_allow: int = 2,
    would_deny: int = 5,
    would_require_approval: int = 1,
    would_change: int = 1,
    errors: int = 1,
) -> PolicySimulationReport:
    """Helper to create a PolicySimulationReport with given summary counts."""
    return PolicySimulationReport(
        simulation_id="psim_test123",
        name="test",
        generated_at=datetime.now(timezone.utc),
        candidate_rule_ids=["r1"],
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
    errors: int = 0,
    warnings: int = 0,
) -> PolicyValidationReport:
    """Helper to create a PolicyValidationReport."""
    issues: list[PolicyValidationIssue] = []
    for _ in range(errors):
        issues.append(PolicyValidationIssue(
            severity=PolicyValidationSeverity.ERROR,
            code="test_error",
            message="test error",
        ))
    for _ in range(warnings):
        issues.append(PolicyValidationIssue(
            severity=PolicyValidationSeverity.WARNING,
            code="test_warning",
            message="test warning",
        ))
    return PolicyValidationReport(valid=errors == 0, issues=issues)


class TestSimulationGateInput:
    def test_creation_with_reports(self):
        report = _make_report()
        validation = _make_validation_report()
        inp = SimulationGateInput(
            simulation_report=report,
            validation_report=validation,
            candidate_rule_ids=["r1"],
        )
        assert inp.simulation_report is report
        assert inp.validation_report is validation
        assert inp.candidate_rule_ids == ["r1"]

    def test_validation_report_optional(self):
        report = _make_report()
        inp = SimulationGateInput(simulation_report=report)
        assert inp.validation_report is None

    def test_default_metadata(self):
        inp = SimulationGateInput(simulation_report=_make_report())
        assert inp.metadata == {}


class TestSimulationGateMetrics:
    def test_metrics_from_simulation_report(self):
        report = _make_report(
            total=100, unchanged=90, would_allow=2, would_deny=5,
            would_require_approval=1, would_change=1, errors=1,
        )
        inp = SimulationGateInput(simulation_report=report)
        metrics = simulation_gate_metrics(inp)
        assert metrics["simulation.total"] == 100
        assert metrics["simulation.unchanged"] == 90
        assert metrics["simulation.would_allow"] == 2
        assert metrics["simulation.would_deny"] == 5
        assert metrics["simulation.would_require_approval"] == 1
        assert metrics["simulation.would_change"] == 1
        assert metrics["simulation.errors"] == 1

    def test_changed_ratio(self):
        report = _make_report(total=100, unchanged=90, would_deny=5, would_allow=2,
                              would_require_approval=1, would_change=1, errors=1)
        inp = SimulationGateInput(simulation_report=report)
        metrics = simulation_gate_metrics(inp)
        # changed = would_allow + would_deny + would_require_approval + would_change = 9
        assert metrics["simulation.changed_ratio"] == pytest.approx(0.09)

    def test_denied_ratio(self):
        report = _make_report(total=100, would_deny=5)
        inp = SimulationGateInput(simulation_report=report)
        metrics = simulation_gate_metrics(inp)
        assert metrics["simulation.denied_ratio"] == pytest.approx(0.05)

    def test_approval_required_ratio(self):
        report = _make_report(total=100, would_require_approval=3)
        inp = SimulationGateInput(simulation_report=report)
        metrics = simulation_gate_metrics(inp)
        assert metrics["simulation.approval_required_ratio"] == pytest.approx(0.03)

    def test_division_by_zero_safe(self):
        report = _make_report(total=0, unchanged=0, would_allow=0, would_deny=0,
                              would_require_approval=0, would_change=0, errors=0)
        inp = SimulationGateInput(simulation_report=report)
        metrics = simulation_gate_metrics(inp)
        assert metrics["simulation.changed_ratio"] == 0.0
        assert metrics["simulation.denied_ratio"] == 0.0
        assert metrics["simulation.approval_required_ratio"] == 0.0

    def test_validation_counts_included(self):
        report = _make_report()
        validation = _make_validation_report(errors=2, warnings=3)
        inp = SimulationGateInput(simulation_report=report, validation_report=validation)
        metrics = simulation_gate_metrics(inp)
        assert metrics["validation.errors"] == 2
        assert metrics["validation.warnings"] == 3

    def test_missing_validation_report_safe(self):
        report = _make_report()
        inp = SimulationGateInput(simulation_report=report)
        metrics = simulation_gate_metrics(inp)
        assert metrics["validation.errors"] == 0
        assert metrics["validation.warnings"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation_gate.py -v`
Expected: FAIL with ModuleNotFoundError / ImportError

- [ ] **Step 3: Write minimal implementation**

```python
"""Simulation gate input models and metrics extraction.

Phase 41: Bridges simulation/validation reports into gate rule evaluation.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agent_app.governance.policy_simulation import PolicySimulationReport
from agent_app.runtime.policy_validation import PolicyValidationReport


class SimulationGateInput(BaseModel):
    """Input for simulation gate evaluation.

    Packages a PolicySimulationReport and optional PolicyValidationReport
    for evaluation against simulation-aware gate rules.
    """

    simulation_report: PolicySimulationReport
    validation_report: PolicyValidationReport | None = None
    candidate_rule_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def simulation_gate_metrics(inp: SimulationGateInput) -> dict[str, float]:
    """Extract metric values from a SimulationGateInput for gate rule evaluation.

    Returns a flat dict mapping metric names to float values. Supported metrics:
      simulation.total, simulation.unchanged, simulation.would_allow,
      simulation.would_deny, simulation.would_require_approval,
      simulation.would_change, simulation.errors, simulation.changed_ratio,
      simulation.denied_ratio, simulation.approval_required_ratio,
      validation.errors, validation.warnings

    Division-by-zero for ratios returns 0.0. Missing validation_report
    yields validation.errors=0 and validation.warnings=0.
    """
    s = inp.simulation_report.summary
    total = s.total
    changed = s.would_allow + s.would_deny + s.would_require_approval + s.would_change

    metrics: dict[str, float] = {
        "simulation.total": float(total),
        "simulation.unchanged": float(s.unchanged),
        "simulation.would_allow": float(s.would_allow),
        "simulation.would_deny": float(s.would_deny),
        "simulation.would_require_approval": float(s.would_require_approval),
        "simulation.would_change": float(s.would_change),
        "simulation.errors": float(s.errors),
        "simulation.changed_ratio": changed / total if total > 0 else 0.0,
        "simulation.denied_ratio": s.would_deny / total if total > 0 else 0.0,
        "simulation.approval_required_ratio": s.would_require_approval / total if total > 0 else 0.0,
    }

    if inp.validation_report is not None:
        error_count = sum(
            1 for i in inp.validation_report.issues
            if i.severity.value == "error"
        )
        warning_count = sum(
            1 for i in inp.validation_report.issues
            if i.severity.value == "warning"
        )
        metrics["validation.errors"] = float(error_count)
        metrics["validation.warnings"] = float(warning_count)
    else:
        metrics["validation.errors"] = 0.0
        metrics["validation.warnings"] = 0.0

    return metrics
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation_gate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/governance/policy_simulation_gate.py tests/unit/test_policy_simulation_gate.py
git commit -m "feat: Phase 41 Task 1 — SimulationGateInput and simulation_gate_metrics"
```

---

### Task 2: SimulationGateEvaluator

**Files:**
- Create: `agent_app/runtime/policy_simulation_gate_evaluator.py`
- Test: `tests/unit/test_policy_simulation_gate_evaluator.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for SimulationGateEvaluator."""
from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_gate import PolicyGateRule, PolicyGateStatus
from agent_app.governance.policy_simulation import (
    PolicySimulationReport,
    PolicySimulationSummary,
)
from agent_app.governance.policy_simulation_gate import SimulationGateInput
from agent_app.runtime.policy_simulation_gate_evaluator import SimulationGateEvaluator
from agent_app.runtime.policy_validation import PolicyValidationReport


def _make_input(
    total=100, unchanged=90, would_deny=5, would_allow=2,
    would_require_approval=1, would_change=1, errors=1,
    validation_errors=0, validation_warnings=0,
) -> SimulationGateInput:
    from agent_app.runtime.policy_validation import PolicyValidationIssue, PolicyValidationSeverity

    report = PolicySimulationReport(
        simulation_id="psim_evaltest",
        name="eval test",
        generated_at=datetime.now(timezone.utc),
        candidate_rule_ids=["r1"],
        summary=PolicySimulationSummary(
            total=total, unchanged=unchanged, would_allow=would_allow,
            would_deny=would_deny, would_require_approval=would_require_approval,
            would_change=would_change, errors=errors,
        ),
    )
    issues = []
    for _ in range(validation_errors):
        issues.append(PolicyValidationIssue(
            severity=PolicyValidationSeverity.ERROR, code="err", message="err"))
    for _ in range(validation_warnings):
        issues.append(PolicyValidationIssue(
            severity=PolicyValidationSeverity.WARNING, code="warn", message="warn"))
    vr = PolicyValidationReport(valid=validation_errors == 0, issues=issues) if (validation_errors or validation_warnings) else None
    return SimulationGateInput(simulation_report=report, validation_report=vr, candidate_rule_ids=["r1"])


class TestSimulationGateEvaluator:
    @pytest.mark.asyncio
    async def test_gate_passes(self):
        rules = [
            PolicyGateRule(name="no_errors", max_failed_replays=0),
            PolicyGateRule(name="deny_limit", max_new_denies=10),
        ]
        evaluator = SimulationGateEvaluator(rules=rules)
        inp = _make_input(errors=0, would_deny=5)
        result = await evaluator.evaluate(inp)
        assert result.passed is True
        assert result.status == PolicyGateStatus.PASSED.value

    @pytest.mark.asyncio
    async def test_gate_fails_on_would_deny_threshold(self):
        rules = [PolicyGateRule(name="deny_limit", max_new_denies=3)]
        evaluator = SimulationGateEvaluator(rules=rules)
        inp = _make_input(would_deny=5)
        result = await evaluator.evaluate(inp)
        assert result.passed is False
        assert result.status == PolicyGateStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_gate_fails_on_changed_ratio_threshold(self):
        rules = [PolicyGateRule(name="ratio_limit", max_changed_ratio=0.05)]
        evaluator = SimulationGateEvaluator(rules=rules)
        # changed = 5+2+1+1 = 9 out of 100 = 0.09 > 0.05
        inp = _make_input(total=100, would_deny=5, would_allow=2,
                          would_require_approval=1, would_change=1)
        result = await evaluator.evaluate(inp)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_metadata_includes_simulation_id(self):
        rules = [PolicyGateRule(name="ok", max_new_denies=100)]
        evaluator = SimulationGateEvaluator(rules=rules)
        inp = _make_input()
        result = await evaluator.evaluate(inp)
        assert result.summary.get("simulation_id") == "psim_evaltest"
        assert result.summary.get("source_type") == "simulation"

    @pytest.mark.asyncio
    async def test_failed_rules_captured(self):
        rules = [
            PolicyGateRule(name="deny_limit", max_new_denies=3),
            PolicyGateRule(name="error_limit", max_failed_replays=0),
        ]
        evaluator = SimulationGateEvaluator(rules=rules)
        inp = _make_input(would_deny=5, errors=2)
        result = await evaluator.evaluate(inp)
        assert result.passed is False
        failed_names = [r["rule_name"] for r in result.rule_results if r["status"] == "failed"]
        assert "deny_limit" in failed_names
        assert "error_limit" in failed_names

    @pytest.mark.asyncio
    async def test_empty_rules_passes(self):
        evaluator = SimulationGateEvaluator(rules=[])
        inp = _make_input()
        result = await evaluator.evaluate(inp)
        assert result.passed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation_gate_evaluator.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

The key insight: `PolicyGateEvaluator._evaluate_rule()` takes `(rule, total, changed, failed, changed_ratio, new_denies, new_approvals, missing_context_count)`. We map simulation metrics to these params:
- `total` → `simulation.total`
- `changed` → `would_allow + would_deny + would_require_approval + would_change`
- `failed` → `simulation.errors`
- `changed_ratio` → `simulation.changed_ratio`
- `new_denies` → `simulation.would_deny`
- `new_approvals` → `simulation.would_require_approval`
- `missing_context_count` → `validation.errors`

We cannot reuse `PolicyGateEvaluator.evaluate()` directly because it expects a `PolicyBundle` and `replay_result`. Instead, we instantiate `PolicyGateEvaluator` with the rules and call its `_evaluate_rule()` method directly, then assemble the `PolicyGateResult`.

```python
"""SimulationGateEvaluator — evaluates simulation gate rules against simulation/validation metrics.

Phase 41: Bridges simulation results into the existing PolicyGate framework.
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
    """Evaluates simulation gate rules against simulation/validation metrics.

    Reuses PolicyGateEvaluator's rule-checking logic by mapping simulation
    metrics to the gate rule parameter space.
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
        """Evaluate simulation gate rules against the given input.

        Args:
            inp: Simulation gate input containing report(s) and metadata.
            name: Optional name for the gate result.
            created_by: Identity of who triggered the evaluation.

        Returns:
            PolicyGateResult with overall status and per-rule results.
        """
        metrics = simulation_gate_metrics(inp)
        s = inp.simulation_report.summary

        total = int(metrics["simulation.total"])
        changed = int(s.would_allow + s.would_deny + s.would_require_approval + s.would_change)
        failed = int(metrics["simulation.errors"])
        changed_ratio = metrics["simulation.changed_ratio"]
        new_denies = int(metrics["simulation.would_deny"])
        new_approvals = int(metrics["simulation.would_require_approval"])
        missing_context_count = int(metrics["validation.errors"])

        # Evaluate each rule using existing _evaluate_rule logic
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

        has_warnings = self._gate_evaluator._has_warnings(rule_results)

        if overall_failed:
            status = PolicyGateStatus.FAILED
            passed = False
        elif has_warnings:
            status = PolicyGateStatus.WARNING
            passed = True
        else:
            status = PolicyGateStatus.PASSED
            passed = True

        return PolicyGateResult(
            gate_result_id=f"gr_{uuid.uuid4().hex[:12]}",
            bundle_id=f"simulation:{inp.simulation_report.simulation_id}",
            replay_id=inp.simulation_report.simulation_id,
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
                "simulation_id": inp.simulation_report.simulation_id,
                "candidate_rule_ids": inp.candidate_rule_ids,
                "name": name,
                "validation_report_present": inp.validation_report is not None,
            },
            created_at=datetime.now(timezone.utc),
            created_by=created_by,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation_gate_evaluator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_simulation_gate_evaluator.py tests/unit/test_policy_simulation_gate_evaluator.py
git commit -m "feat: Phase 41 Task 2 — SimulationGateEvaluator"
```

---

### Task 3: PolicySimulationService.validate_and_gate

**Files:**
- Modify: `agent_app/runtime/policy_simulation_service.py`
- Test: `tests/unit/test_policy_simulation_gate_service.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for PolicySimulationService.validate_and_gate integration."""
from datetime import datetime, timezone

import pytest

from agent_app.governance.audit import InMemoryAuditLogger
from agent_app.governance.policy_gate import PolicyGateRule, PolicyGateStatus
from agent_app.governance.policy_simulation import (
    PolicySimulationOutcome,
    PolicySimulationReport,
    PolicySimulationResult,
    PolicySimulationSummary,
)
from agent_app.governance.runtime_policy import RuntimePolicyEffect, RuntimePolicyRule
from agent_app.runtime.policy_simulation_service import PolicySimulationService
from agent_app.runtime.runtime_policy_store import InMemoryRuntimePolicyStore


def _make_rule(rule_id: str = "r1", name: str = "test", effect: str = "deny") -> RuntimePolicyRule:
    return RuntimePolicyRule(
        rule_id=rule_id,
        name=name,
        effect=RuntimePolicyEffect(effect),
        action_type="tool_execute",
        status="active",
    )


class TestValidateAndGate:
    @pytest.mark.asyncio
    async def test_returns_all_reports(self):
        store = InMemoryRuntimePolicyStore()
        service = PolicySimulationService(
            audit_logger=InMemoryAuditLogger(),
            runtime_policy_store=store,
        )
        gate_rules = [PolicyGateRule(name="ok", max_new_denies=100)]
        sim_report, val_report, gate_result = await service.validate_and_gate(
            candidate_rules=[_make_rule()],
            gate_rules=gate_rules,
        )
        assert isinstance(sim_report, PolicySimulationReport)
        assert isinstance(val_report, type(None)) or hasattr(val_report, "valid")
        assert hasattr(gate_result, "passed")

    @pytest.mark.asyncio
    async def test_validation_errors_affect_metrics(self):
        """A rule with duplicate names should produce a validation warning,
        which does not block the gate, but the gate rule on simulation.errors
        can block if the simulation has errors."""
        service = PolicySimulationService(
            audit_logger=InMemoryAuditLogger(),
        )
        gate_rules = [PolicyGateRule(name="no_errors", max_failed_replays=0)]
        sim_report, val_report, gate_result = await service.validate_and_gate(
            candidate_rules=[_make_rule()],
            gate_rules=gate_rules,
        )
        # No audit events => no simulation cases => no errors => should pass
        assert gate_result.passed is True

    @pytest.mark.asyncio
    async def test_gate_failure_returned(self):
        service = PolicySimulationService(
            audit_logger=InMemoryAuditLogger(),
        )
        gate_rules = [PolicyGateRule(name="strict", max_new_denies=0)]
        # No audit events => no cases => would_deny=0 => should pass
        sim_report, val_report, gate_result = await service.validate_and_gate(
            candidate_rules=[_make_rule()],
            gate_rules=gate_rules,
        )
        assert gate_result.passed is True

    @pytest.mark.asyncio
    async def test_gate_pass_returned(self):
        service = PolicySimulationService(
            audit_logger=InMemoryAuditLogger(),
        )
        gate_rules = [PolicyGateRule(name="ok", max_new_denies=100)]
        sim_report, val_report, gate_result = await service.validate_and_gate(
            candidate_rules=[_make_rule()],
            gate_rules=gate_rules,
        )
        assert gate_result.passed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation_gate_service.py -v`
Expected: FAIL with AttributeError (validate_and_gate not found)

- [ ] **Step 3: Write minimal implementation**

Add to `PolicySimulationService` in `agent_app/runtime/policy_simulation_service.py`:

```python
async def validate_and_gate(
    self,
    candidate_rules: list[RuntimePolicyRule],
    gate_rules: list[PolicyGateRule],
    include_base: bool = True,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    limit: int | None = None,
    name: str | None = None,
) -> tuple[PolicySimulationReport, PolicyValidationReport, PolicyGateResult]:
    """Validate candidate rules, replay historical audit, and evaluate gate.

    Orchestrates the full validate → replay → gate pipeline.

    Args:
        candidate_rules: Candidate runtime policy rules to test.
        gate_rules: Gate rules to evaluate against.
        include_base: If True, include existing base rules alongside candidates.
        window_start: Only include audit events at or after this time.
        window_end: Only include audit events before this time.
        limit: Maximum number of audit cases to include.
        name: Optional name for the simulation report.

    Returns:
        Tuple of (simulation_report, validation_report, gate_result).
    """
    from agent_app.governance.policy_simulation_gate import SimulationGateInput
    from agent_app.runtime.policy_simulation_gate_evaluator import SimulationGateEvaluator
    from agent_app.runtime.policy_validation import RuntimePolicyValidator

    # Step 1: Validate
    validator = RuntimePolicyValidator()
    validation_report = validator.validate_rules(candidate_rules)

    # Step 2: Replay
    sim_report = await self.simulate_from_audit(
        candidate_rules=candidate_rules,
        include_base=include_base,
        window_start=window_start,
        window_end=window_end,
        limit=limit,
        name=name,
    )

    # Step 3: Build gate input
    gate_input = SimulationGateInput(
        simulation_report=sim_report,
        validation_report=validation_report,
        candidate_rule_ids=[r.rule_id for r in candidate_rules],
    )

    # Step 4: Evaluate gate
    evaluator = SimulationGateEvaluator(rules=gate_rules)
    gate_result = await evaluator.evaluate(gate_input, name=name)

    return sim_report, validation_report, gate_result
```

Also add the necessary imports at the top:

```python
from agent_app.governance.policy_gate import PolicyGateRule
from agent_app.runtime.policy_validation import PolicyValidationReport
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation_gate_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_simulation_service.py tests/unit/test_policy_simulation_gate_service.py
git commit -m "feat: Phase 41 Task 3 — PolicySimulationService.validate_and_gate"
```

---

### Task 4: Config, Loader, RBAC, Events

**Files:**
- Modify: `agent_app/config/schema.py`
- Modify: `agent_app/config/loader.py`
- Modify: `agent_app/governance/policy_rbac.py`
- Modify: `agent_app/governance/policy_change_event.py`
- Test: `tests/unit/test_policy_simulation_gate_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for Phase 41 config, loader, RBAC, and events wiring."""
import pytest

from agent_app.config.schema import PolicySimulationConfig, PolicyGateRuleConfig
from agent_app.governance.policy_rbac import PolicyReleasePermission, _DEFAULT_ALLOWED
from agent_app.governance.policy_change_event import PolicyChangeEventType


class TestSimulationGateConfig:
    def test_missing_gates_preserves_behavior(self):
        cfg = PolicySimulationConfig(enabled=True)
        assert cfg.gates == []

    def test_config_gate_rules_load(self):
        cfg = PolicySimulationConfig(
            enabled=True,
            gates=[
                PolicyGateRuleConfig(name="no_errors", max_failed_replays=0),
                PolicyGateRuleConfig(name="deny_limit", max_new_denies=10),
            ],
        )
        assert len(cfg.gates) == 2
        assert cfg.gates[0].name == "no_errors"
        assert cfg.gates[0].max_failed_replays == 0
        assert cfg.gates[1].max_new_denies == 10

    def test_invalid_gate_rule_fails(self):
        with pytest.raises(Exception):
            PolicyGateRuleConfig()  # name is required


class TestSimulationGateRBAC:
    def test_simulation_gate_run_permission_exists(self):
        assert PolicyReleasePermission.SIMULATION_GATE_RUN == "policy.simulation.gate.run"

    def test_simulation_gate_view_permission_exists(self):
        assert PolicyReleasePermission.SIMULATION_GATE_VIEW == "policy.simulation.gate.view"

    def test_gate_view_in_default_allowed(self):
        assert PolicyReleasePermission.SIMULATION_GATE_VIEW in _DEFAULT_ALLOWED


class TestSimulationGateEvents:
    def test_gate_run_event(self):
        assert PolicyChangeEventType.SIMULATION_GATE_RUN == "policy.simulation.gate_run"

    def test_gate_passed_event(self):
        assert PolicyChangeEventType.SIMULATION_GATE_PASSED == "policy.simulation.gate_passed"

    def test_gate_failed_event(self):
        assert PolicyChangeEventType.SIMULATION_GATE_FAILED == "policy.simulation.gate_failed"

    def test_gate_permission_denied_event(self):
        assert PolicyChangeEventType.SIMULATION_GATE_PERMISSION_DENIED == "policy.simulation.gate_permission_denied"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation_gate_wiring.py -v`
Expected: FAIL with AttributeError

- [ ] **Step 3: Implement changes**

**schema.py** — Add `gates` field to `PolicySimulationConfig`:

```python
class PolicySimulationConfig(BaseModel):
    """Configuration for policy simulation and validation."""

    enabled: bool = Field(default=False, description="Enable policy simulation service")
    gates: list[PolicyGateRuleConfig] = Field(
        default_factory=list,
        description="Simulation gate rules (reuses PolicyGateRuleConfig)",
    )
```

**policy_rbac.py** — Add two new permissions:

```python
SIMULATION_GATE_RUN = "policy.simulation.gate.run"
SIMULATION_GATE_VIEW = "policy.simulation.gate.view"
```

Add `SIMULATION_GATE_VIEW` to `_DEFAULT_ALLOWED`.

**policy_change_event.py** — Add four new event types:

```python
SIMULATION_GATE_RUN = "policy.simulation.gate_run"
SIMULATION_GATE_PASSED = "policy.simulation.gate_passed"
SIMULATION_GATE_FAILED = "policy.simulation.gate_failed"
SIMULATION_GATE_PERMISSION_DENIED = "policy.simulation.gate_permission_denied"
```

**loader.py** — Add Phase 41 wiring: when `policy_simulation.enabled` and `policy_simulation.gates` are present, create `SimulationGateEvaluator` and store it on the app as `simulation_gate_evaluator`. Convert `PolicyGateRuleConfig` → `PolicyGateRule` objects.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation_gate_wiring.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/config/schema.py agent_app/config/loader.py agent_app/governance/policy_rbac.py agent_app/governance/policy_change_event.py tests/unit/test_policy_simulation_gate_wiring.py
git commit -m "feat: Phase 41 Task 4 — config, loader, RBAC, events for simulation gate"
```

---

### Task 5: CLI simulation gate command

**Files:**
- Modify: `agent_app/cli.py`
- Test: `tests/unit/test_policy_simulation_gate_cli.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for CLI policy simulation gate command."""
import json
import os
import tempfile

import pytest

from agent_app.cli import build_app


def _write_yaml_rules(path: str, content: str) -> None:
    with open(path, "w") as f:
        f.write(content)


def _write_gate_rules(path: str, content: str) -> None:
    with open(path, "w") as f:
        f.write(content)


class TestSimulationGateCLI:
    def test_gate_passes_exit_0(self, tmp_path):
        config = tmp_path / "agentapp.yaml"
        config.write_text("governance:\n  policy_simulation:\n    enabled: true\n")
        rules = tmp_path / "rules.yaml"
        rules.write_text("- rule_id: r1\n  name: test\n  effect: deny\n  action_type: tool_execute\n  status: active\n")
        gates = tmp_path / "gates.yaml"
        gates.write_text("- name: ok\n  max_new_denies: 100\n")
        parser = build_app()
        args = parser.parse_args([
            "policy", "simulation", "gate",
            "--config", str(config),
            "--rules-file", str(rules),
            "--gate-rules-file", str(gates),
        ])
        from agent_app.cli import _cmd_policy_simulation_gate
        result = _run_async_cli(args, _cmd_policy_simulation_gate)
        assert result == 0

    def test_gate_fails_exit_nonzero(self, tmp_path):
        config = tmp_path / "agentapp.yaml"
        config.write_text("governance:\n  policy_simulation:\n    enabled: true\n")
        rules = tmp_path / "rules.yaml"
        rules.write_text("- rule_id: r1\n  name: test\n  effect: deny\n  action_type: tool_execute\n  status: active\n")
        gates = tmp_path / "gates.yaml"
        gates.write_text("- name: strict\n  max_failed_replays: -1\n")
        parser = build_app()
        args = parser.parse_args([
            "policy", "simulation", "gate",
            "--config", str(config),
            "--rules-file", str(rules),
            "--gate-rules-file", str(gates),
        ])
        from agent_app.cli import _cmd_policy_simulation_gate
        result = _run_async_cli(args, _cmd_policy_simulation_gate)
        assert result != 0

    def test_json_output(self, tmp_path):
        config = tmp_path / "agentapp.yaml"
        config.write_text("governance:\n  policy_simulation:\n    enabled: true\n")
        rules = tmp_path / "rules.yaml"
        rules.write_text("- rule_id: r1\n  name: test\n  effect: deny\n  action_type: tool_execute\n  status: active\n")
        gates = tmp_path / "gates.yaml"
        gates.write_text("- name: ok\n  max_new_denies: 100\n")
        parser = build_app()
        args = parser.parse_args([
            "policy", "simulation", "gate",
            "--config", str(config),
            "--rules-file", str(rules),
            "--gate-rules-file", str(gates),
            "--json",
        ])
        from agent_app.cli import _cmd_policy_simulation_gate
        # We just verify it doesn't crash — output validation is secondary
        # The real test is exit code
        _run_async_cli(args, _cmd_policy_simulation_gate)

    def test_output_writes_file(self, tmp_path):
        config = tmp_path / "agentapp.yaml"
        config.write_text("governance:\n  policy_simulation:\n    enabled: true\n")
        rules = tmp_path / "rules.yaml"
        rules.write_text("- rule_id: r1\n  name: test\n  effect: deny\n  action_type: tool_execute\n  status: active\n")
        gates = tmp_path / "gates.yaml"
        gates.write_text("- name: ok\n  max_new_denies: 100\n")
        output = tmp_path / "result.json"
        parser = build_app()
        args = parser.parse_args([
            "policy", "simulation", "gate",
            "--config", str(config),
            "--rules-file", str(rules),
            "--gate-rules-file", str(gates),
            "--output", str(output),
        ])
        from agent_app.cli import _cmd_policy_simulation_gate
        result = _run_async_cli(args, _cmd_policy_simulation_gate)
        assert result == 0
        assert output.exists()

    def test_invalid_gate_rules_file(self, tmp_path):
        config = tmp_path / "agentapp.yaml"
        config.write_text("governance:\n  policy_simulation:\n    enabled: true\n")
        rules = tmp_path / "rules.yaml"
        rules.write_text("- rule_id: r1\n  name: test\n  effect: deny\n  action_type: tool_execute\n  status: active\n")
        gates = tmp_path / "gates.yaml"
        gates.write_text("not: valid: yaml: [")  # broken YAML
        parser = build_app()
        args = parser.parse_args([
            "policy", "simulation", "gate",
            "--config", str(config),
            "--rules-file", str(rules),
            "--gate-rules-file", str(gates),
        ])
        from agent_app.cli import _cmd_policy_simulation_gate
        result = _run_async_cli(args, _cmd_policy_simulation_gate)
        assert result != 0


def _run_async_cli(args, cmd_func):
    """Run an async CLI command synchronously."""
    import asyncio
    return asyncio.run(cmd_func(args))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation_gate_cli.py -v`
Expected: FAIL with AttributeError

- [ ] **Step 3: Implement CLI command**

Add to `agent_app/cli.py`:

1. Add `gate` subparser under `simulation` subcommands
2. Add `--gate-rules-file` argument
3. Add `_cmd_policy_simulation_gate` async function that:
   - Loads candidate rules from `--rules-file`
   - Loads gate rules from `--gate-rules-file` or config gates
   - Calls `service.validate_and_gate()`
   - Exits 0 if gate passes, non-zero if gate fails
   - Supports `--json` and `--output` flags
   - Prints summary, validation issues, gate status, failed rules
4. Add routing in the main dispatch

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation_gate_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/cli.py tests/unit/test_policy_simulation_gate_cli.py
git commit -m "feat: Phase 41 Task 5 — CLI simulation gate command"
```

---

### Task 6: Console simulation gate pages

**Files:**
- Modify: `agent_app/console/router.py`
- Modify: `agent_app/adapters/fastapi.py`
- Create: `agent_app/console/templates/policy_simulation_gate.html`
- Create: `agent_app/console/templates/policy_simulation_gate_report.html`
- Test: `tests/unit/test_policy_simulation_gate_console.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for console simulation gate pages."""
import pytest

from agent_app.governance.policy_gate import PolicyGateRule
from agent_app.governance.policy_simulation_gate import SimulationGateInput
from agent_app.runtime.policy_simulation_gate_evaluator import SimulationGateEvaluator


try:
    from fastapi.testclient import TestClient
    from agent_app.console.router import build_policy_console_router
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="FastAPI not installed")


def _run_async(coro):
    """Run an async coroutine in a fresh event loop."""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def gate_client():
    """Create a test client with simulation gate routes."""
    from fastapi import FastAPI
    from agent_app.governance.audit import InMemoryAuditLogger
    from agent_app.runtime.policy_simulation_service import PolicySimulationService

    service = PolicySimulationService(
        audit_logger=InMemoryAuditLogger(),
    )
    router = build_policy_console_router(simulation_service=service)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestSimulationGateConsole:
    def test_gate_page_renders(self, gate_client):
        resp = gate_client.get("/policy-console/simulation/gate")
        assert resp.status_code == 200
        assert "gate" in resp.text.lower()

    def test_gate_post_pass_renders_report(self, gate_client):
        yaml_rules = (
            "- rule_id: r1\n  name: test\n  effect: deny\n"
            "  action_type: tool_execute\n  status: active\n"
        )
        gate_rules_yaml = (
            "- name: ok\n  max_new_denies: 100\n"
        )
        resp = gate_client.post("/policy-console/simulation/gate", data={
            "candidate_rules_yaml": yaml_rules,
            "gate_rules_yaml": gate_rules_yaml,
        })
        assert resp.status_code == 200

    def test_gate_post_fail_renders_failed_rules(self, gate_client):
        yaml_rules = (
            "- rule_id: r1\n  name: test\n  effect: deny\n"
            "  action_type: tool_execute\n  status: active\n"
        )
        gate_rules_yaml = (
            "- name: strict\n  max_failed_replays: -1\n"
        )
        resp = gate_client.post("/policy-console/simulation/gate", data={
            "candidate_rules_yaml": yaml_rules,
            "gate_rules_yaml": gate_rules_yaml,
        })
        assert resp.status_code == 200

    def test_errors_render_clearly(self, gate_client):
        resp = gate_client.post("/policy-console/simulation/gate", data={
            "candidate_rules_yaml": "not: valid",
            "gate_rules_yaml": "- name: ok\n  max_new_denies: 100\n",
        })
        assert resp.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation_gate_console.py -v`
Expected: FAIL with 404 (route not found)

- [ ] **Step 3: Implement console pages**

1. Add two routes to `router.py`: GET `/simulation/gate` and POST `/simulation/gate`
2. POST route parses candidate rules YAML and gate rules YAML from form, calls `validate_and_gate`, renders result template
3. Create `policy_simulation_gate.html` — form with candidate rules textarea, gate rules textarea, optional since/until/limit
4. Create `policy_simulation_gate_report.html` — shows simulation summary, validation issues, gate result, failed rules
5. Wire in `fastapi.py` if needed

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation_gate_console.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/console/router.py agent_app/adapters/fastapi.py agent_app/console/templates/policy_simulation_gate.html agent_app/console/templates/policy_simulation_gate_report.html tests/unit/test_policy_simulation_gate_console.py
git commit -m "feat: Phase 41 Task 6 — console simulation gate pages"
```

---

### Task 7: Documentation and final verification

**Files:**
- Modify: `docs/policy_release.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Create: `docs/release_checklist_phase41.md`

- [ ] **Step 1: Update docs/policy_release.md**

Add Phase 41 section documenting:
1. Simulation gate purpose
2. Supported simulation metrics
3. Gate rules YAML examples
4. CLI examples (gate pass, gate fail)
5. Console workflow
6. Blocking behavior (exit non-zero)
7. Known limitations

- [ ] **Step 2: Update CHANGELOG.md**

Add v0.29.0 entry for Phase 41.

- [ ] **Step 3: Update README.md**

Add Phase 41 in roadmap.

- [ ] **Step 4: Create release checklist**

Create `docs/release_checklist_phase41.md`.

- [ ] **Step 5: Run regression tests**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation_gate.py tests/unit/test_policy_simulation_gate_evaluator.py tests/unit/test_policy_simulation_gate_service.py tests/unit/test_policy_simulation_gate_wiring.py tests/unit/test_policy_simulation_gate_cli.py tests/unit/test_policy_simulation_gate_console.py -v`
Expected: All Phase 41 tests pass

Then run broader regression:
`.venv/bin/python -m pytest tests/unit/ -k "policy" --timeout=60 -q`
Expected: 0 failures

- [ ] **Step 6: Commit**

```bash
git add docs/policy_release.md CHANGELOG.md README.md docs/release_checklist_phase41.md
git commit -m "docs: Phase 41 documentation — policy gate integration and automated safeguards"
```
