"""Phase 47: Federation observability service — timeline reconstruction and analytics.

Uses federation history events as primary source.
Enriches from FederatedRolloutPlan and FederatedRolloutTarget if available.
Missing optional stores produce partial timeline/report, not failure.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from agent_app.governance.policy_rollout_federation_history import (
    FederationAnalyticsReport,
    FederationConflictSummary,
    FederationHistoryEvent,
    FederationHistoryEventType,
    FederationTargetHealthSummary,
    FederationTargetTimeline,
    FederationTimeline,
    FederationWaveOutcomeSummary,
    FederationWaveTimeline,
)
from agent_app.governance.policy_rollout_federation_approval import (
    FederationApprovalDashboardSummary,
)
from agent_app.runtime.policy_rollout_federation_history_store import (
    FederationHistoryStore,
)
from agent_app.runtime.policy_rollout_federation_store import (
    FederatedRolloutPlanStore,
    FederatedRolloutTargetStore,
)


class FederationObservabilityService:
    """Federation observability service — timeline reconstruction and analytics.

    Uses federation history events as primary source.
    Enriches from FederatedRolloutPlan and FederatedRolloutTarget if available.
    Missing optional stores produce partial timeline/report, not failure.
    """

    def __init__(
        self,
        history_store: FederationHistoryStore | None = None,
        federation_plan_store: FederatedRolloutPlanStore | None = None,
        federation_target_store: FederatedRolloutTargetStore | None = None,
        rollout_history_service: Any | None = None,
        notification_store: Any | None = None,
        audit_logger: Any | None = None,
        approval_store: Any | None = None,
    ) -> None:
        self._history_store = history_store
        self._federation_plan_store = federation_plan_store
        self._federation_target_store = federation_target_store
        self._rollout_history_service = rollout_history_service
        self._notification_store = notification_store
        self._audit_logger = audit_logger
        self._approval_store = approval_store

    # ------------------------------------------------------------------
    # Timeline
    # ------------------------------------------------------------------

    async def get_timeline(self, federation_id: str) -> FederationTimeline:
        """Build a FederationTimeline from history events for a specific federation.

        Enriches from federation plan/target stores where available.
        Returns a partial timeline if some stores are unavailable.
        """
        timeline = FederationTimeline(federation_id=federation_id)

        if self._history_store is None:
            return timeline

        events = await self._history_store.list(federation_id=federation_id)
        timeline.events = events

        # Enrich from federation plan store
        plan = None
        if self._federation_plan_store is not None:
            try:
                plan = await self._federation_plan_store.get(federation_id)
            except Exception:
                pass

        if plan is not None:
            timeline.name = plan.name
            timeline.bundle_id = plan.bundle_id
            timeline.strategy = plan.strategy.value if hasattr(plan.strategy, "value") else str(plan.strategy)
            timeline.status = plan.status.value if hasattr(plan.status, "value") else str(plan.status)
            timeline.created_at = plan.created_at

        # Group events by target_id → build FederationTargetTimeline list
        target_events: dict[str, list[FederationHistoryEvent]] = defaultdict(list)
        for event in events:
            if event.target_id:
                target_events[event.target_id].append(event)

        targets: list[FederationTargetTimeline] = []
        for target_id, tgt_evts in sorted(target_events.items()):
            target_tl = FederationTargetTimeline(target_id=target_id)
            target_tl.events = tgt_evts

            # Determine status from event types
            for evt in reversed(tgt_evts):
                if evt.event_type == FederationHistoryEventType.TARGET_EXECUTION_STARTED:
                    target_tl.started_at = evt.created_at
                elif evt.event_type == FederationHistoryEventType.TARGET_EXECUTION_SUCCEEDED:
                    target_tl.status = "succeeded"
                    target_tl.completed_at = evt.created_at
                elif evt.event_type == FederationHistoryEventType.TARGET_EXECUTION_FAILED:
                    target_tl.status = "failed"
                    target_tl.completed_at = evt.created_at
                elif evt.event_type == FederationHistoryEventType.TARGET_EXECUTION_BLOCKED:
                    target_tl.status = "blocked"
                elif evt.event_type == FederationHistoryEventType.TARGET_EXECUTION_SKIPPED:
                    target_tl.status = "skipped"

            # Calculate duration
            if target_tl.started_at and target_tl.completed_at:
                target_tl.duration_seconds = (
                    target_tl.completed_at - target_tl.started_at
                ).total_seconds()

            # Enrich from federation target store
            if self._federation_target_store is not None:
                try:
                    target = await self._federation_target_store.get(target_id)
                    if target is not None:
                        target_tl.environment = target.environment
                        target_tl.ring_name = target.ring_name
                        target_tl.region = target.region
                        target_tl.tenant_id = target.tenant_id
                        if target_tl.rollout_id is None:
                            target_tl.rollout_id = None
                except Exception:
                    pass

            # Also enrich from event metadata (environment, ring_name, region)
            if target_tl.environment is None:
                for evt in tgt_evts:
                    if evt.environment:
                        target_tl.environment = evt.environment
                        break
            if target_tl.ring_name is None:
                for evt in tgt_evts:
                    if evt.ring_name:
                        target_tl.ring_name = evt.ring_name
                        break
            if target_tl.region is None:
                for evt in tgt_evts:
                    if evt.region:
                        target_tl.region = evt.region
                        break
            if target_tl.tenant_id is None:
                for evt in tgt_evts:
                    if evt.tenant_id:
                        target_tl.tenant_id = evt.tenant_id
                        break

            # Set rollout_id from events
            for evt in tgt_evts:
                if evt.rollout_id:
                    target_tl.rollout_id = evt.rollout_id
                    break

            targets.append(target_tl)

        timeline.targets = targets

        # Group events by wave_id → build FederationWaveTimeline list
        wave_events: dict[str, list[FederationHistoryEvent]] = defaultdict(list)
        for event in events:
            if event.wave_id:
                wave_events[event.wave_id].append(event)

        waves: list[FederationWaveTimeline] = []
        for wave_id, wv_evts in sorted(wave_events.items()):
            wave_tl = FederationWaveTimeline(wave_id=wave_id)
            wave_tl.events = wv_evts

            # Determine status from event types
            for evt in reversed(wv_evts):
                if evt.event_type == FederationHistoryEventType.WAVE_STARTED:
                    wave_tl.started_at = evt.created_at
                elif evt.event_type == FederationHistoryEventType.WAVE_SUCCEEDED:
                    wave_tl.status = "succeeded"
                    wave_tl.completed_at = evt.created_at
                elif evt.event_type == FederationHistoryEventType.WAVE_FAILED:
                    wave_tl.status = "failed"
                    wave_tl.completed_at = evt.created_at
                elif evt.event_type == FederationHistoryEventType.WAVE_BLOCKED:
                    wave_tl.status = "blocked"

            # Calculate duration
            if wave_tl.started_at and wave_tl.completed_at:
                wave_tl.duration_seconds = (
                    wave_tl.completed_at - wave_tl.started_at
                ).total_seconds()

            # Collect target_ids from events
            for evt in wv_evts:
                if evt.target_id and evt.target_id not in wave_tl.target_ids:
                    wave_tl.target_ids.append(evt.target_id)

            # Link target timelines
            wave_tl.target_timelines = [
                t for t in targets if t.target_id in wave_tl.target_ids
            ]

            waves.append(wave_tl)

        timeline.waves = waves

        # Compute federation-level timing
        for evt in events:
            if evt.event_type == FederationHistoryEventType.FEDERATION_STARTED:
                timeline.started_at = evt.created_at
            elif evt.event_type == FederationHistoryEventType.FEDERATION_COMPLETED:
                timeline.completed_at = evt.created_at
            elif evt.event_type == FederationHistoryEventType.FEDERATION_FAILED:
                timeline.completed_at = evt.created_at
            elif evt.event_type == FederationHistoryEventType.FEDERATION_CANCELLED:
                timeline.completed_at = evt.created_at

        if timeline.started_at and timeline.completed_at:
            timeline.duration_seconds = (
                timeline.completed_at - timeline.started_at
            ).total_seconds()

        # Collect conflicts
        for evt in events:
            if evt.event_type == FederationHistoryEventType.CONFLICT_DETECTED:
                timeline.conflicts.append({
                    "event_id": evt.history_event_id,
                    "message": evt.message,
                    "metadata": evt.metadata,
                    "created_at": evt.created_at.isoformat(),
                })

        return timeline

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    async def generate_report(
        self,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> FederationAnalyticsReport:
        """Generate a FederationAnalyticsReport from history events.

        Computes federation counts, target health, wave outcomes,
        conflict summaries, and environment/region/tenant summaries.
        """
        report = FederationAnalyticsReport(
            report_id=f"far_{uuid.uuid4().hex[:12]}",
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

        # Group by federation_id
        federation_events: dict[str, list[FederationHistoryEvent]] = defaultdict(list)
        for event in events:
            if event.federation_id:
                federation_events[event.federation_id].append(event)

        # Count federation statuses
        completed_ids: set[str] = set()
        failed_ids: set[str] = set()
        cancelled_ids: set[str] = set()
        blocked_ids: set[str] = set()
        active_ids: set[str] = set()

        for fid, fevts in federation_events.items():
            has_started = False
            has_terminal = False
            for evt in fevts:
                if evt.event_type == FederationHistoryEventType.FEDERATION_COMPLETED:
                    completed_ids.add(fid)
                    has_terminal = True
                elif evt.event_type == FederationHistoryEventType.FEDERATION_FAILED:
                    failed_ids.add(fid)
                    has_terminal = True
                elif evt.event_type == FederationHistoryEventType.FEDERATION_CANCELLED:
                    cancelled_ids.add(fid)
                    has_terminal = True
                elif evt.event_type == FederationHistoryEventType.FEDERATION_BLOCKED:
                    blocked_ids.add(fid)
                    has_terminal = True
                elif evt.event_type == FederationHistoryEventType.FEDERATION_STARTED:
                    has_started = True
            if has_started and not has_terminal:
                active_ids.add(fid)

        report.total_federations = len(federation_events)
        report.completed_federations = len(completed_ids)
        report.failed_federations = len(failed_ids)
        report.cancelled_federations = len(cancelled_ids)
        report.blocked_federations = len(blocked_ids)
        report.active_federations = len(active_ids)

        # Target health summary from TARGET_EXECUTION_* events
        target_health = FederationTargetHealthSummary()
        for evt in events:
            if evt.event_type == FederationHistoryEventType.TARGET_EXECUTION_STARTED:
                target_health.total_targets += 1
            elif evt.event_type == FederationHistoryEventType.TARGET_EXECUTION_SUCCEEDED:
                target_health.succeeded_targets += 1
            elif evt.event_type == FederationHistoryEventType.TARGET_EXECUTION_FAILED:
                target_health.failed_targets += 1
            elif evt.event_type == FederationHistoryEventType.TARGET_EXECUTION_BLOCKED:
                target_health.blocked_targets += 1
            elif evt.event_type == FederationHistoryEventType.TARGET_EXECUTION_SKIPPED:
                target_health.skipped_targets += 1
            elif evt.event_type == FederationHistoryEventType.TARGET_ENABLED:
                target_health.enabled_targets += 1
            elif evt.event_type == FederationHistoryEventType.TARGET_DISABLED:
                target_health.disabled_targets += 1
        report.target_health = target_health

        # Wave outcome summary from WAVE_* events
        wave_outcomes = FederationWaveOutcomeSummary()
        for evt in events:
            if evt.event_type == FederationHistoryEventType.WAVE_STARTED:
                wave_outcomes.total_waves += 1
                wave_outcomes.pending_waves += 1
            elif evt.event_type == FederationHistoryEventType.WAVE_SUCCEEDED:
                wave_outcomes.pending_waves -= 1
                wave_outcomes.succeeded_waves += 1
            elif evt.event_type == FederationHistoryEventType.WAVE_FAILED:
                wave_outcomes.pending_waves -= 1
                wave_outcomes.failed_waves += 1
            elif evt.event_type == FederationHistoryEventType.WAVE_BLOCKED:
                wave_outcomes.pending_waves -= 1
                wave_outcomes.blocked_waves += 1
        report.wave_outcomes = wave_outcomes

        # Conflict summary from CONFLICT_DETECTED events
        conflict_summary = FederationConflictSummary()
        for evt in events:
            if evt.event_type == FederationHistoryEventType.CONFLICT_DETECTED:
                conflict_summary.total_conflicts += 1
                severity = evt.metadata.get("severity", "")
                if severity == "error":
                    conflict_summary.error_conflicts += 1
                elif severity == "warning":
                    conflict_summary.warning_conflicts += 1
        report.conflicts = conflict_summary

        # Top failed targets
        failed_target_counts: dict[str, int] = defaultdict(int)
        for evt in events:
            if evt.event_type == FederationHistoryEventType.TARGET_EXECUTION_FAILED and evt.target_id:
                failed_target_counts[evt.target_id] += 1
        top_failed = sorted(failed_target_counts.items(), key=lambda x: (-x[1], x[0]))[:5]
        report.top_failed_targets = [
            {"target_id": tid, "count": count} for tid, count in top_failed
        ]

        # Top blocked targets
        blocked_target_counts: dict[str, int] = defaultdict(int)
        for evt in events:
            if evt.event_type == FederationHistoryEventType.TARGET_EXECUTION_BLOCKED and evt.target_id:
                blocked_target_counts[evt.target_id] += 1
        top_blocked = sorted(blocked_target_counts.items(), key=lambda x: (-x[1], x[0]))[:5]
        report.top_blocked_targets = [
            {"target_id": tid, "count": count} for tid, count in top_blocked
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

        # Region summary
        region_counts: dict[str, int] = defaultdict(int)
        for evt in events:
            if evt.region:
                region_counts[evt.region] += 1
        report.region_summary = [
            {"region": region, "event_count": count}
            for region, count in sorted(region_counts.items(), key=lambda x: (-x[1], x[0]))
        ]

        # Tenant summary
        tenant_counts: dict[str, int] = defaultdict(int)
        for evt in events:
            if evt.tenant_id:
                tenant_counts[evt.tenant_id] += 1
        report.tenant_summary = [
            {"tenant_id": tenant, "event_count": count}
            for tenant, count in sorted(tenant_counts.items(), key=lambda x: (-x[1], x[0]))
        ]

        # Approval summary enrichment from approval_store
        if self._approval_store is not None:
            try:
                approval_summary = await self._approval_store.get_dashboard_summary()
                report.metadata["approvals_pending_count"] = approval_summary.total_pending
                report.metadata["approvals_approved_count"] = approval_summary.total_approved
                report.metadata["approvals_rejected_count"] = approval_summary.total_rejected
                report.metadata["average_approval_latency_seconds"] = approval_summary.average_approval_latency_seconds
                report.metadata["approvals_by_tenant"] = approval_summary.by_tenant
                report.metadata["approvals_by_target"] = approval_summary.by_action
                report.metadata["escalated_approvals_count"] = approval_summary.total_escalated
                report.metadata["blocked_federation_actions_count"] = approval_summary.blocked_federation_actions
            except Exception:
                report.metadata["approvals_pending_count"] = 0
                report.metadata["approvals_approved_count"] = 0
                report.metadata["approvals_rejected_count"] = 0
                report.metadata["average_approval_latency_seconds"] = None
                report.metadata["approvals_by_tenant"] = {}
                report.metadata["approvals_by_target"] = {}
                report.metadata["escalated_approvals_count"] = 0
                report.metadata["blocked_federation_actions_count"] = 0
        else:
            report.metadata["approvals_pending_count"] = 0
            report.metadata["approvals_approved_count"] = 0
            report.metadata["approvals_rejected_count"] = 0
            report.metadata["average_approval_latency_seconds"] = None
            report.metadata["approvals_by_tenant"] = {}
            report.metadata["approvals_by_target"] = {}
            report.metadata["escalated_approvals_count"] = 0
            report.metadata["blocked_federation_actions_count"] = 0

        return report

    # ------------------------------------------------------------------
    # List events
    # ------------------------------------------------------------------

    async def list_history_events(
        self,
        federation_id: str | None = None,
        target_id: str | None = None,
        rollout_id: str | None = None,
        wave_id: str | None = None,
        event_type: FederationHistoryEventType | None = None,
        limit: int | None = None,
    ) -> list[FederationHistoryEvent]:
        """List history events from the store with optional filters."""
        if self._history_store is None:
            return []

        return await self._history_store.list(
            federation_id=federation_id,
            target_id=target_id,
            rollout_id=rollout_id,
            wave_id=wave_id,
            event_type=event_type,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # Approval summary
    # ------------------------------------------------------------------

    async def get_approval_summary(
        self, tenant_id: str | None = None
    ) -> FederationApprovalDashboardSummary:
        """Return a FederationApprovalDashboardSummary from the approval store.

        If no approval_store is configured, returns an empty summary.
        """
        if self._approval_store is None:
            return FederationApprovalDashboardSummary()
        return await self._approval_store.get_dashboard_summary(tenant_id=tenant_id)
