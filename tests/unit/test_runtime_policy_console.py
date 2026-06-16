"""Phase 38 Task 8: Tests for console runtime policy pages."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from conftest import _run_async

from agent_app.config.schema import PolicyConsoleConfig
from agent_app.core.context import RunContext
from agent_app.governance.policy_enforcement import PolicyActionType
from agent_app.governance.runtime_policy import (
    RuntimePolicyEffect,
    RuntimePolicyRule,
    RuntimePolicyRuleStatus,
)
from agent_app.runtime.runtime_policy_store import InMemoryRuntimePolicyStore
from agent_app.runtime.runtime_policy_evaluator import RuntimePolicyEvaluator
from agent_app.runtime.policy_enforcement_service import PolicyEnforcementService


class TestRuntimePolicyConsole:
    """Tests for Phase 38 Task 8 console runtime policy pages."""

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
        from starlette.testclient import TestClient
        return TestClient(api)

    def _make_rule(
        self,
        rule_id="rpr_test1",
        name="Test Rule",
        action_type=PolicyActionType.TOOL_EXECUTE,
        effect=RuntimePolicyEffect.ALLOW,
        status=RuntimePolicyRuleStatus.ENABLED,
        tool_name=None,
        risk_level=None,
    ):
        """Create a test RuntimePolicyRule."""
        return RuntimePolicyRule(
            rule_id=rule_id,
            name=name,
            action_type=action_type,
            effect=effect,
            status=status,
            tool_name=tool_name,
            risk_level=risk_level,
        )

    def _make_enforcement_service(self, store=None):
        """Create a PolicyEnforcementService with a store."""
        if store is None:
            store = InMemoryRuntimePolicyStore()
        evaluator = RuntimePolicyEvaluator(policy_store=store)
        return PolicyEnforcementService(evaluator=evaluator), store

    # --- Test 1: Runtime rules list page ---

    def test_runtime_rules_list_renders(self):
        """GET /runtime-rules returns 200 and renders rules list."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        store = InMemoryRuntimePolicyStore()
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            runtime_policy_store=store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/runtime-rules")
        assert resp.status_code == 200
        assert "Runtime Policy Rules" in resp.text

    # --- Test 2: Runtime rule detail page ---

    def test_runtime_rule_detail_renders(self):
        """GET /runtime-rules/{id} returns 200 and shows rule detail."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        store = InMemoryRuntimePolicyStore()
        rule = self._make_rule("rpr_detail_test", name="Detail Test Rule")
        _run_async(store.create(rule))

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            runtime_policy_store=store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/runtime-rules/rpr_detail_test")
        assert resp.status_code == 200
        assert "rpr_detail_test" in resp.text
        assert "Detail Test Rule" in resp.text

    # --- Test 3: Runtime rule enable/disable ---

    def test_runtime_rule_enable_disable(self):
        """POST enable/disable redirects correctly."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        store = InMemoryRuntimePolicyStore()
        rule = self._make_rule("rpr_toggle_test", status=RuntimePolicyRuleStatus.ENABLED)
        _run_async(store.create(rule))

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            runtime_policy_store=store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        # Disable
        resp = client.post("/policy-console/runtime-rules/rpr_toggle_test/disable", follow_redirects=False)
        assert resp.status_code == 303
        updated = _run_async(store.get("rpr_toggle_test"))
        assert updated.status == RuntimePolicyRuleStatus.DISABLED

        # Enable
        resp = client.post("/policy-console/runtime-rules/rpr_toggle_test/enable", follow_redirects=False)
        assert resp.status_code == 303
        updated = _run_async(store.get("rpr_toggle_test"))
        assert updated.status == RuntimePolicyRuleStatus.ENABLED

    # --- Test 4: Runtime evaluate form renders ---

    def test_runtime_evaluate_form_renders(self):
        """GET /runtime-evaluate returns 200 and shows evaluation form."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)
        resp = client.get("/policy-console/runtime-evaluate")
        assert resp.status_code == 200
        assert "Runtime Policy Evaluation" in resp.text

    # --- Test 5: Runtime evaluate submit ---

    def test_runtime_evaluate_submit(self):
        """POST evaluate returns decision result."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service, store = self._make_enforcement_service()
        # Add a deny rule
        rule = self._make_rule(
            "rpr_eval_test",
            name="Deny dangerous tools",
            effect=RuntimePolicyEffect.DENY,
            tool_name="dangerous_tool",
        )
        _run_async(store.create(rule))

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            policy_enforcement_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/runtime-evaluate", data={
            "action_type": "tool.execute",
            "tool_name": "dangerous_tool",
            "actor_id": "test_user",
        })
        assert resp.status_code == 200
        assert "denied" in resp.text.lower()

    # --- Test 6: No traceback leakage ---

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

        # GET non-existent rule detail when store not configured -> 404
        resp = client.get("/policy-console/runtime-rules/rpr_nonexistent")
        assert "Traceback" not in resp.text

    # --- Test 7: Create rule via POST ---

    def test_runtime_rule_create(self):
        """POST /runtime-rules creates a new rule."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        store = InMemoryRuntimePolicyStore()
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            runtime_policy_store=store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/runtime-rules", data={
            "rule_id": "rpr_created_test",
            "name": "Created Rule",
            "action_type": "tool.execute",
            "effect": "allow",
        })
        assert resp.status_code == 200
        # Verify rule was created in store
        rule = _run_async(store.get("rpr_created_test"))
        assert rule is not None
        assert rule.name == "Created Rule"

    # --- Test 8: Detail page not found ---

    def test_runtime_rule_detail_not_found(self):
        """GET /runtime-rules/{id} with non-existent ID shows error."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        store = InMemoryRuntimePolicyStore()
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            runtime_policy_store=store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.get("/policy-console/runtime-rules/rpr_nonexistent")
        assert resp.status_code == 200
        assert "not found" in resp.text.lower()

    # --- Test 9: Enable non-existent rule returns 404 ---

    def test_enable_nonexistent_rule_returns_404(self):
        """POST enable on non-existent rule returns 404."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        store = InMemoryRuntimePolicyStore()
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            runtime_policy_store=store,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/runtime-rules/rpr_nonexistent/enable")
        assert resp.status_code == 404

    # --- Test 10: Evaluate with allow rule ---

    def test_runtime_evaluate_allow(self):
        """POST evaluate with matching allow rule returns allowed."""
        api = self._make_app()
        from agent_app.console.router import build_policy_console_router

        service, store = self._make_enforcement_service()
        rule = self._make_rule(
            "rpr_allow_test",
            name="Allow safe tools",
            effect=RuntimePolicyEffect.ALLOW,
            tool_name="safe_tool",
        )
        _run_async(store.create(rule))

        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
            policy_enforcement_service=service,
        )
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = self._get_client(api)

        resp = client.post("/policy-console/runtime-evaluate", data={
            "action_type": "tool.execute",
            "tool_name": "safe_tool",
            "actor_id": "test_user",
        })
        assert resp.status_code == 200
        assert "allowed" in resp.text.lower()
