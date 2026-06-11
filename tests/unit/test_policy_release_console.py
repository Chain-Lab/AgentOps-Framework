"""Phase 29: Tests for policy release console pages (bundles and gates)."""

from __future__ import annotations

import pytest

from agent_app.config.schema import PolicyConsoleConfig
from agent_app.governance.policy_bundle import (
    PolicyBundle,
    PolicyBundleStatus,
    compute_config_hash,
)
from agent_app.governance.policy_gate import (
    PolicyGateResult,
    PolicyGateStatus,
)
from agent_app.runtime.policy_gate_store import InMemoryPolicyGateStore
from agent_app.runtime.policy_release import PolicyReleaseService


class TestReleaseConsoleRouter:
    """Tests for Phase 29 console bundle and gate pages."""

    def _make_app(self):
        """Create a minimal FastAPI app for console testing."""
        from agent_app import AgentApp
        from agent_app.governance.audit import InMemoryAuditLogger
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

    def test_bundles_page_returns_200(self):
        """Bundles page returns 200 when store is provided."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.governance.policy_bundle import InMemoryPolicyBundleStore

        store = InMemoryPolicyBundleStore()
        router = build_policy_console_router(
            store=None, config=PolicyConsoleConfig(enabled=True),
            bundle_store=store, gate_store=None,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/bundles")
        assert resp.status_code == 200
        assert "Policy Bundles" in resp.text

    def test_bundles_page_empty_state(self):
        """Bundles page shows empty state when no bundles."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.governance.policy_bundle import InMemoryPolicyBundleStore

        store = InMemoryPolicyBundleStore()
        router = build_policy_console_router(
            store=None, config=PolicyConsoleConfig(enabled=True),
            bundle_store=store, gate_store=None,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/bundles")
        assert resp.status_code == 200
        assert "No policy bundles yet" in resp.text

    def test_bundles_page_lists_bundles(self):
        """Bundles page lists pre-populated bundles."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.governance.policy_bundle import InMemoryPolicyBundleStore

        store = InMemoryPolicyBundleStore()
        bundle = PolicyBundle(
            bundle_id="pb_test123",
            name="test-bundle",
            version="1.0.0",
            status=PolicyBundleStatus.ACTIVE,
            config_hash=compute_config_hash("test"),
            created_by="admin",
        )
        store._bundles["pb_test123"] = bundle
        store._order.append("pb_test123")

        router = build_policy_console_router(
            store=None, config=PolicyConsoleConfig(enabled=True),
            bundle_store=store, gate_store=None,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/bundles")
        assert resp.status_code == 200
        assert "test-bundle" in resp.text
        assert "pb_test123" in resp.text
        assert "1.0.0" in resp.text

    def test_bundle_detail_returns_200(self):
        """Bundle detail page returns 200 for existing bundle."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.governance.policy_bundle import InMemoryPolicyBundleStore

        store = InMemoryPolicyBundleStore()
        bundle = PolicyBundle(
            bundle_id="pb_detail_test",
            name="detail-bundle",
            version="2.0.0",
            status=PolicyBundleStatus.DRAFT,
            config_hash=compute_config_hash("config"),
            config_path="agentapp.yaml",
            description="Test detail bundle",
            created_by="tester",
        )
        store._bundles["pb_detail_test"] = bundle
        store._order.append("pb_detail_test")

        router = build_policy_console_router(
            store=None, config=PolicyConsoleConfig(enabled=True),
            bundle_store=store, gate_store=None,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/bundles/pb_detail_test")
        assert resp.status_code == 200
        assert "detail-bundle" in resp.text
        assert "2.0.0" in resp.text

    def test_bundle_detail_not_found(self):
        """Bundle detail returns 200 with not-found message for missing ID."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.governance.policy_bundle import InMemoryPolicyBundleStore

        store = InMemoryPolicyBundleStore()
        router = build_policy_console_router(
            store=None, config=PolicyConsoleConfig(enabled=True),
            bundle_store=store, gate_store=None,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/bundles/pb_nonexistent")
        assert resp.status_code == 200
        assert "not found" in resp.text.lower()

    def test_gates_page_returns_200(self):
        """Gates page returns 200 when store is provided."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.runtime.policy_gate_store import InMemoryPolicyGateStore

        store = InMemoryPolicyGateStore()
        router = build_policy_console_router(
            store=None, config=PolicyConsoleConfig(enabled=True),
            bundle_store=None, gate_store=store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/gates")
        assert resp.status_code == 200
        assert "Policy Gate Results" in resp.text

    def test_gates_page_empty_state(self):
        """Gates page shows empty state when no gate results."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.runtime.policy_gate_store import InMemoryPolicyGateStore

        store = InMemoryPolicyGateStore()
        router = build_policy_console_router(
            store=None, config=PolicyConsoleConfig(enabled=True),
            bundle_store=None, gate_store=store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/gates")
        assert resp.status_code == 200
        assert "No gate results yet" in resp.text

    def test_gates_page_lists_results(self):
        """Gates page lists pre-populated gate results."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.runtime.policy_gate_store import InMemoryPolicyGateStore

        store = InMemoryPolicyGateStore()
        gate = PolicyGateResult(
            gate_result_id="gr_test123",
            bundle_id="pb_test123",
            status=PolicyGateStatus.PASSED,
            passed=True,
            total_decisions=10,
            changed_decisions=0,
            failed_replays=0,
            changed_ratio=0.0,
            replay_id="replay_123",
            rule_results=[
                {
                    "rule_name": "safe_default",
                    "passed": True,
                    "status": PolicyGateStatus.PASSED,
                    "actual": 0,
                    "threshold": 0,
                    "message": "OK",
                }
            ],
            created_by="admin",
        )
        store._results["gr_test123"] = gate
        store._order.append("gr_test123")

        router = build_policy_console_router(
            store=None, config=PolicyConsoleConfig(enabled=True),
            bundle_store=None, gate_store=store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/gates")
        assert resp.status_code == 200
        assert "gr_test123" in resp.text
        assert "pb_test123" in resp.text

    def test_gate_detail_returns_200(self):
        """Gate detail page returns 200 for existing result."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.runtime.policy_gate_store import InMemoryPolicyGateStore

        store = InMemoryPolicyGateStore()
        gate = PolicyGateResult(
            gate_result_id="gr_detail_test",
            bundle_id="pb_detail",
            status=PolicyGateStatus.PASSED,
            passed=True,
            total_decisions=5,
            changed_decisions=0,
            failed_replays=0,
            changed_ratio=0.0,
            replay_id="replay_detail",
            rule_results=[],
            created_by="admin",
        )
        store._results["gr_detail_test"] = gate
        store._order.append("gr_detail_test")

        router = build_policy_console_router(
            store=None, config=PolicyConsoleConfig(enabled=True),
            bundle_store=None, gate_store=store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/gates/gr_detail_test")
        assert resp.status_code == 200
        assert "gr_detail_test" in resp.text

    def test_gate_detail_not_found(self):
        """Gate detail returns 200 with not-found message for missing ID."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router
        from agent_app.runtime.policy_gate_store import InMemoryPolicyGateStore

        store = InMemoryPolicyGateStore()
        router = build_policy_console_router(
            store=None, config=PolicyConsoleConfig(enabled=True),
            bundle_store=None, gate_store=store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/gates/gr_nonexistent")
        assert resp.status_code == 200
        assert "not found" in resp.text.lower()

    def test_nav_links_present(self):
        """Base template includes Bundles and Gates nav links."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        router = build_policy_console_router(
            store=None, config=PolicyConsoleConfig(enabled=True),
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/bundles")
        assert resp.status_code == 200
        assert "/policy-console/bundles" in resp.text
        assert "/policy-console/gates" in resp.text

    def test_no_stores_shows_not_configured(self):
        """Pages show 'not configured' when stores are None."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        router = build_policy_console_router(
            store=None, config=PolicyConsoleConfig(enabled=True),
            bundle_store=None, gate_store=None,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/bundles")
        assert resp.status_code == 200
        assert "not configured" in resp.text.lower()

        resp = client.get("/policy-console/gates")
        assert resp.status_code == 200
        assert "not configured" in resp.text.lower()
