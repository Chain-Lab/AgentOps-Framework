# Phase 37: Separation of Duties and Multi-Approver Approval Policies — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Phase 36 single-approval lifecycle with configurable approval policies supporting multi-approver quorum, separation-of-duties checks, role/permission constraints, approval expiration, and policy-aware CLI/console workflows.

**Architecture:** Add `RolloutApprovalPolicy` and `RolloutApprovalDecision` models to the governance layer. Add a `RolloutApprovalPolicyEvaluator` to the runtime layer that validates decisions against policy constraints. Extend the approval store with `add_decision()` and `expire_pending()` methods. Modify `RolloutService.approve_step/reject_step` to create decisions rather than directly mutating status. CLI and console gain `--roles` support and quorum-aware display. All changes maintain backward compatibility with Phase 36 single-approval behavior.

**Tech Stack:** Python 3.11+, Pydantic v2, SQLite, FastAPI + Jinja2 (console), pytest + pytest-asyncio

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `agent_app/runtime/policy_rollout_approval_policy.py` | `RolloutApprovalPolicyEvaluator` — validates decisions, evaluates status |
| `tests/unit/test_policy_rollout_approval_policy.py` | Tests for policy model, decision model, evaluator |
| `tests/unit/test_policy_rollout_approval_quorum.py` | Integration tests for quorum workflow (service + store + evaluator) |

### Modified Files
| File | Changes |
|------|---------|
| `agent_app/governance/policy_rollout_approval.py` | Add `RolloutApprovalPolicyType`, `RolloutApprovalPolicy`, `RolloutApprovalDecisionType`, `RolloutApprovalDecision`; extend `RolloutStepApprovalStatus` with EXPIRED; extend `RolloutStepApproval` with `policy`, `decisions`, `expires_at` |
| `agent_app/runtime/policy_rollout_approval_store.py` | Add `add_decision()`, `expire_pending()` to Protocol; implement in InMemory + SQLite; SQLite schema migration for `policy_json`, `decisions_json`, `expires_at` columns |
| `agent_app/runtime/policy_rollout_service.py` | Modify `request_step_approval`, `approve_step`, `reject_step` for policy-aware decision flow; add `expire_approvals` method; resolve policy from step/rollout/config |
| `agent_app/config/schema.py` | Add `RolloutApprovalPolicyConfig` to `RolloutApprovalConfig` |
| `agent_app/config/loader.py` | Wire approval policy from config into service |
| `agent_app/cli.py` | Add `--roles` to approve/reject commands; update `_approval_to_dict` for decisions/policy; add `expire` subcommand; update `_build_context` to pass roles |
| `agent_app/console/router.py` | Update approval detail/list templates for quorum status; add roles to approve/reject forms |
| `agent_app/console/templates/rollout_approval_detail.html` | Show decisions, policy, expires_at, required approvals |
| `docs/policy_release.md` | Phase 37 documentation |
| `CHANGELOG.md` | v0.25.0 entry |
| `README.md` | Phase 37 roadmap |
| `docs/release_checklist_phase37.md` | Release checklist |

---

### Task 1: Approval Policy and Decision Models

**Files:**
- Modify: `agent_app/governance/policy_rollout_approval.py`
- Test: `tests/unit/test_policy_rollout_approval_policy.py`

- [ ] **Step 1: Write failing tests for policy and decision models**

```python
# tests/unit/test_policy_rollout_approval_policy.py
"""Phase 37: Tests for approval policy model, decision model, and evaluator."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_rollout_approval import (
    RolloutApprovalDecision,
    RolloutApprovalDecisionType,
    RolloutApprovalPolicy,
    RolloutApprovalPolicyType,
    RolloutStepApproval,
    RolloutStepApprovalStatus,
)


class TestRolloutApprovalPolicyModel:
    def test_default_single_policy(self):
        """Default policy is SINGLE with required_approvals=1."""
        policy = RolloutApprovalPolicy()
        assert policy.policy_type == RolloutApprovalPolicyType.SINGLE
        assert policy.required_approvals == 1

    def test_quorum_policy(self):
        """Quorum policy with required_approvals=2."""
        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        assert policy.policy_type == RolloutApprovalPolicyType.QUORUM
        assert policy.required_approvals == 2

    def test_single_policy_required_approvals_must_be_1(self):
        """SINGLE policy with required_approvals != 1 raises ValueError."""
        with pytest.raises(ValueError, match="required_approvals"):
            RolloutApprovalPolicy(
                policy_type=RolloutApprovalPolicyType.SINGLE,
                required_approvals=2,
            )

    def test_required_approvals_must_be_positive(self):
        """required_approvals < 1 raises ValueError."""
        with pytest.raises(ValueError, match="required_approvals"):
            RolloutApprovalPolicy(required_approvals=0)

    def test_expires_after_seconds_must_be_positive(self):
        """expires_after_seconds must be positive if provided."""
        with pytest.raises(ValueError, match="expires_after_seconds"):
            RolloutApprovalPolicy(expires_after_seconds=0)

    def test_expires_after_seconds_none_is_valid(self):
        """None for expires_after_seconds is valid (no expiration)."""
        policy = RolloutApprovalPolicy(expires_after_seconds=None)
        assert policy.expires_after_seconds is None

    def test_separation_of_duties_defaults(self):
        """Default separation-of-duties settings."""
        policy = RolloutApprovalPolicy()
        assert policy.prohibit_requester_approval is True
        assert policy.prohibit_creator_approval is False
        assert policy.prohibit_step_actor_approval is False

    def test_empty_roles_and_permissions_means_no_restriction(self):
        """Empty allowed_approver_roles and permissions means no restriction."""
        policy = RolloutApprovalPolicy()
        assert policy.allowed_approver_roles == []
        assert policy.allowed_approver_permissions == []


class TestRolloutApprovalDecisionModel:
    def test_approve_decision(self):
        """Valid approve decision."""
        decision = RolloutApprovalDecision(
            decision_id="rsd_001",
            approval_id="rsa_001",
            decision_type=RolloutApprovalDecisionType.APPROVE,
            decided_by="reviewer1",
            reason="Looks good",
            roles=["release_reviewer"],
            permissions=["policy.rollout.approval.approve"],
            created_at=datetime.now(timezone.utc),
        )
        assert decision.decision_type == RolloutApprovalDecisionType.APPROVE
        assert decision.decision_id.startswith("rsd_")

    def test_reject_decision(self):
        """Valid reject decision."""
        decision = RolloutApprovalDecision(
            decision_id="rsd_002",
            approval_id="rsa_001",
            decision_type=RolloutApprovalDecisionType.REJECT,
            decided_by="reviewer2",
            reason="Not ready",
            created_at=datetime.now(timezone.utc),
        )
        assert decision.decision_type == RolloutApprovalDecisionType.REJECT

    def test_decision_id_prefix(self):
        """decision_id must start with rsd_ prefix."""
        with pytest.raises(ValueError, match="rsd_"):
            RolloutApprovalDecision(
                decision_id="bad_001",
                approval_id="rsa_001",
                decision_type=RolloutApprovalDecisionType.APPROVE,
                decided_by="reviewer1",
                created_at=datetime.now(timezone.utc),
            )

    def test_timezone_aware_created_at(self):
        """created_at must be timezone-aware."""
        with pytest.raises(ValueError):
            RolloutApprovalDecision(
                decision_id="rsd_003",
                approval_id="rsa_001",
                decision_type=RolloutApprovalDecisionType.APPROVE,
                decided_by="reviewer1",
                created_at=datetime.now(),  # naive datetime
            )


class TestRolloutStepApprovalExtended:
    def test_approval_with_policy_and_decisions(self):
        """RolloutStepApproval can carry policy and decisions."""
        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        approval = RolloutStepApproval(
            approval_id="rsa_001",
            rollout_id="ro_001",
            step_id="s1",
            bundle_id="pb_001",
            environment="prod",
            requested_by="deployer",
            status=RolloutStepApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            policy=policy,
            decisions=[],
        )
        assert approval.policy.policy_type == RolloutApprovalPolicyType.QUORUM
        assert approval.decisions == []

    def test_approval_with_expires_at(self):
        """RolloutStepApproval can carry expires_at."""
        expires = datetime.now(timezone.utc)
        approval = RolloutStepApproval(
            approval_id="rsa_002",
            rollout_id="ro_001",
            step_id="s1",
            bundle_id="pb_001",
            environment="prod",
            requested_by="deployer",
            status=RolloutStepApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            expires_at=expires,
        )
        assert approval.expires_at is not None

    def test_expired_status_exists(self):
        """EXPIRED status is available on RolloutStepApprovalStatus."""
        assert hasattr(RolloutStepApprovalStatus, "EXPIRED")
        assert RolloutStepApprovalStatus.EXPIRED.value == "expired"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_approval_policy.py -v`
Expected: FAIL — new models not yet defined

- [ ] **Step 3: Implement models in policy_rollout_approval.py**

Add to `agent_app/governance/policy_rollout_approval.py`:

```python
"""Rollout step approval model — tracks approval requests for rollout steps requiring human sign-off.

Phase 36: Single-approval lifecycle.
Phase 37: Multi-approver quorum, separation-of-duties, expiration.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class RolloutStepApprovalStatus(str, Enum):
    """Status of a rollout step approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class RolloutApprovalPolicyType(str, Enum):
    """Type of approval policy."""

    SINGLE = "single"
    QUORUM = "quorum"


class RolloutApprovalPolicy(BaseModel):
    """Approval policy — determines how many approvals are needed and who can approve."""

    policy_type: RolloutApprovalPolicyType = RolloutApprovalPolicyType.SINGLE
    required_approvals: int = 1
    allowed_approver_permissions: list[str] = Field(default_factory=list)
    allowed_approver_roles: list[str] = Field(default_factory=list)
    prohibit_requester_approval: bool = True
    prohibit_creator_approval: bool = False
    prohibit_step_actor_approval: bool = False
    expires_after_seconds: int | None = None
    require_reason: bool = False

    @field_validator("required_approvals")
    @classmethod
    def _validate_required_approvals(cls, v: int) -> int:
        if v < 1:
            raise ValueError("required_approvals must be >= 1")
        return v

    @field_validator("expires_after_seconds")
    @classmethod
    def _validate_expires_after_seconds(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("expires_after_seconds must be positive if provided")
        return v

    @field_validator("required_approvals")
    @classmethod
    def _validate_single_approvals(cls, v: int, info) -> int:
        # Only enforce when policy_type is SINGLE
        if info.data.get("policy_type") == RolloutApprovalPolicyType.SINGLE and v != 1:
            raise ValueError("SINGLE policy requires required_approvals=1")
        return v


class RolloutApprovalDecisionType(str, Enum):
    """Type of approval decision."""

    APPROVE = "approve"
    REJECT = "reject"


class RolloutApprovalDecision(BaseModel):
    """A single approval decision (approve or reject) by one actor."""

    decision_id: str  # rsd_ prefix
    approval_id: str
    decision_type: RolloutApprovalDecisionType
    decided_by: str
    reason: str | None = None
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    created_at: datetime

    @field_validator("decision_id")
    @classmethod
    def _validate_decision_id_prefix(cls, v: str) -> str:
        if not v.startswith("rsd_"):
            raise ValueError("decision_id must use rsd_ prefix")
        return v

    @field_validator("created_at")
    @classmethod
    def _validate_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return v


class RolloutStepApproval(BaseModel):
    """Tracks an approval request for a rollout step that requires human sign-off."""

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
    # Phase 37: Multi-approver quorum + policy
    policy: RolloutApprovalPolicy = Field(default_factory=RolloutApprovalPolicy)
    decisions: list[RolloutApprovalDecision] = Field(default_factory=list)
    expires_at: datetime | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_approval_policy.py::TestRolloutApprovalPolicyModel tests/unit/test_policy_rollout_approval_policy.py::TestRolloutApprovalDecisionModel tests/unit/test_policy_rollout_approval_policy.py::TestRolloutStepApprovalExtended -v`
Expected: PASS

- [ ] **Step 5: Run existing Phase 36 approval tests for backward compatibility**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_approval.py tests/unit/test_policy_rollout_approval_store.py -v`
Expected: PASS (defaults should maintain old behavior)

- [ ] **Step 6: Commit**

```bash
git add agent_app/governance/policy_rollout_approval.py tests/unit/test_policy_rollout_approval_policy.py
git commit -m "feat: Phase 37 Task 1 — approval policy and decision models"
```

---

### Task 2: Policy Evaluator

**Files:**
- Create: `agent_app/runtime/policy_rollout_approval_policy.py`
- Test: `tests/unit/test_policy_rollout_approval_policy.py` (append)

- [ ] **Step 1: Write failing tests for the evaluator**

Append to `tests/unit/test_policy_rollout_approval_policy.py`:

```python
from agent_app.runtime.policy_rollout_approval_policy import (
    ApprovalPolicyError,
    RolloutApprovalPolicyEvaluator,
)
from agent_app.governance.policy_rollout import RolloutPlan, RolloutStep, RolloutStepType


class TestRolloutApprovalPolicyEvaluator:
    def _make_approval(self, **kwargs) -> RolloutStepApproval:
        defaults = dict(
            approval_id="rsa_eval01",
            rollout_id="ro_eval",
            step_id="s1",
            bundle_id="pb_001",
            environment="prod",
            requested_by="deployer",
            status=RolloutStepApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        defaults.update(kwargs)
        return RolloutStepApproval(**defaults)

    def _make_decision(self, **kwargs) -> RolloutApprovalDecision:
        defaults = dict(
            decision_id="rsd_eval01",
            approval_id="rsa_eval01",
            decision_type=RolloutApprovalDecisionType.APPROVE,
            decided_by="reviewer1",
            created_at=datetime.now(timezone.utc),
        )
        defaults.update(kwargs)
        return RolloutApprovalDecision(**defaults)

    def test_requester_self_approval_denied(self):
        """prohibit_requester_approval=True blocks requester from approving."""
        approval = self._make_approval(
            policy=RolloutApprovalPolicy(prohibit_requester_approval=True),
        )
        decision = self._make_decision(decided_by="deployer")  # same as requested_by
        evaluator = RolloutApprovalPolicyEvaluator()
        with pytest.raises(ApprovalPolicyError, match="requester"):
            evaluator.validate_decision(approval, decision)

    def test_creator_self_approval_denied(self):
        """prohibit_creator_approval=True blocks rollout creator from approving."""
        approval = self._make_approval(
            policy=RolloutApprovalPolicy(prohibit_creator_approval=True),
        )
        rollout = RolloutPlan(
            rollout_id="ro_eval", name="test", bundle_id="pb_001",
            steps=[RolloutStep(step_id="s1", step_type=RolloutStepType.ACTIVATE, environment="prod")],
            created_by="creator1", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        )
        decision = self._make_decision(decided_by="creator1")
        evaluator = RolloutApprovalPolicyEvaluator()
        with pytest.raises(ApprovalPolicyError, match="creator"):
            evaluator.validate_decision(approval, decision, rollout=rollout)

    def test_missing_role_denied(self):
        """Actor without required role is denied."""
        approval = self._make_approval(
            policy=RolloutApprovalPolicy(
                allowed_approver_roles=["release_reviewer"],
            ),
        )
        decision = self._make_decision(decided_by="dev1", roles=["developer"])
        evaluator = RolloutApprovalPolicyEvaluator()
        with pytest.raises(ApprovalPolicyError, match="role"):
            evaluator.validate_decision(approval, decision)

    def test_missing_permission_denied(self):
        """Actor without required permission is denied."""
        approval = self._make_approval(
            policy=RolloutApprovalPolicy(
                allowed_approver_permissions=["policy.rollout.approval.approve"],
            ),
        )
        decision = self._make_decision(decided_by="dev1", permissions=["policy.read"])
        evaluator = RolloutApprovalPolicyEvaluator()
        with pytest.raises(ApprovalPolicyError, match="permission"):
            evaluator.validate_decision(approval, decision)

    def test_reason_required_denied(self):
        """require_reason=True blocks decision without reason."""
        approval = self._make_approval(
            policy=RolloutApprovalPolicy(require_reason=True),
        )
        decision = self._make_decision(reason=None)
        evaluator = RolloutApprovalPolicyEvaluator()
        with pytest.raises(ApprovalPolicyError, match="reason"):
            evaluator.validate_decision(approval, decision)

    def test_duplicate_actor_denied(self):
        """Actor who already decided cannot decide again."""
        existing = self._make_decision(decided_by="reviewer1")
        approval = self._make_approval(decisions=[existing])
        decision = self._make_decision(decided_by="reviewer1", decision_id="rsd_dup01")
        evaluator = RolloutApprovalPolicyEvaluator()
        with pytest.raises(ApprovalPolicyError, match="already"):
            evaluator.validate_decision(approval, decision)

    def test_already_resolved_denied(self):
        """Cannot decide on already-resolved approval."""
        approval = self._make_approval(status=RolloutStepApprovalStatus.APPROVED)
        decision = self._make_decision()
        evaluator = RolloutApprovalPolicyEvaluator()
        with pytest.raises(ApprovalPolicyError, match="pending"):
            evaluator.validate_decision(approval, decision)

    def test_expired_denied(self):
        """Cannot decide on expired approval."""
        approval = self._make_approval(
            expires_at=datetime.now(timezone.utc),  # already expired
        )
        decision = self._make_decision()
        evaluator = RolloutApprovalPolicyEvaluator()
        with pytest.raises(ApprovalPolicyError, match="expired"):
            evaluator.validate_decision(approval, decision)

    def test_valid_approve_passes(self):
        """Valid approve decision passes validation."""
        approval = self._make_approval()
        decision = self._make_decision(
            decided_by="reviewer1",
            roles=["release_reviewer"],
            permissions=["policy.rollout.approval.approve"],
        )
        evaluator = RolloutApprovalPolicyEvaluator()
        evaluator.validate_decision(approval, decision)  # no exception

    def test_evaluate_status_reject_immediate(self):
        """Reject decision makes status REJECTED."""
        approval = self._make_approval(
            policy=RolloutApprovalPolicy(
                policy_type=RolloutApprovalPolicyType.QUORUM,
                required_approvals=2,
            ),
        )
        reject = self._make_decision(
            decision_type=RolloutApprovalDecisionType.REJECT,
        )
        approval.decisions.append(reject)
        evaluator = RolloutApprovalPolicyEvaluator()
        status = evaluator.evaluate_status(approval)
        assert status == RolloutStepApprovalStatus.REJECTED

    def test_evaluate_status_quorum_pending(self):
        """Quorum not yet reached means PENDING."""
        approval = self._make_approval(
            policy=RolloutApprovalPolicy(
                policy_type=RolloutApprovalPolicyType.QUORUM,
                required_approvals=2,
            ),
        )
        approve1 = self._make_decision(decided_by="r1")
        approval.decisions.append(approve1)
        evaluator = RolloutApprovalPolicyEvaluator()
        status = evaluator.evaluate_status(approval)
        assert status == RolloutStepApprovalStatus.PENDING

    def test_evaluate_status_quorum_reached(self):
        """Quorum reached means APPROVED."""
        approval = self._make_approval(
            policy=RolloutApprovalPolicy(
                policy_type=RolloutApprovalPolicyType.QUORUM,
                required_approvals=2,
            ),
        )
        approve1 = self._make_decision(decided_by="r1", decision_id="rsd_q1")
        approve2 = self._make_decision(decided_by="r2", decision_id="rsd_q2")
        approval.decisions.extend([approve1, approve2])
        evaluator = RolloutApprovalPolicyEvaluator()
        status = evaluator.evaluate_status(approval)
        assert status == RolloutStepApprovalStatus.APPROVED

    def test_evaluate_status_single_approve(self):
        """SINGLE policy with one approve means APPROVED."""
        approval = self._make_approval()
        approve1 = self._make_decision(decided_by="r1")
        approval.decisions.append(approve1)
        evaluator = RolloutApprovalPolicyEvaluator()
        status = evaluator.evaluate_status(approval)
        assert status == RolloutStepApprovalStatus.APPROVED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_approval_policy.py::TestRolloutApprovalPolicyEvaluator -v`
Expected: FAIL — evaluator not yet implemented

- [ ] **Step 3: Implement the evaluator**

Create `agent_app/runtime/policy_rollout_approval_policy.py`:

```python
"""Rollout approval policy evaluator — validates decisions against policy constraints.

Phase 37: Separation of duties, quorum approvals, role/permission constraints.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agent_app.governance.policy_rollout_approval import (
    RolloutApprovalDecision,
    RolloutApprovalDecisionType,
    RolloutApprovalPolicy,
    RolloutApprovalPolicyType,
    RolloutStepApproval,
    RolloutStepApprovalStatus,
)


class ApprovalPolicyError(ValueError):
    """Raised when an approval decision violates the policy."""


class RolloutApprovalPolicyEvaluator:
    """Validates approval decisions and evaluates approval status."""

    def validate_decision(
        self,
        approval: RolloutStepApproval,
        decision: RolloutApprovalDecision,
        rollout: object | None = None,
        step: object | None = None,
    ) -> None:
        """Validate that a decision is allowed by the approval policy.

        Raises ApprovalPolicyError if the decision violates any policy constraint.
        """
        # 1. Approval must be pending
        if approval.status != RolloutStepApprovalStatus.PENDING:
            raise ApprovalPolicyError(
                f"Approval '{approval.approval_id}' is {approval.status.value}, expected PENDING"
            )

        # 2. Approval must not be expired
        if approval.expires_at is not None and datetime.now(timezone.utc) >= approval.expires_at:
            raise ApprovalPolicyError(
                f"Approval '{approval.approval_id}' has expired"
            )

        # 3. Actor must not have already decided
        existing_actors = {d.decided_by for d in approval.decisions}
        if decision.decided_by in existing_actors:
            raise ApprovalPolicyError(
                f"Actor '{decision.decided_by}' has already made a decision on approval '{approval.approval_id}'"
            )

        policy = approval.policy

        # 4. Reason required
        if policy.require_reason and not decision.reason:
            raise ApprovalPolicyError(
                f"Reason is required for decisions on approval '{approval.approval_id}'"
            )

        # 5. Requester self-approval
        if policy.prohibit_requester_approval and decision.decided_by == approval.requested_by:
            raise ApprovalPolicyError(
                f"Requester '{decision.decided_by}' cannot approve their own request"
            )

        # 6. Creator self-approval
        if policy.prohibit_creator_approval and rollout is not None:
            creator = getattr(rollout, "created_by", None)
            if creator and decision.decided_by == creator:
                raise ApprovalPolicyError(
                    f"Rollout creator '{decision.decided_by}' cannot approve their own rollout"
                )

        # 7. Role check — actor must have at least one allowed role
        if policy.allowed_approver_roles:
            if not any(r in policy.allowed_approver_roles for r in decision.roles):
                raise ApprovalPolicyError(
                    f"Actor '{decision.decided_by}' lacks required role. "
                    f"Required one of: {policy.allowed_approver_roles}, has: {decision.roles}"
                )

        # 8. Permission check — actor must have at least one allowed permission
        if policy.allowed_approver_permissions:
            if not any(p in policy.allowed_approver_permissions for p in decision.permissions):
                raise ApprovalPolicyError(
                    f"Actor '{decision.decided_by}' lacks required permission. "
                    f"Required one of: {policy.allowed_approver_permissions}, has: {decision.permissions}"
                )

    def evaluate_status(
        self,
        approval: RolloutStepApproval,
    ) -> RolloutStepApprovalStatus:
        """Evaluate the status of an approval based on its decisions and policy.

        Any reject → REJECTED.
        Approve count >= required_approvals → APPROVED.
        Otherwise → PENDING.
        """
        # Any reject immediately rejects
        if any(d.decision_type == RolloutApprovalDecisionType.REJECT for d in approval.decisions):
            return RolloutStepApprovalStatus.REJECTED

        # Count approves
        approve_count = sum(
            1 for d in approval.decisions
            if d.decision_type == RolloutApprovalDecisionType.APPROVE
        )

        if approve_count >= approval.policy.required_approvals:
            return RolloutStepApprovalStatus.APPROVED

        return RolloutStepApprovalStatus.PENDING
```

- [ ] **Step 4: Run evaluator tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_approval_policy.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_rollout_approval_policy.py tests/unit/test_policy_rollout_approval_policy.py
git commit -m "feat: Phase 37 Task 2 — approval policy evaluator"
```

---

### Task 3: Store Changes — add_decision and expire_pending

**Files:**
- Modify: `agent_app/runtime/policy_rollout_approval_store.py`
- Test: `tests/unit/test_policy_rollout_approval_store.py` (append tests)

- [ ] **Step 1: Write failing tests for add_decision and expire_pending**

Append to `tests/unit/test_policy_rollout_approval_store.py`:

```python
from agent_app.governance.policy_rollout_approval import (
    RolloutApprovalDecision,
    RolloutApprovalDecisionType,
    RolloutApprovalPolicy,
    RolloutApprovalPolicyType,
)
from agent_app.runtime.policy_rollout_approval_policy import RolloutApprovalPolicyEvaluator


class TestInMemoryApprovalStorePhase37:
    """Phase 37: add_decision and expire_pending for InMemoryRolloutStepApprovalStore."""

    def _make_approval(self, **kwargs):
        defaults = dict(
            approval_id="rsa_p37_01",
            rollout_id="ro_p37",
            step_id="s1",
            bundle_id="pb_001",
            environment="prod",
            requested_by="deployer",
            status=RolloutStepApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        defaults.update(kwargs)
        return RolloutStepApproval(**defaults)

    def _make_decision(self, **kwargs):
        defaults = dict(
            decision_id="rsd_p37_01",
            approval_id="rsa_p37_01",
            decision_type=RolloutApprovalDecisionType.APPROVE,
            decided_by="reviewer1",
            created_at=datetime.now(timezone.utc),
        )
        defaults.update(kwargs)
        return RolloutApprovalDecision(**defaults)

    def test_add_approve_decision(self):
        """add_decision appends an approve decision and evaluates status."""
        store = InMemoryRolloutStepApprovalStore()
        approval = self._make_approval()
        _run_async(store.create(approval))
        decision = self._make_decision()
        updated = _run_async(store.add_decision("rsa_p37_01", decision))
        assert len(updated.decisions) == 1
        assert updated.decisions[0].decision_type == RolloutApprovalDecisionType.APPROVE

    def test_add_reject_decision(self):
        """add_decision with reject sets status to REJECTED."""
        store = InMemoryRolloutStepApprovalStore()
        approval = self._make_approval()
        _run_async(store.create(approval))
        decision = self._make_decision(
            decision_type=RolloutApprovalDecisionType.REJECT,
        )
        updated = _run_async(store.add_decision("rsa_p37_01", decision))
        assert updated.status == RolloutStepApprovalStatus.REJECTED

    def test_duplicate_actor_decision_rejected(self):
        """add_decision fails if actor already decided."""
        store = InMemoryRolloutStepApprovalStore()
        approval = self._make_approval()
        _run_async(store.create(approval))
        d1 = self._make_decision()
        _run_async(store.add_decision("rsa_p37_01", d1))
        d2 = self._make_decision(decision_id="rsd_p37_02", decided_by="reviewer1")
        with pytest.raises(ValueError, match="already"):
            _run_async(store.add_decision("rsa_p37_01", d2))

    def test_quorum_approval_remains_pending(self):
        """Quorum approval stays PENDING until enough approvals."""
        store = InMemoryRolloutStepApprovalStore()
        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        approval = self._make_approval(policy=policy)
        _run_async(store.create(approval))
        d1 = self._make_decision(decided_by="r1")
        updated = _run_async(store.add_decision("rsa_p37_01", d1))
        assert updated.status == RolloutStepApprovalStatus.PENDING

    def test_quorum_approval_becomes_approved(self):
        """Quorum approval becomes APPROVED when threshold reached."""
        store = InMemoryRolloutStepApprovalStore()
        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        approval = self._make_approval(policy=policy)
        _run_async(store.create(approval))
        d1 = self._make_decision(decided_by="r1", decision_id="rsd_q01")
        _run_async(store.add_decision("rsa_p37_01", d1))
        d2 = self._make_decision(decided_by="r2", decision_id="rsd_q02")
        updated = _run_async(store.add_decision("rsa_p37_01", d2))
        assert updated.status == RolloutStepApprovalStatus.APPROVED

    def test_already_resolved_cannot_receive_decision(self):
        """Already APPROVED approval cannot receive more decisions."""
        store = InMemoryRolloutStepApprovalStore()
        approval = self._make_approval(status=RolloutStepApprovalStatus.APPROVED)
        _run_async(store.create(approval))
        d1 = self._make_decision()
        with pytest.raises(ValueError, match="PENDING"):
            _run_async(store.add_decision("rsa_p37_01", d1))

    def test_expire_pending_marks_expired(self):
        """expire_pending marks past-expires_at approvals as EXPIRED."""
        store = InMemoryRolloutStepApprovalStore()
        past = datetime.now(timezone.utc) - timedelta(seconds=60)
        approval = self._make_approval(expires_at=past)
        _run_async(store.create(approval))
        expired = _run_async(store.expire_pending())
        assert len(expired) == 1
        assert expired[0].status == RolloutStepApprovalStatus.EXPIRED
        fetched = _run_async(store.get("rsa_p37_01"))
        assert fetched.status == RolloutStepApprovalStatus.EXPIRED

    def test_expire_pending_skips_non_expired(self):
        """expire_pending skips approvals that haven't expired yet."""
        store = InMemoryRolloutStepApprovalStore()
        future = datetime.now(timezone.utc) + timedelta(seconds=3600)
        approval = self._make_approval(expires_at=future)
        _run_async(store.create(approval))
        expired = _run_async(store.expire_pending())
        assert len(expired) == 0

    def test_expired_approval_cannot_receive_decision(self):
        """EXPIRED approval cannot receive decisions."""
        store = InMemoryRolloutStepApprovalStore()
        approval = self._make_approval(status=RolloutStepApprovalStatus.EXPIRED)
        _run_async(store.create(approval))
        d1 = self._make_decision()
        with pytest.raises(ValueError, match="PENDING"):
            _run_async(store.add_decision("rsa_p37_01", d1))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_approval_store.py::TestInMemoryApprovalStorePhase37 -v`
Expected: FAIL — `add_decision` and `expire_pending` not yet implemented

- [ ] **Step 3: Implement add_decision and expire_pending in InMemoryRolloutStepApprovalStore**

Add to `InMemoryRolloutStepApprovalStore`:

```python
async def add_decision(self, approval_id: str, decision: RolloutApprovalDecision) -> RolloutStepApproval:
    approval = self._approvals.get(approval_id)
    if approval is None:
        raise KeyError(f"Rollout step approval '{approval_id}' not found")
    if approval.status != RolloutStepApprovalStatus.PENDING:
        raise ValueError(f"Cannot add decision: approval '{approval_id}' status is {approval.status.value}, expected PENDING")
    # Check duplicate actor
    existing_actors = {d.decided_by for d in approval.decisions}
    if decision.decided_by in existing_actors:
        raise ValueError(f"Actor '{decision.decided_by}' has already made a decision on approval '{approval_id}'")
    approval.decisions.append(decision)
    # Evaluate status
    evaluator = RolloutApprovalPolicyEvaluator()
    new_status = evaluator.evaluate_status(approval)
    if new_status != approval.status:
        approval.status = new_status
        if new_status in (RolloutStepApprovalStatus.APPROVED, RolloutStepApprovalStatus.REJECTED):
            approval.resolved_by = decision.decided_by
            approval.resolved_reason = decision.reason
            approval.resolved_at = datetime.now()
    return approval

async def expire_pending(self, now: datetime | None = None) -> list[RolloutStepApproval]:
    if now is None:
        now = datetime.now(timezone.utc)
    expired: list[RolloutStepApproval] = []
    for approval in self._approvals.values():
        if approval.status != RolloutStepApprovalStatus.PENDING:
            continue
        if approval.expires_at is not None and now >= approval.expires_at:
            approval.status = RolloutStepApprovalStatus.EXPIRED
            approval.resolved_at = now
            expired.append(approval)
    return expired
```

Add `add_decision` and `expire_pending` to the `RolloutStepApprovalStore` Protocol.

For `SQLiteRolloutStepApprovalStore`: Add `policy_json`, `decisions_json`, `expires_at` columns to the schema. Implement `add_decision` and `expire_pending` using the evaluator. Add `_row_to_approval` support for the new columns.

- [ ] **Step 4: Run store tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_approval_store.py -v`
Expected: PASS

- [ ] **Step 5: Run full existing test suite for backward compatibility**

Run: `.venv/bin/python -m pytest tests/unit/ -v --timeout=120`
Expected: 0 failures

- [ ] **Step 6: Commit**

```bash
git add agent_app/runtime/policy_rollout_approval_store.py tests/unit/test_policy_rollout_approval_store.py
git commit -m "feat: Phase 37 Task 3 — store add_decision and expire_pending"
```

---

### Task 4: RolloutService Changes for Policy-Aware Approvals

**Files:**
- Modify: `agent_app/runtime/policy_rollout_service.py`
- Test: `tests/unit/test_policy_rollout_approval_quorum.py` (new)

- [ ] **Step 1: Write failing integration tests for quorum workflow**

Create `tests/unit/test_policy_rollout_approval_quorum.py` with tests for:
- `request_step_approval` creates approval with policy
- First quorum approval keeps step BLOCKED
- Second quorum approval unblocks step
- Approved quorum step executes
- Reject fails step and plan
- Self-approval blocked
- Creator approval blocked
- Role restriction enforced
- Expiration enforced
- Audit/change events emitted

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_approval_quorum.py -v`
Expected: FAIL

- [ ] **Step 3: Modify RolloutService**

Key changes:
1. `request_step_approval`: Accept optional `policy` param; resolve policy from step → rollout config → default SINGLE; set `expires_at` if `expires_after_seconds` is configured; store policy on approval
2. `approve_step`: Create `RolloutApprovalDecision(APPROVE)`, validate via evaluator, add decision to store, evaluate status — if APPROVED set step BLOCKED → PENDING, if still PENDING keep step BLOCKED, persist plan, emit events
3. `reject_step`: Create `RolloutApprovalDecision(REJECT)`, validate via evaluator, add decision, approval becomes REJECTED, step FAILED, plan FAILED if active, persist, emit events
4. Add `expire_approvals()` method
5. Add `list_step_approvals()` if not already present

- [ ] **Step 4: Run quorum tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_approval_quorum.py -v`
Expected: PASS

- [ ] **Step 5: Run existing Phase 36 approval tests for backward compatibility**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_approval.py tests/unit/test_policy_rollout_service.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent_app/runtime/policy_rollout_service.py tests/unit/test_policy_rollout_approval_quorum.py
git commit -m "feat: Phase 37 Task 4 — RolloutService policy-aware approvals"
```

---

### Task 5: Config Schema and Loader

**Files:**
- Modify: `agent_app/config/schema.py`
- Modify: `agent_app/config/loader.py`
- Test: `tests/unit/test_policy_rollout_approval_config.py` (extend)

- [ ] **Step 1: Write failing tests for policy config**

Extend `test_policy_rollout_approval_config.py` with tests for:
- `RolloutApprovalPolicyConfig` with all fields
- Default config maps to SINGLE policy
- Quorum config maps correctly
- Config loader passes policy into RolloutService
- Existing `require_reason` mapped into policy

- [ ] **Step 2: Implement config changes**

Add `RolloutApprovalPolicyConfig` to `schema.py`:

```python
class RolloutApprovalPolicyConfig(BaseModel):
    """Configuration for approval policy (Phase 37)."""
    policy_type: Literal["single", "quorum"] = "single"
    required_approvals: int = 1
    allowed_approver_roles: list[str] = Field(default_factory=list)
    allowed_approver_permissions: list[str] = Field(default_factory=list)
    prohibit_requester_approval: bool = True
    prohibit_creator_approval: bool = False
    expires_after_seconds: int | None = None
    require_reason: bool = False
```

Extend `RolloutApprovalConfig` to include `policy: RolloutApprovalPolicyConfig | None = None`.

Update loader to wire policy into RolloutService.

- [ ] **Step 3: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_approval_config.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add agent_app/config/schema.py agent_app/config/loader.py tests/unit/test_policy_rollout_approval_config.py
git commit -m "feat: Phase 37 Task 5 — approval policy config and loader"
```

---

### Task 6: CLI Updates

**Files:**
- Modify: `agent_app/cli.py`
- Test: `tests/unit/test_policy_rollout_approval_cli.py` (extend)

- [ ] **Step 1: Write failing tests for CLI policy-aware approvals**

Extend `test_policy_rollout_approval_cli.py` with tests for:
- `--roles` flag on approve/reject
- Quorum approval: first approval remains pending, second approves
- Self-approval exits non-zero
- Missing role exits non-zero
- Duplicate decision exits non-zero
- `expire` command works
- `_approval_to_dict` includes decisions, policy, expires_at, required_approvals

- [ ] **Step 2: Implement CLI changes**

1. Add `--roles` argument to approve and reject subparsers
2. Update `_build_context` to pass roles into RunContext
3. Update `_approval_to_dict` to include policy, decisions, expires_at
4. Update approve/reject command handlers to pass roles/permissions from context
5. Add `expire` subcommand
6. Update list output to show approval count, required approvals, expires_at

- [ ] **Step 3: Run CLI tests**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_approval_cli.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add agent_app/cli.py tests/unit/test_policy_rollout_approval_cli.py
git commit -m "feat: Phase 37 Task 6 — CLI policy-aware approval commands"
```

---

### Task 7: Console Updates

**Files:**
- Modify: `agent_app/console/router.py`
- Modify: `agent_app/console/templates/rollout_approval_detail.html`
- Test: `tests/unit/test_policy_rollout_approval_console.py` (extend)

- [ ] **Step 1: Write failing tests for console quorum display**

Extend `test_policy_rollout_approval_console.py` with tests for:
- Detail page shows decisions table
- Detail page shows required_approvals
- Detail page shows current approval count
- Detail page shows expires_at
- Approve with role works
- First quorum approval shows pending message
- Second quorum approval shows approved
- Policy denial renders clearly

- [ ] **Step 2: Implement console changes**

1. Update approval detail template to show:
   - Policy type and required approvals
   - Current approve count / required
   - Decisions table (actor, type, reason, timestamp)
   - expires_at
   - Pending message with remaining count
2. Update approve/reject POST handlers to accept `roles` field
3. Pass roles from form data into decision creation

- [ ] **Step 3: Run console tests**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_approval_console.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add agent_app/console/router.py agent_app/console/templates/rollout_approval_detail.html tests/unit/test_policy_rollout_approval_console.py
git commit -m "feat: Phase 37 Task 7 — console quorum approval display"
```

---

### Task 8: Audit and Change Events

**Files:**
- Modify: `agent_app/runtime/policy_rollout_service.py` (extend event emission)
- Test: `tests/unit/test_policy_rollout_approval_quorum.py` (extend)

- [ ] **Step 1: Write failing tests for new event types**

Add tests to `test_policy_rollout_approval_quorum.py` for:
- `policy.rollout.approval.decision_recorded` event
- `policy.rollout.approval.quorum_reached` event
- `policy.rollout.approval.expired` event
- `policy.rollout.approval.policy_denied` event
- Event data includes approval_id, rollout_id, step_id, actor_id, decision_type, required_approvals, current_approvals, policy_type

- [ ] **Step 2: Implement event emission in RolloutService**

Add new event emissions in:
- `approve_step` / `reject_step`: emit `decision_recorded` after adding decision; emit `quorum_reached` when threshold met; emit `policy_denied` on policy violation
- `expire_approvals`: emit `expired` for each expired approval

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_rollout_approval_quorum.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add agent_app/runtime/policy_rollout_service.py tests/unit/test_policy_rollout_approval_quorum.py
git commit -m "feat: Phase 37 Task 8 — approval audit and change events"
```

---

### Task 9: Documentation and Final Verification

**Files:**
- Modify: `docs/policy_release.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Create: `docs/release_checklist_phase37.md`

- [ ] **Step 1: Update docs/policy_release.md with Phase 37 section**

Document:
1. Approval policies (SINGLE vs QUORUM)
2. Separation of duties
3. Role and permission constraints
4. Approval expiration
5. CLI examples
6. Console workflow
7. Known limitations

- [ ] **Step 2: Update CHANGELOG.md with v0.25.0 entry**

- [ ] **Step 3: Update README.md with Phase 37 in roadmap**

- [ ] **Step 4: Create docs/release_checklist_phase37.md**

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/ -v --timeout=120`
Expected: 0 failures

- [ ] **Step 6: Commit**

```bash
git add docs/policy_release.md CHANGELOG.md README.md docs/release_checklist_phase37.md
git commit -m "docs: Phase 37 documentation — approval policies, quorum, separation of duties"
```

---

## Self-Review Checklist

- [x] Spec coverage: All 13 sections of the Phase 37 spec are addressed by at least one task
- [x] Placeholder scan: No TBD/TODO/fill-in-later in any step
- [x] Type consistency: `RolloutApprovalPolicy`, `RolloutApprovalDecision`, `RolloutApprovalPolicyEvaluator` names used consistently across all tasks
- [x] Backward compatibility: Phase 36 single-approval behavior preserved via defaults (SINGLE policy, required_approvals=1, empty decisions list)
- [x] Import boundaries: No FastAPI/Jinja2 in governance or runtime modules
- [x] Test isolation: New tests use `_run_async` from conftest.py
