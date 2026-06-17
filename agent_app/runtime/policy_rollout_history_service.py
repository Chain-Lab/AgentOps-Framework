"""Rollout history service — timeline generation and analytics reporting."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from agent_app.governance.policy_rollout_history import (
    RolloutAnalyticsReport,
    RolloutApprovalOutcomeSummary,
    RolloutGateOutcomeSummary,
    RolloutHistoryEvent,
    RolloutHistoryEventType,
    RolloutStepTimeline,
    RolloutTimeline,
)

logger = logging.getLogger(__name__)

# Event types that indicate rollout-level status
_ROLLOUT_STATUS_EVENTS: set[RolloutHistoryEventType] = {
    RolloutHistoryEventType.ROLLOUT_CREATED,
    RolloutHistoryEventType.ROLLOUT_STARTED,
    RolloutHistoryEventType.ROLLOUT_COMPLETED,
    RolloutHistoryEventType.ROLLOUT_FAILED,
    RolloutHistoryEventType.ROLLOUT_CANCELLED,
}

_GATE_EVENTS: set[RolloutHistoryEventType] = {
    RolloutHistoryEventType.GATE_RUN,
    RolloutHistoryEventType.GATE_SATISFIED,
    RolloutHistoryEventType.GATE_BLOCKED,
    RolloutHistoryEventType.GATE_FAILED,
    RolloutHistoryEventType.GATE_SKIPPED,
    RolloutHistoryEventType.GATE_EXPIRED,
}

_APPROVAL_EVENTS: set[RolloutHistoryEventType] = {
    RolloutHistoryEventType.APPROVAL_REQUESTED,
    RolloutHistoryEventType.APPROVAL_APPROVED,
    RolloutHistoryEventType.APPROVAL_REJECTED,
    RolloutHistoryEventType.APPROVAL_EXPIRED,
    RolloutHistoryEventType.APPROVAL_DECISION_RECORDED,
}


class RolloutHistoryService:
    """Provides rollout timeline generation and analytics reporting.

    Uses normalized history events as primary timeline source.
    Enriches with rollout plan/step data where available.
    Missing optional stores produce partial timeline/report, not failure.
    """

    def __init__(
        self,
        history_store: Any | None = None,
        rollout_store: Any | None = None,
        rollout_approval_store: Any | None = None,
        release_gate_requirement_store: Any | None = None,
        notification_store: Any | None = None,
        audit_logger: Any | None = None,
    ) -> None:
        self._history_store = history_store
        self._rollout_store = rollout_store
        self._rollout_approval_store = rollout_approval_store
        self._release_gate_requirement_store = release_gate_requirement_store
        self._notification_store = notification_store
        self._audit_logger = audit_logger

    async def get_timeline(self, rollout_id: str) -> RolloutTimeline:
        """Build a RolloutTimeline from history events for a specific rollout.

        Enriches from rollout/approval/gate/notification stores where available.
        Returns a partial timeline if some stores are unavailable.
        """
        timeline = RolloutTimeline(rollout_id=rollout_id)

        # Get history events
        if self._history_store is None:
            return timeline

        events = await self._history_store.list(rollout_id=rollout_id)
        timeline.events = events

        # Enrich from rollout store
        plan = None
        if self._rollout_store is not None:
            try:
                plan = await self._rollout_store.get(rollout_id)
            except Exception:
                pass

        if plan is not None:
            timeline.name = plan.name
            timeline.bundle_id = plan.bundle_id
            timeline.status = plan.status.value if hasattr(plan.status, "value") else str(plan.status)
            timeline.created_at = plan.created_at

        # Group events by step_id
        step_events: dict[str, list[RolloutHistoryEvent]] = defaultdict(list)
        rollout_level_events: list[RolloutHistoryEvent] = []
        for event in events:
            if event.step_id:
                step_events[event.step_id].append(event)
            else:
                rollout_level_events.append(event)

        # Build step timelines
        steps: list[RolloutStepTimeline] = []
        for step_id, step_evts in sorted(step_events.items()):
            step_tl = RolloutStepTimeline(step_id=step_id)
            step_tl.events = step_evts

            # Determine step status from events
            for evt in reversed(step_evts):
                if evt.event_type == RolloutHistoryEventType.STEP_STARTED:
                    step_tl.started_at = evt.created_at
                elif evt.event_type == RolloutHistoryEventType.STEP_SUCCEEDED:
                    step_tl.status = "succeeded"
                    step_tl.completed_at = evt.created_at
                elif evt.event_type == RolloutHistoryEventType.STEP_FAILED:
                    step_tl.status = "failed"
                    step_tl.completed_at = evt.created_at
                elif evt.event_type == RolloutHistoryEventType.STEP_BLOCKED:
                    step_tl.status = "blocked"
                elif evt.event_type == RolloutHistoryEventType.STEP_SKIPPED:
                    step_tl.status = "skipped"
                elif evt.event_type == RolloutHistoryEventType.GATE_SATISFIED:
                    step_tl.gate_status = "satisfied"
                elif evt.event_type == RolloutHistoryEventType.GATE_BLOCKED:
                    step_tl.gate_status = "blocked"
                elif evt.event_type == RolloutHistoryEventType.GATE_FAILED:
                    step_tl.gate_status = "failed"
                elif evt.event_type == RolloutHistoryEventType.GATE_SKIPPED:
                    step_tl.gate_status = "skipped"
                elif evt.event_type == RolloutHistoryEventType.GATE_EXPIRED:
                    step_tl.gate_status = "expired"
                elif evt.event_type == RolloutHistoryEventType.APPROVAL_APPROVED:
                    step_tl.approval_status = "approved"
                elif evt.event_type == RolloutHistoryEventType.APPROVAL_REJECTED:
                    step_tl.approval_status = "rejected"
                elif evt.event_type == RolloutHistoryEventType.APPROVAL_EXPIRED:
                    step_tl.approval_status = "expired"
                elif evt.event_type == RolloutHistoryEventType.APPROVAL_REQUESTED:
                    step_tl.approval_status = "pending"

            # Compute duration
            if step_tl.started_at and step_tl.completed_at:
                step_tl.duration_seconds = (step_tl.completed_at - step_tl.started_at).total_seconds()

            # Enrich from rollout plan step data
            if plan is not None:
                for plan_step in plan.steps:
                    if plan_step.step_id == step_id:
                        step_tl.step_type = getattr(plan_step, "step_type", None)
                        step_tl.environment = getattr(plan_step, "environment", None)
                        step_tl.ring_name = getattr(plan_step, "ring_name", None)
                        if step_tl.status is None:
                            step_tl.status = plan_step.status.value if hasattr(plan_step.status, "value") else str(plan_step.status)
                        step_tl.error = getattr(plan_step, "error", None)
                        break

            # Set environment/ring from events if not from plan
            if step_tl.environment is None:
                for evt in step_evts:
                    if evt.environment:
                        step_tl.environment = evt.environment
                        break
            if step_tl.ring_name is None:
                for evt in step_evts:
                    if evt.ring_name:
                        step_tl.ring_name = evt.ring_name
                        break

            steps.append(step_tl)

        timeline.steps = steps

        # Compute rollout-level timing
        rollout_started = None
        rollout_completed = None
        for evt in events:
            if evt.event_type == RolloutHistoryEventType.ROLLOUT_STARTED:
                rollout_started = evt.created_at
                timeline.started_at = evt.created_at
            elif evt.event_type == RolloutHistoryEventType.ROLLOUT_COMPLETED:
                rollout_completed = evt.created_at
                timeline.completed_at = evt.created_at
            elif evt.event_type == RolloutHistoryEventType.ROLLOUT_FAILED:
                rollout_completed = evt.created_at
                timeline.completed_at = evt.created_at
            elif evt.event_type == RolloutHistoryEventType.ROLLOUT_CANCELLED:
                rollout_completed = evt.created_at
                timeline.completed_at = evt.created_at

        if timeline.started_at and timeline.completed_at:
            timeline.duration_seconds = (timeline.completed_at - timeline.started_at).total_seconds()

        return timeline

    async def generate_report(
        self,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> RolloutAnalyticsReport:
        """Generate a RolloutAnalyticsReport from history events.

        Computes rollout counts, gate/approval outcome summaries,
        top blocked steps, top failed gates, and environment/ring summaries.
        """
        report = RolloutAnalyticsReport(
            report_id=f"rar_{uuid.uuid4().hex[:12]}",
            generated_at=datetime.now(timezone.utc),
            window_start=window_start,
            window_end=window_end,
        )

        if self._history_store is None:
            return report

        # Get all events in window
        events = await self._history_store.list(
            window_start=window_start,
            window_end=window_end,
        )

        if not events:
            return report

        # Group by rollout_id
        rollout_events: dict[str, list[RolloutHistoryEvent]] = defaultdict(list)
        for event in events:
            rollout_events[event.rollout_id].append(event)

        # Count rollouts by status
        blocked_rollout_ids: set[str] = set()
        completed_rollout_ids: set[str] = set()
        failed_rollout_ids: set[str] = set()
        cancelled_rollout_ids: set[str] = set()

        for rid, revts in rollout_events.items():
            has_blocked = False
            for evt in revts:
                if evt.event_type == RolloutHistoryEventType.ROLLOUT_COMPLETED:
                    completed_rollout_ids.add(rid)
                elif evt.event_type == RolloutHistoryEventType.ROLLOUT_FAILED:
                    failed_rollout_ids.add(rid)
                elif evt.event_type == RolloutHistoryEventType.ROLLOUT_CANCELLED:
                    cancelled_rollout_ids.add(rid)
                elif evt.event_type == RolloutHistoryEventType.STEP_BLOCKED:
                    has_blocked = True
            if has_blocked and rid not in completed_rollout_ids and rid not in failed_rollout_ids:
                blocked_rollout_ids.add(rid)

        report.total_rollouts = len(rollout_events)
        report.completed_rollouts = len(completed_rollout_ids)
        report.failed_rollouts = len(failed_rollout_ids)
        report.cancelled_rollouts = len(cancelled_rollout_ids)
        report.blocked_rollouts = len(blocked_rollout_ids)

        # Gate outcome summary
        gate_counts: dict[str, int] = defaultdict(int)
        for evt in events:
            if evt.event_type == RolloutHistoryEventType.GATE_SATISFIED:
                gate_counts["satisfied"] += 1
            elif evt.event_type == RolloutHistoryEventType.GATE_BLOCKED:
                gate_counts["blocked"] += 1
            elif evt.event_type == RolloutHistoryEventType.GATE_FAILED:
                gate_counts["failed"] += 1
            elif evt.event_type == RolloutHistoryEventType.GATE_SKIPPED:
                gate_counts["skipped"] += 1
            elif evt.event_type == RolloutHistoryEventType.GATE_EXPIRED:
                gate_counts["expired"] += 1

        report.gate_outcomes = RolloutGateOutcomeSummary(
            total=sum(gate_counts.values()),
            satisfied=gate_counts.get("satisfied", 0),
            blocked=gate_counts.get("blocked", 0),
            failed=gate_counts.get("failed", 0),
            skipped=gate_counts.get("skipped", 0),
            expired=gate_counts.get("expired", 0),
        )

        # Approval outcome summary
        approval_counts: dict[str, int] = defaultdict(int)
        approval_latencies: list[float] = []
        # Track request times for latency calculation
        approval_request_times: dict[str, datetime] = {}

        for evt in events:
            if evt.event_type == RolloutHistoryEventType.APPROVAL_REQUESTED:
                approval_counts["pending"] += 1
                # Key by rollout_id + step_id for matching
                key = f"{evt.rollout_id}:{evt.step_id or ''}"
                approval_request_times[key] = evt.created_at
            elif evt.event_type == RolloutHistoryEventType.APPROVAL_APPROVED:
                approval_counts["approved"] += 1
                key = f"{evt.rollout_id}:{evt.step_id or ''}"
                if key in approval_request_times:
                    latency = (evt.created_at - approval_request_times[key]).total_seconds()
                    approval_latencies.append(latency)
            elif evt.event_type == RolloutHistoryEventType.APPROVAL_REJECTED:
                approval_counts["rejected"] += 1
            elif evt.event_type == RolloutHistoryEventType.APPROVAL_EXPIRED:
                approval_counts["expired"] += 1

        avg_latency = None
        if approval_latencies:
            avg_latency = sum(approval_latencies) / len(approval_latencies)

        report.approval_outcomes = RolloutApprovalOutcomeSummary(
            total=sum(approval_counts.values()),
            pending=approval_counts.get("pending", 0),
            approved=approval_counts.get("approved", 0),
            rejected=approval_counts.get("rejected", 0),
            expired=approval_counts.get("expired", 0),
            average_latency_seconds=avg_latency,
        )

        # Top blocked steps
        blocked_step_counts: dict[str, int] = defaultdict(int)
        for evt in events:
            if evt.event_type == RolloutHistoryEventType.STEP_BLOCKED and evt.step_id:
                blocked_step_counts[evt.step_id] += 1
        top_blocked = sorted(blocked_step_counts.items(), key=lambda x: (-x[1], x[0]))[:5]
        report.top_blocked_steps = [
            {"step_id": sid, "count": count} for sid, count in top_blocked
        ]

        # Top failed gates
        failed_gate_counts: dict[str, int] = defaultdict(int)
        for evt in events:
            if evt.event_type == RolloutHistoryEventType.GATE_FAILED and evt.step_id:
                failed_gate_counts[evt.step_id] += 1
        top_failed = sorted(failed_gate_counts.items(), key=lambda x: (-x[1], x[0]))[:5]
        report.top_failed_gates = [
            {"step_id": sid, "count": count} for sid, count in top_failed
        ]

        # Environment summary
        env_counts: dict[str, int] = defaultdict(int)
        for evt in events:
            if evt.environment:
                env_counts[evt.environment] += 1
        report.environment_summary = [
            {"environment": env, "event_count": count}
            for env, count in sorted(env_counts.items(), key=lambda x: (-x[1], x[0]))
        ]

        # Ring summary
        ring_counts: dict[str, int] = defaultdict(int)
        for evt in events:
            if evt.ring_name:
                ring_counts[evt.ring_name] += 1
        report.ring_summary = [
            {"ring_name": ring, "event_count": count}
            for ring, count in sorted(ring_counts.items(), key=lambda x: (-x[1], x[0]))
        ]

        return report

    async def list_history_events(
        self,
        rollout_id: str | None = None,
        step_id: str | None = None,
        event_type: RolloutHistoryEventType | None = None,
        limit: int | None = None,
    ) -> list[RolloutHistoryEvent]:
        """List history events from the store."""
        if self._history_store is None:
            return []

        return await self._history_store.list(
            rollout_id=rollout_id,
            step_id=step_id,
            event_type=event_type,
            limit=limit,
        )
