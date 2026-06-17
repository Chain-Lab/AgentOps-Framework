"""Phase 44 Task 7: Tests for console notification and expiration pages."""

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
from agent_app.governance.policy_notification import (
    PolicyNotificationMessage,
    PolicyNotificationRule,
    PolicyNotificationRuleStatus,
    PolicyNotificationSeverity,
    PolicyNotificationStatus,
)
from agent_app.runtime.policy_notification_store import PolicyNotificationStore
from agent_app.runtime.policy_notification_rule_store import InMemoryPolicyNotificationRuleStore
from agent_app.runtime.policy_notification_service import PolicyNotificationService
from agent_app.runtime.policy_expiration_service import PolicyExpirationService

from datetime import datetime, timezone


class TestPolicyNotificationConsole:
    """Tests for Phase 44 Task 7 console notification and expiration pages."""

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

    def _make_notification_service(self):
        """Create a PolicyNotificationService for testing."""
        from agent_app.runtime.policy_notification_store import InMemoryPolicyNotificationStore
        from agent_app.governance.audit import InMemoryAuditLogger

        store = InMemoryPolicyNotificationStore()
        rule_store = InMemoryPolicyNotificationRuleStore()
        service = PolicyNotificationService(
            notification_store=store,
            rule_store=rule_store,
            channels={},
            audit_logger=InMemoryAuditLogger(),
        )
        return service

    def _make_expiration_service(self):
        """Create a PolicyExpirationService for testing."""
        from agent_app.governance.audit import InMemoryAuditLogger
        return PolicyExpirationService(
            audit_logger=InMemoryAuditLogger(),
        )

    def _seed_notification(self, service):
        """Add a test notification directly to the store."""
        msg = PolicyNotificationMessage(
            notification_id="pn_test001",
            event_type="policy.rollout.approval.expired",
            severity=PolicyNotificationSeverity.WARNING,
            title="Approval Expired",
            body="A rollout approval has expired.",
            created_at=datetime.now(timezone.utc),
            status=PolicyNotificationStatus.PENDING,
        )
        _run_async(service._store.create(msg))

    def _seed_rule(self, service):
        """Add a test rule directly to the rule store."""
        rule = PolicyNotificationRule(
            rule_id="pnr_test001",
            name="Expired Approval Rule",
            event_types=["policy.rollout.approval.expired"],
            severity=PolicyNotificationSeverity.WARNING,
            channels=["log"],
            status=PolicyNotificationRuleStatus.ENABLED,
        )
        _run_async(service._rule_store.create(rule))

    # --- Test 1: Notifications page renders ---

    def test_notifications_page_renders(self):
        """GET /policy-console/notifications returns 200."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_notification_service()

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            notification_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/notifications")
        assert resp.status_code == 200
        assert "notification" in resp.text.lower()

    # --- Test 2: Send-pending POST works ---

    def test_send_pending_post_works(self):
        """POST /notifications/send-pending returns 200."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_notification_service()
        self._seed_notification(service)

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            notification_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/notifications/send-pending")
        assert resp.status_code == 200
        assert "sent" in resp.text.lower() or "1" in resp.text

    # --- Test 3: Rules page renders ---

    def test_rules_page_renders(self):
        """GET /policy-console/notification-rules returns 200."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_notification_service()
        self._seed_rule(service)

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            notification_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/notification-rules")
        assert resp.status_code == 200
        assert "rule" in resp.text.lower()
        assert "pnr_test001" in resp.text

    # --- Test 4: Expiration page renders ---

    def test_expiration_page_renders(self):
        """GET /policy-console/expiration returns 200."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_expiration_service()

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            expiration_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/expiration")
        assert resp.status_code == 200
        assert "expiration" in resp.text.lower() or "sweep" in resp.text.lower()

    # --- Test 5: Sweep POST works ---

    def test_sweep_post_works(self):
        """POST /expiration/sweep returns 200 with sweep results."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_expiration_service()

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            expiration_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/expiration/sweep")
        assert resp.status_code == 200
        assert "sweep" in resp.text.lower() or "completed" in resp.text.lower()

    # --- Test 6: Rule enable POST works ---

    def test_rule_enable_post_works(self):
        """POST /notification-rules/{rule_id}/enable enables a rule."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service = self._make_notification_service()
        self._seed_rule(service)

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            notification_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        # First disable the rule
        _run_async(service._rule_store.disable("pnr_test001"))

        # Then enable it via console
        resp = client.post("/policy-console/notification-rules/pnr_test001/enable")
        assert resp.status_code == 200
        assert "enabled" in resp.text.lower() or "pnr_test001" in resp.text

    # --- Test 7: No service renders gracefully ---

    def test_notifications_no_service_renders_gracefully(self):
        """GET /notifications without service renders gracefully."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/notifications")
        assert resp.status_code == 200
        assert "not configured" in resp.text.lower() or "notification" in resp.text.lower()
