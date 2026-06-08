"""Tests for FastAPI adapter.

These tests are skipped if FastAPI is not installed.
"""

import json

import pytest

from agent_app import AgentApp, AgentSpec, ToolSpec, Workflow


def _build_test_app():
    from agent_app.registry.agent_registry import AgentRegistry
    from agent_app.registry.tool_registry import ToolRegistry
    from agent_app.registry.workflow_registry import WorkflowRegistry

    ar = AgentRegistry()
    tr = ToolRegistry()
    wr = WorkflowRegistry()
    app = AgentApp(
        registry=type("B", (), {"agent_registry": ar, "tool_registry": tr, "workflow_registry": wr})()
    )
    app.agent_registry = ar
    app.tool_registry = tr
    app.workflow_registry = wr
    app.register_agent(
        AgentSpec(name="support", description="Support agent", model="gpt-4o", instructions="You are a support agent.")
    )
    app.register_workflow(Workflow.single(agent="support", name="cs"))
    app.register_tool(
        ToolSpec(name="order.query", description="Query order", risk_level="low")
    )
    return app


def _has_fastapi() -> bool:
    try:
        import fastapi  # noqa: F401
        import httpx  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _has_fastapi(), reason="fastapi not installed")
class TestFastAPIAdapter:
    @pytest.fixture
    def api(self):
        from agent_app.adapters.fastapi import create_fastapi_app

        return create_fastapi_app(_build_test_app())

    def test_health(self, api) -> None:
        from fastapi.testclient import TestClient

        client = TestClient(api)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_list_agents(self, api) -> None:
        from fastapi.testclient import TestClient

        client = TestClient(api)
        resp = client.get("/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "support"

    def test_list_tools(self, api) -> None:
        from fastapi.testclient import TestClient

        client = TestClient(api)
        resp = client.get("/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert any(t["name"] == "order.query" for t in data)

    def test_list_workflows(self, api) -> None:
        from fastapi.testclient import TestClient

        client = TestClient(api)
        resp = client.get("/workflows")
        assert resp.status_code == 200
        data = resp.json()
        assert any(w["name"] == "cs" for w in data)

    def test_run_endpoint(self, api) -> None:
        from fastapi.testclient import TestClient

        client = TestClient(api)
        resp = client.post(
            "/runs",
            json={
                "agent": "support",
                "input": "hello",
                "user_id": "u1",
                "tenant_id": "t1",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert "support" in data["final_output"]

    def test_run_requires_agent_or_workflow(self, api) -> None:
        from fastapi.testclient import TestClient

        client = TestClient(api)
        resp = client.post("/runs", json={"input": "hi"})
        assert resp.status_code == 400
        assert "agent" in resp.json()["detail"].lower() or "workflow" in resp.json()["detail"].lower()

    def test_run_not_both(self, api) -> None:
        from fastapi.testclient import TestClient

        client = TestClient(api)
        resp = client.post(
            "/runs",
            json={"agent": "support", "workflow": "cs", "input": "hi"},
        )
        assert resp.status_code == 400


@pytest.mark.skipif(not _has_fastapi(), reason="fastapi not installed")
class TestFastAPITraceEndpoints:
    """Tests for GET /traces and GET /traces/{trace_id}."""

    @pytest.fixture
    def api_with_traces(self):
        """Build an app with an InMemoryTraceCollector and pre-recorded events."""
        from agent_app.observability.collector import InMemoryTraceCollector
        from agent_app.observability.events import RunEvent
        import asyncio

        app = _build_test_app()
        collector = InMemoryTraceCollector()
        app.trace_collector = collector

        # Pre-populate traces
        asyncio.run(collector.record(
            RunEvent(event_type="run.started", trace_id="tr-1", run_id="run-1", user_id="u1", tenant_id="t1")
        ))
        asyncio.run(collector.record(
            RunEvent(event_type="run.completed", trace_id="tr-1", run_id="run-1", user_id="u1", tenant_id="t1")
        ))
        asyncio.run(collector.record(
            RunEvent(event_type="run.started", trace_id="tr-2", run_id="run-2", user_id="u1", tenant_id="t2")
        ))
        asyncio.run(collector.record(
            RunEvent(event_type="run.interrupted", trace_id="tr-2", run_id="run-2", user_id="u1", tenant_id="t2")
        ))
        asyncio.run(collector.record(
            RunEvent(event_type="run.started", trace_id="tr-3", run_id="run-3", user_id="u2", tenant_id="t1")
        ))

        from agent_app.adapters.fastapi import create_fastapi_app
        return create_fastapi_app(app), collector

    def test_list_traces_returns_summaries(self, api_with_traces):
        """GET /traces returns list of trace summaries."""
        api, _ = api_with_traces
        from fastapi.testclient import TestClient
        client = TestClient(api)
        resp = client.get("/traces")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 3

    def test_list_traces_default_limit(self, api_with_traces):
        """GET /traces respects default limit of 50."""
        api, _ = api_with_traces
        from fastapi.testclient import TestClient
        client = TestClient(api)
        resp = client.get("/traces?limit=50")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) <= 50

    def test_list_traces_custom_limit(self, api_with_traces):
        """GET /traces?limit=N truncates results."""
        api, _ = api_with_traces
        from fastapi.testclient import TestClient
        client = TestClient(api)
        resp = client.get("/traces?limit=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_list_traces_filter_by_run_id(self, api_with_traces):
        """GET /traces?run_id=xxx filters by run_id."""
        api, _ = api_with_traces
        from fastapi.testclient import TestClient
        client = TestClient(api)
        resp = client.get("/traces?run_id=run-1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["trace_id"] == "tr-1"

    def test_list_traces_filter_by_event_type(self, api_with_traces):
        """GET /traces?event_type=xxx filters traces containing that event type."""
        api, _ = api_with_traces
        from fastapi.testclient import TestClient
        client = TestClient(api)
        # tr-2 has run.interrupted
        resp = client.get("/traces?event_type=run.interrupted")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["trace_id"] == "tr-2"

    def test_list_traces_filter_by_tenant_id(self, api_with_traces):
        """GET /traces?tenant_id=xxx filters by tenant."""
        api, _ = api_with_traces
        from fastapi.testclient import TestClient
        client = TestClient(api)
        resp = client.get("/traces?tenant_id=t2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["trace_id"] == "tr-2"

    def test_list_traces_summary_fields(self, api_with_traces):
        """Each trace summary has the expected fields."""
        api, _ = api_with_traces
        from fastapi.testclient import TestClient
        client = TestClient(api)
        resp = client.get("/traces")
        assert resp.status_code == 200
        data = resp.json()
        for summary in data:
            assert "trace_id" in summary
            assert "run_id" in summary
            assert "event_count" in summary
            assert "status" in summary

    def test_get_trace_existing(self, api_with_traces):
        """GET /traces/{trace_id} returns full events for existing trace."""
        api, _ = api_with_traces
        from fastapi.testclient import TestClient
        client = TestClient(api)
        resp = client.get("/traces/tr-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trace_id"] == "tr-1"
        assert data["run_id"] == "run-1"
        assert len(data["events"]) == 2
        assert data["events"][0]["event_type"] == "run.started"
        assert data["events"][1]["event_type"] == "run.completed"

    def test_get_trace_unknown_returns_404(self, api_with_traces):
        """GET /traces/{unknown_id} returns 404."""
        api, _ = api_with_traces
        from fastapi.testclient import TestClient
        client = TestClient(api)
        resp = client.get("/traces/nonexistent-trace")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_get_trace_no_collector_returns_404(self):
        """GET /traces when no collector is configured returns 404."""
        from agent_app.adapters.fastapi import create_fastapi_app
        api = create_fastapi_app(_build_test_app())
        from fastapi.testclient import TestClient
        client = TestClient(api)
        resp = client.get("/traces/some-trace")
        assert resp.status_code == 404
