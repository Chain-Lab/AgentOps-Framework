"""Phase 35 Task 8: Tests for console rollout pages."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from agent_app.config.schema import PolicyConsoleConfig
from agent_app.governance.policy_rollout import (
    RolloutPlan,
    RolloutPlanStatus,
    RolloutStep,
    RolloutStepStatus,
    RolloutStepType,
)
from agent_app.runtime.policy_rollout_store import InMemoryRolloutPlanStore


class TestRolloutConsoleRouter:
    """Tests for Phase 35 Task 8 console rollout pages."""

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

    def _make_plan(self, rollout_id="ro_test123", status=RolloutPlanStatus.DRAFT):
        """Create a test RolloutPlan."""
        return RolloutPlan(
            rollout_id=rollout_id,
            name="test-rollout",
            bundle_id="pb_001",
            status=status,
            steps=[
                RolloutStep(
                    step_id="s1",
                    step_type=RolloutStepType.ACTIVATE,
                    environment="prod",
                    status=RolloutStepStatus.PENDING,
                ),
            ],
            created_by="admin",
            reason="Test rollout",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    def test_rollout_list_page(self):
        """GET /rollouts renders the rollout list page."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        store = InMemoryRolloutPlanStore()
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_store=store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/rollouts")
        assert resp.status_code == 200
        assert "Rollout Plans" in resp.text or "rollouts" in resp.text.lower()

    def test_rollout_detail_page(self):
        """GET /rollouts/{id} renders the rollout detail page."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        store = InMemoryRolloutPlanStore()
        plan = self._make_plan("ro_detail_test")
        import asyncio
        asyncio.get_event_loop().run_until_complete(store.create(plan))

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_store=store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/rollouts/ro_detail_test")
        assert resp.status_code == 200
        assert "ro_detail_test" in resp.text

    def test_rollout_create_page(self):
        """GET /rollouts/new renders the create form page."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/rollouts/new")
        assert resp.status_code == 200
        assert "Create Rollout Plan" in resp.text or "rollout" in resp.text.lower()

    def test_rollout_create_post(self):
        """POST /rollouts creates a plan via rollout service."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.runtime.policy_rollout_service import RolloutService

        store = InMemoryRolloutPlanStore()
        service = RolloutService(
            rollout_store=store,
            release_service=None,
        )
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_store=store,
            rollout_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.post("/policy-console/rollouts", data={
            "name": "test-plan",
            "bundle_id": "pb_001",
            "actor_id": "admin",
            "reason": "Test",
            "permissions": "policy.rollout.create",
        })
        assert resp.status_code == 200
        # The plan should have been created in the store
        import asyncio
        plans = asyncio.get_event_loop().run_until_complete(store.list())
        assert len(plans) == 1
        assert plans[0].name == "test-plan"

    def test_rollout_start_post(self):
        """POST /rollouts/{id}/start starts a rollout plan."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.runtime.policy_rollout_service import RolloutService

        store = InMemoryRolloutPlanStore()
        plan = self._make_plan("ro_start_test", status=RolloutPlanStatus.DRAFT)
        import asyncio
        asyncio.get_event_loop().run_until_complete(store.create(plan))

        service = RolloutService(
            rollout_store=store,
            release_service=None,
        )
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_store=store,
            rollout_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.post("/policy-console/rollouts/ro_start_test/start", data={
            "actor_id": "admin",
            "permissions": "policy.rollout.start",
        })
        assert resp.status_code == 200
        # Verify the plan is now ACTIVE
        updated = asyncio.get_event_loop().run_until_complete(store.get("ro_start_test"))
        assert updated.status == RolloutPlanStatus.ACTIVE

    def test_rollout_run_next_post(self):
        """POST /rollouts/{id}/run-next executes a step."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.runtime.policy_rollout_service import RolloutService

        store = InMemoryRolloutPlanStore()
        plan = self._make_plan("ro_runnext_test", status=RolloutPlanStatus.ACTIVE)
        import asyncio
        asyncio.get_event_loop().run_until_complete(store.create(plan))

        # Need a release_service for step execution — use a stub
        service = RolloutService(
            rollout_store=store,
            release_service=_StubReleaseService(),
        )
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_store=store,
            rollout_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.post("/policy-console/rollouts/ro_runnext_test/run-next", data={
            "actor_id": "admin",
            "permissions": "policy.rollout.execute",
        })
        assert resp.status_code == 200

    def test_rollout_cancel_post(self):
        """POST /rollouts/{id}/cancel cancels a rollout plan."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.runtime.policy_rollout_service import RolloutService

        store = InMemoryRolloutPlanStore()
        plan = self._make_plan("ro_cancel_test", status=RolloutPlanStatus.ACTIVE)
        import asyncio
        asyncio.get_event_loop().run_until_complete(store.create(plan))

        service = RolloutService(
            rollout_store=store,
            release_service=None,
        )
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_store=store,
            rollout_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.post("/policy-console/rollouts/ro_cancel_test/cancel", data={
            "actor_id": "admin",
            "reason": "No longer needed",
            "permissions": "policy.rollout.cancel",
        })
        assert resp.status_code == 200
        # Verify the plan is now CANCELLED
        updated = asyncio.get_event_loop().run_until_complete(store.get("ro_cancel_test"))
        assert updated.status == RolloutPlanStatus.CANCELLED


class _StubReleaseService:
    """Minimal stub for PolicyReleaseService used by RolloutService step execution."""

    async def execute_promotion(self, **kwargs):
        """Stub execute_promotion that returns a minimal activation."""
        return type("Activation", (), {
            "activation_id": "pa_stub_001",
            "environment": "prod",
            "bundle_id": "pb_001",
            "config_hash": "h_stub",
            "activated_by": "admin",
            "reason": "stub",
            "status": "active",
            "promotion_id": None,
            "created_at": datetime.now(timezone.utc),
            "superseded_at": None,
            "superseded_by_activation_id": None,
        })()

    async def assign_activation_to_ring(self, **kwargs):
        """Stub assign_activation_to_ring."""
        return type("Assignment", (), {
            "assignment_id": "ra_stub_001",
            "environment": "prod",
            "ring_name": "canary",
            "activation_id": "pa_stub_001",
            "bundle_id": "pb_001",
            "config_hash": "h_stub",
            "status": "active",
            "assigned_by": "admin",
            "reason": "stub",
            "created_at": datetime.now(timezone.utc),
        })()

    async def promote_canary_to_stable(self, **kwargs):
        """Stub promote_canary_to_stable."""
        return type("Assignment", (), {
            "assignment_id": "ra_stub_002",
            "environment": "prod",
            "ring_name": "stable",
            "activation_id": "pa_stub_001",
            "bundle_id": "pb_001",
            "config_hash": "h_stub",
            "status": "active",
            "assigned_by": "admin",
            "reason": "stub",
            "created_at": datetime.now(timezone.utc),
        })()
