"""Phase 43 Task 7: Tests for console rollout step gate pages."""

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
from agent_app.runtime.policy_rollout_gate_service import RolloutGateAutomationService
from agent_app.governance.policy_rollout import (
    RolloutPlan,
    RolloutStep,
    RolloutStepType,
    RolloutStepStatus,
    RolloutPlanStatus,
    RolloutGateMode,
    RolloutGateFailureAction,
)
from agent_app.runtime.policy_rollout_store import InMemoryRolloutPlanStore

from datetime import datetime, timezone


class TestPolicyRolloutGateConsole:
    """Tests for Phase 43 Task 7 console rollout step gate pages."""

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
        """Create a RolloutGateAutomationService for testing."""
        requirement_store = InMemoryReleaseGateRequirementStore()
        audit_logger = InMemoryAuditLogger()
        release_service = ReleaseGateAutomationService(
            requirement_store=requirement_store,
            audit_logger=audit_logger,
        )
        return RolloutGateAutomationService(
            release_gate_automation_service=release_service,
            audit_logger=audit_logger,
        )

    def _make_rollout_store_with_plan(self, rollout_id="ro_001", step_id="s1",
                                       gate_mode=RolloutGateMode.MANUAL,
                                       requires_gate=True):
        """Create an InMemoryRolloutPlanStore with a test plan."""
        store = InMemoryRolloutPlanStore()
        step = RolloutStep(
            step_id=step_id,
            step_type=RolloutStepType.ACTIVATE,
            environment="prod",
            requires_simulation_gate=requires_gate,
            simulation_gate_mode=gate_mode,
            simulation_gate_failure_action=RolloutGateFailureAction.BLOCK,
        )
        plan = RolloutPlan(
            rollout_id=rollout_id,
            name="Test Rollout",
            bundle_id="bundle_001",
            status=RolloutPlanStatus.ACTIVE,
            steps=[step],
            created_by="tester",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        _run_async(store.create(plan))
        return store

    # --- Test 1: Gate page renders for existing rollout step ---

    def test_gate_page_renders_for_existing_step(self):
        """GET /rollouts/{rollout_id}/steps/{step_id}/gate returns 200 with gate form."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        rollout_store = self._make_rollout_store_with_plan()

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_store=rollout_store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/rollouts/ro_001/steps/s1/gate")
        assert resp.status_code == 200
        assert "gate" in resp.text.lower()
        assert "ro_001" in resp.text
        assert "s1" in resp.text

    # --- Test 2: Shows existing gate requirement if present ---

    def test_shows_existing_gate_requirement(self):
        """GET gate page shows gate status when a requirement exists."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        rollout_store = self._make_rollout_store_with_plan(
            rollout_id="ro_002", step_id="s1",
            gate_mode=RolloutGateMode.MANUAL,
        )
        service = self._make_automation_service()
        # Pre-create a requirement via the release gate service
        _run_async(
            service._release_gate.require_gate_for_promotion(
                promotion_id="ro_002:s1",
            )
        )

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_store=rollout_store,
            rollout_gate_automation_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/rollouts/ro_002/steps/s1/gate")
        assert resp.status_code == 200
        # Should show gate mode "manual" from the step
        assert "manual" in resp.text.lower()

    # --- Test 3: POST run works ---

    def test_post_run_works(self):
        """POST /gate/run returns 200 with status page."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        rollout_store = self._make_rollout_store_with_plan(
            rollout_id="ro_003", step_id="s1",
            gate_mode=RolloutGateMode.AUTO,
        )
        service = self._make_automation_service()

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_store=rollout_store,
            rollout_gate_automation_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/rollouts/ro_003/steps/s1/gate/run", data={
            "actor_id": "console_user",
        })
        assert resp.status_code == 200
        # Should render the status template
        assert "ro_003" in resp.text
        assert "s1" in resp.text

    # --- Test 4: POST attach works ---

    def test_post_attach_works(self):
        """POST /gate/attach with gate_result_id attaches result."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        rollout_store = self._make_rollout_store_with_plan(
            rollout_id="ro_004", step_id="s1",
            gate_mode=RolloutGateMode.MANUAL,
        )
        service = self._make_automation_service()
        # Pre-create a requirement so attach can find it
        _run_async(
            service._release_gate.require_gate_for_promotion(
                promotion_id="ro_004:s1",
            )
        )

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_store=rollout_store,
            rollout_gate_automation_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/rollouts/ro_004/steps/s1/gate/attach", data={
            "gate_result_id": "pgr_test123",
            "simulation_id": "sim_test456",
        })
        assert resp.status_code == 200
        assert "satisfied" in resp.text.lower()

    # --- Test 5: Errors render clearly (no traceback leakage) ---

    def test_errors_render_clearly_no_traceback(self):
        """POST /gate/run without service renders error without traceback."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        rollout_store = self._make_rollout_store_with_plan(
            rollout_id="ro_005", step_id="s1",
        )

        # No rollout_gate_automation_service configured
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_store=rollout_store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/rollouts/ro_005/steps/s1/gate/run", data={})
        assert resp.status_code == 200
        assert "error" in resp.text.lower()
        # Ensure no traceback leakage
        assert "Traceback" not in resp.text

    # --- Test 6: No service renders gracefully ---

    def test_no_service_renders_gracefully(self):
        """GET gate page renders even without rollout gate automation service."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        rollout_store = self._make_rollout_store_with_plan(
            rollout_id="ro_006", step_id="s1",
        )

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_store=rollout_store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/rollouts/ro_006/steps/s1/gate")
        assert resp.status_code == 200
        assert "gate" in resp.text.lower()
        # Should show gate mode from step data even without service
        assert "manual" in resp.text.lower()

    # --- Test 7: Gate page for missing rollout shows error ---

    def test_gate_page_missing_rollout_shows_error(self):
        """GET gate page for non-existent rollout shows error."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        # Empty store, no plans
        rollout_store = InMemoryRolloutPlanStore()

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_store=rollout_store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/rollouts/nonexistent/steps/s1/gate")
        assert resp.status_code == 200
        assert "error" in resp.text.lower()
        assert "not found" in resp.text.lower()

    # --- Test 8: Attach without gate_result_id shows error ---

    def test_attach_without_gate_result_id_shows_error(self):
        """POST /gate/attach without gate_result_id renders error."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        rollout_store = self._make_rollout_store_with_plan(
            rollout_id="ro_008", step_id="s1",
        )
        service = self._make_automation_service()

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_store=rollout_store,
            rollout_gate_automation_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/rollouts/ro_008/steps/s1/gate/attach", data={})
        assert resp.status_code == 200
        assert "error" in resp.text.lower()
        assert "required" in resp.text.lower()
