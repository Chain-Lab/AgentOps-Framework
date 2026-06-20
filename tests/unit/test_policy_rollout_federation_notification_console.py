"""Phase 50 Task 7: DLQ and worker status console page tests."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_app.console.router import build_policy_console_router
from agent_app.governance.policy_rollout_federation_notification import (
    FederationNotificationDeadLetter,
    FederationNotificationDLQReason,
    FederationNotificationDLQStatus,
)
from agent_app.runtime.policy_rollout_federation_notification_dlq_store import (
    InMemoryFederationNotificationDLQStore,
)
from agent_app.runtime.policy_rollout_federation_scheduled_worker import (
    FederationScheduledWorker,
    FederationScheduledWorkerStatus,
    FederationScheduledWorkerState,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dlq_entry(
    dlq_id: str = "fdlq_test_1",
    notification_id: str = "fn_test_1",
    approval_id: str | None = "fa_test",
    federation_id: str | None = "frp_test",
    channel: str = "email",
    reason: FederationNotificationDLQReason = FederationNotificationDLQReason.MAX_RETRIES_EXCEEDED,
    status: FederationNotificationDLQStatus = FederationNotificationDLQStatus.PENDING,
    failure_count: int = 3,
    last_error: str | None = "Connection refused",
    payload: dict | None = None,
    metadata: dict | None = None,
) -> FederationNotificationDeadLetter:
    return FederationNotificationDeadLetter(
        dlq_id=dlq_id,
        notification_id=notification_id,
        approval_id=approval_id,
        federation_id=federation_id,
        channel=channel,
        adapter="smtp",
        recipient="admin@example.com",
        reason=reason,
        status=status,
        failure_count=failure_count,
        last_error=last_error,
        payload=payload or {"subject": "Test notification", "body": "Test"},
        metadata=metadata or {"source": "test"},
        created_at=_now(),
        updated_at=_now(),
    )


def _store_with_dlq_entries() -> InMemoryFederationNotificationDLQStore:
    """Build a DLQ store with test data."""
    store = InMemoryFederationNotificationDLQStore()
    entries = [
        _dlq_entry(
            dlq_id="fdlq_test_1",
            notification_id="fn_test_1",
            channel="email",
        ),
        _dlq_entry(
            dlq_id="fdlq_test_2",
            notification_id="fn_test_2",
            channel="slack",
            status=FederationNotificationDLQStatus.RETRIED,
        ),
        _dlq_entry(
            dlq_id="fdlq_test_3",
            notification_id="fn_test_3",
            channel="webhook",
        ),
    ]
    loop = asyncio.new_event_loop()
    try:
        for e in entries:
            loop.run_until_complete(store.create(e))
    finally:
        loop.close()
    return store


def _client(
    dlq_store=None,
    scheduled_worker=None,
) -> TestClient:
    app = FastAPI()
    router = build_policy_console_router(
        store=None,
        federation_dlq_store=dlq_store,
        federation_scheduled_worker=scheduled_worker,
    )
    app.include_router(router, prefix="/policy-console")
    return TestClient(app)


def _mock_worker(state: FederationScheduledWorkerState | None = None) -> MagicMock:
    """Build a mock FederationScheduledWorker that returns the given state."""
    worker = MagicMock(spec=FederationScheduledWorker)
    if state is None:
        state = FederationScheduledWorkerState(
            worker_id="fsw_mock_1",
            status=FederationScheduledWorkerStatus.STOPPED,
            interval_seconds=60,
            tick_count=5,
            last_tick_at=_now(),
            last_error=None,
            started_at=_now(),
            stopped_at=None,
        )
    worker.status.return_value = state
    return worker


class TestFederationDLQConsole:
    """Tests for DLQ console pages (Phase 50)."""

    def test_dlq_list_page_renders(self) -> None:
        store = _store_with_dlq_entries()
        client = _client(dlq_store=store)
        response = client.get("/policy-console/federation/notifications/dlq")
        assert response.status_code == 200
        assert "Federation Notification DLQ" in response.text

    def test_dlq_list_page_with_entries(self) -> None:
        store = _store_with_dlq_entries()
        client = _client(dlq_store=store)
        response = client.get("/policy-console/federation/notifications/dlq")
        assert response.status_code == 200
        assert "fdlq_test_1" in response.text
        assert "fdlq_test_3" in response.text

    def test_dlq_list_page_with_status_filter(self) -> None:
        store = _store_with_dlq_entries()
        client = _client(dlq_store=store)
        response = client.get("/policy-console/federation/notifications/dlq?status=retried")
        assert response.status_code == 200
        assert "fdlq_test_2" in response.text

    def test_dlq_list_page_empty(self) -> None:
        store = InMemoryFederationNotificationDLQStore()
        client = _client(dlq_store=store)
        response = client.get("/policy-console/federation/notifications/dlq")
        assert response.status_code == 200
        assert "No DLQ entries" in response.text

    def test_dlq_detail_page_renders(self) -> None:
        store = _store_with_dlq_entries()
        client = _client(dlq_store=store)
        response = client.get("/policy-console/federation/notifications/dlq/fdlq_test_1")
        assert response.status_code == 200
        assert "fdlq_test_1" in response.text
        assert "fn_test_1" in response.text
        assert "DLQ Entry" in response.text

    def test_dlq_detail_page_not_found(self) -> None:
        store = _store_with_dlq_entries()
        client = _client(dlq_store=store)
        response = client.get("/policy-console/federation/notifications/dlq/fdlq_nonexistent")
        assert response.status_code == 200
        assert "not found" in response.text

    def test_dlq_detail_page_shows_payload(self) -> None:
        store = _store_with_dlq_entries()
        client = _client(dlq_store=store)
        response = client.get("/policy-console/federation/notifications/dlq/fdlq_test_1")
        assert response.status_code == 200
        assert "Payload" in response.text
        assert "subject" in response.text

    def test_dlq_detail_page_shows_all_fields(self) -> None:
        store = _store_with_dlq_entries()
        client = _client(dlq_store=store)
        response = client.get("/policy-console/federation/notifications/dlq/fdlq_test_1")
        assert response.status_code == 200
        assert "Channel" in response.text
        assert "Reason" in response.text
        assert "Failure Count" in response.text
        assert "Metadata" in response.text
        assert "Adapter" in response.text
        assert "Recipient" in response.text
        assert "Last Error" in response.text


class TestFederationWorkerConsole:
    """Tests for worker console pages (Phase 50)."""

    def test_worker_status_page_renders(self) -> None:
        worker = _mock_worker()
        client = _client(scheduled_worker=worker)
        response = client.get("/policy-console/federation/workers")
        assert response.status_code == 200
        assert "Federation Worker Status" in response.text

    def test_worker_status_page_shows_state(self) -> None:
        worker = _mock_worker()
        client = _client(scheduled_worker=worker)
        response = client.get("/policy-console/federation/workers")
        assert response.status_code == 200
        assert "fsw_mock_1" in response.text
        assert "stopped" in response.text
        assert "60" in response.text

    def test_worker_status_page_not_configured(self) -> None:
        client = _client(scheduled_worker=None)
        response = client.get("/policy-console/federation/workers")
        assert response.status_code == 200
        assert "not configured" in response.text

    def test_worker_status_page_shows_error(self) -> None:
        state = FederationScheduledWorkerState(
            worker_id="fsw_err_1",
            status=FederationScheduledWorkerStatus.FAILED,
            interval_seconds=30,
            tick_count=3,
            last_tick_at=_now(),
            last_error="Connection timeout",
            started_at=_now(),
            stopped_at=None,
        )
        worker = _mock_worker(state=state)
        client = _client(scheduled_worker=worker)
        response = client.get("/policy-console/federation/workers")
        assert response.status_code == 200
        assert "Connection timeout" in response.text
        assert "failed" in response.text
