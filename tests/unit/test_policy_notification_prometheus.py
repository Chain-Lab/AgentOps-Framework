"""Tests for Phase 53 Task 5 — Prometheus metrics export."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryChannelType,
    AlertDeliveryStatus,
    AlertDeliveryTarget,
    AlertDeliveryAttempt,
)
from agent_app.governance.policy_rollout_federation_notification_observability import (
    ChannelHealthSnapshot,
    ChannelHealthStatus,
    NotificationAlertEvent,
    NotificationMetricWindow,
)
from agent_app.runtime.policy_rollout_federation_notification_prometheus import (
    export_notification_prometheus_metrics,
)


def _make_metric(channel="webhook", federation_id="fed_1", total=100,
                 sent=95, failed=3, suppressed=1, dlq=1,
                 success_rate=0.95, failure_rate=0.03, dlq_rate=0.01,
                 avg_latency_ms=250.0, p95_latency_ms=890.0) -> NotificationMetricWindow:
    now = datetime.now(timezone.utc)
    return NotificationMetricWindow(
        window_start=now, window_end=now + timedelta(hours=1),
        channel=channel, federation_id=federation_id,
        total=total, sent=sent, failed=failed, suppressed=suppressed, dlq=dlq,
        success_rate=success_rate, failure_rate=failure_rate, dlq_rate=dlq_rate,
        avg_latency_ms=avg_latency_ms, p95_latency_ms=p95_latency_ms,
    )


def _make_alert(alert_id="nae_1", severity="warning") -> NotificationAlertEvent:
    return NotificationAlertEvent(
        alert_id=alert_id, rule_id="nar_1", name="Test Alert", severity=severity,
        metric="failure_rate", observed_value=0.1, threshold=0.05,
        message="Test alert", status="open", created_at=datetime.now(timezone.utc),
    )


class TestPrometheusExport:
    def test_empty_output_valid(self):
        result = export_notification_prometheus_metrics([], [], [])
        assert "# HELP" in result
        assert "# TYPE" in result

    def test_metric_names_present(self):
        m = _make_metric()
        result = export_notification_prometheus_metrics([m], [], [])
        assert "agentapp_notification_total" in result
        assert "agentapp_notification_sent" in result
        assert "agentapp_notification_failed" in result
        assert "agentapp_notification_dlq" in result
        assert "agentapp_notification_success_rate" in result
        assert "agentapp_notification_failure_rate" in result
        assert "agentapp_notification_dlq_rate" in result

    def test_labels_present(self):
        m = _make_metric(channel="webhook", federation_id="fed_1")
        result = export_notification_prometheus_metrics([m], [], [])
        assert 'channel="webhook"' in result
        assert 'federation_id="fed_1"' in result

    def test_labels_escaped_quotes(self):
        m = _make_metric(channel='webhook"bad', federation_id="fed_1")
        result = export_notification_prometheus_metrics([m], [], [])
        # Should not contain unescaped quotes
        assert 'webhook"bad' not in result

    def test_labels_escaped_backslashes(self):
        m = _make_metric(channel="web\\hook", federation_id="fed_1")
        result = export_notification_prometheus_metrics([m], [], [])
        assert "web\\hook" not in result

    def test_no_secrets_in_output(self):
        m = _make_metric()
        result = export_notification_prometheus_metrics([m], [], [])
        assert "secret" not in result.lower()
        assert "token" not in result.lower()
        assert "password" not in result.lower()

    def test_open_alerts_exported(self):
        alert = _make_alert(severity="critical")
        result = export_notification_prometheus_metrics([], [], [alert])
        assert "agentapp_notification_alerts_open" in result
        assert 'severity="critical"' in result

    def test_alerts_by_severity(self):
        a1 = _make_alert("nae_1", "warning")
        a2 = _make_alert("nae_2", "critical")
        result = export_notification_prometheus_metrics([], [], [a1, a2])
        assert 'severity="warning"' in result
        assert 'severity="critical"' in result
        # Count data lines (not HELP/TYPE lines)
        data_lines = [l for l in result.split("\n") if l.startswith("agentapp_notification_alerts_open{")]
        assert len(data_lines) == 2

    def test_p95_latency_exported(self):
        m = _make_metric(p95_latency_ms=890.0)
        result = export_notification_prometheus_metrics([m], [], [])
        assert "agentapp_notification_latency_p95_ms" in result
        assert "890" in result

    def test_retry_scheduled_exported(self):
        now = datetime.now(timezone.utc)
        m = NotificationMetricWindow(
            window_start=now, window_end=now + timedelta(hours=1),
            channel="webhook", federation_id="fed_1",
            total=100, sent=90, failed=5, suppressed=2, dlq=2, retry_scheduled=5,
            success_rate=0.90, failure_rate=0.05, dlq_rate=0.02,
        )
        result = export_notification_prometheus_metrics([m], [], [])
        assert "agentapp_notification_retry_scheduled" in result

    def test_suppressed_exported(self):
        m = _make_metric(suppressed=2)
        result = export_notification_prometheus_metrics([m], [], [])
        assert "agentapp_notification_suppressed" in result

    def test_health_status_not_affecting_metrics(self):
        """Health snapshots should not break metrics export."""
        m = _make_metric()
        h = ChannelHealthSnapshot(
            channel="webhook", status=ChannelHealthStatus.HEALTHY,
            window_start=m.window_start, window_end=m.window_end,
            total=100, success_rate=0.95, failure_rate=0.03,
            created_at=datetime.now(timezone.utc),
        )
        result = export_notification_prometheus_metrics([m], [h], [])
        assert "agentapp_notification_total" in result
