"""Phase 52 Task 9: Tests for federation notification observability console pages."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

try:
    from starlette.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="FastAPI not installed")

from conftest import _run_async


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_observability_store() -> MagicMock:
    """Create a mock notification observability store."""
    store = MagicMock()
    store.aggregate_metrics = AsyncMock(return_value=MagicMock(
        window_start=_now(),
        window_end=_now(),
        federation_id=None,
        channel="email",
        total=100,
        sent=92,
        failed=5,
        suppressed=2,
        dlq=1,
        retry_scheduled=0,
        success_rate=0.92,
        failure_rate=0.05,
        dlq_rate=0.01,
        avg_latency_ms=150.5,
        p95_latency_ms=320.0,
    ))
    store.list_events = AsyncMock(return_value=[
        MagicMock(
            event_id="nde_test001",
            event_type="sent",
            notification_id="fn_test001",
            approval_id="fa_test001",
            federation_id="frp_test",
            channel="email",
            status="sent",
            attempt=1,
            latency_ms=150,
            error_message=None,
            adapter_name="console",
            created_at=_now(),
        )
    ])
    return store


def _make_alert_store() -> MagicMock:
    """Create a mock notification alert store."""
    store = MagicMock()
    _alert = MagicMock(
        alert_id="nae_test001",
        rule_id="nar_test001",
        name="High Failure Rate",
        severity="warning",
        metric="failure_rate",
        observed_value=0.25,
        threshold=0.05,
        federation_id="frp_test",
        channel="email",
        message="Failure rate exceeds threshold",
        status="open",
        created_at=_now(),
    )

    async def _get_alert(alert_id):
        if alert_id == "nae_test001":
            return _alert
        return None

    store.get_alert = AsyncMock(side_effect=_get_alert)
    store.list_alerts = AsyncMock(return_value=[_alert])
    store.acknowledge = AsyncMock(return_value=MagicMock(
        alert_id="nae_test001",
        rule_id="nar_test001",
        name="High Failure Rate",
        severity="warning",
        metric="failure_rate",
        observed_value=0.25,
        threshold=0.05,
        federation_id="frp_test",
        channel="email",
        message="Failure rate exceeds threshold",
        status="acknowledged",
        created_at=_now(),
        acknowledged_at=_now(),
        acknowledged_by="console_user",
        resolved_at=None,
        resolved_by=None,
    ))
    store.resolve = AsyncMock(return_value=MagicMock(
        alert_id="nae_test001",
        rule_id="nar_test001",
        name="High Failure Rate",
        severity="warning",
        metric="failure_rate",
        observed_value=0.25,
        threshold=0.05,
        federation_id="frp_test",
        channel="email",
        message="Failure rate exceeds threshold",
        status="resolved",
        created_at=_now(),
        acknowledged_at=_now(),
        acknowledged_by="console_user",
        resolved_at=_now(),
        resolved_by="console_user",
    ))
    return store


def _make_sla_service() -> MagicMock:
    """Create a mock notification SLA service."""
    service = MagicMock()
    service.evaluate = AsyncMock(return_value=[
        MagicMock(
            violation_id="nsv_test001",
            federation_id="frp_test",
            channel="email",
            metric="failure_rate",
            observed_value=0.25,
            threshold=0.05,
            severity="warning",
            message="Failure rate exceeds SLA threshold",
            created_at=_now(),
        )
    ])
    return service


def _client(observability_store=None, alert_store=None, sla_service=None):
    """Create a TestClient with the notification observability router."""
    from fastapi import FastAPI
    from agent_app.console.router import build_policy_console_router
    from agent_app.config.schema import PolicyConsoleConfig

    app = FastAPI()
    router = build_policy_console_router(
        store=None,
        config=PolicyConsoleConfig(enabled=True),
        federation_notification_observability_store=observability_store,
        federation_notification_alert_store=alert_store,
        federation_notification_sla_service=sla_service,
    )
    app.include_router(router, prefix="/policy-console")
    return TestClient(app)


class TestNotificationObservabilityDashboard:
    """Tests for the observability dashboard route."""

    def test_dashboard_renders(self):
        """GET /federation/notifications/observability returns 200."""
        store = _make_observability_store()
        client = _client(observability_store=store)
        resp = client.get("/policy-console/federation/notifications/observability")
        assert resp.status_code == 200
        assert "Notification Observability Dashboard" in resp.text

    def test_dashboard_shows_metrics(self):
        """Dashboard shows delivery metrics when store is available."""
        store = _make_observability_store()
        client = _client(observability_store=store)
        resp = client.get("/policy-console/federation/notifications/observability")
        assert resp.status_code == 200
        assert "email" in resp.text
        assert "92" in resp.text  # sent count
        assert "92.0%" in resp.text or "92%" in resp.text  # success rate

    def test_dashboard_no_store_renders_gracefully(self):
        """GET without store shows empty state."""
        client = _client()
        resp = client.get("/policy-console/federation/notifications/observability")
        assert resp.status_code == 200
        assert "not configured" in resp.text.lower() or "empty-state" in resp.text

    def test_dashboard_with_alert_summary(self):
        """Dashboard includes alert summary when alert store is available."""
        obs_store = _make_observability_store()
        alert_store = _make_alert_store()
        client = _client(observability_store=obs_store, alert_store=alert_store)
        resp = client.get("/policy-console/federation/notifications/observability")
        assert resp.status_code == 200
        assert "Open Alerts" in resp.text or "alert" in resp.text.lower()


class TestNotificationEventsPage:
    """Tests for the events list route."""

    def test_events_page_renders(self):
        """GET /federation/notifications/events returns 200."""
        store = _make_observability_store()
        client = _client(observability_store=store)
        resp = client.get("/policy-console/federation/notifications/events")
        assert resp.status_code == 200
        assert "Delivery Events" in resp.text or "Events" in resp.text

    def test_events_page_shows_events(self):
        """Events page shows delivery events."""
        store = _make_observability_store()
        client = _client(observability_store=store)
        resp = client.get("/policy-console/federation/notifications/events")
        assert resp.status_code == 200
        assert "nde_test001" in resp.text

    def test_events_page_no_store(self):
        """GET without store shows empty state."""
        client = _client()
        resp = client.get("/policy-console/federation/notifications/events")
        assert resp.status_code == 200


class TestNotificationMetricsPage:
    """Tests for the metrics route."""

    def test_metrics_page_renders(self):
        """GET /federation/notifications/metrics returns 200."""
        store = _make_observability_store()
        client = _client(observability_store=store)
        resp = client.get("/policy-console/federation/notifications/metrics")
        assert resp.status_code == 200
        assert "Metrics" in resp.text

    def test_metrics_page_shows_data(self):
        """Metrics page shows delivery metrics."""
        store = _make_observability_store()
        client = _client(observability_store=store)
        resp = client.get("/policy-console/federation/notifications/metrics")
        assert resp.status_code == 200
        assert "100" in resp.text  # total
        assert "92" in resp.text  # sent

    def test_metrics_page_no_store(self):
        """GET without store shows empty state."""
        client = _client()
        resp = client.get("/policy-console/federation/notifications/metrics")
        assert resp.status_code == 200


class TestNotificationHealthPage:
    """Tests for the channel health route."""

    def test_health_page_renders(self):
        """GET /federation/notifications/health returns 200."""
        store = _make_observability_store()
        client = _client(observability_store=store)
        resp = client.get("/policy-console/federation/notifications/health")
        assert resp.status_code == 200
        assert "Health" in resp.text

    def test_health_page_shows_channels(self):
        """Health page shows channel health status."""
        store = _make_observability_store()
        client = _client(observability_store=store)
        resp = client.get("/policy-console/federation/notifications/health")
        assert resp.status_code == 200
        assert "email" in resp.text

    def test_health_page_no_store(self):
        """GET without store shows empty state."""
        client = _client()
        resp = client.get("/policy-console/federation/notifications/health")
        assert resp.status_code == 200


class TestNotificationSLAPage:
    """Tests for the SLA violations route."""

    def test_sla_page_renders(self):
        """GET /federation/notifications/sla returns 200."""
        sla_svc = _make_sla_service()
        client = _client(sla_service=sla_svc)
        resp = client.get("/policy-console/federation/notifications/sla")
        assert resp.status_code == 200
        assert "SLA" in resp.text

    def test_sla_page_shows_violations(self):
        """SLA page shows violations when present."""
        sla_svc = _make_sla_service()
        client = _client(sla_service=sla_svc)
        resp = client.get("/policy-console/federation/notifications/sla")
        assert resp.status_code == 200
        assert "nsv_test001" in resp.text
        assert "failure_rate" in resp.text

    def test_sla_page_no_service(self):
        """GET without SLA service shows empty state."""
        client = _client()
        resp = client.get("/policy-console/federation/notifications/sla")
        assert resp.status_code == 200


class TestNotificationAlertsPage:
    """Tests for the alert list route."""

    def test_alerts_page_renders(self):
        """GET /federation/notifications/alerts returns 200."""
        alert_store = _make_alert_store()
        client = _client(alert_store=alert_store)
        resp = client.get("/policy-console/federation/notifications/alerts")
        assert resp.status_code == 200
        assert "Alert" in resp.text

    def test_alerts_page_shows_alerts(self):
        """Alerts page shows alert entries."""
        alert_store = _make_alert_store()
        client = _client(alert_store=alert_store)
        resp = client.get("/policy-console/federation/notifications/alerts")
        assert resp.status_code == 200
        assert "nae_test001" in resp.text

    def test_alerts_page_no_store(self):
        """GET without alert store shows empty state."""
        client = _client()
        resp = client.get("/policy-console/federation/notifications/alerts")
        assert resp.status_code == 200

    def test_alerts_page_with_filter(self):
        """Alerts page supports status filter query param."""
        alert_store = _make_alert_store()
        client = _client(alert_store=alert_store)
        resp = client.get("/policy-console/federation/notifications/alerts?status=open")
        assert resp.status_code == 200
        alert_store.list_alerts.assert_called_once()


class TestNotificationAlertDetail:
    """Tests for the alert detail route with ack/resolve actions."""

    def test_alert_detail_renders(self):
        """GET /federation/notifications/alerts/{id} returns 200."""
        alert_store = _make_alert_store()
        client = _client(alert_store=alert_store)
        resp = client.get("/policy-console/federation/notifications/alerts/nae_test001")
        assert resp.status_code == 200
        assert "nae_test001" in resp.text
        assert "High Failure Rate" in resp.text

    def test_alert_detail_not_found(self):
        """GET with nonexistent alert_id shows error."""
        alert_store = _make_alert_store()
        client = _client(alert_store=alert_store)
        resp = client.get("/policy-console/federation/notifications/alerts/nae_nonexistent")
        assert resp.status_code == 200
        assert "not found" in resp.text.lower()

    def test_alert_detail_no_store(self):
        """GET without store shows error."""
        client = _client()
        resp = client.get("/policy-console/federation/notifications/alerts/nae_test001")
        assert resp.status_code == 200
        assert "not configured" in resp.text.lower()

    def test_alert_acknowledge_post(self):
        """POST /alerts/{id}/acknowledge updates alert status."""
        alert_store = _make_alert_store()
        client = _client(alert_store=alert_store)
        resp = client.post(
            "/policy-console/federation/notifications/alerts/nae_test001/acknowledge",
            data={"actor_id": "console_user"},
        )
        assert resp.status_code == 200
        assert "acknowledged" in resp.text.lower()
        alert_store.acknowledge.assert_called_once_with(
            alert_id="nae_test001", acknowledged_by="console_user"
        )

    def test_alert_resolve_post(self):
        """POST /alerts/{id}/resolve updates alert status."""
        alert_store = _make_alert_store()
        client = _client(alert_store=alert_store)
        resp = client.post(
            "/policy-console/federation/notifications/alerts/nae_test001/resolve",
            data={"actor_id": "console_user"},
        )
        assert resp.status_code == 200
        assert "resolved" in resp.text.lower()
        alert_store.resolve.assert_called_once_with(
            alert_id="nae_test001", resolved_by="console_user"
        )

    def test_alert_acknowledge_missing_actor_id(self):
        """POST acknowledge without actor_id shows error."""
        alert_store = _make_alert_store()
        client = _client(alert_store=alert_store)
        resp = client.post(
            "/policy-console/federation/notifications/alerts/nae_test001/acknowledge",
            data={},
        )
        assert resp.status_code == 200
        assert "actor_id is required" in resp.text.lower()

    def test_alert_resolve_missing_actor_id(self):
        """POST resolve without actor_id shows error."""
        alert_store = _make_alert_store()
        client = _client(alert_store=alert_store)
        resp = client.post(
            "/policy-console/federation/notifications/alerts/nae_test001/resolve",
            data={},
        )
        assert resp.status_code == 200
        assert "actor_id is required" in resp.text.lower()
