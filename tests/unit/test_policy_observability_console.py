"""Phase 39 Task 6: Tests for console observability dashboard."""

from __future__ import annotations

import pytest

from conftest import _run_async

from agent_app.config.schema import PolicyConsoleConfig
from agent_app.governance.audit import InMemoryAuditLogger
from agent_app.runtime.policy_observability_service import PolicyObservabilityService


class TestPolicyObservabilityConsole:
    """Tests for Phase 39 Task 6 console observability pages."""

    def _make_app(self):
        """Create a minimal FastAPI app for console testing."""
        from agent_app import AgentApp
        from agent_app.governance.approval import InMemoryApprovalStore
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

    def _make_observability_service(self, audit_logger=None):
        """Create a PolicyObservabilityService for testing."""
        if audit_logger is None:
            audit_logger = InMemoryAuditLogger()
        return PolicyObservabilityService(audit_logger=audit_logger)

    # --- Test 1: Observability dashboard renders ---

    def test_observability_dashboard_renders(self):
        """GET /observability returns 200 and renders dashboard."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_observability_service()
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            observability_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/observability")
        assert resp.status_code == 200
        assert "Policy Observability Dashboard" in resp.text

    # --- Test 2: Observability report form renders ---

    def test_observability_report_form_renders(self):
        """GET /observability/report returns 200 and renders form."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/observability/report")
        assert resp.status_code == 200
        assert "Policy Observability Report" in resp.text
        assert "Generate Report" in resp.text

    # --- Test 3: Observability report submit ---

    def test_observability_report_submit(self):
        """POST /observability/report returns 200 with report."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_observability_service()
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            observability_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/observability/report", data={
            "since": "",
            "until": "",
        })
        assert resp.status_code == 200
        assert "Report ID" in resp.text

    # --- Test 4: No service configured returns 404 ---

    def test_observability_no_service_404(self):
        """GET /observability without service returns 404."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/observability")
        assert resp.status_code == 404
        assert "not configured" in resp.text.lower()

    # --- Test 5: No service for report submit returns 404 ---

    def test_observability_report_no_service_404(self):
        """POST /observability/report without service returns 404."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/observability/report", data={
            "since": "",
            "until": "",
        })
        assert resp.status_code == 404
        assert "not configured" in resp.text.lower()

    # --- Test 6: Report with invalid datetime shows error ---

    def test_observability_report_invalid_datetime(self):
        """POST /observability/report with invalid datetime shows error."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_observability_service()
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            observability_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/observability/report", data={
            "since": "not-a-date",
            "until": "",
        })
        assert resp.status_code == 200
        assert "Invalid datetime format" in resp.text

    # --- Test 7: Report with valid datetime filter ---

    def test_observability_report_valid_datetime_filter(self):
        """POST /observability/report with valid datetimes returns report."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_observability_service()
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            observability_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/observability/report", data={
            "since": "2026-01-01T00:00:00Z",
            "until": "2026-12-31T23:59:59Z",
        })
        assert resp.status_code == 200
        assert "Report ID" in resp.text

    # --- Test 8: No traceback leakage ---

    def test_no_traceback_leakage(self):
        """Error responses do not contain tracebacks."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/observability")
        assert "Traceback" not in resp.text
