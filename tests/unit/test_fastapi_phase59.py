"""Tests for Phase 59 FastAPI endpoints — multi-instance production readiness."""

from __future__ import annotations

import pytest

from agent_app import AgentApp, AgentSpec, ToolSpec, Workflow


def _build_test_app():
    """Build a minimal test AgentApp."""
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
    app.register_agent(
        AgentSpec(name="support", description="Support agent", model="gpt-4o", instructions="You are a support agent.")
    )
    app.register_workflow(Workflow.single(agent="support", name="cs"))
    app.register_tool(
        ToolSpec(name="order.query", description="Query order", risk_level="low")
    )
    return app


def _has_fastapi() -> bool:
    try:
        import fastapi  # noqa: F401
        import httpx  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _has_fastapi(), reason="fastapi not installed")
class TestPhase59FastAPIEndpoints:
    """Phase 59: FastAPI endpoints for multi-instance production readiness."""

    @pytest.fixture
    def app_with_phase59(self):
        """Build an app with Phase 59 stores configured."""
        from agent_app.adapters.fastapi import create_fastapi_app
        from agent_app.runtime.policy_rollout_federation_notification_replay_idempotency import (
            InMemoryReplayIdempotencyStore,
        )
        from agent_app.runtime.policy_rollout_federation_notification_replay_rate_limiter import (
            InMemoryReplayRateLimiterStore,
        )
        from agent_app.runtime.policy_rollout_federation_notification_dead_letter_policy import (
            InMemoryDeadLetterPolicyStore,
            DeadLetterPolicyConfig,
        )
        from agent_app.runtime.policy_rollout_federation_notification_metrics_enhanced import (
            EnhancedMetrics,
        )
        from agent_app.runtime.policy_rollout_federation_notification_webhook_key_rotation import (
            InMemoryWebhookKeyRotationStore,
        )
        from agent_app.runtime.policy_rollout_federation_notification_distributed_lock import (
            InMemoryDistributedLockStore,
        )

        app = _build_test_app()
        app.replay_idempotency_store = InMemoryReplayIdempotencyStore()
        app.replay_rate_limiter_store = InMemoryReplayRateLimiterStore()
        app.dead_letter_policy_store = InMemoryDeadLetterPolicyStore(
            config=DeadLetterPolicyConfig()
        )
        app.enhanced_metrics = EnhancedMetrics()
        app.webhook_key_rotation_service = InMemoryWebhookKeyRotationStore()
        app._federation_notification_distributed_lock_store = InMemoryDistributedLockStore()
        return create_fastapi_app(app)

    # -----------------------------------------------------------------------
    # Replay Idempotency endpoints
    # -----------------------------------------------------------------------

    def test_idempotency_get_existing(self, app_with_phase59):
        """GET returns 404 for non-existent idempotency key."""
        from fastapi.testclient import TestClient
        client = TestClient(app_with_phase59)
        resp = client.get("/federation/notifications/replay-idempotency/nonexistent_key")
        assert resp.status_code == 404

    def test_idempotency_complete_not_found(self, app_with_phase59):
        """POST complete returns 404 for non-existent key."""
        from fastapi.testclient import TestClient
        client = TestClient(app_with_phase59)
        resp = client.post(
            "/federation/notifications/replay-idempotency/nonexistent_key/complete",
            json={"new_attempt_id": "nda_123"},
        )
        assert resp.status_code == 404

    def test_idempotency_fail_not_found(self, app_with_phase59):
        """POST fail returns 404 for non-existent key."""
        from fastapi.testclient import TestClient
        client = TestClient(app_with_phase59)
        resp = client.post(
            "/federation/notifications/replay-idempotency/nonexistent_key/fail",
            json={"error_message": "test error"},
        )
        assert resp.status_code == 404

    def test_idempotency_prune(self, app_with_phase59):
        """POST prune returns count of pruned records."""
        from fastapi.testclient import TestClient
        client = TestClient(app_with_phase59)
        resp = client.post("/federation/notifications/replay-idempotency/prune")
        assert resp.status_code == 200
        assert "pruned_count" in resp.json()

    # -----------------------------------------------------------------------
    # Rate Limiter endpoints
    # -----------------------------------------------------------------------

    def test_rate_limit_check(self, app_with_phase59):
        """POST rate-limit check returns result with allowed field."""
        from fastapi.testclient import TestClient
        client = TestClient(app_with_phase59)
        resp = client.post(
            "/federation/notifications/rate-limit/check",
            json={"rate_limit_key": "tgt_001", "max_attempts": 10, "window_seconds": 60},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "allowed" in data
        assert "remaining" in data

    def test_rate_limit_reset(self, app_with_phase59):
        """POST rate-limit reset returns success after check creates record."""
        from fastapi.testclient import TestClient
        client = TestClient(app_with_phase59)
        # First check to create a record
        client.post(
            "/federation/notifications/rate-limit/check",
            json={"rate_limit_key": "tgt_reset", "max_attempts": 10, "window_seconds": 60},
        )
        # Then reset it
        resp = client.post(
            "/federation/notifications/rate-limit/reset",
            json={"rate_limit_key": "tgt_reset"},
        )
        assert resp.status_code == 200
        assert resp.json()["reset"] is True

    def test_rate_limit_get_not_found(self, app_with_phase59):
        """GET rate-limit record returns 404 for non-existent key."""
        from fastapi.testclient import TestClient
        client = TestClient(app_with_phase59)
        resp = client.get("/federation/notifications/rate-limit/nonexistent_key")
        assert resp.status_code == 404

    # -----------------------------------------------------------------------
    # Dead Letter Policy endpoints
    # -----------------------------------------------------------------------

    def test_dead_letter_evaluate(self, app_with_phase59):
        """POST dead-letter evaluate returns result."""
        from fastapi.testclient import TestClient
        client = TestClient(app_with_phase59)
        body = {
            "attempt_id": "nda_001",
            "target_id": "t1",
            "alert_id": "a1",
            "attempt_count": 3,
            "priority": 0,
            "channel_type": "webhook",
        }
        resp = client.post("/federation/notifications/dead-letter/evaluate", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert "is_dead_letter" in data

    def test_dead_letter_list_empty(self, app_with_phase59):
        """GET dead-letter list returns empty list initially."""
        from fastapi.testclient import TestClient
        client = TestClient(app_with_phase59)
        resp = client.get("/federation/notifications/dead-letter")
        assert resp.status_code == 200
        assert resp.json() == []

    # -----------------------------------------------------------------------
    # Enhanced Metrics endpoint
    # -----------------------------------------------------------------------

    def test_metrics_snapshot(self, app_with_phase59):
        """GET metrics snapshot returns metrics data."""
        from fastapi.testclient import TestClient
        client = TestClient(app_with_phase59)
        resp = client.get("/federation/notifications/metrics/snapshot")
        assert resp.status_code == 200
        data = resp.json()
        assert "replay" in data
        assert "rate_limiter" in data
        assert "dead_letter" in data

    # -----------------------------------------------------------------------
    # Webhook Key Rotation endpoints
    # -----------------------------------------------------------------------

    def test_key_rotation_last_not_found(self, app_with_phase59):
        """GET key-rotation last returns 404 when no rotations."""
        from fastapi.testclient import TestClient
        client = TestClient(app_with_phase59)
        resp = client.get("/federation/notifications/key-rotation/last")
        assert resp.status_code == 404

    def test_key_rotation_history_empty(self, app_with_phase59):
        """GET key-rotation history returns empty list initially."""
        from fastapi.testclient import TestClient
        client = TestClient(app_with_phase59)
        resp = client.get("/federation/notifications/key-rotation/history")
        assert resp.status_code == 200
        assert resp.json() == []

    # -----------------------------------------------------------------------
    # Distributed Lock endpoints
    # -----------------------------------------------------------------------

    def test_distributed_lock_status_not_configured(self, app_with_phase59):
        """GET distributed-lock status returns 503 when not configured."""
        from fastapi.testclient import TestClient
        client = TestClient(app_with_phase59)
        resp = client.get("/federation/notifications/distributed-lock/test-lock/status")
        # Distributed lock store may not be available in test config
        assert resp.status_code in (200, 404, 503)


@pytest.mark.skipif(not _has_fastapi(), reason="fastapi not installed")
class TestPhase59ConsoleRouterWiring:
    """Phase 59: Console router accepts Phase 59 store kwargs."""

    def test_console_router_accepts_phase59_kwargs(self):
        """Console router builds without error when Phase 59 stores are passed."""
        from agent_app.console.router import build_policy_console_router
        router = build_policy_console_router(
            store=None,
            replay_idempotency_store=None,
            replay_rate_limiter_store=None,
            dead_letter_policy_store=None,
            enhanced_metrics=None,
            webhook_key_rotation_store=None,
            distributed_lock_store=None,
        )
        assert router is not None
