"""Tests for Console Phase 33 Task 9: ring management pages and routes."""

from __future__ import annotations

import asyncio

import pytest

from agent_app.console.router import build_policy_console_router
from agent_app.config.schema import PolicyConsoleConfig
from agent_app.runtime.policy_ring_store import InMemoryReleaseRingStore
from agent_app.runtime.policy_ring_assignment_store import InMemoryRingActivationAssignmentStore
from agent_app.runtime.policy_activation_store import InMemoryPolicyActivationStore


# ---------------------------------------------------------------------------
# Stub stores for minimal test wiring
# ---------------------------------------------------------------------------

class _StubBundleStore:
    async def get(self, bid):
        return None

    async def list(self):
        return []

    async def get_active(self):
        return None

    async def activate(self, bid):
        return None


class _StubGateStore:
    async def list(self, **kw):
        return []

    async def get(self, gid):
        return None


def _make_api():
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


def _get_client(api):
    from starlette.testclient import TestClient
    return TestClient(api)


def _run_async(coro):
    """Run an async coroutine from synchronous test code."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ring_client():
    """Build a test client with ring stores wired (no release service)."""
    ring_store = InMemoryReleaseRingStore()
    ring_assignment_store = InMemoryRingActivationAssignmentStore()
    activation_store = InMemoryPolicyActivationStore()
    router = build_policy_console_router(
        store=None,
        config=PolicyConsoleConfig(enabled=True),
        bundle_store=_StubBundleStore(),
        gate_store=_StubGateStore(),
        ring_store=ring_store,
        ring_assignment_store=ring_assignment_store,
        activation_store=activation_store,
    )
    api = _make_api()
    api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
    client = _get_client(api)
    return client, ring_store, ring_assignment_store, activation_store


@pytest.fixture
def ring_client_with_service():
    """Build a test client with a real PolicyReleaseService wired."""
    from agent_app.governance.policy_bundle import InMemoryPolicyBundleStore
    from agent_app.runtime.policy_release import PolicyReleaseService
    from agent_app.runtime.policy_environment_store import InMemoryPolicyEnvironmentStore

    bundle_store = InMemoryPolicyBundleStore()
    activation_store = InMemoryPolicyActivationStore()
    environment_store = InMemoryPolicyEnvironmentStore()
    ring_store = InMemoryReleaseRingStore()
    ring_assignment_store = InMemoryRingActivationAssignmentStore()

    release_service = PolicyReleaseService(
        bundle_store=bundle_store,
        replay_runner=None,
        replay_store=None,
        gate_evaluator=None,
        gate_store=_StubGateStore(),
        promotion_store=None,
        activation_store=activation_store,
        environment_store=environment_store,
        ring_store=ring_store,
        ring_assignment_store=ring_assignment_store,
    )

    router = build_policy_console_router(
        store=None,
        config=PolicyConsoleConfig(enabled=True),
        bundle_store=bundle_store,
        gate_store=_StubGateStore(),
        activation_store=activation_store,
        environment_store=environment_store,
        ring_store=ring_store,
        ring_assignment_store=ring_assignment_store,
        release_service=release_service,
    )
    api = _make_api()
    api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
    client = _get_client(api)
    return client, ring_store, ring_assignment_store, activation_store, bundle_store, release_service


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPhase33RingConsole:
    """Phase 33 Task 9: ring list, detail, create, assign, promote, disable/enable."""

    def test_ring_list_page_empty(self, ring_client):
        """GET /rings returns 200 with empty state when no rings exist."""
        client, _, _, _ = ring_client
        resp = client.get("/policy-console/rings")
        assert resp.status_code == 200
        assert "No rings configured" in resp.text

    def test_ring_list_page_with_rings(self, ring_client):
        """GET /rings returns 200 and lists rings after creation."""
        client, ring_store, _, _ = ring_client
        from agent_app.governance.policy_ring import ReleaseRing
        ring = ReleaseRing(ring_id="ring_001", environment="prod", name="canary")
        _run_async(ring_store.create(ring))
        resp = client.get("/policy-console/rings")
        assert resp.status_code == 200
        assert "canary" in resp.text
        assert "prod" in resp.text

    def test_ring_detail_page(self, ring_client_with_service):
        """GET /rings/{env}/{name} returns 200 with ring info."""
        client, ring_store, _, _, _, service = ring_client_with_service
        from agent_app.core.context import RunContext
        context = RunContext(run_id="test", user_id="admin", tenant_id="default",
                            permissions=["policy.ring.create"])
        _run_async(service.create_ring("prod", "canary", "admin", context))
        resp = client.get("/policy-console/rings/prod/canary")
        assert resp.status_code == 200
        assert "canary" in resp.text
        assert "prod" in resp.text

    def test_create_ring_post(self, ring_client_with_service):
        """POST /rings creates a new ring."""
        client, ring_store, _, _, _, _ = ring_client_with_service
        resp = client.post("/policy-console/rings", data={
            "environment": "prod",
            "name": "canary",
            "actor_id": "admin",
            "permissions": "policy.ring.create",
            "description": "Canary ring",
        })
        assert resp.status_code == 200
        assert "Traceback" not in resp.text
        ring = _run_async(ring_store.get_by_name("prod", "canary"))
        assert ring is not None

    def test_disable_ring_post(self, ring_client_with_service):
        """POST /rings/{env}/{name}/disable disables the ring."""
        client, ring_store, _, _, _, service = ring_client_with_service
        from agent_app.core.context import RunContext
        context = RunContext(run_id="test", user_id="admin", tenant_id="default",
                            permissions=["policy.ring.create", "policy.ring.disable"])
        _run_async(service.create_ring("prod", "canary", "admin", context))
        resp = client.post("/policy-console/rings/prod/canary/disable", data={
            "actor_id": "admin",
            "reason": "Emergency",
            "permissions": "policy.ring.disable",
        })
        assert resp.status_code == 200
        assert "Traceback" not in resp.text
        ring = _run_async(ring_store.get_by_name("prod", "canary"))
        assert ring.status.value == "disabled"

    def test_enable_ring_post(self, ring_client_with_service):
        """POST /rings/{env}/{name}/enable re-enables a disabled ring."""
        client, ring_store, _, _, _, service = ring_client_with_service
        from agent_app.core.context import RunContext
        context = RunContext(run_id="test", user_id="admin", tenant_id="default",
                            permissions=["policy.ring.create", "policy.ring.disable", "policy.ring.enable"])
        _run_async(service.create_ring("prod", "canary", "admin", context))
        _run_async(service.disable_ring("prod", "canary", "admin", context))
        resp = client.post("/policy-console/rings/prod/canary/enable", data={
            "actor_id": "admin",
            "permissions": "policy.ring.enable",
        })
        assert resp.status_code == 200
        assert "Traceback" not in resp.text
        ring = _run_async(ring_store.get_by_name("prod", "canary"))
        assert ring.status.value == "enabled"

    def test_permission_error_renders_cleanly(self, ring_client_with_service):
        """POST with no permission shows message, not traceback."""
        client, _, _, _, _, service = ring_client_with_service
        from agent_app.core.context import RunContext
        context = RunContext(run_id="test", user_id="admin", tenant_id="default",
                            permissions=["policy.ring.create"])
        _run_async(service.create_ring("prod", "canary", "admin", context))
        resp = client.post("/policy-console/rings/prod/canary/disable", data={
            "actor_id": "admin",
            "reason": "Emergency",
            "permissions": "",  # empty = no permission
        })
        assert resp.status_code == 200
        assert "Traceback" not in resp.text
