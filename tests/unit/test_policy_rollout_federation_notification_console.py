"""Phase 51 Task 9: Template, preference, and replay console page tests."""
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
    FederationNotificationDeadLetter,
    FederationNotificationDLQReason,
    FederationNotificationDLQStatus,
)
from agent_app.governance.policy_rollout_federation_notification_template import (
    FederationNotificationTemplate,
    FederationNotificationTemplateFormat,
)
from agent_app.governance.policy_rollout_federation_notification_preference import (
    FederationNotificationPreference,
    FederationNotificationPreferenceDecision,
    FederationNotificationPreferenceExplanation,
    FederationNotificationPreferenceSubjectType,
)
from agent_app.runtime.policy_rollout_federation_notification_dlq_store import (
    InMemoryFederationNotificationDLQStore,
)
from agent_app.runtime.policy_rollout_federation_notification_template_store import (
    InMemoryFederationNotificationTemplateStore,
)
from agent_app.runtime.policy_rollout_federation_notification_preference_store import (
    InMemoryFederationNotificationPreferenceStore,
)
from agent_app.runtime.policy_rollout_federation_notification_preference_service import (
    FederationNotificationPreferenceService,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _run_async(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Test data factories
# ---------------------------------------------------------------------------


def _template(
    template_id: str = "fnt_test_1",
    name: str = "Test Template",
    event_type: str | None = "approval.created",
    channel: str | None = "email",
    body_template: str = "Hello {{ name }}, approval {{ approval.id }} created.",
    enabled: bool = True,
    version: int = 1,
) -> FederationNotificationTemplate:
    return FederationNotificationTemplate(
        template_id=template_id,
        name=name,
        event_type=event_type,
        channel=channel,
        body_template=body_template,
        format=FederationNotificationTemplateFormat.TEXT,
        enabled=enabled,
        version=version,
        created_at=_now(),
        updated_at=_now(),
    )


def _preference(
    preference_id: str = "fnp_test_1",
    subject_type: FederationNotificationPreferenceSubjectType = FederationNotificationPreferenceSubjectType.USER,
    subject_id: str = "user_1",
    channel: str | None = "email",
    event_type: str | None = "approval.created",
    decision: FederationNotificationPreferenceDecision = FederationNotificationPreferenceDecision.OPT_IN,
    reason: str | None = "Test preference",
) -> FederationNotificationPreference:
    return FederationNotificationPreference(
        preference_id=preference_id,
        subject_type=subject_type,
        subject_id=subject_id,
        channel=channel,
        event_type=event_type,
        decision=decision,
        reason=reason,
        created_at=_now(),
        updated_at=_now(),
    )


def _dlq_entry_with_replay(
    dlq_id: str = "fdlq_replay_1",
    headers: dict | None = None,
) -> FederationNotificationDeadLetter:
    entry = FederationNotificationDeadLetter(
        dlq_id=dlq_id,
        notification_id="fn_replay_1",
        channel="webhook",
        reason=FederationNotificationDLQReason.MAX_RETRIES_EXCEEDED,
        status=FederationNotificationDLQStatus.PENDING,
        failure_count=3,
        last_error="Connection refused",
        payload={"subject": "Replay test", "body": "Test body"},
        metadata={"source": "test"},
        created_at=_now(),
        updated_at=_now(),
    )
    # Attach Phase 51 replay attributes dynamically
    entry.replay_available = True
    entry.payload_digest = "sha256:abc123def456"
    entry.template_id = "fnt_replay_1"
    entry.template_version = 2
    entry.replay_count = 1
    entry.last_replay_result = "failed"
    entry.headers = headers or {
        "Content-Type": "application/json",
        "Authorization": "Bearer secret-token-12345",
        "X-Signature": "hmac-sha256=deadbeef",
        "X-Request-Id": "req_123",
    }
    return entry


def _client_with_templates():
    """Build a TestClient with a template store containing test data."""
    store = InMemoryFederationNotificationTemplateStore()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(store.create(_template("fnt_test_1", "Email Approval", "approval.created", "email")))
        loop.run_until_complete(store.create(_template("fnt_test_2", "Slack Approval", "approval.approved", "slack")))
        loop.run_until_complete(store.create(_template("fnt_test_3", "Disabled Template", enabled=False)))
    finally:
        loop.close()
    app = FastAPI()
    router = build_policy_console_router(
        store=None,
        federation_notification_template_store=store,
    )
    app.include_router(router, prefix="/policy-console")
    return TestClient(app)


def _client_with_preferences():
    """Build a TestClient with a preference store and service containing test data."""
    store = InMemoryFederationNotificationPreferenceStore()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(store.set_preference(_preference("fnp_test_1", FederationNotificationPreferenceSubjectType.USER, "user_1", "email", "approval.created", FederationNotificationPreferenceDecision.OPT_IN, "Wants email")))
        loop.run_until_complete(store.set_preference(_preference("fnp_test_2", FederationNotificationPreferenceSubjectType.USER, "user_2", "email", "approval.created", FederationNotificationPreferenceDecision.OPT_OUT, "Unsubscribed")))
    finally:
        loop.close()
    service = FederationNotificationPreferenceService(preference_store=store)
    app = FastAPI()
    router = build_policy_console_router(
        store=None,
        federation_notification_preference_store=store,
        federation_notification_preference_service=service,
    )
    app.include_router(router, prefix="/policy-console")
    return TestClient(app)


def _client_with_dlq_replay():
    """Build a TestClient with DLQ entries containing replay info and headers."""
    store = InMemoryFederationNotificationDLQStore()
    loop = asyncio.new_event_loop()
    try:
        entry = _dlq_entry_with_replay()
        loop.run_until_complete(store.create(entry))
    finally:
        loop.close()
    app = FastAPI()
    router = build_policy_console_router(
        store=None,
        federation_dlq_store=store,
    )
    app.include_router(router, prefix="/policy-console")
    return TestClient(app)


# ---------------------------------------------------------------------------
# Template Console Tests
# ---------------------------------------------------------------------------


class TestFederationTemplateConsole:
    """Tests for template console pages (Phase 51)."""

    def test_template_list_page_renders(self) -> None:
        client = _client_with_templates()
        response = client.get("/policy-console/federation/notifications/templates")
        assert response.status_code == 200
        assert "Notification Templates" in response.text

    def test_template_list_with_templates(self) -> None:
        client = _client_with_templates()
        response = client.get("/policy-console/federation/notifications/templates")
        assert response.status_code == 200
        assert "fnt_test_1" in response.text
        assert "fnt_test_2" in response.text
        assert "fnt_test_3" in response.text

    def test_template_detail_page_renders(self) -> None:
        client = _client_with_templates()
        response = client.get("/policy-console/federation/notifications/templates/fnt_test_1")
        assert response.status_code == 200
        assert "Template: Email Approval" in response.text
        assert "fnt_test_1" in response.text
        assert "approval.created" in response.text

    def test_template_detail_not_found(self) -> None:
        client = _client_with_templates()
        response = client.get("/policy-console/federation/notifications/templates/fnt_nonexistent")
        assert response.status_code == 200
        assert "not found" in response.text

    def test_template_body_escaped_in_html(self) -> None:
        """Template body content must be properly escaped — no raw HTML injection."""
        store = InMemoryFederationNotificationTemplateStore()
        dangerous_body = '<script>alert("xss")</script>{{ name }}'
        tmpl = _template(
            template_id="fnt_xss",
            name="XSS Test",
            body_template=dangerous_body,
        )
        _run_async(store.create(tmpl))
        app = FastAPI()
        router = build_policy_console_router(
            store=None,
            federation_notification_template_store=store,
        )
        app.include_router(router, prefix="/policy-console")
        client = TestClient(app)
        response = client.get("/policy-console/federation/notifications/templates/fnt_xss")
        assert response.status_code == 200
        # The script tag should be escaped, not rendered as HTML
        assert "<script>" not in response.text
        assert "&lt;script&gt;" in response.text

    def test_template_detail_disabled_badge(self) -> None:
        """Disabled templates should show a DISABLED badge."""
        client = _client_with_templates()
        response = client.get("/policy-console/federation/notifications/templates/fnt_test_3")
        assert response.status_code == 200
        assert "DISABLED" in response.text


# ---------------------------------------------------------------------------
# Preference Console Tests
# ---------------------------------------------------------------------------


class TestFederationPreferenceConsole:
    """Tests for preference console pages (Phase 51)."""

    def test_preference_list_page_renders(self) -> None:
        client = _client_with_preferences()
        response = client.get("/policy-console/federation/notifications/preferences")
        assert response.status_code == 200
        assert "Notification Preferences" in response.text

    def test_preference_list_with_preferences(self) -> None:
        client = _client_with_preferences()
        response = client.get("/policy-console/federation/notifications/preferences")
        assert response.status_code == 200
        assert "fnp_test_1" in response.text
        assert "fnp_test_2" in response.text

    def test_preference_explain_page_renders(self) -> None:
        client = _client_with_preferences()
        response = client.get(
            "/policy-console/federation/notifications/preferences/explain"
            "?subject_type=user&subject_id=user_1&channel=email&event_type=approval.created"
        )
        assert response.status_code == 200
        assert "Preference Explanation" in response.text

    def test_preference_explain_shows_decision(self) -> None:
        client = _client_with_preferences()
        response = client.get(
            "/policy-console/federation/notifications/preferences/explain"
            "?subject_type=user&subject_id=user_1&channel=email&event_type=approval.created"
        )
        assert response.status_code == 200
        assert "opt_in" in response.text

    def test_preference_explain_mandatory(self) -> None:
        """Mandatory events should override user preference."""
        store = InMemoryFederationNotificationPreferenceStore()
        _run_async(store.set_preference(_preference(
            preference_id="fnp_mand",
            subject_id="user_mand",
            decision=FederationNotificationPreferenceDecision.OPT_OUT,
        )))
        service = FederationNotificationPreferenceService(
            preference_store=store,
            mandatory_event_types=["security.alert"],
        )
        app = FastAPI()
        router = build_policy_console_router(
            store=None,
            federation_notification_preference_store=store,
            federation_notification_preference_service=service,
        )
        app.include_router(router, prefix="/policy-console")
        client = TestClient(app)
        response = client.get(
            "/policy-console/federation/notifications/preferences/explain"
            "?subject_type=user&subject_id=user_mand&channel=email&event_type=security.alert"
        )
        assert response.status_code == 200
        assert "Mandatory" in response.text or "mandatory" in response.text.lower()

    def test_preference_explain_no_matching_preference(self) -> None:
        """When no preference matches, the page should show no matching preference."""
        store = InMemoryFederationNotificationPreferenceStore()
        service = FederationNotificationPreferenceService(preference_store=store)
        app = FastAPI()
        router = build_policy_console_router(
            store=None,
            federation_notification_preference_store=store,
            federation_notification_preference_service=service,
        )
        app.include_router(router, prefix="/policy-console")
        client = TestClient(app)
        response = client.get(
            "/policy-console/federation/notifications/preferences/explain"
            "?subject_type=user&subject_id=user_no_match&channel=email&event_type=approval.created"
        )
        assert response.status_code == 200
        # System default should be indicated
        assert "system_default" in response.text.lower() or "system default" in response.text.lower() or "opt_in" in response.text


# ---------------------------------------------------------------------------
# DLQ Replay Console Tests
# ---------------------------------------------------------------------------


class TestFederationDLQReplayConsole:
    """Tests for DLQ replay info on detail page (Phase 51)."""

    def test_dlq_detail_shows_replay_info(self) -> None:
        client = _client_with_dlq_replay()
        response = client.get("/policy-console/federation/notifications/dlq/fdlq_replay_1")
        assert response.status_code == 200
        assert "Replay Information" in response.text
        assert "Replay Available" in response.text

    def test_dlq_detail_shows_digest(self) -> None:
        client = _client_with_dlq_replay()
        response = client.get("/policy-console/federation/notifications/dlq/fdlq_replay_1")
        assert response.status_code == 200
        assert "Payload Digest" in response.text
        assert "sha256:abc123def456" in response.text

    def test_dlq_detail_sensitizes_headers(self) -> None:
        """Auth headers must be redacted — signature keys must NEVER appear."""
        client = _client_with_dlq_replay()
        response = client.get("/policy-console/federation/notifications/dlq/fdlq_replay_1")
        assert response.status_code == 200
        # Auth and signature headers must be redacted
        assert "secret-token-12345" not in response.text
        assert "deadbeef" not in response.text
        # But non-sensitive headers should be visible
        assert "application/json" in response.text
        # Redacted markers should be present
        assert "[REDACTED]" in response.text
