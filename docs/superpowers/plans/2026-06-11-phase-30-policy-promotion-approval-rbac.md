# Phase 30: Policy Promotion Approval, RBAC, Console Write Governance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade policy release from a CLI-executable workflow to an approvable, authorizable, auditable governance flow with RBAC, promotion requests, and console write operations.

**Architecture:** New `policy_rbac.py` and `policy_promotion.py` models, `promotion_store.py` with Protocol+InMemory+SQLite pattern (matching Phase 29 stores). PolicyReleaseService extended with request/approve/reject/execute methods that check permissions and write audit events. Config, CLI, and console extended to support the full promotion lifecycle.

**Tech Stack:** Pydantic models, stdlib sqlite3, argparse CLI, FastAPI/Jinja2 console (read + write), existing RunContext/permission/audit framework

---

## File Structure

```
agent_app/governance/policy_rbac.py          # New — PolicyReleasePermission, PolicyReleasePermissionChecker
agent_app/governance/policy_promotion.py     # New — PromotionRequestStatus, PromotionRequest
agent_app/runtime/promotion_store.py         # New — PromotionRequestStore protocol + InMemory + SQLite
agent_app/runtime/policy_release.py          # Modify — extend with RBAC + promotion lifecycle + audit
agent_app/config/schema.py                   # Modify — add promotion config fields
agent_app/config/loader.py                   # Modify — wire promotion_store into release service
agent_app/cli.py                             # Modify — add promotion subcommands
agent_app/console/router.py                  # Modify — add promotion pages + POST routes
agent_app/console/templates/policy_promotions.html        # New
agent_app/console/templates/policy_promotion_detail.html  # New
agent_app/adapters/fastapi.py                # Modify — pass promotion_store to console router
tests/unit/test_policy_rbac.py               # New
tests/unit/test_policy_promotion.py          # New — PromotionRequest model tests
tests/unit/test_policy_promotion_store.py    # New — 15+ store tests
tests/unit/test_policy_release.py            # Modify — add RBAC + promotion lifecycle tests
tests/unit/test_policy_release_cli.py        # Modify — add promotion CLI tests
tests/unit/test_policy_release_console.py    # Modify — add promotion console tests
docs/policy_release.md                       # Modify — Phase 30 section
CHANGELOG.md                                 # Modify — Phase 30 section (0.18.0)
README.md                                    # Modify — v0.18 to roadmap
docs/release_checklist_phase30.md            # New
```

---

## Task 1: Policy RBAC models and permission checker

**Files:**
- Create: `agent_app/governance/policy_rbac.py`
- Test: `tests/unit/test_policy_rbac.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_policy_rbac.py
from __future__ import annotations

import pytest
from agent_app.core.context import RunContext
from agent_app.governance.policy_rbac import (
    PolicyReleasePermission,
    PolicyReleasePermissionChecker,
)


class TestPolicyReleasePermissionChecker:
    """Tests for Phase 30 policy release RBAC."""

    def test_permission_enum_values(self):
        """PolicyReleasePermission has all required permission strings."""
        assert PolicyReleasePermission.BUNDLE_CREATE == "policy.bundle.create"
        assert PolicyReleasePermission.GATE_RUN == "policy.gate.run"
        assert PolicyReleasePermission.PROMOTION_REQUEST == "policy.promotion.request"
        assert PolicyReleasePermission.PROMOTION_APPROVE == "policy.promotion.approve"
        assert PolicyReleasePermission.PROMOTION_REJECT == "policy.promotion.reject"
        assert PolicyReleasePermission.PROMOTION_EXECUTE == "policy.promotion.execute"
        assert PolicyReleasePermission.ROLLBACK_EXECUTE == "policy.rollback.execute"
        assert PolicyReleasePermission.BYPASS_GATE == "policy.gate.bypass"

    @pytest.mark.asyncio
    async def test_permission_present_allows(self):
        """Permission present in context grants access."""
        checker = PolicyReleasePermissionChecker()
        ctx = RunContext(
            run_id="run_1",
            user_id="alice",
            tenant_id="tenant_1",
            permissions=["policy.promotion.request", "policy.promotion.approve"],
        )
        assert await checker.check(PolicyReleasePermission.PROMOTION_REQUEST, ctx) is True
        assert await checker.check(PolicyReleasePermission.PROMOTION_APPROVE, ctx) is True

    @pytest.mark.asyncio
    async def test_missing_permission_denies(self):
        """Missing permission denies access."""
        checker = PolicyReleasePermissionChecker()
        ctx = RunContext(
            run_id="run_1",
            user_id="alice",
            tenant_id="tenant_1",
            permissions=["policy.bundle.create"],
        )
        assert await checker.check(PolicyReleasePermission.PROMOTION_REQUEST, ctx) is False
        assert await checker.check(PolicyReleasePermission.PROMOTION_APPROVE, ctx) is False

    @pytest.mark.asyncio
    async def test_empty_permissions_denies(self):
        """Empty permissions list denies everything except bundle_create and gate_run."""
        checker = PolicyReleasePermissionChecker()
        ctx = RunContext(
            run_id="run_1",
            user_id="anon",
            tenant_id="tenant_1",
            permissions=[],
        )
        # bundle_create and gate_run are in the default allowed set
        assert await checker.check(PolicyReleasePermission.BUNDLE_CREATE, ctx) is True
        assert await checker.check(PolicyReleasePermission.GATE_RUN, ctx) is True
        # All promotion/rollback/bypass permissions require explicit grant
        assert await checker.check(PolicyReleasePermission.PROMOTION_REQUEST, ctx) is False
        assert await checker.check(PolicyReleasePermission.PROMOTION_APPROVE, ctx) is False
        assert await checker.check(PolicyReleasePermission.PROMOTION_EXECUTE, ctx) is False
        assert await checker.check(PolicyReleasePermission.ROLLBACK_EXECUTE, ctx) is False
        assert await checker.check(PolicyReleasePermission.BYPASS_GATE, ctx) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_policy_rbac.py -v`
Expected: FAIL with "No module named 'agent_app.governance.policy_rbac'"

- [ ] **Step 3: Write minimal implementation**

```python
# agent_app/governance/policy_rbac.py
"""Policy release RBAC — permissions and checker for policy release operations.

Phase 30: framework-level RBAC for policy bundle promotion lifecycle.
"""

from __future__ import annotations

from enum import Enum

from agent_app.core.context import RunContext


class PolicyReleasePermission(str, Enum):
    """Permissions for policy release operations."""

    BUNDLE_CREATE = "policy.bundle.create"
    GATE_RUN = "policy.gate.run"
    PROMOTION_REQUEST = "policy.promotion.request"
    PROMOTION_APPROVE = "policy.promotion.approve"
    PROMOTION_REJECT = "policy.promotion.reject"
    PROMOTION_EXECUTE = "policy.promotion.execute"
    ROLLBACK_EXECUTE = "policy.rollback.execute"
    BYPASS_GATE = "policy.gate.bypass"


# Permissions that are allowed by default (no explicit grant needed)
_DEFAULT_ALLOWED: set[str] = {
    PolicyReleasePermission.BUNDLE_CREATE,
    PolicyReleasePermission.GATE_RUN,
}


class PolicyReleasePermissionChecker:
    """Check policy release permissions against a RunContext.

    Rules:
    - BUNDLE_CREATE and GATE_RUN are allowed by default (anyone can create bundles and run gates).
    - All other permissions must be explicitly present in context.permissions.
    """

    async def check(
        self,
        required_permission: PolicyReleasePermission,
        context: RunContext,
    ) -> bool:
        """Check if the context grants the required permission.

        Args:
            required_permission: The permission to check.
            context: Current run context with permissions list.

        Returns:
            True if authorized, False otherwise.
        """
        if required_permission in _DEFAULT_ALLOWED:
            return True
        return required_permission in context.permissions
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_policy_rbac.py -v`
Expected: PASS (3/3)

- [ ] **Step 5: Commit**

```bash
git add agent_app/governance/policy_rbac.py tests/unit/test_policy_rbac.py
git commit -m "feat: Phase 30 Task 1 — PolicyReleasePermission and PolicyReleasePermissionChecker"
```

---

## Task 2: PromotionRequest model

**Files:**
- Create: `agent_app/governance/policy_promotion.py`
- Test: `tests/unit/test_policy_promotion.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_policy_promotion.py
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from agent_app.governance.policy_promotion import (
    PromotionRequest,
    PromotionRequestStatus,
)


class TestPromotionRequest:
    """Tests for PromotionRequest model."""

    def _make_request(self, **overrides):
        """Create a PromotionRequest with defaults."""
        defaults = dict(
            promotion_id="pr_abc123",
            bundle_id="pb_001",
            gate_result_id="gr_001",
            requested_by="alice",
            tenant_id="tenant_1",
            status=PromotionRequestStatus.PENDING,
            reason="Ready for release",
            created_at=datetime.now(timezone.utc),
        )
        defaults.update(overrides)
        return PromotionRequest(**defaults)

    def test_promotion_id_prefix(self):
        """promotion_id uses pr_ prefix."""
        req = self._make_request()
        assert req.promotion_id.startswith("pr_")

    def test_default_status_pending(self):
        """Default status is PENDING."""
        req = self._make_request()
        assert req.status == PromotionRequestStatus.PENDING

    def test_status_values(self):
        """PromotionRequestStatus has all required values."""
        assert PromotionRequestStatus.PENDING == "pending"
        assert PromotionRequestStatus.APPROVED == "approved"
        assert PromotionRequestStatus.REJECTED == "rejected"
        assert PromotionRequestStatus.EXECUTED == "executed"
        assert PromotionRequestStatus.CANCELLED == "cancelled"

    def test_timezone_aware_datetimes(self):
        """All datetime fields are timezone-aware."""
        req = self._make_request()
        assert req.created_at.tzinfo is not None
        # resolved_at, executed_at default to None
        assert req.resolved_at is None
        assert req.executed_at is None

    def test_with_gate_result(self):
        """Can create request with gate_result_id."""
        req = self._make_request(gate_result_id="gr_abc")
        assert req.gate_result_id == "gr_abc"

    def test_without_gate_result(self):
        """Can create request without gate_result_id (optional)."""
        req = self._make_request(gate_result_id=None)
        assert req.gate_result_id is None

    def test_optional_fields_default_none(self):
        """Optional reason and tenant_id default to None."""
        req = self._make_request(reason=None, tenant_id=None)
        assert req.reason is None
        assert req.tenant_id is None

    def test_executed_state_has_timestamps(self):
        """Executed request has executed_at and resolved_at set."""
        now = datetime.now(timezone.utc)
        req = self._make_request(
            status=PromotionRequestStatus.EXECUTED,
            resolved_at=now,
            executed_at=now,
            resolved_by="reviewer",
            executed_by="release_manager",
        )
        assert req.status == PromotionRequestStatus.EXECUTED
        assert req.executed_at is not None
        assert req.resolved_at is not None
        assert req.resolved_by == "reviewer"
        assert req.executed_by == "release_manager"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_policy_promotion.py -v`
Expected: FAIL with "No module named 'agent_app.governance.policy_promotion'"

- [ ] **Step 3: Write minimal implementation**

```python
# agent_app/governance/policy_promotion.py
"""Policy promotion — promotion request model for release approval workflow.

Phase 30: introduces PromotionRequest with lifecycle management
(pending → approved → executed / rejected / cancelled).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class PromotionRequestStatus(str, Enum):
    """Lifecycle status of a promotion request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    CANCELLED = "cancelled"


class PromotionRequest(BaseModel):
    """A request to promote a policy bundle.

    Attributes:
        promotion_id: Unique identifier (pr_ prefix).
        bundle_id: The bundle to promote.
        gate_result_id: Optional gate result that backs this request.
        requested_by: Identity of who requested the promotion.
        tenant_id: Optional tenant identifier.
        status: Current lifecycle status.
        reason: Reason for the promotion request.
        approval_reason: Reason for approval.
        rejection_reason: Reason for rejection.
        created_at: When the request was created.
        resolved_at: When the request was resolved (approved/rejected/cancelled).
        resolved_by: Identity of who resolved the request.
        executed_at: When the promotion was executed.
        executed_by: Identity of who executed the promotion.
    """

    promotion_id: str = Field(..., description="Unique promotion request ID (pr_ prefix)")
    bundle_id: str = Field(..., description="Bundle to promote")
    gate_result_id: str | None = Field(
        default=None, description="Gate result backing this request"
    )
    requested_by: str = Field(..., description="Identity of who requested")
    tenant_id: str | None = Field(default=None, description="Tenant identifier")
    status: str = Field(
        default=PromotionRequestStatus.PENDING,
        description="Current lifecycle status",
    )
    reason: str | None = Field(default=None, description="Reason for request")
    approval_reason: str | None = Field(default=None, description="Approval reason")
    rejection_reason: str | None = Field(default=None, description="Rejection reason")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Request timestamp",
    )
    resolved_at: datetime | None = Field(
        default=None, description="Resolution timestamp"
    )
    resolved_by: str | None = Field(default=None, description="Resolver identity")
    executed_at: datetime | None = Field(
        default=None, description="Execution timestamp"
    )
    executed_by: str | None = Field(default=None, description="Executor identity")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_policy_promotion.py -v`
Expected: PASS (8/8)

- [ ] **Step 5: Commit**

```bash
git add agent_app/governance/policy_promotion.py tests/unit/test_policy_promotion.py
git commit -m "feat: Phase 30 Task 2 — PromotionRequest model"
```

---

## Task 3: PromotionRequestStore (Protocol + InMemory + SQLite)

**Files:**
- Create: `agent_app/runtime/promotion_store.py`
- Test: `tests/unit/test_policy_promotion_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_policy_promotion_store.py
from __future__ import annotations

import os
import pytest
from datetime import datetime, timezone
from agent_app.governance.policy_promotion import (
    PromotionRequest,
    PromotionRequestStatus,
)
from agent_app.runtime.promotion_store import (
    InMemoryPromotionRequestStore,
    SQLitePromotionRequestStore,
    create_promotion_store,
)


def _make_request(**overrides):
    defaults = dict(
        promotion_id="pr_001",
        bundle_id="pb_001",
        gate_result_id="gr_001",
        requested_by="alice",
        tenant_id="tenant_1",
        status=PromotionRequestStatus.PENDING,
        reason="Ready for release",
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return PromotionRequest(**defaults)


class TestInMemoryPromotionRequestStore:
    """Tests for InMemoryPromotionRequestStore."""

    @pytest.mark.asyncio
    async def test_create_and_get(self):
        store = InMemoryPromotionRequestStore()
        req = _make_request()
        result = await store.create(req)
        assert result.promotion_id == "pr_001"
        fetched = await store.get("pr_001")
        assert fetched is not None
        assert fetched.bundle_id == "pb_001"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self):
        store = InMemoryPromotionRequestStore()
        assert await store.get("pr_nonexistent") is None

    @pytest.mark.asyncio
    async def test_approve(self):
        store = InMemoryPromotionRequestStore()
        req = _make_request()
        await store.create(req)
        updated = await store.approve("pr_001", "reviewer", reason="Looks good")
        assert updated.status == PromotionRequestStatus.APPROVED
        assert updated.resolved_by == "reviewer"
        assert updated.approval_reason == "Looks good"
        assert updated.resolved_at is not None

    @pytest.mark.asyncio
    async def test_reject(self):
        store = InMemoryPromotionRequestStore()
        req = _make_request()
        await store.create(req)
        updated = await store.reject("pr_001", "reviewer", reason="Too risky")
        assert updated.status == PromotionRequestStatus.REJECTED
        assert updated.rejection_reason == "Too risky"

    @pytest.mark.asyncio
    async def test_mark_executed(self):
        store = InMemoryPromotionRequestStore()
        req = _make_request(status=PromotionRequestStatus.APPROVED)
        await store.create(req)
        updated = await store.mark_executed("pr_001", "release_manager")
        assert updated.status == PromotionRequestStatus.EXECUTED
        assert updated.executed_by == "release_manager"
        assert updated.executed_at is not None

    @pytest.mark.asyncio
    async def test_list_empty(self):
        store = InMemoryPromotionRequestStore()
        assert await store.list() == []

    @pytest.mark.asyncio
    async def test_list_by_status(self):
        store = InMemoryPromotionRequestStore()
        await store.create(_make_request(promotion_id="pr_1", status=PromotionRequestStatus.PENDING))
        await store.create(_make_request(promotion_id="pr_2", status=PromotionRequestStatus.APPROVED))
        await store.create(_make_request(promotion_id="pr_3", status=PromotionRequestStatus.PENDING))
        pending = await store.list(status=PromotionRequestStatus.PENDING)
        assert len(pending) == 2
        approved = await store.list(status=PromotionRequestStatus.APPROVED)
        assert len(approved) == 1

    @pytest.mark.asyncio
    async def test_list_by_tenant_id(self):
        store = InMemoryPromotionRequestStore()
        await store.create(_make_request(promotion_id="pr_1", tenant_id="tenant_a"))
        await store.create(_make_request(promotion_id="pr_2", tenant_id="tenant_b"))
        a = await store.list(tenant_id="tenant_a")
        assert len(a) == 1
        assert a[0].promotion_id == "pr_1"

    @pytest.mark.asyncio
    async def test_cannot_approve_twice(self):
        """Approving an already-approved request should not change it."""
        store = InMemoryPromotionRequestStore()
        req = _make_request()
        await store.create(req)
        await store.approve("pr_001", "reviewer")
        # Second approve should be a no-op (or keep approved status)
        second = await store.approve("pr_001", "reviewer2", reason="Second try")
        assert second.status == PromotionRequestStatus.APPROVED

    @pytest.mark.asyncio
    async def test_create_overwrites(self):
        """Creating with same promotion_id overwrites."""
        store = InMemoryPromotionRequestStore()
        req1 = _make_request(promotion_id="pr_dup", reason="first")
        req2 = _make_request(promotion_id="pr_dup", reason="second")
        await store.create(req1)
        await store.create(req2)
        fetched = await store.get("pr_dup")
        assert fetched.reason == "second"


class TestSQLitePromotionRequestStore:
    """Tests for SQLitePromotionRequestStore."""

    def _make_store(self, tmp_path):
        db_path = str(tmp_path / "promotions.db")
        return SQLitePromotionRequestStore(db_path)

    @pytest.mark.asyncio
    async def test_create_and_get(self, tmp_path):
        store = self._make_store(tmp_path)
        req = _make_request()
        result = await store.create(req)
        assert result.promotion_id == "pr_001"
        fetched = await store.get("pr_001")
        assert fetched is not None
        assert fetched.bundle_id == "pb_001"

    @pytest.mark.asyncio
    async def test_approve_and_reject(self, tmp_path):
        store = self._make_store(tmp_path)
        await store.create(_make_request())
        approved = await store.approve("pr_001", "reviewer", reason="ok")
        assert approved.status == PromotionRequestStatus.APPROVED
        # Can't reject an approved request
        rejected = await store.reject("pr_001", "reviewer2", reason="nope")
        assert rejected.status == PromotionRequestStatus.APPROVED  # unchanged

    @pytest.mark.asyncio
    async def test_mark_executed(self, tmp_path):
        store = self._make_store(tmp_path)
        await store.create(_make_request(status=PromotionRequestStatus.APPROVED))
        executed = await store.mark_executed("pr_001", "release_manager")
        assert executed.status == PromotionRequestStatus.EXECUTED

    @pytest.mark.asyncio
    async def test_list_with_filters(self, tmp_path):
        store = self._make_store(tmp_path)
        await store.create(_make_request(promotion_id="pr_1", tenant_id="t1", status=PromotionRequestStatus.PENDING))
        await store.create(_make_request(promotion_id="pr_2", tenant_id="t2", status=PromotionRequestStatus.APPROVED))
        pending = await store.list(status=PromotionRequestStatus.PENDING)
        assert len(pending) == 1
        t1 = await store.list(tenant_id="t1")
        assert len(t1) == 1

    @pytest.mark.asyncio
    async def test_persists_across_instances(self, tmp_path):
        """SQLite store survives new instance creation."""
        db_path = str(tmp_path / "promotions.db")
        # First instance: create
        store1 = SQLitePromotionRequestStore(db_path)
        await store1.create(_make_request())
        store1.close()
        # Second instance: read
        store2 = SQLitePromotionRequestStore(db_path)
        fetched = await store2.get("pr_001")
        assert fetched is not None
        assert fetched.bundle_id == "pb_001"
        store2.close()


class TestCreatePromotionStoreFactory:
    """Tests for create_promotion_store factory."""

    def test_memory_store(self):
        store = create_promotion_store(store_type="memory")
        assert isinstance(store, InMemoryPromotionRequestStore)

    def test_sqlite_store(self, tmp_path):
        db_path = str(tmp_path / "promos.db")
        store = create_promotion_store(store_type="sqlite", db_path=db_path)
        assert isinstance(store, SQLitePromotionRequestStore)

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown promotion store type"):
            create_promotion_store(store_type="redis")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_policy_promotion_store.py -v`
Expected: FAIL with "No module named 'agent_app.runtime.promotion_store'"

- [ ] **Step 3: Write minimal implementation**

```python
# agent_app/runtime/promotion_store.py
"""Promotion request store — persistence for policy promotion requests.

Phase 30: stores PromotionRequest records with InMemory and SQLite backends.
Follows the same Protocol + InMemory + SQLite pattern as Phase 29 stores.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from agent_app.governance.policy_promotion import PromotionRequest, PromotionRequestStatus


# ---------------------------------------------------------------------------
# Store protocol
# ---------------------------------------------------------------------------

class PromotionRequestStore(Protocol):
    """Protocol for persisting policy promotion requests."""

    async def create(self, request: PromotionRequest) -> PromotionRequest:
        """Create or overwrite a promotion request."""
        ...

    async def get(self, promotion_id: str) -> PromotionRequest | None:
        """Retrieve a request by ID."""
        ...

    async def approve(
        self,
        promotion_id: str,
        approved_by: str,
        reason: str | None = None,
    ) -> PromotionRequest:
        """Approve a pending promotion request."""
        ...

    async def reject(
        self,
        promotion_id: str,
        rejected_by: str,
        reason: str | None = None,
    ) -> PromotionRequest:
        """Reject a pending promotion request."""
        ...

    async def mark_executed(
        self,
        promotion_id: str,
        executed_by: str,
    ) -> PromotionRequest:
        """Mark an approved request as executed."""
        ...

    async def list(
        self,
        status: PromotionRequestStatus | None = None,
        tenant_id: str | None = None,
    ) -> list[PromotionRequest]:
        """List requests, optionally filtered by status and/or tenant."""
        ...


# ---------------------------------------------------------------------------
# InMemoryPromotionRequestStore
# ---------------------------------------------------------------------------

class InMemoryPromotionRequestStore:
    """In-memory promotion request store for testing."""

    def __init__(self) -> None:
        self._requests: dict[str, PromotionRequest] = {}

    async def create(self, request: PromotionRequest) -> PromotionRequest:
        self._requests[request.promotion_id] = request
        return request

    async def get(self, promotion_id: str) -> PromotionRequest | None:
        return self._requests.get(promotion_id)

    async def approve(
        self,
        promotion_id: str,
        approved_by: str,
        reason: str | None = None,
    ) -> PromotionRequest:
        req = self._requests.get(promotion_id)
        if req is None:
            raise KeyError(f"Promotion request '{promotion_id}' not found.")
        # Only pending requests can be approved
        if req.status != PromotionRequestStatus.PENDING:
            return req  # no-op: return current state
        req.status = PromotionRequestStatus.APPROVED
        req.approval_reason = reason
        req.resolved_by = approved_by
        req.resolved_at = datetime.now(timezone.utc)
        self._requests[promotion_id] = req
        return req

    async def reject(
        self,
        promotion_id: str,
        rejected_by: str,
        reason: str | None = None,
    ) -> PromotionRequest:
        req = self._requests.get(promotion_id)
        if req is None:
            raise KeyError(f"Promotion request '{promotion_id}' not found.")
        # Only pending requests can be rejected (not already approved)
        if req.status != PromotionRequestStatus.PENDING:
            return req
        req.status = PromotionRequestStatus.REJECTED
        req.rejection_reason = reason
        req.resolved_by = rejected_by
        req.resolved_at = datetime.now(timezone.utc)
        self._requests[promotion_id] = req
        return req

    async def mark_executed(
        self,
        promotion_id: str,
        executed_by: str,
    ) -> PromotionRequest:
        req = self._requests.get(promotion_id)
        if req is None:
            raise KeyError(f"Promotion request '{promotion_id}' not found.")
        req.status = PromotionRequestStatus.EXECUTED
        req.executed_by = executed_by
        req.executed_at = datetime.now(timezone.utc)
        if req.resolved_at is None:
            req.resolved_at = req.executed_at
        self._requests[promotion_id] = req
        return req

    async def list(
        self,
        status: PromotionRequestStatus | None = None,
        tenant_id: str | None = None,
    ) -> list[PromotionRequest]:
        results = list(self._requests.values())
        if status is not None:
            results = [r for r in results if r.status == status]
        if tenant_id is not None:
            results = [r for r in results if r.tenant_id == tenant_id]
        return results


# ---------------------------------------------------------------------------
# SQLitePromotionRequestStore
# ---------------------------------------------------------------------------

class SQLitePromotionRequestStore:
    """SQLite-backed promotion request store."""

    def __init__(self, db_path: str = ".agent_app/policy_promotions.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS policy_promotion_requests (
                promotion_id TEXT PRIMARY KEY,
                bundle_id TEXT NOT NULL,
                gate_result_id TEXT,
                requested_by TEXT NOT NULL,
                tenant_id TEXT,
                status TEXT NOT NULL,
                reason TEXT,
                approval_reason TEXT,
                rejection_reason TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                resolved_by TEXT,
                executed_at TEXT,
                executed_by TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_promo_status
                ON policy_promotion_requests(status);
            CREATE INDEX IF NOT EXISTS idx_promo_tenant
                ON policy_promotion_requests(tenant_id);
            CREATE INDEX IF NOT EXISTS idx_promo_created
                ON policy_promotion_requests(created_at);
        """)
        self._conn.commit()

    async def create(self, request: PromotionRequest) -> PromotionRequest:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO policy_promotion_requests
                (promotion_id, bundle_id, gate_result_id, requested_by, tenant_id,
                 status, reason, approval_reason, rejection_reason,
                 created_at, resolved_at, resolved_by, executed_at, executed_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.promotion_id,
                request.bundle_id,
                request.gate_result_id,
                request.requested_by,
                request.tenant_id,
                request.status,
                request.reason,
                request.approval_reason,
                request.rejection_reason,
                request.created_at.isoformat(),
                request.resolved_at.isoformat() if request.resolved_at else None,
                request.resolved_by,
                request.executed_at.isoformat() if request.executed_at else None,
                request.executed_by,
            ),
        )
        self._conn.commit()
        return request

    async def get(self, promotion_id: str) -> PromotionRequest | None:
        row = self._conn.execute(
            "SELECT * FROM policy_promotion_requests WHERE promotion_id = ?",
            (promotion_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_request(row)

    async def approve(
        self,
        promotion_id: str,
        approved_by: str,
        reason: str | None = None,
    ) -> PromotionRequest:
        req = await self.get(promotion_id)
        if req is None:
            raise KeyError(f"Promotion request '{promotion_id}' not found.")
        if req.status != PromotionRequestStatus.PENDING:
            return req  # no-op
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE policy_promotion_requests
               SET status = ?, approval_reason = ?, resolved_by = ?, resolved_at = ?
               WHERE promotion_id = ?""",
            (
                PromotionRequestStatus.APPROVED,
                reason,
                approved_by,
                now,
                promotion_id,
            ),
        )
        self._conn.commit()
        updated = await self.get(promotion_id)
        return updated  # type: ignore[return-value]

    async def reject(
        self,
        promotion_id: str,
        rejected_by: str,
        reason: str | None = None,
    ) -> PromotionRequest:
        req = await self.get(promotion_id)
        if req is None:
            raise KeyError(f"Promotion request '{promotion_id}' not found.")
        if req.status != PromotionRequestStatus.PENDING:
            return req  # no-op
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE policy_promotion_requests
               SET status = ?, rejection_reason = ?, resolved_by = ?, resolved_at = ?
               WHERE promotion_id = ?""",
            (
                PromotionRequestStatus.REJECTED,
                reason,
                rejected_by,
                now,
                promotion_id,
            ),
        )
        self._conn.commit()
        updated = await self.get(promotion_id)
        return updated  # type: ignore[return-value]

    async def mark_executed(
        self,
        promotion_id: str,
        executed_by: str,
    ) -> PromotionRequest:
        req = await self.get(promotion_id)
        if req is None:
            raise KeyError(f"Promotion request '{promotion_id}' not found.")
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE policy_promotion_requests
               SET status = ?, executed_by = ?, executed_at = ?,
                   resolved_at = COALESCE(resolved_at, ?)
               WHERE promotion_id = ?""",
            (
                PromotionRequestStatus.EXECUTED,
                executed_by,
                now,
                now,
                promotion_id,
            ),
        )
        self._conn.commit()
        updated = await self.get(promotion_id)
        return updated  # type: ignore[return-value]

    async def list(
        self,
        status: PromotionRequestStatus | None = None,
        tenant_id: str | None = None,
    ) -> list[PromotionRequest]:
        query = "SELECT * FROM policy_promotion_requests"
        params: list[Any] = []
        where: list[str] = []
        if status is not None:
            where.append("status = ?")
            params.append(status)
        if tenant_id is not None:
            where.append("tenant_id = ?")
            params.append(tenant_id)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY created_at DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_request(row) for row in rows]

    def _row_to_request(self, row: sqlite3.Row) -> PromotionRequest:
        data = dict(row)
        data["status"] = PromotionRequestStatus(data.pop("status"))
        for ts_field in ("created_at", "resolved_at", "executed_at"):
            val = data.get(ts_field)
            data[ts_field] = datetime.fromisoformat(val) if val else None
        return PromotionRequest(**data)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------

def create_promotion_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> PromotionRequestStore:
    """Factory function to create a PromotionRequestStore."""
    if store_type == "memory":
        return InMemoryPromotionRequestStore()
    if store_type == "sqlite":
        return SQLitePromotionRequestStore(db_path=db_path or ".agent_app/policy_promotions.db")
    raise ValueError(
        f"Unknown promotion store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_policy_promotion_store.py -v`
Expected: PASS (15/15)

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/promotion_store.py tests/unit/test_policy_promotion_store.py
git commit -m "feat: Phase 30 Task 3 — PromotionRequestStore protocol + InMemory + SQLite"
```

---

## Task 4: Extend PolicyReleaseService with RBAC + promotion lifecycle + audit

**Files:**
- Modify: `agent_app/runtime/policy_release.py`
- Modify: `tests/unit/test_policy_release.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/unit/test_policy_release.py (append after existing tests)

from agent_app.governance.policy_rbac import (
    PolicyReleasePermission,
    PolicyReleasePermissionChecker,
)
from agent_app.runtime.promotion_store import (
    InMemoryPromotionRequestStore,
    PromotionRequest,
    PromotionRequestStatus,
)


class TestPolicyReleaseServiceRBAC:
    """Tests for RBAC and promotion lifecycle in PolicyReleaseService."""

    def _make_service(self, promotion_store=None, permission_checker=None):
        """Create service with optional promotion store and permission checker."""
        from agent_app.governance.policy_gate import PolicyGateEvaluator, PolicyGateRule

        bundle_store = InMemoryPolicyBundleStore()
        gate_store = InMemoryPolicyGateStore()

        if promotion_store is None:
            promotion_store = InMemoryPromotionRequestStore()

        if permission_checker is None:
            permission_checker = PolicyReleasePermissionChecker()

        service = PolicyReleaseService(
            bundle_store=bundle_store,
            replay_runner=_make_mock_replay_runner(),
            replay_store=_make_mock_replay_store(),
            gate_evaluator=PolicyGateEvaluator(rules=[
                PolicyGateRule(name="safe_default", max_changed_ratio=0.10, max_failed_replays=0),
            ]),
            gate_store=gate_store,
            promotion_store=promotion_store,
            permission_checker=permission_checker,
        )
        return service

    def _make_context(self, permissions: list[str]) -> RunContext:
        return RunContext(
            run_id="run_1",
            user_id="alice",
            tenant_id="tenant_1",
            permissions=permissions,
        )

    @pytest.mark.asyncio
    async def test_request_promotion_requires_permission(self):
        """request_promotion fails without policy.promotion.request permission."""
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx = self._make_context(permissions=[])  # no promotion permissions
        with pytest.raises(PermissionError, match="policy.promotion.request"):
            await service.request_promotion(
                bundle_id=bundle.bundle_id,
                requested_by="alice",
                context=ctx,
                reason="Release it",
            )

    @pytest.mark.asyncio
    async def test_request_promotion_success(self):
        """request_promotion creates a pending request when permission granted."""
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx = self._make_context(permissions=["policy.promotion.request"])
        req = await service.request_promotion(
            bundle_id=bundle.bundle_id,
            requested_by="alice",
            context=ctx,
            reason="Ready for release",
        )
        assert req.status == PromotionRequestStatus.PENDING
        assert req.bundle_id == bundle.bundle_id
        assert req.requested_by == "alice"
        assert req.reason == "Ready for release"
        assert req.promotion_id.startswith("pr_")

    @pytest.mark.asyncio
    async def test_approve_promotion_requires_permission(self):
        """approve_promotion fails without policy.promotion.approve."""
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx_req = self._make_context(permissions=["policy.promotion.request"])
        req = await service.request_promotion(
            bundle_id=bundle.bundle_id, requested_by="alice", context=ctx_req,
        )
        ctx_approve = self._make_context(permissions=[])  # no approve permission
        with pytest.raises(PermissionError, match="policy.promotion.approve"):
            await service.approve_promotion(
                promotion_id=req.promotion_id,
                approved_by="reviewer",
                context=ctx_approve,
            )

    @pytest.mark.asyncio
    async def test_approve_promotion_success(self):
        """approve_promotion transitions request to APPROVED."""
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx_req = self._make_context(permissions=["policy.promotion.request"])
        req = await service.request_promotion(
            bundle_id=bundle.bundle_id, requested_by="alice", context=ctx_req,
        )
        ctx_approve = self._make_context(permissions=["policy.promotion.approve"])
        updated = await service.approve_promotion(
            promotion_id=req.promotion_id,
            approved_by="reviewer",
            context=ctx_approve,
            reason="Reviewed gate result",
        )
        assert updated.status == PromotionRequestStatus.APPROVED
        assert updated.resolved_by == "reviewer"

    @pytest.mark.asyncio
    async def test_reject_promotion_requires_permission(self):
        """reject_promotion fails without policy.promotion.reject."""
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx_req = self._make_context(permissions=["policy.promotion.request"])
        req = await service.request_promotion(
            bundle_id=bundle.bundle_id, requested_by="alice", context=ctx_req,
        )
        ctx_reject = self._make_context(permissions=[])
        with pytest.raises(PermissionError, match="policy.promotion.reject"):
            await service.reject_promotion(
                promotion_id=req.promotion_id,
                rejected_by="reviewer",
                context=ctx_reject,
                reason="Too risky",
            )

    @pytest.mark.asyncio
    async def test_reject_promotion_success(self):
        """reject_promotion transitions request to REJECTED."""
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx_req = self._make_context(permissions=["policy.promotion.request"])
        req = await service.request_promotion(
            bundle_id=bundle.bundle_id, requested_by="alice", context=ctx_req,
        )
        ctx_reject = self._make_context(permissions=["policy.promotion.reject"])
        updated = await service.reject_promotion(
            promotion_id=req.promotion_id,
            rejected_by="reviewer",
            context=ctx_reject,
            reason="Too risky",
        )
        assert updated.status == PromotionRequestStatus.REJECTED

    @pytest.mark.asyncio
    async def test_execute_pending_fails(self):
        """execute_promotion fails for pending request."""
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx_req = self._make_context(permissions=["policy.promotion.request"])
        req = await service.request_promotion(
            bundle_id=bundle.bundle_id, requested_by="alice", context=ctx_req,
        )
        ctx_exec = self._make_context(permissions=["policy.promotion.execute"])
        with pytest.raises(ValueError, match="must be approved"):
            await service.execute_promotion(
                promotion_id=req.promotion_id,
                executed_by="release_manager",
                context=ctx_exec,
            )

    @pytest.mark.asyncio
    async def test_execute_rejected_fails(self):
        """execute_promotion fails for rejected request."""
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx_req = self._make_context(permissions=["policy.promotion.request", "policy.promotion.reject"])
        req = await service.request_promotion(
            bundle_id=bundle.bundle_id, requested_by="alice", context=ctx_req,
        )
        await service.reject_promotion(
            promotion_id=req.promotion_id, rejected_by="reviewer", context=ctx_req,
        )
        ctx_exec = self._make_context(permissions=["policy.promotion.execute"])
        with pytest.raises(ValueError, match="must be approved"):
            await service.execute_promotion(
                promotion_id=req.promotion_id,
                executed_by="release_manager",
                context=ctx_exec,
            )

    @pytest.mark.asyncio
    async def test_execute_approved_activates_bundle(self):
        """execute_promotion activates the bundle for approved request."""
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        # Run gate so bundle has a passing gate
        await service.run_gate(bundle_id=bundle.bundle_id, created_by="admin")
        ctx = self._make_context(permissions=[
            "policy.promotion.request", "policy.promotion.approve", "policy.promotion.execute",
        ])
        req = await service.request_promotion(
            bundle_id=bundle.bundle_id, requested_by="alice", context=ctx,
        )
        await service.approve_promotion(
            promotion_id=req.promotion_id, approved_by="reviewer", context=ctx,
        )
        result = await service.execute_promotion(
            promotion_id=req.promotion_id,
            executed_by="release_manager",
            context=ctx,
        )
        assert result.status == PolicyBundleStatus.ACTIVE
        assert result.bundle_id == bundle.bundle_id

    @pytest.mark.asyncio
    async def test_execute_requires_permission(self):
        """execute_promotion fails without policy.promotion.execute."""
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx = self._make_context(permissions=["policy.promotion.request", "policy.promotion.approve"])
        req = await service.request_promotion(
            bundle_id=bundle.bundle_id, requested_by="alice", context=ctx,
        )
        await service.approve_promotion(
            promotion_id=req.promotion_id, approved_by="reviewer", context=ctx,
        )
        # No execute permission
        ctx_exec = self._make_context(permissions=[])
        with pytest.raises(PermissionError, match="policy.promotion.execute"):
            await service.execute_promotion(
                promotion_id=req.promotion_id,
                executed_by="release_manager",
                context=ctx_exec,
            )

    @pytest.mark.asyncio
    async def test_bypass_gate_requires_config_and_permission(self):
        """bypass_gate=True in config + BYPASS_GATE permission allows promotion despite failed gate."""
        from agent_app.governance.policy_bundle import PolicyBundleStatus

        # Create service with allow_gate_bypass=True
        bundle_store = InMemoryPolicyBundleStore()
        gate_store = InMemoryPolicyGateStore()
        promotion_store = InMemoryPromotionRequestStore()
        checker = PolicyReleasePermissionChecker()

        # Gate evaluator that always fails
        from agent_app.governance.policy_gate import PolicyGateEvaluator, PolicyGateRule
        evaluator = PolicyGateEvaluator(rules=[
            PolicyGateRule(name="always_fail", max_changed_ratio=0.0),  # any change fails
        ])

        service = PolicyReleaseService(
            bundle_store=bundle_store,
            replay_runner=_make_mock_replay_runner(changed=1, total=10),
            replay_store=_make_mock_replay_store(),
            gate_evaluator=evaluator,
            gate_store=gate_store,
            promotion_store=promotion_store,
            permission_checker=checker,
            allow_gate_bypass=True,
        )

        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        # Gate will fail
        await service.run_gate(bundle_id=bundle.bundle_id, created_by="admin")

        ctx = self._make_context(permissions=[
            "policy.promotion.request", "policy.promotion.approve",
            "policy.promotion.execute", "policy.gate.bypass",
        ])
        req = await service.request_promotion(
            bundle_id=bundle.bundle_id, requested_by="alice", context=ctx,
        )
        await service.approve_promotion(
            promotion_id=req.promotion_id, approved_by="reviewer", context=ctx,
        )
        # Without bypass reason, should still fail
        with pytest.raises(ValueError, match="gate"):
            await service.execute_promotion(
                promotion_id=req.promotion_id,
                executed_by="release_manager",
                context=ctx,
                bypass_gate=True,
                bypass_reason=None,
            )
        # With bypass reason, should succeed
        result = await service.execute_promotion(
            promotion_id=req.promotion_id,
            executed_by="release_manager",
            context=ctx,
            bypass_gate=True,
            bypass_reason="Emergency release — rollback plan ready",
        )
        assert result.status == PolicyBundleStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_audit_events_written(self):
        """Policy release operations write audit events."""
        from agent_app.governance.audit import InMemoryAuditLogger, AuditEvent

        audit = InMemoryAuditLogger()
        service = PolicyReleaseService(
            bundle_store=InMemoryPolicyBundleStore(),
            replay_runner=_make_mock_replay_runner(),
            replay_store=_make_mock_replay_store(),
            gate_evaluator=_make_default_evaluator(),
            gate_store=InMemoryPolicyGateStore(),
            promotion_store=InMemoryPromotionRequestStore(),
            permission_checker=PolicyReleasePermissionChecker(),
            audit_logger=audit,
        )

        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx = self._make_context(permissions=[
            "policy.promotion.request", "policy.promotion.approve", "policy.promotion.execute",
        ])
        req = await service.request_promotion(
            bundle_id=bundle.bundle_id, requested_by="alice", context=ctx, reason="release",
        )
        await service.approve_promotion(
            promotion_id=req.promotion_id, approved_by="reviewer", context=ctx,
        )
        await service.run_gate(bundle_id=bundle.bundle_id, created_by="admin")
        await service.execute_promotion(
            promotion_id=req.promotion_id, executed_by="release_manager", context=ctx,
        )

        event_types = [e.event_type for e in audit.list_events()]
        assert "policy.promotion.requested" in event_types
        assert "policy.promotion.approved" in event_types
        assert "policy.promotion.executed" in event_types
```

Helper functions to add at the top of the test class area:

```python
def _make_mock_replay_runner(total=100, changed=5, failed=0):
    class MockRunner:
        async def run_replay(self, **kwargs):
            return _make_replay_result(total=total, changed=changed, failed=failed)
    return MockRunner()


def _make_mock_replay_store():
    class MockStore:
        async def save(self, result):
            return result
    return MockStore()


def _make_default_evaluator():
    from agent_app.governance.policy_gate import PolicyGateEvaluator, PolicyGateRule
    return PolicyGateEvaluator(rules=[
        PolicyGateRule(name="safe_default", max_changed_ratio=0.10, max_failed_replays=0),
    ])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_policy_release.py::TestPolicyReleaseServiceRBAC -v`
Expected: FAIL — missing `promotion_store`, `permission_checker`, `audit_logger` params and new methods

- [ ] **Step 3: Write minimal implementation**

```python
# Modify agent_app/runtime/policy_release.py

# Add imports at top:
from agent_app.core.context import RunContext
from agent_app.governance.policy_rbac import (
    PolicyReleasePermission,
    PolicyReleasePermissionChecker,
)
from agent_app.governance.policy_promotion import (
    PromotionRequest,
    PromotionRequestStatus,
)
from agent_app.governance.audit import AuditEvent


# Add PermissionError class (if not already defined):
class PolicyReleasePermissionError(Exception):
    """Raised when a policy release permission check fails."""
    pass


# Extend PolicyReleaseService.__init__:
def __init__(
    self,
    bundle_store: Any,
    replay_runner: Any,
    replay_store: Any,
    gate_evaluator: Any,
    gate_store: Any,
    promotion_store: Any = None,
    permission_checker: Any = None,
    audit_logger: Any = None,
    allow_gate_bypass: bool = False,
    require_promotion_approval: bool = True,
) -> None:
    self._bundle_store = bundle_store
    self._replay_runner = replay_runner
    self._replay_store = replay_store
    self._gate_evaluator = gate_evaluator
    self._gate_store = gate_store
    self._promotion_store = promotion_store
    self._permission_checker = permission_checker or PolicyReleasePermissionChecker()
    self._audit_logger = audit_logger
    self._allow_gate_bypass = allow_gate_bypass
    self._require_promotion_approval = require_promotion_approval


# Add _check_permission helper:
async def _check_permission(
    self,
    permission: PolicyReleasePermission,
    context: RunContext,
) -> None:
    """Check permission, raise PolicyReleasePermissionError if denied."""
    if not await self._permission_checker.check(permission, context):
        await self._write_audit(
            event_type="policy.promotion.permission_denied",
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            data={"required_permission": permission.value},
        )
        raise PolicyReleasePermissionError(
            f"Permission denied: '{permission.value}' "
            f"not in context permissions: {context.permissions}"
        )


# Add _write_audit helper:
async def _write_audit(
    self,
    event_type: str,
    user_id: str | None = None,
    tenant_id: str | None = None,
    data: dict | None = None,
) -> None:
    if self._audit_logger is None:
        return
    from agent_app.governance.audit import AuditEvent
    await self._audit_logger.log(AuditEvent(
        event_id=f"ae_{uuid.uuid4().hex[:12]}",
        event_type=event_type,
        user_id=user_id,
        tenant_id=tenant_id,
        data=data or {},
    ))


# Add new promotion lifecycle methods:

async def request_promotion(
    self,
    bundle_id: str,
    requested_by: str,
    context: RunContext,
    reason: str | None = None,
) -> PromotionRequest:
    """Request promotion of a bundle.

    Requires: policy.promotion.request permission.
    Creates a PENDING PromotionRequest.
    """
    from agent_app.governance.policy_promotion import PromotionRequest

    await self._check_permission(PolicyReleasePermission.PROMOTION_REQUEST, context)

    bundle = await self._bundle_store.get(bundle_id)
    if bundle is None:
        raise KeyError(f"Bundle '{bundle_id}' not found.")

    request = PromotionRequest(
        promotion_id=f"pr_{uuid.uuid4().hex[:12]}",
        bundle_id=bundle_id,
        requested_by=requested_by,
        tenant_id=context.tenant_id,
        reason=reason,
    )

    if self._promotion_store is not None:
        request = await self._promotion_store.create(request)

    await self._write_audit(
        event_type="policy.promotion.requested",
        user_id=requested_by,
        tenant_id=context.tenant_id,
        data={
            "promotion_id": request.promotion_id,
            "bundle_id": bundle_id,
            "reason": reason,
        },
    )
    return request


async def approve_promotion(
    self,
    promotion_id: str,
    approved_by: str,
    context: RunContext,
    reason: str | None = None,
) -> PromotionRequest:
    """Approve a pending promotion request.

    Requires: policy.promotion.approve permission.
    """
    await self._check_permission(PolicyReleasePermission.PROMOTION_APPROVE, context)

    if self._promotion_store is None:
        raise RuntimeError("Promotion store not configured.")

    request = await self._promotion_store.approve(promotion_id, approved_by, reason=reason)
    if request is None:
        raise KeyError(f"Promotion request '{promotion_id}' not found.")

    await self._write_audit(
        event_type="policy.promotion.approved",
        user_id=approved_by,
        tenant_id=context.tenant_id,
        data={
            "promotion_id": promotion_id,
            "bundle_id": request.bundle_id,
            "reason": reason,
        },
    )
    return request


async def reject_promotion(
    self,
    promotion_id: str,
    rejected_by: str,
    context: RunContext,
    reason: str | None = None,
) -> PromotionRequest:
    """Reject a pending promotion request.

    Requires: policy.promotion.reject permission.
    """
    await self._check_permission(PolicyReleasePermission.PROMOTION_REJECT, context)

    if self._promotion_store is None:
        raise RuntimeError("Promotion store not configured.")

    request = await self._promotion_store.reject(promotion_id, rejected_by, reason=reason)
    if request is None:
        raise KeyError(f"Promotion request '{promotion_id}' not found.")

    await self._write_audit(
        event_type="policy.promotion.rejected",
        user_id=rejected_by,
        tenant_id=context.tenant_id,
        data={
            "promotion_id": promotion_id,
            "bundle_id": request.bundle_id,
            "reason": reason,
        },
    )
    return request


async def execute_promotion(
    self,
    promotion_id: str,
    executed_by: str,
    context: RunContext,
    bypass_gate: bool = False,
    bypass_reason: str | None = None,
) -> Any:
    """Execute an approved promotion.

    Requires: policy.promotion.execute permission.
    Validates request is APPROVED, checks gate status, then promotes.
    """
    from agent_app.governance.policy_promotion import PromotionRequestStatus
    from agent_app.governance.policy_bundle import PolicyBundleStatus

    await self._check_permission(PolicyReleasePermission.PROMOTION_EXECUTE, context)

    if self._promotion_store is None:
        raise RuntimeError("Promotion store not configured.")

    request = await self._promotion_store.get(promotion_id)
    if request is None:
        raise KeyError(f"Promotion request '{promotion_id}' not found.")

    if request.status != PromotionRequestStatus.APPROVED:
        raise ValueError(
            f"Cannot execute promotion '{promotion_id}': status is '{request.status}'. "
            f"Must be 'approved'."
        )

    # Check gate status
    gate_results = await self._gate_store.list(bundle_id=request.bundle_id, limit=1)
    if gate_results:
        latest = gate_results[0]
        if not latest.passed:
            if bypass_gate and self._allow_gate_bypass:
                if not bypass_reason:
                    raise ValueError(
                        "Gate bypass requires a bypass_reason when "
                        "allow_gate_bypass is enabled."
                    )
                if not await self._permission_checker.check(
                    PolicyReleasePermission.BYPASS_GATE, context
                ):
                    await self._write_audit(
                        event_type="policy.promotion.execute_blocked",
                        user_id=executed_by,
                        tenant_id=context.tenant_id,
                        data={
                            "promotion_id": promotion_id,
                            "bundle_id": request.bundle_id,
                            "reason": "bypass permission missing",
                        },
                    )
                    raise PolicyReleasePermissionError(
                        "Gate bypass requires 'policy.gate.bypass' permission."
                    )
                await self._write_audit(
                    event_type="policy.gate.bypass_used",
                    user_id=executed_by,
                    tenant_id=context.tenant_id,
                    data={
                        "promotion_id": promotion_id,
                        "bundle_id": request.bundle_id,
                        "bypass_reason": bypass_reason,
                        "gate_result_id": latest.gate_result_id,
                    },
                )
            else:
                await self._write_audit(
                    event_type="policy.promotion.execute_blocked",
                    user_id=executed_by,
                    tenant_id=context.tenant_id,
                    data={
                        "promotion_id": promotion_id,
                        "bundle_id": request.bundle_id,
                        "reason": f"latest gate {latest.status}",
                    },
                )
                raise ValueError(
                    f"Cannot execute promotion '{promotion_id}': "
                    f"latest gate result is {latest.status}. "
                    f"Use bypass_gate=True with appropriate permission and reason."
                )

    # Execute: promote the bundle
    bundle = await self._bundle_store.activate(request.bundle_id)

    # Mark promotion as executed
    await self._promotion_store.mark_executed(promotion_id, executed_by)

    await self._write_audit(
        event_type="policy.promotion.executed",
        user_id=executed_by,
        tenant_id=context.tenant_id,
        data={
            "promotion_id": promotion_id,
            "bundle_id": request.bundle_id,
            "bypass_gate": bypass_gate,
            "bypass_reason": bypass_reason,
        },
    )
    return bundle


# Add promotion_store accessor property:
@property
def promotion_store(self) -> Any:
    """Access the underlying promotion store (for console integration)."""
    return self._promotion_store
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_policy_release.py -v`
Expected: All tests pass (original + new RBAC tests)

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_release.py tests/unit/test_policy_release.py
git commit -m "feat: Phase 30 Task 4 — PolicyReleaseService RBAC, promotion lifecycle, audit"
```

---

## Task 5: Config schema and loader extensions

**Files:**
- Modify: `agent_app/config/schema.py`
- Modify: `agent_app/config/loader.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/unit/test_policy_release.py (or a new test_config_phase30.py)
# We'll add it to the existing test file for simplicity.

# At the top of the test section, add:
def test_phase30_config_schema(self):
    """PolicyReleaseConfig supports Phase 30 fields."""
    from agent_app.config.schema import (
        PolicyReleaseConfig,
        PolicyReleaseStoreConfig,
    )
    cfg = PolicyReleaseConfig(
        bundles=PolicyReleaseStoreConfig(type="sqlite", path="bundles.db"),
        gates=PolicyReleaseStoreConfig(type="sqlite", path="gates.db"),
        promotions=PolicyReleaseStoreConfig(type="sqlite", path="promos.db"),
        rules=[],
        require_promotion_approval=True,
        allow_gate_bypass=False,
    )
    assert cfg.promotions.type == "sqlite"
    assert cfg.promotions.path == "promos.db"
    assert cfg.require_promotion_approval is True
    assert cfg.allow_gate_bypass is False
```

Actually, let me write a dedicated test approach. I'll add the config test inline in the existing test file.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_policy_release.py::TestPolicyReleaseServiceRBAC::test_phase30_config_schema -v`
Expected: FAIL — `PolicyReleaseConfig` has no `promotions`, `require_promotion_approval`, or `allow_gate_bypass` fields

- [ ] **Step 3: Write minimal implementation**

```python
# In agent_app/config/schema.py, modify PolicyReleaseConfig:

class PolicyReleaseConfig(BaseModel):
    """Policy release gate configuration (Phase 29, extended Phase 30)."""

    bundles: PolicyReleaseStoreConfig = Field(
        default_factory=PolicyReleaseStoreConfig,
        description="Policy bundle store configuration",
    )
    gates: PolicyReleaseStoreConfig = Field(
        default_factory=PolicyReleaseStoreConfig,
        description="Policy gate result store configuration",
    )
    promotions: PolicyReleaseStoreConfig = Field(
        default_factory=PolicyReleaseStoreConfig,
        description="Policy promotion request store configuration (Phase 30)",
    )
    rules: list[PolicyGateRuleConfig] = Field(
        default_factory=list,
        description="Release gate rules",
    )
    require_promotion_approval: bool = Field(
        default=True,
        description="Require approval workflow before executing promotion (Phase 30)",
    )
    allow_gate_bypass: bool = Field(
        default=False,
        description="Allow bypassing failed gate with explicit permission and reason (Phase 30)",
    )
```

```python
# In agent_app/config/loader.py, extend the release service creation:

# After the existing gate_store creation (around line 2244), add:
promotion_store = None
if getattr(release_config, "promotions", None):
    promo_type = getattr(release_config.promotions, "type", "memory")
    promo_path = getattr(release_config.promotions, "path", None)
    from agent_app.runtime.promotion_store import create_promotion_store
    promotion_store = create_promotion_store(
        store_type=promo_type,
        db_path=promo_path,
    )

# After the service creation (around line 2278), add:
service = PolicyReleaseService(
    bundle_store=bundle_store,
    replay_runner=replay_runner,
    replay_store=None,
    gate_evaluator=evaluator,
    gate_store=gate_store,
    promotion_store=promotion_store,
    allow_gate_bypass=getattr(release_config, "allow_gate_bypass", False),
    require_promotion_approval=getattr(release_config, "require_promotion_approval", True),
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_policy_release.py -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add agent_app/config/schema.py agent_app/config/loader.py
git commit -m "feat: Phase 30 Task 5 — Config schema and loader for promotion store + RBAC settings"
```

---

## Task 6: CLI promotion subcommands

**Files:**
- Modify: `agent_app/cli.py`
- Modify: `tests/unit/test_policy_release_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/unit/test_policy_release_cli.py:

_BASE_CONFIG_30 = """
app:
  name: test
  environment: dev
governance:
  policies:
    enabled: true
    default_action: allow
    rules: []
  policy_decisions:
    type: memory
  policy_release:
    bundles:
      type: sqlite
      path: {bundle_db}
    gates:
      type: sqlite
      path: {gate_db}
    promotions:
      type: sqlite
      path: {promo_db}
    require_promotion_approval: true
    allow_gate_bypass: false
    rules:
      - name: safe_default
        max_changed_ratio: 0.10
        max_failed_replays: 0
"""


class TestPolicyPromotionCLI:
    """Tests for Phase 30 promotion CLI commands."""

    def _write_config(self, tmp_path):
        bundle_db = str(tmp_path / "bundles.db")
        gate_db = str(tmp_path / "gates.db")
        promo_db = str(tmp_path / "promos.db")
        for p in [bundle_db, gate_db, promo_db]:
            if os.path.exists(p):
                os.remove(p)
        config = _write_config(tmp_path, _BASE_CONFIG_30.format(
            bundle_db=bundle_db, gate_db=gate_db, promo_db=promo_db,
        ))
        return config, bundle_db, gate_db, promo_db

    def test_promotion_request_success(self, tmp_path):
        """promotion request command succeeds."""
        config, *_ = self._write_config(tmp_path)
        rc, out, err = _run_cli(
            "policy", "promotion", "request",
            "--config", config,
            "--bundle-id", "pb_test",
            "--actor-id", "alice",
            "--permissions", "policy.promotion.request",
            "--reason", "Ready for release",
        )
        assert rc == 0, f"stderr: {err}"
        assert "promotion_id" in out
        assert "pending" in out

    def test_promotion_request_permission_denied(self, tmp_path):
        """promotion request fails without correct permission."""
        config, *_ = self._write_config(tmp_path)
        rc, out, err = _run_cli(
            "policy", "promotion", "request",
            "--config", config,
            "--bundle-id", "pb_test",
            "--actor-id", "alice",
            "--permissions", "policy.bundle.create",
            "--reason", "hacking",
        )
        assert rc != 0
        assert "Permission denied" in err or "Permission denied" in out

    def test_promotion_list_empty(self, tmp_path):
        """promotion list shows no requests when empty."""
        config, *_ = self._write_config(tmp_path)
        rc, out, err = _run_cli(
            "policy", "promotion", "list",
            "--config", config,
        )
        assert rc == 0
        assert "No promotion requests" in out or "pending" in out

    def test_promotion_approve(self, tmp_path):
        """promotion approve transitions request to approved."""
        config, *_ = self._write_config(tmp_path)
        # First create a request
        rc, out, err = _run_cli(
            "policy", "promotion", "request",
            "--config", config,
            "--bundle-id", "pb_test",
            "--actor-id", "alice",
            "--permissions", "policy.promotion.request",
            "--reason", "release",
        )
        assert rc == 0
        # Extract promotion_id from output
        promo_id = None
        for line in out.split("\n"):
            if line.startswith("promotion_id:"):
                promo_id = line.split(":", 1)[1].strip()
                break
        assert promo_id is not None

        rc, out, err = _run_cli(
            "policy", "promotion", "approve",
            "--config", config,
            "--promotion-id", promo_id,
            "--actor-id", "reviewer",
            "--permissions", "policy.promotion.approve",
            "--reason", "Looks good",
        )
        assert rc == 0, f"stderr: {err}"
        assert "approved" in out

    def test_promotion_reject(self, tmp_path):
        """promotion reject transitions request to rejected."""
        config, *_ = self._write_config(tmp_path)
        rc, out, _ = _run_cli(
            "policy", "promotion", "request",
            "--config", config,
            "--bundle-id", "pb_test",
            "--actor-id", "alice",
            "--permissions", "policy.promotion.request",
        )
        promo_id = None
        for line in out.split("\n"):
            if line.startswith("promotion_id:"):
                promo_id = line.split(":", 1)[1].strip()
                break
        rc, out, err = _run_cli(
            "policy", "promotion", "reject",
            "--config", config,
            "--promotion-id", promo_id,
            "--actor-id", "reviewer",
            "--permissions", "policy.promotion.reject",
            "--reason", "Too risky",
        )
        assert rc == 0, f"stderr: {err}"
        assert "rejected" in out

    def test_promotion_execute_pending_fails(self, tmp_path):
        """promotion execute fails for pending request."""
        config, *_ = self._write_config(tmp_path)
        rc, out, _ = _run_cli(
            "policy", "promotion", "request",
            "--config", config,
            "--bundle-id", "pb_test",
            "--actor-id", "alice",
            "--permissions", "policy.promotion.request",
        )
        promo_id = None
        for line in out.split("\n"):
            if line.startswith("promotion_id:"):
                promo_id = line.split(":", 1)[1].strip()
                break
        rc, out, err = _run_cli(
            "policy", "promotion", "execute",
            "--config", config,
            "--promotion-id", promo_id,
            "--actor-id", "release_manager",
            "--permissions", "policy.promotion.execute",
        )
        assert rc != 0
        assert "approved" in err or "approved" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_policy_release_cli.py::TestPolicyPromotionCLI -v`
Expected: FAIL — "No promotion subcommand" or argparse error

- [ ] **Step 3: Write minimal implementation**

```python
# In agent_app/cli.py, add promotion subcommands after the Phase 29 gate subcommands (after line 329):

    # Phase 30: policy promotion subcommands
    promotion_parser = policy_sub.add_parser("promotion", help="Policy promotion commands")
    promo_sub = promotion_parser.add_subparsers(dest="promotion_command")

    promo_request_parser = promo_sub.add_parser(
        "request", help="Request promotion of a policy bundle"
    )
    promo_request_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    promo_request_parser.add_argument(
        "--bundle-id", required=True, help="Bundle ID to promote"
    )
    promo_request_parser.add_argument(
        "--actor-id", required=True, help="Identity of the requester"
    )
    promo_request_parser.add_argument(
        "--permissions", action="append", default=[], help="Permissions (repeatable)"
    )
    promo_request_parser.add_argument(
        "--reason", default=None, help="Reason for promotion request"
    )
    promo_request_parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )

    promo_list_parser = promo_sub.add_parser(
        "list", help="List promotion requests"
    )
    promo_list_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    promo_list_parser.add_argument(
        "--status", default=None, help="Filter by status"
    )
    promo_list_parser.add_argument(
        "--limit", type=int, default=20, help="Max results"
    )
    promo_list_parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )

    promo_approve_parser = promo_sub.add_parser(
        "approve", help="Approve a promotion request"
    )
    promo_approve_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    promo_approve_parser.add_argument(
        "--promotion-id", required=True, help="Promotion request ID"
    )
    promo_approve_parser.add_argument(
        "--actor-id", required=True, help="Identity of the approver"
    )
    promo_approve_parser.add_argument(
        "--permissions", action="append", default=[], help="Permissions (repeatable)"
    )
    promo_approve_parser.add_argument(
        "--reason", default=None, help="Approval reason"
    )
    promo_approve_parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )

    promo_reject_parser = promo_sub.add_parser(
        "reject", help="Reject a promotion request"
    )
    promo_reject_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    promo_reject_parser.add_argument(
        "--promotion-id", required=True, help="Promotion request ID"
    )
    promo_reject_parser.add_argument(
        "--actor-id", required=True, help="Identity of the rejecter"
    )
    promo_reject_parser.add_argument(
        "--permissions", action="append", default=[], help="Permissions (repeatable)"
    )
    promo_reject_parser.add_argument(
        "--reason", default=None, help="Rejection reason"
    )
    promo_reject_parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )

    promo_execute_parser = promo_sub.add_parser(
        "execute", help="Execute an approved promotion"
    )
    promo_execute_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    promo_execute_parser.add_argument(
        "--promotion-id", required=True, help="Promotion request ID"
    )
    promo_execute_parser.add_argument(
        "--actor-id", required=True, help="Identity of the executor"
    )
    promo_execute_parser.add_argument(
        "--permissions", action="append", default=[], help="Permissions (repeatable)"
    )
    promo_execute_parser.add_argument(
        "--bypass-gate", action="store_true",
        help="Bypass gate check if enabled in config",
    )
    promo_execute_parser.add_argument(
        "--bypass-reason", default=None,
        help="Reason for gate bypass (required when --bypass-gate is used)",
    )
    promo_execute_parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )


# Add dispatch after the existing gate dispatch (after line 516):

    # Phase 30: policy promotion subcommands
    if args.command == "policy" and args.policy_command == "promotion":
        if args.promotion_command == "request":
            return asyncio.run(_cmd_policy_promotion_request(args))
        if args.promotion_command == "list":
            return asyncio.run(_cmd_policy_promotion_list(args))
        if args.promotion_command == "approve":
            return asyncio.run(_cmd_policy_promotion_approve(args))
        if args.promotion_command == "reject":
            return asyncio.run(_cmd_policy_promotion_reject(args))
        if args.promotion_command == "execute":
            return asyncio.run(_cmd_policy_promotion_execute(args))
```

Then add the command implementations at the end of the file:

```python
# -- Phase 30: Policy Promotion CLI commands --


def _build_context(actor_id: str, permissions: list[str], tenant_id: str = "default") -> RunContext:
    """Build a RunContext from CLI args."""
    return RunContext(
        run_id=f"cli_{actor_id}",
        user_id=actor_id,
        tenant_id=tenant_id,
        permissions=permissions,
    )


async def _cmd_policy_promotion_request(args: argparse.Namespace) -> int:
    """Request promotion of a policy bundle."""
    from agent_app.config.loader import build_app
    from agent_app.core.context import RunContext

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions)
    try:
        req = await service.request_promotion(
            bundle_id=args.bundle_id,
            requested_by=args.actor_id,
            context=context,
            reason=args.reason,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error requesting promotion: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "promotion_id": req.promotion_id,
            "bundle_id": req.bundle_id,
            "status": req.status,
            "requested_by": req.requested_by,
            "reason": req.reason,
            "created_at": req.created_at.isoformat(),
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print("Promotion request created")
        print()
        print(f"Promotion ID:  {req.promotion_id}")
        print(f"Bundle ID:     {req.bundle_id}")
        print(f"Status:        {req.status}")
        print(f"Requested By:  {req.requested_by}")
        if req.reason:
            print(f"Reason:        {req.reason}")
    return 0


async def _cmd_policy_promotion_list(args: argparse.Namespace) -> int:
    """List promotion requests."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    store = _get_promotion_store(app)
    if store is None:
        print("Promotion store not configured.", file=sys.stderr)
        return 1

    status = None
    if args.status:
        from agent_app.governance.policy_promotion import PromotionRequestStatus
        try:
            status = PromotionRequestStatus(args.status)
        except ValueError:
            print(f"Invalid status: '{args.status}'. Valid: pending, approved, rejected, executed, cancelled", file=sys.stderr)
            return 1

    requests = await store.list(status=status)

    if not requests:
        print("No promotion requests found.")
        return 0

    if args.json:
        data = []
        for r in requests:
            data.append({
                "promotion_id": r.promotion_id,
                "bundle_id": r.bundle_id,
                "status": r.status,
                "requested_by": r.requested_by,
                "resolved_by": r.resolved_by,
                "executed_by": r.executed_by,
                "reason": r.reason,
                "created_at": r.created_at.isoformat(),
            })
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"{'Promotion ID':<20} {'Bundle ID':<20} {'Status':<12} {'Requested By':<15} {'Created'}")
        print("-" * 85)
        for r in requests:
            print(
                f"{r.promotion_id:<20} {r.bundle_id:<20} "
                f"{r.status:<12} {r.requested_by:<15} "
                f"{r.created_at.isoformat()[:19]}"
            )
    return 0


async def _cmd_policy_promotion_approve(args: argparse.Namespace) -> int:
    """Approve a promotion request."""
    from agent_app.config.loader import build_app
    from agent_app.core.context import RunContext

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions)
    try:
        req = await service.approve_promotion(
            promotion_id=args.promotion_id,
            approved_by=args.actor_id,
            context=context,
            reason=args.reason,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error approving promotion: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "promotion_id": req.promotion_id,
            "bundle_id": req.bundle_id,
            "status": req.status,
            "resolved_by": req.resolved_by,
            "approval_reason": req.approval_reason,
            "resolved_at": req.resolved_at.isoformat() if req.resolved_at else None,
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print("Promotion request approved")
        print()
        print(f"Promotion ID:  {req.promotion_id}")
        print(f"Bundle ID:     {req.bundle_id}")
        print(f"Status:        {req.status}")
        print(f"Approved By:   {req.resolved_by}")
        if req.approval_reason:
            print(f"Reason:        {req.approval_reason}")
    return 0


async def _cmd_policy_promotion_reject(args: argparse.Namespace) -> int:
    """Reject a promotion request."""
    from agent_app.config.loader import build_app
    from agent_app.core.context import RunContext

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions)
    try:
        req = await service.reject_promotion(
            promotion_id=args.promotion_id,
            rejected_by=args.actor_id,
            context=context,
            reason=args.reason,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error rejecting promotion: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "promotion_id": req.promotion_id,
            "bundle_id": req.bundle_id,
            "status": req.status,
            "resolved_by": req.resolved_by,
            "rejection_reason": req.rejection_reason,
            "resolved_at": req.resolved_at.isoformat() if req.resolved_at else None,
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print("Promotion request rejected")
        print()
        print(f"Promotion ID:  {req.promotion_id}")
        print(f"Bundle ID:     {req.bundle_id}")
        print(f"Status:        {req.status}")
        print(f"Rejected By:   {req.resolved_by}")
        if req.rejection_reason:
            print(f"Reason:        {req.rejection_reason}")
    return 0


async def _cmd_policy_promotion_execute(args: argparse.Namespace) -> int:
    """Execute an approved promotion."""
    from agent_app.config.loader import build_app
    from agent_app.core.context import RunContext

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions)
    try:
        bundle = await service.execute_promotion(
            promotion_id=args.promotion_id,
            executed_by=args.actor_id,
            context=context,
            bypass_gate=args.bypass_gate,
            bypass_reason=args.bypass_reason,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error executing promotion: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "bundle_id": bundle.bundle_id,
            "name": bundle.name,
            "version": bundle.version,
            "status": bundle.status,
            "activated_at": bundle.activated_at.isoformat() if bundle.activated_at else None,
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print("Promotion executed — bundle activated")
        print()
        print(f"Bundle ID:    {bundle.bundle_id}")
        print(f"Name:         {bundle.name}")
        print(f"Version:      {bundle.version}")
        print(f"Status:       {bundle.status}")
    return 0


def _get_promotion_store(app: Any) -> Any:
    """Get the promotion request store from the app."""
    service = _get_release_service(app)
    if service is not None:
        return getattr(service, "promotion_store", None)
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_policy_release_cli.py -v`
Expected: All CLI tests pass (Phase 29 + Phase 30)

- [ ] **Step 5: Commit**

```bash
git add agent_app/cli.py tests/unit/test_policy_release_cli.py
git commit -m "feat: Phase 30 Task 6 — CLI promotion request/approve/reject/execute commands"
```

---

## Task 7: Console promotion pages and write actions

**Files:**
- Modify: `agent_app/console/router.py`
- Create: `agent_app/console/templates/policy_promotions.html`
- Create: `agent_app/console/templates/policy_promotion_detail.html`
- Modify: `agent_app/adapters/fastapi.py`
- Modify: `tests/unit/test_policy_release_console.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/unit/test_policy_release_console.py:

_BASE_CONFIG_30 = """
app:
  name: test
  environment: dev
governance:
  policies:
    enabled: true
    default_action: allow
    rules: []
  policy_decisions:
    type: memory
  policy_release:
    bundles:
      type: sqlite
      path: {bundle_db}
    gates:
      type: sqlite
      path: {gate_db}
    promotions:
      type: sqlite
      path: {promo_db}
    require_promotion_approval: true
    allow_gate_bypass: false
    rules:
      - name: safe_default
        max_changed_ratio: 0.10
        max_failed_replays: 0
"""


class TestPromotionConsoleRouter:
    """Tests for Phase 30 console promotion pages."""

    def test_promotions_page_returns_200(self):
        """Promotions list page returns 200."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.governance.policy_promotion import InMemoryPromotionRequestStore

        store = InMemoryPromotionRequestStore()
        router = build_policy_console_router(
            store=None, config=PolicyConsoleConfig(enabled=True),
            bundle_store=None, gate_store=None,
            promotion_store=store,
        )
        api.include_router(router, prefix="/policy-console")
        client = self._get_client(api)
        resp = client.get("/policy-console/promotions")
        assert resp.status_code == 200
        assert "Promotion Requests" in resp.text or "promotions" in resp.text

    def test_promotions_detail_page_returns_200(self):
        """Promotion detail page returns 200."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.governance.policy_promotion import (
            InMemoryPromotionRequestStore,
            PromotionRequest,
            PromotionRequestStatus,
        )
        from datetime import datetime, timezone

        store = InMemoryPromotionRequestStore()
        req = PromotionRequest(
            promotion_id="pr_test123",
            bundle_id="pb_001",
            requested_by="alice",
            status=PromotionRequestStatus.PENDING,
            reason="Release it",
            created_at=datetime.now(timezone.utc),
        )
        await store.create(req)

        router = build_policy_console_router(
            store=None, config=PolicyConsoleConfig(enabled=True),
            bundle_store=None, gate_store=None,
            promotion_store=store,
        )
        api.include_router(router, prefix="/policy-console")
        client = self._get_client(api)
        resp = client.get("/policy-console/promotions/pr_test123")
        assert resp.status_code == 200

    def test_promotion_detail_not_found(self):
        """Promotion detail page handles missing promotion gracefully."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.governance.policy_promotion import InMemoryPromotionRequestStore

        store = InMemoryPromotionRequestStore()
        router = build_policy_console_router(
            store=None, config=PolicyConsoleConfig(enabled=True),
            bundle_store=None, gate_store=None,
            promotion_store=store,
        )
        api.include_router(router, prefix="/policy-console")
        client = self._get_client(api)
        resp = client.get("/policy-console/promotions/pr_nonexistent")
        assert resp.status_code == 200
        assert "not found" in resp.text.lower() or "error" in resp.text.lower()

    def test_create_promotion_post(self):
        """POST to /promotions creates a promotion request."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.governance.policy_promotion import InMemoryPromotionRequestStore

        store = InMemoryPromotionRequestStore()
        router = build_policy_console_router(
            store=None, config=PolicyConsoleConfig(enabled=True),
            bundle_store=None, gate_store=None,
            promotion_store=store,
        )
        api.include_router(router, prefix="/policy-console")
        client = self._get_client(api)
        resp = client.post("/policy-console/promotions", data={
            "bundle_id": "pb_001",
            "requested_by": "alice",
            "reason": "Console request",
        })
        assert resp.status_code in (200, 302)
        requests = await store.list()
        assert len(requests) == 1
        assert requests[0].bundle_id == "pb_001"

    def test_approve_post(self):
        """POST to /promotions/{id}/approve approves a request."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.governance.policy_promotion import (
            InMemoryPromotionRequestStore,
            PromotionRequest,
            PromotionRequestStatus,
        )
        from datetime import datetime, timezone

        store = InMemoryPromotionRequestStore()
        req = PromotionRequest(
            promotion_id="pr_test",
            bundle_id="pb_001",
            requested_by="alice",
            status=PromotionRequestStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        await store.create(req)

        router = build_policy_console_router(
            store=None, config=PolicyConsoleConfig(enabled=True),
            bundle_store=None, gate_store=None,
            promotion_store=store,
        )
        api.include_router(router, prefix="/policy-console")
        client = self._get_client(api)
        resp = client.post("/policy-console/promotions/pr_test/approve", data={
            "approved_by": "reviewer",
            "reason": "Looks good",
        })
        assert resp.status_code in (200, 302)
        updated = await store.get("pr_test")
        assert updated.status == PromotionRequestStatus.APPROVED

    def test_reject_post(self):
        """POST to /promotions/{id}/reject rejects a request."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.governance.policy_promotion import (
            InMemoryPromotionRequestStore,
            PromotionRequest,
            PromotionRequestStatus,
        )
        from datetime import datetime, timezone

        store = InMemoryPromotionRequestStore()
        req = PromotionRequest(
            promotion_id="pr_test",
            bundle_id="pb_001",
            requested_by="alice",
            status=PromotionRequestStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        await store.create(req)

        router = build_policy_console_router(
            store=None, config=PolicyConsoleConfig(enabled=True),
            bundle_store=None, gate_store=None,
            promotion_store=store,
        )
        api.include_router(router, prefix="/policy-console")
        client = self._get_client(api)
        resp = client.post("/policy-console/promotions/pr_test/reject", data={
            "rejected_by": "reviewer",
            "reason": "Too risky",
        })
        assert resp.status_code in (200, 302)
        updated = await store.get("pr_test")
        assert updated.status == PromotionRequestStatus.REJECTED

    def test_execute_post(self):
        """POST to /promotions/{id}/execute executes an approved request."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.governance.policy_promotion import (
            InMemoryPromotionRequestStore,
            PromotionRequest,
            PromotionRequestStatus,
        )
        from agent_app.governance.policy_bundle import (
            InMemoryPolicyBundleStore,
            PolicyBundle,
            PolicyBundleStatus,
        )
        from agent_app.governance.policy_gate import PolicyGateEvaluator, PolicyGateRule
        from agent_app.runtime.policy_gate_store import InMemoryPolicyGateStore
        from agent_app.runtime.policy_release import PolicyReleaseService
        from datetime import datetime, timezone

        bundle_store = InMemoryPolicyBundleStore()
        gate_store = InMemoryPolicyGateStore()
        promo_store = InMemoryPromotionRequestStore()

        # Create a bundle and run a passing gate
        bundle = PolicyBundle(
            bundle_id="pb_001",
            name="test",
            version="1.0.0",
            config_hash="abc",
            created_at=datetime.now(timezone.utc),
        )
        await bundle_store.create(bundle)

        from agent_app.governance.policy_replay import (
            PolicyReplayResult, PolicyReplayRun, PolicyReplayStatus,
            PolicyReplayDecisionChange,
        )
        run = PolicyReplayRun(
            replay_id="replay_1",
            status=PolicyReplayStatus.COMPLETED,
            source_decision_count=10,
            changed_count=1,
            unchanged_count=9,
            failed_count=0,
            created_at=datetime.now(timezone.utc),
        )
        changes = [PolicyReplayDecisionChange(
            decision_id="dec_1", original_action="allow",
            replayed_action="allow", changed=False,
        ) for _ in range(10)]
        replay_result = PolicyReplayResult(replay=run, changes=changes)
        gate_result = PolicyGateEvaluator(rules=[
            PolicyGateRule(name="safe_default", max_changed_ratio=0.10, max_failed_replays=0),
        ]).evaluate(bundle, replay_result)
        await gate_store.save(gate_result)

        req = PromotionRequest(
            promotion_id="pr_test",
            bundle_id="pb_001",
            gate_result_id=gate_result.gate_result_id,
            requested_by="alice",
            status=PromotionRequestStatus.APPROVED,
            created_at=datetime.now(timezone.utc),
        )
        await promo_store.create(req)

        service = PolicyReleaseService(
            bundle_store=bundle_store,
            replay_runner=None,
            replay_store=None,
            gate_evaluator=PolicyGateEvaluator(rules=[
                PolicyGateRule(name="safe_default", max_changed_ratio=0.10, max_failed_replays=0),
            ]),
            gate_store=gate_store,
            promotion_store=promo_store,
        )

        router = build_policy_console_router(
            store=None, config=PolicyConsoleConfig(enabled=True),
            bundle_store=bundle_store, gate_store=gate_store,
            promotion_store=promo_store,
            release_service=service,
        )
        api.include_router(router, prefix="/policy-console")
        client = self._get_client(api)
        resp = client.post("/policy-console/promotions/pr_test/execute", data={
            "executed_by": "release_manager",
        })
        assert resp.status_code in (200, 302)
        updated = await promo_store.get("pr_test")
        assert updated.status == PromotionRequestStatus.EXECUTED
        active = await bundle_store.get_active()
        assert active.bundle_id == "pb_001"

    def test_permission_error_renders_cleanly(self):
        """Permission errors render as page messages, not tracebacks."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.governance.policy_promotion import InMemoryPromotionRequestStore

        store = InMemoryPromotionRequestStore()
        # No release_service provided — POST will show error gracefully
        router = build_policy_console_router(
            store=None, config=PolicyConsoleConfig(enabled=True),
            bundle_store=None, gate_store=None,
            promotion_store=store,
            release_service=None,
        )
        api.include_router(router, prefix="/policy-console")
        client = self._get_client(api)
        resp = client.post("/policy-console/promotions", data={
            "bundle_id": "pb_001",
            "requested_by": "alice",
        })
        # Should return 200 with error message, not 500
        assert resp.status_code == 200
        assert "Traceback" not in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_policy_release_console.py::TestPromotionConsoleRouter -v`
Expected: FAIL — promotion_store parameter not accepted, no promotion routes

- [ ] **Step 3: Write minimal implementation**

```python
# In agent_app/console/router.py:

# 1. Update function signature to accept promotion_store and release_service:
def build_policy_console_router(
    store: PolicyDecisionStore | None,
    config: Any = None,
    replay_store: PolicyReplayStore | None = None,
    replay_job_store: Any = None,
    bundle_store: Any = None,
    gate_store: Any = None,
    promotion_store: Any = None,
    release_service: Any = None,
) -> APIRouter:

# 2. Add promotion list route (before return router):
    @router.get("/promotions", response_class=HTMLResponse)
    async def promotions_index(request: Request):
        """Policy promotion requests list."""
        promotions_list: list[dict] = []
        if promotion_store is not None:
            requests = await promotion_store.list(limit=page_size)
            for r in requests:
                promotions_list.append(_promotion_to_row(r))
        return templates.TemplateResponse(
            request,
            "policy_promotions.html",
            {
                "title": title,
                "base_path": base_path,
                "promotions": promotions_list,
                "store_available": promotion_store is not None,
            },
        )

    @router.get("/promotions/{promotion_id}", response_class=HTMLResponse)
    async def promotion_detail(request: Request, promotion_id: str):
        """Single promotion request detail."""
        if promotion_store is None:
            return templates.TemplateResponse(
                request,
                "policy_promotion_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "store_available": False,
                    "promotion": None,
                    "error": "Promotion store not configured.",
                },
            )
        req = await promotion_store.get(promotion_id)
        if req is None:
            return templates.TemplateResponse(
                request,
                "policy_promotion_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "store_available": True,
                    "promotion": None,
                    "error": f"Promotion request '{promotion_id}' not found.",
                },
            )
        return templates.TemplateResponse(
            request,
            "policy_promotion_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": True,
                "promotion": _promotion_to_detail(req),
                "error": None,
            },
        )

    # POST routes for write actions
    @router.post("/promotions", response_class=HTMLResponse)
    async def create_promotion(request: Request):
        """Create a new promotion request."""
        error_msg = None
        created_request = None
        if release_service is None:
            error_msg = "Policy release service not configured."
        else:
            try:
                form = await request.form()
                bundle_id = form.get("bundle_id", "")
                requested_by = form.get("requested_by", "")
                reason = form.get("reason") or None
                if not bundle_id or not requested_by:
                    error_msg = "bundle_id and requested_by are required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{requested_by}",
                        user_id=requested_by,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    created_request = await release_service.request_promotion(
                        bundle_id=bundle_id,
                        requested_by=requested_by,
                        context=context,
                        reason=reason,
                    )
            except Exception as exc:
                error_msg = str(exc)
        return templates.TemplateResponse(
            request,
            "policy_promotions.html",
            {
                "title": title,
                "base_path": base_path,
                "promotions": [],
                "store_available": promotion_store is not None,
                "error": error_msg,
                "created_request": created_request,
            },
        )

    @router.post("/promotions/{promotion_id}/approve", response_class=HTMLResponse)
    async def approve_promotion(request: Request, promotion_id: str):
        """Approve a promotion request."""
        error_msg = None
        updated = None
        if release_service is None:
            error_msg = "Policy release service not configured."
        else:
            try:
                form = await request.form()
                approved_by = form.get("approved_by", "")
                reason = form.get("reason") or None
                if not approved_by:
                    error_msg = "approved_by is required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{approved_by}",
                        user_id=approved_by,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    updated = await release_service.approve_promotion(
                        promotion_id=promotion_id,
                        approved_by=approved_by,
                        context=context,
                        reason=reason,
                    )
            except PermissionError as exc:
                error_msg = f"Permission denied: {exc}"
            except Exception as exc:
                error_msg = str(exc)
        return templates.TemplateResponse(
            request,
            "policy_promotion_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": promotion_store is not None,
                "promotion": updated if updated else (await promotion_store.get(promotion_id) if promotion_store else None),
                "error": error_msg,
            },
        )

    @router.post("/promotions/{promotion_id}/reject", response_class=HTMLResponse)
    async def reject_promotion(request: Request, promotion_id: str):
        """Reject a promotion request."""
        error_msg = None
        updated = None
        if release_service is None:
            error_msg = "Policy release service not configured."
        else:
            try:
                form = await request.form()
                rejected_by = form.get("rejected_by", "")
                reason = form.get("reason") or None
                if not rejected_by:
                    error_msg = "rejected_by is required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{rejected_by}",
                        user_id=rejected_by,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    updated = await release_service.reject_promotion(
                        promotion_id=promotion_id,
                        rejected_by=rejected_by,
                        context=context,
                        reason=reason,
                    )
            except PermissionError as exc:
                error_msg = f"Permission denied: {exc}"
            except Exception as exc:
                error_msg = str(exc)
        return templates.TemplateResponse(
            request,
            "policy_promotion_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": promotion_store is not None,
                "promotion": updated if updated else (await promotion_store.get(promotion_id) if promotion_store else None),
                "error": error_msg,
            },
        )

    @router.post("/promotions/{promotion_id}/execute", response_class=HTMLResponse)
    async def execute_promotion(request: Request, promotion_id: str):
        """Execute an approved promotion."""
        error_msg = None
        result = None
        if release_service is None:
            error_msg = "Policy release service not configured."
        else:
            try:
                form = await request.form()
                executed_by = form.get("executed_by", "")
                bypass_gate = form.get("bypass_gate") == "on"
                bypass_reason = form.get("bypass_reason") or None
                if not executed_by:
                    error_msg = "executed_by is required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{executed_by}",
                        user_id=executed_by,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    result = await release_service.execute_promotion(
                        promotion_id=promotion_id,
                        executed_by=executed_by,
                        context=context,
                        bypass_gate=bypass_gate,
                        bypass_reason=bypass_reason,
                    )
            except PermissionError as exc:
                error_msg = f"Permission denied: {exc}"
            except (KeyError, ValueError) as exc:
                error_msg = str(exc)
            except Exception as exc:
                error_msg = str(exc)
        return templates.TemplateResponse(
            request,
            "policy_promotion_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": promotion_store is not None,
                "promotion": await promotion_store.get(promotion_id) if promotion_store else None,
                "error": error_msg,
                "executed_bundle": result,
            },
        )

# 3. Add helper functions at the bottom (before _get_templates_dir):

def _promotion_to_row(req: Any) -> dict:
    """Convert PromotionRequest to a table row dict."""
    created = req.created_at
    if hasattr(created, "isoformat"):
        created = created.isoformat()
    resolved = req.resolved_at
    if hasattr(resolved, "isoformat"):
        resolved = resolved.isoformat()
    return {
        "promotion_id": req.promotion_id,
        "bundle_id": req.bundle_id,
        "status": req.status,
        "requested_by": req.requested_by,
        "resolved_by": req.resolved_by or "—",
        "created_at": created,
        "resolved_at": resolved or "—",
        "reason": req.reason or "—",
    }


def _promotion_to_detail(req: Any) -> dict:
    """Convert PromotionRequest to a detail page dict."""
    created = req.created_at
    if hasattr(created, "isoformat"):
        created = created.isoformat()
    resolved = req.resolved_at
    if hasattr(resolved, "isoformat"):
        resolved = resolved.isoformat()
    executed = req.executed_at
    if hasattr(executed, "isoformat"):
        executed = executed.isoformat()
    return {
        "promotion_id": req.promotion_id,
        "bundle_id": req.bundle_id,
        "gate_result_id": req.gate_result_id or "—",
        "status": req.status,
        "requested_by": req.requested_by,
        "tenant_id": req.tenant_id or "—",
        "reason": req.reason or "—",
        "approval_reason": req.approval_reason or "—",
        "rejection_reason": req.rejection_reason or "—",
        "resolved_by": req.resolved_by or "—",
        "resolved_at": resolved,
        "executed_by": req.executed_by or "—",
        "executed_at": executed,
        "created_at": created,
    }
```

```html
<!-- agent_app/console/templates/policy_promotions.html -->
{% extends "base.html" %}

{% block content %}
<h1>Promotion Requests</h1>

{% if error %}
<div class="error-state">
  <h2>Error</h2>
  <p>{{ error }}</p>
</div>
{% elif not store_available %}
<div class="empty-state">
  <p>Promotion store not configured.</p>
</div>
{% else %}
<table class="data-table">
  <thead>
    <tr>
      <th>Promotion ID</th>
      <th>Bundle ID</th>
      <th>Status</th>
      <th>Requested By</th>
      <th>Resolved By</th>
      <th>Created</th>
      <th>Action</th>
    </tr>
  </thead>
  <tbody>
    {% for p in promotions %}
    <tr>
      <td><a href="{{ base_path }}/promotions/{{ p.promotion_id }}">{{ p.promotion_id }}</a></td>
      <td><a href="{{ base_path }}/bundles/{{ p.bundle_id }}">{{ p.bundle_id }}</a></td>
      <td>{{ p.status }}</td>
      <td>{{ p.requested_by }}</td>
      <td>{{ p.resolved_by }}</td>
      <td>{{ p.created_at }}</td>
      <td>
        {% if p.status == 'pending' %}
        <a href="{{ base_path }}/promotions/{{ p.promotion_id }}">Review</a>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>

{% if not promotions %}
<div class="empty-state">
  <p>No promotion requests found.</p>
</div>
{% endif %}

<div class="detail-section" style="margin-top: 2rem;">
  <h3>Create Promotion Request</h3>
  <form method="post" action="{{ base_path }}/promotions">
    <label>Bundle ID: <input type="text" name="bundle_id" required></label><br>
    <label>Requested By: <input type="text" name="requested_by" required></label><br>
    <label>Reason: <input type="text" name="reason"></label><br>
    <label>Permissions (comma-separated): <input type="text" name="permissions" value="policy.promotion.request"></label><br>
    <button type="submit">Create Request</button>
  </form>
</div>
{% endif %}

<p style="margin-top: 1rem;">
  <a href="{{ base_path }}/bundles">← Bundles</a>
</p>
{% endblock %}
```

```html
<!-- agent_app/console/templates/policy_promotion_detail.html -->
{% extends "base.html" %}

{% block content %}
<h1>Promotion Request Detail</h1>

{% if error %}
<div class="error-state">
  <h2>{% if promotion %}Action Result{% else %}Error{% endif %}</h2>
  <p>{{ error }}</p>
</div>
{% elif not promotion %}
<div class="empty-state">
  <p>Loading promotion request...</p>
</div>
{% else %}
<div class="detail-header">
  <h2>{{ promotion.promotion_id }}</h2>
  <span class="badge badge-allow">{{ promotion.status }}</span>
</div>

<dl class="detail-fields">
  <dt>Promotion ID</dt>
  <dd><code>{{ promotion.promotion_id }}</code></dd>

  <dt>Bundle ID</dt>
  <dd><a href="{{ base_path }}/bundles/{{ promotion.bundle_id }}">{{ promotion.bundle_id }}</a></dd>

  <dt>Gate Result ID</dt>
  <dd>{{ promotion.gate_result_id }}</dd>

  <dt>Status</dt>
  <dd>{{ promotion.status }}</dd>

  <dt>Requested By</dt>
  <dd>{{ promotion.requested_by }}</dd>

  <dt>Tenant ID</dt>
  <dd>{{ promotion.tenant_id }}</dd>

  <dt>Reason</dt>
  <dd>{{ promotion.reason or '—' }}</dd>

  <dt>Approval Reason</dt>
  <dd>{{ promotion.approval_reason or '—' }}</dd>

  <dt>Rejection Reason</dt>
  <dd>{{ promotion.rejection_reason or '—' }}</dd>

  <dt>Resolved By</dt>
  <dd>{{ promotion.resolved_by or '—' }}</dd>

  <dt>Resolved At</dt>
  <dd>{{ promotion.resolved_at or '—' }}</dd>

  <dt>Executed By</dt>
  <dd>{{ promotion.executed_by or '—' }}</dd>

  <dt>Executed At</dt>
  <dd>{{ promotion.executed_at or '—' }}</dd>

  <dt>Created At</dt>
  <dd>{{ promotion.created_at }}</dd>
</dl>

{% if promotion.status == 'pending' %}
<div class="detail-section">
  <h3>Approve</h3>
  <form method="post" action="{{ base_path }}/promotions/{{ promotion.promotion_id }}/approve">
    <label>Approved By: <input type="text" name="approved_by" required></label><br>
    <label>Reason: <input type="text" name="reason"></label><br>
    <label>Permissions: <input type="text" name="permissions" value="policy.promotion.approve"></label><br>
    <button type="submit">Approve</button>
  </form>
</div>

<div class="detail-section">
  <h3>Reject</h3>
  <form method="post" action="{{ base_path }}/promotions/{{ promotion.promotion_id }}/reject">
    <label>Rejected By: <input type="text" name="rejected_by" required></label><br>
    <label>Reason: <input type="text" name="reason"></label><br>
    <label>Permissions: <input type="text" name="permissions" value="policy.promotion.reject"></label><br>
    <button type="submit">Reject</button>
  </form>
</div>
{% endif %}

{% if promotion.status == 'approved' %}
<div class="detail-section">
  <h3>Execute Promotion</h3>
  <form method="post" action="{{ base_path }}/promotions/{{ promotion.promotion_id }}/execute">
    <label>Executed By: <input type="text" name="executed_by" required></label><br>
    <label>Permissions: <input type="text" name="permissions" value="policy.promotion.execute"></label><br>
    <button type="submit">Execute</button>
  </form>
</div>
{% endif %}

{% if executed_bundle %}
<div class="detail-section">
  <h3>Execution Result</h3>
  <p>Bundle activated: {{ executed_bundle.bundle_id }} (status: {{ executed_bundle.status }})</p>
</div>
{% endif %}

<p style="margin-top: 1rem;">
  <a href="{{ base_path }}/promotions">← Back to Promotion Requests</a>
</p>
{% endif %}
{% endblock %}
```

```python
# In agent_app/adapters/fastapi.py, add promotion_store extraction:

def _get_promotion_store(agent_app: Any) -> Any:
    release_service = getattr(agent_app, "_release_service", None)
    if release_service is not None:
        return getattr(release_service, "promotion_store", None)
    return None
```

And pass `promotion_store` and `release_service` to the console router builder in the FastAPI adapter's console setup.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_policy_release_console.py -v`
Expected: All console tests pass

- [ ] **Step 5: Commit**

```bash
git add agent_app/console/router.py agent_app/console/templates/policy_promotions.html \
    agent_app/console/templates/policy_promotion_detail.html \
    agent_app/adapters/fastapi.py tests/unit/test_policy_release_console.py
git commit -m "feat: Phase 30 Task 7 — Console promotion pages and write actions"
```

---

## Task 8: Base template nav link + documentation + final verification

**Files:**
- Modify: `agent_app/console/templates/base.html`
- Modify: `docs/policy_release.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Create: `docs/release_checklist_phase30.md`

- [ ] **Step 1: Update base.html nav**

Add a "Promotions" nav link next to "Bundles" and "Gates":

```html
<li><a href="{{ base_path }}/promotions">Promotions</a></li>
```

- [ ] **Step 2: Update documentation**

Update `docs/policy_release.md` with Phase 30 sections:
- Promotion Approval Lifecycle
- Policy Release Permissions
- Gate Bypass Rules
- CLI examples for promotion flow
- Console promotion pages
- Current limitations

- [ ] **Step 3: Update CHANGELOG and README**

CHANGELOG.md: Add Phase 30 section (0.18.0)
README.md: Add v0.18 to roadmap

- [ ] **Step 4: Run full test suite**

```bash
pytest tests/unit/ -v --tb=short 2>&1 | tail -30
```

Expected: All Phase 30 tests pass, no regressions in Phase 29 tests

- [ ] **Step 5: Commit**

```bash
git add agent_app/console/templates/base.html docs/policy_release.md \
    CHANGELOG.md README.md docs/release_checklist_phase30.md
git commit -m "feat: Phase 30 Task 8 — Documentation, nav link, release checklist"
```

---

## Summary of Changes

### New Files (10)
1. `agent_app/governance/policy_rbac.py` — PolicyReleasePermission, PolicyReleasePermissionChecker
2. `agent_app/governance/policy_promotion.py` — PromotionRequestStatus, PromotionRequest
3. `agent_app/runtime/promotion_store.py` — PromotionRequestStore protocol + InMemory + SQLite
4. `tests/unit/test_policy_rbac.py` — 3 RBAC tests
5. `tests/unit/test_policy_promotion.py` — 8 model tests
6. `tests/unit/test_policy_promotion_store.py` — 15 store tests
7. `agent_app/console/templates/policy_promotions.html` — promotions list page
8. `agent_app/console/templates/policy_promotion_detail.html` — promotion detail + forms
9. `docs/release_checklist_phase30.md` — release checklist

### Modified Files (7)
1. `agent_app/runtime/policy_release.py` — extended with RBAC + promotion lifecycle + audit
2. `agent_app/config/schema.py` — added promotions config + require_promotion_approval + allow_gate_bypass
3. `agent_app/config/loader.py` — wired promotion_store into release service
4. `agent_app/cli.py` — added promotion request/list/approve/reject/execute
5. `agent_app/console/router.py` — added promotion pages + POST routes
6. `agent_app/adapters/fastapi.py` — pass promotion_store to console router
7. `tests/unit/test_policy_release.py` — added RBAC + promotion lifecycle tests
8. `tests/unit/test_policy_release_cli.py` — added promotion CLI tests
9. `tests/unit/test_policy_release_console.py` — added promotion console tests
10. `docs/policy_release.md` — Phase 30 documentation
11. `CHANGELOG.md` — Phase 30 section
12. `README.md` — v0.18 roadmap entry
13. `agent_app/console/templates/base.html` — Promotions nav link

### Test Count
- ~50+ new tests (3 RBAC + 8 model + 15 store + 10 release service + 6 CLI + 8 console)
- All Phase 29 tests continue to pass
- Total: ~140+ tests for policy release system

### Acceptance Criteria Checklist
- [ ] Full test suite passes
- [ ] PolicyReleasePermissionChecker implemented
- [ ] PromotionRequest model implemented
- [ ] InMemoryPromotionRequestStore implemented
- [ ] SQLitePromotionRequestStore implemented
- [ ] PolicyReleaseService supports request/approve/reject/execute
- [ ] CLI supports promotion lifecycle
- [ ] Console supports promotion pages and POST actions
- [ ] Promotion execute activates approved bundle
- [ ] Failed gate blocks execute by default
- [ ] Bypass requires config + permission + reason
- [ ] Audit events are written
- [ ] Docs and changelog updated
- [ ] No optional dependency boundary regressions
