# Phase 43: Policy Rollout Automation with Simulation Gates — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade rollout execution from manual simulation gate blocking to automatic gate evaluation per step, with configurable failure actions (block/fail/skip).

**Architecture:** Extend RolloutStep with gate mode and failure action fields. Create RolloutGateAutomationService that orchestrates ensure/run/check per step by reusing ReleaseGateAutomationService and PolicySimulationService. Integrate into RolloutService.run_next_step() so AUTO steps automatically run simulation gates before execution.

**Tech Stack:** Python 3.12, Pydantic v2, asyncio, FastAPI/Jinja2 (optional console)

---

## File Structure

### New Files
| File | Responsibility |
|------|----------------|
| `agent_app/governance/policy_rollout_gate.py` | RolloutGateExecutionStatus, RolloutGateExecutionResult models |
| `agent_app/runtime/policy_rollout_gate_service.py` | RolloutGateAutomationService (ensure_step_gate, run_step_gate, check_step_gate) |
| `agent_app/console/templates/policy_rollout_gate.html` | Gate form/status page for rollout step |
| `agent_app/console/templates/policy_rollout_gate_status.html` | Gate execution result display |
| `tests/unit/test_policy_rollout_gate_model.py` | Model tests for RolloutGateExecutionResult |
| `tests/unit/test_policy_rollout_gate_service.py` | Service tests for RolloutGateAutomationService |
| `tests/unit/test_policy_rollout_gate_integration.py` | Integration tests for RolloutService + gate automation |
| `tests/unit/test_policy_rollout_gate_config.py` | Config/loader/RBAC/events tests |
| `tests/unit/test_policy_rollout_gate_cli.py` | CLI rollout gate command tests |
| `tests/unit/test_policy_rollout_gate_console.py` | Console rollout gate page tests |
| `docs/release_checklist_phase43.md` | Release checklist |

### Modified Files
| File | Changes |
|------|---------|
| `agent_app/governance/policy_rollout.py` | Add RolloutGateMode, RolloutGateFailureAction enums; extend RolloutStep with 8 new fields |
| `agent_app/governance/policy_rbac.py` | Add 3 rollout gate permissions |
| `agent_app/governance/policy_change_event.py` | Add 7 rollout gate event types |
| `agent_app/config/schema.py` | Add RolloutGateAutomationConfig + SimulationGateRuleConfig |
| `agent_app/config/loader.py` | Wire RolloutGateAutomationService |
| `agent_app/runtime/policy_rollout_service.py` | Accept rollout_gate_automation_service; integrate gate check in run_next_step(); update run_all_available() |
| `agent_app/cli.py` | Add rollout gate run/status/attach subcommands |
| `agent_app/console/router.py` | Add rollout gate routes |
| `agent_app/adapters/fastapi.py` | Wire rollout_gate_automation_service |
| `agent_app/app.py` | Expose rollout_gate_automation_service property |
| `docs/policy_release.md` | Phase 43 section |
| `CHANGELOG.md` | v0.31.0 entry |
| `README.md` | Phase 43 in roadmap |

---

### Task 1: Rollout Gate Enums and RolloutStep Extension

**Files:**
- Modify: `agent_app/governance/policy_rollout.py`
- Test: `tests/unit/test_policy_rollout.py` (extend existing or add targeted tests)

- [ ] **Step 1: Write failing tests for new enums and RolloutStep fields**

Add to a new test file `tests/unit/test_policy_rollout_gate_model.py`:

```python
"""Tests for Phase 43 rollout gate enums and RolloutStep extension."""
import pytest
from datetime import datetime, timezone


def test_rollout_gate_mode_values():
    from agent_app.governance.policy_rollout import RolloutGateMode
    assert RolloutGateMode.DISABLED == "disabled"
    assert RolloutGateMode.MANUAL == "manual"
    assert RolloutGateMode.AUTO == "auto"
    assert len(RolloutGateMode) == 3


def test_rollout_gate_failure_action_values():
    from agent_app.governance.policy_rollout import RolloutGateFailureAction
    assert RolloutGateFailureAction.BLOCK == "block"
    assert RolloutGateFailureAction.FAIL == "fail"
    assert RolloutGateFailureAction.SKIP == "skip"
    assert len(RolloutGateFailureAction) == 3


def test_rollout_step_default_gate_mode_disabled():
    from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType, RolloutGateMode
    step = RolloutStep(
        step_id="s1",
        step_type=RolloutStepType.ACTIVATE,
        environment="prod",
    )
    assert step.simulation_gate_mode == RolloutGateMode.DISABLED


def test_rollout_step_default_failure_action_block():
    from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType, RolloutGateFailureAction
    step = RolloutStep(
        step_id="s1",
        step_type=RolloutStepType.ACTIVATE,
        environment="prod",
    )
    assert step.simulation_gate_failure_action == RolloutGateFailureAction.BLOCK


def test_rollout_step_new_fields_default():
    from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType
    step = RolloutStep(
        step_id="s1",
        step_type=RolloutStepType.ACTIVATE,
        environment="prod",
    )
    assert step.simulation_candidate_rules == []
    assert step.simulation_gate_rules == []
    assert step.simulation_window_start is None
    assert step.simulation_window_end is None
    assert step.simulation_limit is None
    assert step.simulation_include_base is True
    assert step.simulation_gate_max_age_seconds is None


def test_rollout_step_with_auto_gate():
    from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType, RolloutGateMode, RolloutGateFailureAction
    step = RolloutStep(
        step_id="s1",
        step_type=RolloutStepType.ASSIGN_RING,
        environment="prod",
        ring_name="canary",
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.AUTO,
        simulation_gate_failure_action=RolloutGateFailureAction.FAIL,
        simulation_limit=1000,
    )
    assert step.simulation_gate_mode == RolloutGateMode.AUTO
    assert step.simulation_gate_failure_action == RolloutGateFailureAction.FAIL
    assert step.simulation_limit == 1000


def test_rollout_step_backward_compat_phase42_fields():
    """Phase 42 fields must still exist and work."""
    from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType
    step = RolloutStep(
        step_id="s1",
        step_type=RolloutStepType.ACTIVATE,
        environment="prod",
        requires_simulation_gate=True,
        simulation_gate_requirement_id="rgr_abc",
        simulation_gate_result_id="gr_def",
    )
    assert step.requires_simulation_gate is True
    assert step.simulation_gate_requirement_id == "rgr_abc"
    assert step.simulation_gate_result_id == "gr_def"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_gate_model.py -v`
Expected: FAIL — RolloutGateMode and RolloutGateFailureAction not yet defined

- [ ] **Step 3: Implement enums and extend RolloutStep**

Add to `agent_app/governance/policy_rollout.py` after the existing `RolloutStepType` enum:

```python
class RolloutGateMode(StrEnum):
    """Gate automation mode for rollout steps."""
    DISABLED = "disabled"
    MANUAL = "manual"
    AUTO = "auto"


class RolloutGateFailureAction(StrEnum):
    """Action to take when simulation gate fails for a rollout step."""
    BLOCK = "block"
    FAIL = "fail"
    SKIP = "skip"
```

Add to `RolloutStep` after the existing Phase 42 fields (`simulation_gate_result_id`), before `started_at`:

```python
    # Phase 43: Rollout gate automation
    simulation_gate_mode: RolloutGateMode = Field(
        default=RolloutGateMode.DISABLED,
        description="Gate automation mode: disabled, manual, or auto (Phase 43)",
    )
    simulation_gate_failure_action: RolloutGateFailureAction = Field(
        default=RolloutGateFailureAction.BLOCK,
        description="Action when gate fails: block, fail, or skip (Phase 43)",
    )
    simulation_candidate_rules: list[Any] = Field(
        default_factory=list,
        description="Candidate runtime policy rules for auto gate (Phase 43)",
    )
    simulation_gate_rules: list[Any] = Field(
        default_factory=list,
        description="Gate rules for auto gate evaluation (Phase 43)",
    )
    simulation_window_start: datetime | None = Field(
        default=None,
        description="Audit window start for simulation (Phase 43)",
    )
    simulation_window_end: datetime | None = Field(
        default=None,
        description="Audit window end for simulation (Phase 43)",
    )
    simulation_limit: int | None = Field(
        default=None,
        description="Max audit cases for simulation (Phase 43)",
    )
    simulation_include_base: bool = Field(
        default=True,
        description="Include base rules alongside candidates in simulation (Phase 43)",
    )
    simulation_gate_max_age_seconds: int | None = Field(
        default=None,
        description="Max age in seconds for gate result freshness (Phase 43)",
    )
```

Note: Use `list[Any]` for `simulation_candidate_rules` and `simulation_gate_rules` to avoid circular imports. The service layer will validate/cast them at runtime.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_gate_model.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Run existing rollout tests to verify backward compatibility**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout.py tests/unit/test_policy_rollout_service.py tests/unit/test_policy_rollout_approval_policy.py tests/unit/test_policy_release_gate_integration.py -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
git add agent_app/governance/policy_rollout.py tests/unit/test_policy_rollout_gate_model.py
git commit -m "feat: Phase 43 Task 1 — RolloutGateMode, RolloutGateFailureAction enums and RolloutStep extension"
```

---

### Task 2: Rollout Gate Execution Result Model

**Files:**
- Create: `agent_app/governance/policy_rollout_gate.py`
- Test: `tests/unit/test_policy_rollout_gate_model.py` (append tests)

- [ ] **Step 1: Write failing tests for RolloutGateExecutionResult**

Append to `tests/unit/test_policy_rollout_gate_model.py`:

```python
def test_rollout_gate_execution_status_values():
    from agent_app.governance.policy_rollout_gate import RolloutGateExecutionStatus
    assert RolloutGateExecutionStatus.NOT_REQUIRED == "not_required"
    assert RolloutGateExecutionStatus.SATISFIED == "satisfied"
    assert RolloutGateExecutionStatus.BLOCKED == "blocked"
    assert RolloutGateExecutionStatus.FAILED == "failed"
    assert RolloutGateExecutionStatus.SKIPPED == "skipped"
    assert RolloutGateExecutionStatus.ERROR == "error"
    assert len(RolloutGateExecutionStatus) == 6


def test_rollout_gate_execution_result_valid():
    from agent_app.governance.policy_rollout_gate import RolloutGateExecutionResult, RolloutGateExecutionStatus
    result = RolloutGateExecutionResult(
        execution_id="rge_abc123",
        rollout_id="ro_xyz",
        step_id="s1",
        status=RolloutGateExecutionStatus.SATISFIED,
        created_at=datetime.now(timezone.utc),
    )
    assert result.execution_id == "rge_abc123"
    assert result.status == RolloutGateExecutionStatus.SATISFIED
    assert result.requirement_id is None
    assert result.gate_result_id is None
    assert result.simulation_id is None


def test_rollout_gate_execution_result_id_prefix():
    from agent_app.governance.policy_rollout_gate import RolloutGateExecutionResult, RolloutGateExecutionStatus
    with pytest.raises(ValueError):
        RolloutGateExecutionResult(
            execution_id="bad_prefix",
            rollout_id="ro_xyz",
            step_id="s1",
            status=RolloutGateExecutionStatus.SATISFIED,
            created_at=datetime.now(timezone.utc),
        )


def test_rollout_gate_execution_result_tz_aware():
    from agent_app.governance.policy_rollout_gate import RolloutGateExecutionResult, RolloutGateExecutionStatus
    with pytest.raises(ValueError):
        RolloutGateExecutionResult(
            execution_id="rge_abc",
            rollout_id="ro_xyz",
            step_id="s1",
            status=RolloutGateExecutionStatus.SATISFIED,
            created_at=datetime(2026, 1, 1),  # naive datetime
        )


def test_rollout_gate_execution_result_with_all_fields():
    from agent_app.governance.policy_rollout_gate import RolloutGateExecutionResult, RolloutGateExecutionStatus
    result = RolloutGateExecutionResult(
        execution_id="rge_abc",
        rollout_id="ro_xyz",
        step_id="s1",
        status=RolloutGateExecutionStatus.BLOCKED,
        requirement_id="rgr_def",
        gate_result_id="gr_ghi",
        simulation_id="psim_jkl",
        action_taken="gate_blocked",
        reason="Gate result expired",
        error={"type": "gate_expired"},
        created_at=datetime.now(timezone.utc),
        metadata={"max_age_seconds": 86400},
    )
    assert result.requirement_id == "rgr_def"
    assert result.action_taken == "gate_blocked"
    assert result.reason == "Gate result expired"
    assert result.error == {"type": "gate_expired"}
    assert result.metadata == {"max_age_seconds": 86400}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_gate_model.py::test_rollout_gate_execution_status_values -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement RolloutGateExecutionResult model**

Create `agent_app/governance/policy_rollout_gate.py`:

```python
"""Rollout gate execution models — results of gate evaluation per rollout step.

Phase 43: Models for tracking rollout step gate automation outcomes.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class RolloutGateExecutionStatus(StrEnum):
    """Status of a rollout step gate execution."""
    NOT_REQUIRED = "not_required"
    SATISFIED = "satisfied"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class RolloutGateExecutionResult(BaseModel):
    """Result of evaluating a simulation gate for a rollout step.

    Captures the outcome of ensure_step_gate, run_step_gate, and
    check_step_gate operations.
    """

    execution_id: str = Field(..., description="Unique execution result ID (rge_ prefix)")
    rollout_id: str = Field(..., description="Rollout plan ID")
    step_id: str = Field(..., description="Step ID within the rollout")
    status: RolloutGateExecutionStatus = Field(..., description="Gate execution status")
    requirement_id: str | None = Field(default=None, description="Gate requirement ID")
    gate_result_id: str | None = Field(default=None, description="Gate result ID")
    simulation_id: str | None = Field(default=None, description="Simulation report ID")
    action_taken: str | None = Field(default=None, description="Action taken by automation")
    reason: str | None = Field(default=None, description="Human-readable reason")
    error: dict[str, Any] | None = Field(default=None, description="Error details if status=ERROR")
    created_at: datetime = Field(..., description="Timezone-aware creation timestamp")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

    @field_validator("execution_id")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        if not v.startswith("rge_"):
            raise ValueError("execution_id must use rge_ prefix")
        return v

    @field_validator("created_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return v
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_gate_model.py -v`
Expected: All 13 tests PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/governance/policy_rollout_gate.py tests/unit/test_policy_rollout_gate_model.py
git commit -m "feat: Phase 43 Task 2 — RolloutGateExecutionStatus and RolloutGateExecutionResult models"
```

---

### Task 3: RolloutGateAutomationService

**Files:**
- Create: `agent_app/runtime/policy_rollout_gate_service.py`
- Test: `tests/unit/test_policy_rollout_gate_service.py`

- [ ] **Step 1: Write failing tests for RolloutGateAutomationService**

Create `tests/unit/test_policy_rollout_gate_service.py`:

```python
"""Tests for RolloutGateAutomationService — Phase 43."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from agent_app.core.context import RunContext
from agent_app.governance.policy_rollout import (
    RolloutPlan,
    RolloutStep,
    RolloutStepType,
    RolloutPlanStatus,
    RolloutGateMode,
    RolloutGateFailureAction,
)
from agent_app.governance.policy_rollout_gate import RolloutGateExecutionStatus
from agent_app.governance.policy_release_gate import (
    ReleaseGateRequirement,
    ReleaseGateRequirementStatus,
)
from agent_app.runtime.policy_rollout_gate_service import RolloutGateAutomationService


def _make_step(**overrides) -> RolloutStep:
    defaults = dict(
        step_id="s1",
        step_type=RolloutStepType.ASSIGN_RING,
        environment="prod",
        ring_name="canary",
    )
    defaults.update(overrides)
    return RolloutStep(**defaults)


def _make_plan(steps=None, **overrides) -> RolloutPlan:
    now = datetime.now(timezone.utc)
    defaults = dict(
        rollout_id="ro_test",
        name="test plan",
        bundle_id="pb_test",
        status=RolloutPlanStatus.ACTIVE,
        steps=steps or [_make_step()],
        created_by="admin",
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return RolloutPlan(**defaults)


def _make_context() -> RunContext:
    return RunContext(run_id="r1", user_id="admin", tenant_id="t1")


def _make_requirement(status=ReleaseGateRequirementStatus.REQUIRED, **overrides) -> ReleaseGateRequirement:
    defaults = dict(
        requirement_id="rgr_test",
        source_type="rollout_step",
        source_id="ro_test:s1",
        status=status,
        max_age_seconds=None,
        metadata={},
    )
    defaults.update(overrides)
    return ReleaseGateRequirement(**defaults)


# --- ensure_step_gate ---

@pytest.mark.asyncio
async def test_ensure_not_required_when_gate_disabled():
    """DISABLED mode returns NOT_REQUIRED."""
    release_gate = AsyncMock()
    svc = RolloutGateAutomationService(release_gate_automation_service=release_gate)
    step = _make_step(simulation_gate_mode=RolloutGateMode.DISABLED)
    plan = _make_plan(steps=[step])
    result = await svc.ensure_step_gate(plan, step, _make_context())
    assert result.status == RolloutGateExecutionStatus.NOT_REQUIRED


@pytest.mark.asyncio
async def test_ensure_satisfied_when_fresh_existing():
    """If existing requirement is SATISFIED and fresh, return SATISFIED."""
    release_gate = AsyncMock()
    req = _make_requirement(status=ReleaseGateRequirementStatus.SATISFIED)
    release_gate.check_requirement.return_value = req
    svc = RolloutGateAutomationService(release_gate_automation_service=release_gate)
    step = _make_step(
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.MANUAL,
    )
    plan = _make_plan(steps=[step])
    result = await svc.ensure_step_gate(plan, step, _make_context())
    assert result.status == RolloutGateExecutionStatus.SATISFIED


@pytest.mark.asyncio
async def test_ensure_manual_missing_gate_returns_blocked():
    """MANUAL mode with missing gate requirement returns BLOCKED."""
    release_gate = AsyncMock()
    req = _make_requirement(status=ReleaseGateRequirementStatus.REQUIRED)
    release_gate.check_requirement.return_value = req
    svc = RolloutGateAutomationService(release_gate_automation_service=release_gate)
    step = _make_step(
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.MANUAL,
    )
    plan = _make_plan(steps=[step])
    result = await svc.ensure_step_gate(plan, step, _make_context())
    assert result.status == RolloutGateExecutionStatus.BLOCKED


@pytest.mark.asyncio
async def test_ensure_manual_failed_gate_returns_blocked():
    """MANUAL mode with FAILED requirement returns BLOCKED."""
    release_gate = AsyncMock()
    req = _make_requirement(status=ReleaseGateRequirementStatus.FAILED)
    release_gate.check_requirement.return_value = req
    svc = RolloutGateAutomationService(release_gate_automation_service=release_gate)
    step = _make_step(
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.MANUAL,
    )
    plan = _make_plan(steps=[step])
    result = await svc.ensure_step_gate(plan, step, _make_context())
    assert result.status == RolloutGateExecutionStatus.BLOCKED


@pytest.mark.asyncio
async def test_ensure_manual_expired_gate_returns_blocked():
    """MANUAL mode with EXPIRED requirement returns BLOCKED."""
    release_gate = AsyncMock()
    req = _make_requirement(status=ReleaseGateRequirementStatus.EXPIRED)
    release_gate.check_requirement.return_value = req
    svc = RolloutGateAutomationService(release_gate_automation_service=release_gate)
    step = _make_step(
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.MANUAL,
    )
    plan = _make_plan(steps=[step])
    result = await svc.ensure_step_gate(plan, step, _make_context())
    assert result.status == RolloutGateExecutionStatus.BLOCKED


@pytest.mark.asyncio
async def test_ensure_auto_pass_returns_satisfied():
    """AUTO mode runs simulation, gate passes, returns SATISFIED."""
    release_gate = AsyncMock()
    # check_requirement returns REQUIRED initially
    req = _make_requirement(status=ReleaseGateRequirementStatus.REQUIRED)
    release_gate.check_requirement.return_value = req
    # run_and_attach returns SATISFIED
    satisfied_req = _make_requirement(status=ReleaseGateRequirementStatus.SATISFIED)
    satisfied_req.gate_result_id = "gr_pass"
    satisfied_req.simulation_id = "psim_1"
    release_gate.run_and_attach_simulation_gate_for_promotion.return_value = satisfied_req
    # require_gate_for_promotion creates requirement
    release_gate.require_gate_for_promotion.return_value = req

    svc = RolloutGateAutomationService(
        release_gate_automation_service=release_gate,
    )
    step = _make_step(
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.AUTO,
        simulation_candidate_rules=[{"name": "rule1"}],
        simulation_gate_rules=[{"name": "gate1"}],
    )
    plan = _make_plan(steps=[step])
    result = await svc.ensure_step_gate(plan, step, _make_context())
    assert result.status == RolloutGateExecutionStatus.SATISFIED


@pytest.mark.asyncio
async def test_ensure_auto_fail_with_block_action():
    """AUTO mode, gate fails, failure_action=BLOCK → returns BLOCKED."""
    release_gate = AsyncMock()
    req = _make_requirement(status=ReleaseGateRequirementStatus.REQUIRED)
    release_gate.check_requirement.return_value = req
    failed_req = _make_requirement(status=ReleaseGateRequirementStatus.FAILED)
    release_gate.run_and_attach_simulation_gate_for_promotion.return_value = failed_req
    release_gate.require_gate_for_promotion.return_value = req

    svc = RolloutGateAutomationService(
        release_gate_automation_service=release_gate,
    )
    step = _make_step(
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.AUTO,
        simulation_gate_failure_action=RolloutGateFailureAction.BLOCK,
        simulation_candidate_rules=[{"name": "rule1"}],
        simulation_gate_rules=[{"name": "gate1"}],
    )
    plan = _make_plan(steps=[step])
    result = await svc.ensure_step_gate(plan, step, _make_context())
    assert result.status == RolloutGateExecutionStatus.BLOCKED


@pytest.mark.asyncio
async def test_ensure_auto_fail_with_fail_action():
    """AUTO mode, gate fails, failure_action=FAIL → returns FAILED."""
    release_gate = AsyncMock()
    req = _make_requirement(status=ReleaseGateRequirementStatus.REQUIRED)
    release_gate.check_requirement.return_value = req
    failed_req = _make_requirement(status=ReleaseGateRequirementStatus.FAILED)
    release_gate.run_and_attach_simulation_gate_for_promotion.return_value = failed_req
    release_gate.require_gate_for_promotion.return_value = req

    svc = RolloutGateAutomationService(
        release_gate_automation_service=release_gate,
    )
    step = _make_step(
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.AUTO,
        simulation_gate_failure_action=RolloutGateFailureAction.FAIL,
        simulation_candidate_rules=[{"name": "rule1"}],
        simulation_gate_rules=[{"name": "gate1"}],
    )
    plan = _make_plan(steps=[step])
    result = await svc.ensure_step_gate(plan, step, _make_context())
    assert result.status == RolloutGateExecutionStatus.FAILED


@pytest.mark.asyncio
async def test_ensure_auto_fail_with_skip_action():
    """AUTO mode, gate fails, failure_action=SKIP → returns SKIPPED."""
    release_gate = AsyncMock()
    req = _make_requirement(status=ReleaseGateRequirementStatus.REQUIRED)
    release_gate.check_requirement.return_value = req
    failed_req = _make_requirement(status=ReleaseGateRequirementStatus.FAILED)
    release_gate.run_and_attach_simulation_gate_for_promotion.return_value = failed_req
    release_gate.require_gate_for_promotion.return_value = req

    svc = RolloutGateAutomationService(
        release_gate_automation_service=release_gate,
    )
    step = _make_step(
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.AUTO,
        simulation_gate_failure_action=RolloutGateFailureAction.SKIP,
        simulation_candidate_rules=[{"name": "rule1"}],
        simulation_gate_rules=[{"name": "gate1"}],
    )
    plan = _make_plan(steps=[step])
    result = await svc.ensure_step_gate(plan, step, _make_context())
    assert result.status == RolloutGateExecutionStatus.SKIPPED


@pytest.mark.asyncio
async def test_ensure_auto_error_returns_error():
    """AUTO mode, simulation throws, returns ERROR."""
    release_gate = AsyncMock()
    req = _make_requirement(status=ReleaseGateRequirementStatus.REQUIRED)
    release_gate.check_requirement.return_value = req
    release_gate.run_and_attach_simulation_gate_for_promotion.side_effect = RuntimeError("Sim failed")
    release_gate.require_gate_for_promotion.return_value = req

    svc = RolloutGateAutomationService(
        release_gate_automation_service=release_gate,
    )
    step = _make_step(
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.AUTO,
        simulation_candidate_rules=[{"name": "rule1"}],
        simulation_gate_rules=[{"name": "gate1"}],
    )
    plan = _make_plan(steps=[step])
    result = await svc.ensure_step_gate(plan, step, _make_context())
    assert result.status == RolloutGateExecutionStatus.ERROR
    assert result.error is not None


# --- check_step_gate ---

@pytest.mark.asyncio
async def test_check_not_required():
    """Check on DISABLED step returns NOT_REQUIRED."""
    release_gate = AsyncMock()
    svc = RolloutGateAutomationService(release_gate_automation_service=release_gate)
    step = _make_step(simulation_gate_mode=RolloutGateMode.DISABLED)
    plan = _make_plan(steps=[step])
    result = await svc.check_step_gate(plan, step)
    assert result.status == RolloutGateExecutionStatus.NOT_REQUIRED


@pytest.mark.asyncio
async def test_check_satisfied():
    """Check on step with SATISFIED requirement returns SATISFIED."""
    release_gate = AsyncMock()
    req = _make_requirement(status=ReleaseGateRequirementStatus.SATISFIED)
    release_gate.check_requirement.return_value = req
    svc = RolloutGateAutomationService(release_gate_automation_service=release_gate)
    step = _make_step(requires_simulation_gate=True, simulation_gate_mode=RolloutGateMode.MANUAL)
    plan = _make_plan(steps=[step])
    result = await svc.check_step_gate(plan, step)
    assert result.status == RolloutGateExecutionStatus.SATISFIED


@pytest.mark.asyncio
async def test_check_required_returns_blocked():
    """Check on step with REQUIRED (no result) returns BLOCKED."""
    release_gate = AsyncMock()
    req = _make_requirement(status=ReleaseGateRequirementStatus.REQUIRED)
    release_gate.check_requirement.return_value = req
    svc = RolloutGateAutomationService(release_gate_automation_service=release_gate)
    step = _make_step(requires_simulation_gate=True, simulation_gate_mode=RolloutGateMode.MANUAL)
    plan = _make_plan(steps=[step])
    result = await svc.check_step_gate(plan, step)
    assert result.status == RolloutGateExecutionStatus.BLOCKED


# --- run_step_gate ---

@pytest.mark.asyncio
async def test_run_step_gate_creates_requirement_and_runs():
    """run_step_gate creates requirement and runs simulation."""
    release_gate = AsyncMock()
    req = _make_requirement(status=ReleaseGateRequirementStatus.REQUIRED)
    release_gate.require_gate_for_promotion.return_value = req
    satisfied_req = _make_requirement(status=ReleaseGateRequirementStatus.SATISFIED)
    satisfied_req.gate_result_id = "gr_pass"
    satisfied_req.simulation_id = "psim_1"
    satisfied_req.requirement_id = "rgr_test"
    release_gate.run_and_attach_simulation_gate_for_promotion.return_value = satisfied_req

    svc = RolloutGateAutomationService(
        release_gate_automation_service=release_gate,
    )
    step = _make_step(
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.AUTO,
        simulation_candidate_rules=[{"name": "rule1"}],
        simulation_gate_rules=[{"name": "gate1"}],
    )
    plan = _make_plan(steps=[step])
    result = await svc.run_step_gate(plan, step, _make_context())
    assert result.status == RolloutGateExecutionStatus.SATISFIED
    assert result.requirement_id == "rgr_test"


@pytest.mark.asyncio
async def test_run_step_gate_no_rules_raises():
    """run_step_gate with no candidate/gate rules raises ValueError."""
    release_gate = AsyncMock()
    svc = RolloutGateAutomationService(
        release_gate_automation_service=release_gate,
    )
    step = _make_step(
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.AUTO,
        simulation_candidate_rules=[],
        simulation_gate_rules=[],
    )
    plan = _make_plan(steps=[step])
    with pytest.raises(ValueError, match="candidate_rules and gate_rules"):
        await svc.run_step_gate(plan, step, _make_context())


# --- audit events ---

@pytest.mark.asyncio
async def test_ensure_emits_audit_events():
    """ensure_step_gate emits audit and change events."""
    from agent_app.governance.audit import InMemoryAuditLogger
    from agent_app.runtime.policy_change_event_store import InMemoryPolicyChangeEventStore

    audit = InMemoryAuditLogger()
    event_store = InMemoryPolicyChangeEventStore()

    release_gate = AsyncMock()
    req = _make_requirement(status=ReleaseGateRequirementStatus.REQUIRED)
    release_gate.check_requirement.return_value = req
    satisfied_req = _make_requirement(status=ReleaseGateRequirementStatus.SATISFIED)
    satisfied_req.gate_result_id = "gr_pass"
    satisfied_req.simulation_id = "psim_1"
    release_gate.run_and_attach_simulation_gate_for_promotion.return_value = satisfied_req
    release_gate.require_gate_for_promotion.return_value = req

    svc = RolloutGateAutomationService(
        release_gate_automation_service=release_gate,
        audit_logger=audit,
        event_store=event_store,
    )
    step = _make_step(
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.AUTO,
        simulation_candidate_rules=[{"name": "r1"}],
        simulation_gate_rules=[{"name": "g1"}],
    )
    plan = _make_plan(steps=[step])
    await svc.ensure_step_gate(plan, step, _make_context())

    events = audit.list_events()
    gate_events = [e for e in events if "rollout.gate" in e.event_type]
    assert len(gate_events) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_gate_service.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement RolloutGateAutomationService**

Create `agent_app/runtime/policy_rollout_gate_service.py`:

```python
"""RolloutGateAutomationService — orchestrates simulation gate evaluation per rollout step.

Phase 43: Automates gate evaluation for rollout steps with DISABLED/MANUAL/AUTO modes
and BLOCK/FAIL/SKIP failure actions.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from agent_app.core.context import RunContext
from agent_app.governance.policy_rollout import (
    RolloutGateFailureAction,
    RolloutGateMode,
    RolloutPlan,
    RolloutStep,
)
from agent_app.governance.policy_release_gate import ReleaseGateRequirementStatus
from agent_app.governance.policy_rollout_gate import (
    RolloutGateExecutionResult,
    RolloutGateExecutionStatus,
)


class RolloutGateAutomationService:
    """Orchestrates simulation gate evaluation for rollout steps.

    Delegates to ReleaseGateAutomationService for requirement management
    and simulation execution. Provides ensure/run/check step gate methods
    that the RolloutService calls during step execution.
    """

    def __init__(
        self,
        release_gate_automation_service: Any,
        simulation_service: Any | None = None,
        simulation_gate_evaluator: Any | None = None,
        audit_logger: Any | None = None,
        event_store: Any | None = None,
        default_gate_rules: list[Any] | None = None,
        default_max_age_seconds: int | None = None,
    ) -> None:
        self._release_gate = release_gate_automation_service
        self._simulation_service = simulation_service
        self._simulation_gate_evaluator = simulation_gate_evaluator
        self._audit_logger = audit_logger
        self._event_store = event_store
        self._default_gate_rules = default_gate_rules or []
        self._default_max_age_seconds = default_max_age_seconds

    async def ensure_step_gate(
        self,
        rollout: RolloutPlan,
        step: RolloutStep,
        context: RunContext,
    ) -> RolloutGateExecutionResult:
        """Ensure the step's gate requirement is satisfied.

        * If gate is not required (DISABLED), return NOT_REQUIRED.
        * If existing attached requirement is SATISFIED and fresh, return SATISFIED.
        * If MANUAL mode, return BLOCKED when missing/failed/expired.
        * If AUTO mode, run simulation gate and attach result.
        * If gate passes, return SATISFIED.
        * If gate fails, apply simulation_gate_failure_action.
        * If error occurs, return ERROR.
        """
        source_id = f"{rollout.rollout_id}:{step.step_id}"

        # Gate not required
        if not step.requires_simulation_gate and step.simulation_gate_mode == RolloutGateMode.DISABLED:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.NOT_REQUIRED,
                action_taken="gate_disabled",
            )

        # Check existing requirement
        try:
            existing = await self._release_gate.check_requirement(
                "rollout_step", source_id,
            )
        except Exception as exc:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.ERROR,
                error={"type": "check_error", "message": str(exc)},
            )

        # Already satisfied
        if existing.status == ReleaseGateRequirementStatus.SATISFIED:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.SATISFIED,
                requirement_id=existing.requirement_id if existing.requirement_id != "rgr_none" else None,
                gate_result_id=existing.gate_result_id,
                simulation_id=existing.simulation_id,
                action_taken="existing_satisfied",
            )

        # MANUAL mode — cannot auto-run, block
        if step.simulation_gate_mode == RolloutGateMode.MANUAL:
            await self._emit_events(rollout, step, "policy.rollout.gate.blocked", context)
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.BLOCKED,
                requirement_id=existing.requirement_id if existing.requirement_id != "rgr_none" else None,
                action_taken="manual_blocked",
                reason=f"Gate is {existing.status.value}, manual mode requires explicit gate result",
            )

        # AUTO mode — run simulation
        if step.simulation_gate_mode == RolloutGateMode.AUTO:
            try:
                run_result = await self.run_step_gate(rollout, step, context)
            except Exception as exc:
                await self._emit_events(rollout, step, "policy.rollout.gate.blocked", context)
                return self._make_result(
                    rollout, step, RolloutGateExecutionStatus.ERROR,
                    error={"type": "run_error", "message": str(exc)},
                    action_taken="auto_error",
                )

            if run_result.status == RolloutGateExecutionStatus.SATISFIED:
                await self._emit_events(rollout, step, "policy.rollout.gate.satisfied", context)
                return run_result

            # Gate failed — apply failure action
            if run_result.status == RolloutGateExecutionStatus.FAILED:
                await self._emit_events(rollout, step, "policy.rollout.gate.failed", context)
                return run_result

            if run_result.status == RolloutGateExecutionStatus.BLOCKED:
                await self._emit_events(rollout, step, "policy.rollout.gate.blocked", context)
                return run_result

            if run_result.status == RolloutGateExecutionStatus.SKIPPED:
                await self._emit_events(rollout, step, "policy.rollout.gate.skipped", context)
                return run_result

            return run_result

        # Fallback: block if gate is in a bad state
        await self._emit_events(rollout, step, "policy.rollout.gate.blocked", context)
        return self._make_result(
            rollout, step, RolloutGateExecutionStatus.BLOCKED,
            requirement_id=existing.requirement_id if existing.requirement_id != "rgr_none" else None,
            action_taken="fallback_blocked",
            reason=f"Gate is {existing.status.value}",
        )

    async def run_step_gate(
        self,
        rollout: RolloutPlan,
        step: RolloutStep,
        context: RunContext,
    ) -> RolloutGateExecutionResult:
        """Run simulation gate for a step and attach the result.

        Uses candidate rules and gate rules from the step (or defaults).
        Creates or reuses a release gate requirement for the step.
        """
        source_id = f"{rollout.rollout_id}:{step.step_id}"

        # Resolve rules: step-level or defaults
        candidate_rules = step.simulation_candidate_rules or []
        gate_rules = step.simulation_gate_rules or self._default_gate_rules

        if not candidate_rules or not gate_rules:
            raise ValueError(
                "candidate_rules and gate_rules must be provided either on the step "
                "or as defaults to run simulation gate"
            )

        # Create requirement if not exists
        try:
            existing = await self._release_gate.check_requirement(
                "rollout_step", source_id,
            )
            if existing.requirement_id == "rgr_none" or existing.status == ReleaseGateRequirementStatus.NOT_REQUIRED:
                await self._release_gate.require_gate_for_promotion(
                    promotion_id=source_id,
                    max_age_seconds=step.simulation_gate_max_age_seconds or self._default_max_age_seconds,
                    metadata={"rollout_id": rollout.rollout_id, "step_id": step.step_id},
                )
        except Exception:
            pass  # Requirement may already exist

        await self._emit_events(rollout, step, "policy.rollout.gate.run", context)

        # Run simulation + gate
        try:
            from agent_app.governance.runtime_policy import RuntimePolicyRule
            from agent_app.governance.policy_gate import PolicyGateRule

            # Cast dict rules to proper types if needed
            cast_candidates = self._cast_candidate_rules(candidate_rules)
            cast_gates = self._cast_gate_rules(gate_rules)

            req = await self._release_gate.run_and_attach_simulation_gate_for_promotion(
                promotion_id=source_id,
                candidate_rules=cast_candidates,
                gate_rules=cast_gates,
                context=context,
                include_base=step.simulation_include_base,
                window_start=step.simulation_window_start,
                window_end=step.simulation_window_end,
                limit=step.simulation_limit,
            )
        except Exception as exc:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.ERROR,
                error={"type": "simulation_error", "message": str(exc)},
                action_taken="simulation_failed",
            )

        # Determine result based on requirement status and failure action
        if req.status == ReleaseGateRequirementStatus.SATISFIED:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.SATISFIED,
                requirement_id=req.requirement_id,
                gate_result_id=req.gate_result_id,
                simulation_id=req.simulation_id,
                action_taken="auto_passed",
            )

        # Gate failed — apply failure action
        failure_action = step.simulation_gate_failure_action
        if failure_action == RolloutGateFailureAction.FAIL:
            status = RolloutGateExecutionStatus.FAILED
            action = "auto_failed"
        elif failure_action == RolloutGateFailureAction.SKIP:
            status = RolloutGateExecutionStatus.SKIPPED
            action = "auto_skipped"
        else:
            status = RolloutGateExecutionStatus.BLOCKED
            action = "auto_blocked"

        return self._make_result(
            rollout, step, status,
            requirement_id=req.requirement_id,
            gate_result_id=req.gate_result_id,
            simulation_id=req.simulation_id,
            action_taken=action,
            reason=f"Gate failed with status {req.status.value}",
        )

    async def check_step_gate(
        self,
        rollout: RolloutPlan,
        step: RolloutStep,
        now: datetime | None = None,
    ) -> RolloutGateExecutionResult:
        """Check step gate status without running simulation.

        Uses existing requirement if present. Checks max age / failed /
        expired / satisfied status. Does not run simulation.
        """
        source_id = f"{rollout.rollout_id}:{step.step_id}"

        # Gate not required
        if not step.requires_simulation_gate and step.simulation_gate_mode == RolloutGateMode.DISABLED:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.NOT_REQUIRED,
                action_taken="gate_disabled",
            )

        # Check existing requirement
        try:
            existing = await self._release_gate.check_requirement(
                "rollout_step", source_id, now=now,
            )
        except Exception as exc:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.ERROR,
                error={"type": "check_error", "message": str(exc)},
            )

        if existing.status == ReleaseGateRequirementStatus.NOT_REQUIRED:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.BLOCKED,
                action_taken="no_requirement",
                reason="No gate requirement found for step",
            )

        if existing.status == ReleaseGateRequirementStatus.SATISFIED:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.SATISFIED,
                requirement_id=existing.requirement_id if existing.requirement_id != "rgr_none" else None,
                gate_result_id=existing.gate_result_id,
                simulation_id=existing.simulation_id,
                action_taken="existing_satisfied",
            )

        if existing.status == ReleaseGateRequirementStatus.REQUIRED:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.BLOCKED,
                requirement_id=existing.requirement_id if existing.requirement_id != "rgr_none" else None,
                action_taken="no_result_attached",
                reason="Gate is required but no result attached",
            )

        if existing.status == ReleaseGateRequirementStatus.FAILED:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.BLOCKED,
                requirement_id=existing.requirement_id if existing.requirement_id != "rgr_none" else None,
                action_taken="gate_failed",
                reason="Gate result indicates failure",
            )

        if existing.status == ReleaseGateRequirementStatus.EXPIRED:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.BLOCKED,
                requirement_id=existing.requirement_id if existing.requirement_id != "rgr_none" else None,
                action_taken="gate_expired",
                reason="Gate result has expired",
            )

        return self._make_result(
            rollout, step, RolloutGateExecutionStatus.BLOCKED,
            requirement_id=existing.requirement_id if existing.requirement_id != "rgr_none" else None,
            reason=f"Gate status: {existing.status.value}",
        )

    # --- Helpers ---

    def _make_result(
        self,
        rollout: RolloutPlan,
        step: RolloutStep,
        status: RolloutGateExecutionStatus,
        *,
        requirement_id: str | None = None,
        gate_result_id: str | None = None,
        simulation_id: str | None = None,
        action_taken: str | None = None,
        reason: str | None = None,
        error: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RolloutGateExecutionResult:
        return RolloutGateExecutionResult(
            execution_id=f"rge_{uuid.uuid4().hex[:12]}",
            rollout_id=rollout.rollout_id,
            step_id=step.step_id,
            status=status,
            requirement_id=requirement_id,
            gate_result_id=gate_result_id,
            simulation_id=simulation_id,
            action_taken=action_taken,
            reason=reason,
            error=error,
            created_at=datetime.now(timezone.utc),
            metadata=metadata or {},
        )

    async def _emit_events(
        self,
        rollout: RolloutPlan,
        step: RolloutStep,
        event_type: str,
        context: RunContext | None = None,
    ) -> None:
        """Emit audit and change events (best-effort)."""
        data = {
            "rollout_id": rollout.rollout_id,
            "step_id": step.step_id,
        }
        # Audit event
        if self._audit_logger is not None:
            try:
                from agent_app.governance.audit import AuditEvent
                event = AuditEvent(
                    event_id=f"ae_{uuid.uuid4().hex[:12]}",
                    event_type=event_type,
                    user_id=getattr(context, "user_id", None) if context else None,
                    tenant_id=getattr(context, "tenant_id", None) if context else None,
                    data=data,
                )
                await self._audit_logger.log(event)
            except Exception:
                pass

        # Change event
        if self._event_store is not None:
            try:
                from agent_app.governance.policy_change_event import PolicyChangeEvent
                event = PolicyChangeEvent(
                    event_id=f"pce_{uuid.uuid4().hex[:12]}",
                    event_type=event_type,
                    bundle_id=rollout.bundle_id,
                    environment=step.environment,
                    ring_name=step.ring_name,
                    actor_id=getattr(context, "user_id", None) if context else None,
                    data=data,
                    created_at=datetime.now(timezone.utc),
                )
                await self._event_store.append(event)
            except Exception:
                pass

    @staticmethod
    def _cast_candidate_rules(rules: list[Any]) -> list[Any]:
        """Cast candidate rule dicts to RuntimePolicyRule if needed."""
        from agent_app.governance.runtime_policy import RuntimePolicyRule
        result: list[Any] = []
        for r in rules:
            if isinstance(r, RuntimePolicyRule):
                result.append(r)
            elif isinstance(r, dict):
                result.append(RuntimePolicyRule(**r))
            else:
                result.append(r)
        return result

    @staticmethod
    def _cast_gate_rules(rules: list[Any]) -> list[Any]:
        """Cast gate rule dicts to PolicyGateRule if needed."""
        from agent_app.governance.policy_gate import PolicyGateRule
        result: list[Any] = []
        for r in rules:
            if isinstance(r, PolicyGateRule):
                result.append(r)
            elif isinstance(r, dict):
                result.append(PolicyGateRule(**r))
            else:
                result.append(r)
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_gate_service.py -v`
Expected: All 17 tests PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_rollout_gate_service.py tests/unit/test_policy_rollout_gate_service.py
git commit -m "feat: Phase 43 Task 3 — RolloutGateAutomationService with ensure/run/check step gate"
```

---

### Task 4: RolloutService Integration

**Files:**
- Modify: `agent_app/runtime/policy_rollout_service.py`
- Test: `tests/unit/test_policy_rollout_gate_integration.py`

- [ ] **Step 1: Write failing integration tests**

Create `tests/unit/test_policy_rollout_gate_integration.py`:

```python
"""Integration tests for RolloutService + RolloutGateAutomationService — Phase 43."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from agent_app.core.context import RunContext
from agent_app.governance.policy_rollout import (
    RolloutPlan,
    RolloutStep,
    RolloutStepType,
    RolloutPlanStatus,
    RolloutStepStatus,
    RolloutGateMode,
    RolloutGateFailureAction,
)
from agent_app.governance.policy_rollout_gate import RolloutGateExecutionStatus, RolloutGateExecutionResult
from agent_app.governance.policy_release_gate import ReleaseGateRequirementStatus, ReleaseGateRequirement
from agent_app.runtime.policy_rollout_service import RolloutService
from agent_app.runtime.policy_rollout_store import InMemoryRolloutPlanStore
from agent_app.runtime.policy_rollout_gate_service import RolloutGateAutomationService


def _make_step(**overrides) -> RolloutStep:
    defaults = dict(
        step_id="s1",
        step_type=RolloutStepType.ASSIGN_RING,
        environment="prod",
        ring_name="canary",
    )
    defaults.update(overrides)
    return RolloutStep(**defaults)


def _make_plan(steps=None, **overrides) -> RolloutPlan:
    now = datetime.now(timezone.utc)
    defaults = dict(
        rollout_id="ro_test",
        name="test plan",
        bundle_id="pb_test",
        status=RolloutPlanStatus.ACTIVE,
        steps=steps or [_make_step()],
        created_by="admin",
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return RolloutPlan(**defaults)


def _make_context() -> RunContext:
    return RunContext(run_id="r1", user_id="admin", tenant_id="t1")


def _gate_result(status, **kwargs):
    return RolloutGateExecutionResult(
        execution_id="rge_test",
        rollout_id="ro_test",
        step_id="s1",
        status=status,
        created_at=datetime.now(timezone.utc),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_no_gate_config_preserves_existing_behavior():
    """Steps without gate config execute normally."""
    store = InMemoryRolloutPlanStore()
    release_svc = AsyncMock()
    release_svc.activate_bundle = AsyncMock(return_value=MagicMock(activation_id="act_1"))
    release_svc.assign_ring = AsyncMock(return_value=MagicMock(assignment_id="asg_1"))

    svc = RolloutService(
        rollout_store=store,
        release_service=release_svc,
    )
    step = _make_step()
    plan = _make_plan(steps=[step])
    await store.create(plan)
    result = await svc.run_next_step("ro_test", "admin", _make_context())
    assert result.steps[0].status == RolloutStepStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_manual_missing_gate_blocks_step():
    """MANUAL mode with missing gate blocks step."""
    store = InMemoryRolloutPlanStore()
    release_svc = AsyncMock()
    gate_automation = AsyncMock(spec=RolloutGateAutomationService)
    gate_automation.ensure_step_gate.return_value = _gate_result(
        RolloutGateExecutionStatus.BLOCKED,
        action_taken="manual_blocked",
        reason="No gate result attached",
    )

    svc = RolloutService(
        rollout_store=store,
        release_service=release_svc,
        rollout_gate_automation_service=gate_automation,
    )
    step = _make_step(
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.MANUAL,
    )
    plan = _make_plan(steps=[step])
    await store.create(plan)
    result = await svc.run_next_step("ro_test", "admin", _make_context())
    assert result.steps[0].status == RolloutStepStatus.BLOCKED
    assert result.steps[0].error is not None
    assert result.steps[0].error["type"] == "simulation_gate_required"


@pytest.mark.asyncio
async def test_auto_passing_gate_executes_step():
    """AUTO mode with passing gate executes step normally."""
    store = InMemoryRolloutPlanStore()
    release_svc = AsyncMock()
    release_svc.assign_ring = AsyncMock(return_value=MagicMock(assignment_id="asg_1"))
    gate_automation = AsyncMock(spec=RolloutGateAutomationService)
    gate_automation.ensure_step_gate.return_value = _gate_result(
        RolloutGateExecutionStatus.SATISFIED,
        requirement_id="rgr_1",
        gate_result_id="gr_1",
        simulation_id="psim_1",
    )

    svc = RolloutService(
        rollout_store=store,
        release_service=release_svc,
        rollout_gate_automation_service=gate_automation,
    )
    step = _make_step(
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.AUTO,
    )
    plan = _make_plan(steps=[step])
    await store.create(plan)
    result = await svc.run_next_step("ro_test", "admin", _make_context())
    assert result.steps[0].status == RolloutStepStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_auto_failing_gate_with_block():
    """AUTO mode with failing gate and BLOCK action blocks step."""
    store = InMemoryRolloutPlanStore()
    release_svc = AsyncMock()
    gate_automation = AsyncMock(spec=RolloutGateAutomationService)
    gate_automation.ensure_step_gate.return_value = _gate_result(
        RolloutGateExecutionStatus.BLOCKED,
        action_taken="auto_blocked",
    )

    svc = RolloutService(
        rollout_store=store,
        release_service=release_svc,
        rollout_gate_automation_service=gate_automation,
    )
    step = _make_step(
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.AUTO,
        simulation_gate_failure_action=RolloutGateFailureAction.BLOCK,
    )
    plan = _make_plan(steps=[step])
    await store.create(plan)
    result = await svc.run_next_step("ro_test", "admin", _make_context())
    assert result.steps[0].status == RolloutStepStatus.BLOCKED
    assert result.steps[0].error["type"] == "simulation_gate_required"


@pytest.mark.asyncio
async def test_auto_failing_gate_with_fail():
    """AUTO mode with failing gate and FAIL action fails step."""
    store = InMemoryRolloutPlanStore()
    release_svc = AsyncMock()
    gate_automation = AsyncMock(spec=RolloutGateAutomationService)
    gate_automation.ensure_step_gate.return_value = _gate_result(
        RolloutGateExecutionStatus.FAILED,
        action_taken="auto_failed",
        reason="Gate failed",
    )

    svc = RolloutService(
        rollout_store=store,
        release_service=release_svc,
        rollout_gate_automation_service=gate_automation,
    )
    step = _make_step(
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.AUTO,
        simulation_gate_failure_action=RolloutGateFailureAction.FAIL,
    )
    plan = _make_plan(steps=[step])
    await store.create(plan)
    result = await svc.run_next_step("ro_test", "admin", _make_context())
    assert result.steps[0].status == RolloutStepStatus.FAILED
    assert result.steps[0].error["type"] == "simulation_gate_failed"


@pytest.mark.asyncio
async def test_auto_failing_gate_with_skip():
    """AUTO mode with failing gate and SKIP action skips step."""
    store = InMemoryRolloutPlanStore()
    release_svc = AsyncMock()
    gate_automation = AsyncMock(spec=RolloutGateAutomationService)
    gate_automation.ensure_step_gate.return_value = _gate_result(
        RolloutGateExecutionStatus.SKIPPED,
        action_taken="auto_skipped",
    )

    svc = RolloutService(
        rollout_store=store,
        release_service=release_svc,
        rollout_gate_automation_service=gate_automation,
    )
    step = _make_step(
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.AUTO,
        simulation_gate_failure_action=RolloutGateFailureAction.SKIP,
    )
    plan = _make_plan(steps=[step])
    await store.create(plan)
    result = await svc.run_next_step("ro_test", "admin", _make_context())
    assert result.steps[0].status == RolloutStepStatus.SKIPPED


@pytest.mark.asyncio
async def test_run_all_available_stops_on_blocked():
    """run_all_available stops on BLOCKED gate result."""
    store = InMemoryRolloutPlanStore()
    release_svc = AsyncMock()
    gate_automation = AsyncMock(spec=RolloutGateAutomationService)
    gate_automation.ensure_step_gate.return_value = _gate_result(
        RolloutGateExecutionStatus.BLOCKED,
    )

    svc = RolloutService(
        rollout_store=store,
        release_service=release_svc,
        rollout_gate_automation_service=gate_automation,
    )
    step = _make_step(
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.AUTO,
    )
    plan = _make_plan(steps=[step])
    await store.create(plan)
    result = await svc.run_all_available("ro_test", "admin", _make_context())
    assert result.steps[0].status == RolloutStepStatus.BLOCKED


@pytest.mark.asyncio
async def test_run_all_available_stops_on_failed():
    """run_all_available stops on FAILED gate result."""
    store = InMemoryRolloutPlanStore()
    release_svc = AsyncMock()
    gate_automation = AsyncMock(spec=RolloutGateAutomationService)
    gate_automation.ensure_step_gate.return_value = _gate_result(
        RolloutGateExecutionStatus.FAILED,
    )

    svc = RolloutService(
        rollout_store=store,
        release_service=release_svc,
        rollout_gate_automation_service=gate_automation,
    )
    step = _make_step(
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.AUTO,
        simulation_gate_failure_action=RolloutGateFailureAction.FAIL,
    )
    plan = _make_plan(steps=[step])
    await store.create(plan)
    result = await svc.run_all_available("ro_test", "admin", _make_context())
    assert result.steps[0].status == RolloutStepStatus.FAILED


@pytest.mark.asyncio
async def test_step_fields_updated_with_gate_ids():
    """Step fields are updated with requirement/gate/simulation IDs on pass."""
    store = InMemoryRolloutPlanStore()
    release_svc = AsyncMock()
    release_svc.assign_ring = AsyncMock(return_value=MagicMock(assignment_id="asg_1"))
    gate_automation = AsyncMock(spec=RolloutGateAutomationService)
    gate_automation.ensure_step_gate.return_value = _gate_result(
        RolloutGateExecutionStatus.SATISFIED,
        requirement_id="rgr_1",
        gate_result_id="gr_1",
        simulation_id="psim_1",
    )

    svc = RolloutService(
        rollout_store=store,
        release_service=release_svc,
        rollout_gate_automation_service=gate_automation,
    )
    step = _make_step(
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.AUTO,
    )
    plan = _make_plan(steps=[step])
    await store.create(plan)
    result = await svc.run_next_step("ro_test", "admin", _make_context())
    assert result.steps[0].simulation_gate_requirement_id == "rgr_1"
    assert result.steps[0].simulation_gate_result_id == "gr_1"


@pytest.mark.asyncio
async def test_phase42_backward_compat_manual_blocking():
    """Phase 42 manual blocking behavior still works without gate automation service."""
    store = InMemoryRolloutPlanStore()
    release_svc = AsyncMock()
    release_gate_automation = AsyncMock()
    req = ReleaseGateRequirement(
        requirement_id="rgr_1",
        source_type="rollout_step",
        source_id="s1",
        status=ReleaseGateRequirementStatus.REQUIRED,
        metadata={},
    )
    release_gate_automation.check_requirement.return_value = req

    svc = RolloutService(
        rollout_store=store,
        release_service=release_svc,
        release_gate_automation_service=release_gate_automation,
    )
    step = _make_step(requires_simulation_gate=True)
    plan = _make_plan(steps=[step])
    await store.create(plan)
    result = await svc.run_next_step("ro_test", "admin", _make_context())
    assert result.steps[0].status == RolloutStepStatus.BLOCKED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_gate_integration.py -v`
Expected: Some tests fail because RolloutService doesn't use rollout_gate_automation_service yet

- [ ] **Step 3: Modify RolloutService**

In `agent_app/runtime/policy_rollout_service.py`:

1. Add constructor parameter `rollout_gate_automation_service: Any = None` after `release_gate_automation_service`
2. Store it as `self._rollout_gate_automation_service`
3. In `run_next_step()`, after the existing Phase 42 gate check block (lines 233-268) and BEFORE executing the step (line 271), add Phase 43 gate automation check:

```python
        # Phase 43: Rollout gate automation (enhanced over Phase 42 manual blocking)
        if (
            self._rollout_gate_automation_service is not None
            and (
                next_step.requires_simulation_gate
                or next_step.simulation_gate_mode != RolloutGateMode.DISABLED
            )
        ):
            gate_result = await self._rollout_gate_automation_service.ensure_step_gate(
                plan, next_step, context,
            )

            if gate_result.status == RolloutGateExecutionStatus.SATISFIED:
                # Update step with gate IDs
                next_step = next_step.model_copy(update={
                    "simulation_gate_requirement_id": gate_result.requirement_id or next_step.simulation_gate_requirement_id,
                    "simulation_gate_result_id": gate_result.gate_result_id or next_step.simulation_gate_result_id,
                })

            elif gate_result.status == RolloutGateExecutionStatus.BLOCKED:
                blocked_step = next_step.model_copy(update={
                    "status": RolloutStepStatus.BLOCKED,
                    "error": {
                        "type": "simulation_gate_required",
                        "message": gate_result.reason or "Simulation gate blocked",
                        "action_taken": gate_result.action_taken,
                    },
                    "simulation_gate_requirement_id": gate_result.requirement_id or next_step.simulation_gate_requirement_id,
                })
                updated_steps = [
                    blocked_step if s.step_id == blocked_step.step_id else s
                    for s in plan.steps
                ]
                plan = plan.model_copy(update={
                    "steps": updated_steps,
                    "updated_at": datetime.now(timezone.utc),
                })
                await self._rollout_store.update(plan)
                await self._write_audit(
                    "policy.rollout.step_blocked",
                    user_id=actor_id,
                    tenant_id=context.tenant_id,
                    data={
                        "rollout_id": rollout_id,
                        "step_id": next_step.step_id,
                        "reason": "simulation_gate_blocked",
                        "gate_action": gate_result.action_taken,
                    },
                )
                return plan

            elif gate_result.status == RolloutGateExecutionStatus.FAILED:
                failed_step = next_step.model_copy(update={
                    "status": RolloutStepStatus.FAILED,
                    "error": {
                        "type": "simulation_gate_failed",
                        "message": gate_result.reason or "Simulation gate failed",
                        "action_taken": gate_result.action_taken,
                    },
                    "simulation_gate_requirement_id": gate_result.requirement_id or next_step.simulation_gate_requirement_id,
                })
                updated_steps = [
                    failed_step if s.step_id == failed_step.step_id else s
                    for s in plan.steps
                ]
                now = datetime.now(timezone.utc)
                plan = plan.model_copy(update={
                    "steps": updated_steps,
                    "status": RolloutPlanStatus.FAILED,
                    "updated_at": now,
                })
                await self._rollout_store.update(plan)
                await self._write_audit(
                    "policy.rollout.step_failed",
                    user_id=actor_id,
                    tenant_id=context.tenant_id,
                    data={
                        "rollout_id": rollout_id,
                        "step_id": next_step.step_id,
                        "reason": "simulation_gate_failed",
                        "gate_action": gate_result.action_taken,
                    },
                )
                return plan

            elif gate_result.status == RolloutGateExecutionStatus.SKIPPED:
                skipped_step = next_step.model_copy(update={
                    "status": RolloutStepStatus.SKIPPED,
                    "simulation_gate_requirement_id": gate_result.requirement_id or next_step.simulation_gate_requirement_id,
                })
                updated_steps = [
                    skipped_step if s.step_id == skipped_step.step_id else s
                    for s in plan.steps
                ]
                now = datetime.now(timezone.utc)
                # If all steps done (succeeded or skipped), complete the plan
                all_done = all(
                    s.status in (RolloutStepStatus.SUCCEEDED, RolloutStepStatus.SKIPPED)
                    for s in updated_steps
                )
                plan = plan.model_copy(update={
                    "steps": updated_steps,
                    "status": RolloutPlanStatus.COMPLETED if all_done else plan.status,
                    "updated_at": now,
                })
                await self._rollout_store.update(plan)
                await self._write_audit(
                    "policy.rollout.step_skipped",
                    user_id=actor_id,
                    tenant_id=context.tenant_id,
                    data={
                        "rollout_id": rollout_id,
                        "step_id": next_step.step_id,
                        "reason": "simulation_gate_skipped",
                    },
                )
                return plan

            elif gate_result.status == RolloutGateExecutionStatus.ERROR:
                # ERROR: treat as BLOCKED (conservative)
                blocked_step = next_step.model_copy(update={
                    "status": RolloutStepStatus.BLOCKED,
                    "error": {
                        "type": "simulation_gate_error",
                        "message": gate_result.error.get("message", "Gate evaluation error") if gate_result.error else "Gate evaluation error",
                    },
                })
                updated_steps = [
                    blocked_step if s.step_id == blocked_step.step_id else s
                    for s in plan.steps
                ]
                plan = plan.model_copy(update={
                    "steps": updated_steps,
                    "updated_at": datetime.now(timezone.utc),
                })
                await self._rollout_store.update(plan)
                return plan
```

Also update `run_all_available()` to handle SKIPPED steps — after the BLOCKED check, add:

```python
            if step and step.status == RolloutStepStatus.SKIPPED:
                continue  # SKIPPED steps allow progression
```

Add the necessary imports at the top of the file:
```python
from agent_app.governance.policy_rollout import RolloutGateMode
from agent_app.governance.policy_rollout_gate import RolloutGateExecutionStatus
```

- [ ] **Step 4: Run integration tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_gate_integration.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Run existing rollout tests for backward compatibility**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_service.py tests/unit/test_policy_rollout_approval_policy.py tests/unit/test_policy_release_gate_integration.py -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
git add agent_app/runtime/policy_rollout_service.py tests/unit/test_policy_rollout_gate_integration.py
git commit -m "feat: Phase 43 Task 4 — RolloutService simulation gate automation integration"
```

---

### Task 5: Config Schema, Loader, RBAC, Events

**Files:**
- Modify: `agent_app/config/schema.py`
- Modify: `agent_app/config/loader.py`
- Modify: `agent_app/governance/policy_rbac.py`
- Modify: `agent_app/governance/policy_change_event.py`
- Modify: `agent_app/app.py`
- Test: `tests/unit/test_policy_rollout_gate_config.py`

- [ ] **Step 1: Write failing config/RBAC/events tests**

Create `tests/unit/test_policy_rollout_gate_config.py` with tests for:
- RolloutGateAutomationConfig defaults
- Config wiring
- 3 new RBAC permissions
- 7 new change event types
- Loader integration

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Add RolloutGateAutomationConfig to schema.py**

Add after `SimulationGateEnforcementConfig`:

```python
class SimulationGateRuleConfig(BaseModel):
    """Default gate rule from rollout YAML config."""
    name: str = Field(..., description="Gate rule name")
    metric: str = Field(default="simulation.changed_ratio", description="Metric path")
    operator: str = Field(default="lte", description="Comparison operator")
    threshold: float | int = Field(default=0.05, description="Threshold value")


class RolloutGateAutomationConfig(BaseModel):
    """Rollout gate automation configuration."""
    enabled: bool = Field(default=False, description="Enable rollout gate automation")
    default_mode: str = Field(default="manual", description="Default gate mode: disabled, manual, auto")
    default_failure_action: str = Field(default="block", description="Default failure action: block, fail, skip")
    default_max_age_seconds: int | None = Field(default=None, description="Default gate result max age")
    default_gate_rules: list[SimulationGateRuleConfig] = Field(
        default_factory=list, description="Default gate rules for auto mode"
    )
```

Add `rollout_gate_automation: RolloutGateAutomationConfig | None = None` to `PolicyReleaseConfig`.

- [ ] **Step 4: Add RBAC permissions to policy_rbac.py**

```python
ROLLOUT_GATE_RUN = "policy.rollout.gate.run"
ROLLOUT_GATE_ATTACH = "policy.rollout.gate.attach"
ROLLOUT_GATE_VIEW = "policy.rollout.gate.view"
```

Add `ROLLOUT_GATE_VIEW` to `_DEFAULT_ALLOWED`.

- [ ] **Step 5: Add change event types to policy_change_event.py**

```python
ROLLOUT_GATE_RUN = "policy.rollout.gate.run"
ROLLOUT_GATE_SATISFIED = "policy.rollout.gate.satisfied"
ROLLOUT_GATE_BLOCKED = "policy.rollout.gate.blocked"
ROLLOUT_GATE_FAILED = "policy.rollout.gate.failed"
ROLLOUT_GATE_SKIPPED = "policy.rollout.gate.skipped"
ROLLOUT_GATE_ATTACHED = "policy.rollout.gate.attached"
ROLLOUT_GATE_PERMISSION_DENIED = "policy.rollout.gate.permission_denied"
```

- [ ] **Step 6: Wire in loader.py**

When `rollout_gate_automation` config is present and enabled:
- Create `RolloutGateAutomationService` with `release_gate_automation_service`, `simulation_service`, `simulation_gate_evaluator`, `audit_logger`, `event_store`, `default_gate_rules`, `default_max_age_seconds`
- Set it on `RolloutService`
- Expose on `AgentApp`

- [ ] **Step 7: Expose on app.py**

Add `rollout_gate_automation_service` property to `AgentApp`.

- [ ] **Step 8: Run tests to verify they pass**

- [ ] **Step 9: Commit**

```bash
git add agent_app/config/schema.py agent_app/config/loader.py agent_app/governance/policy_rbac.py agent_app/governance/policy_change_event.py agent_app/app.py tests/unit/test_policy_rollout_gate_config.py
git commit -m "feat: Phase 43 Task 5 — config, loader, RBAC, events for rollout gate automation"
```

---

### Task 6: CLI Commands

**Files:**
- Modify: `agent_app/cli.py`
- Test: `tests/unit/test_policy_rollout_gate_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/unit/test_policy_rollout_gate_cli.py` with tests for:
- `rollout gate run` — basic pass/fail
- `rollout gate status` — text/JSON output
- `rollout gate attach` — basic attach
- Missing service exits non-zero

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Add CLI subcommands**

Add `rollout gate` group with 3 subcommands:
- `rollout gate run --rollout-id --step-id --actor-id --permissions`
- `rollout gate status --rollout-id --step-id [--json]`
- `rollout gate attach --rollout-id --step-id --gate-result-id --simulation-id --actor-id --permissions`

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add agent_app/cli.py tests/unit/test_policy_rollout_gate_cli.py
git commit -m "feat: Phase 43 Task 6 — CLI rollout gate commands"
```

---

### Task 7: Console Pages

**Files:**
- Create: `agent_app/console/templates/policy_rollout_gate.html`
- Create: `agent_app/console/templates/policy_rollout_gate_status.html`
- Modify: `agent_app/console/router.py`
- Modify: `agent_app/adapters/fastapi.py`
- Test: `tests/unit/test_policy_rollout_gate_console.py`

- [ ] **Step 1: Write failing console tests**

Create `tests/unit/test_policy_rollout_gate_console.py` with tests for:
- Gate page renders
- POST run works
- POST attach works
- Errors render clearly (no traceback)

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Create templates and routes**

Add 3 routes:
- `GET /policy-console/rollouts/{rollout_id}/steps/{step_id}/gate`
- `POST /policy-console/rollouts/{rollout_id}/steps/{step_id}/gate/run`
- `POST /policy-console/rollouts/{rollout_id}/steps/{step_id}/gate/attach`

Create two Jinja2 templates for gate form and status display.

Wire `rollout_gate_automation_service` in `fastapi.py`.

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add agent_app/console/templates/policy_rollout_gate.html agent_app/console/templates/policy_rollout_gate_status.html agent_app/console/router.py agent_app/adapters/fastapi.py tests/unit/test_policy_rollout_gate_console.py
git commit -m "feat: Phase 43 Task 7 — console rollout gate pages"
```

---

### Task 8: Documentation and Final Verification

**Files:**
- Modify: `docs/policy_release.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Create: `docs/release_checklist_phase43.md`
- Test: Run full test suite

- [ ] **Step 1: Update docs/policy_release.md**

Add Phase 43 section covering:
1. Rollout gate automation purpose
2. Manual vs auto gate modes
3. Failure actions: block/fail/skip
4. Rollout YAML examples
5. CLI flows
6. Console workflow
7. Relationship to Phase 42 promotion gate enforcement
8. Known limitations

- [ ] **Step 2: Update CHANGELOG.md**

Add v0.31.0 entry with Phase 43 features.

- [ ] **Step 3: Update README.md**

Add Phase 43 in roadmap.

- [ ] **Step 4: Create release checklist**

Create `docs/release_checklist_phase43.md`.

- [ ] **Step 5: Run full Phase 43 test suite**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_gate_model.py tests/unit/test_policy_rollout_gate_service.py tests/unit/test_policy_rollout_gate_integration.py tests/unit/test_policy_rollout_gate_config.py tests/unit/test_policy_rollout_gate_cli.py tests/unit/test_policy_rollout_gate_console.py -v`

- [ ] **Step 6: Run broader policy regression tests**

Run: `.venv/bin/python -m pytest tests/unit/ -k "policy_rollout or policy_release or policy_gate or policy_simulation or policy_rbac or policy_change_event" -v`

- [ ] **Step 7: Commit**

```bash
git add docs/policy_release.md CHANGELOG.md README.md docs/release_checklist_phase43.md
git commit -m "docs: Phase 43 documentation — policy rollout automation with simulation gates"
```

---

## Self-Review

**1. Spec coverage:** All 13 spec sections mapped to tasks:
- §1 Rollout step gate automation model → Task 1
- §2 Rollout gate execution result model → Task 2
- §3 RolloutGateAutomationService → Task 3
- §4 RolloutService integration → Task 4
- §5 Rollout config defaults → Task 5
- §6 Rollout steps file format → Task 1 (fields on RolloutStep) + Task 5 (config)
- §7 CLI commands → Task 6
- §8 Console updates → Task 7
- §9 RBAC → Task 5
- §10 Audit and change events → Task 5
- §11 Tests → Tasks 1-8 (each has dedicated test files)
- §12 Documentation → Task 8
- §13 Acceptance criteria → Verified across all tasks

**2. Placeholder scan:** No TBD/TODO/placeholders found. All steps contain complete code or clear instructions.

**3. Type consistency:** 
- `RolloutGateMode` used consistently across policy_rollout.py, policy_rollout_gate_service.py, tests
- `RolloutGateFailureAction` used consistently
- `RolloutGateExecutionStatus` defined in policy_rollout_gate.py, used in service and integration tests
- `source_id` format `"{rollout_id}:{step_id}"` used consistently in service and tests
- Step field names match between model extension (Task 1) and service usage (Task 3/4)
