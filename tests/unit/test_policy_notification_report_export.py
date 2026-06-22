"""Tests for notification report export helpers (Phase 52 Task 7)."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone, timedelta

import pytest

from agent_app.governance.policy_rollout_federation_notification_observability import (
    NotificationAlertEvent,
    NotificationDeliveryEvent,
    NotificationDeliveryEventType,
    NotificationMetricWindow,
)
from agent_app.runtime.policy_rollout_federation_notification_report_export import (
    export_notification_alerts_csv,
    export_notification_alerts_json,
    export_notification_events_csv,
    export_notification_events_json,
    export_notification_metrics_csv,
    export_notification_metrics_json,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    """Return a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def _make_event(**overrides) -> NotificationDeliveryEvent:
    """Build a NotificationDeliveryEvent with sensible defaults."""
    defaults = dict(
        event_id="nde_test001",
        notification_id="fn_test001",
        approval_id="fap_test001",
        federation_id="frp_test001",
        channel="webhook",
        event_type=NotificationDeliveryEventType.SENT,
        status="delivered",
        attempt=1,
        latency_ms=250,
        error_code=None,
        error_message=None,
        adapter_name="webhook_adapter",
        template_id="tmpl_welcome",
        preference_decision="send",
        metadata={"source": "unit-test", "region": "us-east-1"},
        created_at=_now(),
    )
    defaults.update(overrides)
    return NotificationDeliveryEvent(**defaults)


def _make_metric(**overrides) -> NotificationMetricWindow:
    """Build a NotificationMetricWindow with sensible defaults."""
    defaults = dict(
        window_start=datetime(2026, 6, 20, 10, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 20, 11, 0, 0, tzinfo=timezone.utc),
        federation_id="frp_test001",
        channel="webhook",
        total=100,
        sent=95,
        failed=3,
        suppressed=1,
        dlq=1,
        retry_scheduled=0,
        success_rate=0.95,
        failure_rate=0.03,
        dlq_rate=0.01,
        avg_latency_ms=250.0,
        p95_latency_ms=450.0,
    )
    defaults.update(overrides)
    return NotificationMetricWindow(**defaults)


def _make_alert(**overrides) -> NotificationAlertEvent:
    """Build a NotificationAlertEvent with sensible defaults."""
    defaults = dict(
        alert_id="nae_alert001",
        rule_id="nar_rule001",
        name="High failure rate",
        severity="warning",
        metric="failure_rate",
        observed_value=0.15,
        threshold=0.10,
        federation_id="frp_test001",
        channel="webhook",
        message="Failure rate exceeded threshold of 0.10",
        status="open",
        created_at=_now(),
        acknowledged_at=None,
        acknowledged_by=None,
        resolved_at=None,
        resolved_by=None,
    )
    defaults.update(overrides)
    return NotificationAlertEvent(**defaults)


# ===========================================================================
# NotificationDeliveryEvent JSON Export
# ===========================================================================


class TestExportNotificationEventsJson:
    """Tests for export_notification_events_json."""

    def test_single_event_json(self) -> None:
        """Single event produces a JSON array with one element."""
        event = _make_event()
        result = export_notification_events_json([event])
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["event_id"] == "nde_test001"

    def test_multiple_events_json(self) -> None:
        """Multiple events produce a JSON array with all elements."""
        e1 = _make_event(event_id="nde_a")
        e2 = _make_event(event_id="nde_b")
        result = export_notification_events_json([e1, e2])
        data = json.loads(result)
        assert len(data) == 2
        assert data[0]["event_id"] == "nde_a"
        assert data[1]["event_id"] == "nde_b"

    def test_empty_events_json(self) -> None:
        """Empty list produces valid JSON '[]'."""
        result = export_notification_events_json([])
        assert json.loads(result) == []

    def test_json_indent_default(self) -> None:
        """Default indent is 2."""
        event = _make_event()
        result = export_notification_events_json([event])
        # Indented JSON has newlines
        assert "\n" in result

    def test_json_indent_zero(self) -> None:
        """Indent=0 produces compact JSON (no indentation spaces, but newlines between keys)."""
        event = _make_event()
        result = export_notification_events_json([event], indent=0)
        # indent=0 in Python's json.dumps still inserts newlines between items
        # but no leading whitespace on continuation lines
        assert "\n" in result
        for line in result.strip().split("\n"):
            assert not line.startswith("  "), "indent=0 should not produce indented lines"

    def test_datetime_serialized_as_iso(self) -> None:
        """Datetime fields are ISO format strings (UTC uses 'Z' suffix via Pydantic)."""
        ts = datetime(2026, 6, 20, 12, 30, 0, tzinfo=timezone.utc)
        event = _make_event(created_at=ts)
        result = export_notification_events_json([event])
        data = json.loads(result)
        assert data[0]["created_at"] == "2026-06-20T12:30:00Z"

    def test_enum_event_type_as_string(self) -> None:
        """event_type is serialized as a string."""
        event = _make_event(event_type=NotificationDeliveryEventType.FAILED)
        result = export_notification_events_json([event])
        data = json.loads(result)
        assert data[0]["event_type"] == "failed"

    def test_sensitive_metadata_redacted_in_json(self) -> None:
        """Sensitive keys in metadata are redacted to [REDACTED]."""
        event = _make_event(metadata={"api_key": "secret-123", "source": "ok"})
        result = export_notification_events_json([event])
        data = json.loads(result)
        assert data[0]["metadata"]["api_key"] == "[REDACTED]"
        assert data[0]["metadata"]["source"] == "ok"

    def test_none_fields_in_json(self) -> None:
        """None fields are preserved as null in JSON."""
        event = _make_event(approval_id=None, federation_id=None, error_message=None)
        result = export_notification_events_json([event])
        data = json.loads(result)
        assert data[0]["approval_id"] is None
        assert data[0]["federation_id"] is None
        assert data[0]["error_message"] is None


# ===========================================================================
# NotificationDeliveryEvent CSV Export
# ===========================================================================


class TestExportNotificationEventsCsv:
    """Tests for export_notification_events_csv."""

    def test_csv_returns_string(self) -> None:
        """CSV export returns a string."""
        event = _make_event()
        result = export_notification_events_csv([event])
        assert isinstance(result, str)

    def test_csv_header_row(self) -> None:
        """CSV output has the correct header row."""
        event = _make_event()
        result = export_notification_events_csv([event])
        reader = csv.DictReader(io.StringIO(result))
        headers = reader.fieldnames
        expected = [
            "event_id",
            "notification_id",
            "approval_id",
            "federation_id",
            "channel",
            "event_type",
            "status",
            "attempt",
            "latency_ms",
            "error_code",
            "error_message",
            "adapter_name",
            "template_id",
            "preference_decision",
            "created_at",
        ]
        assert headers == expected

    def test_csv_column_order_stable(self) -> None:
        """Columns are in the specified stable order."""
        event = _make_event()
        result = export_notification_events_csv([event])
        reader = csv.DictReader(io.StringIO(result))
        row = next(reader)
        assert row["event_id"] == "nde_test001"
        assert row["notification_id"] == "fn_test001"
        assert row["approval_id"] == "fap_test001"
        assert row["federation_id"] == "frp_test001"
        assert row["channel"] == "webhook"
        assert row["event_type"] == "sent"

    def test_csv_single_row(self) -> None:
        """Single event produces one data row."""
        event = _make_event()
        result = export_notification_events_csv([event])
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1

    def test_csv_multiple_rows(self) -> None:
        """Multiple events produce multiple data rows."""
        e1 = _make_event(event_id="nde_a")
        e2 = _make_event(event_id="nde_b")
        result = export_notification_events_csv([e1, e2])
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["event_id"] == "nde_a"
        assert rows[1]["event_id"] == "nde_b"

    def test_csv_error_message_sanitized(self) -> None:
        """error_message with sensitive patterns is redacted."""
        event = _make_event(error_message="Failed with api_key=secret123")
        result = export_notification_events_csv([event])
        reader = csv.DictReader(io.StringIO(result))
        row = next(reader)
        assert "secret123" not in row["error_message"]
        assert "[REDACTED]" in row["error_message"]

    def test_csv_metadata_not_included(self) -> None:
        """metadata is not a direct column in CSV."""
        event = _make_event(metadata={"key": "value"})
        result = export_notification_events_csv([event])
        reader = csv.DictReader(io.StringIO(result))
        headers = reader.fieldnames
        assert "metadata" not in headers
        assert "metadata_json" not in headers

    def test_csv_none_fields_become_empty(self) -> None:
        """None fields are rendered as empty strings."""
        event = _make_event(approval_id=None, federation_id=None, error_message=None)
        result = export_notification_events_csv([event])
        reader = csv.DictReader(io.StringIO(result))
        row = next(reader)
        assert row["approval_id"] == ""
        assert row["federation_id"] == ""
        assert row["error_message"] == ""

    def test_csv_datetime_iso_format(self) -> None:
        """Datetime fields use isoformat() (UTC produces +00:00)."""
        ts = datetime(2026, 6, 20, 12, 30, 0, tzinfo=timezone.utc)
        event = _make_event(created_at=ts)
        result = export_notification_events_csv([event])
        reader = csv.DictReader(io.StringIO(result))
        row = next(reader)
        assert row["created_at"] == "2026-06-20T12:30:00+00:00"


# ===========================================================================
# NotificationMetricWindow JSON Export
# ===========================================================================


class TestExportNotificationMetricsJson:
    """Tests for export_notification_metrics_json."""

    def test_single_metric_json(self) -> None:
        """Single metric produces a JSON array with one element."""
        metric = _make_metric()
        result = export_notification_metrics_json([metric])
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["channel"] == "webhook"

    def test_multiple_metrics_json(self) -> None:
        """Multiple metrics produce a JSON array with all elements."""
        m1 = _make_metric(channel="webhook")
        m2 = _make_metric(channel="email")
        result = export_notification_metrics_json([m1, m2])
        data = json.loads(result)
        assert len(data) == 2
        assert data[0]["channel"] == "webhook"
        assert data[1]["channel"] == "email"

    def test_empty_metrics_json(self) -> None:
        """Empty list produces valid JSON '[]'."""
        result = export_notification_metrics_json([])
        assert json.loads(result) == []

    def test_rates_not_formatted_in_json(self) -> None:
        """Rates are stored as raw floats in JSON (not percentages)."""
        metric = _make_metric(success_rate=0.95, failure_rate=0.03, dlq_rate=0.01)
        result = export_notification_metrics_json([metric])
        data = json.loads(result)
        assert data[0]["success_rate"] == 0.95
        assert data[0]["failure_rate"] == 0.03
        assert data[0]["dlq_rate"] == 0.01

    def test_datetime_serialized_as_iso(self) -> None:
        """Datetime fields are ISO format strings (UTC uses 'Z' suffix)."""
        ts_start = datetime(2026, 6, 20, 10, 0, 0, tzinfo=timezone.utc)
        ts_end = datetime(2026, 6, 20, 11, 0, 0, tzinfo=timezone.utc)
        metric = _make_metric(window_start=ts_start, window_end=ts_end)
        result = export_notification_metrics_json([metric])
        data = json.loads(result)
        assert data[0]["window_start"] == "2026-06-20T10:00:00Z"
        assert data[0]["window_end"] == "2026-06-20T11:00:00Z"

    def test_json_indent_default(self) -> None:
        """Default indent produces pretty-printed JSON."""
        metric = _make_metric()
        result = export_notification_metrics_json([metric])
        assert "\n" in result


# ===========================================================================
# NotificationMetricWindow CSV Export
# ===========================================================================


class TestExportNotificationMetricsCsv:
    """Tests for export_notification_metrics_csv."""

    def test_csv_returns_string(self) -> None:
        """CSV export returns a string."""
        metric = _make_metric()
        result = export_notification_metrics_csv([metric])
        assert isinstance(result, str)

    def test_csv_header_row(self) -> None:
        """CSV output has the correct header row."""
        metric = _make_metric()
        result = export_notification_metrics_csv([metric])
        reader = csv.DictReader(io.StringIO(result))
        headers = reader.fieldnames
        expected = [
            "window_start",
            "window_end",
            "federation_id",
            "channel",
            "total",
            "sent",
            "failed",
            "suppressed",
            "dlq",
            "retry_scheduled",
            "success_rate",
            "failure_rate",
            "dlq_rate",
            "avg_latency_ms",
            "p95_latency_ms",
        ]
        assert headers == expected

    def test_csv_column_order_stable(self) -> None:
        """Columns are in the specified stable order."""
        metric = _make_metric()
        result = export_notification_metrics_csv([metric])
        reader = csv.DictReader(io.StringIO(result))
        row = next(reader)
        assert row["window_start"] == "2026-06-20T10:00:00+00:00"
        assert row["window_end"] == "2026-06-20T11:00:00+00:00"
        assert row["federation_id"] == "frp_test001"
        assert row["channel"] == "webhook"
        assert row["total"] == "100"

    def test_csv_rates_formatted_as_percentages(self) -> None:
        """Rates are formatted as percentages (multiply by 100, 2 decimal places)."""
        metric = _make_metric(success_rate=0.95, failure_rate=0.03, dlq_rate=0.01)
        result = export_notification_metrics_csv([metric])
        reader = csv.DictReader(io.StringIO(result))
        row = next(reader)
        assert row["success_rate"] == "95.00"
        assert row["failure_rate"] == "3.00"
        assert row["dlq_rate"] == "1.00"

    def test_csv_rates_zero(self) -> None:
        """Zero rates format correctly."""
        metric = _make_metric(success_rate=0.0, failure_rate=0.0, dlq_rate=0.0)
        result = export_notification_metrics_csv([metric])
        reader = csv.DictReader(io.StringIO(result))
        row = next(reader)
        assert row["success_rate"] == "0.00"
        assert row["failure_rate"] == "0.00"
        assert row["dlq_rate"] == "0.00"

    def test_csv_single_row(self) -> None:
        """Single metric produces one data row."""
        metric = _make_metric()
        result = export_notification_metrics_csv([metric])
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1

    def test_csv_multiple_rows(self) -> None:
        """Multiple metrics produce multiple data rows."""
        m1 = _make_metric(channel="webhook", total=100)
        m2 = _make_metric(channel="email", total=200)
        result = export_notification_metrics_csv([m1, m2])
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["channel"] == "webhook"
        assert rows[1]["channel"] == "email"

    def test_csv_latency_values(self) -> None:
        """Latency values are serialized correctly."""
        metric = _make_metric(avg_latency_ms=250.5, p95_latency_ms=450.0)
        result = export_notification_metrics_csv([metric])
        reader = csv.DictReader(io.StringIO(result))
        row = next(reader)
        assert row["avg_latency_ms"] == "250.5"
        assert row["p95_latency_ms"] == "450.0"

    def test_csv_none_latency_empty(self) -> None:
        """None latency values become empty strings."""
        metric = _make_metric(avg_latency_ms=None, p95_latency_ms=None)
        result = export_notification_metrics_csv([metric])
        reader = csv.DictReader(io.StringIO(result))
        row = next(reader)
        assert row["avg_latency_ms"] == ""
        assert row["p95_latency_ms"] == ""


# ===========================================================================
# NotificationAlertEvent JSON Export
# ===========================================================================


class TestExportNotificationAlertsJson:
    """Tests for export_notification_alerts_json."""

    def test_single_alert_json(self) -> None:
        """Single alert produces a JSON array with one element."""
        alert = _make_alert()
        result = export_notification_alerts_json([alert])
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["alert_id"] == "nae_alert001"

    def test_multiple_alerts_json(self) -> None:
        """Multiple alerts produce a JSON array with all elements."""
        a1 = _make_alert(alert_id="nae_a")
        a2 = _make_alert(alert_id="nae_b")
        result = export_notification_alerts_json([a1, a2])
        data = json.loads(result)
        assert len(data) == 2
        assert data[0]["alert_id"] == "nae_a"
        assert data[1]["alert_id"] == "nae_b"

    def test_empty_alerts_json(self) -> None:
        """Empty list produces valid JSON '[]'."""
        result = export_notification_alerts_json([])
        assert json.loads(result) == []

    def test_json_indent_default(self) -> None:
        """Default indent produces pretty-printed JSON."""
        alert = _make_alert()
        result = export_notification_alerts_json([alert])
        assert "\n" in result

    def test_datetime_serialized_as_iso(self) -> None:
        """Datetime fields use ISO format (UTC uses 'Z' suffix)."""
        ts = datetime(2026, 6, 20, 12, 30, 0, tzinfo=timezone.utc)
        alert = _make_alert(created_at=ts)
        result = export_notification_alerts_json([alert])
        data = json.loads(result)
        assert data[0]["created_at"] == "2026-06-20T12:30:00Z"

    def test_optional_timestamps_serialized(self) -> None:
        """Optional timestamp fields are serialized correctly."""
        now = _now()
        alert = _make_alert(
            acknowledged_at=now,
            resolved_at=now,
        )
        result = export_notification_alerts_json([alert])
        data = json.loads(result)
        assert data[0]["acknowledged_at"] is not None
        assert data[0]["resolved_at"] is not None

    def test_message_not_sensitive(self) -> None:
        """Normal message is preserved without redaction."""
        alert = _make_alert(message="Failure rate exceeded threshold of 0.10")
        result = export_notification_alerts_json([alert])
        data = json.loads(result)
        assert data[0]["message"] == "Failure rate exceeded threshold of 0.10"


# ===========================================================================
# NotificationAlertEvent CSV Export
# ===========================================================================


class TestExportNotificationAlertsCsv:
    """Tests for export_notification_alerts_csv."""

    def test_csv_returns_string(self) -> None:
        """CSV export returns a string."""
        alert = _make_alert()
        result = export_notification_alerts_csv([alert])
        assert isinstance(result, str)

    def test_csv_header_row(self) -> None:
        """CSV output has the correct header row."""
        alert = _make_alert()
        result = export_notification_alerts_csv([alert])
        reader = csv.DictReader(io.StringIO(result))
        headers = reader.fieldnames
        expected = [
            "alert_id",
            "rule_id",
            "name",
            "severity",
            "metric",
            "observed_value",
            "threshold",
            "federation_id",
            "channel",
            "message",
            "status",
            "created_at",
            "acknowledged_at",
            "acknowledged_by",
            "resolved_at",
            "resolved_by",
        ]
        assert headers == expected

    def test_csv_column_order_stable(self) -> None:
        """Columns are in the specified stable order."""
        alert = _make_alert()
        result = export_notification_alerts_csv([alert])
        reader = csv.DictReader(io.StringIO(result))
        row = next(reader)
        assert row["alert_id"] == "nae_alert001"
        assert row["rule_id"] == "nar_rule001"
        assert row["name"] == "High failure rate"
        assert row["severity"] == "warning"
        assert row["metric"] == "failure_rate"

    def test_csv_single_row(self) -> None:
        """Single alert produces one data row."""
        alert = _make_alert()
        result = export_notification_alerts_csv([alert])
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1

    def test_csv_multiple_rows(self) -> None:
        """Multiple alerts produce multiple data rows."""
        a1 = _make_alert(alert_id="nae_a")
        a2 = _make_alert(alert_id="nae_b")
        result = export_notification_alerts_csv([a1, a2])
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["alert_id"] == "nae_a"
        assert rows[1]["alert_id"] == "nae_b"

    def test_csv_message_sanitized(self) -> None:
        """Message field with sensitive patterns is redacted."""
        alert = _make_alert(message="Connection failed: api_key=secret123")
        result = export_notification_alerts_csv([alert])
        reader = csv.DictReader(io.StringIO(result))
        row = next(reader)
        assert "secret123" not in row["message"]
        assert "[REDACTED]" in row["message"]

    def test_csv_optional_fields_none(self) -> None:
        """None optional fields become empty strings."""
        alert = _make_alert(
            federation_id=None,
            channel=None,
            acknowledged_at=None,
            acknowledged_by=None,
            resolved_at=None,
            resolved_by=None,
        )
        result = export_notification_alerts_csv([alert])
        reader = csv.DictReader(io.StringIO(result))
        row = next(reader)
        assert row["federation_id"] == ""
        assert row["channel"] == ""
        assert row["acknowledged_at"] == ""
        assert row["acknowledged_by"] == ""
        assert row["resolved_at"] == ""
        assert row["resolved_by"] == ""

    def test_csv_datetime_iso_format(self) -> None:
        """Datetime fields use isoformat() (UTC produces +00:00)."""
        ts = datetime(2026, 6, 20, 12, 30, 0, tzinfo=timezone.utc)
        alert = _make_alert(created_at=ts)
        result = export_notification_alerts_csv([alert])
        reader = csv.DictReader(io.StringIO(result))
        row = next(reader)
        assert row["created_at"] == "2026-06-20T12:30:00+00:00"


# ===========================================================================
# Sensitive Field Handling Tests
# ===========================================================================


class TestSensitiveFieldHandling:
    """Tests for sanitization behavior across all export functions."""

    def test_metadata_with_sensitive_keys_redacted_in_json(self) -> None:
        """Sensitive keys in metadata are redacted in JSON export."""
        event = _make_event(metadata={
            "source": "unit-test",
            "api_key": "secret-key-123",
            "authorization": "Bearer token-abc",
            "x-signature": "sig-xyz",
        })
        result = export_notification_events_json([event])
        data = json.loads(result)
        assert data[0]["metadata"]["source"] == "unit-test"
        assert data[0]["metadata"]["api_key"] == "[REDACTED]"
        assert data[0]["metadata"]["authorization"] == "[REDACTED]"
        assert data[0]["metadata"]["x-signature"] == "[REDACTED]"

    def test_error_message_with_sensitive_patterns_redacted_in_json(self) -> None:
        """Error messages containing sensitive patterns are redacted in JSON."""
        event = _make_event(
            error_message="Failed with api_key=secret123 and token=xyz and password=pw123"
        )
        result = export_notification_events_json([event])
        data = json.loads(result)
        msg = data[0]["error_message"]
        assert "secret123" not in msg
        assert "xyz" not in msg
        assert "pw123" not in msg
        assert "[REDACTED]" in msg

    def test_error_message_with_sensitive_patterns_redacted_in_csv(self) -> None:
        """Error messages containing sensitive patterns are redacted in CSV."""
        event = _make_event(
            error_message="Connection failed: token=abc123 password=pw"
        )
        result = export_notification_events_csv([event])
        reader = csv.DictReader(io.StringIO(result))
        row = next(reader)
        assert "abc123" not in row["error_message"]
        assert "pw" not in row["error_message"]
        assert "[REDACTED]" in row["error_message"]

    def test_csv_headers_stable_and_complete(self) -> None:
        """All CSV exports have stable, complete headers."""
        event = _make_event()
        metric = _make_metric()
        alert = _make_alert()

        event_result = export_notification_events_csv([event])
        metric_result = export_notification_metrics_csv([metric])
        alert_result = export_notification_alerts_csv([alert])

        event_reader = csv.DictReader(io.StringIO(event_result))
        metric_reader = csv.DictReader(io.StringIO(metric_result))
        alert_reader = csv.DictReader(io.StringIO(alert_result))

        # All readers should have non-None fieldnames
        assert event_reader.fieldnames is not None
        assert metric_reader.fieldnames is not None
        assert alert_reader.fieldnames is not None

        # Verify no None values appear in any rows
        for reader in (event_reader, metric_reader, alert_reader):
            for row in reader:
                for value in row.values():
                    assert value is not None

    def test_alert_message_sanitized_in_json(self) -> None:
        """Alert message with sensitive patterns is redacted in JSON."""
        alert = _make_alert(message="Auth error: api_key=leaked_value")
        result = export_notification_alerts_json([alert])
        data = json.loads(result)
        assert "leaked_value" not in data[0]["message"]
        assert "[REDACTED]" in data[0]["message"]

    def test_alert_message_sanitized_in_csv(self) -> None:
        """Alert message with sensitive patterns is redacted in CSV."""
        alert = _make_alert(message="Auth error: api_key=leaked_value")
        result = export_notification_alerts_csv([alert])
        reader = csv.DictReader(io.StringIO(result))
        row = next(reader)
        assert "leaked_value" not in row["message"]
        assert "[REDACTED]" in row["message"]
