"""Policy compliance export — JSON and CSV export helpers for observability reports.

Phase 39: MVP export for compliance-oriented reporting.
"""

from __future__ import annotations

import json
from typing import Any

from agent_app.governance.policy_observability import PolicyObservabilityReport
from agent_app.governance.policy_rollout_federation_notification import FederationNotificationDeadLetter
from agent_app.governance.policy_simulation import PolicySimulationReport
from agent_app.runtime.policy_validation import PolicyValidationReport


def report_to_json(report: PolicyObservabilityReport) -> str:
    """Export full report as JSON string."""
    return report.model_dump_json(indent=2)


def report_to_csv_rows(report: PolicyObservabilityReport) -> list[dict[str, Any]]:
    """Export report summaries as flat CSV-ready rows.

    Returns rows with a 'section' column indicating which summary the row belongs to.
    Columns: section, key, allowed, denied, approval_required, total
    """
    rows: list[dict[str, Any]] = []

    # Action summaries
    for action in report.actions:
        rows.append({
            "section": "action",
            "key": action.action_type,
            "allowed": action.allowed,
            "denied": action.denied,
            "approval_required": action.approval_required,
            "total": action.total,
        })

    # Actor summaries
    for actor in report.actors:
        rows.append({
            "section": "actor",
            "key": actor.actor_id,
            "allowed": actor.allowed,
            "denied": actor.denied,
            "approval_required": actor.approval_required,
            "total": actor.total,
        })

    # Tool summaries
    for tool in report.tools:
        rows.append({
            "section": "tool",
            "key": tool.tool_name,
            "allowed": tool.allowed,
            "denied": tool.denied,
            "approval_required": tool.approval_required,
            "total": tool.total,
        })

    return rows


def simulation_report_to_json(report: PolicySimulationReport) -> str:
    """Export simulation report as JSON string."""
    return report.model_dump_json(indent=2)


def simulation_report_to_csv_rows(report: PolicySimulationReport) -> list[dict[str, Any]]:
    """Export simulation report results as flat CSV-ready rows."""
    rows: list[dict[str, Any]] = []
    for result in report.results:
        rows.append({
            "case_id": result.case_id,
            "baseline_status": result.baseline_status,
            "candidate_status": result.candidate_status,
            "outcome": result.outcome.value,
            "reason": result.reason,
            "decision_id": result.decision_id,
            "errors": "; ".join(result.errors) if result.errors else "",
        })
    return rows


def validation_report_to_json(report: PolicyValidationReport) -> str:
    """Export validation report as JSON string."""
    return report.model_dump_json(indent=2)


def rollout_timeline_to_json(timeline: Any) -> str:
    """Export rollout timeline as JSON string."""
    return timeline.model_dump_json(indent=2)


def rollout_analytics_report_to_json(report: Any) -> str:
    """Export rollout analytics report as JSON string."""
    return report.model_dump_json(indent=2)


def rollout_analytics_report_to_csv_rows(report: Any) -> list[dict[str, Any]]:
    """Export rollout analytics report as flat CSV-ready rows."""
    rows: list[dict[str, Any]] = []

    # Summary row
    rows.append({
        "section": "summary",
        "total_rollouts": report.total_rollouts,
        "completed_rollouts": report.completed_rollouts,
        "failed_rollouts": report.failed_rollouts,
        "cancelled_rollouts": report.cancelled_rollouts,
        "blocked_rollouts": report.blocked_rollouts,
    })

    # Gate outcomes
    rows.append({
        "section": "gate_outcomes",
        "total": report.gate_outcomes.total,
        "satisfied": report.gate_outcomes.satisfied,
        "blocked": report.gate_outcomes.blocked,
        "failed": report.gate_outcomes.failed,
        "skipped": report.gate_outcomes.skipped,
        "expired": report.gate_outcomes.expired,
    })

    # Approval outcomes
    rows.append({
        "section": "approval_outcomes",
        "total": report.approval_outcomes.total,
        "pending": report.approval_outcomes.pending,
        "approved": report.approval_outcomes.approved,
        "rejected": report.approval_outcomes.rejected,
        "expired": report.approval_outcomes.expired,
        "average_latency_seconds": report.approval_outcomes.average_latency_seconds,
    })

    # Top blocked steps
    for item in report.top_blocked_steps:
        rows.append({
            "section": "top_blocked_steps",
            "step_id": item.get("step_id", ""),
            "count": item.get("count", 0),
        })

    # Top failed gates
    for item in report.top_failed_gates:
        rows.append({
            "section": "top_failed_gates",
            "step_id": item.get("step_id", ""),
            "count": item.get("count", 0),
        })

    # Environment summary
    for item in report.environment_summary:
        rows.append({
            "section": "environment_summary",
            "environment": item.get("environment", ""),
            "event_count": item.get("event_count", 0),
        })

    # Ring summary
    for item in report.ring_summary:
        rows.append({
            "section": "ring_summary",
            "ring_name": item.get("ring_name", ""),
            "event_count": item.get("event_count", 0),
        })

    return rows


def federation_timeline_to_json(timeline: Any) -> str:
    """Export a FederationTimeline to JSON string."""
    return timeline.model_dump_json(indent=2)


def federation_analytics_report_to_json(report: Any) -> str:
    """Export a FederationAnalyticsReport to JSON string."""
    return report.model_dump_json(indent=2)


def federation_analytics_report_to_csv_rows(report: Any) -> list[dict[str, Any]]:
    """Export a FederationAnalyticsReport to flat CSV-compatible rows."""
    rows: list[dict[str, Any]] = []
    # Summary row
    rows.append({
        "section": "summary",
        "report_id": report.report_id,
        "generated_at": report.generated_at.isoformat(),
        "window_start": report.window_start.isoformat() if report.window_start else "",
        "window_end": report.window_end.isoformat() if report.window_end else "",
        "total_federations": report.total_federations,
        "active_federations": report.active_federations,
        "completed_federations": report.completed_federations,
        "failed_federations": report.failed_federations,
        "cancelled_federations": report.cancelled_federations,
        "blocked_federations": report.blocked_federations,
    })
    # Target health row
    rows.append({
        "section": "target_health",
        "total_targets": report.target_health.total_targets,
        "enabled_targets": report.target_health.enabled_targets,
        "disabled_targets": report.target_health.disabled_targets,
        "succeeded_targets": report.target_health.succeeded_targets,
        "failed_targets": report.target_health.failed_targets,
        "blocked_targets": report.target_health.blocked_targets,
        "skipped_targets": report.target_health.skipped_targets,
    })
    # Wave outcomes row
    rows.append({
        "section": "wave_outcomes",
        "total_waves": report.wave_outcomes.total_waves,
        "succeeded_waves": report.wave_outcomes.succeeded_waves,
        "failed_waves": report.wave_outcomes.failed_waves,
        "blocked_waves": report.wave_outcomes.blocked_waves,
        "pending_waves": report.wave_outcomes.pending_waves,
    })
    # Conflict summary row
    rows.append({
        "section": "conflicts",
        "total_conflicts": report.conflicts.total_conflicts,
        "error_conflicts": report.conflicts.error_conflicts,
        "warning_conflicts": report.conflicts.warning_conflicts,
    })
    # Environment summary rows
    for env in report.environment_summary:
        rows.append({"section": "environment_summary", **env})
    # Region summary rows
    for reg in report.region_summary:
        rows.append({"section": "region_summary", **reg})
    # Tenant summary rows
    for ten in report.tenant_summary:
        rows.append({"section": "tenant_summary", **ten})
    # Approval summary row (if present in metadata)
    approval_fields = {
        k: report.metadata.get(k)
        for k in (
            "approvals_pending_count",
            "approvals_approved_count",
            "approvals_rejected_count",
            "average_approval_latency_seconds",
            "escalated_approvals_count",
            "blocked_federation_actions_count",
        )
    }
    if any(v is not None for v in approval_fields.values()):
        rows.append({"section": "approval_summary", **approval_fields})
    return rows


def export_federation_approval_summary_json(
    summary: Any,
) -> str:
    """Export a FederationApprovalDashboardSummary as JSON string."""
    return summary.model_dump_json(indent=2)


def export_federation_approval_summary_csv(
    summary: Any,
) -> list[dict[str, Any]]:
    """Export a FederationApprovalDashboardSummary as flat CSV-compatible rows."""
    rows: list[dict[str, Any]] = []
    # Totals row
    rows.append({
        "section": "totals",
        "total_pending": summary.total_pending,
        "total_approved": summary.total_approved,
        "total_rejected": summary.total_rejected,
        "total_expired": summary.total_expired,
        "total_escalated": summary.total_escalated,
        "total_cancelled": summary.total_cancelled,
        "average_approval_latency_seconds": summary.average_approval_latency_seconds or "",
        "blocked_federation_actions": summary.blocked_federation_actions,
    })
    # By tenant rows
    for tenant, count in sorted(summary.by_tenant.items()):
        rows.append({"section": "by_tenant", "tenant_id": tenant, "count": count})
    # By action rows
    for action, count in sorted(summary.by_action.items()):
        rows.append({"section": "by_action", "action": action, "count": count})
    return rows


def export_federation_notification_summary_json(summary: dict[str, Any]) -> str:
    """Export federation notification summary as JSON."""
    import json
    return json.dumps(summary, indent=2, default=str)


def export_federation_notification_summary_csv(summary: dict[str, Any]) -> str:
    """Export federation notification summary as CSV."""
    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["metric", "value"])
    for key, value in summary.items():
        writer.writerow([key, value])
    return output.getvalue()


def export_federation_dlq_summary_json(
    items: list[FederationNotificationDeadLetter],
) -> str:
    """Export DLQ entries as JSON string.

    Args:
        items: List of DLQ entries to export.

    Returns:
        JSON string with dlq_id, notification_id, approval_id, federation_id,
        channel, reason, status, failure_count, last_error, created_at, updated_at.
    """
    rows = []
    for item in items:
        rows.append({
            "dlq_id": item.dlq_id,
            "notification_id": item.notification_id,
            "approval_id": item.approval_id or "",
            "federation_id": item.federation_id or "",
            "channel": item.channel,
            "reason": item.reason.value,
            "status": item.status.value,
            "failure_count": item.failure_count,
            "last_error": item.last_error or "",
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
        })
    return json.dumps(rows, indent=2)


def export_federation_dlq_summary_csv(
    items: list[FederationNotificationDeadLetter],
) -> str:
    """Export DLQ entries as CSV string.

    Args:
        items: List of DLQ entries to export.

    Returns:
        CSV string with header row and one row per entry.
    """
    import csv
    import io

    headers = [
        "dlq_id",
        "notification_id",
        "approval_id",
        "federation_id",
        "channel",
        "reason",
        "status",
        "failure_count",
        "last_error",
        "created_at",
        "updated_at",
    ]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for item in items:
        writer.writerow([
            item.dlq_id,
            item.notification_id,
            item.approval_id or "",
            item.federation_id or "",
            item.channel,
            item.reason.value,
            item.status.value,
            item.failure_count,
            item.last_error or "",
            item.created_at.isoformat(),
            item.updated_at.isoformat(),
        ])
    return output.getvalue()


def export_federation_notification_templates_json(items: list[Any]) -> str:
    """Export notification templates as JSON.

    Fields: template_id, name, event_type, channel, format, enabled,
    version, created_at, updated_at.
    Never exports signature keys, auth headers, or full webhook bodies.
    """
    rows = []
    for item in items:
        rows.append({
            "template_id": item.template_id,
            "name": item.name,
            "event_type": item.event_type.value if hasattr(item.event_type, "value") else str(item.event_type),
            "channel": item.channel.value if hasattr(item.channel, "value") else str(item.channel),
            "format": item.format.value if hasattr(item.format, "value") else str(item.format),
            "enabled": item.enabled,
            "version": item.version,
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
        })
    return json.dumps(rows, indent=2)


def export_federation_notification_templates_csv(items: list[Any]) -> str:
    """Export notification templates as CSV.

    Fields: template_id, name, event_type, channel, format, enabled,
    version, created_at, updated_at.
    Never exports signature keys, auth headers, or full webhook bodies.
    """
    import csv
    import io

    headers = [
        "template_id",
        "name",
        "event_type",
        "channel",
        "format",
        "enabled",
        "version",
        "created_at",
        "updated_at",
    ]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for item in items:
        writer.writerow([
            item.template_id,
            item.name,
            item.event_type.value if hasattr(item.event_type, "value") else str(item.event_type),
            item.channel.value if hasattr(item.channel, "value") else str(item.channel),
            item.format.value if hasattr(item.format, "value") else str(item.format),
            item.enabled,
            item.version,
            item.created_at.isoformat(),
            item.updated_at.isoformat(),
        ])
    return output.getvalue()


def export_federation_notification_preferences_json(items: list[Any]) -> str:
    """Export notification preferences as JSON.

    Fields: preference_id, subject_type, subject_id, channel, event_type,
    decision, created_at.
    Never exports signature keys, auth headers, or full webhook bodies.
    """
    rows = []
    for item in items:
        rows.append({
            "preference_id": item.preference_id,
            "subject_type": item.subject_type.value if hasattr(item.subject_type, "value") else str(item.subject_type),
            "subject_id": item.subject_id,
            "channel": item.channel.value if hasattr(item.channel, "value") else str(item.channel),
            "event_type": item.event_type.value if hasattr(item.event_type, "value") else str(item.event_type),
            "decision": item.decision.value if hasattr(item.decision, "value") else str(item.decision),
            "created_at": item.created_at.isoformat(),
        })
    return json.dumps(rows, indent=2)


def export_federation_notification_preferences_csv(items: list[Any]) -> str:
    """Export notification preferences as CSV.

    Fields: preference_id, subject_type, subject_id, channel, event_type,
    decision, created_at.
    Never exports signature keys, auth headers, or full webhook bodies.
    """
    import csv
    import io

    headers = [
        "preference_id",
        "subject_type",
        "subject_id",
        "channel",
        "event_type",
        "decision",
        "created_at",
    ]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for item in items:
        writer.writerow([
            item.preference_id,
            item.subject_type.value if hasattr(item.subject_type, "value") else str(item.subject_type),
            item.subject_id,
            item.channel.value if hasattr(item.channel, "value") else str(item.channel),
            item.event_type.value if hasattr(item.event_type, "value") else str(item.event_type),
            item.decision.value if hasattr(item.decision, "value") else str(item.decision),
            item.created_at.isoformat(),
        ])
    return output.getvalue()


def export_federation_webhook_replays_json(items: list[Any]) -> str:
    """Export webhook replay results as JSON. Only includes digest, NOT full body.

    Fields: replay_id, dlq_id, notification_id, success, replay_count,
    last_replay_at, payload_digest.
    Never exports signature keys, auth headers, or full webhook bodies.
    """
    rows = []
    for item in items:
        rows.append({
            "replay_id": item.replay_id,
            "dlq_id": item.dlq_id,
            "notification_id": item.notification_id,
            "success": item.success,
            "replay_count": item.replay_count,
            "last_replay_at": item.last_replay_at.isoformat() if item.last_replay_at else "",
            "payload_digest": item.payload_digest if hasattr(item, "payload_digest") and item.payload_digest else "",
        })
    return json.dumps(rows, indent=2)


def export_federation_webhook_replays_csv(items: list[Any]) -> str:
    """Export webhook replay results as CSV. Only includes digest, NOT full body.

    Fields: replay_id, dlq_id, notification_id, success, replay_count,
    last_replay_at, payload_digest.
    Never exports signature keys, auth headers, or full webhook bodies.
    """
    import csv
    import io

    headers = [
        "replay_id",
        "dlq_id",
        "notification_id",
        "success",
        "replay_count",
        "last_replay_at",
        "payload_digest",
    ]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for item in items:
        writer.writerow([
            item.replay_id,
            item.dlq_id,
            item.notification_id,
            item.success,
            item.replay_count,
            item.last_replay_at.isoformat() if item.last_replay_at else "",
            item.payload_digest if hasattr(item, "payload_digest") and item.payload_digest else "",
        ])
    return output.getvalue()
