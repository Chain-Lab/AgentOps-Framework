# Phase 35: Multi-Environment Rollout Orchestration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a framework-level rollout plan system for promoting policy bundles across multiple environments in a controlled order, with step-by-step execution, gate/eval checks, approval blocking, and full auditability.

**Architecture:** Define RolloutPlan and RolloutStep models in governance, a RolloutPlanStore (Protocol + InMemory + SQLite + factory) in runtime, a RolloutService that orchestrates step execution by delegating to PolicyReleaseService for activations, ring assignments, canary evals, and ring promotions. Extend RBAC with 5 rollout permissions. Wire through config/loader. Add CLI commands and console pages.

**Tech Stack:** Python 3.12, Pydantic v2, sqlite3, asyncio, Click/argparse, FastAPI/Jinja2, hashlib (stdlib)

---

## File Structure

| New File | Purpose |
|----------|---------|
| `agent_app/governance/policy_rollout.py` | RolloutPlanStatus, RolloutStepStatus, RolloutStepType enums; RolloutStep, RolloutPlan models |
| `agent_app/runtime/policy_rollout_store.py` | RolloutPlanStore Protocol + InMemory + SQLite + factory |
| `agent_app/runtime/policy_rollout_service.py` | RolloutService — create, start, run_next_step, run_all_available, cancel |
| `agent_app/console/templates/policy_rollouts.html` | Rollouts list template |
| `agent_app/console/templates/policy_rollout_detail.html` | Rollout detail template |
| `agent_app/console/templates/policy_rollout_create.html` | Rollout create template |

| Modified File | Change |
|---------------|--------|
| `agent_app/governance/policy_change_event.py` | Add ROLLOUT_CREATED, ROLLOUT_STARTED, ROLLOUT_STEP_SUCCEEDED, ROLLOUT_COMPLETED, ROLLOUT_FAILED, ROLLOUT_CANCELLED event types |
| `agent_app/governance/policy_rbac.py` | Add ROLLOUT_CREATE, ROLLOUT_START, ROLLOUT_EXECUTE, ROLLOUT_CANCEL, ROLLOUT_VIEW permissions |
| `agent_app/config/schema.py` | Add RolloutStoreConfig, rollouts field to PolicyReleaseConfig |
| `agent_app/config/loader.py` | Wire rollout_store, rollout_service, attach to app |
| `agent_app/cli.py` | Add rollout create, list, show, start, run-next, run-all, cancel commands |
| `agent_app/console/router.py` | Add rollout routes (list, detail, create, start, run-next, run-all, cancel) |
| `agent_app/adapters/fastapi.py` | Wire rollout_store, rollout_service to console router |
| `agent_app/console/templates/base.html` | Add Rollouts nav link |
| `docs/policy_release.md` | Phase 35 section |
| `CHANGELOG.md` | v0.23.0 entry |
| `README.md` | v0.23 roadmap entry |
| `docs/release_checklist_phase35.md` | Release checklist |

---

### Task 1: RolloutPlan and RolloutStep models

**Files:**
- Create: `agent_app/governance/policy_rollout.py`
- Test: `tests/unit/test_policy_rollout.py`

Create enums:

```python
class RolloutPlanStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class RolloutStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"

class RolloutStepType(StrEnum):
    ACTIVATE = "activate"
    ASSIGN_RING = "assign_ring"
    CANARY_EVAL = "canary_eval"
    PROMOTE_RING = "promote_ring"
```

Create models:

```python
class RolloutStep(BaseModel):
    step_id: str
    step_type: RolloutStepType
    environment: str
    ring_name: str | None = None
    from_ring: str | None = None
    to_ring: str | None = None
    required_gate_status: str | None = None
    eval_suite: str | None = None
    requires_approval: bool = False
    require_previous_step: str | None = None
    status: RolloutStepStatus = RolloutStepStatus.PENDING
    activation_id: str | None = None
    assignment_id: str | None = None
    approval_id: str | None = None
    error: dict[str, Any] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

class RolloutPlan(BaseModel):
    rollout_id: str
    name: str
    bundle_id: str
    status: RolloutPlanStatus = RolloutPlanStatus.DRAFT
    steps: list[RolloutStep]
    created_by: str
    reason: str | None = None
    created_at: datetime
    updated_at: datetime
```

Add model validators:

```python
@model_validator(mode="after")
def _validate_steps(self) -> RolloutPlan:
    if not self.steps:
        raise ValueError("Rollout plan must have at least one step")
    # Check duplicate step_ids
    seen: set[str] = set()
    for step in self.steps:
        if step.step_id in seen:
            raise ValueError(f"Duplicate step_id: {step.step_id}")
        seen.add(step.step_id)
    # Check require_previous_step references exist
    step_ids = {s.step_id for s in self.steps}
    for step in self.steps:
        if step.require_previous_step is not None:
            if step.require_previous_step not in step_ids:
                raise ValueError(
                    f"Step '{step.step_id}' requires previous step "
                    f"'{step.require_previous_step}' which does not exist"
                )
    return self
```

Tests (~7): valid plan creation, rollout_id ro_ prefix, default status draft, empty steps raises, duplicate step_id raises, invalid require_previous_step raises, timezone-aware datetimes.

---

### Task 2: RolloutPlanStore

**Files:**
- Create: `agent_app/runtime/policy_rollout_store.py`
- Test: `tests/unit/test_policy_rollout_store.py`

Protocol:

```python
@runtime_checkable
class RolloutPlanStore(Protocol):
    async def create(self, plan: RolloutPlan) -> RolloutPlan: ...
    async def get(self, rollout_id: str) -> RolloutPlan | None: ...
    async def update(self, plan: RolloutPlan) -> RolloutPlan: ...
    async def list(
        self,
        status: RolloutPlanStatus | None = None,
        bundle_id: str | None = None,
    ) -> list[RolloutPlan]: ...
```

InMemory: dict[str, RolloutPlan] storage, list filters by status/bundle_id.

SQLite:

```sql
CREATE TABLE IF NOT EXISTS policy_rollout_plans (
    rollout_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    status TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    created_by TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Factory:

```python
def create_rollout_plan_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> RolloutPlanStore:
```

Tests (~7): create/get, update, list by status, list by bundle_id, list all, SQLite persistence, factory function.

---

### Task 3: PolicyChangeEventType extensions

**Files:**
- Modify: `agent_app/governance/policy_change_event.py`
- Test: append to `tests/unit/test_policy_change_event.py`

Add 6 new event types:

```python
ROLLOUT_CREATED = "policy.rollout.created"
ROLLOUT_STARTED = "policy.rollout.started"
ROLLOUT_STEP_SUCCEEDED = "policy.rollout.step_succeeded"
ROLLOUT_COMPLETED = "policy.rollout.completed"
ROLLOUT_FAILED = "policy.rollout.failed"
ROLLOUT_CANCELLED = "policy.rollout.cancelled"
```

Tests (~2): all event types valid, new rollout event types exist.

---

### Task 4: RBAC extensions

**Files:**
- Modify: `agent_app/governance/policy_rbac.py`
- Test: append to `tests/unit/test_policy_rbac.py`

Add 5 permissions:

```python
ROLLOUT_CREATE = "policy.rollout.create"
ROLLOUT_START = "policy.rollout.start"
ROLLOUT_EXECUTE = "policy.rollout.execute"
ROLLOUT_CANCEL = "policy.rollout.cancel"
ROLLOUT_VIEW = "policy.rollout.view"
```

Add ROLLOUT_VIEW to _DEFAULT_ALLOWED.

Tests (~3): new permissions exist, ROLLOUT_VIEW is default-allowed, ROLLOUT_CREATE requires explicit permission.

---

### Task 5: RolloutService

**Files:**
- Create: `agent_app/runtime/policy_rollout_service.py`
- Test: `tests/unit/test_policy_rollout_service.py`

Constructor:

```python
class RolloutService:
    def __init__(
        self,
        rollout_store: Any,
        release_service: Any,
        eval_runner: Any | None = None,
        audit_logger: Any | None = None,
        event_store: Any | None = None,
        permission_checker: Any | None = None,
    ) -> None:
        ...
```

Methods:

```python
async def create_plan(
    self,
    name: str,
    bundle_id: str,
    steps: list[RolloutStep],
    created_by: str,
    context: RunContext,
    reason: str | None = None,
) -> RolloutPlan:
    # 1. Check ROLLOUT_CREATE permission
    # 2. Generate rollout_id with ro_ prefix
    # 3. Validate bundle exists via release_service
    # 4. Create RolloutPlan with DRAFT status
    # 5. Store via rollout_store.create()
    # 6. Emit ROLLOUT_CREATED change event
    # 7. Write policy.rollout.created audit event
    # 8. Return plan

async def start_plan(
    self,
    rollout_id: str,
    started_by: str,
    context: RunContext,
) -> RolloutPlan:
    # 1. Check ROLLOUT_START permission
    # 2. Get plan, verify status is DRAFT
    # 3. Set status to ACTIVE, first step to RUNNING if no require_previous_step
    # 4. Emit ROLLOUT_STARTED change event
    # 5. Write audit
    # 6. Return updated plan

async def run_next_step(
    self,
    rollout_id: str,
    actor_id: str,
    context: RunContext,
) -> RolloutPlan:
    # 1. Check ROLLOUT_EXECUTE permission
    # 2. Get plan, verify status is ACTIVE
    # 3. Find next PENDING step that has previous step SUCCEEDED (or no requirement)
    # 4. If no runnable step, return plan unchanged
    # 5. Execute step based on step_type (see step execution below)
    # 6. Update plan status (COMPLETED if all succeeded, FAILED if any failed)
    # 7. Write audit and change events
    # 8. Return updated plan

async def run_all_available(
    self,
    rollout_id: str,
    actor_id: str,
    context: RunContext,
) -> RolloutPlan:
    # Loop run_next_step until no more runnable steps or a step fails

async def cancel_plan(
    self,
    rollout_id: str,
    cancelled_by: str,
    context: RunContext,
    reason: str | None = None,
) -> RolloutPlan:
    # 1. Check ROLLOUT_CANCEL permission
    # 2. Get plan, verify status is ACTIVE or DRAFT
    # 3. Set status to CANCELLED
    # 4. Emit ROLLOUT_CANCELLED change event
    # 5. Write audit
    # 6. Return updated plan
```

Step execution in `_execute_step()`:

```python
async def _execute_step(self, plan, step, actor_id, context) -> RolloutStep:
    # Set step to RUNNING, started_at=now
    step = step.model_copy(update={"status": RolloutStepStatus.RUNNING, "started_at": datetime.now(timezone.utc)})
    
    try:
        if step.requires_approval:
            # MVP: mark BLOCKED with error {"type": "approval_required"}
            return step.model_copy(update={
                "status": RolloutStepStatus.BLOCKED,
                "error": {"type": "approval_required", "message": "Step requires approval before execution"},
                "completed_at": datetime.now(timezone.utc),
            })
        
        result_step = None
        if step.step_type == RolloutStepType.ACTIVATE:
            result_step = await self._execute_activate(plan, step, actor_id, context)
        elif step.step_type == RolloutStepType.ASSIGN_RING:
            result_step = await self._execute_assign_ring(plan, step, actor_id, context)
        elif step.step_type == RolloutStepType.CANARY_EVAL:
            result_step = await self._execute_canary_eval(plan, step, actor_id, context)
        elif step.step_type == RolloutStepType.PROMOTE_RING:
            result_step = await self._execute_promote_ring(plan, step, actor_id, context)
        
        if result_step is None:
            raise ValueError(f"Unknown step type: {step.step_type}")
        
        return result_step
    except Exception as e:
        return step.model_copy(update={
            "status": RolloutStepStatus.FAILED,
            "error": {"type": "execution_error", "message": str(e)},
            "completed_at": datetime.now(timezone.utc),
        })
```

ACTIVATE execution:

```python
async def _execute_activate(self, plan, step, actor_id, context) -> RolloutStep:
    # 1. Verify bundle exists
    # 2. If required_gate_status, check gate status
    # 3. Execute promotion/activation via release_service
    # 4. If ring_name provided, assign to ring
    # 5. Mark SUCCEEDED with activation_id (and assignment_id if ring assigned)
```

ASSIGN_RING execution:

```python
async def _execute_assign_ring(self, plan, step, actor_id, context) -> RolloutStep:
    # 1. Find or resolve activation for environment
    # 2. Assign to ring via release_service.assign_activation_to_ring()
    # 3. Mark SUCCEEDED with assignment_id
```

CANARY_EVAL execution:

```python
async def _execute_canary_eval(self, plan, step, actor_id, context) -> RolloutStep:
    # 1. If eval_runner available, run eval suite
    # 2. If eval passes, mark SUCCEEDED
    # 3. If eval fails, mark FAILED with error
    # 4. If no eval_runner, mark FAILED with error {"type": "no_eval_runner"}
```

PROMOTE_RING execution:

```python
async def _execute_promote_ring(self, plan, step, actor_id, context) -> RolloutStep:
    # 1. Call release_service.promote_canary_to_stable() or similar
    # 2. Mark SUCCEEDED with assignment_id
```

Previous step blocking in `_find_next_runnable_step()`:

```python
def _find_next_runnable_step(self, plan) -> RolloutStep | None:
    for step in plan.steps:
        if step.status != RolloutStepStatus.PENDING:
            continue
        if step.require_previous_step is not None:
            prev = next((s for s in plan.steps if s.step_id == step.require_previous_step), None)
            if prev is None or prev.status != RolloutStepStatus.SUCCEEDED:
                # Don't mark BLOCKED here — just skip
                continue
        return step
    return None
```

Tests (~14): create requires permission, start requires permission, cancel requires permission, run_next requires permission, create plan stores and returns, start plan sets ACTIVE, previous step blocking, activate step succeeds, canary eval succeeds, canary eval failure marks failed, promote ring succeeds, run_all stops on failure, plan completes when all succeeded, approval-required marks BLOCKED, audit/change events written.

---

### Task 6: Config schema and loader

**Files:**
- Modify: `agent_app/config/schema.py`
- Modify: `agent_app/config/loader.py`
- Test: `tests/unit/test_policy_rollout_config.py`

Schema additions:

```python
class RolloutStoreConfig(BaseModel):
    type: str = "memory"
    path: str | None = None
```

Add to PolicyReleaseConfig:

```python
rollouts: RolloutStoreConfig | None = None
```

Loader changes:

```python
# In build_app(), after existing wiring:
if release_config.rollouts is not None:
    rollout_store = create_rollout_plan_store(
        store_type=release_config.rollouts.type,
        db_path=release_config.rollouts.path,
    )
    rollout_service = RolloutService(
        rollout_store=rollout_store,
        release_service=release_service,
        audit_logger=audit_logger,
        event_store=event_store,
        permission_checker=permission_checker,
    )
    app._rollout_store = rollout_store
    app._rollout_service = rollout_service
```

Tests (~5): rollout store config, backward compat (no rollouts config), rollout service wired, rollout store wired, memory default.

---

### Task 7: CLI rollout commands

**Files:**
- Modify: `agent_app/cli.py`
- Test: `tests/unit/test_policy_rollout_cli.py`

Add sub-parser group `rollout` under `policy`:

Commands:
- `rollout create --config <path> --name <name> --bundle-id <id> --steps-file <yaml> --actor-id <id> --permissions <list> --reason <text>`
- `rollout list --config <path> --json`
- `rollout show --config <path> --rollout-id <id> --json`
- `rollout start --config <path> --rollout-id <id> --actor-id <id> --permissions <list>`
- `rollout run-next --config <path> --rollout-id <id> --actor-id <id> --permissions <list>`
- `rollout run-all --config <path> --rollout-id <id> --actor-id <id> --permissions <list>`
- `rollout cancel --config <path> --rollout-id <id> --actor-id <id> --permissions <list> --reason <text>`

Steps file format (YAML):

```yaml
steps:
  - step_id: dev_activate
    step_type: activate
    environment: dev
    ring_name: stable
  - step_id: staging_eval
    step_type: canary_eval
    environment: staging
    ring_name: stable
    eval_suite: examples/customer_support/evals/customer_support.yaml
    require_previous_step: dev_activate
```

Handler functions follow `_cmd_policy_rollout_<action>(args)` pattern.

Each handler: `build_app(args.config)` → access `app._rollout_service` → call method → print JSON output.

CLI output includes: rollout_id, name, bundle_id, status, step statuses, latest error.

Tests (~8): rollout create from steps file, rollout list, rollout show, rollout start, rollout run-next, rollout run-all, rollout cancel, permission denied exits non-zero.

---

### Task 8: Console rollout pages

**Files:**
- Modify: `agent_app/console/router.py`
- Create: `agent_app/console/templates/policy_rollouts.html`
- Create: `agent_app/console/templates/policy_rollout_detail.html`
- Create: `agent_app/console/templates/policy_rollout_create.html`
- Modify: `agent_app/adapters/fastapi.py`
- Modify: `agent_app/console/templates/base.html`
- Test: `tests/unit/test_policy_rollout_console.py`

Routes:
- `GET /policy-console/rollouts` — list page
- `GET /policy-console/rollouts/{rollout_id}` — detail page
- `GET /policy-console/rollouts/new` — create form
- `POST /policy-console/rollouts` — create rollout
- `POST /policy-console/rollouts/{rollout_id}/start` — start
- `POST /policy-console/rollouts/{rollout_id}/run-next` — run next step
- `POST /policy-console/rollouts/{rollout_id}/run-all` — run all available
- `POST /policy-console/rollouts/{rollout_id}/cancel` — cancel

Update `build_policy_console_router` signature to accept `rollout_store`, `rollout_service`.

Update `_mount_policy_console` in fastapi.py to pass new stores.

Add "Rollouts" nav link to base.html.

Tests (~7): list page renders, detail page renders, create page renders, create POST works, start POST works, run-next POST works, cancel POST works.

---

### Task 9: Documentation + final verification

**Files:**
- Modify: `docs/policy_release.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Create: `docs/release_checklist_phase35.md`

Add Phase 35 section covering: rollout plans and steps, multi-environment flow, step execution behavior, blocked/approval steps, CLI examples, console workflows, failure behavior, known limitations, design decisions.

Known limitations:
- No background scheduler
- No external CI/CD integration
- No deployment platform integration
- Step approval is MVP/block-only
- No automatic production metric rollback
- No distributed execution lock
- Rollout execution is local command/API driven

Run full test suite, verify Phase 31/32/33/34 tests pass, verify import boundaries.
