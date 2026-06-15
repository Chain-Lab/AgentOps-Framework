# Phase 36: Rollout Approval Workflow

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn rollout step approval from a blocked placeholder into a complete approval lifecycle that can unblock or fail rollout execution.

**Architecture:** Define RolloutStepApproval model in governance, a RolloutStepApprovalStore (Protocol + InMemory + SQLite + factory) in runtime, extend RolloutService with approval request/approve/reject APIs, update run_next_step to auto-create/reuse pending approvals, add RBAC permissions, CLI commands, and console pages.

**Tech Stack:** Python 3.12, Pydantic v2, sqlite3, asyncio, Click/argparse, FastAPI/Jinja2

---

## File Structure

| New File | Purpose |
|----------|---------|
| `agent_app/governance/policy_rollout_approval.py` | RolloutStepApprovalStatus enum, RolloutStepApproval model |
| `agent_app/runtime/policy_rollout_approval_store.py` | RolloutStepApprovalStore Protocol + InMemory + SQLite + factory |
| `agent_app/console/templates/policy_rollout_approvals.html` | Approvals list template |
| `agent_app/console/templates/policy_rollout_approval_detail.html` | Approval detail template |

| Modified File | Change |
|---------------|--------|
| `agent_app/governance/policy_change_event.py` | Add ROLLOUT_APPROVAL_REQUESTED, ROLLOUT_APPROVAL_APPROVED, ROLLOUT_APPROVAL_REJECTED event types |
| `agent_app/governance/policy_rbac.py` | Add ROLLOUT_APPROVAL_REQUEST, ROLLOUT_APPROVAL_APPROVE, ROLLOUT_APPROVAL_REJECT, ROLLOUT_APPROVAL_VIEW permissions |
| `agent_app/runtime/policy_rollout_service.py` | Add approval_store, approval_require_reason params; add request_step_approval, approve_step, reject_step, list_step_approvals methods; update _execute_step and run_next_step |
| `agent_app/config/schema.py` | Add RolloutApprovalConfig, approvals field to RolloutStoreConfig |
| `agent_app/config/loader.py` | Wire approval_store into RolloutService |
| `agent_app/core/app.py` | Add rollout_approval_store property |
| `agent_app/cli.py` | Add approval list/request/approve/reject CLI commands |
| `agent_app/console/router.py` | Add approval routes |
| `agent_app/adapters/fastapi.py` | Wire approval_store to console router |
| `agent_app/console/templates/policy_rollout_detail.html` | Show approval state for blocked steps |
| `docs/policy_release.md` | Phase 36 section |
| `CHANGELOG.md` | v0.24.0 entry |
| `README.md` | Phase 36 roadmap entry |
| `docs/release_checklist_phase36.md` | Release checklist |

---

### Task 1: RolloutStepApproval model

**Files:**
- Create: `agent_app/governance/policy_rollout_approval.py`
- Test: `tests/unit/test_policy_rollout_approval.py`

Create enum:

```python
class RolloutStepApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
```

Create model:

```python
class RolloutStepApproval(BaseModel):
    approval_id: str  # rsa_ prefix
    rollout_id: str
    step_id: str
    bundle_id: str
    environment: str
    ring_name: str | None = None
    requested_by: str
    requested_reason: str | None = None
    status: RolloutStepApprovalStatus = RolloutStepApprovalStatus.PENDING
    resolved_by: str | None = None
    resolved_reason: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None
```

Requirements:
- Use timezone-aware datetimes
- `approval_id` uses `rsa_` prefix
- `status` defaults to PENDING
- Do NOT import FastAPI/Jinja2/Starlette

Tests (~5): valid model, default status pending, approval_id prefix, timezone-aware timestamps, ring_name optional.

---

### Task 2: RolloutStepApprovalStore

**Files:**
- Create: `agent_app/runtime/policy_rollout_approval_store.py`
- Test: `tests/unit/test_policy_rollout_approval_store.py`

Protocol:

```python
@runtime_checkable
class RolloutStepApprovalStore(Protocol):
    async def create(self, approval: RolloutStepApproval) -> RolloutStepApproval: ...
    async def get(self, approval_id: str) -> RolloutStepApproval | None: ...
    async def get_pending_for_step(self, rollout_id: str, step_id: str) -> RolloutStepApproval | None: ...
    async def approve(self, approval_id: str, approved_by: str, reason: str | None = None) -> RolloutStepApproval: ...
    async def reject(self, approval_id: str, rejected_by: str, reason: str | None = None) -> RolloutStepApproval: ...
    async def cancel_for_step(self, rollout_id: str, step_id: str, cancelled_by: str, reason: str | None = None) -> RolloutStepApproval | None: ...
    async def list(self, status: RolloutStepApprovalStatus | None = None, rollout_id: str | None = None) -> list[RolloutStepApproval]: ...
```

InMemory: dict[str, RolloutStepApproval] storage.

SQLite:

```sql
CREATE TABLE IF NOT EXISTS policy_rollout_step_approvals (
    approval_id TEXT PRIMARY KEY,
    rollout_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    ring_name TEXT,
    requested_by TEXT NOT NULL,
    requested_reason TEXT,
    status TEXT NOT NULL,
    resolved_by TEXT,
    resolved_reason TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_rsa_status ON policy_rollout_step_approvals(status);
CREATE INDEX IF NOT EXISTS idx_rsa_rollout ON policy_rollout_step_approvals(rollout_id);
```

Factory:

```python
def create_rollout_step_approval_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> RolloutStepApprovalStore:
```

Behavior:
- Creating duplicate pending approval for same rollout_id + step_id: return existing pending approval
- Already resolved approvals cannot be approved/rejected again (raise ValueError)
- `cancel_for_step()` only cancels pending approval (sets status to CANCELLED)
- SQLite persists across instances

Tests (~10): create/get, duplicate pending returns existing, approve, reject, cannot approve twice, cannot reject approved, cancel_for_step, list by status, list by rollout_id, SQLite persistence, factory function.

---

### Task 3: PolicyChangeEventType approval extensions

**Files:**
- Modify: `agent_app/governance/policy_change_event.py`
- Test: append to `tests/unit/test_policy_change_event.py`

Add 3 new event types:

```python
ROLLOUT_APPROVAL_REQUESTED = "policy.rollout.approval.requested"
ROLLOUT_APPROVAL_APPROVED = "policy.rollout.approval.approved"
ROLLOUT_APPROVAL_REJECTED = "policy.rollout.approval.rejected"
```

Tests (~2): new approval event types exist, all event types valid.

---

### Task 4: RBAC approval permissions

**Files:**
- Modify: `agent_app/governance/policy_rbac.py`
- Test: append to `tests/unit/test_policy_rbac.py`

Add 4 permissions:

```python
ROLLOUT_APPROVAL_REQUEST = "policy.rollout.approval.request"
ROLLOUT_APPROVAL_APPROVE = "policy.rollout.approval.approve"
ROLLOUT_APPROVAL_REJECT = "policy.rollout.approval.reject"
ROLLOUT_APPROVAL_VIEW = "policy.rollout.approval.view"
```

Add ROLLOUT_APPROVAL_VIEW to _DEFAULT_ALLOWED.

Tests (~3): new permissions exist, ROLLOUT_APPROVAL_VIEW is default-allowed, ROLLOUT_APPROVAL_REQUEST requires explicit permission.

---

### Task 5: RolloutService approval APIs + run_next_step interaction

**Files:**
- Modify: `agent_app/runtime/policy_rollout_service.py`
- Test: `tests/unit/test_policy_rollout_service.py`

Constructor additions:

```python
def __init__(
    self,
    rollout_store: Any,
    release_service: Any,
    eval_runner: Any | None = None,
    audit_logger: Any | None = None,
    event_store: Any | None = None,
    permission_checker: Any | None = None,
    approval_store: Any | None = None,  # NEW
    approval_require_reason: bool = False,  # NEW
) -> None:
```

New public methods:

```python
async def request_step_approval(
    self, rollout_id: str, step_id: str, requested_by: str, context: RunContext, reason: str | None = None,
) -> RolloutStepApproval:
    # 1. Check ROLLOUT_APPROVAL_REQUEST permission (or ROLLOUT_EXECUTE)
    # 2. Plan must exist
    # 3. Step must exist
    # 4. Step must have requires_approval=True
    # 5. Step must be BLOCKED or PENDING
    # 6. If approval_store is None, raise RuntimeError
    # 7. Create pending approval or return existing pending approval
    # 8. Set step status to BLOCKED, set step.approval_id
    # 9. Persist plan
    # 10. Emit audit "policy.rollout.approval.requested" and change event ROLLOUT_APPROVAL_REQUESTED
    # 11. Return approval

async def approve_step(
    self, approval_id: str, approved_by: str, context: RunContext, reason: str | None = None,
) -> RolloutStepApproval:
    # 1. Check ROLLOUT_APPROVAL_APPROVE permission
    # 2. Approval must be pending (else ValueError)
    # 3. If approval_require_reason and reason is None, raise ValueError
    # 4. Mark approval approved, set resolved_by, resolved_reason, resolved_at
    # 5. Find rollout plan and step via rollout_id + step_id
    # 6. If step is BLOCKED, set it back to PENDING, clear error
    # 7. Persist plan
    # 8. Emit audit "policy.rollout.approval.approved" and change event ROLLOUT_APPROVAL_APPROVED
    # 9. Return approval

async def reject_step(
    self, approval_id: str, rejected_by: str, context: RunContext, reason: str | None = None,
) -> RolloutStepApproval:
    # 1. Check ROLLOUT_APPROVAL_REJECT permission
    # 2. Approval must be pending (else ValueError)
    # 3. If approval_require_reason and reason is None, raise ValueError
    # 4. Mark approval rejected, set resolved_by, resolved_reason, resolved_at
    # 5. Find rollout plan and step via rollout_id + step_id
    # 6. Set step status to FAILED, set error {"type": "approval_rejected", "message": "..."}
    # 7. If plan is ACTIVE, set plan status to FAILED
    # 8. Persist plan
    # 9. Emit audit "policy.rollout.approval.rejected" and change event ROLLOUT_APPROVAL_REJECTED
    # 10. Return approval

async def list_step_approvals(
    self, status: RolloutStepApprovalStatus | None = None, rollout_id: str | None = None,
) -> list[RolloutStepApproval]:
    # 1. Check ROLLOUT_APPROVAL_VIEW permission
    # 2. If approval_store is None, return []
    # 3. Return approval_store.list(status, rollout_id)
```

Update `_execute_step`:

```python
async def _execute_step(self, plan, step, actor_id, context) -> RolloutStep:
    # ... existing RUNNING status set ...
    try:
        if step.requires_approval:
            if self._approval_store is not None:
                # Full approval workflow: create/reuse pending approval
                existing = await self._approval_store.get_pending_for_step(plan.rollout_id, step.step_id)
                if existing is not None and existing.status == RolloutStepApprovalStatus.APPROVED:
                    # Already approved — proceed with execution
                    pass  # fall through to step type execution
                elif existing is not None and existing.status == RolloutStepApprovalStatus.REJECTED:
                    return step.model_copy(update={
                        "status": RolloutStepStatus.FAILED,
                        "error": {"type": "approval_rejected", "message": "Approval was rejected"},
                        "completed_at": datetime.now(timezone.utc),
                    })
                else:
                    # Create or reuse pending approval
                    if existing is None:
                        approval = RolloutStepApproval(
                            approval_id=f"rsa_{uuid.uuid4().hex[:12]}",
                            rollout_id=plan.rollout_id,
                            step_id=step.step_id,
                            bundle_id=plan.bundle_id,
                            environment=step.environment,
                            ring_name=step.ring_name,
                            requested_by=actor_id,
                            status=RolloutStepApprovalStatus.PENDING,
                            created_at=datetime.now(timezone.utc),
                        )
                        approval = await self._approval_store.create(approval)
                    else:
                        approval = existing
                    return step.model_copy(update={
                        "status": RolloutStepStatus.BLOCKED,
                        "approval_id": approval.approval_id,
                        "error": {"type": "approval_required", "message": "Step requires approval before execution", "approval_id": approval.approval_id},
                        "completed_at": datetime.now(timezone.utc),
                    })
            else:
                # Legacy MVP: block without approval store
                return step.model_copy(update={
                    "status": RolloutStepStatus.BLOCKED,
                    "error": {"type": "approval_required", "message": "Step requires approval before execution"},
                    "completed_at": datetime.now(timezone.utc),
                })
        # ... existing step type execution ...
```

Tests (~15): request approval requires permission, cannot request for non-approval step, request sets step blocked and approval_id, run_next_step auto-creates approval for requires_approval step, approved step becomes pending again, run_next_step executes approved step, rejected approval marks step and plan failed, reason required enforced, audit events emitted, change events emitted, list_step_approvals works, approve already resolved raises, reject already resolved raises, cancel_for_step on approval, backward compat (no approval store still blocks).

---

### Task 6: Config schema and loader for approval store

**Files:**
- Modify: `agent_app/config/schema.py`
- Modify: `agent_app/config/loader.py`
- Modify: `agent_app/core/app.py`
- Test: `tests/unit/test_policy_rollout_approval_config.py`

Schema additions:

```python
class RolloutApprovalConfig(BaseModel):
    """Configuration for rollout step approval store (Phase 36)."""
    type: Literal["memory", "sqlite"] = "memory"
    path: str | None = None
    require_reason: bool = False
```

Add to RolloutStoreConfig:

```python
approvals: RolloutApprovalConfig | None = None
```

Loader changes in build_app():

```python
# Inside the rollout config block:
approval_store = None
approval_require_reason = False
if release_config.rollouts is not None and release_config.rollouts.approvals is not None:
    from agent_app.runtime.policy_rollout_approval_store import create_rollout_step_approval_store
    apv_cfg = release_config.rollouts.approvals
    approval_store = create_rollout_step_approval_store(
        store_type=apv_cfg.type,
        db_path=apv_cfg.path,
    )
    approval_require_reason = apv_cfg.require_reason
    rollout_service = RolloutService(
        rollout_store=rollout_store,
        release_service=release_service,
        audit_logger=audit_logger,
        event_store=event_store,
        permission_checker=permission_checker,
        approval_store=approval_store,
        approval_require_reason=approval_require_reason,
    )
    app._rollout_approval_store = approval_store
```

App changes:

```python
# In AgentApp.__init__:
self._rollout_approval_store: Any = None

# In AgentApp properties:
@property
def rollout_approval_store(self) -> Any:
    """Phase 36: Return the rollout approval store, if configured."""
    return self._rollout_approval_store
```

Tests (~6): approval store config, backward compat (no approvals config), approval store wired, require_reason wired, rollout service gets approval params, memory default.

---

### Task 7: CLI approval commands

**Files:**
- Modify: `agent_app/cli.py`
- Test: `tests/unit/test_policy_rollout_approval_cli.py`

Add approval sub-commands under rollout:

```bash
agentapp policy rollout approval list --config <path> --rollout-id <id> --status <status> --json
agentapp policy rollout approval request --config <path> --rollout-id <id> --step-id <id> --actor-id <id> --permissions <list> --reason <text>
agentapp policy rollout approval approve --config <path> --approval-id <id> --actor-id <id> --permissions <list> --reason <text>
agentapp policy rollout approval reject --config <path> --approval-id <id> --actor-id <id> --permissions <list> --reason <text>
```

Handler functions follow `_cmd_policy_rollout_approval_<action>(args)` pattern.

Each handler: `build_app(args.config)` → access `app._rollout_service` → call method → print JSON output.

Helper: `_get_rollout_service(args)` already exists — reuse it.

Failure modes:
- Missing permission exits non-zero
- Missing reason exits non-zero when required
- Approval not found exits non-zero
- Already resolved approval exits non-zero
- Step does not require approval exits non-zero
- Rollout not found exits non-zero

Tests (~7): approval list, approval request, approval approve, approval reject, permission denied exits non-zero, missing required reason exits non-zero, already resolved exits non-zero.

---

### Task 8: Console approval pages

**Files:**
- Modify: `agent_app/console/router.py`
- Create: `agent_app/console/templates/policy_rollout_approvals.html`
- Create: `agent_app/console/templates/policy_rollout_approval_detail.html`
- Modify: `agent_app/adapters/fastapi.py`
- Modify: `agent_app/console/templates/policy_rollout_detail.html` (show approval state for blocked steps)
- Test: `tests/unit/test_policy_rollout_approval_console.py`

Routes:

```http
GET /policy-console/rollout-approvals — list approvals
GET /policy-console/rollout-approvals/{approval_id} — detail page
POST /policy-console/rollouts/{rollout_id}/steps/{step_id}/request-approval — request
POST /policy-console/rollout-approvals/{approval_id}/approve — approve
POST /policy-console/rollout-approvals/{approval_id}/reject — reject
```

Update `build_policy_console_router` signature to accept `approval_store`.

Update `_mount_policy_console` in fastapi.py to pass approval_store.

Update rollout detail page to show approval_id and approval status for blocked steps.

Tests (~7): approvals list page renders, approval detail page renders, request approval POST works, approve POST works, reject POST works, rollout detail shows approval state, error renders clearly.

---

### Task 9: Documentation + final verification

**Files:**
- Modify: `docs/policy_release.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Create: `docs/release_checklist_phase36.md`

Add Phase 36 section covering: rollout step approvals, approval lifecycle, CLI examples, console workflows, approval reason policy, blocked → approved → pending → executed flow, rejection failure behavior, known limitations.

Known limitations:
- No multi-party approval
- No separation-of-duties enforcement
- No external identity integration
- No notification system
- No approval expiration
- No cryptographic signing
- Step approval is rollout-local only

Run full test suite, verify Phase 35 backward compatibility, verify import boundaries.
