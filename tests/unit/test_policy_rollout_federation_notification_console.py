"""Phase 49 Task 10: Console federation notification and escalation page tests."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_app.console.router import build_policy_console_router
from agent_app.governance.policy_rollout_federation_notification import (
    FederationNotificationChannel,
    FederationNotificationEventType,
    FederationNotificationMessage,
    FederationNotificationStatus,
)
from agent_app.runtime.policy_rollout_federation_notification_store import InMemoryFederationNotificationStore


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _notification(
    notification_id: str = "fn_test_1",
    approval_id: str = "fa_test",
    federation_id: str | None = "frp_test",
    event_type: FederationNotificationEventType = FederationNotificationEventType.APPROVAL_CREATED,
    channel: FederationNotificationChannel = FederationNotificationChannel.EMAIL,
    status: FederationNotificationStatus = FederationNotificationStatus.PENDING,
    recipients: list[str] | None = None,
    subject: str | None = "Approval Created",
    body: str = "A federation approval has been created.",
) -> FederationNotificationMessage:
    return FederationNotificationMessage(
        notification_id=notification_id,
        approval_id=approval_id,
        federation_id=federation_id,
        event_type=event_type,
        channel=channel,
        recipients=recipients or ["admin@example.com"],
        subject=subject,
        body=body,
        status=status,
        attempt_count=0,
        max_attempts=3,
        created_at=_now(),
    )


def _client(
    notification_store=None,
    escalation_worker=None,
) -> TestClient:
    app = FastAPI()
    router = build_policy_console_router(
        store=None,
        federation_notification_store=notification_store,
        federation_escalation_worker=escalation_worker,
    )
    app.include_router(router, prefix="/policy-console")
    return TestClient(app)


def _store_with_notifications() -> InMemoryFederationNotificationStore:
    """Build a notification store with test data."""
    store = InMemoryFederationNotificationStore()
    msgs = [
        _notification(
            notification_id="fn_test_1",
            approval_id="fa_approve_1",
            event_type=FederationNotificationEventType.APPROVAL_CREATED,
            channel=FederationNotificationChannel.EMAIL,
        ),
        _notification(
            notification_id="fn_test_2",
            approval_id="fa_approve_1",
            event_type=FederationNotificationEventType.APPROVAL_ESCALATED,
            channel=FederationNotificationChannel.SLACK,
            status=FederationNotificationStatus.SENT,
        ),
        _notification(
            notification_id="fn_test_3",
            approval_id="fa_approve_2",
            event_type=FederationNotificationEventType.APPROVAL_REJECTED,
            channel=FederationNotificationChannel.WEBHOOK,
            status=FederationNotificationStatus.PENDING,
        ),
    ]
    loop = asyncio.new_event_loop()
    try:
        for m in msgs:
            loop.run_until_complete(store.create(m))
    finally:
        loop.close()
    return store


class TestFederationNotificationListConsole:
    def test_notification_list_page_renders_with_notifications(self) -> None:
        store = _store_with_notifications()
        client = _client(notification_store=store)
        response = client.get("/policy-console/federation/notifications")
        assert response.status_code == 200
        assert "fn_test_1" in response.text
        assert "fn_test_3" in response.text
        assert "Federation Notifications" in response.text

    def test_notification_list_page_renders_when_store_not_configured(self) -> None:
        client = _client(notification_store=None)
        response = client.get("/policy-console/federation/notifications")
        assert response.status_code == 200
        assert "not configured" in response.text


class TestFederationNotificationDetailConsole:
    def test_notification_detail_page_renders(self) -> None:
        store = _store_with_notifications()
        client = _client(notification_store=store)
        response = client.get("/policy-console/federation/notifications/fn_test_1")
        assert response.status_code == 200
        assert "fn_test_1" in response.text
        assert "fa_approve_1" in response.text
        assert "Federation Notification" in response.text

    def test_notification_detail_page_404_for_missing_notification(self) -> None:
        store = _store_with_notifications()
        client = _client(notification_store=store)
        response = client.get("/policy-console/federation/notifications/fn_nonexistent")
        assert response.status_code == 404
        assert "not found" in response.text


class TestFederationApprovalNotificationsConsole:
    def test_approval_notifications_page_renders(self) -> None:
        store = _store_with_notifications()
        client = _client(notification_store=store)
        response = client.get("/policy-console/federation/approvals/fa_approve_1/notifications")
        assert response.status_code == 200
        assert "fn_test_1" in response.text
        assert "fn_test_2" in response.text

    def test_approval_notifications_page_renders_when_store_not_configured(self) -> None:
        client = _client(notification_store=None)
        response = client.get("/policy-console/federation/approvals/fa_approve_1/notifications")
        assert response.status_code == 200
        assert "not configured" in response.text


class TestFederationEscalationDashboardConsole:
    def test_escalation_dashboard_renders_with_worker(self) -> None:
        worker = MagicMock()
        worker.__class__.__name__ = "FederationEscalationWorker"
        client = _client(escalation_worker=worker)
        response = client.get("/policy-console/federation/escalations")
        assert response.status_code == 200
        assert "Escalation Dashboard" in response.text

    def test_escalation_dashboard_renders_without_worker(self) -> None:
        client = _client(escalation_worker=None)
        response = client.get("/policy-console/federation/escalations")
        assert response.status_code == 200
        assert "not configured" in response.text

    def test_escalation_dashboard_shows_worker_type(self) -> None:
        worker = MagicMock()
        worker.__class__.__name__ = "FederationEscalationWorker"
        client = _client(escalation_worker=worker)
        response = client.get("/policy-console/federation/escalations")
        assert response.status_code == 200
        assert "FederationEscalationWorker" in response.text


class TestNotificationListShowsApprovalFilter:
    def test_notification_list_shows_approval_id_filter(self) -> None:
        store = _store_with_notifications()
        client = _client(notification_store=store)
        response = client.get("/policy-console/federation/approvals/fa_approve_1/notifications")
        assert response.status_code == 200
        assert "fa_approve_1" in response.text


class TestNotificationDetailShowsFields:
    def test_notification_detail_shows_all_fields(self) -> None:
        store = _store_with_notifications()
        client = _client(notification_store=store)
        response = client.get("/policy-console/federation/notifications/fn_test_1")
        assert response.status_code == 200
        assert "approval.created" in response.text
        assert "email" in response.text
        assert "admin@example.com" in response.text


class TestNotificationDetailStoreNotConfigured:
    def test_notification_detail_not_configured(self) -> None:
        client = _client(notification_store=None)
        response = client.get("/policy-console/federation/notifications/fn_any")
        assert response.status_code == 200
        assert "not configured" in response.text


class TestNotificationListTableColumns:
    def test_notification_list_shows_channel_column(self) -> None:
        store = _store_with_notifications()
        client = _client(notification_store=store)
        response = client.get("/policy-console/federation/notifications")
        assert response.status_code == 200
        assert "Channel" in response.text

    def test_notification_list_shows_event_type_column(self) -> None:
        store = _store_with_notifications()
        client = _client(notification_store=store)
        response = client.get("/policy-console/federation/notifications")
        assert response.status_code == 200
        assert "Event Type" in response.text

    def test_notification_list_shows_status_column(self) -> None:
        store = _store_with_notifications()
        client = _client(notification_store=store)
        response = client.get("/policy-console/federation/notifications")
        assert response.status_code == 200
        assert "Status" in response.text


class TestNotificationDetailLinks:
    def test_notification_detail_links_back_to_list(self) -> None:
        store = _store_with_notifications()
        client = _client(notification_store=store)
        response = client.get("/policy-console/federation/notifications/fn_test_1")
        assert response.status_code == 200
        assert "/federation/notifications" in response.text
