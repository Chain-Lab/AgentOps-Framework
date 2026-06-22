"""Prometheus text exposition export for notification metrics.

Phase 53 Task 5: Prometheus metrics export.
"""
from __future__ import annotations

from typing import Any


def _escape_label_value(value: str) -> str:
    """Escape a Prometheus label value per the exposition format spec."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return escaped


def export_notification_prometheus_metrics(
    metrics: list[Any],
    health: list[Any],
    alerts: list[Any],
) -> str:
    """Generate Prometheus text exposition format from notification data.

    Args:
        metrics: List of NotificationMetricWindow objects.
        health: List of ChannelHealthSnapshot objects (for reference, not directly exported).
        alerts: List of NotificationAlertEvent objects.

    Returns:
        Prometheus text exposition format string.
    """
    lines: list[str] = []

    # --- Metrics counters and gauges ---
    lines.append("# HELP agentapp_notification_total Total notification delivery events")
    lines.append("# TYPE agentapp_notification_total counter")
    for m in metrics:
        labels = _format_labels({"channel": m.channel or "all", "federation_id": m.federation_id or "all"})
        lines.append(f'agentapp_notification_total{labels} {m.total}')

    lines.append("# HELP agentapp_notification_sent_total Successfully sent notifications")
    lines.append("# TYPE agentapp_notification_sent_total counter")
    for m in metrics:
        labels = _format_labels({"channel": m.channel or "all", "federation_id": m.federation_id or "all"})
        lines.append(f'agentapp_notification_sent_total{labels} {m.sent}')

    lines.append("# HELP agentapp_notification_failed_total Failed notifications")
    lines.append("# TYPE agentapp_notification_failed_total counter")
    for m in metrics:
        labels = _format_labels({"channel": m.channel or "all", "federation_id": m.federation_id or "all"})
        lines.append(f'agentapp_notification_failed_total{labels} {m.failed}')

    lines.append("# HELP agentapp_notification_suppressed_total Suppressed notifications")
    lines.append("# TYPE agentapp_notification_suppressed_total counter")
    for m in metrics:
        labels = _format_labels({"channel": m.channel or "all", "federation_id": m.federation_id or "all"})
        lines.append(f'agentapp_notification_suppressed_total{labels} {m.suppressed}')

    lines.append("# HELP agentapp_notification_dlq_total Notifications sent to DLQ")
    lines.append("# TYPE agentapp_notification_dlq_total counter")
    for m in metrics:
        labels = _format_labels({"channel": m.channel or "all", "federation_id": m.federation_id or "all"})
        lines.append(f'agentapp_notification_dlq_total{labels} {m.dlq}')

    lines.append("# HELP agentapp_notification_retry_scheduled_total Notifications with retry scheduled")
    lines.append("# TYPE agentapp_notification_retry_scheduled_total counter")
    for m in metrics:
        labels = _format_labels({"channel": m.channel or "all", "federation_id": m.federation_id or "all"})
        lines.append(f'agentapp_notification_retry_scheduled_total{labels} {m.retry_scheduled}')

    lines.append("# HELP agentapp_notification_success_rate Notification success rate")
    lines.append("# TYPE agentapp_notification_success_rate gauge")
    for m in metrics:
        labels = _format_labels({"channel": m.channel or "all", "federation_id": m.federation_id or "all"})
        lines.append(f'agentapp_notification_success_rate{labels} {m.success_rate:.6f}')

    lines.append("# HELP agentapp_notification_failure_rate Notification failure rate")
    lines.append("# TYPE agentapp_notification_failure_rate gauge")
    for m in metrics:
        labels = _format_labels({"channel": m.channel or "all", "federation_id": m.federation_id or "all"})
        lines.append(f'agentapp_notification_failure_rate{labels} {m.failure_rate:.6f}')

    lines.append("# HELP agentapp_notification_dlq_rate Notification DLQ rate")
    lines.append("# TYPE agentapp_notification_dlq_rate gauge")
    for m in metrics:
        labels = _format_labels({"channel": m.channel or "all", "federation_id": m.federation_id or "all"})
        lines.append(f'agentapp_notification_dlq_rate{labels} {m.dlq_rate:.6f}')

    lines.append("# HELP agentapp_notification_avg_latency_ms Average delivery latency in milliseconds")
    lines.append("# TYPE agentapp_notification_avg_latency_ms gauge")
    for m in metrics:
        if m.avg_latency_ms is not None:
            labels = _format_labels({"channel": m.channel or "all", "federation_id": m.federation_id or "all"})
            lines.append(f'agentapp_notification_avg_latency_ms{labels} {m.avg_latency_ms:.1f}')

    lines.append("# HELP agentapp_notification_latency_p95_ms Notification p95 latency in milliseconds")
    lines.append("# TYPE agentapp_notification_latency_p95_ms gauge")
    for m in metrics:
        if m.p95_latency_ms is not None:
            labels = _format_labels({"channel": m.channel or "all", "federation_id": m.federation_id or "all"})
            lines.append(f'agentapp_notification_latency_p95_ms{labels} {m.p95_latency_ms:.1f}')

    # --- Open alerts by severity ---
    severity_counts: dict[str, int] = {}
    for a in alerts:
        if a.status == "open":
            sev = a.severity or "unknown"
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

    lines.append("# HELP agentapp_notification_alerts_open Open notification alerts")
    lines.append("# TYPE agentapp_notification_alerts_open gauge")
    for sev, count in severity_counts.items():
        labels = _format_labels({"severity": sev})
        lines.append(f'agentapp_notification_alerts_open{labels} {count}')

    return "\n".join(lines) + "\n"


def _format_labels(labels: dict[str, str]) -> str:
    """Format labels dict as Prometheus label string."""
    parts = []
    for k, v in labels.items():
        escaped = _escape_label_value(str(v))
        parts.append(f'{k}="{escaped}"')
    if parts:
        return "{" + ",".join(parts) + "}"
    return ""
