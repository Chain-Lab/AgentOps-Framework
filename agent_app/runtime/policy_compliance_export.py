"""Policy compliance export — JSON and CSV export helpers for observability reports.

Phase 39: MVP export for compliance-oriented reporting.
"""

from __future__ import annotations

import json
from typing import Any

from agent_app.governance.policy_observability import PolicyObservabilityReport
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
