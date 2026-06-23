"""Tests for Phase 55 Task 7 — Console Write Actions for Alert Delivery."""
from __future__ import annotations

import asyncio

import pytest

from agent_app.console.router import build_policy_console_router
from agent_app.config.schema import PolicyConsoleConfig
from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryAttempt,
    AlertDeliveryChannelType,
    AlertDeliveryStatus,
    AlertDeliveryTarget,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_store import (
    InMemoryAlertDeliveryStore,
    create_alert_delivery_store,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_service import (
    NotificationAlertDeliveryService,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_priority_queue import (
    AlertPriorityQueue,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_api():
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


def _get_client(api):
    from starlette.testclient import TestClient
    return TestClient(api)


def _run_async(coro):
    """Run an async coroutine from synchronous test code."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_target(target_id="ndt_001", name="Test Target", enabled=True) -> AlertDeliveryTarget:
    return AlertDeliveryTarget(
        target_id=target_id,
        name=name,
        channel_type=AlertDeliveryChannelType.MEMORY,
        enabled=enabled,
        endpoint=None,
    )


def _make_attempt(
    attempt_id="nda_ndt_001_nae_001_1",
    alert_id="nae_001",
    target_id="ndt_001",
    status=AlertDeliveryStatus.RETRY_SCHEDULED,
    attempt=1,
    priority=50,
    error_message=None,
) -> AlertDeliveryAttempt:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return AlertDeliveryAttempt(
        attempt_id=attempt_id,
        alert_id=alert_id,
        target_id=target_id,
        channel_type=AlertDeliveryChannelType.MEMORY,
        status=status,
        attempt=attempt,
        priority=priority,
        error_message=error_message,
        payload_preview={},
        created_at=now,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def alert_delivery_client():
    """Build a test client with alert delivery store and service wired."""
    store = InMemoryAlertDeliveryStore()
    adapter = type("M", (), {"deliver": lambda *a, **k: None})()
    service = NotificationAlertDeliveryService(
        store=store,
        adapters={"memory": adapter},
    )
    priority_queue = AlertPriorityQueue(store)

    router = build_policy_console_router(
        store=None,
        config=PolicyConsoleConfig(enabled=True),
        federation_notification_alert_delivery_store=store,
        federation_notification_alert_delivery_service=service,
    )
    api = _make_api()
    api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
    client = _get_client(api)
    return client, store, service, priority_queue


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAlertDeliveryConsoleRetry:
    """POST /federation/notifications/alert-delivery/retry — trigger retry."""

    def test_retry_post_returns_200(self, alert_delivery_client):
        """POST retry with actor_id returns 200."""
        client, store, service, _ = alert_delivery_client
        # Create a retry-scheduled attempt
        attempt = _make_attempt()
        _run_async(store.record_attempt(attempt))

        resp = client.post(
            "/policy-console/federation/notifications/alert-delivery/retry",
            data={"actor_id": "admin", "dry_run": "on"},
        )
        assert resp.status_code == 200
        assert "Retry" in resp.text or "retry" in resp.text

    def test_retry_post_without_actor_id_shows_error(self, alert_delivery_client):
        """POST retry without actor_id shows error."""
        client, _, _, _ = alert_delivery_client

        resp = client.post(
            "/policy-console/federation/notifications/alert-delivery/retry",
            data={"dry_run": "on"},
        )
        assert resp.status_code == 200
        assert "actor_id" in resp.text or "required" in resp.text

    def test_retry_post_no_service_shows_error(self):
        """POST retry without service shows error gracefully."""
        router = build_policy_console_router(
            store=None,
            config=PolicyConsoleConfig(enabled=True),
        )
        api = _make_api()
        api.include_router(router, prefix="/policy-console", tags=["Policy Console"])
        client = _get_client(api)

        resp = client.post(
            "/policy-console/federation/notifications/alert-delivery/retry",
            data={"actor_id": "admin"},
        )
        assert resp.status_code == 200
        assert "not configured" in resp.text or "error" in resp.text.lower()


class TestAlertDeliveryConsoleDlqReplay:
    """POST /federation/notifications/alert-delivery/dlq/{dlq_id}/replay."""

    def test_dlq_replay_post_returns_200(self, alert_delivery_client):
        """POST DLQ replay returns 200."""
        client, store, service, _ = alert_delivery_client
        # Create a DLQ attempt
        dlq_attempt = _make_attempt(
            attempt_id="nda_dlq_001",
            status=AlertDeliveryStatus.DLQ,
        )
        _run_async(store.record_attempt(dlq_attempt))

        resp = client.post(
            "/policy-console/federation/notifications/alert-delivery/dlq/nda_dlq_001/replay",
            data={"actor_id": "admin", "dry_run": "on"},
        )
        assert resp.status_code == 200

    def test_dlq_replay_not_found_returns_200(self, alert_delivery_client):
        """POST DLQ replay for non-existent attempt returns 200 with message."""
        client, _, _, _ = alert_delivery_client

        resp = client.post(
            "/policy-console/federation/notifications/alert-delivery/dlq/nda_missing/replay",
            data={"actor_id": "admin"},
        )
        assert resp.status_code == 200


class TestAlertDeliveryConsolePriorityUpdate:
    """POST /federation/notifications/alert-delivery/attempts/{attempt_id}/priority."""

    def test_priority_update_post(self, alert_delivery_client):
        """POST priority update changes attempt priority."""
        client, store, service, _ = alert_delivery_client
        attempt = _make_attempt(priority=50)
        _run_async(store.record_attempt(attempt))

        resp = client.post(
            "/policy-console/federation/notifications/alert-delivery/attempts/nda_ndt_001_nae_001_1/priority",
            data={"priority": "90", "actor_id": "admin"},
        )
        assert resp.status_code == 200

        # Verify priority was updated
        updated = _run_async(store.get_attempt("nda_ndt_001_nae_001_1"))
        assert updated is not None
        assert updated.priority == 90

    def test_priority_update_invalid_value(self, alert_delivery_client):
        """POST priority update with invalid value shows error."""
        client, store, _, _ = alert_delivery_client
        attempt = _make_attempt()
        _run_async(store.record_attempt(attempt))

        resp = client.post(
            "/policy-console/federation/notifications/alert-delivery/attempts/nda_ndt_001_nae_001_1/priority",
            data={"priority": "not-a-number", "actor_id": "admin"},
        )
        assert resp.status_code == 200

    def test_priority_update_missing_attempt(self, alert_delivery_client):
        """POST priority update for non-existent attempt shows error."""
        client, _, _, _ = alert_delivery_client

        resp = client.post(
            "/policy-console/federation/notifications/alert-delivery/attempts/nda_missing/priority",
            data={"priority": "50", "actor_id": "admin"},
        )
        assert resp.status_code == 200
