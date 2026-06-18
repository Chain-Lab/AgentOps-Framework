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
    FederatedRolloutPlan, FederatedRolloutPlanStatus, FederatedRolloutTarget,
    FederatedRolloutTargetExecution, FederatedRolloutTargetExecutionStatus,
    FederationExecutionStrategy, RolloutConflict, RolloutConflictSeverity, RolloutConflictType,
)

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _target() -> FederatedRolloutTarget:
    return FederatedRolloutTarget(target_id="frt_test", name="prod-us-canary", tenant_id="tenant_a", environment="prod", ring_name="canary", region="us-east", created_at=_now())

def _step() -> RolloutStep:
    return RolloutStep(step_id="step_activate", step_type=RolloutStepType.ACTIVATE, environment="prod", ring_name="canary")

def _plan() -> FederatedRolloutPlan:
    return FederatedRolloutPlan(federation_id="frp_test", name="global rollout", bundle_id="pb_123", strategy=FederationExecutionStrategy.SEQUENTIAL, status=FederatedRolloutPlanStatus.ACTIVE, target_ids=["frt_test"], executions=[FederatedRolloutTargetExecution(execution_id="fre_test", target_id="frt_test", rollout_id="ro_child", status=FederatedRolloutTargetExecutionStatus.SUCCEEDED)], rollout_template_steps=[_step()], created_by="release_manager", created_at=_now(), updated_at=_now())

def _client(service=None, target_store=None, plan_store=None) -> TestClient:
    app = FastAPI()
    router = build_policy_console_router(store=None, rollout_federation_service=service, federated_rollout_target_store=target_store, federated_rollout_plan_store=plan_store)
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
        response = client.post("/policy-console/federation/targets", data={"name": "prod-us-canary", "environment": "prod", "ring_name": "canary", "region": "us-east", "tenant_id": "tenant_a", "actor_id": "admin", "permissions": "policy.federation.target.create"})
        assert response.status_code in (200, 303)
        assert service.create_target.await_count == 1

    def test_target_disable_enable_posts_work(self) -> None:
        target_store = MagicMock()
        target_store.list = AsyncMock(return_value=[_target()])
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
        service.detect_conflicts = AsyncMock(return_value=[RolloutConflict(conflict_id="frc_test", conflict_type=RolloutConflictType.DISABLED_TARGET, severity=RolloutConflictSeverity.ERROR, target_id="frt_test", message="Target disabled")])
        client = _client(service=service)
        response = client.get("/policy-console/federation/plans/frp_test/conflicts")
        assert response.status_code == 200
        assert "disabled_target" in response.text
        assert "Target disabled" in response.text
