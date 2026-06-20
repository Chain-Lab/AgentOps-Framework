# Phase 46 Policy Rollout Federation and Conflict Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a framework-level rollout federation layer that coordinates child rollout plans across tenants, environments, regions, rings, and target groups while detecting conflicts before unsafe execution.

**Architecture:** Add governance-only federation models, runtime stores, a pure conflict detector, and a coordinator service that creates child `RolloutPlan` instances through the existing `RolloutService`. Federation integration is optional and config-gated; existing Phase 45 rollout history remains backward compatible. CLI and console pages expose the target lifecycle, federated plan lifecycle, execution status, and conflict reports without implementing real distributed locks or external deployment orchestration.

**Tech Stack:** Python 3.11+, Pydantic v2, asyncio-style runtime services, SQLite/in-memory stores, argparse CLI, FastAPI/Jinja2 console templates, pytest.

---

## Scope and design decisions

1. **Framework-level only:** No distributed locks, Kubernetes, service mesh, cloud control planes, external CI/CD orchestration, cross-process schedulers, or multi-region network coordination.
2. **Conflict severity choices:**
   - Duplicate, missing, disabled target, active same-target federation, and existing same environment/ring active rollout conflicts are `ERROR`.
   - Active different-bundle conflict is `WARNING`; it is surfaced and audited, but does not block unless it also overlaps a target or environment/ring as an error.
3. **Missing target conflict type:** Add `RolloutConflictType.MISSING_TARGET = "missing_target"` because the spec requires missing-target detection and the listed enum values do not otherwise represent it.
4. **History integration:** Federation lifecycle uses audit and policy change events. Child rollout plans continue to produce Phase 45 rollout history events once created. Do not add federation-specific values to `RolloutHistoryEventType` in this phase because those events are not rollout-scoped until a child rollout exists.
5. **Conflict override:** `create_federated_plan()` may proceed with `ERROR` conflicts only when `context.metadata.get("allow_federation_conflict_override") is True`. `start_federated_plan()` always fails on current `ERROR` conflicts because starting unsafe execution should require conflicts to be resolved.
6. **Parallel strategy:** `PARALLEL` is logical. `run_next_target()` executes one deterministic pending target; `run_all_available()` loops deterministically until terminal state or no progress.
7. **Disabled targets:** Conflict detection reports disabled selected targets as `ERROR`. If a target becomes disabled after plan creation, execution defensively marks that target execution `SKIPPED` with metadata `{"reason": "target_disabled"}` so automatic execution ignores it.
8. **Store `get()` behavior:** Runtime store `get()` methods return `None` for missing records, matching existing rollout store patterns and enabling deterministic missing-target conflicts.
9. **Wave validation:** Direct models allow wave-only construction when `target_ids=[]`; if `target_ids` is present, every wave target must be in `target_ids`. The service normalizes wave plans so stored federated plans contain the union of all wave targets in `target_ids`.
10. **RBAC default allowed:** View permissions (`FEDERATION_TARGET_VIEW`, `FEDERATION_PLAN_VIEW`, `FEDERATION_CONFLICT_VIEW`) are default-allowed like Phase 45 view permissions. Mutating permissions are not default-allowed.

## File structure

### New files

- `agent_app/governance/policy_rollout_federation.py` — federation target/plan/execution/wave/conflict models and validators.
- `agent_app/runtime/policy_rollout_federation_store.py` — target and federated plan store protocols, in-memory stores, SQLite stores, and factories.
- `agent_app/runtime/policy_rollout_conflict_detector.py` — deterministic, non-mutating conflict checks.
- `agent_app/runtime/policy_rollout_federation_service.py` — target creation, federated plan creation/start/execution/cancel, audit/change/notification hooks.
- `agent_app/console/templates/policy_federation_targets.html` — target list/create page.
- `agent_app/console/templates/policy_federation_target_detail.html` — target detail page.
- `agent_app/console/templates/policy_federation_plans.html` — federated plan list page.
- `agent_app/console/templates/policy_federation_plan_detail.html` — federated plan detail/actions page.
- `agent_app/console/templates/policy_federation_plan_create.html` — plan creation form.
- `agent_app/console/templates/policy_federation_conflicts.html` — conflict report page.
- `tests/unit/test_policy_rollout_federation_model.py` — model and validation tests.
- `tests/unit/test_policy_rollout_federation_store.py` — store tests.
- `tests/unit/test_policy_rollout_conflict_detector.py` — conflict detector tests.
- `tests/unit/test_policy_rollout_federation_service.py` — service lifecycle/execution/integration tests.
- `tests/unit/test_policy_rollout_federation_config.py` — config/RBAC/loader/AgentApp/change event tests.
- `tests/unit/test_policy_rollout_federation_cli.py` — CLI command tests.
- `tests/unit/test_policy_rollout_federation_console.py` — console page/action tests.
- `docs/release_checklist_phase46.md` — Phase 46 release checklist.

### Modified files

- `agent_app/governance/policy_rbac.py` — add federation permissions and default allowed view permissions.
- `agent_app/governance/policy_change_event.py` — add federation change event types.
- `agent_app/config/schema.py` — add rollout federation config models and field under policy release config.
- `agent_app/config/loader.py` — wire stores, conflict detector, and federation service when enabled.
- `agent_app/core/app.py` — expose `federated_rollout_target_store`, `federated_rollout_plan_store`, and `rollout_federation_service` properties.
- `agent_app/cli.py` — add `policy federation target ...` and `policy federation plan ...` commands.
- `agent_app/console/router.py` — add federation routes and service parameter.
- `agent_app/adapters/fastapi.py` — pass `rollout_federation_service` into the policy console router.
- `docs/policy_release.md` — document federation purpose, models, strategies, conflicts, CLI, console, limitations.
- `CHANGELOG.md` — add Phase 46 entry.
- `README.md` — mark Phase 46 complete in roadmap after implementation.

---

## Task 1: Federation governance models

**Files:**
- Create: `agent_app/governance/policy_rollout_federation.py`
- Test: `tests/unit/test_policy_rollout_federation_model.py`

- [ ] **Step 1: Write failing model tests**

Create `tests/unit/test_policy_rollout_federation_model.py` with these tests:

```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType
from agent_app.governance.policy_rollout_federation import (
    FederatedRolloutPlan,
    FederatedRolloutPlanStatus,
    FederatedRolloutTarget,
    FederatedRolloutTargetExecution,
    FederatedRolloutTargetExecutionStatus,
    FederatedRolloutWave,
    FederatedTargetStatus,
    FederationExecutionStrategy,
    RolloutConflict,
    RolloutConflictSeverity,
    RolloutConflictType,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _step(environment: str = "prod", ring_name: str | None = "canary") -> RolloutStep:
    return RolloutStep(
        step_id="step_activate",
        step_type=RolloutStepType.ACTIVATE,
        environment=environment,
        ring_name=ring_name,
    )


class TestFederatedRolloutTarget:
    def test_valid_target_preserves_optional_fields(self) -> None:
        target = FederatedRolloutTarget(
            target_id="frt_prod_us_canary",
            name="prod-us-canary",
            tenant_id="tenant_a",
            environment="prod",
            ring_name="canary",
            region="us-east",
            labels={"tier": "gold"},
            metadata={"owner": "release"},
            created_at=_now(),
        )

        assert target.target_id == "frt_prod_us_canary"
        assert target.tenant_id == "tenant_a"
        assert target.environment == "prod"
        assert target.ring_name == "canary"
        assert target.region == "us-east"
        assert target.labels == {"tier": "gold"}
        assert target.status == FederatedTargetStatus.ENABLED

    def test_target_id_requires_prefix(self) -> None:
        with pytest.raises(ValidationError, match="frt_"):
            FederatedRolloutTarget(
                target_id="bad_target",
                name="bad",
                environment="prod",
                created_at=_now(),
            )

    def test_target_created_at_requires_timezone(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            FederatedRolloutTarget(
                target_id="frt_no_tz",
                name="bad",
                environment="prod",
                created_at=datetime(2026, 6, 18, 12, 0, 0),
            )


class TestFederatedRolloutPlan:
    def test_valid_sequential_plan(self) -> None:
        plan = FederatedRolloutPlan(
            federation_id="frp_global_rollout",
            name="global rollout",
            bundle_id="pb_123",
            target_ids=["frt_a", "frt_b"],
            rollout_template_steps=[_step()],
            created_by="release_manager",
            created_at=_now(),
            updated_at=_now(),
        )

        assert plan.federation_id == "frp_global_rollout"
        assert plan.strategy == FederationExecutionStrategy.SEQUENTIAL
        assert plan.status == FederatedRolloutPlanStatus.DRAFT
        assert plan.target_ids == ["frt_a", "frt_b"]
        assert len(plan.rollout_template_steps) == 1

    def test_federation_id_requires_prefix(self) -> None:
        with pytest.raises(ValidationError, match="frp_"):
            FederatedRolloutPlan(
                federation_id="bad",
                name="bad",
                bundle_id="pb_123",
                target_ids=["frt_a"],
                rollout_template_steps=[_step()],
                created_by="user",
                created_at=_now(),
                updated_at=_now(),
            )

    def test_execution_id_requires_prefix(self) -> None:
        with pytest.raises(ValidationError, match="fre_"):
            FederatedRolloutTargetExecution(
                execution_id="bad",
                target_id="frt_a",
            )

    def test_wave_id_requires_prefix(self) -> None:
        with pytest.raises(ValidationError, match="frw_"):
            FederatedRolloutWave(wave_id="bad", target_ids=["frt_a"])

    def test_wave_strategy_requires_waves(self) -> None:
        with pytest.raises(ValidationError, match="WAVE strategy requires at least one wave"):
            FederatedRolloutPlan(
                federation_id="frp_wave_missing",
                name="wave missing",
                bundle_id="pb_123",
                strategy=FederationExecutionStrategy.WAVE,
                target_ids=["frt_a"],
                rollout_template_steps=[_step()],
                created_by="user",
                created_at=_now(),
                updated_at=_now(),
            )

    def test_wave_targets_must_exist_when_target_ids_present(self) -> None:
        with pytest.raises(ValidationError, match="unknown target_id"):
            FederatedRolloutPlan(
                federation_id="frp_bad_wave",
                name="bad wave",
                bundle_id="pb_123",
                strategy=FederationExecutionStrategy.WAVE,
                target_ids=["frt_a"],
                waves=[FederatedRolloutWave(wave_id="frw_1", target_ids=["frt_missing"])],
                rollout_template_steps=[_step()],
                created_by="user",
                created_at=_now(),
                updated_at=_now(),
            )

    def test_duplicate_target_validation(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate target_id"):
            FederatedRolloutPlan(
                federation_id="frp_dup",
                name="dup",
                bundle_id="pb_123",
                target_ids=["frt_a", "frt_a"],
                rollout_template_steps=[_step()],
                created_by="user",
                created_at=_now(),
                updated_at=_now(),
            )

    def test_target_ids_can_be_empty_when_waves_provided(self) -> None:
        plan = FederatedRolloutPlan(
            federation_id="frp_wave_only",
            name="wave only",
            bundle_id="pb_123",
            strategy=FederationExecutionStrategy.WAVE,
            target_ids=[],
            waves=[FederatedRolloutWave(wave_id="frw_1", target_ids=["frt_a"])],
            rollout_template_steps=[_step()],
            created_by="user",
            created_at=_now(),
            updated_at=_now(),
        )

        assert plan.waves[0].target_ids == ["frt_a"]

    def test_plan_datetimes_require_timezone(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            FederatedRolloutPlan(
                federation_id="frp_no_tz",
                name="bad",
                bundle_id="pb_123",
                target_ids=["frt_a"],
                rollout_template_steps=[_step()],
                created_by="user",
                created_at=datetime(2026, 6, 18, 12, 0, 0),
                updated_at=_now(),
            )


class TestRolloutConflict:
    def test_valid_conflict(self) -> None:
        conflict = RolloutConflict(
            conflict_id="frc_001",
            conflict_type=RolloutConflictType.TARGET_ALREADY_ACTIVE,
            severity=RolloutConflictSeverity.ERROR,
            target_id="frt_a",
            environment="prod",
            ring_name="canary",
            existing_federation_id="frp_existing",
            message="Target is already active in another federation.",
        )

        assert conflict.conflict_id == "frc_001"
        assert conflict.severity == RolloutConflictSeverity.ERROR
        assert conflict.target_id == "frt_a"

    def test_conflict_id_requires_prefix(self) -> None:
        with pytest.raises(ValidationError, match="frc_"):
            RolloutConflict(
                conflict_id="bad",
                conflict_type=RolloutConflictType.DUPLICATE_TARGET,
                severity=RolloutConflictSeverity.ERROR,
                message="Duplicate target.",
            )

    def test_missing_target_conflict_type_exists(self) -> None:
        assert RolloutConflictType.MISSING_TARGET.value == "missing_target"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_policy_rollout_federation_model.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'agent_app.governance.policy_rollout_federation'`.

- [ ] **Step 3: Implement federation model module**

Create `agent_app/governance/policy_rollout_federation.py` with these public models and validators:

```python
"""Policy rollout federation models — framework-level coordinated rollouts.

Phase 46: Federated rollout targets, plans, executions, waves, and conflicts.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from agent_app.governance.policy_rollout import RolloutStep


class FederatedTargetStatus(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class FederatedRolloutTarget(BaseModel):
    target_id: str
    name: str
    tenant_id: str | None = None
    environment: str
    ring_name: str | None = None
    region: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    status: FederatedTargetStatus = FederatedTargetStatus.ENABLED
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("target_id")
    @classmethod
    def _validate_target_id(cls, value: str) -> str:
        if not value.startswith("frt_"):
            raise ValueError(f"ID must start with 'frt_', got '{value}'")
        return value

    @field_validator("created_at")
    @classmethod
    def _validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class FederatedRolloutPlanStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class FederationExecutionStrategy(StrEnum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    WAVE = "wave"


class FederatedRolloutTargetExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class FederatedRolloutTargetExecution(BaseModel):
    execution_id: str
    target_id: str
    rollout_id: str | None = None
    status: FederatedRolloutTargetExecutionStatus = FederatedRolloutTargetExecutionStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("execution_id")
    @classmethod
    def _validate_execution_id(cls, value: str) -> str:
        if not value.startswith("fre_"):
            raise ValueError(f"ID must start with 'fre_', got '{value}'")
        return value

    @field_validator("started_at", "completed_at")
    @classmethod
    def _validate_optional_datetimes(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.tzinfo.utcoffset(value) is None):
            raise ValueError("datetime fields must be timezone-aware")
        return value


class FederatedRolloutWave(BaseModel):
    wave_id: str
    name: str | None = None
    target_ids: list[str]
    require_all_successful: bool = True
    status: FederatedRolloutTargetExecutionStatus = FederatedRolloutTargetExecutionStatus.PENDING
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("wave_id")
    @classmethod
    def _validate_wave_id(cls, value: str) -> str:
        if not value.startswith("frw_"):
            raise ValueError(f"ID must start with 'frw_', got '{value}'")
        return value

    @model_validator(mode="after")
    def _validate_targets(self) -> "FederatedRolloutWave":
        if not self.target_ids:
            raise ValueError("Federated rollout wave must include at least one target_id")
        seen: set[str] = set()
        for target_id in self.target_ids:
            if target_id in seen:
                raise ValueError(f"Duplicate target_id in wave: {target_id}")
            seen.add(target_id)
        return self


class FederatedRolloutPlan(BaseModel):
    federation_id: str
    name: str
    bundle_id: str
    strategy: FederationExecutionStrategy = FederationExecutionStrategy.SEQUENTIAL
    status: FederatedRolloutPlanStatus = FederatedRolloutPlanStatus.DRAFT
    target_ids: list[str] = Field(default_factory=list)
    waves: list[FederatedRolloutWave] = Field(default_factory=list)
    executions: list[FederatedRolloutTargetExecution] = Field(default_factory=list)
    rollout_template_steps: list[RolloutStep] = Field(default_factory=list)
    created_by: str
    reason: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("federation_id")
    @classmethod
    def _validate_federation_id(cls, value: str) -> str:
        if not value.startswith("frp_"):
            raise ValueError(f"ID must start with 'frp_', got '{value}'")
        return value

    @field_validator("created_at", "updated_at")
    @classmethod
    def _validate_datetimes(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("created_at and updated_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _validate_plan(self) -> "FederatedRolloutPlan":
        if not self.target_ids and not self.waves:
            raise ValueError("Federated rollout plan must include target_ids or waves")
        if self.strategy == FederationExecutionStrategy.WAVE and not self.waves:
            raise ValueError("WAVE strategy requires at least one wave")
        seen_targets: set[str] = set()
        for target_id in self.target_ids:
            if target_id in seen_targets:
                raise ValueError(f"Duplicate target_id: {target_id}")
            seen_targets.add(target_id)
        if self.target_ids:
            for wave in self.waves:
                for target_id in wave.target_ids:
                    if target_id not in seen_targets:
                        raise ValueError(f"Wave '{wave.wave_id}' references unknown target_id '{target_id}'")
        seen_executions: set[str] = set()
        for execution in self.executions:
            if execution.execution_id in seen_executions:
                raise ValueError(f"Duplicate execution_id: {execution.execution_id}")
            seen_executions.add(execution.execution_id)
        return self


class RolloutConflictSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


class RolloutConflictType(StrEnum):
    TARGET_ALREADY_ACTIVE = "target_already_active"
    ENVIRONMENT_RING_CONFLICT = "environment_ring_conflict"
    BUNDLE_CONFLICT = "bundle_conflict"
    DISABLED_TARGET = "disabled_target"
    DUPLICATE_TARGET = "duplicate_target"
    MISSING_TARGET = "missing_target"


class RolloutConflict(BaseModel):
    conflict_id: str
    conflict_type: RolloutConflictType
    severity: RolloutConflictSeverity
    target_id: str | None = None
    environment: str | None = None
    ring_name: str | None = None
    existing_rollout_id: str | None = None
    existing_federation_id: str | None = None
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("conflict_id")
    @classmethod
    def _validate_conflict_id(cls, value: str) -> str:
        if not value.startswith("frc_"):
            raise ValueError(f"ID must start with 'frc_', got '{value}'")
        return value
```

- [ ] **Step 4: Run model tests to verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_policy_rollout_federation_model.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add agent_app/governance/policy_rollout_federation.py tests/unit/test_policy_rollout_federation_model.py
git commit -m "feat: Phase 46 Task 1 — federation target, plan, and conflict models"
```

---

## Task 2: Federation stores

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_store.py`
- Test: `tests/unit/test_policy_rollout_federation_store.py`

- [ ] **Step 1: Write failing store tests**

Create `tests/unit/test_policy_rollout_federation_store.py` with these tests:

```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType
from agent_app.governance.policy_rollout_federation import (
    FederatedRolloutPlan,
    FederatedRolloutPlanStatus,
    FederatedRolloutTarget,
    FederatedTargetStatus,
)
from agent_app.runtime.policy_rollout_federation_store import (
    FederatedRolloutPlanStore,
    FederatedRolloutTargetStore,
    InMemoryFederatedRolloutPlanStore,
    InMemoryFederatedRolloutTargetStore,
    SQLiteFederatedRolloutPlanStore,
    SQLiteFederatedRolloutTargetStore,
    create_federated_rollout_plan_store,
    create_federated_rollout_target_store,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _target(target_id: str = "frt_a", environment: str = "prod", ring_name: str | None = "canary") -> FederatedRolloutTarget:
    return FederatedRolloutTarget(
        target_id=target_id,
        name=target_id,
        tenant_id="tenant_a",
        environment=environment,
        ring_name=ring_name,
        region="us-east",
        labels={"tier": "gold"},
        created_at=_now(),
    )


def _step() -> RolloutStep:
    return RolloutStep(
        step_id="step_activate",
        step_type=RolloutStepType.ACTIVATE,
        environment="prod",
        ring_name="canary",
    )


def _plan(federation_id: str = "frp_a", bundle_id: str = "pb_123") -> FederatedRolloutPlan:
    return FederatedRolloutPlan(
        federation_id=federation_id,
        name=federation_id,
        bundle_id=bundle_id,
        target_ids=["frt_a"],
        rollout_template_steps=[_step()],
        created_by="release_manager",
        created_at=_now(),
        updated_at=_now(),
    )


@pytest.mark.asyncio
class TestInMemoryTargetStore:
    async def test_create_get_list_target(self) -> None:
        store = InMemoryFederatedRolloutTargetStore()
        target = await store.create(_target())

        assert await store.get("frt_a") == target
        assert await store.list() == [target]
        assert await store.list(tenant_id="tenant_a") == [target]
        assert await store.list(environment="prod") == [target]
        assert await store.list(ring_name="canary") == [target]
        assert await store.list(status=FederatedTargetStatus.ENABLED) == [target]
        assert await store.list(environment="staging") == []

    async def test_enable_disable_target(self) -> None:
        store = InMemoryFederatedRolloutTargetStore()
        await store.create(_target())

        disabled = await store.disable("frt_a")
        assert disabled.status == FederatedTargetStatus.DISABLED
        assert (await store.get("frt_a")).status == FederatedTargetStatus.DISABLED

        enabled = await store.enable("frt_a")
        assert enabled.status == FederatedTargetStatus.ENABLED

    async def test_enable_missing_target_raises_key_error(self) -> None:
        store = InMemoryFederatedRolloutTargetStore()

        with pytest.raises(KeyError, match="frt_missing"):
            await store.enable("frt_missing")


@pytest.mark.asyncio
class TestSQLiteTargetStore:
    async def test_sqlite_target_persists_across_instances(self, tmp_path) -> None:
        db_path = tmp_path / "targets.db"
        store = SQLiteFederatedRolloutTargetStore(str(db_path))
        await store.create(_target())
        store.close()

        reopened = SQLiteFederatedRolloutTargetStore(str(db_path))
        loaded = await reopened.get("frt_a")

        assert loaded is not None
        assert loaded.target_id == "frt_a"
        assert loaded.labels == {"tier": "gold"}
        assert loaded.status == FederatedTargetStatus.ENABLED
        reopened.close()


@pytest.mark.asyncio
class TestInMemoryPlanStore:
    async def test_create_get_update_list_plan(self) -> None:
        store = InMemoryFederatedRolloutPlanStore()
        plan = await store.create(_plan())

        assert await store.get("frp_a") == plan
        assert await store.list() == [plan]
        assert await store.list(status=FederatedRolloutPlanStatus.DRAFT) == [plan]
        assert await store.list(bundle_id="pb_123") == [plan]
        assert await store.list(bundle_id="pb_missing") == []

        updated = plan.model_copy(update={"status": FederatedRolloutPlanStatus.ACTIVE, "updated_at": _now()})
        await store.update(updated)
        assert (await store.get("frp_a")).status == FederatedRolloutPlanStatus.ACTIVE

    async def test_update_missing_plan_raises_key_error(self) -> None:
        store = InMemoryFederatedRolloutPlanStore()

        with pytest.raises(KeyError, match="frp_missing"):
            await store.update(_plan("frp_missing"))


@pytest.mark.asyncio
class TestSQLitePlanStore:
    async def test_sqlite_plan_persists_across_instances(self, tmp_path) -> None:
        db_path = tmp_path / "plans.db"
        store = SQLiteFederatedRolloutPlanStore(str(db_path))
        await store.create(_plan())
        store.close()

        reopened = SQLiteFederatedRolloutPlanStore(str(db_path))
        loaded = await reopened.get("frp_a")

        assert loaded is not None
        assert loaded.federation_id == "frp_a"
        assert loaded.rollout_template_steps[0].step_id == "step_activate"
        assert loaded.target_ids == ["frt_a"]
        reopened.close()

    async def test_sqlite_update_replaces_json_fields(self, tmp_path) -> None:
        store = SQLiteFederatedRolloutPlanStore(str(tmp_path / "plans.db"))
        plan = await store.create(_plan())
        updated = plan.model_copy(update={
            "target_ids": ["frt_a", "frt_b"],
            "status": FederatedRolloutPlanStatus.ACTIVE,
            "updated_at": _now(),
        })

        await store.update(updated)
        loaded = await store.get("frp_a")

        assert loaded is not None
        assert loaded.target_ids == ["frt_a", "frt_b"]
        assert loaded.status == FederatedRolloutPlanStatus.ACTIVE
        store.close()


class TestFactoriesAndProtocols:
    def test_target_factory_memory_and_sqlite(self, tmp_path) -> None:
        assert isinstance(create_federated_rollout_target_store("memory"), FederatedRolloutTargetStore)
        sqlite_store = create_federated_rollout_target_store("sqlite", str(tmp_path / "targets.db"))
        assert isinstance(sqlite_store, SQLiteFederatedRolloutTargetStore)
        sqlite_store.close()

    def test_plan_factory_memory_and_sqlite(self, tmp_path) -> None:
        assert isinstance(create_federated_rollout_plan_store("memory"), FederatedRolloutPlanStore)
        sqlite_store = create_federated_rollout_plan_store("sqlite", str(tmp_path / "plans.db"))
        assert isinstance(sqlite_store, SQLiteFederatedRolloutPlanStore)
        sqlite_store.close()

    def test_factory_rejects_unknown_type(self) -> None:
        with pytest.raises(ValueError, match="Unknown"):
            create_federated_rollout_target_store("redis")
        with pytest.raises(ValueError, match="Unknown"):
            create_federated_rollout_plan_store("redis")
```

- [ ] **Step 2: Run store tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_policy_rollout_federation_store.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'agent_app.runtime.policy_rollout_federation_store'`.

- [ ] **Step 3: Implement federation stores**

Create `agent_app/runtime/policy_rollout_federation_store.py` with these implementation requirements:

```python
"""Stores for federated rollout targets and plans."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from agent_app.governance.policy_rollout import RolloutStep
from agent_app.governance.policy_rollout_federation import (
    FederatedRolloutPlan,
    FederatedRolloutPlanStatus,
    FederatedRolloutTarget,
    FederatedRolloutTargetExecution,
    FederatedRolloutWave,
    FederatedTargetStatus,
    FederationExecutionStrategy,
)


@runtime_checkable
class FederatedRolloutTargetStore(Protocol):
    async def create(self, target: FederatedRolloutTarget) -> FederatedRolloutTarget: ...
    async def get(self, target_id: str) -> FederatedRolloutTarget | None: ...
    async def list(
        self,
        tenant_id: str | None = None,
        environment: str | None = None,
        ring_name: str | None = None,
        status: FederatedTargetStatus | None = None,
    ) -> list[FederatedRolloutTarget]: ...
    async def enable(self, target_id: str) -> FederatedRolloutTarget: ...
    async def disable(self, target_id: str) -> FederatedRolloutTarget: ...


@runtime_checkable
class FederatedRolloutPlanStore(Protocol):
    async def create(self, plan: FederatedRolloutPlan) -> FederatedRolloutPlan: ...
    async def get(self, federation_id: str) -> FederatedRolloutPlan | None: ...
    async def update(self, plan: FederatedRolloutPlan) -> FederatedRolloutPlan: ...
    async def list(
        self,
        status: FederatedRolloutPlanStatus | None = None,
        bundle_id: str | None = None,
    ) -> list[FederatedRolloutPlan]: ...
```

Implementation details:

- `InMemoryFederatedRolloutTargetStore` uses `self._targets: dict[str, FederatedRolloutTarget]` and returns results sorted by `created_at ASC, target_id ASC`.
- `enable()` and `disable()` fetch the target, raise `KeyError(f"Federated rollout target '{target_id}' not found")` when missing, and store a `model_copy(update={"status": ...})`.
- `SQLiteFederatedRolloutTargetStore` creates exactly the table from the Phase 46 spec plus indexes on `tenant_id`, `environment`, `ring_name`, and `status`.
- Target JSON columns are `labels_json` and `metadata_json`; load with `json.loads`, dump with `model_dump(mode="json")`-compatible values.
- `InMemoryFederatedRolloutPlanStore` uses `self._plans: dict[str, FederatedRolloutPlan]` and returns results sorted by `created_at ASC, federation_id ASC`.
- `SQLiteFederatedRolloutPlanStore` creates exactly the table from the Phase 46 spec plus indexes on `status` and `bundle_id`.
- Plan update first checks existence and raises `KeyError(f"Federated rollout plan '{plan.federation_id}' not found")` when missing.
- Plan SQLite serialization uses:

```python
json.dumps(plan.target_ids)
json.dumps([wave.model_dump(mode="json") for wave in plan.waves])
json.dumps([execution.model_dump(mode="json") for execution in plan.executions])
json.dumps([step.model_dump(mode="json") for step in plan.rollout_template_steps])
```

- Plan SQLite deserialization uses:

```python
data["strategy"] = FederationExecutionStrategy(data["strategy"])
data["status"] = FederatedRolloutPlanStatus(data["status"])
data["target_ids"] = json.loads(data.pop("target_ids_json"))
data["waves"] = [FederatedRolloutWave(**item) for item in json.loads(data.pop("waves_json"))]
data["executions"] = [FederatedRolloutTargetExecution(**item) for item in json.loads(data.pop("executions_json"))]
data["rollout_template_steps"] = [RolloutStep(**item) for item in json.loads(data.pop("rollout_template_steps_json"))]
data["created_at"] = datetime.fromisoformat(data["created_at"])
data["updated_at"] = datetime.fromisoformat(data["updated_at"])
```

- Factories:

```python
def create_federated_rollout_target_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> FederatedRolloutTargetStore:
    if store_type == "memory":
        return InMemoryFederatedRolloutTargetStore()
    if store_type == "sqlite":
        return SQLiteFederatedRolloutTargetStore(db_path or ".agent_app/federated_rollout_targets.db")
    raise ValueError(f"Unknown federated rollout target store type '{store_type}'. Supported: 'memory', 'sqlite'.")


def create_federated_rollout_plan_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> FederatedRolloutPlanStore:
    if store_type == "memory":
        return InMemoryFederatedRolloutPlanStore()
    if store_type == "sqlite":
        return SQLiteFederatedRolloutPlanStore(db_path or ".agent_app/federated_rollout_plans.db")
    raise ValueError(f"Unknown federated rollout plan store type '{store_type}'. Supported: 'memory', 'sqlite'.")
```

- [ ] **Step 4: Run store tests to verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_policy_rollout_federation_store.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add agent_app/runtime/policy_rollout_federation_store.py tests/unit/test_policy_rollout_federation_store.py
git commit -m "feat: Phase 46 Task 2 — federation target and plan stores"
```

---

## Task 3: Federation conflict detector

**Files:**
- Create: `agent_app/runtime/policy_rollout_conflict_detector.py`
- Test: `tests/unit/test_policy_rollout_conflict_detector.py`

- [ ] **Step 1: Write failing conflict detector tests**

Create `tests/unit/test_policy_rollout_conflict_detector.py` with these tests:

```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_rollout import RolloutPlan, RolloutPlanStatus, RolloutStep, RolloutStepType
from agent_app.governance.policy_rollout_federation import (
    FederatedRolloutPlan,
    FederatedRolloutPlanStatus,
    FederatedRolloutTarget,
    FederatedTargetStatus,
    RolloutConflictSeverity,
    RolloutConflictType,
)
from agent_app.runtime.policy_rollout_conflict_detector import RolloutConflictDetector
from agent_app.runtime.policy_rollout_federation_store import (
    InMemoryFederatedRolloutPlanStore,
    InMemoryFederatedRolloutTargetStore,
)
from agent_app.runtime.policy_rollout_store import InMemoryRolloutPlanStore


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _target(
    target_id: str,
    environment: str = "prod",
    ring_name: str | None = "canary",
    status: FederatedTargetStatus = FederatedTargetStatus.ENABLED,
) -> FederatedRolloutTarget:
    return FederatedRolloutTarget(
        target_id=target_id,
        name=target_id,
        environment=environment,
        ring_name=ring_name,
        status=status,
        created_at=_now(),
    )


def _step(environment: str = "prod", ring_name: str | None = "canary") -> RolloutStep:
    return RolloutStep(
        step_id="step_activate",
        step_type=RolloutStepType.ACTIVATE,
        environment=environment,
        ring_name=ring_name,
    )


def _federated_plan(target_ids: list[str], federation_id: str = "frp_new", bundle_id: str = "pb_new") -> FederatedRolloutPlan:
    return FederatedRolloutPlan(
        federation_id=federation_id,
        name=federation_id,
        bundle_id=bundle_id,
        target_ids=target_ids,
        rollout_template_steps=[_step()],
        created_by="release_manager",
        created_at=_now(),
        updated_at=_now(),
    )


@pytest.mark.asyncio
class TestRolloutConflictDetector:
    async def test_duplicate_target_conflict(self) -> None:
        targets = InMemoryFederatedRolloutTargetStore()
        federations = InMemoryFederatedRolloutPlanStore()
        detector = RolloutConflictDetector(targets, federations)
        plan = FederatedRolloutPlan.model_construct(
            federation_id="frp_dup",
            name="dup",
            bundle_id="pb_123",
            target_ids=["frt_a", "frt_a"],
            rollout_template_steps=[_step()],
            created_by="user",
            created_at=_now(),
            updated_at=_now(),
        )

        conflicts = await detector.detect_conflicts(plan)

        assert [c.conflict_type for c in conflicts] == [RolloutConflictType.DUPLICATE_TARGET]
        assert conflicts[0].severity == RolloutConflictSeverity.ERROR
        assert conflicts[0].target_id == "frt_a"

    async def test_missing_target_conflict(self) -> None:
        detector = RolloutConflictDetector(InMemoryFederatedRolloutTargetStore(), InMemoryFederatedRolloutPlanStore())

        conflicts = await detector.detect_conflicts(_federated_plan(["frt_missing"]))

        assert conflicts[0].conflict_type == RolloutConflictType.MISSING_TARGET
        assert conflicts[0].severity == RolloutConflictSeverity.ERROR
        assert conflicts[0].target_id == "frt_missing"

    async def test_disabled_target_conflict(self) -> None:
        target_store = InMemoryFederatedRolloutTargetStore()
        await target_store.create(_target("frt_disabled", status=FederatedTargetStatus.DISABLED))
        detector = RolloutConflictDetector(target_store, InMemoryFederatedRolloutPlanStore())

        conflicts = await detector.detect_conflicts(_federated_plan(["frt_disabled"]))

        assert conflicts[0].conflict_type == RolloutConflictType.DISABLED_TARGET
        assert conflicts[0].severity == RolloutConflictSeverity.ERROR

    async def test_active_federation_same_target_conflict(self) -> None:
        target_store = InMemoryFederatedRolloutTargetStore()
        federation_store = InMemoryFederatedRolloutPlanStore()
        await target_store.create(_target("frt_a"))
        existing = _federated_plan(["frt_a"], federation_id="frp_existing")
        existing = existing.model_copy(update={"status": FederatedRolloutPlanStatus.ACTIVE})
        await federation_store.create(existing)
        detector = RolloutConflictDetector(target_store, federation_store)

        conflicts = await detector.detect_conflicts(_federated_plan(["frt_a"], federation_id="frp_new"))

        assert conflicts[0].conflict_type == RolloutConflictType.TARGET_ALREADY_ACTIVE
        assert conflicts[0].severity == RolloutConflictSeverity.ERROR
        assert conflicts[0].existing_federation_id == "frp_existing"

    async def test_existing_active_rollout_same_environment_ring_conflict(self) -> None:
        target_store = InMemoryFederatedRolloutTargetStore()
        await target_store.create(_target("frt_a", environment="prod", ring_name="canary"))
        rollout_store = InMemoryRolloutPlanStore()
        await rollout_store.create(RolloutPlan(
            rollout_id="ro_existing",
            name="existing",
            bundle_id="pb_existing",
            status=RolloutPlanStatus.ACTIVE,
            steps=[_step("prod", "canary")],
            created_by="user",
            created_at=_now(),
            updated_at=_now(),
        ))
        detector = RolloutConflictDetector(target_store, InMemoryFederatedRolloutPlanStore(), rollout_store)

        conflicts = await detector.detect_conflicts(_federated_plan(["frt_a"], bundle_id="pb_new"))

        assert [c.conflict_type for c in conflicts] == [
            RolloutConflictType.ENVIRONMENT_RING_CONFLICT,
            RolloutConflictType.BUNDLE_CONFLICT,
        ]
        assert conflicts[0].severity == RolloutConflictSeverity.ERROR
        assert conflicts[0].existing_rollout_id == "ro_existing"
        assert conflicts[1].severity == RolloutConflictSeverity.WARNING

    async def test_detector_does_not_mutate_state(self) -> None:
        target_store = InMemoryFederatedRolloutTargetStore()
        federation_store = InMemoryFederatedRolloutPlanStore()
        target = await target_store.create(_target("frt_a"))
        plan = _federated_plan(["frt_a"])
        detector = RolloutConflictDetector(target_store, federation_store)

        await detector.detect_conflicts(plan)

        assert await target_store.get("frt_a") == target
        assert await federation_store.list() == []
        assert plan.status == FederatedRolloutPlanStatus.DRAFT

    async def test_conflicts_are_deterministically_ordered(self) -> None:
        detector = RolloutConflictDetector(InMemoryFederatedRolloutTargetStore(), InMemoryFederatedRolloutPlanStore())
        plan = FederatedRolloutPlan.model_construct(
            federation_id="frp_order",
            name="order",
            bundle_id="pb_123",
            target_ids=["frt_b", "frt_a", "frt_b", "frt_a"],
            rollout_template_steps=[_step()],
            created_by="user",
            created_at=_now(),
            updated_at=_now(),
        )

        conflicts = await detector.detect_conflicts(plan)

        assert [(c.conflict_type.value, c.target_id) for c in conflicts] == [
            ("duplicate_target", "frt_a"),
            ("duplicate_target", "frt_b"),
            ("missing_target", "frt_a"),
            ("missing_target", "frt_b"),
        ]
        assert [c.conflict_id for c in conflicts] == [
            "frc_duplicate_target_frt_a",
            "frc_duplicate_target_frt_b",
            "frc_missing_target_frt_a",
            "frc_missing_target_frt_b",
        ]
```

- [ ] **Step 2: Run detector tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_policy_rollout_conflict_detector.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'agent_app.runtime.policy_rollout_conflict_detector'`.

- [ ] **Step 3: Implement conflict detector**

Create `agent_app/runtime/policy_rollout_conflict_detector.py` with this behavior:

```python
"""Conflict detection for federated policy rollouts."""
from __future__ import annotations

from agent_app.governance.policy_rollout import RolloutPlanStatus
from agent_app.governance.policy_rollout_federation import (
    FederatedRolloutPlan,
    FederatedRolloutPlanStatus,
    FederatedTargetStatus,
    RolloutConflict,
    RolloutConflictSeverity,
    RolloutConflictType,
)
from agent_app.runtime.policy_rollout_federation_store import (
    FederatedRolloutPlanStore,
    FederatedRolloutTargetStore,
)
from agent_app.runtime.policy_rollout_store import RolloutPlanStore


class RolloutConflictDetector:
    def __init__(
        self,
        target_store: FederatedRolloutTargetStore,
        federation_store: FederatedRolloutPlanStore,
        rollout_store: RolloutPlanStore | None = None,
    ) -> None:
        self._target_store = target_store
        self._federation_store = federation_store
        self._rollout_store = rollout_store

    async def detect_conflicts(self, federation_plan: FederatedRolloutPlan) -> list[RolloutConflict]:
        conflicts: list[RolloutConflict] = []
        target_ids = self._effective_target_ids(federation_plan)
        conflicts.extend(self._detect_duplicates(target_ids))

        loaded_targets = {}
        for target_id in sorted(set(target_ids)):
            target = await self._target_store.get(target_id)
            if target is None:
                conflicts.append(self._conflict(
                    RolloutConflictType.MISSING_TARGET,
                    RolloutConflictSeverity.ERROR,
                    target_id=target_id,
                    message=f"Federated rollout target '{target_id}' does not exist.",
                ))
                continue
            loaded_targets[target_id] = target
            if target.status == FederatedTargetStatus.DISABLED:
                conflicts.append(self._conflict(
                    RolloutConflictType.DISABLED_TARGET,
                    RolloutConflictSeverity.ERROR,
                    target_id=target_id,
                    environment=target.environment,
                    ring_name=target.ring_name,
                    message=f"Federated rollout target '{target_id}' is disabled.",
                ))

        active_federations = await self._federation_store.list(status=FederatedRolloutPlanStatus.ACTIVE)
        for existing in sorted(active_federations, key=lambda item: item.federation_id):
            if existing.federation_id == federation_plan.federation_id:
                continue
            existing_targets = set(self._effective_target_ids(existing))
            for target_id in sorted(set(target_ids) & existing_targets):
                target = loaded_targets.get(target_id)
                conflicts.append(self._conflict(
                    RolloutConflictType.TARGET_ALREADY_ACTIVE,
                    RolloutConflictSeverity.ERROR,
                    target_id=target_id,
                    environment=getattr(target, "environment", None),
                    ring_name=getattr(target, "ring_name", None),
                    existing_federation_id=existing.federation_id,
                    message=f"Target '{target_id}' is already active in federated rollout '{existing.federation_id}'.",
                ))

        if self._rollout_store is not None:
            active_rollouts = await self._rollout_store.list(status=RolloutPlanStatus.ACTIVE)
            for target_id in sorted(loaded_targets):
                target = loaded_targets[target_id]
                for rollout in sorted(active_rollouts, key=lambda item: item.rollout_id):
                    matching_steps = [
                        step for step in rollout.steps
                        if step.environment == target.environment and step.ring_name == target.ring_name
                    ]
                    if not matching_steps:
                        continue
                    conflicts.append(self._conflict(
                        RolloutConflictType.ENVIRONMENT_RING_CONFLICT,
                        RolloutConflictSeverity.ERROR,
                        target_id=target_id,
                        environment=target.environment,
                        ring_name=target.ring_name,
                        existing_rollout_id=rollout.rollout_id,
                        message=(
                            f"Active rollout '{rollout.rollout_id}' already targets "
                            f"environment '{target.environment}' ring '{target.ring_name or ''}'."
                        ),
                    ))
                    if rollout.bundle_id != federation_plan.bundle_id:
                        conflicts.append(self._conflict(
                            RolloutConflictType.BUNDLE_CONFLICT,
                            RolloutConflictSeverity.WARNING,
                            target_id=target_id,
                            environment=target.environment,
                            ring_name=target.ring_name,
                            existing_rollout_id=rollout.rollout_id,
                            message=(
                                f"Active rollout '{rollout.rollout_id}' uses bundle '{rollout.bundle_id}' "
                                f"while federated rollout uses bundle '{federation_plan.bundle_id}'."
                            ),
                        ))

        return sorted(conflicts, key=lambda c: (c.conflict_type.value, c.target_id or "", c.existing_rollout_id or "", c.existing_federation_id or ""))
```

Also implement helpers:

```python
    def _effective_target_ids(self, plan: FederatedRolloutPlan) -> list[str]:
        if plan.target_ids:
            return list(plan.target_ids)
        ids: list[str] = []
        for wave in plan.waves:
            ids.extend(wave.target_ids)
        return ids

    def _detect_duplicates(self, target_ids: list[str]) -> list[RolloutConflict]:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for target_id in target_ids:
            if target_id in seen:
                duplicates.add(target_id)
            seen.add(target_id)
        return [
            self._conflict(
                RolloutConflictType.DUPLICATE_TARGET,
                RolloutConflictSeverity.ERROR,
                target_id=target_id,
                message=f"Target '{target_id}' appears multiple times in the federated rollout plan.",
            )
            for target_id in sorted(duplicates)
        ]

    def _conflict(
        self,
        conflict_type: RolloutConflictType,
        severity: RolloutConflictSeverity,
        message: str,
        target_id: str | None = None,
        environment: str | None = None,
        ring_name: str | None = None,
        existing_rollout_id: str | None = None,
        existing_federation_id: str | None = None,
        metadata: dict | None = None,
    ) -> RolloutConflict:
        parts = [conflict_type.value]
        if target_id:
            parts.append(target_id)
        if existing_rollout_id:
            parts.append(existing_rollout_id)
        if existing_federation_id:
            parts.append(existing_federation_id)
        conflict_id = "frc_" + "_".join(parts).replace(".", "_").replace(":", "_")
        return RolloutConflict(
            conflict_id=conflict_id,
            conflict_type=conflict_type,
            severity=severity,
            target_id=target_id,
            environment=environment,
            ring_name=ring_name,
            existing_rollout_id=existing_rollout_id,
            existing_federation_id=existing_federation_id,
            message=message,
            metadata=metadata or {},
        )
```

- [ ] **Step 4: Run detector tests to verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_policy_rollout_conflict_detector.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add agent_app/runtime/policy_rollout_conflict_detector.py tests/unit/test_policy_rollout_conflict_detector.py
git commit -m "feat: Phase 46 Task 3 — rollout federation conflict detector"
```

---

## Task 4: Federation service target, plan creation, start, and conflict flow

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_service.py`
- Test: `tests/unit/test_policy_rollout_federation_service.py`

- [ ] **Step 1: Write failing service tests for target/create/start/conflicts**

Create `tests/unit/test_policy_rollout_federation_service.py` with these initial tests:

```python
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_app.core.context import RunContext
from agent_app.governance.policy_change_event import PolicyChangeEventType
from agent_app.governance.policy_rbac import PolicyReleasePermission
from agent_app.governance.policy_rollout import RolloutPlan, RolloutPlanStatus, RolloutStep, RolloutStepStatus, RolloutStepType
from agent_app.governance.policy_rollout_federation import (
    FederatedRolloutPlanStatus,
    FederatedRolloutTargetExecutionStatus,
    FederatedRolloutWave,
    FederationExecutionStrategy,
    RolloutConflict,
    RolloutConflictSeverity,
    RolloutConflictType,
)
from agent_app.runtime.policy_rollout_conflict_detector import RolloutConflictDetector
from agent_app.runtime.policy_rollout_federation_service import RolloutFederationService
from agent_app.runtime.policy_rollout_federation_store import (
    InMemoryFederatedRolloutPlanStore,
    InMemoryFederatedRolloutTargetStore,
)
from agent_app.runtime.policy_rollout_store import InMemoryRolloutPlanStore


def _context(*permissions: str, metadata: dict | None = None) -> RunContext:
    return RunContext(
        run_id="run_test",
        user_id="release_manager",
        tenant_id="tenant_a",
        permissions=list(permissions),
        metadata=metadata or {},
    )


def _step(environment: str = "prod", ring_name: str | None = "canary") -> RolloutStep:
    return RolloutStep(
        step_id="step_activate",
        step_type=RolloutStepType.ACTIVATE,
        environment=environment,
        ring_name=ring_name,
    )


def _service(
    notification_service=None,
    audit_logger=None,
    event_store=None,
    conflict_detector=None,
):
    target_store = InMemoryFederatedRolloutTargetStore()
    federation_store = InMemoryFederatedRolloutPlanStore()
    rollout_store = InMemoryRolloutPlanStore()
    rollout_service = MagicMock()
    rollout_service.create_plan = AsyncMock()
    rollout_service.start_plan = AsyncMock()
    rollout_service.run_all_available = AsyncMock()
    service = RolloutFederationService(
        target_store=target_store,
        federation_store=federation_store,
        rollout_store=rollout_store,
        rollout_service=rollout_service,
        conflict_detector=conflict_detector,
        notification_service=notification_service,
        audit_logger=audit_logger,
        event_store=event_store,
    )
    return service, target_store, federation_store, rollout_store, rollout_service


@pytest.mark.asyncio
class TestRolloutFederationServiceCreate:
    async def test_create_target_requires_permission(self) -> None:
        service, _, _, _, _ = _service()

        with pytest.raises(PermissionError, match="policy.federation.target.create"):
            await service.create_target(
                name="prod-us-canary",
                environment="prod",
                actor_id="admin",
                context=_context(),
            )

    async def test_create_target_stores_target_and_audits(self) -> None:
        audit_logger = MagicMock()
        audit_logger.log = AsyncMock()
        event_store = MagicMock()
        event_store.append = AsyncMock()
        service, target_store, _, _, _ = _service(audit_logger=audit_logger, event_store=event_store)

        target = await service.create_target(
            name="prod-us-canary",
            environment="prod",
            tenant_id="tenant_a",
            ring_name="canary",
            region="us-east",
            labels={"tier": "gold"},
            actor_id="admin",
            context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value),
        )

        assert target.target_id.startswith("frt_")
        assert target.environment == "prod"
        assert await target_store.get(target.target_id) == target
        assert audit_logger.log.await_args.args[0].event_type == "policy.federation.target.created"
        assert event_store.append.await_args.args[0].event_type == PolicyChangeEventType.FEDERATION_TARGET_CREATED

    async def test_create_federated_plan_requires_permission(self) -> None:
        service, target_store, _, _, _ = _service()
        target = await service.create_target(
            name="prod",
            environment="prod",
            context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value),
        )

        with pytest.raises(PermissionError, match="policy.federation.plan.create"):
            await service.create_federated_plan(
                name="global rollout",
                bundle_id="pb_123",
                target_ids=[target.target_id],
                rollout_template_steps=[_step()],
                created_by="release_manager",
                context=_context(),
            )

    async def test_create_federated_plan_creates_executions_and_stores_draft(self) -> None:
        service, target_store, federation_store, _, _ = _service()
        target = await service.create_target(
            name="prod",
            environment="prod",
            context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value),
        )

        plan = await service.create_federated_plan(
            name="global rollout",
            bundle_id="pb_123",
            target_ids=[target.target_id],
            rollout_template_steps=[_step()],
            created_by="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_CREATE.value),
            reason="release",
        )

        assert plan.federation_id.startswith("frp_")
        assert plan.status == FederatedRolloutPlanStatus.DRAFT
        assert plan.executions[0].execution_id.startswith("fre_")
        assert plan.executions[0].target_id == target.target_id
        assert plan.executions[0].status == FederatedRolloutTargetExecutionStatus.PENDING
        assert await federation_store.get(plan.federation_id) == plan

    async def test_create_plan_fails_on_error_conflicts(self) -> None:
        conflict = RolloutConflict(
            conflict_id="frc_test",
            conflict_type=RolloutConflictType.MISSING_TARGET,
            severity=RolloutConflictSeverity.ERROR,
            target_id="frt_missing",
            message="missing",
        )
        detector = MagicMock()
        detector.detect_conflicts = AsyncMock(return_value=[conflict])
        service, _, _, _, _ = _service(conflict_detector=detector)

        with pytest.raises(ValueError, match="Federated rollout conflicts"):
            await service.create_federated_plan(
                name="bad",
                bundle_id="pb_123",
                target_ids=["frt_missing"],
                rollout_template_steps=[_step()],
                created_by="release_manager",
                context=_context(PolicyReleasePermission.FEDERATION_PLAN_CREATE.value),
            )

    async def test_create_plan_allows_error_conflicts_with_context_override(self) -> None:
        conflict = RolloutConflict(
            conflict_id="frc_test",
            conflict_type=RolloutConflictType.MISSING_TARGET,
            severity=RolloutConflictSeverity.ERROR,
            target_id="frt_missing",
            message="missing",
        )
        detector = MagicMock()
        detector.detect_conflicts = AsyncMock(return_value=[conflict])
        service, _, federation_store, _, _ = _service(conflict_detector=detector)

        plan = await service.create_federated_plan(
            name="override",
            bundle_id="pb_123",
            target_ids=["frt_missing"],
            rollout_template_steps=[_step()],
            created_by="release_manager",
            context=_context(
                PolicyReleasePermission.FEDERATION_PLAN_CREATE.value,
                metadata={"allow_federation_conflict_override": True},
            ),
        )

        assert await federation_store.get(plan.federation_id) == plan

    async def test_start_plan_rechecks_conflicts_and_marks_active(self) -> None:
        service, target_store, federation_store, _, _ = _service()
        target = await service.create_target(
            name="prod",
            environment="prod",
            context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value),
        )
        plan = await service.create_federated_plan(
            name="global rollout",
            bundle_id="pb_123",
            target_ids=[target.target_id],
            rollout_template_steps=[_step()],
            created_by="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_CREATE.value),
        )

        started = await service.start_federated_plan(
            plan.federation_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_START.value),
        )

        assert started.status == FederatedRolloutPlanStatus.ACTIVE
        assert (await federation_store.get(plan.federation_id)).status == FederatedRolloutPlanStatus.ACTIVE

    async def test_detect_conflicts_by_id_delegates_to_detector(self) -> None:
        service, target_store, federation_store, _, _ = _service()
        target = await service.create_target(
            name="prod",
            environment="prod",
            context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value),
        )
        plan = await service.create_federated_plan(
            name="global rollout",
            bundle_id="pb_123",
            target_ids=[target.target_id],
            rollout_template_steps=[_step()],
            created_by="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_CREATE.value),
        )

        conflicts = await service.detect_conflicts(plan.federation_id)

        assert conflicts == []
```

- [ ] **Step 2: Run service tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_policy_rollout_federation_service.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'agent_app.runtime.policy_rollout_federation_service'` or missing RBAC/change event values.

- [ ] **Step 3: Implement service permissions, target creation, plan creation, start, and conflicts**

Create `agent_app/runtime/policy_rollout_federation_service.py` with this structure:

```python
"""Federated rollout coordinator service."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from agent_app.core.context import RunContext
from agent_app.governance.audit import AuditEvent
from agent_app.governance.policy_change_event import PolicyChangeEvent, PolicyChangeEventType
from agent_app.governance.policy_rbac import PolicyReleasePermission
from agent_app.governance.policy_rollout import RolloutPlanStatus, RolloutStep, RolloutStepStatus
from agent_app.governance.policy_rollout_federation import (
    FederatedRolloutPlan,
    FederatedRolloutPlanStatus,
    FederatedRolloutTarget,
    FederatedRolloutTargetExecution,
    FederatedRolloutTargetExecutionStatus,
    FederatedRolloutWave,
    FederatedTargetStatus,
    FederationExecutionStrategy,
    RolloutConflict,
    RolloutConflictSeverity,
)
from agent_app.runtime.policy_rollout_conflict_detector import RolloutConflictDetector
from agent_app.runtime.policy_rollout_federation_store import FederatedRolloutPlanStore, FederatedRolloutTargetStore
from agent_app.runtime.policy_rollout_store import RolloutPlanStore


class RolloutFederationService:
    def __init__(
        self,
        target_store: FederatedRolloutTargetStore,
        federation_store: FederatedRolloutPlanStore,
        rollout_store: RolloutPlanStore,
        rollout_service: Any,
        conflict_detector: RolloutConflictDetector | None = None,
        history_recorder: Any | None = None,
        notification_service: Any | None = None,
        audit_logger: Any | None = None,
        event_store: Any | None = None,
        fail_on_error_conflicts: bool = True,
        warn_on_bundle_conflict: bool = True,
    ) -> None:
        self._target_store = target_store
        self._federation_store = federation_store
        self._rollout_store = rollout_store
        self._rollout_service = rollout_service
        self._conflict_detector = conflict_detector or RolloutConflictDetector(target_store, federation_store, rollout_store)
        self._history_recorder = history_recorder
        self._notification_service = notification_service
        self._audit_logger = audit_logger
        self._event_store = event_store
        self._fail_on_error_conflicts = fail_on_error_conflicts
        self._warn_on_bundle_conflict = warn_on_bundle_conflict
```

Implement helper methods:

```python
    async def _check_permission(self, permission: PolicyReleasePermission, context: RunContext) -> None:
        if permission.value not in (context.permissions or []):
            raise PermissionError(f"Permission denied: {permission.value} required")

    async def _write_audit(self, event_type: str, user_id: str | None, tenant_id: str | None, data: dict[str, Any]) -> None:
        if self._audit_logger is None:
            return
        event = AuditEvent(
            event_id=f"ae_{uuid.uuid4().hex[:12]}",
            event_type=event_type,
            user_id=user_id,
            tenant_id=tenant_id,
            data=data,
            created_at=datetime.now(timezone.utc),
        )
        await self._audit_logger.log(event)

    async def _emit_change_event(
        self,
        event_type: PolicyChangeEventType,
        actor_id: str | None,
        bundle_id: str | None = None,
        reason: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        if self._event_store is None:
            return
        event = PolicyChangeEvent(
            event_id=f"pce_{uuid.uuid4().hex[:12]}",
            event_type=event_type,
            bundle_id=bundle_id,
            actor_id=actor_id,
            reason=reason,
            data=data or {},
            created_at=datetime.now(timezone.utc),
        )
        await self._event_store.append(event)

    def _has_error_conflicts(self, conflicts: list[RolloutConflict]) -> bool:
        return any(c.severity == RolloutConflictSeverity.ERROR for c in conflicts)

    def _conflict_summary(self, conflicts: list[RolloutConflict]) -> str:
        return "; ".join(f"{c.conflict_type.value}:{c.target_id or '-'}:{c.message}" for c in conflicts)

    def _effective_target_ids(self, target_ids: list[str], waves: list[FederatedRolloutWave] | None) -> list[str]:
        if target_ids:
            return list(target_ids)
        ordered: list[str] = []
        for wave in waves or []:
            for target_id in wave.target_ids:
                if target_id not in ordered:
                    ordered.append(target_id)
        return ordered
```

Implement public methods from this task:

```python
    async def create_target(
        self,
        name: str,
        environment: str,
        tenant_id: str | None = None,
        ring_name: str | None = None,
        region: str | None = None,
        labels: dict[str, str] | None = None,
        actor_id: str | None = None,
        context: RunContext | None = None,
    ) -> FederatedRolloutTarget:
        ctx = context or RunContext(run_id="federation", user_id=actor_id or "system", permissions=[])
        await self._check_permission(PolicyReleasePermission.FEDERATION_TARGET_CREATE, ctx)
        target = FederatedRolloutTarget(
            target_id=f"frt_{uuid.uuid4().hex[:12]}",
            name=name,
            tenant_id=tenant_id,
            environment=environment,
            ring_name=ring_name,
            region=region,
            labels=labels or {},
            created_at=datetime.now(timezone.utc),
        )
        target = await self._target_store.create(target)
        await self._write_audit("policy.federation.target.created", actor_id, ctx.tenant_id, {"target_id": target.target_id})
        await self._emit_change_event(
            PolicyChangeEventType.FEDERATION_TARGET_CREATED,
            actor_id=actor_id,
            data={"target_id": target.target_id, "environment": environment, "ring_name": ring_name},
        )
        return target

    async def create_federated_plan(
        self,
        name: str,
        bundle_id: str,
        target_ids: list[str],
        rollout_template_steps: list[RolloutStep],
        created_by: str,
        context: RunContext,
        strategy: FederationExecutionStrategy = FederationExecutionStrategy.SEQUENTIAL,
        waves: list[FederatedRolloutWave] | None = None,
        reason: str | None = None,
    ) -> FederatedRolloutPlan:
        await self._check_permission(PolicyReleasePermission.FEDERATION_PLAN_CREATE, context)
        normalized_target_ids = self._effective_target_ids(target_ids, waves)
        now = datetime.now(timezone.utc)
        executions = [
            FederatedRolloutTargetExecution(
                execution_id=f"fre_{uuid.uuid4().hex[:12]}",
                target_id=target_id,
            )
            for target_id in normalized_target_ids
        ]
        plan = FederatedRolloutPlan(
            federation_id=f"frp_{uuid.uuid4().hex[:12]}",
            name=name,
            bundle_id=bundle_id,
            strategy=strategy,
            status=FederatedRolloutPlanStatus.DRAFT,
            target_ids=normalized_target_ids,
            waves=waves or [],
            executions=executions,
            rollout_template_steps=rollout_template_steps,
            created_by=created_by,
            reason=reason,
            created_at=now,
            updated_at=now,
        )
        conflicts = await self._conflict_detector.detect_conflicts(plan)
        if conflicts:
            await self._write_audit("policy.federation.conflict.detected", created_by, context.tenant_id, {
                "federation_id": plan.federation_id,
                "conflicts": [c.model_dump(mode="json") for c in conflicts],
            })
        allow_override = bool(context.metadata.get("allow_federation_conflict_override"))
        if self._fail_on_error_conflicts and self._has_error_conflicts(conflicts) and not allow_override:
            raise ValueError(f"Federated rollout conflicts: {self._conflict_summary(conflicts)}")
        plan = await self._federation_store.create(plan)
        await self._write_audit("policy.federation.plan.created", created_by, context.tenant_id, {
            "federation_id": plan.federation_id,
            "bundle_id": bundle_id,
            "target_count": len(plan.target_ids),
        })
        await self._emit_change_event(
            PolicyChangeEventType.FEDERATION_PLAN_CREATED,
            actor_id=created_by,
            bundle_id=bundle_id,
            reason=reason,
            data={"federation_id": plan.federation_id, "target_count": len(plan.target_ids)},
        )
        return plan

    async def start_federated_plan(self, federation_id: str, actor_id: str, context: RunContext) -> FederatedRolloutPlan:
        await self._check_permission(PolicyReleasePermission.FEDERATION_PLAN_START, context)
        plan = await self._federation_store.get(federation_id)
        if plan is None:
            raise KeyError(f"Federated rollout plan '{federation_id}' not found")
        if plan.status != FederatedRolloutPlanStatus.DRAFT:
            raise ValueError(f"Cannot start federated rollout plan with status '{plan.status}'. Must be DRAFT.")
        conflicts = await self._conflict_detector.detect_conflicts(plan)
        if self._has_error_conflicts(conflicts):
            await self._write_audit("policy.federation.conflict.detected", actor_id, context.tenant_id, {
                "federation_id": federation_id,
                "conflicts": [c.model_dump(mode="json") for c in conflicts],
            })
            raise ValueError(f"Federated rollout conflicts: {self._conflict_summary(conflicts)}")
        updated = plan.model_copy(update={
            "status": FederatedRolloutPlanStatus.ACTIVE,
            "updated_at": datetime.now(timezone.utc),
        })
        updated = await self._federation_store.update(updated)
        await self._write_audit("policy.federation.plan.started", actor_id, context.tenant_id, {"federation_id": federation_id})
        await self._emit_change_event(
            PolicyChangeEventType.FEDERATION_PLAN_STARTED,
            actor_id=actor_id,
            bundle_id=updated.bundle_id,
            data={"federation_id": federation_id},
        )
        return updated

    async def detect_conflicts(self, federation_id: str) -> list[RolloutConflict]:
        plan = await self._federation_store.get(federation_id)
        if plan is None:
            raise KeyError(f"Federated rollout plan '{federation_id}' not found")
        return await self._conflict_detector.detect_conflicts(plan)
```

- [ ] **Step 4: Add temporary RBAC and change event values if tests need them**

If tests fail because permissions or change events are missing, add the Phase 46 enum values in Task 6 before rerunning the service tests, or temporarily import string constants in the service and complete enum wiring in Task 6. Prefer completing the enum wiring in Task 6 immediately if this blocks Task 4.

- [ ] **Step 5: Run service tests to verify this slice passes**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_policy_rollout_federation_service.py -v
```

Expected: PASS for the tests added in Step 1.

- [ ] **Step 6: Commit Task 4**

```bash
git add agent_app/runtime/policy_rollout_federation_service.py tests/unit/test_policy_rollout_federation_service.py
git commit -m "feat: Phase 46 Task 4 — federation service create and start lifecycle"
```

---

## Task 5: Federation service execution, waves, cancellation, and notifications

**Files:**
- Modify: `agent_app/runtime/policy_rollout_federation_service.py`
- Modify: `tests/unit/test_policy_rollout_federation_service.py`

- [ ] **Step 1: Add failing execution tests**

Append these tests to `tests/unit/test_policy_rollout_federation_service.py`:

```python
@pytest.mark.asyncio
class TestRolloutFederationServiceExecution:
    async def test_run_next_creates_child_rollout_and_marks_execution_succeeded(self) -> None:
        service, target_store, federation_store, _, rollout_service = _service()
        target = await service.create_target(
            name="prod",
            environment="prod",
            ring_name="canary",
            context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value),
        )
        child_created = RolloutPlan(
            rollout_id="ro_child",
            name="global rollout / prod",
            bundle_id="pb_123",
            status=RolloutPlanStatus.DRAFT,
            steps=[_step("prod", "canary")],
            created_by="release_manager",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        child_done = child_created.model_copy(update={
            "status": RolloutPlanStatus.COMPLETED,
            "steps": [child_created.steps[0].model_copy(update={"status": RolloutStepStatus.SUCCEEDED})],
        })
        rollout_service.create_plan.return_value = child_created
        rollout_service.start_plan.return_value = child_created.model_copy(update={"status": RolloutPlanStatus.ACTIVE})
        rollout_service.run_all_available.return_value = child_done
        plan = await service.create_federated_plan(
            name="global rollout",
            bundle_id="pb_123",
            target_ids=[target.target_id],
            rollout_template_steps=[_step()],
            created_by="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_CREATE.value),
        )
        await service.start_federated_plan(plan.federation_id, "release_manager", _context(PolicyReleasePermission.FEDERATION_PLAN_START.value))

        updated = await service.run_next_target(
            plan.federation_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value),
        )

        assert updated.executions[0].rollout_id == "ro_child"
        assert updated.executions[0].status == FederatedRolloutTargetExecutionStatus.SUCCEEDED
        assert updated.status == FederatedRolloutPlanStatus.COMPLETED
        created_steps = rollout_service.create_plan.await_args.kwargs["steps"]
        assert created_steps[0].environment == "prod"
        assert created_steps[0].ring_name == "canary"
        assert created_steps[0].step_id.endswith(target.target_id[-6:])

    async def test_run_next_marks_blocked_child_rollout_blocked(self) -> None:
        service, target_store, federation_store, _, rollout_service = _service()
        target = await service.create_target(
            name="prod",
            environment="prod",
            context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value),
        )
        child = RolloutPlan(
            rollout_id="ro_blocked",
            name="blocked",
            bundle_id="pb_123",
            status=RolloutPlanStatus.ACTIVE,
            steps=[_step().model_copy(update={"status": RolloutStepStatus.BLOCKED, "error": {"message": "gate blocked"}})],
            created_by="release_manager",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        rollout_service.create_plan.return_value = child.model_copy(update={"status": RolloutPlanStatus.DRAFT})
        rollout_service.start_plan.return_value = child
        rollout_service.run_all_available.return_value = child
        plan = await service.create_federated_plan(
            name="global rollout",
            bundle_id="pb_123",
            target_ids=[target.target_id],
            rollout_template_steps=[_step()],
            created_by="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_CREATE.value),
        )
        await service.start_federated_plan(plan.federation_id, "release_manager", _context(PolicyReleasePermission.FEDERATION_PLAN_START.value))

        updated = await service.run_next_target(
            plan.federation_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value),
        )

        assert updated.executions[0].status == FederatedRolloutTargetExecutionStatus.BLOCKED
        assert updated.status == FederatedRolloutPlanStatus.BLOCKED
        assert updated.executions[0].error == {"message": "gate blocked"}

    async def test_run_next_failed_child_marks_plan_failed_and_notifies(self) -> None:
        notification_service = MagicMock()
        notification_service.notify = AsyncMock()
        service, target_store, _, _, rollout_service = _service(notification_service=notification_service)
        target = await service.create_target(
            name="prod",
            environment="prod",
            context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value),
        )
        failed_child = RolloutPlan(
            rollout_id="ro_failed",
            name="failed",
            bundle_id="pb_123",
            status=RolloutPlanStatus.FAILED,
            steps=[_step().model_copy(update={"status": RolloutStepStatus.FAILED, "error": {"message": "boom"}})],
            created_by="release_manager",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        rollout_service.create_plan.return_value = failed_child.model_copy(update={"status": RolloutPlanStatus.DRAFT})
        rollout_service.start_plan.return_value = failed_child.model_copy(update={"status": RolloutPlanStatus.ACTIVE})
        rollout_service.run_all_available.return_value = failed_child
        plan = await service.create_federated_plan(
            name="global rollout",
            bundle_id="pb_123",
            target_ids=[target.target_id],
            rollout_template_steps=[_step()],
            created_by="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_CREATE.value),
        )
        await service.start_federated_plan(plan.federation_id, "release_manager", _context(PolicyReleasePermission.FEDERATION_PLAN_START.value))

        updated = await service.run_next_target(
            plan.federation_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value),
        )

        assert updated.status == FederatedRolloutPlanStatus.FAILED
        assert updated.executions[0].status == FederatedRolloutTargetExecutionStatus.FAILED
        assert notification_service.notify.await_count == 1

    async def test_run_all_available_completes_sequential_plan(self) -> None:
        service, target_store, _, _, rollout_service = _service()
        t1 = await service.create_target("prod-a", "prod", ring_name="canary", context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value))
        t2 = await service.create_target("prod-b", "prod", ring_name="stable", context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value))
        created = RolloutPlan(
            rollout_id="ro_child",
            name="child",
            bundle_id="pb_123",
            status=RolloutPlanStatus.DRAFT,
            steps=[_step()],
            created_by="release_manager",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        done = created.model_copy(update={"status": RolloutPlanStatus.COMPLETED})
        rollout_service.create_plan.side_effect = [created.model_copy(update={"rollout_id": "ro_1"}), created.model_copy(update={"rollout_id": "ro_2"})]
        rollout_service.start_plan.side_effect = [done.model_copy(update={"rollout_id": "ro_1"}), done.model_copy(update={"rollout_id": "ro_2"})]
        rollout_service.run_all_available.side_effect = [done.model_copy(update={"rollout_id": "ro_1"}), done.model_copy(update={"rollout_id": "ro_2"})]
        plan = await service.create_federated_plan(
            "global rollout",
            "pb_123",
            [t1.target_id, t2.target_id],
            [_step()],
            "release_manager",
            _context(PolicyReleasePermission.FEDERATION_PLAN_CREATE.value),
        )
        await service.start_federated_plan(plan.federation_id, "release_manager", _context(PolicyReleasePermission.FEDERATION_PLAN_START.value))

        updated = await service.run_all_available(
            plan.federation_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value),
        )

        assert [e.status for e in updated.executions] == [
            FederatedRolloutTargetExecutionStatus.SUCCEEDED,
            FederatedRolloutTargetExecutionStatus.SUCCEEDED,
        ]
        assert updated.status == FederatedRolloutPlanStatus.COMPLETED

    async def test_wave_strategy_advances_first_wave_before_second_wave(self) -> None:
        service, target_store, _, _, rollout_service = _service()
        t1 = await service.create_target("wave1", "prod", ring_name="canary", context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value))
        t2 = await service.create_target("wave2", "prod", ring_name="stable", context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value))
        child = RolloutPlan(
            rollout_id="ro_child",
            name="child",
            bundle_id="pb_123",
            status=RolloutPlanStatus.COMPLETED,
            steps=[_step()],
            created_by="release_manager",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        rollout_service.create_plan.side_effect = [child.model_copy(update={"rollout_id": "ro_1"}), child.model_copy(update={"rollout_id": "ro_2"})]
        rollout_service.start_plan.side_effect = [child.model_copy(update={"rollout_id": "ro_1"}), child.model_copy(update={"rollout_id": "ro_2"})]
        rollout_service.run_all_available.side_effect = [child.model_copy(update={"rollout_id": "ro_1"}), child.model_copy(update={"rollout_id": "ro_2"})]
        plan = await service.create_federated_plan(
            "wave rollout",
            "pb_123",
            [t1.target_id, t2.target_id],
            [_step()],
            "release_manager",
            _context(PolicyReleasePermission.FEDERATION_PLAN_CREATE.value),
            strategy=FederationExecutionStrategy.WAVE,
            waves=[
                FederatedRolloutWave(wave_id="frw_one", target_ids=[t1.target_id]),
                FederatedRolloutWave(wave_id="frw_two", target_ids=[t2.target_id]),
            ],
        )
        await service.start_federated_plan(plan.federation_id, "release_manager", _context(PolicyReleasePermission.FEDERATION_PLAN_START.value))

        first = await service.run_next_target(plan.federation_id, "release_manager", _context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value))
        second = await service.run_next_target(plan.federation_id, "release_manager", _context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value))

        assert first.executions[0].target_id == t1.target_id
        assert first.executions[0].status == FederatedRolloutTargetExecutionStatus.SUCCEEDED
        assert first.executions[1].status == FederatedRolloutTargetExecutionStatus.PENDING
        assert second.executions[1].target_id == t2.target_id
        assert second.status == FederatedRolloutPlanStatus.COMPLETED

    async def test_cancel_marks_plan_and_pending_executions_cancelled(self) -> None:
        service, target_store, federation_store, _, _ = _service()
        target = await service.create_target("prod", "prod", context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value))
        plan = await service.create_federated_plan(
            "global rollout",
            "pb_123",
            [target.target_id],
            [_step()],
            "release_manager",
            _context(PolicyReleasePermission.FEDERATION_PLAN_CREATE.value),
        )
        await service.start_federated_plan(plan.federation_id, "release_manager", _context(PolicyReleasePermission.FEDERATION_PLAN_START.value))

        cancelled = await service.cancel_federated_plan(
            plan.federation_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_CANCEL.value),
            reason="stop release",
        )

        assert cancelled.status == FederatedRolloutPlanStatus.CANCELLED
        assert cancelled.executions[0].status == FederatedRolloutTargetExecutionStatus.CANCELLED
        assert (await federation_store.get(plan.federation_id)).status == FederatedRolloutPlanStatus.CANCELLED
```

- [ ] **Step 2: Run service tests to verify new tests fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_policy_rollout_federation_service.py -v
```

Expected: FAIL because `run_next_target`, `run_all_available`, `cancel_federated_plan`, and notification helpers are missing or incomplete.

- [ ] **Step 3: Implement execution helpers and public execution methods**

Modify `agent_app/runtime/policy_rollout_federation_service.py` with these helper behaviors:

```python
    async def _notify(self, event_type: str, data: dict[str, Any]) -> None:
        if self._notification_service is None:
            return
        try:
            await self._notification_service.notify(event_type=event_type, data=data)
        except TypeError:
            await self._notification_service.notify(event_type, data)
        except Exception:
            return

    def _clone_template_steps_for_target(self, plan: FederatedRolloutPlan, target: FederatedRolloutTarget) -> list[RolloutStep]:
        steps: list[RolloutStep] = []
        suffix = target.target_id[-6:]
        for step in plan.rollout_template_steps:
            step_id = f"{step.step_id}_{suffix}"
            require_previous_step = None
            if step.require_previous_step:
                require_previous_step = f"{step.require_previous_step}_{suffix}"
            steps.append(step.model_copy(update={
                "step_id": step_id,
                "environment": target.environment,
                "ring_name": target.ring_name if target.ring_name is not None else step.ring_name,
                "require_previous_step": require_previous_step,
                "status": RolloutStepStatus.PENDING,
                "activation_id": None,
                "assignment_id": None,
                "approval_id": None,
                "error": None,
                "started_at": None,
                "completed_at": None,
            }))
        return steps

    def _next_execution_index(self, plan: FederatedRolloutPlan) -> int | None:
        if plan.strategy in (FederationExecutionStrategy.SEQUENTIAL, FederationExecutionStrategy.PARALLEL):
            for index, execution in enumerate(plan.executions):
                if execution.status == FederatedRolloutTargetExecutionStatus.PENDING:
                    return index
            return None
        if plan.strategy == FederationExecutionStrategy.WAVE:
            by_target = {execution.target_id: (index, execution) for index, execution in enumerate(plan.executions)}
            for wave in plan.waves:
                wave_executions = [by_target[target_id] for target_id in wave.target_ids if target_id in by_target]
                if not wave_executions:
                    continue
                if wave.require_all_successful and any(e.status in (FederatedRolloutTargetExecutionStatus.FAILED, FederatedRolloutTargetExecutionStatus.BLOCKED) for _, e in wave_executions):
                    return None
                pending = [(idx, e) for idx, e in wave_executions if e.status == FederatedRolloutTargetExecutionStatus.PENDING]
                if pending:
                    return pending[0][0]
                if all(e.status in (FederatedRolloutTargetExecutionStatus.SUCCEEDED, FederatedRolloutTargetExecutionStatus.SKIPPED) for _, e in wave_executions):
                    continue
                return None
        return None

    def _execution_status_from_child(self, child_plan: Any) -> FederatedRolloutTargetExecutionStatus:
        if child_plan.status == RolloutPlanStatus.COMPLETED:
            return FederatedRolloutTargetExecutionStatus.SUCCEEDED
        if child_plan.status == RolloutPlanStatus.FAILED:
            return FederatedRolloutTargetExecutionStatus.FAILED
        if any(step.status == RolloutStepStatus.BLOCKED for step in child_plan.steps):
            return FederatedRolloutTargetExecutionStatus.BLOCKED
        return FederatedRolloutTargetExecutionStatus.RUNNING

    def _error_from_child(self, child_plan: Any) -> dict[str, Any] | None:
        for step in child_plan.steps:
            if step.error:
                return step.error
        return None

    def _plan_status_from_executions(self, executions: list[FederatedRolloutTargetExecution]) -> FederatedRolloutPlanStatus:
        statuses = [e.status for e in executions]
        if statuses and all(s in (FederatedRolloutTargetExecutionStatus.SUCCEEDED, FederatedRolloutTargetExecutionStatus.SKIPPED) for s in statuses):
            return FederatedRolloutPlanStatus.COMPLETED
        if any(s == FederatedRolloutTargetExecutionStatus.FAILED for s in statuses):
            return FederatedRolloutPlanStatus.FAILED
        if any(s == FederatedRolloutTargetExecutionStatus.BLOCKED for s in statuses):
            if not any(s == FederatedRolloutTargetExecutionStatus.PENDING for s in statuses):
                return FederatedRolloutPlanStatus.BLOCKED
            return FederatedRolloutPlanStatus.BLOCKED
        return FederatedRolloutPlanStatus.ACTIVE
```

Implement `run_next_target()`:

```python
    async def run_next_target(self, federation_id: str, actor_id: str, context: RunContext) -> FederatedRolloutPlan:
        await self._check_permission(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE, context)
        plan = await self._federation_store.get(federation_id)
        if plan is None:
            raise KeyError(f"Federated rollout plan '{federation_id}' not found")
        if plan.status != FederatedRolloutPlanStatus.ACTIVE:
            raise ValueError(f"Cannot execute federated rollout plan with status '{plan.status}'. Must be ACTIVE.")
        index = self._next_execution_index(plan)
        if index is None:
            return plan
        execution = plan.executions[index]
        target = await self._target_store.get(execution.target_id)
        now = datetime.now(timezone.utc)
        executions = list(plan.executions)
        if target is None:
            executions[index] = execution.model_copy(update={
                "status": FederatedRolloutTargetExecutionStatus.BLOCKED,
                "completed_at": now,
                "error": {"type": "missing_target", "message": f"Target '{execution.target_id}' not found"},
            })
        elif target.status == FederatedTargetStatus.DISABLED:
            executions[index] = execution.model_copy(update={
                "status": FederatedRolloutTargetExecutionStatus.SKIPPED,
                "completed_at": now,
                "metadata": {**execution.metadata, "reason": "target_disabled"},
            })
        else:
            running_execution = execution.model_copy(update={"status": FederatedRolloutTargetExecutionStatus.RUNNING, "started_at": now})
            executions[index] = running_execution
            plan = await self._federation_store.update(plan.model_copy(update={"executions": executions, "updated_at": now}))
            steps = self._clone_template_steps_for_target(plan, target)
            child_plan = await self._rollout_service.create_plan(
                name=f"{plan.name} / {target.name}",
                bundle_id=plan.bundle_id,
                steps=steps,
                created_by=actor_id,
                context=context,
                reason=plan.reason,
            )
            await self._write_audit("policy.federation.plan.target_started", actor_id, context.tenant_id, {
                "federation_id": federation_id,
                "target_id": target.target_id,
                "rollout_id": child_plan.rollout_id,
            })
            started_child = await self._rollout_service.start_plan(child_plan.rollout_id, actor_id, context)
            completed_child = await self._rollout_service.run_all_available(started_child.rollout_id, actor_id, context)
            status = self._execution_status_from_child(completed_child)
            event_name = {
                FederatedRolloutTargetExecutionStatus.SUCCEEDED: "policy.federation.plan.target_succeeded",
                FederatedRolloutTargetExecutionStatus.FAILED: "policy.federation.plan.target_failed",
                FederatedRolloutTargetExecutionStatus.BLOCKED: "policy.federation.plan.blocked",
            }.get(status, "policy.federation.plan.target_started")
            executions = list(plan.executions)
            executions[index] = running_execution.model_copy(update={
                "rollout_id": completed_child.rollout_id,
                "status": status,
                "completed_at": datetime.now(timezone.utc) if status != FederatedRolloutTargetExecutionStatus.RUNNING else None,
                "error": self._error_from_child(completed_child),
            })
            await self._write_audit(event_name, actor_id, context.tenant_id, {
                "federation_id": federation_id,
                "target_id": target.target_id,
                "rollout_id": completed_child.rollout_id,
                "status": status.value,
            })
            if status in (FederatedRolloutTargetExecutionStatus.FAILED, FederatedRolloutTargetExecutionStatus.BLOCKED):
                await self._notify("policy.federation.target_execution_failed", {
                    "federation_id": federation_id,
                    "target_id": target.target_id,
                    "rollout_id": completed_child.rollout_id,
                    "status": status.value,
                })
        new_status = self._plan_status_from_executions(executions)
        updated = plan.model_copy(update={
            "executions": executions,
            "status": new_status,
            "updated_at": datetime.now(timezone.utc),
        })
        updated = await self._federation_store.update(updated)
        if new_status == FederatedRolloutPlanStatus.COMPLETED:
            await self._emit_change_event(PolicyChangeEventType.FEDERATION_PLAN_COMPLETED, actor_id=actor_id, bundle_id=updated.bundle_id, data={"federation_id": federation_id})
            await self._write_audit("policy.federation.plan.completed", actor_id, context.tenant_id, {"federation_id": federation_id})
        if new_status == FederatedRolloutPlanStatus.FAILED:
            await self._emit_change_event(PolicyChangeEventType.FEDERATION_PLAN_FAILED, actor_id=actor_id, bundle_id=updated.bundle_id, data={"federation_id": federation_id})
            await self._write_audit("policy.federation.plan.failed", actor_id, context.tenant_id, {"federation_id": federation_id})
            await self._notify("policy.federation.plan_failed", {"federation_id": federation_id, "status": new_status.value})
        if new_status == FederatedRolloutPlanStatus.BLOCKED:
            await self._notify("policy.federation.plan_blocked", {"federation_id": federation_id, "status": new_status.value})
        return updated
```

Implement `run_all_available()` and `cancel_federated_plan()`:

```python
    async def run_all_available(self, federation_id: str, actor_id: str, context: RunContext) -> FederatedRolloutPlan:
        last_plan = await self._federation_store.get(federation_id)
        if last_plan is None:
            raise KeyError(f"Federated rollout plan '{federation_id}' not found")
        max_iterations = max(len(last_plan.executions), 1) + 1
        for _ in range(max_iterations):
            before = [(e.execution_id, e.status, e.rollout_id) for e in last_plan.executions]
            if last_plan.status in (
                FederatedRolloutPlanStatus.COMPLETED,
                FederatedRolloutPlanStatus.FAILED,
                FederatedRolloutPlanStatus.BLOCKED,
                FederatedRolloutPlanStatus.CANCELLED,
            ):
                return last_plan
            last_plan = await self.run_next_target(federation_id, actor_id, context)
            after = [(e.execution_id, e.status, e.rollout_id) for e in last_plan.executions]
            if after == before:
                return last_plan
        return last_plan

    async def cancel_federated_plan(
        self,
        federation_id: str,
        actor_id: str,
        context: RunContext,
        reason: str | None = None,
    ) -> FederatedRolloutPlan:
        await self._check_permission(PolicyReleasePermission.FEDERATION_PLAN_CANCEL, context)
        plan = await self._federation_store.get(federation_id)
        if plan is None:
            raise KeyError(f"Federated rollout plan '{federation_id}' not found")
        now = datetime.now(timezone.utc)
        executions = [
            execution.model_copy(update={"status": FederatedRolloutTargetExecutionStatus.CANCELLED, "completed_at": now})
            if execution.status in (FederatedRolloutTargetExecutionStatus.PENDING, FederatedRolloutTargetExecutionStatus.RUNNING)
            else execution
            for execution in plan.executions
        ]
        updated = plan.model_copy(update={
            "status": FederatedRolloutPlanStatus.CANCELLED,
            "executions": executions,
            "reason": reason or plan.reason,
            "updated_at": now,
        })
        updated = await self._federation_store.update(updated)
        await self._write_audit("policy.federation.plan.cancelled", actor_id, context.tenant_id, {"federation_id": federation_id, "reason": reason})
        await self._emit_change_event(
            PolicyChangeEventType.FEDERATION_PLAN_CANCELLED,
            actor_id=actor_id,
            bundle_id=updated.bundle_id,
            reason=reason,
            data={"federation_id": federation_id},
        )
        return updated
```

- [ ] **Step 4: Run service tests to verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_policy_rollout_federation_service.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 5**

```bash
git add agent_app/runtime/policy_rollout_federation_service.py tests/unit/test_policy_rollout_federation_service.py
git commit -m "feat: Phase 46 Task 5 — federation execution, waves, cancellation, notifications"
```

---

## Task 6: RBAC, change events, config, loader, and AgentApp properties

**Files:**
- Modify: `agent_app/governance/policy_rbac.py`
- Modify: `agent_app/governance/policy_change_event.py`
- Modify: `agent_app/config/schema.py`
- Modify: `agent_app/config/loader.py`
- Modify: `agent_app/core/app.py`
- Test: `tests/unit/test_policy_rollout_federation_config.py`

- [ ] **Step 1: Write failing config/RBAC/loader tests**

Create `tests/unit/test_policy_rollout_federation_config.py` with these tests:

```python
from __future__ import annotations

import textwrap

from agent_app.config.loader import build_app, load_config
from agent_app.config.schema import PolicyReleaseStoreConfig, RolloutFederationConfig, RolloutFederationConflictPolicyConfig
from agent_app.core.app import AgentApp
from agent_app.governance.policy_change_event import PolicyChangeEventType
from agent_app.governance.policy_rbac import PolicyReleasePermission, _DEFAULT_ALLOWED


class TestRolloutFederationConfig:
    def test_rollout_federation_config_defaults(self) -> None:
        cfg = RolloutFederationConfig()
        assert cfg.enabled is False
        assert cfg.target_store is None
        assert cfg.plan_store is None
        assert cfg.conflict_policy.fail_on_error is True
        assert cfg.conflict_policy.warn_on_bundle_conflict is True

    def test_rollout_federation_config_with_sqlite_stores(self) -> None:
        cfg = RolloutFederationConfig(
            enabled=True,
            target_store=PolicyReleaseStoreConfig(type="sqlite", path=".agent_app/federated_rollout_targets.db"),
            plan_store=PolicyReleaseStoreConfig(type="sqlite", path=".agent_app/federated_rollout_plans.db"),
            conflict_policy=RolloutFederationConflictPolicyConfig(fail_on_error=True, warn_on_bundle_conflict=False),
        )

        assert cfg.enabled is True
        assert cfg.target_store.type == "sqlite"
        assert cfg.plan_store.path == ".agent_app/federated_rollout_plans.db"
        assert cfg.conflict_policy.warn_on_bundle_conflict is False

    def test_phase45_config_still_loads_without_federation(self, tmp_path) -> None:
        config_path = tmp_path / "agentapp.yaml"
        config_path.write_text(textwrap.dedent("""
        governance:
          policy_release:
            bundles:
              type: memory
            gates:
              type: memory
            rollout_history:
              enabled: true
              store:
                type: memory
        """))

        cfg = load_config(config_path)

        assert cfg.governance.policy_release.rollout_history.enabled is True
        assert cfg.governance.policy_release.rollout_federation is None


class TestFederationRBAC:
    def test_federation_permissions_exist(self) -> None:
        expected = {
            "FEDERATION_TARGET_CREATE": "policy.federation.target.create",
            "FEDERATION_TARGET_VIEW": "policy.federation.target.view",
            "FEDERATION_TARGET_ENABLE": "policy.federation.target.enable",
            "FEDERATION_TARGET_DISABLE": "policy.federation.target.disable",
            "FEDERATION_PLAN_CREATE": "policy.federation.plan.create",
            "FEDERATION_PLAN_START": "policy.federation.plan.start",
            "FEDERATION_PLAN_EXECUTE": "policy.federation.plan.execute",
            "FEDERATION_PLAN_CANCEL": "policy.federation.plan.cancel",
            "FEDERATION_PLAN_VIEW": "policy.federation.plan.view",
            "FEDERATION_CONFLICT_VIEW": "policy.federation.conflict.view",
        }

        for name, value in expected.items():
            assert getattr(PolicyReleasePermission, name).value == value

    def test_federation_view_permissions_default_allowed(self) -> None:
        assert PolicyReleasePermission.FEDERATION_TARGET_VIEW in _DEFAULT_ALLOWED
        assert PolicyReleasePermission.FEDERATION_PLAN_VIEW in _DEFAULT_ALLOWED
        assert PolicyReleasePermission.FEDERATION_CONFLICT_VIEW in _DEFAULT_ALLOWED
        assert PolicyReleasePermission.FEDERATION_PLAN_CREATE not in _DEFAULT_ALLOWED


class TestFederationChangeEvents:
    def test_federation_change_events_exist(self) -> None:
        assert PolicyChangeEventType.FEDERATION_TARGET_CREATED.value == "policy.federation.target.created"
        assert PolicyChangeEventType.FEDERATION_TARGET_ENABLED.value == "policy.federation.target.enabled"
        assert PolicyChangeEventType.FEDERATION_TARGET_DISABLED.value == "policy.federation.target.disabled"
        assert PolicyChangeEventType.FEDERATION_PLAN_CREATED.value == "policy.federation.plan.created"
        assert PolicyChangeEventType.FEDERATION_PLAN_STARTED.value == "policy.federation.plan.started"
        assert PolicyChangeEventType.FEDERATION_PLAN_COMPLETED.value == "policy.federation.plan.completed"
        assert PolicyChangeEventType.FEDERATION_PLAN_FAILED.value == "policy.federation.plan.failed"
        assert PolicyChangeEventType.FEDERATION_PLAN_CANCELLED.value == "policy.federation.plan.cancelled"
        assert PolicyChangeEventType.FEDERATION_CONFLICT_DETECTED.value == "policy.federation.conflict.detected"


class TestAgentAppFederationProperties:
    def test_agent_app_federation_properties(self) -> None:
        app = AgentApp()
        target_store = object()
        plan_store = object()
        service = object()

        app.federated_rollout_target_store = target_store
        app.federated_rollout_plan_store = plan_store
        app.rollout_federation_service = service

        assert app.federated_rollout_target_store is target_store
        assert app.federated_rollout_plan_store is plan_store
        assert app.rollout_federation_service is service


class TestLoaderFederationWiring:
    def test_missing_federation_config_preserves_behavior(self, tmp_path) -> None:
        config_path = tmp_path / "agentapp.yaml"
        config_path.write_text(textwrap.dedent("""
        governance:
          policy_release:
            bundles:
              type: memory
            gates:
              type: memory
        """))

        app = build_app(config_path)

        assert getattr(app, "rollout_federation_service", None) is None

    def test_enabled_federation_config_wires_service(self, tmp_path) -> None:
        config_path = tmp_path / "agentapp.yaml"
        config_path.write_text(textwrap.dedent("""
        governance:
          policy_release:
            bundles:
              type: memory
            gates:
              type: memory
            rollouts:
              type: memory
            rollout_federation:
              enabled: true
              target_store:
                type: memory
              plan_store:
                type: memory
              conflict_policy:
                fail_on_error: true
                warn_on_bundle_conflict: true
        """))

        app = build_app(config_path)

        assert app.federated_rollout_target_store is not None
        assert app.federated_rollout_plan_store is not None
        assert app.rollout_federation_service is not None

    def test_enabled_sqlite_federation_config_wires_sqlite_stores(self, tmp_path) -> None:
        config_path = tmp_path / "agentapp.yaml"
        targets_db = tmp_path / "targets.db"
        plans_db = tmp_path / "plans.db"
        config_path.write_text(textwrap.dedent(f"""
        governance:
          policy_release:
            bundles:
              type: memory
            gates:
              type: memory
            rollouts:
              type: memory
            rollout_federation:
              enabled: true
              target_store:
                type: sqlite
                path: {targets_db}
              plan_store:
                type: sqlite
                path: {plans_db}
        """))

        app = build_app(config_path)

        assert type(app.federated_rollout_target_store).__name__ == "SQLiteFederatedRolloutTargetStore"
        assert type(app.federated_rollout_plan_store).__name__ == "SQLiteFederatedRolloutPlanStore"
```

- [ ] **Step 2: Run config tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_policy_rollout_federation_config.py -v
```

Expected: FAIL because config classes, permissions, change events, app properties, and loader wiring are missing.

- [ ] **Step 3: Add RBAC permissions**

Modify `agent_app/governance/policy_rbac.py`:

```python
    FEDERATION_TARGET_CREATE = "policy.federation.target.create"
    FEDERATION_TARGET_VIEW = "policy.federation.target.view"
    FEDERATION_TARGET_ENABLE = "policy.federation.target.enable"
    FEDERATION_TARGET_DISABLE = "policy.federation.target.disable"
    FEDERATION_PLAN_CREATE = "policy.federation.plan.create"
    FEDERATION_PLAN_START = "policy.federation.plan.start"
    FEDERATION_PLAN_EXECUTE = "policy.federation.plan.execute"
    FEDERATION_PLAN_CANCEL = "policy.federation.plan.cancel"
    FEDERATION_PLAN_VIEW = "policy.federation.plan.view"
    FEDERATION_CONFLICT_VIEW = "policy.federation.conflict.view"
```

Add only these three to `_DEFAULT_ALLOWED`:

```python
    PolicyReleasePermission.FEDERATION_TARGET_VIEW,
    PolicyReleasePermission.FEDERATION_PLAN_VIEW,
    PolicyReleasePermission.FEDERATION_CONFLICT_VIEW,
```

- [ ] **Step 4: Add policy change events**

Modify `agent_app/governance/policy_change_event.py` by appending these enum values:

```python
    FEDERATION_TARGET_CREATED = "policy.federation.target.created"
    FEDERATION_TARGET_ENABLED = "policy.federation.target.enabled"
    FEDERATION_TARGET_DISABLED = "policy.federation.target.disabled"
    FEDERATION_PLAN_CREATED = "policy.federation.plan.created"
    FEDERATION_PLAN_STARTED = "policy.federation.plan.started"
    FEDERATION_PLAN_COMPLETED = "policy.federation.plan.completed"
    FEDERATION_PLAN_FAILED = "policy.federation.plan.failed"
    FEDERATION_PLAN_CANCELLED = "policy.federation.plan.cancelled"
    FEDERATION_CONFLICT_DETECTED = "policy.federation.conflict.detected"
```

Update any event-count tests that assert the full enum count. Phase 45 count was 72; Phase 46 count should be 81 after adding these 9 events.

- [ ] **Step 5: Add config models**

Modify `agent_app/config/schema.py` near `RolloutHistoryConfig`:

```python
class RolloutFederationConflictPolicyConfig(BaseModel):
    """Rollout federation conflict policy configuration (Phase 46)."""

    fail_on_error: bool = Field(default=True, description="Fail federation create/start on error conflicts")
    warn_on_bundle_conflict: bool = Field(default=True, description="Report active different-bundle overlaps as warnings")


class RolloutFederationConfig(BaseModel):
    """Rollout federation configuration (Phase 46)."""

    enabled: bool = Field(default=False, description="Enable rollout federation services")
    target_store: PolicyReleaseStoreConfig | None = Field(default=None, description="Federated rollout target store")
    plan_store: PolicyReleaseStoreConfig | None = Field(default=None, description="Federated rollout plan store")
    conflict_policy: RolloutFederationConflictPolicyConfig = Field(
        default_factory=RolloutFederationConflictPolicyConfig,
        description="Federation conflict policy",
    )
```

Add this field to `PolicyReleaseConfig`:

```python
    rollout_federation: RolloutFederationConfig | None = Field(
        default=None,
        description="Rollout federation config (Phase 46)",
    )
```

- [ ] **Step 6: Add AgentApp properties**

Modify `agent_app/core/app.py` after Phase 45 rollout history properties:

```python
    @property
    def federated_rollout_target_store(self) -> Any:
        """Phase 46: Return the federated rollout target store, if configured."""
        return getattr(self, "_federated_rollout_target_store", None)

    @federated_rollout_target_store.setter
    def federated_rollout_target_store(self, value: Any) -> None:
        """Phase 46: Set the federated rollout target store."""
        self._federated_rollout_target_store = value

    @property
    def federated_rollout_plan_store(self) -> Any:
        """Phase 46: Return the federated rollout plan store, if configured."""
        return getattr(self, "_federated_rollout_plan_store", None)

    @federated_rollout_plan_store.setter
    def federated_rollout_plan_store(self, value: Any) -> None:
        """Phase 46: Set the federated rollout plan store."""
        self._federated_rollout_plan_store = value

    @property
    def rollout_federation_service(self) -> Any:
        """Phase 46: Return the rollout federation service, if configured."""
        return getattr(self, "_rollout_federation_service", None)

    @rollout_federation_service.setter
    def rollout_federation_service(self, value: Any) -> None:
        """Phase 46: Set the rollout federation service."""
        self._rollout_federation_service = value
```

- [ ] **Step 7: Wire loader**

Modify `agent_app/config/loader.py` after Phase 45 rollout history wiring:

```python
        # -- Phase 46: Rollout federation --
        try:
            fed_cfg = getattr(release_config, "rollout_federation", None) if release_config else None
            if fed_cfg is not None and fed_cfg.enabled:
                from agent_app.runtime.policy_rollout_conflict_detector import RolloutConflictDetector
                from agent_app.runtime.policy_rollout_federation_service import RolloutFederationService
                from agent_app.runtime.policy_rollout_federation_store import (
                    create_federated_rollout_plan_store,
                    create_federated_rollout_target_store,
                )

                target_store_cfg = fed_cfg.target_store
                plan_store_cfg = fed_cfg.plan_store
                federated_target_store = create_federated_rollout_target_store(
                    store_type=target_store_cfg.type if target_store_cfg else "memory",
                    db_path=target_store_cfg.path if target_store_cfg else None,
                )
                federated_plan_store = create_federated_rollout_plan_store(
                    store_type=plan_store_cfg.type if plan_store_cfg else "memory",
                    db_path=plan_store_cfg.path if plan_store_cfg else None,
                )
                conflict_detector = RolloutConflictDetector(
                    target_store=federated_target_store,
                    federation_store=federated_plan_store,
                    rollout_store=app.rollout_store,
                )
                conflict_policy = getattr(fed_cfg, "conflict_policy", None)
                federation_service = RolloutFederationService(
                    target_store=federated_target_store,
                    federation_store=federated_plan_store,
                    rollout_store=app.rollout_store,
                    rollout_service=app.rollout_service,
                    conflict_detector=conflict_detector,
                    history_recorder=getattr(app, "rollout_history_recorder", None),
                    notification_service=app.notification_service,
                    audit_logger=audit_logger,
                    event_store=event_store,
                    fail_on_error_conflicts=getattr(conflict_policy, "fail_on_error", True),
                    warn_on_bundle_conflict=getattr(conflict_policy, "warn_on_bundle_conflict", True),
                )
                app.federated_rollout_target_store = federated_target_store
                app.federated_rollout_plan_store = federated_plan_store
                app.rollout_federation_service = federation_service
        except Exception:
            pass
```

Ensure this code only runs when `app.rollout_store` and `app.rollout_service` are available. If either is missing, leave `app.rollout_federation_service` unset.

- [ ] **Step 8: Run config and existing event tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/unit/test_policy_rollout_federation_config.py \
  tests/unit/test_policy_change_event.py \
  tests/unit/test_policy_rollout_gate_config.py \
  tests/unit/test_policy_notification_config.py \
  -v
```

Expected: PASS after updating event-count assertions to 81.

- [ ] **Step 9: Commit Task 6**

```bash
git add agent_app/governance/policy_rbac.py agent_app/governance/policy_change_event.py agent_app/config/schema.py agent_app/config/loader.py agent_app/core/app.py tests/unit/test_policy_rollout_federation_config.py tests/unit/test_policy_change_event.py tests/unit/test_policy_rollout_gate_config.py tests/unit/test_policy_notification_config.py
git commit -m "feat: Phase 46 Task 6 — federation config, RBAC, loader, and events"
```

---

## Task 7: CLI federation commands

**Files:**
- Modify: `agent_app/cli.py`
- Test: `tests/unit/test_policy_rollout_federation_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/unit/test_policy_rollout_federation_cli.py` with these tests:

```python
from __future__ import annotations

import argparse
import asyncio
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType
from agent_app.governance.policy_rollout_federation import (
    FederatedRolloutPlan,
    FederatedRolloutPlanStatus,
    FederatedRolloutTarget,
    FederatedRolloutTargetExecution,
    FederatedRolloutTargetExecutionStatus,
    FederatedTargetStatus,
    FederationExecutionStrategy,
    RolloutConflict,
    RolloutConflictSeverity,
    RolloutConflictType,
)


def _run(coro):
    return asyncio.run(coro)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _target() -> FederatedRolloutTarget:
    return FederatedRolloutTarget(
        target_id="frt_test",
        name="prod-us-canary",
        tenant_id="tenant_a",
        environment="prod",
        ring_name="canary",
        region="us-east",
        created_at=_now(),
    )


def _step() -> RolloutStep:
    return RolloutStep(step_id="step_activate", step_type=RolloutStepType.ACTIVATE, environment="prod", ring_name="canary")


def _plan() -> FederatedRolloutPlan:
    return FederatedRolloutPlan(
        federation_id="frp_test",
        name="global rollout",
        bundle_id="pb_123",
        strategy=FederationExecutionStrategy.SEQUENTIAL,
        status=FederatedRolloutPlanStatus.ACTIVE,
        target_ids=["frt_test"],
        executions=[FederatedRolloutTargetExecution(
            execution_id="fre_test",
            target_id="frt_test",
            rollout_id="ro_child",
            status=FederatedRolloutTargetExecutionStatus.SUCCEEDED,
        )],
        rollout_template_steps=[_step()],
        created_by="release_manager",
        created_at=_now(),
        updated_at=_now(),
    )


def _app(service=None, target_store=None, plan_store=None):
    app = MagicMock()
    app.rollout_federation_service = service
    app.federated_rollout_target_store = target_store
    app.federated_rollout_plan_store = plan_store
    return app


class TestFederationTargetCLI:
    def test_target_create(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_target_create
        service = MagicMock()
        service.create_target = AsyncMock(return_value=_target())
        args = argparse.Namespace(
            config="agentapp.yaml",
            name="prod-us-canary",
            environment="prod",
            ring="canary",
            region="us-east",
            tenant_id="tenant_a",
            label=["tier=gold"],
            actor_id="admin",
            permissions="policy.federation.target.create",
        )

        with patch("agent_app.config.loader.build_app", return_value=_app(service=service)):
            rc = _run(_cmd_policy_federation_target_create(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "frt_test" in captured.out
        assert "prod-us-canary" in captured.out

    def test_target_list(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_target_list
        target_store = MagicMock()
        target_store.list = AsyncMock(return_value=[_target()])
        args = argparse.Namespace(config="agentapp.yaml", tenant_id=None, environment=None, ring=None, status=None)

        with patch("agent_app.config.loader.build_app", return_value=_app(target_store=target_store)):
            rc = _run(_cmd_policy_federation_target_list(args))

        assert rc == 0
        assert "prod-us-canary" in capsys.readouterr().out

    def test_target_disable_and_enable(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_target_disable, _cmd_policy_federation_target_enable
        target_store = MagicMock()
        disabled = _target().model_copy(update={"status": FederatedTargetStatus.DISABLED})
        target_store.disable = AsyncMock(return_value=disabled)
        target_store.enable = AsyncMock(return_value=_target())
        args = argparse.Namespace(config="agentapp.yaml", target_id="frt_test", actor_id="admin", permissions="policy.federation.target.disable")

        with patch("agent_app.config.loader.build_app", return_value=_app(target_store=target_store)):
            disable_rc = _run(_cmd_policy_federation_target_disable(args))
        args.permissions = "policy.federation.target.enable"
        with patch("agent_app.config.loader.build_app", return_value=_app(target_store=target_store)):
            enable_rc = _run(_cmd_policy_federation_target_enable(args))

        assert disable_rc == 0
        assert enable_rc == 0
        output = capsys.readouterr().out
        assert "disabled" in output
        assert "enabled" in output


class TestFederationPlanCLI:
    def test_plan_create_from_yaml_files(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_plan_create
        service = MagicMock()
        service.create_federated_plan = AsyncMock(return_value=_plan())
        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as targets_file, tempfile.NamedTemporaryFile("w", suffix=".yaml") as steps_file:
            yaml.safe_dump(["frt_test"], targets_file)
            targets_file.flush()
            yaml.safe_dump([{"step_id": "step_activate", "step_type": "activate", "environment": "prod", "ring_name": "canary"}], steps_file)
            steps_file.flush()
            args = argparse.Namespace(
                config="agentapp.yaml",
                name="global rollout",
                bundle_id="pb_123",
                targets_file=targets_file.name,
                steps_file=steps_file.name,
                strategy="sequential",
                actor_id="release_manager",
                permissions="policy.federation.plan.create",
                reason="release",
            )
            with patch("agent_app.config.loader.build_app", return_value=_app(service=service)):
                rc = _run(_cmd_policy_federation_plan_create(args))

        assert rc == 0
        assert "frp_test" in capsys.readouterr().out
        assert service.create_federated_plan.await_args.kwargs["target_ids"] == ["frt_test"]

    def test_plan_start_run_next_run_all_cancel(self, capsys) -> None:
        from agent_app.cli import (
            _cmd_policy_federation_plan_cancel,
            _cmd_policy_federation_plan_run_all,
            _cmd_policy_federation_plan_run_next,
            _cmd_policy_federation_plan_start,
        )
        service = MagicMock()
        service.start_federated_plan = AsyncMock(return_value=_plan())
        service.run_next_target = AsyncMock(return_value=_plan())
        service.run_all_available = AsyncMock(return_value=_plan())
        service.cancel_federated_plan = AsyncMock(return_value=_plan().model_copy(update={"status": FederatedRolloutPlanStatus.CANCELLED}))
        args = argparse.Namespace(config="agentapp.yaml", federation_id="frp_test", actor_id="release_manager", permissions="policy.federation.plan.start", reason="stop")

        with patch("agent_app.config.loader.build_app", return_value=_app(service=service)):
            assert _run(_cmd_policy_federation_plan_start(args)) == 0
        args.permissions = "policy.federation.plan.execute"
        with patch("agent_app.config.loader.build_app", return_value=_app(service=service)):
            assert _run(_cmd_policy_federation_plan_run_next(args)) == 0
        with patch("agent_app.config.loader.build_app", return_value=_app(service=service)):
            assert _run(_cmd_policy_federation_plan_run_all(args)) == 0
        args.permissions = "policy.federation.plan.cancel"
        with patch("agent_app.config.loader.build_app", return_value=_app(service=service)):
            assert _run(_cmd_policy_federation_plan_cancel(args)) == 0

        output = capsys.readouterr().out
        assert "frp_test" in output
        assert "cancelled" in output

    def test_plan_conflicts(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_plan_conflicts
        service = MagicMock()
        service.detect_conflicts = AsyncMock(return_value=[RolloutConflict(
            conflict_id="frc_test",
            conflict_type=RolloutConflictType.DISABLED_TARGET,
            severity=RolloutConflictSeverity.ERROR,
            target_id="frt_test",
            message="disabled",
        )])
        args = argparse.Namespace(config="agentapp.yaml", federation_id="frp_test")

        with patch("agent_app.config.loader.build_app", return_value=_app(service=service)):
            rc = _run(_cmd_policy_federation_plan_conflicts(args))

        assert rc == 1
        output = capsys.readouterr().out
        assert "disabled_target" in output
        assert "ERROR" in output

    def test_permission_denied_exits_nonzero(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_plan_start
        service = MagicMock()
        service.start_federated_plan = AsyncMock(side_effect=PermissionError("Permission denied"))
        args = argparse.Namespace(config="agentapp.yaml", federation_id="frp_test", actor_id="release_manager", permissions="", reason=None)

        with patch("agent_app.config.loader.build_app", return_value=_app(service=service)):
            rc = _run(_cmd_policy_federation_plan_start(args))

        assert rc != 0
        assert "Permission denied" in capsys.readouterr().err
```

- [ ] **Step 2: Run CLI tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_policy_rollout_federation_cli.py -v
```

Expected: FAIL because CLI command functions do not exist.

- [ ] **Step 3: Implement CLI helpers and command functions**

Modify `agent_app/cli.py` near existing policy rollout history commands. Add helpers:

```python
def _permissions_from_arg(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _labels_from_args(values: list[str] | None) -> dict[str, str]:
    labels: dict[str, str] = {}
    for item in values or []:
        if "=" not in item:
            raise ValueError(f"Invalid label '{item}'. Expected key=value.")
        key, val = item.split("=", 1)
        labels[key.strip()] = val.strip()
    return labels


def _federation_context(args: argparse.Namespace, actor_attr: str = "actor_id"):
    from agent_app.core.context import RunContext
    actor_id = getattr(args, actor_attr, None) or "cli"
    return RunContext(
        run_id="cli-policy-federation",
        user_id=actor_id,
        tenant_id=getattr(args, "tenant_id", None),
        permissions=_permissions_from_arg(getattr(args, "permissions", None)),
    )


def _format_federation_plan(plan) -> None:
    print(f"Federation: {plan.federation_id}")
    print(f"Name: {plan.name}")
    print(f"Bundle: {plan.bundle_id}")
    print(f"Strategy: {plan.strategy.value if hasattr(plan.strategy, 'value') else plan.strategy}")
    print(f"Status: {plan.status.value if hasattr(plan.status, 'value') else plan.status}")
    print(f"{'Execution':<18} {'Target':<18} {'Status':<12} {'Rollout':<18}")
    print("-" * 72)
    for execution in plan.executions:
        status = execution.status.value if hasattr(execution.status, "value") else str(execution.status)
        print(f"{execution.execution_id:<18} {execution.target_id:<18} {status:<12} {(execution.rollout_id or '-'):<18}")
```

Add async command functions:

```python
async def _cmd_policy_federation_target_create(args: argparse.Namespace) -> int:
    from agent_app.config.loader import build_app
    try:
        labels = _labels_from_args(getattr(args, "label", None))
        app = build_app(args.config)
        service = getattr(app, "rollout_federation_service", None)
        if service is None:
            print("Rollout federation not configured.", file=sys.stderr)
            return 1
        target = await service.create_target(
            name=args.name,
            environment=args.environment,
            tenant_id=args.tenant_id,
            ring_name=args.ring,
            region=args.region,
            labels=labels,
            actor_id=args.actor_id,
            context=_federation_context(args),
        )
        print(f"Created target {target.target_id}: {target.name} ({target.environment}/{target.ring_name or '-'})")
        return 0
    except Exception as exc:
        print(f"Error creating federation target: {exc}", file=sys.stderr)
        return 1
```

Implement matching functions:

- `_cmd_policy_federation_target_list(args)` — use `app.federated_rollout_target_store.list(...)`, parse `status` with `FederatedTargetStatus` when provided, print target table, return 0.
- `_cmd_policy_federation_target_enable(args)` — require `PolicyReleasePermission.FEDERATION_TARGET_ENABLE.value` in permissions, call store.enable, print `Target frt_x enabled.`.
- `_cmd_policy_federation_target_disable(args)` — require `PolicyReleasePermission.FEDERATION_TARGET_DISABLE.value` in permissions, call store.disable, print `Target frt_x disabled.`.
- `_cmd_policy_federation_plan_create(args)` — load YAML target list and step list; support targets file as either `['frt_a']` or `{'target_ids': ['frt_a']}`; create `RolloutStep(**item)` for each step; parse `FederationExecutionStrategy(args.strategy)`; call service; print plan with `_format_federation_plan()`.
- `_cmd_policy_federation_plan_start(args)`, `_cmd_policy_federation_plan_run_next(args)`, `_cmd_policy_federation_plan_run_all(args)`, `_cmd_policy_federation_plan_cancel(args)` — call corresponding service method and print `_format_federation_plan()`.
- `_cmd_policy_federation_plan_conflicts(args)` — call `service.detect_conflicts`, print table with severity/type/target/message. Return 1 if any `ERROR` conflict exists, else 0.

Use this YAML loader:

```python
def _load_yaml_file(path: str):
    import yaml
    with open(path, "r") as fh:
        return yaml.safe_load(fh) or []
```

- [ ] **Step 4: Wire argparse subcommands**

In the CLI parser setup, add:

```python
# policy federation
federation_parser = policy_subparsers.add_parser("federation", help="Policy rollout federation commands")
federation_sub = federation_parser.add_subparsers(dest="federation_command")

target_parser = federation_sub.add_parser("target", help="Federation target commands")
target_sub = target_parser.add_subparsers(dest="target_command")

# target create/list/enable/disable definitions set func to async command wrappers

plan_parser = federation_sub.add_parser("plan", help="Federated rollout plan commands")
plan_sub = plan_parser.add_subparsers(dest="plan_command")

# plan create/list/show/start/run-next/run-all/conflicts/cancel definitions set func to async command wrappers
```

Use argument names from the Phase 46 spec exactly: `--config`, `--name`, `--environment`, `--ring`, `--region`, `--tenant-id`, `--actor-id`, `--permissions`, `--target-id`, `--federation-id`, `--targets-file`, `--steps-file`, `--strategy`, `--bundle-id`, `--reason`.

- [ ] **Step 5: Run CLI tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_policy_rollout_federation_cli.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 7**

```bash
git add agent_app/cli.py tests/unit/test_policy_rollout_federation_cli.py
git commit -m "feat: Phase 46 Task 7 — CLI rollout federation commands"
```

---

## Task 8: Console federation pages

**Files:**
- Modify: `agent_app/console/router.py`
- Modify: `agent_app/adapters/fastapi.py`
- Create: `agent_app/console/templates/policy_federation_targets.html`
- Create: `agent_app/console/templates/policy_federation_target_detail.html`
- Create: `agent_app/console/templates/policy_federation_plans.html`
- Create: `agent_app/console/templates/policy_federation_plan_detail.html`
- Create: `agent_app/console/templates/policy_federation_plan_create.html`
- Create: `agent_app/console/templates/policy_federation_conflicts.html`
- Test: `tests/unit/test_policy_rollout_federation_console.py`

- [ ] **Step 1: Write failing console tests**

Create `tests/unit/test_policy_rollout_federation_console.py` with these tests:

```python
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_app.console.router import build_policy_console_router
from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType
from agent_app.governance.policy_rollout_federation import (
    FederatedRolloutPlan,
    FederatedRolloutPlanStatus,
    FederatedRolloutTarget,
    FederatedRolloutTargetExecution,
    FederatedRolloutTargetExecutionStatus,
    FederationExecutionStrategy,
    RolloutConflict,
    RolloutConflictSeverity,
    RolloutConflictType,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _target() -> FederatedRolloutTarget:
    return FederatedRolloutTarget(
        target_id="frt_test",
        name="prod-us-canary",
        tenant_id="tenant_a",
        environment="prod",
        ring_name="canary",
        region="us-east",
        created_at=_now(),
    )


def _step() -> RolloutStep:
    return RolloutStep(step_id="step_activate", step_type=RolloutStepType.ACTIVATE, environment="prod", ring_name="canary")


def _plan() -> FederatedRolloutPlan:
    return FederatedRolloutPlan(
        federation_id="frp_test",
        name="global rollout",
        bundle_id="pb_123",
        strategy=FederationExecutionStrategy.SEQUENTIAL,
        status=FederatedRolloutPlanStatus.ACTIVE,
        target_ids=["frt_test"],
        executions=[FederatedRolloutTargetExecution(
            execution_id="fre_test",
            target_id="frt_test",
            rollout_id="ro_child",
            status=FederatedRolloutTargetExecutionStatus.SUCCEEDED,
        )],
        rollout_template_steps=[_step()],
        created_by="release_manager",
        created_at=_now(),
        updated_at=_now(),
    )


def _client(service=None, target_store=None, plan_store=None) -> TestClient:
    app = FastAPI()
    router = build_policy_console_router(
        store=None,
        rollout_federation_service=service,
        federated_rollout_target_store=target_store,
        federated_rollout_plan_store=plan_store,
    )
    app.include_router(router, prefix="/policy-console")
    return TestClient(app)


class TestFederationConsoleTargets:
    def test_targets_page_renders(self) -> None:
        target_store = MagicMock()
        target_store.list = AsyncMock(return_value=[_target()])
        client = _client(target_store=target_store)

        response = client.get("/policy-console/federation/targets")

        assert response.status_code == 200
        assert "prod-us-canary" in response.text
        assert "tenant_a" in response.text
        assert "canary" in response.text

    def test_target_create_post_works(self) -> None:
        service = MagicMock()
        service.create_target = AsyncMock(return_value=_target())
        client = _client(service=service)

        response = client.post("/policy-console/federation/targets", data={
            "name": "prod-us-canary",
            "environment": "prod",
            "ring_name": "canary",
            "region": "us-east",
            "tenant_id": "tenant_a",
            "actor_id": "admin",
            "permissions": "policy.federation.target.create",
        })

        assert response.status_code in (200, 303)
        assert service.create_target.await_count == 1

    def test_target_disable_enable_posts_work(self) -> None:
        target_store = MagicMock()
        target_store.disable = AsyncMock(return_value=_target())
        target_store.enable = AsyncMock(return_value=_target())
        client = _client(target_store=target_store)

        disable_response = client.post("/policy-console/federation/targets/frt_test/disable", data={"actor_id": "admin", "permissions": "policy.federation.target.disable"})
        enable_response = client.post("/policy-console/federation/targets/frt_test/enable", data={"actor_id": "admin", "permissions": "policy.federation.target.enable"})

        assert disable_response.status_code in (200, 303)
        assert enable_response.status_code in (200, 303)


class TestFederationConsolePlans:
    def test_plans_page_renders(self) -> None:
        plan_store = MagicMock()
        plan_store.list = AsyncMock(return_value=[_plan()])
        client = _client(plan_store=plan_store)

        response = client.get("/policy-console/federation/plans")

        assert response.status_code == 200
        assert "global rollout" in response.text
        assert "pb_123" in response.text

    def test_plan_create_page_renders(self) -> None:
        client = _client(service=MagicMock())

        response = client.get("/policy-console/federation/plans/new")

        assert response.status_code == 200
        assert "Create Federated Rollout Plan" in response.text

    def test_plan_detail_renders(self) -> None:
        plan_store = MagicMock()
        plan_store.get = AsyncMock(return_value=_plan())
        client = _client(plan_store=plan_store)

        response = client.get("/policy-console/federation/plans/frp_test")

        assert response.status_code == 200
        assert "fre_test" in response.text
        assert "ro_child" in response.text

    def test_start_run_next_run_all_cancel_posts_work(self) -> None:
        service = MagicMock()
        service.start_federated_plan = AsyncMock(return_value=_plan())
        service.run_next_target = AsyncMock(return_value=_plan())
        service.run_all_available = AsyncMock(return_value=_plan())
        service.cancel_federated_plan = AsyncMock(return_value=_plan().model_copy(update={"status": FederatedRolloutPlanStatus.CANCELLED}))
        client = _client(service=service)
        form = {"actor_id": "release_manager", "permissions": "policy.federation.plan.start", "reason": "stop"}

        assert client.post("/policy-console/federation/plans/frp_test/start", data=form).status_code in (200, 303)
        form["permissions"] = "policy.federation.plan.execute"
        assert client.post("/policy-console/federation/plans/frp_test/run-next", data=form).status_code in (200, 303)
        assert client.post("/policy-console/federation/plans/frp_test/run-all", data=form).status_code in (200, 303)
        form["permissions"] = "policy.federation.plan.cancel"
        assert client.post("/policy-console/federation/plans/frp_test/cancel", data=form).status_code in (200, 303)

    def test_conflict_page_renders(self) -> None:
        service = MagicMock()
        service.detect_conflicts = AsyncMock(return_value=[RolloutConflict(
            conflict_id="frc_test",
            conflict_type=RolloutConflictType.DISABLED_TARGET,
            severity=RolloutConflictSeverity.ERROR,
            target_id="frt_test",
            message="Target disabled",
        )])
        client = _client(service=service)

        response = client.get("/policy-console/federation/plans/frp_test/conflicts")

        assert response.status_code == 200
        assert "disabled_target" in response.text
        assert "Target disabled" in response.text
```

- [ ] **Step 2: Run console tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_policy_rollout_federation_console.py -v
```

Expected: FAIL because router signature/routes/templates are missing.

- [ ] **Step 3: Extend console router signature and helpers**

Modify `agent_app/console/router.py`:

- Add parameters to `build_policy_console_router()`:

```python
    rollout_federation_service: Any = None,
    federated_rollout_target_store: Any = None,
    federated_rollout_plan_store: Any = None,
```

- Add helper inside router:

```python
    async def _form_dict(request: Request) -> dict[str, str]:
        form = await request.form()
        return {str(k): str(v) for k, v in form.items()}

    def _context_from_form(form: dict[str, str]):
        from agent_app.core.context import RunContext
        permissions = [p.strip() for p in form.get("permissions", "").split(",") if p.strip()]
        return RunContext(
            run_id="console-policy-federation",
            user_id=form.get("actor_id") or "console",
            tenant_id=form.get("tenant_id") or None,
            permissions=permissions,
        )
```

- [ ] **Step 4: Add routes**

Add these routes before returning the router:

```python
    @router.get("/federation/targets", response_class=HTMLResponse)
    async def federation_targets(request: Request):
        targets = []
        error = None
        if federated_rollout_target_store is None:
            error = "Rollout federation target store not configured."
        else:
            targets = await federated_rollout_target_store.list()
        return templates.TemplateResponse(request, "policy_federation_targets.html", {
            "title": title,
            "base_path": base_path,
            "targets": targets,
            "error": error,
        })

    @router.post("/federation/targets")
    async def federation_target_create(request: Request):
        form = await _form_dict(request)
        error = None
        if rollout_federation_service is None:
            error = "Rollout federation service not configured."
        else:
            try:
                await rollout_federation_service.create_target(
                    name=form.get("name", ""),
                    environment=form.get("environment", ""),
                    tenant_id=form.get("tenant_id") or None,
                    ring_name=form.get("ring_name") or None,
                    region=form.get("region") or None,
                    actor_id=form.get("actor_id") or "console",
                    context=_context_from_form(form),
                )
            except Exception as exc:
                error = str(exc)
        targets = [] if federated_rollout_target_store is None else await federated_rollout_target_store.list()
        return templates.TemplateResponse(request, "policy_federation_targets.html", {
            "title": title,
            "base_path": base_path,
            "targets": targets,
            "error": error,
        })
```

Add matching POST routes for enable/disable, GET plans list, GET plan detail, GET new plan form, POST plan create, POST start/run-next/run-all/cancel, and GET conflicts using the exact URLs from the Phase 46 spec.

Plan create form parsing:

```python
        target_ids = [item.strip() for item in form.get("target_ids", "").replace("\n", ",").split(",") if item.strip()]
        steps = [RolloutStep(
            step_id=form.get("step_id", "step_activate"),
            step_type=RolloutStepType(form.get("step_type", "activate")),
            environment=form.get("step_environment", form.get("environment", "prod")),
            ring_name=form.get("step_ring_name") or None,
        )]
```

- [ ] **Step 5: Add templates**

Create minimal Jinja2 templates with the required fields.

`agent_app/console/templates/policy_federation_targets.html` must show:

```html
<h1>Federation Targets</h1>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<form method="post" action="{{ base_path }}/federation/targets">
  <input name="name" placeholder="Name">
  <input name="environment" placeholder="Environment">
  <input name="ring_name" placeholder="Ring">
  <input name="region" placeholder="Region">
  <input name="tenant_id" placeholder="Tenant ID">
  <input name="actor_id" placeholder="Actor ID">
  <input name="permissions" placeholder="Permissions">
  <button type="submit">Create Target</button>
</form>
<table>
  <tr><th>Target ID</th><th>Name</th><th>Tenant</th><th>Environment</th><th>Ring</th><th>Region</th><th>Status</th><th>Actions</th></tr>
  {% for target in targets %}
  <tr>
    <td>{{ target.target_id }}</td>
    <td>{{ target.name }}</td>
    <td>{{ target.tenant_id or "—" }}</td>
    <td>{{ target.environment }}</td>
    <td>{{ target.ring_name or "—" }}</td>
    <td>{{ target.region or "—" }}</td>
    <td>{{ target.status.value if target.status.value is defined else target.status }}</td>
    <td>
      <form method="post" action="{{ base_path }}/federation/targets/{{ target.target_id }}/disable"><input name="actor_id"><input name="permissions"><button>Disable</button></form>
      <form method="post" action="{{ base_path }}/federation/targets/{{ target.target_id }}/enable"><input name="actor_id"><input name="permissions"><button>Enable</button></form>
    </td>
  </tr>
  {% endfor %}
</table>
```

`policy_federation_plans.html` must show plan ID, name, status, strategy, bundle ID, and execution progress (`succeeded/total`).

`policy_federation_plan_detail.html` must show target execution table with execution ID, target ID, status, child rollout ID, error, and forms for start/run-next/run-all/cancel.

`policy_federation_plan_create.html` must show inputs for name, bundle ID, target IDs, strategy, step ID, step type, step environment, step ring, actor ID, permissions, reason.

`policy_federation_conflicts.html` must show conflict ID, type, severity, target, existing rollout, existing federation, and message.

- [ ] **Step 6: Wire FastAPI adapter**

Modify `agent_app/adapters/fastapi.py` where `build_policy_console_router()` is called:

```python
rollout_federation_service=getattr(agent_app, "rollout_federation_service", None),
federated_rollout_target_store=getattr(agent_app, "federated_rollout_target_store", None),
federated_rollout_plan_store=getattr(agent_app, "federated_rollout_plan_store", None),
```

- [ ] **Step 7: Run console tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_policy_rollout_federation_console.py -v
```

Expected: PASS or SKIP only if FastAPI/Jinja2 dependencies are unavailable.

- [ ] **Step 8: Commit Task 8**

```bash
git add agent_app/console/router.py agent_app/adapters/fastapi.py agent_app/console/templates/policy_federation_targets.html agent_app/console/templates/policy_federation_target_detail.html agent_app/console/templates/policy_federation_plans.html agent_app/console/templates/policy_federation_plan_detail.html agent_app/console/templates/policy_federation_plan_create.html agent_app/console/templates/policy_federation_conflicts.html tests/unit/test_policy_rollout_federation_console.py
git commit -m "feat: Phase 46 Task 8 — console rollout federation pages"
```

---

## Task 9: Documentation and release checklist

**Files:**
- Modify: `docs/policy_release.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Create: `docs/release_checklist_phase46.md`

- [ ] **Step 1: Update `docs/policy_release.md`**

Add a `## Phase 46: Policy Rollout Federation and Conflict Detection` section containing these exact subsections:

```markdown
## Phase 46: Policy Rollout Federation and Conflict Detection

Phase 46 adds a framework-level federation layer for coordinating child rollout plans across tenants, environments, regions, rings, and target groups. It does not implement distributed locks, external deployment engines, Kubernetes/service mesh rollouts, cloud control planes, or cross-process schedulers.

### Federation targets

A `FederatedRolloutTarget` describes where a child rollout can run. Targets include `target_id`, `name`, optional `tenant_id`, required `environment`, optional `ring_name`, optional `region`, labels, status, metadata, and `created_at`. Disabled targets remain visible in stores and console pages but are ignored by automatic execution.

### Federated rollout plans

A `FederatedRolloutPlan` coordinates a policy bundle across target IDs and optional waves. The plan stores target executions, rollout template steps, strategy, status, creator, reason, and timestamps. Each target execution can reference the child `RolloutPlan` created for that target.

### Execution strategies

- `sequential`: `run_next_target()` executes the first pending target.
- `parallel`: logical parallelism; `run_next_target()` still executes one deterministic target and `run_all_available()` loops through all available targets.
- `wave`: targets execute in wave order; the next wave starts only after the current wave succeeds or skips according to `require_all_successful`.

### Conflict detection

`RolloutConflictDetector` detects duplicate targets, missing targets, disabled targets, active federated rollouts targeting the same target, active rollout plans for the same environment/ring, and active different-bundle overlaps. Duplicate, missing, disabled, same-target active federation, and environment/ring conflicts are errors. Bundle conflicts are warnings.

### CLI workflow

```bash
agentapp policy federation target create --config agentapp.yaml --name prod-us-canary --environment prod --ring canary --region us-east --tenant-id tenant_a --actor-id admin --permissions policy.federation.target.create
agentapp policy federation target list --config agentapp.yaml
agentapp policy federation plan create --config agentapp.yaml --name prod-global-rollout --bundle-id pb_123 --targets-file targets.yaml --steps-file rollout_steps.yaml --strategy wave --actor-id release_manager --permissions policy.federation.plan.create
agentapp policy federation plan conflicts --config agentapp.yaml --federation-id frp_123
agentapp policy federation plan start --config agentapp.yaml --federation-id frp_123 --actor-id release_manager --permissions policy.federation.plan.start
agentapp policy federation plan run-all --config agentapp.yaml --federation-id frp_123 --actor-id release_manager --permissions policy.federation.plan.execute
```

### Console workflow

Operators can use `/policy-console/federation/targets` to create, list, enable, and disable targets. They can use `/policy-console/federation/plans` and `/policy-console/federation/plans/new` to create and operate federated rollout plans. Plan detail pages show target execution status, child rollout IDs, and action forms. Conflict pages show conflict severity, type, target, existing rollout/federation, and message.

### Relationship to rollout history and analytics

Federation emits audit and policy change events for target and plan lifecycle operations. Child rollout plans created by the federation service use the existing `RolloutService`, so Phase 45 rollout history and analytics continue to apply to each child rollout.

### Known limitations

- Framework-level coordination only.
- No distributed lock.
- No external deployment engine.
- No Kubernetes or service mesh integration.
- No cross-process scheduler.
- Parallel strategy is logical, not concurrent execution.
- Conflict detection depends on configured stores and recorded state.
- Child rollout cancellation is deferred; federation cancellation marks federation state and pending/running executions only.
```

- [ ] **Step 2: Update changelog and README**

Add to `CHANGELOG.md`:

```markdown
## v0.34.0 — Phase 46: Policy Rollout Federation and Conflict Detection

- Added federated rollout target, plan, execution, wave, and conflict models.
- Added in-memory and SQLite federation target and plan stores.
- Added deterministic rollout conflict detection for duplicate, missing, disabled, active-target, environment/ring, and bundle conflicts.
- Added `RolloutFederationService` for target creation, federated plan creation/start/execution/cancel, child rollout creation, audit/change events, and optional notifications.
- Added federation RBAC permissions, config schema, loader wiring, and AgentApp properties.
- Added CLI commands for federation targets, plans, execution, cancellation, and conflict reports.
- Added console pages for federation targets, plans, plan details, plan creation, and conflicts.
- Documented limitations: framework-level coordination only, no distributed locks, no external deployment engine, no cross-process scheduler, and logical parallel strategy.
```

Update `README.md` roadmap:

```markdown
- [x] **Phase 46**: Policy Rollout Federation and Conflict Detection
```

- [ ] **Step 3: Create release checklist**

Create `docs/release_checklist_phase46.md`:

```markdown
# Phase 46 Release Checklist — Policy Rollout Federation and Conflict Detection

## Feature summary

Phase 46 introduces framework-level rollout federation for coordinating child rollout plans across tenants, environments, regions, rings, and target groups. It adds target and plan models, stores, conflict detection, coordinator service, CLI commands, console pages, RBAC, config, audit/change events, and optional notifications.

## Verification

- [ ] Federation model tests pass
- [ ] Federation store tests pass
- [ ] Conflict detector tests pass
- [ ] Federation service tests pass
- [ ] Federation config/loader/RBAC tests pass
- [ ] Federation CLI tests pass
- [ ] Federation console tests pass or skip only when optional dependencies are unavailable
- [ ] Existing Phase 45 rollout history tests pass
- [ ] Existing rollout/gate/promotion tests pass
- [ ] Full policy regression test subset passes

## New files

- `agent_app/governance/policy_rollout_federation.py`
- `agent_app/runtime/policy_rollout_federation_store.py`
- `agent_app/runtime/policy_rollout_conflict_detector.py`
- `agent_app/runtime/policy_rollout_federation_service.py`
- `agent_app/console/templates/policy_federation_targets.html`
- `agent_app/console/templates/policy_federation_target_detail.html`
- `agent_app/console/templates/policy_federation_plans.html`
- `agent_app/console/templates/policy_federation_plan_detail.html`
- `agent_app/console/templates/policy_federation_plan_create.html`
- `agent_app/console/templates/policy_federation_conflicts.html`
- `tests/unit/test_policy_rollout_federation_model.py`
- `tests/unit/test_policy_rollout_federation_store.py`
- `tests/unit/test_policy_rollout_conflict_detector.py`
- `tests/unit/test_policy_rollout_federation_service.py`
- `tests/unit/test_policy_rollout_federation_config.py`
- `tests/unit/test_policy_rollout_federation_cli.py`
- `tests/unit/test_policy_rollout_federation_console.py`

## Modified files

- `agent_app/governance/policy_rbac.py`
- `agent_app/governance/policy_change_event.py`
- `agent_app/config/schema.py`
- `agent_app/config/loader.py`
- `agent_app/core/app.py`
- `agent_app/cli.py`
- `agent_app/console/router.py`
- `agent_app/adapters/fastapi.py`
- `docs/policy_release.md`
- `CHANGELOG.md`
- `README.md`

## Known limitations

- Framework-level coordination only.
- No distributed locks.
- No external deployment engine.
- No Kubernetes/service mesh integration.
- No cross-process scheduler.
- Parallel strategy is logical and deterministic, not concurrent.
- Conflict detection depends on configured stores and current recorded state.
- Child rollout cancellation is deferred to a future phase.

## Phase 47 recommendation

Phase 47 should add policy rollout federation observability: federation-level timeline reconstruction, federation analytics, conflict trend reporting, target health summaries, and export helpers for federation reports. This builds on Phase 45 rollout history and Phase 46 federation state without adding external BI or distributed tracing backends.
```

- [ ] **Step 4: Commit Task 9**

```bash
git add docs/policy_release.md CHANGELOG.md README.md docs/release_checklist_phase46.md
git commit -m "docs: Phase 46 rollout federation documentation"
```

---

## Task 10: Final verification and compatibility sweep

**Files:**
- No production edits expected unless verification reveals a defect.

- [ ] **Step 1: Run Phase 46 targeted tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/unit/test_policy_rollout_federation_model.py \
  tests/unit/test_policy_rollout_federation_store.py \
  tests/unit/test_policy_rollout_conflict_detector.py \
  tests/unit/test_policy_rollout_federation_service.py \
  tests/unit/test_policy_rollout_federation_config.py \
  tests/unit/test_policy_rollout_federation_cli.py \
  tests/unit/test_policy_rollout_federation_console.py \
  -v
```

Expected: PASS, with console tests allowed to SKIP only if optional FastAPI/Jinja2 dependencies are unavailable.

- [ ] **Step 2: Run backward compatibility tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/unit/test_policy_rollout_history_model.py \
  tests/unit/test_policy_rollout_history_store.py \
  tests/unit/test_policy_rollout_history_recorder.py \
  tests/unit/test_policy_rollout_history_service.py \
  tests/unit/test_policy_rollout_history_integration.py \
  tests/unit/test_policy_rollout_history_config.py \
  tests/unit/test_policy_rollout_history_cli.py \
  tests/unit/test_policy_rollout_history_console.py \
  tests/unit/test_policy_rollout_service.py \
  tests/unit/test_policy_rollout_store.py \
  tests/unit/test_policy_rollout_gate_service.py \
  tests/unit/test_policy_rollout_gate_config.py \
  tests/unit/test_policy_notification_config.py \
  tests/unit/test_policy_change_event.py \
  -v
```

Expected: PASS.

- [ ] **Step 3: Run import boundary tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_import_boundaries.py -v
```

Expected: PASS. If this fails because `agent_app/governance/policy_rollout_federation.py` imports FastAPI, Jinja2, OpenAI SDK, or runtime modules, remove those imports. The governance module should import only Python stdlib, Pydantic, and `agent_app.governance.policy_rollout.RolloutStep`.

- [ ] **Step 4: Run broader policy regression**

Run:

```bash
.venv/bin/python -m pytest tests/unit -k "policy" -v
```

Expected: PASS. Use this exact command for the final reported policy regression result.

- [ ] **Step 5: Inspect git status and commit verification-only fixes if any**

Run:

```bash
git status --short
git log --oneline -10
```

Expected: Only the pre-existing untracked plan files remain untracked, unless new fixes were needed. If verification fixes were needed, commit them:

```bash
git add <changed-files>
git commit -m "fix: Phase 46 verification fixes"
```

- [ ] **Step 6: Final report**

Report the following to the user:

1. Modified files
2. New files
3. New tests
4. Full test result
5. Example CLI target flow
6. Example CLI federated rollout flow
7. Example conflict detection flow
8. Example console flow
9. Current limitations
10. Phase 47 recommendation

Use the actual test output from Steps 1–4. Do not claim tests pass unless the fresh command output shows exit code 0.

---

## Self-review

### Spec coverage

- Federation target model: Task 1.
- Federation plan, execution, and wave models: Task 1.
- Conflict model: Task 1.
- Memory and SQLite federation stores: Task 2.
- Conflict detector: Task 3.
- Federation coordinator service: Tasks 4 and 5.
- RBAC: Task 6.
- Config and loader: Task 6.
- CLI commands: Task 7.
- Console pages: Task 8.
- History/audit/change/notification integration: Tasks 4, 5, and 6.
- Tests: Tasks 1–8 and 10.
- Documentation: Task 9.
- Acceptance criteria and final report: Task 10.

### Placeholder scan

No section uses `TBD`, `TODO`, “implement later,” “fill in details,” or “write tests for the above” as instructions. Each task includes exact paths, test commands, expected failures, implementation guidance, verification commands, and commit commands.

### Type consistency

The plan consistently uses:

- `FederatedRolloutTargetStore`
- `FederatedRolloutPlanStore`
- `RolloutConflictDetector`
- `RolloutFederationService`
- `FederatedRolloutPlanStatus`
- `FederatedRolloutTargetExecutionStatus`
- `FederationExecutionStrategy`
- `PolicyReleasePermission.FEDERATION_*`
- `PolicyChangeEventType.FEDERATION_*`

The service method signatures match the Phase 46 specification with one extension: `create_target()` accepts `context` so RBAC can be enforced consistently. CLI and console pass that context explicitly.
