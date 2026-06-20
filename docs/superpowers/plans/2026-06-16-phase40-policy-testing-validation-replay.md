# Phase 40: Policy Testing, Validation, and Historical Replay — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a policy simulation and validation framework that allows teams to test runtime policy rule changes against historical audit events before deploying them.

**Architecture:** `PolicySimulationReport` models capture baseline vs candidate decision comparisons. `PolicySimulationService` reads enforcement audit events, converts them to simulation cases via `audit_event_to_simulation_case()`, evaluates them against a candidate `RuntimePolicyStore` (built by `build_candidate_policy_store()`), and produces impact reports. `RuntimePolicyValidator` checks candidate rules for issues before simulation. CLI and Console provide human interfaces. All new modules live in governance/ and runtime/ layers — no FastAPI/OpenAI imports in those modules.

**Tech Stack:** Python 3.11+, Pydantic v2, SQLite, FastAPI + Jinja2 (console only), pytest + pytest-asyncio

**Naming note:** A `PolicySimulator` class already exists in `agent_app/governance/policy_simulator.py` (Phase 25, operates on `PolicyEngine`). Phase 40's simulation is for runtime policy rules against audit events. New classes use `RuntimePolicy` prefix or `Simulation` suffix to avoid collision.

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `agent_app/governance/policy_simulation.py` | Simulation models: PolicySimulationOutcome, PolicySimulationCase, PolicySimulationResult, PolicySimulationSummary, PolicySimulationReport |
| `agent_app/runtime/policy_simulation_cases.py` | `audit_event_to_simulation_case()` — converts audit events to simulation cases |
| `agent_app/runtime/policy_candidate_store.py` | `CandidateRuntimePolicySet`, `build_candidate_policy_store()` — builds isolated candidate stores |
| `agent_app/runtime/policy_simulation_service.py` | `PolicySimulationService` — collect cases, simulate, produce reports |
| `agent_app/runtime/policy_validation.py` | `RuntimePolicyValidator`, `PolicyValidationIssue`, `PolicyValidationReport` |
| `tests/unit/test_policy_simulation.py` | Tests for models, case extraction, candidate store, service, validation, export, config, RBAC, events |
| `tests/unit/test_policy_simulation_cli.py` | Tests for CLI simulation commands |
| `tests/unit/test_policy_simulation_console.py` | Tests for console simulation pages |

### Modified Files
| File | Changes |
|------|---------|
| `agent_app/runtime/policy_compliance_export.py` | Add simulation_report_to_json, simulation_report_to_csv_rows, validation_report_to_json |
| `agent_app/governance/policy_rbac.py` | Add SIMULATION_RUN, SIMULATION_VIEW, SIMULATION_EXPORT permissions |
| `agent_app/governance/policy_change_event.py` | Add simulation event types to PolicyChangeEventType |
| `agent_app/config/schema.py` | Add PolicySimulationConfig |
| `agent_app/config/loader.py` | Wire PolicySimulationService |
| `agent_app/cli.py` | Add simulation validate/replay/export commands |
| `agent_app/console/router.py` | Add simulation routes |
| `agent_app/console/templates/policy_simulation.html` | Simulation page |
| `agent_app/console/templates/policy_simulation_report.html` | Simulation report page |
| `agent_app/console/templates/policy_validation_report.html` | Validation report page |
| `docs/policy_release.md` | Phase 40 section |
| `CHANGELOG.md` | v0.28.0 entry |
| `README.md` | Phase 40 roadmap |
| `docs/release_checklist_phase40.md` | Release checklist |

---

### Task 1: Simulation Models

**Files:**
- Create: `agent_app/governance/policy_simulation.py`
- Test: `tests/unit/test_policy_simulation.py`

- [ ] **Step 1: Write failing tests for simulation models**

Create `tests/unit/test_policy_simulation.py`:

```python
"""Tests for Phase 40 policy simulation models and service."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_simulation import (
    PolicySimulationCase,
    PolicySimulationOutcome,
    PolicySimulationReport,
    PolicySimulationResult,
    PolicySimulationSummary,
)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestPolicySimulationOutcome:
    def test_enum_values(self):
        assert PolicySimulationOutcome.UNCHANGED == "unchanged"
        assert PolicySimulationOutcome.WOULD_ALLOW == "would_allow"
        assert PolicySimulationOutcome.WOULD_DENY == "would_deny"
        assert PolicySimulationOutcome.WOULD_REQUIRE_APPROVAL == "would_require_approval"
        assert PolicySimulationOutcome.WOULD_CHANGE == "would_change"
        assert PolicySimulationOutcome.ERROR == "error"


class TestPolicySimulationCase:
    def test_valid_case(self):
        case = PolicySimulationCase(
            case_id="psc_abc123",
            action_type="tool.execute",
            baseline_status="allowed",
        )
        assert case.case_id == "psc_abc123"
        assert case.action_type == "tool.execute"
        assert case.baseline_status == "allowed"
        assert case.subject is None
        assert case.tool_name is None
        assert case.roles == []
        assert case.permissions == []

    def test_case_with_all_fields(self):
        case = PolicySimulationCase(
            case_id="psc_full",
            action_type="tool.execute",
            subject="user_1",
            tool_name="refund.request",
            risk_level="high",
            actor_id="actor_1",
            user_id="user_1",
            tenant_id="tenant_1",
            roles=["admin"],
            permissions=["refund:create"],
            baseline_status="denied",
            metadata={"key": "value"},
        )
        assert case.subject == "user_1"
        assert case.tool_name == "refund.request"
        assert case.risk_level == "high"
        assert case.actor_id == "actor_1"
        assert case.user_id == "user_1"
        assert case.tenant_id == "tenant_1"
        assert case.roles == ["admin"]
        assert case.permissions == ["refund:create"]
        assert case.metadata == {"key": "value"}

    def test_case_id_psc_prefix(self):
        case = PolicySimulationCase(
            case_id="psc_abc123",
            action_type="tool.execute",
        )
        assert case.case_id.startswith("psc_")

    def test_case_defaults(self):
        case = PolicySimulationCase(
            case_id="psc_def",
            action_type="tool.execute",
        )
        assert case.subject is None
        assert case.tool_name is None
        assert case.risk_level is None
        assert case.actor_id is None
        assert case.user_id is None
        assert case.tenant_id is None
        assert case.roles == []
        assert case.permissions == []
        assert case.baseline_status is None
        assert case.metadata == {}


class TestPolicySimulationResult:
    def test_unchanged_result(self):
        result = PolicySimulationResult(
            case_id="psc_abc",
            baseline_status="allowed",
            candidate_status="allowed",
            outcome=PolicySimulationOutcome.UNCHANGED,
        )
        assert result.outcome == PolicySimulationOutcome.UNCHANGED
        assert result.reason is None
        assert result.errors == []

    def test_would_deny_result(self):
        result = PolicySimulationResult(
            case_id="psc_denied",
            baseline_status="allowed",
            candidate_status="denied",
            outcome=PolicySimulationOutcome.WOULD_DENY,
            reason="Denied by rule 'deny_refunds'",
        )
        assert result.outcome == PolicySimulationOutcome.WOULD_DENY
        assert result.reason == "Denied by rule 'deny_refunds'"

    def test_error_result(self):
        result = PolicySimulationResult(
            case_id="psc_err",
            outcome=PolicySimulationOutcome.ERROR,
            errors=["Evaluation failed: missing action_type"],
        )
        assert result.outcome == PolicySimulationOutcome.ERROR
        assert len(result.errors) == 1

    def test_result_decision_id(self):
        result = PolicySimulationResult(
            case_id="psc_dec",
            baseline_status="allowed",
            candidate_status="denied",
            outcome=PolicySimulationOutcome.WOULD_DENY,
            decision_id="ped_abc123",
        )
        assert result.decision_id == "ped_abc123"


class TestPolicySimulationSummary:
    def test_default_summary(self):
        summary = PolicySimulationSummary()
        assert summary.total == 0
        assert summary.unchanged == 0
        assert summary.would_allow == 0
        assert summary.would_deny == 0
        assert summary.would_require_approval == 0
        assert summary.would_change == 0
        assert summary.errors == 0

    def test_summary_with_counts(self):
        summary = PolicySimulationSummary(
            total=10,
            unchanged=5,
            would_allow=2,
            would_deny=1,
            would_require_approval=1,
            would_change=0,
            errors=1,
        )
        assert summary.total == 10
        assert summary.would_deny == 1


class TestPolicySimulationReport:
    def test_valid_report(self):
        report = PolicySimulationReport(
            simulation_id="psim_abc123",
            generated_at=datetime.now(timezone.utc),
            summary=PolicySimulationSummary(total=1, unchanged=1),
            results=[
                PolicySimulationResult(
                    case_id="psc_1",
                    baseline_status="allowed",
                    candidate_status="allowed",
                    outcome=PolicySimulationOutcome.UNCHANGED,
                ),
            ],
        )
        assert report.simulation_id == "psim_abc123"
        assert report.summary.total == 1
        assert len(report.results) == 1

    def test_simulation_id_psim_prefix(self):
        report = PolicySimulationReport(
            simulation_id="psim_abc",
            generated_at=datetime.now(timezone.utc),
            summary=PolicySimulationSummary(),
        )
        assert report.simulation_id.startswith("psim_")

    def test_report_timezone_aware(self):
        report = PolicySimulationReport(
            simulation_id="psim_tz",
            generated_at=datetime.now(timezone.utc),
            summary=PolicySimulationSummary(),
        )
        assert report.generated_at.tzinfo is not None

    def test_report_optional_fields(self):
        report = PolicySimulationReport(
            simulation_id="psim_opt",
            generated_at=datetime.now(timezone.utc),
            summary=PolicySimulationSummary(),
        )
        assert report.name is None
        assert report.candidate_rule_ids == []
        assert report.results == []
        assert report.metadata == {}

    def test_report_json_serializable(self):
        report = PolicySimulationReport(
            simulation_id="psim_json",
            generated_at=datetime.now(timezone.utc),
            summary=PolicySimulationSummary(total=1),
        )
        json_str = report.model_dump_json()
        assert "psim_json" in json_str
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation.py::TestPolicySimulationOutcome -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_app.governance.policy_simulation'`

- [ ] **Step 3: Implement simulation models**

Create `agent_app/governance/policy_simulation.py`:

```python
"""Policy simulation models — for testing runtime policy rule changes against historical events.

Phase 40: Offline policy validation and historical replay framework.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class PolicySimulationOutcome(StrEnum):
    """Outcome of comparing baseline vs candidate policy decision."""

    UNCHANGED = "unchanged"
    WOULD_ALLOW = "would_allow"
    WOULD_DENY = "would_deny"
    WOULD_REQUIRE_APPROVAL = "would_require_approval"
    WOULD_CHANGE = "would_change"
    ERROR = "error"


class PolicySimulationCase(BaseModel):
    """A single case extracted from audit history for simulation."""

    case_id: str  # psc_ prefix
    action_type: str
    subject: str | None = None
    tool_name: str | None = None
    risk_level: str | None = None
    actor_id: str | None = None
    user_id: str | None = None
    tenant_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    baseline_status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PolicySimulationResult(BaseModel):
    """Result of simulating a single case against candidate rules."""

    case_id: str
    baseline_status: str | None = None
    candidate_status: str | None = None
    outcome: PolicySimulationOutcome
    reason: str | None = None
    decision_id: str | None = None
    errors: list[str] = Field(default_factory=list)


class PolicySimulationSummary(BaseModel):
    """Aggregate summary of simulation outcomes."""

    total: int = 0
    unchanged: int = 0
    would_allow: int = 0
    would_deny: int = 0
    would_require_approval: int = 0
    would_change: int = 0
    errors: int = 0


class PolicySimulationReport(BaseModel):
    """Full simulation report comparing baseline vs candidate decisions."""

    simulation_id: str  # psim_ prefix
    name: str | None = None
    generated_at: datetime
    candidate_rule_ids: list[str] = Field(default_factory=list)
    summary: PolicySimulationSummary
    results: list[PolicySimulationResult] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("simulation_id")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        if not v.startswith("psim_"):
            raise ValueError("simulation_id must use psim_ prefix")
        return v

    @field_validator("generated_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")
        return v
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation.py::TestPolicySimulationOutcome tests/unit/test_policy_simulation.py::TestPolicySimulationCase tests/unit/test_policy_simulation.py::TestPolicySimulationResult tests/unit/test_policy_simulation.py::TestPolicySimulationSummary tests/unit/test_policy_simulation.py::TestPolicySimulationReport -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/governance/policy_simulation.py tests/unit/test_policy_simulation.py
git commit -m "feat: Phase 40 Task 1 — policy simulation models"
```

---

### Task 2: Audit-to-Simulation Case Extraction

**Files:**
- Create: `agent_app/runtime/policy_simulation_cases.py`
- Test: `tests/unit/test_policy_simulation.py` (extend)

- [ ] **Step 1: Write failing tests for case extraction**

Append to `tests/unit/test_policy_simulation.py`:

```python
from agent_app.runtime.policy_simulation_cases import audit_event_to_simulation_case
from agent_app.governance.audit import AuditEvent


class TestAuditEventToSimulationCase:
    def test_allowed_event(self):
        event = AuditEvent(
            event_id="evt_1",
            event_type="policy.runtime.enforcement.allowed",
            data={
                "action_type": "tool.execute",
                "tool_name": "refund.request",
                "risk_level": "high",
                "user_id": "user_1",
                "actor_id": "actor_1",
                "tenant_id": "tenant_1",
                "roles": ["admin"],
                "permissions": ["refund:create"],
                "subject": "user_1",
            },
        )
        case = audit_event_to_simulation_case(event)
        assert case is not None
        assert case.action_type == "tool.execute"
        assert case.baseline_status == "allowed"
        assert case.tool_name == "refund.request"
        assert case.risk_level == "high"
        assert case.user_id == "user_1"
        assert case.actor_id == "actor_1"
        assert case.tenant_id == "tenant_1"
        assert case.roles == ["admin"]
        assert case.permissions == ["refund:create"]
        assert case.subject == "user_1"

    def test_denied_event(self):
        event = AuditEvent(
            event_id="evt_2",
            event_type="policy.runtime.enforcement.denied",
            data={"action_type": "tool.execute", "tool_name": "refund.request"},
        )
        case = audit_event_to_simulation_case(event)
        assert case is not None
        assert case.baseline_status == "denied"

    def test_approval_required_event(self):
        event = AuditEvent(
            event_id="evt_3",
            event_type="policy.runtime.enforcement.approval_required",
            data={"action_type": "tool.execute"},
        )
        case = audit_event_to_simulation_case(event)
        assert case is not None
        assert case.baseline_status == "approval_required"

    def test_evaluated_event(self):
        event = AuditEvent(
            event_id="evt_4",
            event_type="policy.runtime.evaluated",
            data={
                "action_type": "tool.execute",
                "status": "allowed",
                "tool_name": "some.tool",
            },
        )
        case = audit_event_to_simulation_case(event)
        assert case is not None
        assert case.baseline_status == "allowed"
        assert case.tool_name == "some.tool"

    def test_unsupported_event_returns_none(self):
        event = AuditEvent(
            event_id="evt_5",
            event_type="recovery.daemon_tick_started",
            data={},
        )
        case = audit_event_to_simulation_case(event)
        assert case is None

    def test_missing_fields_tolerated(self):
        event = AuditEvent(
            event_id="evt_6",
            event_type="policy.runtime.enforcement.allowed",
            data={"action_type": "tool.execute"},
        )
        case = audit_event_to_simulation_case(event)
        assert case is not None
        assert case.tool_name is None
        assert case.risk_level is None
        assert case.user_id is None
        assert case.roles == []
        assert case.permissions == []

    def test_case_id_has_psc_prefix(self):
        event = AuditEvent(
            event_id="evt_7",
            event_type="policy.runtime.enforcement.allowed",
            data={"action_type": "tool.execute"},
        )
        case = audit_event_to_simulation_case(event)
        assert case is not None
        assert case.case_id.startswith("psc_")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation.py::TestAuditEventToSimulationCase -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement case extraction**

Create `agent_app/runtime/policy_simulation_cases.py`:

```python
"""Audit-to-simulation case extraction — converts enforcement audit events into simulation cases.

Phase 40: Historical audit replay for policy validation.
"""
from __future__ import annotations

import uuid
from typing import Any

from agent_app.governance.policy_simulation import PolicySimulationCase

# Audit event types that map to enforcement decisions
_ENFORCEMENT_EVENT_MAP: dict[str, str] = {
    "policy.runtime.enforcement.allowed": "allowed",
    "policy.runtime.enforcement.denied": "denied",
    "policy.runtime.enforcement.approval_required": "approval_required",
}

_EVALUATED_EVENT_TYPE = "policy.runtime.evaluated"


def audit_event_to_simulation_case(
    event: Any,
) -> PolicySimulationCase | None:
    """Convert a runtime enforcement audit event into a simulation case.

    Supports:
      - policy.runtime.enforcement.{allowed,denied,approval_required}
      - policy.runtime.evaluated (extracts status from data)

    Returns None for unsupported event types.
    Tolerates missing fields — sets them to None/empty.
    """
    data = getattr(event, "data", None) or {}
    event_type = getattr(event, "event_type", None)

    if event_type in _ENFORCEMENT_EVENT_MAP:
        baseline_status = _ENFORCEMENT_EVENT_MAP[event_type]
    elif event_type == _EVALUATED_EVENT_TYPE:
        baseline_status = data.get("status")
        if baseline_status is None:
            return None
    else:
        return None

    case_id = f"psc_{uuid.uuid4().hex[:12]}"

    return PolicySimulationCase(
        case_id=case_id,
        action_type=data.get("action_type", "unknown"),
        subject=data.get("subject"),
        tool_name=data.get("tool_name"),
        risk_level=data.get("risk_level"),
        actor_id=data.get("actor_id"),
        user_id=data.get("user_id"),
        tenant_id=data.get("tenant_id"),
        roles=data.get("roles", []) or [],
        permissions=data.get("permissions", []) or [],
        baseline_status=baseline_status,
        metadata={k: v for k, v in data.items() if k not in {
            "action_type", "subject", "tool_name", "risk_level",
            "actor_id", "user_id", "tenant_id", "roles",
            "permissions", "status",
        }},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation.py::TestAuditEventToSimulationCase -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_simulation_cases.py tests/unit/test_policy_simulation.py
git commit -m "feat: Phase 40 Task 2 — audit-to-simulation case extraction"
```

---

### Task 3: Candidate Policy Store

**Files:**
- Create: `agent_app/runtime/policy_candidate_store.py`
- Test: `tests/unit/test_policy_simulation.py` (extend)

- [ ] **Step 1: Write failing tests for candidate store**

Append to `tests/unit/test_policy_simulation.py`:

```python
from agent_app.governance.runtime_policy import RuntimePolicyRule, RuntimePolicyEffect, RuntimePolicyRuleStatus
from agent_app.governance.policy_enforcement import PolicyActionType
from agent_app.runtime.policy_candidate_store import (
    CandidateRuntimePolicySet,
    build_candidate_policy_store,
)


def _make_rule(name: str, effect: RuntimePolicyEffect, **kwargs) -> RuntimePolicyRule:
    return RuntimePolicyRule(
        rule_id=f"rpr_{name}",
        name=name,
        action_type=kwargs.get("action_type", PolicyActionType.TOOL_EXECUTE),
        effect=effect,
        tool_name=kwargs.get("tool_name"),
        risk_level=kwargs.get("risk_level"),
        status=kwargs.get("status", RuntimePolicyRuleStatus.ENABLED),
    )


class TestCandidateRuntimePolicySet:
    def test_model(self):
        rules = [_make_rule("deny_refunds", RuntimePolicyEffect.DENY)]
        cs = CandidateRuntimePolicySet(name="test_set", rules=rules)
        assert cs.name == "test_set"
        assert len(cs.rules) == 1

    def test_default_name(self):
        cs = CandidateRuntimePolicySet(rules=[])
        assert cs.name is None


class TestBuildCandidatePolicyStore:
    def test_candidate_only(self):
        candidate_rules = [_make_rule("deny_all", RuntimePolicyEffect.DENY)]
        store = build_candidate_policy_store(
            base_rules=[], candidate_rules=candidate_rules, include_base=False,
        )
        rules = _run_async(store.list())
        assert len(rules) == 1
        assert rules[0].name == "deny_all"

    def test_base_plus_candidate(self):
        base_rules = [_make_rule("allow_all", RuntimePolicyEffect.ALLOW)]
        candidate_rules = [_make_rule("deny_refunds", RuntimePolicyEffect.DENY, tool_name="refund.request")]
        store = build_candidate_policy_store(
            base_rules=base_rules, candidate_rules=candidate_rules, include_base=True,
        )
        rules = _run_async(store.list())
        assert len(rules) == 2

    def test_disabled_candidate_ignored(self):
        candidate_rules = [
            _make_rule("disabled_rule", RuntimePolicyEffect.DENY, status=RuntimePolicyRuleStatus.DISABLED),
        ]
        store = build_candidate_policy_store(
            base_rules=[], candidate_rules=candidate_rules, include_base=False,
        )
        # Disabled rules exist in store but evaluator filters them
        all_rules = _run_async(store.list())
        enabled_rules = _run_async(store.list(status=RuntimePolicyRuleStatus.ENABLED))
        assert len(all_rules) == 1
        assert len(enabled_rules) == 0

    def test_actual_runtime_store_not_mutated(self):
        from agent_app.runtime.runtime_policy_store import InMemoryRuntimePolicyStore
        actual_store = InMemoryRuntimePolicyStore()
        # Pre-populate actual store
        _run_async(actual_store.create(_make_rule("base_rule", RuntimePolicyEffect.ALLOW)))

        # Build candidate store — should not affect actual
        candidate_rules = [_make_rule("candidate_rule", RuntimePolicyEffect.DENY)]
        candidate_store = build_candidate_policy_store(
            base_rules=[], candidate_rules=candidate_rules, include_base=False,
        )

        # Actual store should still only have base_rule
        actual_rules = _run_async(actual_store.list())
        assert len(actual_rules) == 1
        assert actual_rules[0].name == "base_rule"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation.py::TestCandidateRuntimePolicySet tests/unit/test_policy_simulation.py::TestBuildCandidatePolicyStore -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement candidate store**

Create `agent_app/runtime/policy_candidate_store.py`:

```python
"""Candidate policy store — builds isolated runtime policy stores for simulation.

Phase 40: Simulate candidate rules without mutating active policy store.
"""
from __future__ import annotations

from pydantic import BaseModel

from agent_app.governance.runtime_policy import RuntimePolicyRule
from agent_app.runtime.runtime_policy_store import InMemoryRuntimePolicyStore, RuntimePolicyStore


class CandidateRuntimePolicySet(BaseModel):
    """A named set of candidate runtime policy rules for simulation."""

    name: str | None = None
    rules: list[RuntimePolicyRule] = []


def build_candidate_policy_store(
    base_rules: list[RuntimePolicyRule],
    candidate_rules: list[RuntimePolicyRule],
    include_base: bool = True,
) -> RuntimePolicyStore:
    """Build an isolated InMemoryRuntimePolicyStore for simulation.

    Args:
        base_rules: Existing runtime policy rules (from active store).
        candidate_rules: New candidate rules to test.
        include_base: If True, include base rules alongside candidates.
                      If False, only candidate rules are included.

    Returns:
        An InMemoryRuntimePolicyStore populated with the appropriate rules.
        This store is independent of the active runtime policy store.
    """
    store = InMemoryRuntimePolicyStore()
    all_rules: list[RuntimePolicyRule] = []

    if include_base:
        all_rules.extend(base_rules)

    all_rules.extend(candidate_rules)

    import asyncio

    async def _populate() -> None:
        for rule in all_rules:
            await store.create(rule)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_populate())
    finally:
        loop.close()

    return store
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation.py::TestCandidateRuntimePolicySet tests/unit/test_policy_simulation.py::TestBuildCandidatePolicyStore -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_candidate_store.py tests/unit/test_policy_simulation.py
git commit -m "feat: Phase 40 Task 3 — candidate policy store"
```

---

### Task 4: PolicySimulationService

**Files:**
- Create: `agent_app/runtime/policy_simulation_service.py`
- Test: `tests/unit/test_policy_simulation.py` (extend)

- [ ] **Step 1: Write failing tests for simulation service**

Append to `tests/unit/test_policy_simulation.py`:

```python
from agent_app.governance.audit import AuditEvent, InMemoryAuditLogger
from agent_app.runtime.runtime_policy_store import InMemoryRuntimePolicyStore
from agent_app.runtime.policy_simulation_service import PolicySimulationService


class TestPolicySimulationService:
    def _make_service(
        self,
        audit_logger: InMemoryAuditLogger | None = None,
        runtime_policy_store: InMemoryRuntimePolicyStore | None = None,
    ) -> PolicySimulationService:
        return PolicySimulationService(
            audit_logger=audit_logger,
            runtime_policy_store=runtime_policy_store,
        )

    def _log_enforcement_event(
        self,
        logger: InMemoryAuditLogger,
        status: str,
        action_type: str = "tool.execute",
        tool_name: str = "refund.request",
        user_id: str = "user_1",
    ) -> None:
        event_type = f"policy.runtime.enforcement.{status}"
        event = AuditEvent(
            event_id=f"evt_{status}",
            event_type=event_type,
            data={
                "action_type": action_type,
                "tool_name": tool_name,
                "user_id": user_id,
                "risk_level": "high",
            },
        )
        _run_async(logger.log(event))

    def test_collect_cases_from_audit(self):
        logger = InMemoryAuditLogger()
        self._log_enforcement_event(logger, "allowed")
        self._log_enforcement_event(logger, "denied")
        service = self._make_service(audit_logger=logger)
        cases = _run_async(service.collect_cases_from_audit())
        assert len(cases) == 2

    def test_simulate_unchanged(self):
        service = self._make_service()
        cases = [
            PolicySimulationCase(
                case_id="psc_1",
                action_type="tool.execute",
                tool_name="refund.request",
                baseline_status="allowed",
            ),
        ]
        # Candidate rules that don't match — no change
        candidate_rules = [
            _make_rule("deny_other", RuntimePolicyEffect.DENY, tool_name="other.tool"),
        ]
        report = _run_async(service.simulate_cases(
            cases=cases,
            candidate_rules=candidate_rules,
            include_base=False,
        ))
        assert report.summary.unchanged == 1
        assert report.summary.would_deny == 0

    def test_simulate_would_deny(self):
        service = self._make_service()
        cases = [
            PolicySimulationCase(
                case_id="psc_2",
                action_type="tool.execute",
                tool_name="refund.request",
                baseline_status="allowed",
            ),
        ]
        candidate_rules = [
            _make_rule("deny_refunds", RuntimePolicyEffect.DENY, tool_name="refund.request"),
        ]
        report = _run_async(service.simulate_cases(
            cases=cases,
            candidate_rules=candidate_rules,
            include_base=False,
        ))
        assert report.summary.would_deny == 1

    def test_simulate_would_allow(self):
        service = self._make_service()
        cases = [
            PolicySimulationCase(
                case_id="psc_3",
                action_type="tool.execute",
                tool_name="refund.request",
                baseline_status="denied",
                permissions=["refund:create"],
                roles=["admin"],
            ),
        ]
        # Allow rule with matching permissions
        candidate_rules = [
            _make_rule("allow_refunds", RuntimePolicyEffect.ALLOW, tool_name="refund.request"),
        ]
        report = _run_async(service.simulate_cases(
            cases=cases,
            candidate_rules=candidate_rules,
            include_base=False,
        ))
        assert report.summary.would_allow >= 1

    def test_simulate_would_require_approval(self):
        service = self._make_service()
        cases = [
            PolicySimulationCase(
                case_id="psc_4",
                action_type="tool.execute",
                tool_name="refund.request",
                baseline_status="allowed",
                permissions=["refund:create"],
                roles=["admin"],
            ),
        ]
        candidate_rules = [
            _make_rule("require_approval_refunds", RuntimePolicyEffect.REQUIRE_APPROVAL, tool_name="refund.request"),
        ]
        report = _run_async(service.simulate_cases(
            cases=cases,
            candidate_rules=candidate_rules,
            include_base=False,
        ))
        assert report.summary.would_require_approval >= 1

    def test_simulate_errors_captured(self):
        service = self._make_service()
        cases = [
            PolicySimulationCase(
                case_id="psc_err",
                action_type="",  # Empty action type may cause issues
                baseline_status="allowed",
            ),
        ]
        candidate_rules = [
            _make_rule("some_rule", RuntimePolicyEffect.DENY),
        ]
        report = _run_async(service.simulate_cases(
            cases=cases,
            candidate_rules=candidate_rules,
            include_base=False,
        ))
        # Should not crash; errors should be captured gracefully
        assert report.summary.total == 1

    def test_simulate_from_audit(self):
        logger = InMemoryAuditLogger()
        self._log_enforcement_event(logger, "allowed")
        self._log_enforcement_event(logger, "denied")
        service = self._make_service(audit_logger=logger)
        candidate_rules = [
            _make_rule("deny_refunds", RuntimePolicyEffect.DENY, tool_name="refund.request"),
        ]
        report = _run_async(service.simulate_from_audit(
            candidate_rules=candidate_rules,
            include_base=False,
        ))
        assert report.summary.total == 2
        assert report.simulation_id.startswith("psim_")

    def test_limit_applied(self):
        logger = InMemoryAuditLogger()
        for i in range(10):
            self._log_enforcement_event(logger, "allowed", tool_name=f"tool_{i}")
        service = self._make_service(audit_logger=logger)
        cases = _run_async(service.collect_cases_from_audit(limit=5))
        assert len(cases) == 5

    def test_window_filters_applied(self):
        logger = InMemoryAuditLogger()
        self._log_enforcement_event(logger, "allowed")
        service = self._make_service(audit_logger=logger)
        # Window in the future — should find nothing
        future = datetime(2099, 1, 1, tzinfo=timezone.utc)
        cases = _run_async(service.collect_cases_from_audit(window_start=future))
        assert len(cases) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation.py::TestPolicySimulationService -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement PolicySimulationService**

Create `agent_app/runtime/policy_simulation_service.py`:

```python
"""Policy simulation service — replay audit events against candidate rules.

Phase 40: Test runtime policy rule changes before deploying them.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from agent_app.core.context import RunContext
from agent_app.governance.policy_enforcement import PolicyActionType
from agent_app.governance.policy_simulation import (
    PolicySimulationCase,
    PolicySimulationOutcome,
    PolicySimulationReport,
    PolicySimulationResult,
    PolicySimulationSummary,
)
from agent_app.runtime.policy_candidate_store import build_candidate_policy_store
from agent_app.runtime.runtime_policy_evaluator import RuntimePolicyEvaluationRequest, RuntimePolicyEvaluator
from agent_app.governance.runtime_policy import RuntimePolicyRule

# Lazy import to avoid circular dependencies
AuditLogger: Any = None


def _get_audit_logger_type() -> Any:
    global AuditLogger
    if AuditLogger is None:
        from agent_app.governance.audit import AuditLogger as _AL
        AuditLogger = _AL
    return AuditLogger


class PolicySimulationService:
    """Service for simulating candidate runtime policy rules against historical audit events."""

    def __init__(
        self,
        audit_logger: Any = None,
        runtime_policy_store: Any = None,
    ) -> None:
        self._audit_logger = audit_logger
        self._runtime_policy_store = runtime_policy_store

    async def collect_cases_from_audit(
        self,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        limit: int | None = None,
    ) -> list[PolicySimulationCase]:
        """Collect simulation cases from enforcement audit events."""
        from agent_app.runtime.policy_simulation_cases import audit_event_to_simulation_case

        if self._audit_logger is None:
            return []

        events = await self._audit_logger.list_events()

        cases: list[PolicySimulationCase] = []
        for event in events:
            # Window filtering
            if window_start is not None:
                event_time = getattr(event, "created_at", None)
                if event_time is not None and event_time < window_start:
                    continue
            if window_end is not None:
                event_time = getattr(event, "created_at", None)
                if event_time is not None and event_time > window_end:
                    continue

            case = audit_event_to_simulation_case(event)
            if case is not None:
                cases.append(case)

        if limit is not None:
            cases = cases[:limit]

        return cases

    async def simulate_cases(
        self,
        cases: list[PolicySimulationCase],
        candidate_rules: list[RuntimePolicyRule],
        include_base: bool = True,
        name: str | None = None,
    ) -> PolicySimulationReport:
        """Simulate cases against candidate rules and produce a report."""
        # Get base rules if available
        base_rules: list[RuntimePolicyRule] = []
        if include_base and self._runtime_policy_store is not None:
            base_rules = await self._runtime_policy_store.list()

        # Build candidate store
        candidate_store = build_candidate_policy_store(
            base_rules=base_rules,
            candidate_rules=candidate_rules,
            include_base=include_base,
        )

        # Create evaluator over candidate store
        evaluator = RuntimePolicyEvaluator(policy_store=candidate_store)

        # Simulate each case
        results: list[PolicySimulationResult] = []
        summary = PolicySimulationSummary()

        for case in cases:
            result = await self._simulate_one_case(case, evaluator)
            results.append(result)
            summary.total += 1

            if result.outcome == PolicySimulationOutcome.UNCHANGED:
                summary.unchanged += 1
            elif result.outcome == PolicySimulationOutcome.WOULD_ALLOW:
                summary.would_allow += 1
            elif result.outcome == PolicySimulationOutcome.WOULD_DENY:
                summary.would_deny += 1
            elif result.outcome == PolicySimulationOutcome.WOULD_REQUIRE_APPROVAL:
                summary.would_require_approval += 1
            elif result.outcome == PolicySimulationOutcome.WOULD_CHANGE:
                summary.would_change += 1
            elif result.outcome == PolicySimulationOutcome.ERROR:
                summary.errors += 1

        candidate_rule_ids = [r.rule_id for r in candidate_rules]

        return PolicySimulationReport(
            simulation_id=f"psim_{uuid.uuid4().hex[:12]}",
            name=name,
            generated_at=datetime.now(timezone.utc),
            candidate_rule_ids=candidate_rule_ids,
            summary=summary,
            results=results,
        )

    async def simulate_from_audit(
        self,
        candidate_rules: list[RuntimePolicyRule],
        include_base: bool = True,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        limit: int | None = None,
        name: str | None = None,
    ) -> PolicySimulationReport:
        """Collect cases from audit and simulate them against candidate rules."""
        cases = await self.collect_cases_from_audit(
            window_start=window_start,
            window_end=window_end,
            limit=limit,
        )
        return await self.simulate_cases(
            cases=cases,
            candidate_rules=candidate_rules,
            include_base=include_base,
            name=name,
        )

    async def _simulate_one_case(
        self,
        case: PolicySimulationCase,
        evaluator: RuntimePolicyEvaluator,
    ) -> PolicySimulationResult:
        """Simulate a single case against the evaluator."""
        try:
            # Build evaluation request from case
            action_type = case.action_type
            try:
                action_type_enum = PolicyActionType(action_type)
            except ValueError:
                action_type_enum = PolicyActionType.TOOL_EXECUTE

            context = RunContext(
                user_id=case.user_id or "simulation_user",
                tenant_id=case.tenant_id,
                roles=case.roles,
                permissions=case.permissions,
            )

            request = RuntimePolicyEvaluationRequest(
                action_type=action_type_enum,
                subject=case.subject,
                tool_name=case.tool_name,
                risk_level=case.risk_level,
                context=context,
                metadata=case.metadata,
            )

            decision = await evaluator.evaluate(request)

            # Map decision status to candidate status string
            candidate_status = decision.status.value

            # Determine outcome
            outcome = self._compare_statuses(case.baseline_status, candidate_status)

            return PolicySimulationResult(
                case_id=case.case_id,
                baseline_status=case.baseline_status,
                candidate_status=candidate_status,
                outcome=outcome,
                reason=decision.reason,
                decision_id=decision.decision_id,
            )
        except Exception as exc:
            return PolicySimulationResult(
                case_id=case.case_id,
                baseline_status=case.baseline_status,
                outcome=PolicySimulationOutcome.ERROR,
                errors=[str(exc)],
            )

    @staticmethod
    def _compare_statuses(
        baseline: str | None,
        candidate: str,
    ) -> PolicySimulationOutcome:
        """Compare baseline and candidate decision statuses."""
        if baseline == candidate:
            return PolicySimulationOutcome.UNCHANGED

        if candidate == "allowed" and baseline != "allowed":
            return PolicySimulationOutcome.WOULD_ALLOW

        if candidate == "denied" and baseline != "denied":
            return PolicySimulationOutcome.WOULD_DENY

        if candidate == "approval_required" and baseline != "approval_required":
            return PolicySimulationOutcome.WOULD_REQUIRE_APPROVAL

        return PolicySimulationOutcome.WOULD_CHANGE
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation.py::TestPolicySimulationService -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_simulation_service.py tests/unit/test_policy_simulation.py
git commit -m "feat: Phase 40 Task 4 — PolicySimulationService"
```

---

### Task 5: Policy Validation

**Files:**
- Create: `agent_app/runtime/policy_validation.py`
- Test: `tests/unit/test_policy_simulation.py` (extend)

- [ ] **Step 1: Write failing tests for validation**

Append to `tests/unit/test_policy_simulation.py`:

```python
from agent_app.runtime.policy_validation import (
    PolicyValidationIssue,
    PolicyValidationReport,
    PolicyValidationSeverity,
    RuntimePolicyValidator,
)


class TestPolicyValidationSeverity:
    def test_enum_values(self):
        assert PolicyValidationSeverity.ERROR == "error"
        assert PolicyValidationSeverity.WARNING == "warning"
        assert PolicyValidationSeverity.INFO == "info"


class TestPolicyValidationIssue:
    def test_issue(self):
        issue = PolicyValidationIssue(
            severity=PolicyValidationSeverity.WARNING,
            code="broad_rule",
            message="Rule has no tool_name or risk_level",
            rule_id="rpr_abc",
        )
        assert issue.severity == PolicyValidationSeverity.WARNING
        assert issue.code == "broad_rule"
        assert issue.rule_id == "rpr_abc"


class TestPolicyValidationReport:
    def test_valid_report(self):
        report = PolicyValidationReport(valid=True)
        assert report.valid is True
        assert report.issues == []

    def test_invalid_report(self):
        report = PolicyValidationReport(
            valid=False,
            issues=[
                PolicyValidationIssue(
                    severity=PolicyValidationSeverity.ERROR,
                    code="duplicate_name",
                    message="Duplicate rule name",
                ),
            ],
        )
        assert report.valid is False
        assert len(report.issues) == 1


class TestRuntimePolicyValidator:
    def test_valid_rules_pass(self):
        rules = [
            _make_rule("allow_payments", RuntimePolicyEffect.ALLOW, tool_name="payment.process"),
            _make_rule("deny_refunds", RuntimePolicyEffect.DENY, tool_name="refund.request"),
        ]
        validator = RuntimePolicyValidator()
        report = validator.validate_rules(rules)
        assert report.valid is True

    def test_duplicate_names_warning(self):
        rules = [
            _make_rule("same_name", RuntimePolicyEffect.ALLOW, tool_name="tool_a"),
            _make_rule("same_name", RuntimePolicyEffect.DENY, tool_name="tool_b"),
        ]
        validator = RuntimePolicyValidator()
        report = validator.validate_rules(rules)
        dup_issues = [i for i in report.issues if i.code == "duplicate_name"]
        assert len(dup_issues) > 0

    def test_broad_rule_warning(self):
        rules = [
            _make_rule("broad_rule", RuntimePolicyEffect.DENY),
            # No tool_name, no risk_level
        ]
        # Force tool_name and risk_level to None
        rules[0].tool_name = None
        rules[0].risk_level = None
        validator = RuntimePolicyValidator()
        report = validator.validate_rules(rules)
        broad_issues = [i for i in report.issues if i.code == "broad_rule"]
        assert len(broad_issues) > 0

    def test_deny_with_approval_policy_warning(self):
        from agent_app.governance.policy_rollout_approval import (
            RolloutApprovalPolicy,
            RolloutApprovalPolicyType,
        )
        rules = [
            RuntimePolicyRule(
                rule_id="rpr_deny_ap",
                name="deny_with_ap",
                action_type=PolicyActionType.TOOL_EXECUTE,
                effect=RuntimePolicyEffect.DENY,
                approval_policy=RolloutApprovalPolicy(
                    policy_type=RolloutApprovalPolicyType.SINGLE,
                    required_approvals=1,
                ),
            ),
        ]
        validator = RuntimePolicyValidator()
        report = validator.validate_rules(rules)
        ap_issues = [i for i in report.issues if i.code == "deny_with_approval_policy"]
        assert len(ap_issues) > 0

    def test_require_approval_without_policy_warning(self):
        rules = [
            _make_rule("req_ap_no_policy", RuntimePolicyEffect.REQUIRE_APPROVAL, tool_name="some.tool"),
        ]
        rules[0].approval_policy = None
        validator = RuntimePolicyValidator()
        report = validator.validate_rules(rules)
        nap_issues = [i for i in report.issues if i.code == "require_approval_without_policy"]
        assert len(nap_issues) > 0

    def test_conflicting_rules_warning(self):
        rules = [
            _make_rule("allow_refunds", RuntimePolicyEffect.ALLOW, tool_name="refund.request"),
            _make_rule("deny_refunds", RuntimePolicyEffect.DENY, tool_name="refund.request"),
        ]
        validator = RuntimePolicyValidator()
        report = validator.validate_rules(rules)
        conflict_issues = [i for i in report.issues if i.code == "conflicting_rules"]
        assert len(conflict_issues) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation.py::TestPolicyValidationSeverity tests/unit/test_policy_simulation.py::TestPolicyValidationIssue tests/unit/test_policy_simulation.py::TestPolicyValidationReport tests/unit/test_policy_simulation.py::TestRuntimePolicyValidator -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement validation**

Create `agent_app/runtime/policy_validation.py`:

```python
"""Runtime policy validation — checks candidate rules for issues before simulation.

Phase 40: Pre-simulation validation of runtime policy rules.
"""
from __future__ import annotations

from enum import StrEnum
from collections import Counter

from pydantic import BaseModel, Field

from agent_app.governance.runtime_policy import RuntimePolicyEffect, RuntimePolicyRule


class PolicyValidationSeverity(str, StrEnum):
    """Severity level for validation issues."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class PolicyValidationIssue(BaseModel):
    """A single validation issue found in candidate rules."""

    severity: PolicyValidationSeverity
    code: str
    message: str
    rule_id: str | None = None
    field: str | None = None


class PolicyValidationReport(BaseModel):
    """Report of validation issues found in candidate rules."""

    valid: bool
    issues: list[PolicyValidationIssue] = Field(default_factory=list)


class RuntimePolicyValidator:
    """Validates candidate runtime policy rules before simulation."""

    def validate_rules(
        self,
        rules: list[RuntimePolicyRule],
    ) -> PolicyValidationReport:
        """Validate candidate rules and return a report of issues.

        Checks:
        - Duplicate rule names (warning)
        - DENY rule with approval_policy (warning — approval never triggers on DENY)
        - REQUIRE_APPROVAL rule without approval_policy (warning)
        - Broad rule: no tool_name and no risk_level (warning)
        - Conflicting rules: same action_type/tool/risk with ALLOW and DENY (warning)
        """
        issues: list[PolicyValidationIssue] = []

        # Check duplicate names
        name_counts = Counter(r.name for r in rules)
        for name, count in name_counts.items():
            if count > 1:
                issues.append(PolicyValidationIssue(
                    severity=PolicyValidationSeverity.WARNING,
                    code="duplicate_name",
                    message=f"Duplicate rule name '{name}' appears {count} times",
                ))

        for rule in rules:
            # DENY with approval_policy
            if rule.effect == RuntimePolicyEffect.DENY and rule.approval_policy is not None:
                issues.append(PolicyValidationIssue(
                    severity=PolicyValidationSeverity.WARNING,
                    code="deny_with_approval_policy",
                    message=f"DENY rule '{rule.name}' has approval_policy — approval is never triggered for DENY",
                    rule_id=rule.rule_id,
                ))

            # REQUIRE_APPROVAL without approval_policy
            if rule.effect == RuntimePolicyEffect.REQUIRE_APPROVAL and rule.approval_policy is None:
                issues.append(PolicyValidationIssue(
                    severity=PolicyValidationSeverity.WARNING,
                    code="require_approval_without_policy",
                    message=f"REQUIRE_APPROVAL rule '{rule.name}' has no approval_policy — approval flow may be ambiguous",
                    rule_id=rule.rule_id,
                ))

            # Broad rule (no tool_name and no risk_level)
            if rule.tool_name is None and rule.risk_level is None:
                issues.append(PolicyValidationIssue(
                    severity=PolicyValidationSeverity.WARNING,
                    code="broad_rule",
                    message=f"Rule '{rule.name}' has no tool_name or risk_level — will match all requests for this action_type",
                    rule_id=rule.rule_id,
                ))

        # Check conflicting rules (same action_type + tool_name + risk_level but different effects)
        _check_conflicts(rules, issues)

        # Report validity — only ERROR severity issues make it invalid
        has_errors = any(i.severity == PolicyValidationSeverity.ERROR for i in issues)
        return PolicyValidationReport(valid=not has_errors, issues=issues)


def _check_conflicts(
    rules: list[RuntimePolicyRule],
    issues: list[PolicyValidationIssue],
) -> None:
    """Check for conflicting rules with same scope but different effects."""
    # Group by (action_type, tool_name, risk_level)
    from collections import defaultdict

    groups: dict[tuple, list[RuntimePolicyRule]] = defaultdict(list)
    for rule in rules:
        key = (rule.action_type, rule.tool_name, rule.risk_level)
        groups[key].append(rule)

    for key, group in groups.items():
        effects = {r.effect for r in group}
        if len(effects) > 1 and RuntimePolicyEffect.DENY in effects:
            # ALLOW + DENY or REQUIRE_APPROVAL + DENY — most restrictive wins
            rule_names = [r.name for r in group]
            issues.append(PolicyValidationIssue(
                severity=PolicyValidationSeverity.WARNING,
                code="conflicting_rules",
                message=(
                    f"Conflicting effects for action_type={key[0]}, "
                    f"tool_name={key[1]}, risk_level={key[2]}: "
                    f"{', '.join(rule_names)} — most restrictive (DENY) wins"
                ),
            ))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation.py::TestPolicyValidationSeverity tests/unit/test_policy_simulation.py::TestPolicyValidationIssue tests/unit/test_policy_simulation.py::TestPolicyValidationReport tests/unit/test_policy_simulation.py::TestRuntimePolicyValidator -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_validation.py tests/unit/test_policy_simulation.py
git commit -m "feat: Phase 40 Task 5 — RuntimePolicyValidator"
```

---

### Task 6: Export, Config, Loader, RBAC, Events

**Files:**
- Modify: `agent_app/runtime/policy_compliance_export.py`
- Modify: `agent_app/config/schema.py`
- Modify: `agent_app/config/loader.py`
- Modify: `agent_app/governance/policy_rbac.py`
- Modify: `agent_app/governance/policy_change_event.py`
- Test: `tests/unit/test_policy_simulation.py` (extend)

- [ ] **Step 1: Write failing tests for export, config, RBAC, events**

Append to `tests/unit/test_policy_simulation.py`:

```python
from agent_app.runtime.policy_compliance_export import (
    simulation_report_to_json,
    simulation_report_to_csv_rows,
    validation_report_to_json,
)


class TestSimulationExport:
    def test_simulation_json_export(self):
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
        assert len(rows) >= 2  # One row per result

    def test_validation_json_export(self):
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
```

- [ ] **Step 2: Add export helpers to policy_compliance_export.py**

Add these functions to the end of `agent_app/runtime/policy_compliance_export.py`:

```python
from agent_app.governance.policy_simulation import PolicySimulationReport
from agent_app.runtime.policy_validation import PolicyValidationReport


def simulation_report_to_json(report: PolicySimulationReport) -> str:
    """Export simulation report as JSON string."""
    return report.model_dump_json(indent=2)


def simulation_report_to_csv_rows(report: PolicySimulationReport) -> list[dict[str, Any]]:
    """Export simulation report results as flat CSV-ready rows."""
    rows: list[dict[str, Any]] = []
    for result in report.results:
        rows.append({
            "case_id": result.case_id,
            "baseline_status": result.baseline_status,
            "candidate_status": result.candidate_status,
            "outcome": result.outcome.value,
            "reason": result.reason,
            "decision_id": result.decision_id,
            "errors": "; ".join(result.errors) if result.errors else "",
        })
    return rows


def validation_report_to_json(report: PolicyValidationReport) -> str:
    """Export validation report as JSON string."""
    return report.model_dump_json(indent=2)
```

Also add the imports at the top of the file.

- [ ] **Step 3: Add PolicySimulationConfig to schema.py**

Add to `agent_app/config/schema.py`:

```python
class PolicySimulationConfig(BaseModel):
    """Configuration for policy simulation and validation."""

    enabled: bool = Field(default=False, description="Enable policy simulation service")
```

- [ ] **Step 4: Add simulation permissions to policy_rbac.py**

Add to `PolicyReleasePermission` enum in `agent_app/governance/policy_rbac.py`:

```python
    SIMULATION_RUN = "policy.simulation.run"
    SIMULATION_VIEW = "policy.simulation.view"
    SIMULATION_EXPORT = "policy.simulation.export"
```

Add `PolicyReleasePermission.SIMULATION_VIEW` to `_DEFAULT_ALLOWED` set.

- [ ] **Step 5: Add simulation event types to policy_change_event.py**

Add to `PolicyChangeEventType` enum in `agent_app/governance/policy_change_event.py`:

```python
    SIMULATION_VALIDATION_RUN = "policy.simulation.validation_run"
    SIMULATION_REPLAY_RUN = "policy.simulation.replay_run"
    SIMULATION_EXPORT_GENERATED = "policy.simulation.export_generated"
    SIMULATION_PERMISSION_DENIED = "policy.simulation.permission_denied"
```

- [ ] **Step 6: Wire PolicySimulationService in loader.py**

In `agent_app/config/loader.py`, in the `load_config` function, add:

```python
# Wire policy simulation service if enabled
simulation_config = getattr(governance_config, "policy_simulation", None)
if simulation_config and simulation_config.enabled:
    from agent_app.runtime.policy_simulation_service import PolicySimulationService
    app.policy_simulation_service = PolicySimulationService(
        audit_logger=app.audit_logger,
        runtime_policy_store=app.runtime_policy_store,
    )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_simulation.py::TestSimulationExport tests/unit/test_policy_simulation.py::TestPolicySimulationConfig tests/unit/test_policy_simulation.py::TestSimulationRBAC tests/unit/test_policy_simulation.py::TestSimulationEvents -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add agent_app/runtime/policy_compliance_export.py agent_app/config/schema.py agent_app/config/loader.py agent_app/governance/policy_rbac.py agent_app/governance/policy_change_event.py tests/unit/test_policy_simulation.py
git commit -m "feat: Phase 40 Task 6 — export, config, loader, RBAC, events"
```

---

### Task 7: CLI Commands

**Files:**
- Modify: `agent_app/cli.py`
- Test: `tests/unit/test_policy_simulation_cli.py` (new)

- [ ] **Step 1: Create CLI tests**

Create `tests/unit/test_policy_simulation_cli.py` with tests for:
- `policy simulation validate` success
- `policy simulation validate` with errors exits non-zero
- `policy simulation replay` success
- `policy simulation replay --json`
- `policy simulation export` json/csv
- Invalid rules file exits non-zero
- Invalid datetime exits non-zero

Follow the existing pattern in `tests/unit/test_policy_observability_cli.py`.

- [ ] **Step 2: Implement CLI commands**

Add to `agent_app/cli.py`:
- `policy simulation validate --config --rules-file`
- `policy simulation replay --config --rules-file --since --until --limit --json`
- `policy simulation export --config --rules-file --format --output`

Parse candidate rules from YAML file using `RuntimePolicyRuleConfig` pattern.

- [ ] **Step 3: Run tests (GREEN)**

- [ ] **Step 4: Commit**

```bash
git add agent_app/cli.py tests/unit/test_policy_simulation_cli.py
git commit -m "feat: Phase 40 Task 7 — CLI simulation commands"
```

---

### Task 8: Console Pages

**Files:**
- Modify: `agent_app/console/router.py`
- Create: `agent_app/console/templates/policy_simulation.html`
- Create: `agent_app/console/templates/policy_simulation_report.html`
- Create: `agent_app/console/templates/policy_validation_report.html`
- Test: `tests/unit/test_policy_simulation_console.py` (new)

- [ ] **Step 1: Write console tests**

Create `tests/unit/test_policy_simulation_console.py` with tests for:
- Simulation page renders
- Validation POST works
- Replay POST works
- Errors render clearly

Follow the existing pattern in `tests/unit/test_policy_observability_console.py`.

- [ ] **Step 2: Implement console routes and templates**

Add routes:
- `GET /policy-console/simulation`
- `POST /policy-console/simulation/validate`
- `POST /policy-console/simulation/replay`

Create templates with textarea for candidate YAML rules, optional filters, validation report, simulation report.

- [ ] **Step 3: Run tests (GREEN)**

- [ ] **Step 4: Commit**

```bash
git add agent_app/console/router.py agent_app/console/templates/policy_simulation*.html agent_app/console/templates/policy_validation_report.html tests/unit/test_policy_simulation_console.py
git commit -m "feat: Phase 40 Task 8 — console simulation pages"
```

---

### Task 9: Documentation and Final Verification

**Files:**
- Modify: `docs/policy_release.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Create: `docs/release_checklist_phase40.md`

- [ ] **Step 1: Update documentation**

Phase 40 section in policy_release.md, CHANGELOG v0.28.0, README roadmap, release checklist.

- [ ] **Step 2: Run relevant test suite**

```bash
.venv/bin/python -m pytest tests/unit/test_policy_simulation.py tests/unit/test_policy_simulation_cli.py tests/unit/test_policy_simulation_console.py tests/unit/test_policy_observability.py tests/unit/test_runtime_policy.py -v
```
Expected: 0 failures

- [ ] **Step 3: Commit**

```bash
git add docs/policy_release.md CHANGELOG.md README.md docs/release_checklist_phase40.md
git commit -m "docs: Phase 40 documentation — policy testing, validation, and historical replay"
```

---

## Self-Review Checklist

- [x] Spec coverage: All 15 sections of the Phase 40 spec are addressed
- [x] Placeholder scan: No TBD/TODO placeholders
- [x] Type consistency: PolicySimulationOutcome, PolicySimulationCase, PolicySimulationResult, PolicySimulationSummary, PolicySimulationReport names used consistently
- [x] Prefix consistency: psc_ for cases, psim_ for simulations, matching spec
- [x] Backward compatibility: All new fields/params have defaults; existing RuntimePolicyEvaluator, PolicyObservabilityService, etc. unchanged
- [x] Import boundaries: No FastAPI/Jinja2/OpenAI in governance/runtime modules
- [x] No collision with existing PolicySimulator (Phase 25) — different namespace and purpose
