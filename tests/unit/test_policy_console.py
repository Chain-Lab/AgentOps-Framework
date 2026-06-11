"""Phase 26: Tests for policy console config and router."""

from __future__ import annotations

import pytest

from agent_app.config.schema import (
    AppConfig,
    GovernanceConfig,
    PolicyConsoleConfig,
    PolicyDecisionStoreConfig,
)


class TestPolicyConsoleConfig:
    """Tests for PolicyConsoleConfig defaults and YAML loading."""

    def test_default_disabled(self):
        """Default console config is disabled."""
        cfg = PolicyConsoleConfig()
        assert cfg.enabled is False
        assert cfg.base_path == "/policy-console"
        assert cfg.title == "Agent App Policy Console"
        assert cfg.page_size == 50

    def test_enabled_from_dict(self):
        """Enabled console config from dict."""
        cfg = PolicyConsoleConfig(enabled=True, base_path="/console", title="My Console", page_size=25)
        assert cfg.enabled is True
        assert cfg.base_path == "/console"
        assert cfg.title == "My Console"
        assert cfg.page_size == 25

    def test_in_governance_config(self):
        """PolicyConsoleConfig is accessible from GovernanceConfig."""
        gov = GovernanceConfig(policy_console=PolicyConsoleConfig(enabled=True))
        assert gov.policy_console is not None
        assert gov.policy_console.enabled is True

    def test_in_app_config(self):
        """Full AppConfig loads policy_console from YAML-style dict."""
        raw = {
            "app": {"name": "test"},
            "governance": {
                "policy_console": {
                    "enabled": True,
                    "base_path": "/policy-console",
                    "title": "Test Console",
                    "page_size": 100,
                },
            },
        }
        cfg = AppConfig(**raw)
        assert cfg.governance.policy_console is not None
        assert cfg.governance.policy_console.enabled is True
        assert cfg.governance.policy_console.base_path == "/policy-console"
        assert cfg.governance.policy_console.title == "Test Console"
        assert cfg.governance.policy_console.page_size == 100

    def test_default_governance_has_no_console(self):
        """Default GovernanceConfig has no console config."""
        gov = GovernanceConfig()
        assert gov.policy_console is None


class TestConsoleRouterRegistration:
    """Tests for console router being registered conditionally."""

    def _make_app_with_store(self, store_traces=None):
        """Create a FastAPI app with an in-memory store pre-populated."""
        from agent_app import AgentApp
        from agent_app.governance.audit import InMemoryAuditLogger
        from agent_app.governance.approval import InMemoryApprovalStore
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.adapters.fastapi import create_fastapi_app
        from agent_app.governance.policy_decision_store import InMemoryPolicyDecisionStore

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

        store = InMemoryPolicyDecisionStore()
        if store_traces:
            for t in store_traces:
                store._traces.append(t)
        app.policy_decision_store = store

        return create_fastapi_app(app), store

    def _get_client(self, api):
        from starlette.testclient import TestClient
        return TestClient(api)

    def test_disabled_no_console_routes(self):
        """Console disabled: /policy-console/ returns 404."""
        api, _ = self._make_app_with_store()
        client = self._get_client(api)
        resp = client.get("/policy-console/")
        assert resp.status_code == 404

    def test_enabled_dashboard_returns_200(self):
        """Console enabled: /policy-console/ returns 200."""
        from agent_app.config.schema import PolicyConsoleConfig
        api, _ = self._make_app_with_store()
        # Manually mount console
        from agent_app.console.router import build_policy_console_router
        from agent_app.governance.policy_decision_store import InMemoryPolicyDecisionStore
        store = InMemoryPolicyDecisionStore()
        router = build_policy_console_router(store=store, config=PolicyConsoleConfig(enabled=True))
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/")
        assert resp.status_code == 200
        assert "Agent App Policy Console" in resp.text

    def test_enabled_decisions_page_returns_200(self):
        """Console enabled: /policy-console/decisions returns 200."""
        from agent_app.config.schema import PolicyConsoleConfig
        api, _ = self._make_app_with_store()
        from agent_app.console.router import build_policy_console_router
        from agent_app.governance.policy_decision_store import InMemoryPolicyDecisionStore
        store = InMemoryPolicyDecisionStore()
        router = build_policy_console_router(store=store, config=PolicyConsoleConfig(enabled=True))
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/decisions")
        assert resp.status_code == 200

    def test_enabled_report_page_returns_200(self):
        """Console enabled: /policy-console/report returns 200."""
        from agent_app.config.schema import PolicyConsoleConfig
        api, _ = self._make_app_with_store()
        from agent_app.console.router import build_policy_console_router
        from agent_app.governance.policy_decision_store import InMemoryPolicyDecisionStore
        store = InMemoryPolicyDecisionStore()
        router = build_policy_console_router(store=store, config=PolicyConsoleConfig(enabled=True))
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/report")
        assert resp.status_code == 200

    def test_decision_detail_returns_200(self):
        """Decision detail page returns 200 for existing decision."""
        from agent_app.config.schema import PolicyConsoleConfig
        from agent_app.governance.policy import PolicyAction, PolicyDecisionTrace
        from datetime import datetime, timezone
        api, _ = self._make_app_with_store()
        from agent_app.console.router import build_policy_console_router
        from agent_app.governance.policy_decision_store import InMemoryPolicyDecisionStore

        store = InMemoryPolicyDecisionStore()
        trace = PolicyDecisionTrace(
            decision_id="dec_test_1",
            action=PolicyAction.ALLOW,
            reason="Test",
            matched_conditions={},
            context_summary={},
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        store._traces.append(trace)

        router = build_policy_console_router(store=store, config=PolicyConsoleConfig(enabled=True))
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/decisions/dec_test_1")
        assert resp.status_code == 200

    def test_decision_detail_404_for_missing(self):
        """Decision detail page returns 200 with not-found message for missing ID."""
        from agent_app.config.schema import PolicyConsoleConfig
        api, _ = self._make_app_with_store()
        from agent_app.console.router import build_policy_console_router
        from agent_app.governance.policy_decision_store import InMemoryPolicyDecisionStore
        store = InMemoryPolicyDecisionStore()
        router = build_policy_console_router(store=store, config=PolicyConsoleConfig(enabled=True))
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/decisions/dec_missing")
        assert resp.status_code == 200
        assert "not found" in resp.text.lower()
