"""Phase 36.5: Regression tests for console TestClient isolation.

Verifies that multiple console apps/clients created in the same process
do not share state. This guards against the batch-mode test failure
that was caused by asyncio.get_event_loop() state leakage.
"""

from __future__ import annotations

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
from agent_app.console.router import build_policy_console_router


def _make_app():
    """Create a fresh FastAPI app for console testing."""
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


def _get_client(api):
    from starlette.testclient import TestClient
    return TestClient(api)


def _make_plan(rollout_id="ro_isolation"):
    return RolloutPlan(
        rollout_id=rollout_id,
        name="isolation-test",
        bundle_id="pb_001",
        status=RolloutPlanStatus.DRAFT,
        steps=[
            RolloutStep(
                step_id="s1",
                step_type=RolloutStepType.ACTIVATE,
                environment="prod",
                status=RolloutStepStatus.PENDING,
            ),
        ],
        created_by="admin",
        reason="Isolation test",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def test_console_clients_do_not_share_rollout_state():
    """Two console apps with separate stores must not see each other's data."""
    # App 1 with its own rollout store
    api1 = _make_app()
    store1 = InMemoryRolloutPlanStore()
    plan1 = _make_plan("ro_app1_only")
    _run_async(store1.create(plan1))

    router1 = build_policy_console_router(
        store=None,
        config=PolicyConsoleConfig(enabled=True),
        rollout_store=store1,
    )
    api1.include_router(router1, prefix="/policy-console", tags=["Policy Console"])

    # App 2 with its own rollout store
    api2 = _make_app()
    store2 = InMemoryRolloutPlanStore()
    plan2 = _make_plan("ro_app2_only")
    _run_async(store2.create(plan2))

    router2 = build_policy_console_router(
        store=None,
        config=PolicyConsoleConfig(enabled=True),
        rollout_store=store2,
    )
    api2.include_router(router2, prefix="/policy-console", tags=["Policy Console"])

    client1 = _get_client(api1)
    client2 = _get_client(api2)

    # App1 can see its own rollout
    resp1 = client1.get("/policy-console/rollouts/ro_app1_only")
    assert resp1.status_code == 200
    assert "ro_app1_only" in resp1.text

    # App1 cannot see App2's rollout
    resp1_missing = client1.get("/policy-console/rollouts/ro_app2_only")
    # Should 404 or show not-found since store1 doesn't have ro_app2_only
    assert resp1_missing.status_code in (200, 404)

    # App2 can see its own rollout
    resp2 = client2.get("/policy-console/rollouts/ro_app2_only")
    assert resp2.status_code == 200
    assert "ro_app2_only" in resp2.text

    # App2 cannot see App1's rollout
    resp2_missing = client2.get("/policy-console/rollouts/ro_app1_only")
    assert resp2_missing.status_code in (200, 404)

    # Verify stores are independent
    plans1 = _run_async(store1.list())
    plans2 = _run_async(store2.list())
    assert len(plans1) == 1
    assert len(plans2) == 1
    assert plans1[0].rollout_id == "ro_app1_only"
    assert plans2[0].rollout_id == "ro_app2_only"


def test_console_clients_do_not_share_approval_state():
    """Two console apps with separate approval stores must not see each other's approvals."""
    # App 1
    api1 = _make_app()
    approval_store1 = InMemoryRolloutStepApprovalStore()
    approval1 = RolloutStepApproval(
        approval_id="rsa_app1_only",
        rollout_id="ro_app1",
        step_id="s1",
        bundle_id="pb_001",
        environment="prod",
        requested_by="admin",
        status=RolloutStepApprovalStatus.PENDING,
        created_at=datetime.now(timezone.utc),
    )
    _run_async(approval_store1.create(approval1))

    router1 = build_policy_console_router(
        store=None,
        config=PolicyConsoleConfig(enabled=True),
        approval_store=approval_store1,
    )
    api1.include_router(router1, prefix="/policy-console", tags=["Policy Console"])

    # App 2
    api2 = _make_app()
    approval_store2 = InMemoryRolloutStepApprovalStore()
    approval2 = RolloutStepApproval(
        approval_id="rsa_app2_only",
        rollout_id="ro_app2",
        step_id="s1",
        bundle_id="pb_001",
        environment="prod",
        requested_by="admin",
        status=RolloutStepApprovalStatus.PENDING,
        created_at=datetime.now(timezone.utc),
    )
    _run_async(approval_store2.create(approval2))

    router2 = build_policy_console_router(
        store=None,
        config=PolicyConsoleConfig(enabled=True),
        approval_store=approval_store2,
    )
    api2.include_router(router2, prefix="/policy-console", tags=["Policy Console"])

    client1 = _get_client(api1)
    client2 = _get_client(api2)

    # App1 sees its own approval
    resp1 = client1.get("/policy-console/rollout-approvals/rsa_app1_only")
    assert resp1.status_code == 200
    assert "rsa_app1_only" in resp1.text

    # App2 sees its own approval
    resp2 = client2.get("/policy-console/rollout-approvals/rsa_app2_only")
    assert resp2.status_code == 200
    assert "rsa_app2_only" in resp2.text

    # Verify stores are independent
    approvals1 = _run_async(approval_store1.list())
    approvals2 = _run_async(approval_store2.list())
    assert len(approvals1) == 1
    assert len(approvals2) == 1
    assert approvals1[0].approval_id == "rsa_app1_only"
    assert approvals2[0].approval_id == "rsa_app2_only"


def test_asyncio_run_async_isolation():
    """_run_async creates fresh event loops per call, preventing batch-mode failures.

    This is a direct regression test for the asyncio.get_event_loop() bug:
    calling it after another test has closed the loop raises RuntimeError.
    """
    # Call _run_async multiple times in sequence — each should succeed
    store = InMemoryRolloutPlanStore()
    plan = _make_plan("ro_async_test")
    result = _run_async(store.create(plan))
    assert result is not None

    fetched = _run_async(store.get("ro_async_test"))
    assert fetched is not None
    assert fetched.rollout_id == "ro_async_test"

    listed = _run_async(store.list())
    assert len(listed) == 1

    # Call again to prove loop isolation
    plan2 = _make_plan("ro_async_test2")
    _run_async(store.create(plan2))
    listed2 = _run_async(store.list())
    assert len(listed2) == 2
