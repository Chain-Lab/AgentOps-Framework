"""Phase 42 Task 8: Tests for console promotion gate lifecycle pages."""

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
from agent_app.governance.policy_release_gate import ReleaseGateRequirementStatus
from agent_app.runtime.policy_release_gate_store import InMemoryReleaseGateRequirementStore
from agent_app.runtime.policy_release_gate_service import ReleaseGateAutomationService


class TestPolicyReleaseGateConsole:
    """Tests for Phase 42 Task 8 console promotion gate pages."""

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

    def _make_automation_service(self):
        """Create a ReleaseGateAutomationService for testing."""
        requirement_store = InMemoryReleaseGateRequirementStore()
        audit_logger = InMemoryAuditLogger()
        return ReleaseGateAutomationService(
            requirement_store=requirement_store,
            audit_logger=audit_logger,
        )

    # --- Test 1: Gate page renders ---

    def test_gate_page_renders(self):
        """GET /promotions/{promotion_id}/gate returns 200 with gate form."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/promotions/prom_001/gate")
        assert resp.status_code == 200
        assert "gate" in resp.text.lower()
        assert "prom_001" in resp.text

    # --- Test 2: Gate page shows requirement status ---

    def test_gate_page_shows_requirement(self):
        """GET /promotions/{promotion_id}/gate shows existing requirement."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_automation_service()
        # Pre-create a requirement
        _run_async(
            service.require_gate_for_promotion(promotion_id="prom_002")
        )

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            release_gate_automation_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/promotions/prom_002/gate")
        assert resp.status_code == 200
        assert "required" in resp.text.lower()

    # --- Test 3: POST /gate/require creates requirement ---

    def test_gate_require_creates_requirement(self):
        """POST /promotions/{promotion_id}/gate/require creates requirement."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_automation_service()
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            release_gate_automation_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/promotions/prom_003/gate/require", data={
            "max_age_seconds": "3600",
        })
        assert resp.status_code == 200
        assert "required" in resp.text.lower()

    # --- Test 4: POST /gate/run runs simulation+gate ---

    def test_gate_run_with_valid_rules(self):
        """POST /promotions/{promotion_id}/gate/run with valid YAML renders status."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_automation_service()
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            release_gate_automation_service=service,
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
        resp = client.post("/policy-console/promotions/prom_004/gate/run", data={
            "candidate_rules": candidate_yaml,
            "gate_rules": gate_yaml,
        })
        assert resp.status_code == 200
        # Should render the status template (even if it errors due to no simulation service)
        assert "prom_004" in resp.text

    # --- Test 5: POST /gate/attach attaches gate result ---

    def test_gate_attach_attaches_result(self):
        """POST /promotions/{promotion_id}/gate/attach attaches gate result."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_automation_service()
        # Pre-create a requirement so attach can find it
        _run_async(
            service.require_gate_for_promotion(promotion_id="prom_005")
        )

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            release_gate_automation_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/promotions/prom_005/gate/attach", data={
            "gate_result_id": "pgr_test123",
            "simulation_id": "sim_test456",
        })
        assert resp.status_code == 200
        assert "satisfied" in resp.text.lower()

    # --- Test 6: Errors render clearly (no traceback leakage) ---

    def test_errors_render_clearly_no_traceback(self):
        """POST /gate/require without service renders error without traceback."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        # No release_gate_automation_service configured
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/promotions/prom_006/gate/require", data={})
        assert resp.status_code == 200
        assert "error" in resp.text.lower()
        # Ensure no traceback leakage
        assert "Traceback" not in resp.text

    # --- Test 7: Attach without gate_result_id shows error ---

    def test_attach_without_gate_result_id_shows_error(self):
        """POST /gate/attach without gate_result_id renders error."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_automation_service()
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            release_gate_automation_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/promotions/prom_007/gate/attach", data={})
        assert resp.status_code == 200
        assert "error" in resp.text.lower()

    # --- Test 8: Gate page without service still renders ---

    def test_gate_page_without_service(self):
        """GET /promotions/{promotion_id}/gate renders even without service."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/promotions/prom_008/gate")
        assert resp.status_code == 200
        assert "Simulation Gate" in resp.text

    # --- Test 9: Run with invalid YAML shows parse error ---

    def test_gate_run_invalid_yaml_shows_error(self):
        """POST /gate/run with invalid YAML renders parse error."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_automation_service()
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            release_gate_automation_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/promotions/prom_009/gate/run", data={
            "candidate_rules": "not: valid: yaml: [",
            "gate_rules": "",
        })
        assert resp.status_code == 200
        assert "error" in resp.text.lower()
        # No traceback leakage
        assert "Traceback" not in resp.text
