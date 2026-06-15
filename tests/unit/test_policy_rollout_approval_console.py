"""Phase 36 Task 8: Tests for console rollout approval pages."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from conftest import _run_async

from agent_app.config.schema import PolicyConsoleConfig
from agent_app.governance.policy_rollout import (
    RolloutPlan,
    RolloutPlanStatus,
    RolloutStep,
    RolloutStepStatus,
    RolloutStepType,
)
from agent_app.governance.policy_rollout_approval import (
    RolloutStepApproval,
    RolloutStepApprovalStatus,
)
from agent_app.runtime.policy_rollout_store import InMemoryRolloutPlanStore
from agent_app.runtime.policy_rollout_approval_store import InMemoryRolloutStepApprovalStore


class TestRolloutApprovalConsoleRouter:
    """Tests for Phase 36 Task 8 console rollout approval pages."""

    def _make_app(self):
        """Create a minimal FastAPI app for console testing."""
        from agent_app import AgentApp
        from agent_app.governance.approval import InMemoryApprovalStore
        from agent_app.governance.audit import InMemoryAuditLogger
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.adapters.fastapi import create_fastapi_app

        ar = AgentRegistry()
        tr = ToolRegistry()
        wr = WorkflowRegistry()
        app = AgentApp(
            registry=type("B", (), {"agent_registry": ar, "tool_registry": tr, "workflow_registry": wr})()
        )
        app.agent_registry = ar
        app.tool_registry = tr
        app.workflow_registry = wr
        app.approval_store = InMemoryApprovalStore()
        app.audit_logger = InMemoryAuditLogger()
        return create_fastapi_app(app)

    def _get_client(self, api):
        from starlette.testclient import TestClient
        return TestClient(api)

    def _make_approval(self, approval_id="rsa_test123", status=RolloutStepApprovalStatus.PENDING):
        """Create a test RolloutStepApproval."""
        return RolloutStepApproval(
            approval_id=approval_id,
            rollout_id="ro_test",
            step_id="s1",
            bundle_id="pb_001",
            environment="prod",
            ring_name="canary",
            requested_by="admin",
            requested_reason="Deploy to canary",
            status=status,
            created_at=datetime.now(timezone.utc),
        )

    def _make_plan_with_approval_step(self, rollout_id="ro_appr_test"):
        """Create a test RolloutPlan with a step requiring approval in BLOCKED status."""
        return RolloutPlan(
            rollout_id=rollout_id,
            name="approval-rollout",
            bundle_id="pb_001",
            status=RolloutPlanStatus.ACTIVE,
            steps=[
                RolloutStep(
                    step_id="s1",
                    step_type=RolloutStepType.ASSIGN_RING,
                    environment="prod",
                    ring_name="canary",
                    status=RolloutStepStatus.BLOCKED,
                    requires_approval=True,
                    approval_id="rsa_test123",
                    error={"type": "approval_required", "message": "Step requires approval", "approval_id": "rsa_test123"},
                ),
            ],
            created_by="admin",
            reason="Test approval rollout",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    # --- Test 1: Approvals list page ---

    def test_approvals_list_page_renders(self):
        """GET /rollout-approvals returns 200 and renders approvals list."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        approval_store = InMemoryRolloutStepApprovalStore()
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            approval_store=approval_store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/rollout-approvals")
        assert resp.status_code == 200
        assert "rollout-approvals" in resp.text.lower() or "approvals" in resp.text.lower()

    # --- Test 2: Approval detail page ---

    def test_approval_detail_page_renders(self):
        """GET /rollout-approvals/{id} returns 200 and shows approval detail."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        approval_store = InMemoryRolloutStepApprovalStore()
        approval = self._make_approval("rsa_detail_test")
        _run_async(approval_store.create(approval))

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            approval_store=approval_store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/rollout-approvals/rsa_detail_test")
        assert resp.status_code == 200
        assert "rsa_detail_test" in resp.text

    # --- Test 3: Request approval POST ---

    def test_request_approval_post_works(self):
        """POST /rollouts/{id}/steps/{id}/request-approval creates an approval request."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.runtime.policy_rollout_service import RolloutService

        rollout_store = InMemoryRolloutPlanStore()
        approval_store = InMemoryRolloutStepApprovalStore()

        plan = RolloutPlan(
            rollout_id="ro_req_appr",
            name="req-approval",
            bundle_id="pb_001",
            status=RolloutPlanStatus.ACTIVE,
            steps=[
                RolloutStep(
                    step_id="s1",
                    step_type=RolloutStepType.ASSIGN_RING,
                    environment="prod",
                    ring_name="canary",
                    status=RolloutStepStatus.BLOCKED,
                    requires_approval=True,
                ),
            ],
            created_by="admin",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        _run_async(rollout_store.create(plan))

        service = RolloutService(
            rollout_store=rollout_store,
            release_service=None,
            approval_store=approval_store,
        )

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_store=rollout_store,
            rollout_service=service,
            approval_store=approval_store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.post("/policy-console/rollouts/ro_req_appr/steps/s1/request-approval", data={
            "actor_id": "admin",
            "permissions": "policy.rollout.approval.request",
            "reason": "Deploy to canary",
        })
        assert resp.status_code == 200
        # Verify approval was created
        approvals = _run_async(approval_store.list())
        assert len(approvals) == 1
        assert approvals[0].rollout_id == "ro_req_appr"

    # --- Test 4: Approve POST ---

    def test_approve_post_works(self):
        """POST /rollout-approvals/{id}/approve approves a pending approval."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.runtime.policy_rollout_service import RolloutService

        rollout_store = InMemoryRolloutPlanStore()
        approval_store = InMemoryRolloutStepApprovalStore()

        plan = self._make_plan_with_approval_step("ro_approve_test")
        approval = self._make_approval("rsa_approve_test")
        _run_async(rollout_store.create(plan))
        _run_async(approval_store.create(approval))

        service = RolloutService(
            rollout_store=rollout_store,
            release_service=None,
            approval_store=approval_store,
        )

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_store=rollout_store,
            rollout_service=service,
            approval_store=approval_store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.post("/policy-console/rollout-approvals/rsa_approve_test/approve", data={
            "actor_id": "reviewer",
            "permissions": "policy.rollout.approval.approve",
            "reason": "Looks good",
        })
        assert resp.status_code == 200
        # Verify approval was approved
        updated = _run_async(approval_store.get("rsa_approve_test"))
        assert updated.status == RolloutStepApprovalStatus.APPROVED

    # --- Test 5: Reject POST ---

    def test_reject_post_works(self):
        """POST /rollout-approvals/{id}/reject rejects a pending approval."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.runtime.policy_rollout_service import RolloutService

        rollout_store = InMemoryRolloutPlanStore()
        approval_store = InMemoryRolloutStepApprovalStore()

        plan = self._make_plan_with_approval_step("ro_reject_test")
        approval = self._make_approval("rsa_reject_test")
        _run_async(rollout_store.create(plan))
        _run_async(approval_store.create(approval))

        service = RolloutService(
            rollout_store=rollout_store,
            release_service=None,
            approval_store=approval_store,
        )

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_store=rollout_store,
            rollout_service=service,
            approval_store=approval_store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.post("/policy-console/rollout-approvals/rsa_reject_test/reject", data={
            "actor_id": "reviewer",
            "permissions": "policy.rollout.approval.reject",
            "reason": "Not ready",
        })
        assert resp.status_code == 200
        # Verify approval was rejected
        updated = _run_async(approval_store.get("rsa_reject_test"))
        assert updated.status == RolloutStepApprovalStatus.REJECTED

    # --- Test 6: Rollout detail shows approval state for blocked steps ---

    def test_rollout_detail_shows_approval_state(self):
        """Rollout detail page shows approval_id for blocked steps."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        rollout_store = InMemoryRolloutPlanStore()
        plan = self._make_plan_with_approval_step("ro_appr_detail")
        _run_async(rollout_store.create(plan))

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_store=rollout_store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/rollouts/ro_appr_detail")
        assert resp.status_code == 200
        assert "rsa_test123" in resp.text
        assert "blocked" in resp.text.lower()

    # --- Test 7: Error renders clearly without traceback ---

    def test_error_renders_clearly(self):
        """Error in request/approve/reject shows error message without traceback."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        approval_store = InMemoryRolloutStepApprovalStore()
        # Try to approve a non-existent approval - should show clean error
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_service=type("Svc", (), {"approve_step": staticmethod(lambda **kw: (_ for _ in ()).throw(KeyError("not found")))})(),
            approval_store=approval_store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.post("/policy-console/rollout-approvals/rsa_nonexistent/approve", data={
            "actor_id": "reviewer",
            "permissions": "policy.rollout.approval.approve",
        })
        assert resp.status_code == 200
        # Should show error message but no traceback
        assert "Traceback" not in resp.text
        assert "rsa_nonexistent" in resp.text or "not found" in resp.text.lower() or "error" in resp.text.lower()
