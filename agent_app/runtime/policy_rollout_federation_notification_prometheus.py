"""Prometheus text exposition export for notification metrics.

Phase 53 Task 5: Prometheus metrics export.
Phase 56 Task 728: Expanded metrics — daemon health, retry queue, DLQ, dedup.
"""
from __future__ import annotations

from typing import Any


def _escape_label_value(value: str) -> str:
    """Escape a Prometheus label value per the exposition format spec."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return escaped


def _format_labels(labels: dict[str, str]) -> str:
    """Format labels dict as Prometheus label string."""
    parts = []
    for k, v in labels.items():
        escaped = _escape_label_value(str(v))
        parts.append(f'{k}="{escaped}"')
    if parts:
        return "{" + ",".join(parts) + "}"
    return ""


# Phase 56: Daemon state mapping for gauge
_DAEMON_STATE_MAP = {
    "stopped": 0,
    "healthy": 1,
    "degraded": 2,
    "unhealthy": 3,
}


def export_notification_prometheus_metrics(
    metrics: list[Any],
    health: list[Any],
    alerts: list[Any],
    daemon_health: dict[str, Any] | None = None,
    retry_queue_depth: int = 0,
    dlq_depth: int = 0,
    dedup_active: int = 0,
) -> str:
    """Generate Prometheus text exposition format from notification data.

    Args:
        metrics: List of NotificationMetricWindow objects.
        health: List of ChannelHealthSnapshot objects (for reference, not directly exported).
        alerts: List of NotificationAlertEvent objects.
        daemon_health: Optional daemon health status dict from get_health_status().
        retry_queue_depth: Number of items in the retry priority queue.
        dlq_depth: Number of items in the dead-letter queue.
        dedup_active: Number of active dedup entries.

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

    # --- Phase 56: Daemon health metrics ---
    if daemon_health is not None:
        state_name = daemon_health.get("state", "stopped")
        state_value = _DAEMON_STATE_MAP.get(state_name, 0)

        lines.append("# HELP agentapp_notification_daemon_state Daemon health state (0=stopped, 1=healthy, 2=degraded, 3=unhealthy)")
        lines.append("# TYPE agentapp_notification_daemon_state gauge")
        lines.append(f'agentapp_notification_daemon_state {state_value}')

        lines.append("# HELP agentapp_notification_daemon_consecutive_failures Number of consecutive daemon run failures")
        lines.append("# TYPE agentapp_notification_daemon_consecutive_failures gauge")
        lines.append(f'agentapp_notification_daemon_consecutive_failures {daemon_health.get("consecutive_failures", 0)}')

    # --- Phase 56: Queue depth metrics ---
    lines.append("# HELP agentapp_notification_retry_queue_depth Number of items in the retry priority queue")
    lines.append("# TYPE agentapp_notification_retry_queue_depth gauge")
    lines.append(f'agentapp_notification_retry_queue_depth {retry_queue_depth}')

    lines.append("# HELP agentapp_notification_dlq_depth Number of items in the dead-letter queue")
    lines.append("# TYPE agentapp_notification_dlq_depth gauge")
    lines.append(f'agentapp_notification_dlq_depth {dlq_depth}')

    lines.append("# HELP agentapp_notification_dedup_active_active Number of active dedup entries")
    lines.append("# TYPE agentapp_notification_dedup_active_active gauge")
    lines.append(f'agentapp_notification_dedup_active_active {dedup_active}')

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
