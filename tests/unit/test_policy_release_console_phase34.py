"""Tests for Console Phase 34 Task 10: events, reload, and routing simulation pages."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum

import pytest

from agent_app.console.router import build_policy_console_router
from agent_app.config.schema import PolicyConsoleConfig


# ---------------------------------------------------------------------------
# Stub stores / managers for minimal test wiring
# ---------------------------------------------------------------------------

class _EventType(Enum):
    RING_ASSIGNED = "ring_assigned"
    RING_PROMOTED = "ring_promoted"
    ACTIVATION_CREATED = "activation_created"


class _StubEvent:
    def __init__(self, event_id, event_type, environment, ring_name, actor_id, created_at):
        self.event_id = event_id
        self.event_type = event_type
        self.environment = environment
        self.ring_name = ring_name
        self.actor_id = actor_id
        self.created_at = created_at


class _StubEventStore:
    def __init__(self, events=None):
        self._events = events or []

    async def list(self, limit=50):
        return self._events[:limit]


class _StubReloadResult:
    def __init__(self, target, refreshed=True, error=None):
        self.target = target
        self.refreshed = refreshed
        self.error = error


class _StubReloadTarget:
    def model_dump(self):
        return {"environment": "prod", "ring_name": "canary"}


class _StubReloadManager:
    async def request_reload(self, environment=None, ring_name=None,
                             requested_by=None, reason=None):
        target = _StubReloadTarget()
        return [_StubReloadResult(target=target, refreshed=True)]


class _StubRingRouter:
    async def simulate_routing(self, environment, context):
        return {
            "selected_ring": "canary",
            "routing_mode": "canary",
            "bucket": 42,
            "canary_percentage": 10,
            "reason": "User bucketed to canary",
        }


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
def events_client():
    """Build a test client with event_store wired."""
    event_store = _StubEventStore(events=[
        _StubEvent(
            event_id="evt_001",
            event_type=_EventType.RING_ASSIGNED,
            environment="prod",
            ring_name="canary",
            actor_id="admin",
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        ),
    ])
    router = build_policy_console_router(
        store=None,
        config=PolicyConsoleConfig(enabled=True),
        event_store=event_store,
    )
    api = _make_api()
    api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
    client = _get_client(api)
    return client, event_store


@pytest.fixture
def reload_client():
    """Build a test client with reload_manager wired."""
    reload_manager = _StubReloadManager()
    router = build_policy_console_router(
        store=None,
        config=PolicyConsoleConfig(enabled=True),
        reload_manager=reload_manager,
    )
    api = _make_api()
    api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
    client = _get_client(api)
    return client, reload_manager


@pytest.fixture
def routing_client():
    """Build a test client with ring_router wired."""
    ring_router = _StubRingRouter()
    router = build_policy_console_router(
        store=None,
        config=PolicyConsoleConfig(enabled=True),
        ring_router=ring_router,
    )
    api = _make_api()
    api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
    client = _get_client(api)
    return client, ring_router


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPhase34EventConsole:
    """Phase 34 Task 10: events list page."""

    def test_events_page_renders(self, events_client):
        """GET /events returns 200 with event data."""
        client, _ = events_client
        resp = client.get("/policy-console/events")
        assert resp.status_code == 200
        assert "evt_001" in resp.text
        assert "ring_assigned" in resp.text
        assert "prod" in resp.text
        assert "canary" in resp.text

    def test_events_no_store(self):
        """Without event_store, events page shows empty list with error."""
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            event_store=None,
        )
        api = _make_api()
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = _get_client(api)
        resp = client.get("/policy-console/events")
        assert resp.status_code == 200
        assert "Event store not configured" in resp.text


class TestPhase34ReloadConsole:
    """Phase 34 Task 10: reload page."""

    def test_reload_page_renders(self, reload_client):
        """GET /reload returns 200."""
        client, _ = reload_client
        resp = client.get("/policy-console/reload")
        assert resp.status_code == 200
        assert "Reload" in resp.text

    def test_reload_post_works(self, reload_client):
        """POST /reload returns 200 with message about results."""
        client, _ = reload_client
        resp = client.post("/policy-console/reload", data={
            "environment": "prod",
            "ring_name": "canary",
            "actor_id": "admin",
            "reason": "policy update",
        })
        assert resp.status_code == 200
        assert "Reload requested" in resp.text
        assert "1 results" in resp.text


class TestPhase34RoutingSimulateConsole:
    """Phase 34 Task 10: routing simulation page."""

    def test_routing_simulate_page_renders(self, routing_client):
        """GET /routing/simulate returns 200."""
        client, _ = routing_client
        resp = client.get("/policy-console/routing/simulate")
        assert resp.status_code == 200
        assert "Simulate" in resp.text or "simulate" in resp.text.lower()

    def test_routing_simulate_post_works(self, routing_client):
        """POST /routing/simulate returns 200 with result."""
        client, _ = routing_client
        resp = client.post("/policy-console/routing/simulate", data={
            "environment": "prod",
            "actor_id": "admin",
            "user_id": "user_42",
            "tenant_id": "default",
        })
        assert resp.status_code == 200
        assert "canary" in resp.text
        assert "routing_mode" in resp.text.lower() or "Routing" in resp.text
