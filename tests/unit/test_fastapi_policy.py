"""Phase 24: Tests for FastAPI policy diagnostics endpoints."""

from __future__ import annotations

import pytest

from agent_app.adapters.fastapi import create_fastapi_app


def _make_app_with_policy(policy_engine=None):
    """Create a FastAPI app with optional policy engine."""
    from agent_app import AgentApp, AgentSpec, ToolSpec, Workflow
    from agent_app.governance.audit import InMemoryAuditLogger
    from agent_app.governance.approval import InMemoryApprovalStore
    from agent_app.registry.agent_registry import AgentRegistry
    from agent_app.registry.tool_registry import ToolRegistry
    from agent_app.registry.workflow_registry import WorkflowRegistry

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
    if policy_engine is not None:
        app.policy_engine = policy_engine
    app._config = _make_config_with_policies()
    return create_fastapi_app(app)


def _make_config_with_policies():
    from agent_app.config.schema import AppConfig, GovernanceConfig, PolicyEngineConfig

    return AppConfig(
        app={"name": "test"},
        agents=[],
        governance=GovernanceConfig(
            policies=PolicyEngineConfig(
                enabled=True,
                default_action="allow",
                rules=[
                    {
                        "name": "require_refund_approval",
                        "when": {"tool_name": "refund.request"},
                        "then": {
                            "action": "require_approval",
                            "reason": "Refunds need approval",
                            "ttl_seconds": 1800,
                        },
                    },
                    {
                        "name": "deny_dangerous",
                        "when": {"tool_name": "dangerous.delete"},
                        "then": {"action": "deny", "reason": "Blocked"},
                    },
                    {
                        "name": "audit_billing",
                        "when": {"tool_name": "billing.query"},
                        "then": {"action": "audit_only", "reason": "Compliance"},
                    },
                ],
            )
        ),
    )


class TestFastAPIPolicyEndpoints:
    def test_get_policies_enabled(self):
        """GET /policies returns summary when enabled."""
        api = _make_app_with_policy()
        client = _get_client(api)
        resp = client.get("/policies")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["rule_count"] == 3
        assert len(data["rules"]) == 3

    def test_get_policies_no_sensitive_data(self):
        """GET /policies does not leak sensitive config."""
        api = _make_app_with_policy()
        client = _get_client(api)
        resp = client.get("/policies")
        data = resp.json()
        # Should not contain raw YAML or internal paths
        assert "db_path" not in str(data).lower()
        assert "password" not in str(data).lower()

    def test_post_policies_validate_valid(self):
        """POST /policies/validate returns valid=True for good config."""
        api = _make_app_with_policy()
        client = _get_client(api)
        resp = client.post("/policies/validate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True

    def test_post_policies_validate_no_issues(self):
        """POST /policies/validate returns empty issues for valid config."""
        api = _make_app_with_policy()
        client = _get_client(api)
        resp = client.post("/policies/validate")
        data = resp.json()
        assert len(data["issues"]) == 0

    def test_post_policies_simulate_allow(self):
        """POST /policies/simulate returns allow for safe tool."""
        api = _make_app_with_policy()
        client = _get_client(api)
        resp = client.post("/policies/simulate", json={
            "tool_name": "order.query",
            "risk_level": "low",
            "tenant_id": "t1",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "allow"
        assert data["allowed"] is True

    def test_post_policies_simulate_deny(self):
        """POST /policies/simulate returns deny for blocked tool."""
        api = _make_app_with_policy()
        client = _get_client(api)
        resp = client.post("/policies/simulate", json={
            "tool_name": "dangerous.delete",
            "risk_level": "critical",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "deny"
        assert data["allowed"] is False
        assert data["rule_name"] == "deny_dangerous"

    def test_post_policies_simulate_require_approval(self):
        """POST /policies/simulate returns require_approval."""
        api = _make_app_with_policy()
        client = _get_client(api)
        resp = client.post("/policies/simulate", json={
            "tool_name": "refund.request",
            "risk_level": "high",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "require_approval"
        assert data["requires_approval"] is True
        assert data["rule_name"] == "require_refund_approval"
        assert data["ttl_seconds"] == 1800

    def test_post_policies_explain(self):
        """POST /policies/explain returns trace with matched conditions."""
        api = _make_app_with_policy()
        client = _get_client(api)
        resp = client.post("/policies/explain", json={
            "tool_name": "refund.request",
            "risk_level": "high",
            "tenant_id": "eval_tenant",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "require_approval"
        assert data["rule_name"] == "require_refund_approval"
        assert data["matched_conditions"]["tool_name"] == "refund.request"
        assert data["reason"] == "Refunds need approval"
        assert "tool_name" in data["context_summary"]

    def test_post_policies_explain_no_match(self):
        """POST /policies/explain returns default for unmatched tool."""
        api = _make_app_with_policy()
        client = _get_client(api)
        resp = client.post("/policies/explain", json={
            "tool_name": "unknown.tool",
            "risk_level": "low",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "allow"
        assert data["rule_name"] is None

    def test_get_policy_decisions_empty(self):
        """GET /policy-decisions returns empty list when no policy events."""
        api = _make_app_with_policy()
        client = _get_client(api)
        resp = client.get("/policy-decisions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_policy_decisions_with_filter(self):
        """GET /policy-decisions supports tenant_id filter."""
        api = _make_app_with_policy()
        client = _get_client(api)
        resp = client.get("/policy-decisions?tenant_id=t1&limit=10")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


def _get_client(api):
    from starlette.testclient import TestClient
    return TestClient(api)
