"""Tests for Console Phase 32: environment detail and rollback actions."""

from __future__ import annotations

import asyncio

import pytest

from agent_app.console.router import build_policy_console_router
from agent_app.config.schema import PolicyConsoleConfig
from agent_app.runtime.policy_activation_store import InMemoryPolicyActivationStore
from agent_app.runtime.policy_environment_store import InMemoryPolicyEnvironmentStore


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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def env_client():
    """Build a test client with activation + environment stores wired."""
    activation_store = InMemoryPolicyActivationStore()
    environment_store = InMemoryPolicyEnvironmentStore()
    router = build_policy_console_router(
        store=None,
        config=PolicyConsoleConfig(enabled=True),
        bundle_store=_StubBundleStore(),
        gate_store=_StubGateStore(),
        activation_store=activation_store,
        environment_store=environment_store,
    )
    api = _make_api()
    api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
    client = _get_client(api)
    return client, activation_store, environment_store


@pytest.fixture
def env_client_with_service():
    """Build a test client with a real PolicyReleaseService wired."""
    from agent_app.governance.policy_bundle import InMemoryPolicyBundleStore
    from agent_app.runtime.policy_release import PolicyReleaseService

    bundle_store = InMemoryPolicyBundleStore()
    activation_store = InMemoryPolicyActivationStore()
    environment_store = InMemoryPolicyEnvironmentStore()

    release_service = PolicyReleaseService(
        bundle_store=bundle_store,
        replay_runner=None,
        replay_store=None,
        gate_evaluator=None,
        gate_store=_StubGateStore(),
        promotion_store=None,
        activation_store=activation_store,
        environment_store=environment_store,
    )

    router = build_policy_console_router(
        store=None,
        config=PolicyConsoleConfig(enabled=True),
        bundle_store=bundle_store,
        gate_store=_StubGateStore(),
        activation_store=activation_store,
        environment_store=environment_store,
        release_service=release_service,
    )
    api = _make_api()
    api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
    client = _get_client(api)
    return client, activation_store, environment_store, bundle_store, release_service


# ---------------------------------------------------------------------------
# Helper for async calls from sync test code
# ---------------------------------------------------------------------------

def _run_async(coro):
    """Run an async coroutine from synchronous test code."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPhase32EnvironmentConsole:
    """Phase 32: environment detail page, disable/enable/rollback POST routes."""

    def test_environment_detail_page(self, env_client):
        """GET /environments/{environment} returns 200 with environment name."""
        client, _, _ = env_client
        resp = client.get("/policy-console/environments/prod")
        assert resp.status_code == 200
        assert "prod" in resp.text

    def test_environment_detail_shows_status(self, env_client):
        """Environment detail page shows the environment status."""
        client, _, _ = env_client
        resp = client.get("/policy-console/environments/staging")
        assert resp.status_code == 200
        # Default status is enabled
        assert "enabled" in resp.text.lower()

    def test_environment_disable_post(self, env_client_with_service):
        """POST /environments/{environment}/disable disables the environment."""
        client, _, env_store, _, _ = env_client_with_service
        resp = client.post("/policy-console/environments/prod/disable", data={
            "actor_id": "admin",
            "reason": "Emergency maintenance",
            "permissions": "policy.environment.disable",
        })
        assert resp.status_code == 200
        # Verify the environment is now disabled in the store
        state = _run_async(env_store.get("prod"))
        assert state.status.value == "disabled"

    def test_environment_enable_post(self, env_client_with_service):
        """POST /environments/{environment}/enable re-enables a disabled environment."""
        client, _, env_store, _, _ = env_client_with_service
        # First disable
        _run_async(env_store.disable("prod", "admin", "Emergency"))
        # Now enable via POST
        resp = client.post("/policy-console/environments/prod/enable", data={
            "actor_id": "admin2",
            "reason": "Resolved",
            "permissions": "policy.environment.enable",
        })
        assert resp.status_code == 200
        state = _run_async(env_store.get("prod"))
        assert state.status.value == "enabled"

    def test_rollback_post(self, env_client_with_service):
        """POST /activations/{activation_id}/rollback rolls back to previous activation."""
        client, activation_store, _, bundle_store, _ = env_client_with_service
        from agent_app.governance.policy_activation import PolicyActivation
        from agent_app.governance.policy_bundle import PolicyBundle, compute_config_hash

        # Create bundles in the store so rollback can validate they exist
        b1 = PolicyBundle(
            bundle_id="pb_001", name="v1", version="1.0.0",
            config_hash=compute_config_hash("c1"), created_by="admin",
        )
        b2 = PolicyBundle(
            bundle_id="pb_002", name="v2", version="2.0.0",
            config_hash=compute_config_hash("c2"), created_by="admin",
        )
        bundle_store._bundles["pb_001"] = b1
        bundle_store._order.append("pb_001")
        bundle_store._bundles["pb_002"] = b2
        bundle_store._order.append("pb_002")

        # Create two activations for prod
        a1 = PolicyActivation(
            activation_id="pa_001", environment="prod",
            bundle_id="pb_001", config_hash="h1", activated_by="admin",
        )
        a2 = PolicyActivation(
            activation_id="pa_002", environment="prod",
            bundle_id="pb_002", config_hash="h2", activated_by="admin",
        )
        _run_async(activation_store.activate(a1))
        _run_async(activation_store.activate(a2))

        resp = client.post("/policy-console/activations/pa_002/rollback", data={
            "environment": "prod",
            "actor_id": "ops",
            "reason": "Rollback to v1",
            "permissions": "policy.rollback.execute",
        })
        assert resp.status_code == 200

    def test_permission_error_renders_cleanly(self, env_client_with_service):
        """Permission denied shows message, not traceback."""
        client, _, _, _, _ = env_client_with_service
        resp = client.post("/policy-console/environments/prod/disable", data={
            "actor_id": "admin",
            "reason": "Emergency",
            "permissions": "",  # empty = no permission = will be denied
        })
        assert resp.status_code == 200
        # Should NOT contain traceback
        assert "Traceback" not in resp.text

    def test_no_service_shows_error_gracefully(self, env_client):
        """POST without release_service shows error message, not crash."""
        client, _, _ = env_client
        resp = client.post("/policy-console/environments/prod/disable", data={
            "actor_id": "admin",
            "reason": "Emergency",
            "permissions": "policy.environment.disable",
        })
        assert resp.status_code == 200
        assert "Traceback" not in resp.text
