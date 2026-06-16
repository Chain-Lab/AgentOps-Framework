"""Phase 40 Task 8: Tests for console simulation pages."""

from __future__ import annotations

import pytest

from conftest import _run_async

from agent_app.config.schema import PolicyConsoleConfig
from agent_app.governance.audit import InMemoryAuditLogger
from agent_app.runtime.policy_simulation_service import PolicySimulationService


class TestPolicySimulationConsole:
    """Tests for Phase 40 Task 8 console simulation pages."""

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

    def _make_simulation_service(self, audit_logger=None):
        """Create a PolicySimulationService for testing."""
        if audit_logger is None:
            audit_logger = InMemoryAuditLogger()
        return PolicySimulationService(audit_logger=audit_logger)

    # --- Test 1: Simulation page renders ---

    def test_simulation_page_renders(self):
        """GET /simulation returns 200 and renders simulation page."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/simulation")
        assert resp.status_code == 200
        assert "Policy Simulation" in resp.text
        assert "Validate Rules" in resp.text
        assert "Run Simulation" in resp.text

    # --- Test 2: Validation POST works ---

    def test_validation_post_works(self):
        """POST /simulation/validate returns 200 with validation report."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        yaml_rules = """- rule_id: test_rule
  name: Test Rule
  action_type: tool.execute
  effect: allow
  risk_level: low
"""
        resp = client.post("/policy-console/simulation/validate", data={
            "candidate_yaml": yaml_rules,
        })
        assert resp.status_code == 200
        assert "Policy Validation Report" in resp.text

    # --- Test 3: Validation POST with empty YAML shows error ---

    def test_validation_post_empty_yaml(self):
        """POST /simulation/validate with empty YAML shows error."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/simulation/validate", data={
            "candidate_yaml": "",
        })
        assert resp.status_code == 200
        assert "required" in resp.text.lower()

    # --- Test 4: Validation POST with invalid YAML shows error ---

    def test_validation_post_invalid_yaml(self):
        """POST /simulation/validate with invalid YAML shows error."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/simulation/validate", data={
            "candidate_yaml": "not: valid: yaml: [",
        })
        assert resp.status_code == 200
        assert "Invalid YAML" in resp.text

    # --- Test 5: Validation POST shows severity badges ---

    def test_validation_post_shows_severity_badges(self):
        """POST /simulation/validate with warnings shows severity badges."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        # Two rules with same name to trigger duplicate_name warning
        yaml_rules = """- rule_id: rule_a
  name: Duplicate Name
  action_type: tool.execute
  effect: allow
- rule_id: rule_b
  name: Duplicate Name
  action_type: tool.execute
  effect: deny
"""
        resp = client.post("/policy-console/simulation/validate", data={
            "candidate_yaml": yaml_rules,
        })
        assert resp.status_code == 200
        assert "WARNING" in resp.text

    # --- Test 6: Replay POST works ---

    def test_replay_post_works(self):
        """POST /simulation/replay returns 200 with simulation report."""
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

        yaml_rules = """- rule_id: test_rule
  name: Test Rule
  action_type: tool.execute
  effect: allow
"""
        resp = client.post("/policy-console/simulation/replay", data={
            "candidate_yaml": yaml_rules,
        })
        assert resp.status_code == 200
        assert "Policy Simulation Report" in resp.text
        assert "Simulation ID" in resp.text

    # --- Test 7: Replay POST without service returns 404 ---

    def test_replay_post_no_service_404(self):
        """POST /simulation/replay without service returns 404."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        yaml_rules = """- rule_id: test_rule
  name: Test Rule
  action_type: tool.execute
  effect: allow
"""
        resp = client.post("/policy-console/simulation/replay", data={
            "candidate_yaml": yaml_rules,
        })
        assert resp.status_code == 404
        assert "not configured" in resp.text.lower()

    # --- Test 8: Replay POST with invalid datetime shows error ---

    def test_replay_post_invalid_datetime(self):
        """POST /simulation/replay with invalid datetime shows error."""
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

        yaml_rules = """- rule_id: test_rule
  name: Test Rule
  action_type: tool.execute
  effect: allow
"""
        resp = client.post("/policy-console/simulation/replay", data={
            "candidate_yaml": yaml_rules,
            "since": "not-a-date",
        })
        assert resp.status_code == 200
        assert "Invalid datetime" in resp.text

    # --- Test 9: Replay POST with valid datetime filter ---

    def test_replay_post_valid_datetime_filter(self):
        """POST /simulation/replay with valid datetimes returns report."""
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

        yaml_rules = """- rule_id: test_rule
  name: Test Rule
  action_type: tool.execute
  effect: allow
"""
        resp = client.post("/policy-console/simulation/replay", data={
            "candidate_yaml": yaml_rules,
            "since": "2026-01-01T00:00:00Z",
            "until": "2026-12-31T23:59:59Z",
        })
        assert resp.status_code == 200
        assert "Policy Simulation Report" in resp.text

    # --- Test 10: No traceback leakage ---

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

        # Test on validate endpoint
        resp = client.post("/policy-console/simulation/validate", data={
            "candidate_yaml": "",
        })
        assert "Traceback" not in resp.text

        # Test on replay endpoint (no service configured → 404)
        resp = client.post("/policy-console/simulation/replay", data={
            "candidate_yaml": "- rule_id: x\n  name: x\n  action_type: tool.execute\n  effect: allow",
        })
        assert "Traceback" not in resp.text

    # --- Test 11: Replay POST with empty YAML shows error ---

    def test_replay_post_empty_yaml(self):
        """POST /simulation/replay with empty YAML shows error."""
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

        resp = client.post("/policy-console/simulation/replay", data={
            "candidate_yaml": "",
        })
        assert resp.status_code == 200
        assert "required" in resp.text.lower()
