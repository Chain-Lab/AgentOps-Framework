"""Notification report export helpers — JSON and CSV for events, metrics, and alerts.

Phase 52 Task 7: Report export helpers for notification observability data.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from agent_app.governance.policy_rollout_federation_notification_observability import (
    NotificationAlertEvent,
    NotificationDeliveryEvent,
    NotificationDeliveryEventType,
    NotificationMetricWindow,
    _SENSITIVE_KEYS,
    _redact_sensitive_values,
)


# ===========================================================================
# Private helper functions
# ===========================================================================


def _sanitize_value(key: str, value: Any) -> Any:
    """Redact sensitive values for safe export."""
    if isinstance(value, str):
        if key.lower() in _SENSITIVE_KEYS:
            return "[REDACTED]"
        return _redact_sensitive_values(value)
    if isinstance(value, dict):
        return _sanitize_metadata(value)
    return value


def _sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Sanitize metadata dict for safe export."""
    if not metadata:
        return {}
    result: dict[str, Any] = {}
    for k, v in metadata.items():
        if k.lower() in _SENSITIVE_KEYS:
            result[k] = "[REDACTED]"
        elif isinstance(v, str):
            result[k] = _redact_sensitive_values(v)
        else:
            result[k] = v
    return result


def _redact_sensitive_in_text(text: str) -> str:
    """Redact sensitive patterns in free-text fields."""
    return _redact_sensitive_values(text)


# ===========================================================================
# NotificationDeliveryEvent exports
# ===========================================================================


def export_notification_events_json(
    events: list[NotificationDeliveryEvent], indent: int = 2
) -> str:
    """Serialize notification delivery events to JSON.

    Sensitive fields in metadata and error_message are sanitized.
    """
    rows = []
    for event in events:
        data = event.model_dump(mode="json")
        # Ensure metadata is sanitized in JSON output
        if data.get("metadata"):
            data["metadata"] = _sanitize_metadata(data["metadata"])
        # Ensure error_message is sanitized
        if data.get("error_message"):
            data["error_message"] = _redact_sensitive_in_text(data["error_message"])
        rows.append(data)
    return json.dumps(rows, indent=indent)


def export_notification_events_csv(
    events: list[NotificationDeliveryEvent],
) -> str:
    """Convert notification delivery events to CSV string.

    Stable column order: event_id, notification_id, approval_id, federation_id,
    channel, event_type, status, attempt, latency_ms, error_code, error_message,
    adapter_name, template_id, preference_decision, created_at.

    metadata is NOT included in CSV (too complex), but error_message is
    sanitized.
    """
    headers = [
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
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    for event in events:
        writer.writerow({
            "event_id": event.event_id,
            "notification_id": event.notification_id or "",
            "approval_id": event.approval_id or "",
            "federation_id": event.federation_id or "",
            "channel": event.channel or "",
            "event_type": event.event_type.value,
            "status": event.status or "",
            "attempt": event.attempt if event.attempt is not None else "",
            "latency_ms": event.latency_ms if event.latency_ms is not None else "",
            "error_code": event.error_code or "",
            "error_message": (
                _redact_sensitive_in_text(event.error_message)
                if event.error_message
                else ""
            ),
            "adapter_name": event.adapter_name or "",
            "template_id": event.template_id or "",
            "preference_decision": event.preference_decision or "",
            "created_at": event.created_at.isoformat(),
        })
    return output.getvalue()


# ===========================================================================
# NotificationMetricWindow exports
# ===========================================================================


def export_notification_metrics_json(
    metrics: list[NotificationMetricWindow], indent: int = 2
) -> str:
    """Serialize notification metric windows to JSON.

    Rates are stored as raw fractions (0.0–1.0).
    """
    rows = []
    for metric in metrics:
        data = metric.model_dump(mode="json")
        rows.append(data)
    return json.dumps(rows, indent=indent)


def export_notification_metrics_csv(
    metrics: list[NotificationMetricWindow],
) -> str:
    """Convert notification metric windows to CSV string.

    Stable column order: window_start, window_end, federation_id, channel,
    total, sent, failed, suppressed, dlq, retry_scheduled, success_rate,
    failure_rate, dlq_rate, avg_latency_ms, p95_latency_ms.

    Rates are formatted as percentages (multiply by 100, round to 2 decimals).
    """
    headers = [
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
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    for metric in metrics:
        writer.writerow({
            "window_start": metric.window_start.isoformat(),
            "window_end": metric.window_end.isoformat(),
            "federation_id": metric.federation_id or "",
            "channel": metric.channel or "",
            "total": metric.total,
            "sent": metric.sent,
            "failed": metric.failed,
            "suppressed": metric.suppressed,
            "dlq": metric.dlq,
            "retry_scheduled": metric.retry_scheduled,
            "success_rate": f"{metric.success_rate * 100:.2f}",
            "failure_rate": f"{metric.failure_rate * 100:.2f}",
            "dlq_rate": f"{metric.dlq_rate * 100:.2f}",
            "avg_latency_ms": (
                metric.avg_latency_ms if metric.avg_latency_ms is not None else ""
            ),
            "p95_latency_ms": (
                metric.p95_latency_ms if metric.p95_latency_ms is not None else ""
            ),
        })
    return output.getvalue()


# ===========================================================================
# NotificationAlertEvent exports
# ===========================================================================


def export_notification_alerts_json(
    alerts: list[NotificationAlertEvent], indent: int = 2
) -> str:
    """Serialize notification alert events to JSON.

    Sensitive fields in message are sanitized.
    """
    rows = []
    for alert in alerts:
        data = alert.model_dump(mode="json")
        # Sanitize message field for sensitive patterns
        if data.get("message"):
            data["message"] = _redact_sensitive_in_text(data["message"])
        rows.append(data)
    return json.dumps(rows, indent=indent)


def export_notification_alerts_csv(
    alerts: list[NotificationAlertEvent],
) -> str:
    """Convert notification alert events to CSV string.

    Stable column order: alert_id, rule_id, name, severity, metric,
    observed_value, threshold, federation_id, channel, message, status,
    created_at, acknowledged_at, acknowledged_by, resolved_at, resolved_by.

    message field is sanitized for sensitive patterns.
    """
    headers = [
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
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    for alert in alerts:
        writer.writerow({
            "alert_id": alert.alert_id,
            "rule_id": alert.rule_id,
            "name": alert.name,
            "severity": alert.severity,
            "metric": alert.metric,
            "observed_value": alert.observed_value,
            "threshold": alert.threshold,
            "federation_id": alert.federation_id or "",
            "channel": alert.channel or "",
            "message": (
                _redact_sensitive_in_text(alert.message)
                if alert.message
                else ""
            ),
            "status": alert.status,
            "created_at": alert.created_at.isoformat(),
            "acknowledged_at": (
                alert.acknowledged_at.isoformat() if alert.acknowledged_at else ""
            ),
            "acknowledged_by": alert.acknowledged_by or "",
            "resolved_at": (
                alert.resolved_at.isoformat() if alert.resolved_at else ""
            ),
            "resolved_by": alert.resolved_by or "",
        })
    return output.getvalue()
