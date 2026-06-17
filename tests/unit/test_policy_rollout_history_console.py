"""Phase 45 Task 7: Tests for console rollout history, timeline, and analytics pages."""

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
from agent_app.governance.policy_rollout_history import (
    RolloutHistoryEvent,
    RolloutHistoryEventType,
)
from agent_app.runtime.policy_rollout_history_service import RolloutHistoryService
from agent_app.runtime.policy_rollout_history_store import InMemoryRolloutHistoryStore

from datetime import datetime, timezone


class TestPolicyRolloutHistoryConsole:
    """Tests for Phase 45 Task 7 console rollout history/timeline/analytics pages."""

    def _make_app(self):
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

    def _get_client(self, api):
        return TestClient(api)

    def _make_history_service(self):
        """Create a RolloutHistoryService for testing."""
        store = InMemoryRolloutHistoryStore()
        service = RolloutHistoryService(history_store=store)
        return service

    def _seed_event(self, service, rollout_id="ro_test001"):
        """Add a test history event directly to the store."""
        event = RolloutHistoryEvent(
            history_event_id="rhe_test001",
            rollout_id=rollout_id,
            event_type=RolloutHistoryEventType.ROLLOUT_STARTED,
            actor_id="actor1",
            message="Rollout started",
            created_at=datetime.now(timezone.utc),
        )
        _run_async(service._history_store.append(event))

    # --- Test 1: Rollout history page renders ---

    def test_rollout_history_page_renders(self):
        """GET /rollouts/{id}/history renders."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_history_service()
        self._seed_event(service)

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_history_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/rollouts/ro_test001/history")
        assert resp.status_code == 200
        assert "history" in resp.text.lower() or "rollout" in resp.text.lower()

    # --- Test 2: Rollout history page not configured ---

    def test_rollout_history_page_not_configured(self):
        """Shows message when service not configured."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/rollouts/ro_test001/history")
        assert resp.status_code == 200
        assert "not configured" in resp.text.lower()

    # --- Test 3: Rollout timeline page renders ---

    def test_rollout_timeline_page_renders(self):
        """GET /rollouts/{id}/timeline renders."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_history_service()
        self._seed_event(service)

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_history_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/rollouts/ro_test001/timeline")
        assert resp.status_code == 200
        assert "timeline" in resp.text.lower() or "rollout" in resp.text.lower()

    # --- Test 4: Rollout analytics page renders ---

    def test_rollout_analytics_page_renders(self):
        """GET /rollout-analytics renders."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_history_service()

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_history_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/rollout-analytics")
        assert resp.status_code == 200
        assert "analytics" in resp.text.lower() or "rollout" in resp.text.lower()

    # --- Test 5: Rollout analytics POST generates report ---

    def test_rollout_analytics_post_generates_report(self):
        """POST /rollout-analytics with time window generates report."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_history_service()
        self._seed_event(service)

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_history_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post(
            "/policy-console/rollout-analytics",
            data={"since": "2025-01-01T00:00:00", "until": "2027-01-01T00:00:00"},
        )
        assert resp.status_code == 200
        assert "analytics" in resp.text.lower() or "rollout" in resp.text.lower()

    # --- Test 6: Rollout analytics POST invalid datetime ---

    def test_rollout_analytics_post_invalid_datetime(self):
        """POST /rollout-analytics with invalid datetime handles gracefully."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_history_service()

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_history_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post(
            "/policy-console/rollout-analytics",
            data={"since": "not-a-date", "until": "also-not-a-date"},
        )
        assert resp.status_code == 200
        assert "invalid" in resp.text.lower() or "analytics" in resp.text.lower()

    # --- Test 7: Rollout detail page has history/timeline links ---

    def test_rollout_detail_has_history_links(self):
        """Rollout detail page includes history/timeline links."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        from agent_app.governance.policy_rollout import (
            RolloutPlan,
            RolloutStep,
            RolloutPlanStatus,
            RolloutStepStatus,
        )
        from agent_app.runtime.policy_rollout_store import InMemoryRolloutPlanStore

        now = datetime.now(timezone.utc)
        store = InMemoryRolloutPlanStore()
        plan = RolloutPlan(
            rollout_id="ro_linktest",
            name="Link Test",
            bundle_id="pb_test",
            steps=[
                RolloutStep(
                    step_id="rs_link1",
                    step_type="promote_ring",
                    environment="staging",
                    ring_name="canary",
                    status=RolloutStepStatus.PENDING,
                ),
            ],
            status=RolloutPlanStatus.DRAFT,
            created_by="tester",
            created_at=now,
            updated_at=now,
        )
        _run_async(store.create(plan))

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            rollout_store=store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/rollouts/ro_linktest")
        assert resp.status_code == 200
        assert "/policy-console/rollouts/ro_linktest/history" in resp.text
        assert "/policy-console/rollouts/ro_linktest/timeline" in resp.text
