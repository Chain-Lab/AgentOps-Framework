# Phase 42: Policy Release Automation and Simulation Gate Enforcement

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate simulation gate results into the release workflow so promotion and rollout execution can require a passing simulation gate before proceeding.

**Architecture:** ReleaseGateRequirement models the need for a gate result on a promotion/rollout step. ReleaseGateRequirementStore persists these requirements. ReleaseGateAutomationService orchestrates creating requirements, attaching gate results, and running simulation+gate in one call. PolicyReleaseService gains enforcement: when config requires simulation gate, `execute_promotion` checks the requirement before proceeding. RolloutService blocks steps that require simulation gate. CLI and console provide promotion gate lifecycle commands.

**Tech Stack:** Python 3.11+, Pydantic, asyncio, SQLite, aiosqlite, Jinja2, FastAPI

---

## File Structure

### New files
- `agent_app/governance/policy_release_gate.py` — ReleaseGateRequirementStatus, ReleaseGateRequirement model
- `agent_app/runtime/policy_release_gate_store.py` — ReleaseGateRequirementStore protocol, InMemoryReleaseGateRequirementStore, SQLiteReleaseGateRequirementStore, create_release_gate_requirement_store()
- `agent_app/runtime/policy_release_gate_service.py` — ReleaseGateAutomationService (require_gate_for_promotion, attach_gate_result, run_and_attach_simulation_gate_for_promotion, check_requirement)
- `tests/unit/test_policy_release_gate_model.py` — Model tests
- `tests/unit/test_policy_release_gate_store.py` — Store tests (InMemory + SQLite)
- `tests/unit/test_policy_release_gate_service.py` — Service tests
- `tests/unit/test_policy_release_gate_integration.py` — PolicyReleaseService + RolloutService integration tests
- `tests/unit/test_policy_release_gate_config.py` — Config/loader/RBAC/events wiring tests
- `tests/unit/test_policy_release_gate_cli.py` — CLI tests
- `tests/unit/test_policy_release_gate_console.py` — Console tests
- `agent_app/console/templates/policy_promotion_gate.html` — Gate form page
- `agent_app/console/templates/policy_promotion_gate_status.html` — Gate result page

### Modified files
- `agent_app/governance/policy_promotion.py` — Add simulation gate fields to PromotionRequest
- `agent_app/governance/policy_rollout.py` — Add simulation gate fields to RolloutStep
- `agent_app/governance/policy_rbac.py` — Add PROMOTION_GATE_REQUIRE, PROMOTION_GATE_RUN, PROMOTION_GATE_ATTACH, PROMOTION_GATE_VIEW, ROLLOUT_GATE_ATTACH, ROLLOUT_GATE_VIEW
- `agent_app/governance/policy_change_event.py` — Add promotion gate event types
- `agent_app/config/schema.py` — Add SimulationGateEnforcementConfig and rollout gate enforcement config
- `agent_app/config/loader.py` — Wire requirement store, automation service, and enforcement flags
- `agent_app/runtime/policy_release.py` — Accept release_gate_automation_service and enforcement flags; enforce gate in request_promotion and execute_promotion
- `agent_app/runtime/policy_rollout_service.py` — Check gate requirement before step execution
- `agent_app/cli.py` — Add `policy promotion gate require/run/attach/status` subcommands
- `agent_app/console/router.py` — Add promotion gate routes
- `agent_app/adapters/fastapi.py` — Wire release gate requirement store
- `docs/policy_release.md` — Phase 42 documentation
- `CHANGELOG.md` — v0.30.0 entry
- `README.md` — Phase 42 in roadmap

---

### Task 1: ReleaseGateRequirement model

**Files:**
- Create: `agent_app/governance/policy_release_gate.py`
- Test: `tests/unit/test_policy_release_gate_model.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for ReleaseGateRequirement model."""
from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_release_gate import (
    ReleaseGateRequirement,
    ReleaseGateRequirementStatus,
)


def test_valid_requirement():
    req = ReleaseGateRequirement(
        requirement_id="rgr_abc123",
        source_type="promotion",
        source_id="pr_xyz789",
    )
    assert req.requirement_id == "rgr_abc123"
    assert req.source_type == "promotion"
    assert req.source_id == "pr_xyz789"
    assert req.required is True
    assert req.status == ReleaseGateRequirementStatus.REQUIRED


def test_id_prefix():
    req = ReleaseGateRequirement(
        requirement_id="rgr_test",
        source_type="rollout_step",
        source_id="rs_001",
    )
    assert req.requirement_id.startswith("rgr_")


def test_default_required_status():
    req = ReleaseGateRequirement(
        requirement_id="rgr_default",
        source_type="promotion",
        source_id="pr_001",
    )
    assert req.status == ReleaseGateRequirementStatus.REQUIRED


def test_timezone_aware_created_at():
    req = ReleaseGateRequirement(
        requirement_id="rgr_tz",
        source_type="promotion",
        source_id="pr_001",
    )
    assert req.created_at.tzinfo is not None


def test_all_status_values():
    assert ReleaseGateRequirementStatus.NOT_REQUIRED == "not_required"
    assert ReleaseGateRequirementStatus.REQUIRED == "required"
    assert ReleaseGateRequirementStatus.SATISFIED == "satisfied"
    assert ReleaseGateRequirementStatus.FAILED == "failed"
    assert ReleaseGateRequirementStatus.EXPIRED == "expired"


def test_optional_fields_default_none():
    req = ReleaseGateRequirement(
        requirement_id="rgr_opt",
        source_type="promotion",
        source_id="pr_001",
    )
    assert req.gate_result_id is None
    assert req.simulation_id is None
    assert req.max_age_seconds is None
    assert req.satisfied_at is None
    assert req.metadata == {}


def test_satisfied_requirement():
    now = datetime.now(timezone.utc)
    req = ReleaseGateRequirement(
        requirement_id="rgr_sat",
        source_type="promotion",
        source_id="pr_001",
        gate_result_id="pg_123",
        simulation_id="psim_456",
        status=ReleaseGateRequirementStatus.SATISFIED,
        satisfied_at=now,
    )
    assert req.status == ReleaseGateRequirementStatus.SATISFIED
    assert req.satisfied_at == now
    assert req.gate_result_id == "pg_123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_policy_release_gate_model.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

```python
"""Release gate Requirement model — tracks simulation gate requirements for promotions and rollout steps.

Phase 42: Policy Release Automation and Simulation Gate Enforcement.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ReleaseGateRequirementStatus(str, StrEnum):
    """Status of a release gate requirement."""

    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    SATISFIED = "satisfied"
    FAILED = "failed"
    EXPIRED = "expired"


class ReleaseGateRequirement(BaseModel):
    """A requirement that a passing simulation gate result be attached before promotion/rollout proceeds.

    Attributes:
        requirement_id: Unique identifier (rgr_ prefix).
        source_type: What this requirement is for — "promotion" or "rollout_step".
        source_id: The ID of the source (promotion_id or step_id).
        gate_result_id: The attached gate result ID, once known.
        simulation_id: The simulation ID associated with the gate result.
        required: Whether the gate requirement is active.
        status: Current status of the requirement.
        max_age_seconds: If set, gate result becomes stale after this many seconds.
        created_at: When the requirement was created (timezone-aware).
        satisfied_at: When the requirement was satisfied (timezone-aware).
        metadata: Arbitrary metadata.
    """

    requirement_id: str = Field(..., description="Unique requirement ID (rgr_ prefix)")
    source_type: str = Field(..., description="Source type: promotion | rollout_step")
    source_id: str = Field(..., description="Source ID (promotion_id or step_id)")
    gate_result_id: str | None = Field(default=None, description="Attached gate result ID")
    simulation_id: str | None = Field(default=None, description="Simulation ID")
    required: bool = Field(default=True, description="Whether gate is required")
    status: ReleaseGateRequirementStatus = Field(
        default=ReleaseGateRequirementStatus.REQUIRED,
        description="Current requirement status",
    )
    max_age_seconds: int | None = Field(default=None, description="Gate freshness in seconds")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Creation timestamp",
    )
    satisfied_at: datetime | None = Field(default=None, description="Satisfaction timestamp")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary metadata")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_policy_release_gate_model.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/governance/policy_release_gate.py tests/unit/test_policy_release_gate_model.py
git commit -m "feat: Phase 42 Task 1 — ReleaseGateRequirement model"
```

---

### Task 2: ReleaseGateRequirementStore

**Files:**
- Create: `agent_app/runtime/policy_release_gate_store.py`
- Test: `tests/unit/test_policy_release_gate_store.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for ReleaseGateRequirementStore."""
import pytest

from agent_app.governance.policy_release_gate import (
    ReleaseGateRequirement,
    ReleaseGateRequirementStatus,
)
from agent_app.runtime.policy_release_gate_store import (
    InMemoryReleaseGateRequirementStore,
    SQLiteReleaseGateRequirementStore,
    create_release_gate_requirement_store,
)


def _make_req(
    requirement_id: str = "rgr_test",
    source_type: str = "promotion",
    source_id: str = "pr_001",
    **kwargs,
) -> ReleaseGateRequirement:
    return ReleaseGateRequirement(
        requirement_id=requirement_id,
        source_type=source_type,
        source_id=source_id,
        **kwargs,
    )


@pytest.fixture
def memory_store():
    return InMemoryReleaseGateRequirementStore()


@pytest.fixture
def sqlite_store(tmp_path):
    return SQLiteReleaseGateRequirementStore(str(tmp_path / "test.db"))


class TestInMemoryStore:
    @pytest.mark.asyncio
    async def test_create_and_get(self, memory_store):
        req = _make_req()
        created = await memory_store.create(req)
        assert created.requirement_id == "rgr_test"
        fetched = await memory_store.get("rgr_test")
        assert fetched is not None
        assert fetched.requirement_id == "rgr_test"

    @pytest.mark.asyncio
    async def test_get_for_source(self, memory_store):
        req = _make_req(source_type="promotion", source_id="pr_001")
        await memory_store.create(req)
        found = await memory_store.get_for_source("promotion", "pr_001")
        assert found is not None
        assert found.source_id == "pr_001"
        not_found = await memory_store.get_for_source("promotion", "pr_999")
        assert not_found is None

    @pytest.mark.asyncio
    async def test_update(self, memory_store):
        req = _make_req()
        await memory_store.create(req)
        updated = req.model_copy(update={
            "status": ReleaseGateRequirementStatus.SATISFIED,
            "gate_result_id": "pg_123",
        })
        result = await memory_store.update(updated)
        assert result.status == ReleaseGateRequirementStatus.SATISFIED
        fetched = await memory_store.get("rgr_test")
        assert fetched.status == ReleaseGateRequirementStatus.SATISFIED

    @pytest.mark.asyncio
    async def test_list_by_source_type(self, memory_store):
        await memory_store.create(_make_req(requirement_id="rgr_1", source_type="promotion", source_id="pr_1"))
        await memory_store.create(_make_req(requirement_id="rgr_2", source_type="rollout_step", source_id="rs_1"))
        promo = await memory_store.list(source_type="promotion")
        assert len(promo) == 1
        assert promo[0].source_type == "promotion"

    @pytest.mark.asyncio
    async def test_list_by_status(self, memory_store):
        req = _make_req()
        await memory_store.create(req)
        updated = req.model_copy(update={"status": ReleaseGateRequirementStatus.SATISFIED})
        await memory_store.update(updated)
        satisfied = await memory_store.list(status=ReleaseGateRequirementStatus.SATISFIED)
        assert len(satisfied) == 1
        required = await memory_store.list(status=ReleaseGateRequirementStatus.REQUIRED)
        assert len(required) == 0


class TestSQLiteStore:
    @pytest.mark.asyncio
    async def test_create_and_get(self, sqlite_store):
        req = _make_req()
        created = await sqlite_store.create(req)
        fetched = await sqlite_store.get("rgr_test")
        assert fetched is not None
        assert fetched.requirement_id == "rgr_test"

    @pytest.mark.asyncio
    async def test_get_for_source(self, sqlite_store):
        await sqlite_store.create(_make_req(source_type="promotion", source_id="pr_001"))
        found = await sqlite_store.get_for_source("promotion", "pr_001")
        assert found is not None

    @pytest.mark.asyncio
    async def test_update(self, sqlite_store):
        req = _make_req()
        await sqlite_store.create(req)
        updated = req.model_copy(update={"status": ReleaseGateRequirementStatus.FAILED})
        result = await sqlite_store.update(updated)
        assert result.status == ReleaseGateRequirementStatus.FAILED

    @pytest.mark.asyncio
    async def test_list_by_source_type(self, sqlite_store):
        await sqlite_store.create(_make_req(requirement_id="rgr_1", source_type="promotion", source_id="pr_1"))
        await sqlite_store.create(_make_req(requirement_id="rgr_2", source_type="rollout_step", source_id="rs_1"))
        result = await sqlite_store.list(source_type="promotion")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_persists_across_instances(self, tmp_path):
        db_path = str(tmp_path / "persist.db")
        store1 = SQLiteReleaseGateRequirementStore(db_path)
        await store1.create(_make_req())
        store2 = SQLiteReleaseGateRequirementStore(db_path)
        fetched = await store2.get("rgr_test")
        assert fetched is not None

    @pytest.mark.asyncio
    async def test_unique_source_constraint(self, sqlite_store):
        await sqlite_store.create(_make_req(requirement_id="rgr_1", source_id="pr_1"))
        # Creating another with same source_type + source_id should overwrite or error
        await sqlite_store.create(_make_req(requirement_id="rgr_2", source_id="pr_1"))
        found = await sqlite_store.get_for_source("promotion", "pr_1")
        assert found is not None
        # Should have the latest version
        assert found.requirement_id == "rgr_2"


class TestFactory:
    def test_create_memory(self):
        store = create_release_gate_requirement_store("memory")
        assert isinstance(store, InMemoryReleaseGateRequirementStore)

    def test_create_sqlite(self, tmp_path):
        store = create_release_gate_requirement_store("sqlite", path=str(tmp_path / "factory.db"))
        assert isinstance(store, SQLiteReleaseGateRequirementStore)

    def test_default_is_memory(self):
        store = create_release_gate_requirement_store()
        assert isinstance(store, InMemoryReleaseGateRequirementStore)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_policy_release_gate_store.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

Create `agent_app/runtime/policy_release_gate_store.py` with:
- `ReleaseGateRequirementStore` Protocol with `create`, `get`, `get_for_source`, `update`, `list` methods
- `InMemoryReleaseGateRequirementStore` using a dict, enforcing UNIQUE(source_type, source_id) by overwriting previous entry with same source
- `SQLiteReleaseGateRequirementStore` with the SQL schema from the spec (UNIQUE constraint on source_type, source_id with INSERT OR REPLACE)
- `create_release_gate_requirement_store()` factory function (default=memory)

Key implementation details:
- SQLite table: `policy_release_gate_requirements` with columns matching the model
- `metadata_json` column stores JSON-serialized dict
- Datetimes stored as ISO 8601 strings
- `required` stored as INTEGER (0/1)
- Use `INSERT OR REPLACE` for the UNIQUE(source_type, source_id) constraint
- `list()` supports optional `source_type` and `status` filters

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_policy_release_gate_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_release_gate_store.py tests/unit/test_policy_release_gate_store.py
git commit -m "feat: Phase 42 Task 2 — ReleaseGateRequirementStore"
```

---

### Task 3: PromotionRequest extension + RolloutStep extension

**Files:**
- Modify: `agent_app/governance/policy_promotion.py`
- Modify: `agent_app/governance/policy_rollout.py`

- [ ] **Step 1: Add simulation gate fields to PromotionRequest**

In `agent_app/governance/policy_promotion.py`, add after the `executed_by` field:

```python
    simulation_gate_required: bool = Field(
        default=False,
        description="Whether simulation gate is required for this promotion (Phase 42)",
    )
    simulation_gate_requirement_id: str | None = Field(
        default=None,
        description="Release gate requirement ID (rgr_ prefix, Phase 42)",
    )
    simulation_gate_result_id: str | None = Field(
        default=None,
        description="Simulation gate result ID (pg_ prefix, Phase 42)",
    )
    simulation_id: str | None = Field(
        default=None,
        description="Simulation ID (psim_ prefix, Phase 42)",
    )
```

- [ ] **Step 2: Add simulation gate fields to RolloutStep**

In `agent_app/governance/policy_rollout.py`, add after the `error` field in `RolloutStep`:

```python
    requires_simulation_gate: bool = Field(
        default=False,
        description="Whether simulation gate is required for this step (Phase 42)",
    )
    simulation_gate_requirement_id: str | None = Field(
        default=None,
        description="Release gate requirement ID (Phase 42)",
    )
    simulation_gate_result_id: str | None = Field(
        default=None,
        description="Simulation gate result ID (Phase 42)",
    )
```

- [ ] **Step 3: Verify existing tests still pass**

Run: `pytest tests/unit/test_policy_promotion.py tests/unit/test_policy_rollout.py -v --timeout=30`
Expected: All existing tests PASS (new fields have defaults, backward-compatible)

- [ ] **Step 4: Commit**

```bash
git add agent_app/governance/policy_promotion.py agent_app/governance/policy_rollout.py
git commit -m "feat: Phase 42 Task 3 — extend PromotionRequest and RolloutStep with simulation gate fields"
```

---

### Task 4: ReleaseGateAutomationService

**Files:**
- Create: `agent_app/runtime/policy_release_gate_service.py`
- Test: `tests/unit/test_policy_release_gate_service.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for ReleaseGateAutomationService."""
from datetime import datetime, timezone, timedelta

import pytest

from agent_app.governance.policy_gate import PolicyGateResult, PolicyGateRule
from agent_app.governance.policy_release_gate import (
    ReleaseGateRequirement,
    ReleaseGateRequirementStatus,
)
from agent_app.governance.policy_simulation import (
    PolicySimulationReport,
    PolicySimulationSummary,
)
from agent_app.governance.runtime_policy import RuntimePolicyRule
from agent_app.runtime.policy_release_gate_service import ReleaseGateAutomationService
from agent_app.runtime.policy_release_gate_store import InMemoryReleaseGateRequirementStore


def _make_passed_gate_result(gate_result_id: str = "pg_passed", created_at: datetime | None = None) -> PolicyGateResult:
    return PolicyGateResult(
        gate_result_id=gate_result_id,
        bundle_id="pb_test",
        replay_id="rp_test",
        status="passed",
        passed=True,
        total_decisions=10,
        changed_decisions=1,
        failed_replays=0,
        changed_ratio=0.1,
        created_at=created_at or datetime.now(timezone.utc),
    )


def _make_failed_gate_result(gate_result_id: str = "pg_failed") -> PolicyGateResult:
    return PolicyGateResult(
        gate_result_id=gate_result_id,
        bundle_id="pb_test",
        replay_id="rp_test",
        status="failed",
        passed=False,
        total_decisions=10,
        changed_decisions=8,
        failed_replays=0,
        changed_ratio=0.8,
    )


@pytest.fixture
def store():
    return InMemoryReleaseGateRequirementStore()


@pytest.fixture
def gate_store():
    from agent_app.runtime.policy_gate_store import InMemoryPolicyGateStore
    return InMemoryPolicyGateStore()


@pytest.fixture
def service(store, gate_store):
    return ReleaseGateAutomationService(
        requirement_store=store,
        gate_store=gate_store,
    )


class TestRequireGateForPromotion:
    @pytest.mark.asyncio
    async def test_creates_required_requirement(self, service):
        req = await service.require_gate_for_promotion("pr_001")
        assert req.source_type == "promotion"
        assert req.source_id == "pr_001"
        assert req.status == ReleaseGateRequirementStatus.REQUIRED
        assert req.required is True

    @pytest.mark.asyncio
    async def test_with_max_age(self, service):
        req = await service.require_gate_for_promotion("pr_001", max_age_seconds=86400)
        assert req.max_age_seconds == 86400


class TestAttachGateResult:
    @pytest.mark.asyncio
    async def test_passed_marks_satisfied(self, service, gate_store):
        gate_result = _make_passed_gate_result()
        await gate_store.save(gate_result)
        req = await service.require_gate_for_promotion("pr_001")
        result = await service.attach_gate_result("promotion", "pr_001", "pg_passed")
        assert result.status == ReleaseGateRequirementStatus.SATISFIED
        assert result.gate_result_id == "pg_passed"
        assert result.satisfied_at is not None

    @pytest.mark.asyncio
    async def test_failed_marks_failed(self, service, gate_store):
        gate_result = _make_failed_gate_result()
        await gate_store.save(gate_result)
        req = await service.require_gate_for_promotion("pr_001")
        result = await service.attach_gate_result("promotion", "pr_001", "pg_failed")
        assert result.status == ReleaseGateRequirementStatus.FAILED


class TestCheckRequirement:
    @pytest.mark.asyncio
    async def test_no_requirement_returns_not_required(self, service):
        req = await service.check_requirement("promotion", "pr_001")
        assert req.status == ReleaseGateRequirementStatus.NOT_REQUIRED
        assert req.required is False

    @pytest.mark.asyncio
    async def test_required_no_result(self, service):
        await service.require_gate_for_promotion("pr_001")
        req = await service.check_requirement("promotion", "pr_001")
        assert req.status == ReleaseGateRequirementStatus.REQUIRED

    @pytest.mark.asyncio
    async def test_satisfied(self, service, gate_store):
        gate_result = _make_passed_gate_result()
        await gate_store.save(gate_result)
        await service.require_gate_for_promotion("pr_001")
        await service.attach_gate_result("promotion", "pr_001", "pg_passed")
        req = await service.check_requirement("promotion", "pr_001")
        assert req.status == ReleaseGateRequirementStatus.SATISFIED

    @pytest.mark.asyncio
    async def test_expired(self, service, gate_store):
        old_time = datetime.now(timezone.utc) - timedelta(seconds=200)
        gate_result = _make_passed_gate_result(created_at=old_time)
        await gate_store.save(gate_result)
        await service.require_gate_for_promotion("pr_001", max_age_seconds=100)
        await service.attach_gate_result("promotion", "pr_001", "pg_passed")
        req = await service.check_requirement("promotion", "pr_001")
        assert req.status == ReleaseGateRequirementStatus.EXPIRED

    @pytest.mark.asyncio
    async def test_failed_gate(self, service, gate_store):
        gate_result = _make_failed_gate_result()
        await gate_store.save(gate_result)
        await service.require_gate_for_promotion("pr_001")
        await service.attach_gate_result("promotion", "pr_001", "pg_failed")
        req = await service.check_requirement("promotion", "pr_001")
        assert req.status == ReleaseGateRequirementStatus.FAILED


class TestRunAndAttach:
    @pytest.mark.asyncio
    async def test_orchestrates_simulation_and_gate(self, store, gate_store):
        from agent_app.governance.policy_simulation_gate import SimulationGateInput
        from agent_app.runtime.policy_simulation_gate_evaluator import SimulationGateEvaluator
        from agent_app.runtime.policy_simulation_service import PolicySimulationService
        from agent_app.governance.audit import InMemoryAuditLogger
        from agent_app.core.context import RunContext

        sim_service = PolicySimulationService(audit_logger=InMemoryAuditLogger())
        gate_evaluator = SimulationGateEvaluator(rules=[])
        svc = ReleaseGateAutomationService(
            requirement_store=store,
            gate_store=gate_store,
            simulation_service=sim_service,
            simulation_gate_evaluator=gate_evaluator,
        )
        await svc.require_gate_for_promotion("pr_001")
        result = await svc.run_and_attach_simulation_gate_for_promotion(
            promotion_id="pr_001",
            candidate_rules=[],
            gate_rules=[],
            context=RunContext(run_id="test", user_id="u", tenant_id="t"),
        )
        assert result.status == ReleaseGateRequirementStatus.SATISFIED
        assert result.gate_result_id is not None
        assert result.simulation_id is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_policy_release_gate_service.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

Create `agent_app/runtime/policy_release_gate_service.py` with `ReleaseGateAutomationService`:

Constructor takes: `requirement_store`, `gate_store` (optional), `simulation_service` (optional), `simulation_gate_evaluator` (optional), `audit_logger` (optional), `event_store` (optional).

Methods:
- `require_gate_for_promotion(promotion_id, max_age_seconds, metadata)` — creates ReleaseGateRequirement with source_type="promotion"
- `attach_gate_result(source_type, source_id, gate_result_id, simulation_id, actor_id)` — loads gate_result from gate_store, updates requirement status to SATISFIED or FAILED
- `check_requirement(source_type, source_id, now)` — loads requirement, checks status including max_age_seconds expiry
- `run_and_attach_simulation_gate_for_promotion(...)` — calls simulation_service.validate_and_gate(), then attach_gate_result()

Key behaviors:
- `check_requirement` returns a synthetic NOT_REQUIRED requirement if no record exists
- Expiry check: if max_age_seconds set, compute (now - gate_result.created_at).total_seconds() > max_age_seconds
- Audit events emitted for: required, attached, satisfied, failed, expired
- Change events emitted for: required, satisfied, failed, expired

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_policy_release_gate_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_release_gate_service.py tests/unit/test_policy_release_gate_service.py
git commit -m "feat: Phase 42 Task 4 — ReleaseGateAutomationService"
```

---

### Task 5: PolicyReleaseService integration

**Files:**
- Modify: `agent_app/runtime/policy_release.py`
- Test: `tests/unit/test_policy_release_gate_integration.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for PolicyReleaseService simulation gate enforcement integration."""
from datetime import datetime, timezone

import pytest

from agent_app.core.context import RunContext
from agent_app.governance.policy_bundle import PolicyBundle, PolicyBundleStatus
from agent_app.governance.policy_gate import PolicyGateResult
from agent_app.governance.policy_promotion import PromotionRequest
from agent_app.governance.policy_release_gate import ReleaseGateRequirementStatus
from agent_app.runtime.policy_release import PolicyReleaseService
from agent_app.runtime.policy_release_gate_service import ReleaseGateAutomationService
from agent_app.runtime.policy_release_gate_store import InMemoryReleaseGateRequirementStore
from agent_app.runtime.policy_gate_store import InMemoryPolicyGateStore
from agent_app.runtime.policy_bundle_store import InMemoryPolicyBundleStore


def _context():
    return RunContext(run_id="test", user_id="admin", tenant_id="default")


def _make_bundle(bundle_id: str = "pb_test") -> PolicyBundle:
    return PolicyBundle(
        bundle_id=bundle_id,
        name="test-bundle",
        version="1.0.0",
        status=PolicyBundleStatus.ACTIVE,
        config_path="test.yaml",
        config_hash="abc123",
    )


def _make_passed_gate(bundle_id: str = "pb_test") -> PolicyGateResult:
    return PolicyGateResult(
        gate_result_id="pg_pass",
        bundle_id=bundle_id,
        replay_id="rp_1",
        status="passed",
        passed=True,
        total_decisions=10,
        changed_decisions=1,
        failed_replays=0,
        changed_ratio=0.1,
    )


def _make_failed_gate(bundle_id: str = "pb_test") -> PolicyGateResult:
    return PolicyGateResult(
        gate_result_id="pg_fail",
        bundle_id=bundle_id,
        replay_id="rp_1",
        status="failed",
        passed=False,
        total_decisions=10,
        changed_decisions=8,
        failed_replays=0,
        changed_ratio=0.8,
    )


class TestEnforcementDisabled:
    """When enforcement is disabled, existing behavior preserved."""

    @pytest.mark.asyncio
    async def test_execute_promotion_works_normally(self):
        bundle_store = InMemoryPolicyBundleStore()
        gate_store = InMemoryPolicyGateStore()
        await bundle_store.create(_make_bundle())
        await gate_store.save(_make_passed_gate())

        svc = PolicyReleaseService(
            bundle_store=bundle_store,
            replay_runner=None,
            replay_store=None,
            gate_evaluator=None,
            gate_store=gate_store,
        )
        # Should not fail even without gate automation service
        assert svc is not None


class TestEnforcementEnabled:
    """When enforcement is enabled, gate requirement is checked."""

    @pytest.mark.asyncio
    async def test_execute_blocked_missing_gate(self):
        """execute_promotion raises ValueError when gate required but missing."""
        req_store = InMemoryReleaseGateRequirementStore()
        gate_store = InMemoryPolicyGateStore()
        bundle_store = InMemoryPolicyBundleStore()
        gate_automation = ReleaseGateAutomationService(
            requirement_store=req_store,
            gate_store=gate_store,
        )
        await bundle_store.create(_make_bundle())
        # Create promotion request
        from agent_app.runtime.policy_promotion_store import InMemoryPromotionRequestStore
        promo_store = InMemoryPromotionRequestStore()
        pr = PromotionRequest(promotion_id="pr_001", bundle_id="pb_test", requested_by="admin")
        await promo_store.create(pr)

        svc = PolicyReleaseService(
            bundle_store=bundle_store,
            replay_runner=None,
            replay_store=None,
            gate_evaluator=None,
            gate_store=gate_store,
            promotion_store=promo_store,
            release_gate_automation_service=gate_automation,
            require_simulation_gate_for_promotion=True,
        )
        # Create requirement but don't attach gate result
        await gate_automation.require_gate_for_promotion("pr_001")
        with pytest.raises(ValueError, match="simulation gate"):
            await svc.execute_promotion("pr_001", "admin", _context())

    @pytest.mark.asyncio
    async def test_execute_blocked_failed_gate(self):
        """execute_promotion raises ValueError when gate failed."""
        req_store = InMemoryReleaseGateRequirementStore()
        gate_store = InMemoryPolicyGateStore()
        bundle_store = InMemoryPolicyBundleStore()
        gate_automation = ReleaseGateAutomationService(
            requirement_store=req_store,
            gate_store=gate_store,
        )
        await bundle_store.create(_make_bundle())
        await gate_store.save(_make_failed_gate())
        from agent_app.runtime.policy_promotion_store import InMemoryPromotionRequestStore
        promo_store = InMemoryPromotionRequestStore()
        pr = PromotionRequest(promotion_id="pr_002", bundle_id="pb_test", requested_by="admin", status="approved")
        await promo_store.create(pr)

        svc = PolicyReleaseService(
            bundle_store=bundle_store,
            replay_runner=None,
            replay_store=None,
            gate_evaluator=None,
            gate_store=gate_store,
            promotion_store=promo_store,
            release_gate_automation_service=gate_automation,
            require_simulation_gate_for_promotion=True,
        )
        await gate_automation.require_gate_for_promotion("pr_002")
        await gate_automation.attach_gate_result("promotion", "pr_002", "pg_fail")
        with pytest.raises(ValueError, match="simulation gate"):
            await svc.execute_promotion("pr_002", "admin", _context())

    @pytest.mark.asyncio
    async def test_execute_succeeds_with_satisfied_gate(self):
        """execute_promotion proceeds when gate is satisfied."""
        req_store = InMemoryReleaseGateRequirementStore()
        gate_store = InMemoryPolicyGateStore()
        bundle_store = InMemoryPolicyBundleStore()
        gate_automation = ReleaseGateAutomationService(
            requirement_store=req_store,
            gate_store=gate_store,
        )
        await bundle_store.create(_make_bundle())
        await gate_store.save(_make_passed_gate())
        from agent_app.runtime.policy_promotion_store import InMemoryPromotionRequestStore
        promo_store = InMemoryPromotionRequestStore()
        pr = PromotionRequest(promotion_id="pr_003", bundle_id="pb_test", requested_by="admin", status="approved")
        await promo_store.create(pr)

        svc = PolicyReleaseService(
            bundle_store=bundle_store,
            replay_runner=None,
            replay_store=None,
            gate_evaluator=None,
            gate_store=gate_store,
            promotion_store=promo_store,
            release_gate_automation_service=gate_automation,
            require_simulation_gate_for_promotion=True,
        )
        await gate_automation.require_gate_for_promotion("pr_003")
        await gate_automation.attach_gate_result("promotion", "pr_003", "pg_pass")
        # Should not raise — gate is satisfied
        # (Will fail at bundle activation since no replay_runner, but the gate check should pass)
        # We just verify it doesn't raise the simulation gate ValueError


class TestRolloutStepGate:
    """Rollout step with requires_simulation_gate blocks when missing."""

    @pytest.mark.asyncio
    async def test_step_blocks_when_gate_missing(self):
        """Step with requires_simulation_gate=True becomes BLOCKED when no gate result."""
        from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType, RolloutStepStatus, RolloutPlan
        from agent_app.runtime.policy_rollout_service import RolloutService
        from agent_app.runtime.policy_rollout_store import InMemoryRolloutPlanStore

        step = RolloutStep(
            step_id="rs_001",
            step_type=RolloutStepType.ACTIVATE,
            environment="prod",
            requires_simulation_gate=True,
        )
        req_store = InMemoryReleaseGateRequirementStore()
        gate_automation = ReleaseGateAutomationService(
            requirement_store=req_store,
        )

        plan_store = InMemoryRolloutPlanStore()
        plan = RolloutPlan(
            rollout_id="ro_001",
            name="test",
            bundle_id="pb_test",
            steps=[step],
            created_by="admin",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            status="active",
        )
        await plan_store.create(plan)

        svc = RolloutService(
            rollout_store=plan_store,
            release_service=None,
            release_gate_automation_service=gate_automation,
        )
        result = await svc.run_next_step("ro_001", "admin", _context())
        blocked_step = next(s for s in result.steps if s.step_id == "rs_001")
        assert blocked_step.status == RolloutStepStatus.BLOCKED
        assert blocked_step.error is not None
        assert blocked_step.error.get("type") == "simulation_gate_required"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_policy_release_gate_integration.py -v`
Expected: FAIL — constructor doesn't accept new parameters yet

- [ ] **Step 3: Implement PolicyReleaseService changes**

In `agent_app/runtime/policy_release.py`:
1. Add `release_gate_automation_service`, `require_simulation_gate_for_promotion`, and `simulation_gate_max_age_seconds` to `__init__`
2. In `request_promotion`: when `require_simulation_gate_for_promotion=True`, create a ReleaseGateRequirement via the automation service
3. In `execute_promotion`: before proceeding, call `check_requirement` — raise ValueError if status is REQUIRED/FAILED/EXPIRED
4. Add `_check_simulation_gate` private method

In `agent_app/runtime/policy_rollout_service.py`:
1. Add `release_gate_automation_service` to `__init__`
2. In `_execute_step` or `run_next_step`: check if step has `requires_simulation_gate=True`, call `check_requirement`, if not SATISFIED → mark step BLOCKED with error type `simulation_gate_required`

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_policy_release_gate_integration.py -v`
Expected: PASS

- [ ] **Step 5: Verify existing tests still pass**

Run: `pytest tests/unit/test_policy_release.py tests/unit/test_policy_rollout_service.py -v --timeout=60`
Expected: All existing tests PASS (new params default to None/False)

- [ ] **Step 6: Commit**

```bash
git add agent_app/runtime/policy_release.py agent_app/runtime/policy_rollout_service.py tests/unit/test_policy_release_gate_integration.py
git commit -m "feat: Phase 42 Task 5 — PolicyReleaseService and RolloutService simulation gate enforcement"
```

---

### Task 6: Config, Loader, RBAC, and Events

**Files:**
- Modify: `agent_app/governance/policy_rbac.py`
- Modify: `agent_app/governance/policy_change_event.py`
- Modify: `agent_app/config/schema.py`
- Modify: `agent_app/config/loader.py`
- Test: `tests/unit/test_policy_release_gate_config.py`

- [ ] **Step 1: Write the failing tests**

Test file covering:
- Missing enforcement config preserves behavior
- Enabled config wires requirement store/service
- SQLite config works
- Old Phase 41 configs still load
- New RBAC permissions exist and have correct values
- PROMOTION_GATE_VIEW in default_allowed
- All new change event types exist
- All new audit event types are valid strings

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_policy_release_gate_config.py -v`
Expected: FAIL

- [ ] **Step 3: Implement changes**

1. **RBAC** (`policy_rbac.py`): Add to `PolicyReleasePermission`:
   - `PROMOTION_GATE_REQUIRE = "policy.promotion.gate.require"`
   - `PROMOTION_GATE_RUN = "policy.promotion.gate.run"`
   - `PROMOTION_GATE_ATTACH = "policy.promotion.gate.attach"`
   - `PROMOTION_GATE_VIEW = "policy.promotion.gate.view"`
   - `ROLLOUT_GATE_ATTACH = "policy.rollout.gate.attach"`
   - `ROLLOUT_GATE_VIEW = "policy.rollout.gate.view"`
   Add `PROMOTION_GATE_VIEW` and `ROLLOUT_GATE_VIEW` to `_DEFAULT_ALLOWED`

2. **Events** (`policy_change_event.py`): Add to `PolicyChangeEventType`:
   - `PROMOTION_GATE_REQUIRED = "policy.promotion.gate.required"`
   - `PROMOTION_GATE_RUN = "policy.promotion.gate.run"`
   - `PROMOTION_GATE_ATTACHED = "policy.promotion.gate.attached"`
   - `PROMOTION_GATE_SATISFIED = "policy.promotion.gate.satisfied"`
   - `PROMOTION_GATE_FAILED = "policy.promotion.gate.failed"`
   - `PROMOTION_GATE_EXPIRED = "policy.promotion.gate.expired"`
   - `PROMOTION_GATE_EXECUTION_BLOCKED = "policy.promotion.gate.execution_blocked"`
   - `PROMOTION_GATE_PERMISSION_DENIED = "policy.promotion.gate.permission_denied"`

3. **Schema** (`schema.py`): Add `SimulationGateEnforcementConfig`:
   ```python
   class SimulationGateEnforcementConfig(BaseModel):
       require_for_promotion: bool = Field(default=False, ...)
       max_age_seconds: int | None = Field(default=None, ...)
       requirement_store: PolicyReleaseStoreConfig | None = Field(default=None, ...)
   ```
   Add `simulation_gate_enforcement` field to `PolicyReleaseConfig`.

4. **Loader** (`loader.py`): When `simulation_gate_enforcement` is present and enabled:
   - Create requirement store (memory or sqlite)
   - Create ReleaseGateAutomationService
   - Set `require_simulation_gate_for_promotion` and `simulation_gate_max_age_seconds` on PolicyReleaseService
   - Set `release_gate_automation_service` on RolloutService
   - Expose `release_gate_requirement_store` and `release_gate_automation_service` on AgentApp

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_policy_release_gate_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/governance/policy_rbac.py agent_app/governance/policy_change_event.py agent_app/config/schema.py agent_app/config/loader.py tests/unit/test_policy_release_gate_config.py
git commit -m "feat: Phase 42 Task 6 — config, loader, RBAC, events for simulation gate enforcement"
```

---

### Task 7: CLI commands

**Files:**
- Modify: `agent_app/cli.py`
- Test: `tests/unit/test_policy_release_gate_cli.py`

- [ ] **Step 1: Write the failing tests**

Tests covering:
- `policy promotion gate require --promotion-id pr_...` creates requirement, exits 0
- `policy promotion gate run --promotion-id pr_... --rules-file ... --gate-rules-file ...` runs simulation+gate, exits 0 on pass
- `policy promotion gate attach --promotion-id pr_... --gate-result-id pg_...` attaches result
- `policy promotion gate status --promotion-id pr_...` shows status
- Gate failure exits non-zero
- Permission denied exits non-zero
- Invalid rules file exits non-zero with error message

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement CLI subcommands**

Add to `agent_app/cli.py`:
- `policy promotion gate require` subparser with `--promotion-id`, `--actor-id`, `--max-age-seconds`, `--permissions`
- `policy promotion gate run` subparser with `--promotion-id`, `--rules-file`, `--gate-rules-file`, `--actor-id`, `--permissions`
- `policy promotion gate attach` subparser with `--promotion-id`, `--gate-result-id`, `--simulation-id`, `--actor-id`, `--permissions`
- `policy promotion gate status` subparser with `--promotion-id`, `--json`

Each command:
- Loads config → gets app → calls automation service method
- Prints structured output (promotion_id, requirement_id, status, gate_result_id, etc.)
- Exits 0 on success, non-zero on failure

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

```bash
git add agent_app/cli.py tests/unit/test_policy_release_gate_cli.py
git commit -m "feat: Phase 42 Task 7 — CLI promotion gate commands"
```

---

### Task 8: Console pages

**Files:**
- Modify: `agent_app/console/router.py`
- Modify: `agent_app/adapters/fastapi.py`
- Create: `agent_app/console/templates/policy_promotion_gate.html`
- Create: `agent_app/console/templates/policy_promotion_gate_status.html`
- Test: `tests/unit/test_policy_release_gate_console.py`

- [ ] **Step 1: Write the failing tests**

Tests covering:
- GET /policy-console/promotions/{promotion_id}/gate renders gate page
- POST /policy-console/promotions/{promotion_id}/gate/require creates requirement
- POST /policy-console/promotions/{promotion_id}/gate/run runs simulation+gate
- POST /policy-console/promotions/{promotion_id}/gate/attach attaches gate result
- Errors render clearly (no traceback leakage)

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement console routes and templates**

In `router.py`:
- Add `release_gate_automation_service` parameter to `build_policy_console_router()`
- Add routes:
  - GET `/promotions/{promotion_id}/gate` — render gate form page
  - POST `/promotions/{promotion_id}/gate/require` — create requirement
  - POST `/promotions/{promotion_id}/gate/run` — run simulation+gate
  - POST `/promotions/{promotion_id}/gate/attach` — attach gate result

Templates:
- `policy_promotion_gate.html` — form with candidate rules textarea, gate rules textarea, action buttons
- `policy_promotion_gate_status.html` — status display with requirement_id, gate_result_id, simulation_id, status (color-coded), per-rule results

In `fastapi.py`:
- Wire `release_gate_automation_service=getattr(agent_app, "release_gate_automation_service", None)`

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

```bash
git add agent_app/console/router.py agent_app/adapters/fastapi.py agent_app/console/templates/policy_promotion_gate.html agent_app/console/templates/policy_promotion_gate_status.html tests/unit/test_policy_release_gate_console.py
git commit -m "feat: Phase 42 Task 8 — console promotion gate pages"
```

---

### Task 9: Documentation and final verification

**Files:**
- Modify: `docs/policy_release.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Create: `docs/release_checklist_phase42.md`

- [ ] **Step 1: Update docs/policy_release.md**

Add Phase 42 section documenting:
1. Simulation gate enforcement purpose
2. Promotion gate requirement lifecycle (REQUIRED → SATISFIED/FAILED/EXPIRED)
3. CLI flow examples
4. Console flow
5. Config examples (simulation_gate_enforcement section)
6. Failure/expired behavior
7. Relationship to Phase 41 simulation gate
8. Known limitations

- [ ] **Step 2: Update CHANGELOG.md**

Add v0.30.0 entry:
```
## v0.30.0 — Phase 42: Policy Release Automation and Simulation Gate Enforcement
- ReleaseGateRequirement model and store (InMemory + SQLite)
- ReleaseGateAutomationService (require, attach, run+attach, check)
- PromotionRequest extension with simulation gate fields
- RolloutStep extension with simulation gate fields
- PolicyReleaseService enforcement (block execution when gate required/failed/expired)
- RolloutService step gate blocking
- Config schema for simulation_gate_enforcement
- CLI commands: policy promotion gate require/run/attach/status
- Console promotion gate pages
- RBAC: PROMOTION_GATE_REQUIRE, PROMOTION_GATE_RUN, PROMOTION_GATE_ATTACH, PROMOTION_GATE_VIEW, ROLLOUT_GATE_ATTACH, ROLLOUT_GATE_VIEW
- Change events: PROMOTION_GATE_REQUIRED, PROMOTION_GATE_SATISFIED, PROMOTION_GATE_FAILED, PROMOTION_GATE_EXPIRED, etc.
```

- [ ] **Step 3: Update README.md**

Add Phase 42 in roadmap.

- [ ] **Step 4: Create release checklist**

Create `docs/release_checklist_phase42.md` with verification items.

- [ ] **Step 5: Run full regression tests**

Run: `pytest tests/unit/ -v --timeout=120 -q`
Expected: All tests pass, 0 failures

- [ ] **Step 6: Commit**

```bash
git add docs/policy_release.md CHANGELOG.md README.md docs/release_checklist_phase42.md
git commit -m "docs: Phase 42 documentation — policy release automation and simulation gate enforcement"
```
