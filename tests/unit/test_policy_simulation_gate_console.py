"""Phase 41 Task 6: Tests for console simulation gate pages."""

from __future__ import annotations

import pytest

try:
    from starlette.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="FastAPI not installed")

from conftest import _run_async

from agent_app.config.schema import PolicyConsoleConfig
from agent_app.governance.audit import InMemoryAuditLogger
from agent_app.governance.policy_gate import PolicyGateRule
from agent_app.runtime.policy_simulation_service import PolicySimulationService


class TestPolicySimulationGateConsole:
    """Tests for Phase 41 Task 6 console simulation gate pages."""

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
        return TestClient(api)

    def _make_simulation_service(self, audit_logger=None):
        """Create a PolicySimulationService for testing."""
        if audit_logger is None:
            audit_logger = InMemoryAuditLogger()
        return PolicySimulationService(audit_logger=audit_logger)

    # --- Test 1: Gate page renders ---

    def test_gate_page_renders(self):
        """GET /simulation/gate returns 200 with 'gate' in text."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/simulation/gate")
        assert resp.status_code == 200
        assert "gate" in resp.text.lower()

    # --- Test 2: Gate POST with pass renders report ---

    def test_gate_post_pass_renders_report(self):
        """POST /simulation/gate with valid rules and lenient gate rules renders passed report."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_simulation_service()
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            simulation_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        candidate_yaml = """- rule_id: test_rule
  name: Test Rule
  action_type: tool.execute
  effect: allow
"""
        gate_yaml = """gate_rules:
  - name: lenient_gate
    max_changed_decisions: 1000
    max_failed_replays: 1000
"""
        resp = client.post("/policy-console/simulation/gate", data={
            "candidate_rules_yaml": candidate_yaml,
            "gate_rules_yaml": gate_yaml,
        })
        assert resp.status_code == 200
        assert "Simulation Gate Report" in resp.text
        assert "PASSED" in resp.text

    # --- Test 3: Gate POST with fail renders failed rules ---

    def test_gate_post_fail_renders_failed_rules(self):
        """POST /simulation/gate with strict gate rules renders FAILED status."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_simulation_service()
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            simulation_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        candidate_yaml = """- rule_id: test_rule
  name: Test Rule
  action_type: tool.execute
  effect: allow
"""
        # max_failed_replays: -1 means any replay will fail this gate
        gate_yaml = """gate_rules:
  - name: strict_gate
    max_failed_replays: -1
"""
        resp = client.post("/policy-console/simulation/gate", data={
            "candidate_rules_yaml": candidate_yaml,
            "gate_rules_yaml": gate_yaml,
        })
        assert resp.status_code == 200
        assert "Simulation Gate Report" in resp.text
        assert "FAILED" in resp.text

    # --- Test 4: Errors render clearly ---

    def test_errors_render_clearly(self):
        """POST /simulation/gate with invalid YAML renders error message."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_simulation_service()
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            simulation_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/simulation/gate", data={
            "candidate_rules_yaml": "not: valid: yaml: [",
            "gate_rules_yaml": "",
        })
        assert resp.status_code == 200
        assert "error" in resp.text.lower()
