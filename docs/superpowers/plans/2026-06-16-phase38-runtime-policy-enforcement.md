# Phase 38: Runtime Policy Enforcement Points and Unified Approval Governance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend approval policy enforcement into runtime execution paths so that approval and separation-of-duties controls are enforced consistently across tool execution, runtime approval resume, and rollout approvals.

**Architecture:** Introduce Policy Enforcement Points (PEPs) via a `RuntimePolicyRule` model and `RuntimePolicyEvaluator`. A `PolicyEnforcementService` wraps the evaluator with audit logging. `ToolExecutor.execute()` is extended to check runtime policies before execution. The resume path re-checks policies. All new modules live in governance/ and runtime/ layers — no FastAPI/OpenAI imports.

**Tech Stack:** Python 3.11+, Pydantic v2, SQLite, FastAPI + Jinja2 (console), pytest + pytest-asyncio

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `agent_app/governance/policy_enforcement.py` | PolicyActionType, PolicyDecisionStatus, PolicyEnforcementDecision models |
| `agent_app/governance/runtime_policy.py` | RuntimePolicyRuleStatus, RuntimePolicyEffect, RuntimePolicyRule models |
| `agent_app/runtime/runtime_policy_store.py` | RuntimePolicyStore Protocol, InMemory, SQLite, factory |
| `agent_app/runtime/runtime_policy_evaluator.py` | RuntimePolicyEvaluationRequest, RuntimePolicyEvaluator |
| `agent_app/runtime/policy_enforcement_service.py` | PolicyEnforcementService (evaluator + audit) |
| `tests/unit/test_runtime_policy.py` | Tests for models, store, evaluator, enforcement service |
| `tests/unit/test_runtime_policy_executor_integration.py` | ToolExecutor + resume integration tests |
| `tests/unit/test_runtime_policy_cli.py` | CLI runtime policy command tests |
| `tests/unit/test_runtime_policy_console.py` | Console runtime policy page tests |

### Modified Files
| File | Changes |
|------|---------|
| `agent_app/runtime/tool_executor.py` | Add policy enforcement check before execution |
| `agent_app/runtime/approval_resume_service.py` | Re-check policy on resume |
| `agent_app/governance/policy_change_event.py` | Add runtime policy event types |
| `agent_app/config/schema.py` | Add RuntimePolicyConfig, RuntimePolicyRuleConfig |
| `agent_app/config/loader.py` | Wire runtime policy store/evaluator/service |
| `agent_app/cli.py` | Add runtime policy CLI commands |
| `agent_app/console/router.py` | Add runtime policy console routes |
| `docs/policy_release.md` | Phase 38 documentation |
| `CHANGELOG.md` | v0.26.0 entry |
| `README.md` | Phase 38 roadmap |
| `docs/release_checklist_phase38.md` | Release checklist |

---

### Task 1: Policy Enforcement and Runtime Policy Models

**Files:**
- Create: `agent_app/governance/policy_enforcement.py`
- Create: `agent_app/governance/runtime_policy.py`
- Test: `tests/unit/test_runtime_policy.py`

- [ ] **Step 1: Write failing tests for models**

Create `tests/unit/test_runtime_policy.py` with tests for:
- `PolicyActionType` enum values
- `PolicyDecisionStatus` enum values
- `PolicyEnforcementDecision` — valid decision, ped_ prefix, timezone-aware created_at
- `RuntimePolicyRuleStatus` enum values
- `RuntimePolicyEffect` enum values
- `RuntimePolicyRule` — valid rule, rpr_ prefix, default ENABLED, with/without approval_policy

- [ ] **Step 2: Run tests to verify they fail (RED)**

- [ ] **Step 3: Implement models**

Create `agent_app/governance/policy_enforcement.py`:
- `PolicyActionType` enum: TOOL_EXECUTE, TOOL_RESUME, APPROVAL_APPROVE, APPROVAL_REJECT, ROLLOUT_STEP_EXECUTE, POLICY_PROMOTION_EXECUTE
- `PolicyDecisionStatus` enum: ALLOWED, DENIED, APPROVAL_REQUIRED
- `PolicyEnforcementDecision` model with decision_id (ped_ prefix), status, action_type, subject, reason, required_permissions, required_roles, approval_policy, metadata, created_at (timezone-aware)

Create `agent_app/governance/runtime_policy.py`:
- `RuntimePolicyRuleStatus` enum: ENABLED, DISABLED
- `RuntimePolicyEffect` enum: ALLOW, DENY, REQUIRE_APPROVAL
- `RuntimePolicyRule` model with rule_id (rpr_ prefix), name, action_type, effect, status, tool_name, risk_level, required_permissions, required_roles, approval_policy, reason, metadata

- [ ] **Step 4: Run tests to verify they pass (GREEN)**

- [ ] **Step 5: Commit**

```bash
git add agent_app/governance/policy_enforcement.py agent_app/governance/runtime_policy.py tests/unit/test_runtime_policy.py
git commit -m "feat: Phase 38 Task 1 — policy enforcement and runtime policy models"
```

---

### Task 2: RuntimePolicyStore

**Files:**
- Create: `agent_app/runtime/runtime_policy_store.py`
- Test: `tests/unit/test_runtime_policy.py` (extend)

- [ ] **Step 1: Write failing tests for store**

Append `TestInMemoryRuntimePolicyStore` and `TestSQLiteRuntimePolicyStore`:
- create / get
- list by action_type
- list by status
- enable / disable
- SQLite persists across instances

- [ ] **Step 2: Implement store**

`RuntimePolicyStore` Protocol with create, get, list, enable, disable.
`InMemoryRuntimePolicyStore` with dict-based storage.
`SQLiteRuntimePolicyStore` with JSON columns for required_permissions, required_roles, approval_policy, metadata.
`create_runtime_policy_store()` factory.

- [ ] **Step 3: Run tests (GREEN)**

- [ ] **Step 4: Commit**

```bash
git add agent_app/runtime/runtime_policy_store.py tests/unit/test_runtime_policy.py
git commit -m "feat: Phase 38 Task 2 — RuntimePolicyStore InMemory + SQLite"
```

---

### Task 3: RuntimePolicyEvaluator and PolicyEnforcementService

**Files:**
- Create: `agent_app/runtime/runtime_policy_evaluator.py`
- Create: `agent_app/runtime/policy_enforcement_service.py`
- Test: `tests/unit/test_runtime_policy.py` (extend)

- [ ] **Step 1: Write failing tests for evaluator and service**

Append `TestRuntimePolicyEvaluator`:
- no matching rule → ALLOWED
- deny rule → DENIED
- require approval rule → APPROVAL_REQUIRED
- allow rule with permissions → ALLOWED (satisfied) / DENIED (missing)
- role restriction — allows/denies
- tool_name matching
- risk_level matching
- most restrictive wins (deny > require_approval > allow)

Append `TestPolicyEnforcementService`:
- allowed decision audited
- denied decision audited
- approval_required decision audited

- [ ] **Step 2: Implement evaluator**

`RuntimePolicyEvaluationRequest` model with action_type, subject, tool_name, risk_level, context, metadata.
`RuntimePolicyEvaluator` with evaluate() method:
1. Load enabled rules matching action_type
2. Match by tool_name/risk_level if set
3. No match → ALLOWED
4. Multiple matches → most restrictive wins
5. DENY → DENIED
6. REQUIRE_APPROVAL → check permissions/roles; if missing → DENIED, if present → APPROVAL_REQUIRED
7. ALLOW → check permissions/roles; if missing → DENIED, if present → ALLOWED

- [ ] **Step 3: Implement enforcement service**

`PolicyEnforcementService` with enforce() method:
1. Call evaluator
2. Write audit event for every decision
3. Return decision

- [ ] **Step 4: Run tests (GREEN)**

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/runtime_policy_evaluator.py agent_app/runtime/policy_enforcement_service.py tests/unit/test_runtime_policy.py
git commit -m "feat: Phase 38 Task 3 — RuntimePolicyEvaluator and PolicyEnforcementService"
```

---

### Task 4: ToolExecutor Integration

**Files:**
- Modify: `agent_app/runtime/tool_executor.py`
- Test: `tests/unit/test_runtime_policy_executor_integration.py` (new)

- [ ] **Step 1: Write failing integration tests**

Create `tests/unit/test_runtime_policy_executor_integration.py`:
- existing low-risk tool still executes (no enforcement service)
- existing ToolSpec.requires_approval still interrupts
- runtime deny blocks tool execution
- runtime require approval interrupts tool execution
- runtime require approval includes decision metadata in result
- no duplicate approval when both ToolSpec and runtime policy require approval
- permission denial still results in FAILED

- [ ] **Step 2: Modify ToolExecutor**

Add `policy_enforcement_service` parameter to `__init__`.
In `execute()`, after permission check and before requires_approval check:
1. If enforcement service is set, build `RuntimePolicyEvaluationRequest`
2. Call `enforce()` to get decision
3. If DENIED → return FAILED with policy denial reason
4. If APPROVAL_REQUIRED → set flag that policy requires approval (but check ToolSpec.requires_approval first to avoid duplicates)
5. If ALLOWED → continue as normal
6. Add `policy_decision_id` to approval request metadata if policy triggered it

- [ ] **Step 3: Run tests (GREEN)**

- [ ] **Step 4: Run existing ToolExecutor tests for backward compat**

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/tool_executor.py tests/unit/test_runtime_policy_executor_integration.py
git commit -m "feat: Phase 38 Task 4 — ToolExecutor runtime policy enforcement"
```

---

### Task 5: Resume Integration + Runtime Approval Extension

**Files:**
- Modify: `agent_app/runtime/approval_resume_service.py`
- Modify: `agent_app/governance/approval.py` (extend ApprovalRequest with optional fields)
- Test: `tests/unit/test_runtime_policy_executor_integration.py` (extend)

- [ ] **Step 1: Write failing resume tests**

Append to integration test file:
- resume allowed under unchanged policy
- resume blocked if policy changed to deny
- resume re-interrupts if policy now requires approval
- enforcement decision audited on resume

- [ ] **Step 2: Extend ApprovalRequest with optional fields**

Add to `agent_app/governance/approval.py` ApprovalRequest:
- `policy: RolloutApprovalPolicy | None = None`
- `decisions: list[RolloutApprovalDecision] = Field(default_factory=list)`
- `expires_at: datetime | None = None`
- `subject: str | None = None`
- `action_type: str | None = None`

All with defaults — backward compatible.

- [ ] **Step 3: Modify ApprovalResumeService**

Add `policy_enforcement_service` parameter.
In resume flow, before executing:
1. If enforcement service is set, build evaluation request for TOOL_RESUME
2. Check policy decision
3. If DENIED → return failed result
4. If APPROVAL_REQUIRED → return interrupted with new approval
5. If ALLOWED → continue as before

- [ ] **Step 4: Run tests (GREEN)**

- [ ] **Step 5: Run existing approval resume tests for backward compat**

- [ ] **Step 6: Commit**

```bash
git add agent_app/runtime/approval_resume_service.py agent_app/governance/approval.py tests/unit/test_runtime_policy_executor_integration.py
git commit -m "feat: Phase 38 Task 5 — resume enforcement + runtime approval extension"
```

---

### Task 6: Config, Loader, RBAC, and Change Events

**Files:**
- Modify: `agent_app/config/schema.py`
- Modify: `agent_app/config/loader.py`
- Modify: `agent_app/governance/policy_change_event.py`
- Test: `tests/unit/test_runtime_policy.py` (extend with config tests)

- [ ] **Step 1: Write failing config tests**

- [ ] **Step 2: Add config schema**

`RuntimePolicyRuleConfig` — inline rule config from YAML
`RuntimePoliciesConfig` — type, path, rules list

- [ ] **Step 3: Update loader**

Wire runtime_policy_store, evaluator, enforcement_service into AgentApp/AppRunner/ToolExecutor.
Load inline rules into store on startup.

- [ ] **Step 4: Add RBAC permissions**

Add `RUNTIME_POLICY_CREATE/VIEW/ENABLE/DISABLE/EVALUATE` to permissions.

- [ ] **Step 5: Add change event types**

Add runtime policy event types to PolicyChangeEventType.

- [ ] **Step 6: Run tests (GREEN)**

- [ ] **Step 7: Commit**

```bash
git add agent_app/config/schema.py agent_app/config/loader.py agent_app/governance/policy_change_event.py tests/unit/test_runtime_policy.py
git commit -m "feat: Phase 38 Task 6 — config, loader, RBAC, change events"
```

---

### Task 7: CLI Commands

**Files:**
- Modify: `agent_app/cli.py`
- Test: `tests/unit/test_runtime_policy_cli.py` (new)

- [ ] **Step 1: Write failing CLI tests**

- [ ] **Step 2: Implement CLI commands**

`agentapp policy runtime list`
`agentapp policy runtime create`
`agentapp policy runtime enable`
`agentapp policy runtime disable`
`agentapp policy runtime evaluate`

- [ ] **Step 3: Run tests (GREEN)**

- [ ] **Step 4: Commit**

```bash
git add agent_app/cli.py tests/unit/test_runtime_policy_cli.py
git commit -m "feat: Phase 38 Task 7 — CLI runtime policy commands"
```

---

### Task 8: Console Pages

**Files:**
- Modify: `agent_app/console/router.py`
- Create: `agent_app/console/templates/policy_runtime_rules.html`
- Create: `agent_app/console/templates/policy_runtime_rule_detail.html`
- Create: `agent_app/console/templates/policy_runtime_evaluate.html`
- Test: `tests/unit/test_runtime_policy_console.py` (new)

- [ ] **Step 1: Write failing console tests**

- [ ] **Step 2: Implement console routes and templates**

Rule list, detail, create, enable/disable, evaluate.

- [ ] **Step 3: Run tests (GREEN)**

- [ ] **Step 4: Commit**

```bash
git add agent_app/console/router.py agent_app/console/templates/policy_runtime_*.html tests/unit/test_runtime_policy_console.py
git commit -m "feat: Phase 38 Task 8 — console runtime policy pages"
```

---

### Task 9: Documentation and Final Verification

**Files:**
- Modify: `docs/policy_release.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Create: `docs/release_checklist_phase38.md`

- [ ] **Step 1: Update documentation**

Phase 38 section in policy_release.md, CHANGELOG v0.26.0, README roadmap, release checklist.

- [ ] **Step 2: Run full test suite**

```bash
.venv/bin/python -m pytest tests/unit/ -q
```
Expected: 0 failures

- [ ] **Step 3: Commit**

```bash
git add docs/policy_release.md CHANGELOG.md README.md docs/release_checklist_phase38.md
git commit -m "docs: Phase 38 documentation — runtime policy enforcement points"
```

---

## Self-Review Checklist

- [x] Spec coverage: All 16 sections of the Phase 38 spec are addressed
- [x] Placeholder scan: No TBD/TODO placeholders
- [x] Type consistency: PolicyActionType, RuntimePolicyRule, RuntimePolicyEvaluator names used consistently
- [x] Backward compatibility: All new fields/params have defaults; existing ToolSpec.requires_approval and Phase 37 approvals work unchanged
- [x] Import boundaries: No FastAPI/Jinja2/OpenAI in governance/runtime modules
