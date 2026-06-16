"""Policy observability service — aggregates audit events into analytics reports.

Phase 39: Framework-level governance visibility.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from agent_app.governance.policy_observability import (
    ApprovalLatencySummary,
    PolicyActionSummary,
    PolicyActorSummary,
    PolicyDecisionCount,
    PolicyObservabilityReport,
    PolicyToolSummary,
)

# Audit event types for runtime enforcement
_ENFORCEMENT_EVENT_TYPES = {
    "policy.runtime.enforcement.allowed": "allowed",
    "policy.runtime.enforcement.denied": "denied",
    "policy.runtime.enforcement.approval_required": "approval_required",
}


class PolicyObservabilityService:
    """Aggregates audit events and store data into governance analytics reports."""

    def __init__(
        self,
        audit_logger: Any | None = None,
        event_store: Any | None = None,
        rollout_store: Any | None = None,
        rollout_approval_store: Any | None = None,
        runtime_policy_store: Any | None = None,
    ) -> None:
        self._audit_logger = audit_logger
        self._event_store = event_store
        self._rollout_store = rollout_store
        self._rollout_approval_store = rollout_approval_store
        self._runtime_policy_store = runtime_policy_store

    async def generate_report(
        self,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> PolicyObservabilityReport:
        """Generate a full governance analytics report."""
        actions = await self.summarize_enforcement_decisions(window_start, window_end)
        actors = await self.summarize_actors(window_start, window_end)
        tools = await self.summarize_tools(window_start, window_end)
        approval_latency = await self.approval_latency_summary(window_start, window_end)

        # Compute totals
        total = sum(a.total for a in actions)
        status_counts: dict[str, int] = defaultdict(int)
        for a in actions:
            status_counts["allowed"] += a.allowed
            status_counts["denied"] += a.denied
            status_counts["approval_required"] += a.approval_required
        decisions_by_status = [
            PolicyDecisionCount(status=k, count=v)
            for k, v in sorted(status_counts.items())
            if v > 0
        ]

        # Top denials
        top_denials = await self._top_denials(window_start, window_end)

        return PolicyObservabilityReport(
            report_id=f"por_{uuid.uuid4().hex[:12]}",
            generated_at=datetime.now(timezone.utc),
            window_start=window_start,
            window_end=window_end,
            total_decisions=total,
            decisions_by_status=decisions_by_status,
            actions=actions,
            actors=actors,
            tools=tools,
            approval_latency=approval_latency,
            top_denials=top_denials,
        )

    async def summarize_enforcement_decisions(
        self,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> list[PolicyActionSummary]:
        """Summarize enforcement decisions by action type."""
        events = await self._get_enforcement_events(window_start, window_end)
        buckets: dict[str, dict[str, int]] = defaultdict(
            lambda: {"allowed": 0, "denied": 0, "approval_required": 0, "total": 0}
        )

        for event in events:
            status = _ENFORCEMENT_EVENT_TYPES.get(event.event_type)
            if status is None:
                continue
            action_type = event.data.get("action_type", "unknown")
            buckets[action_type][status] += 1
            buckets[action_type]["total"] += 1

        return [
            PolicyActionSummary(action_type=k, **v)
            for k, v in sorted(buckets.items())
        ]

    async def summarize_actors(
        self,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> list[PolicyActorSummary]:
        """Summarize enforcement decisions by actor."""
        events = await self._get_enforcement_events(window_start, window_end)
        buckets: dict[str, dict[str, int]] = defaultdict(
            lambda: {"allowed": 0, "denied": 0, "approval_required": 0, "total": 0}
        )

        for event in events:
            status = _ENFORCEMENT_EVENT_TYPES.get(event.event_type)
            if status is None:
                continue
            actor_id = event.user_id or event.data.get("user_id", "unknown")
            buckets[actor_id][status] += 1
            buckets[actor_id]["total"] += 1

        return [
            PolicyActorSummary(actor_id=k, **v)
            for k, v in sorted(buckets.items())
        ]

    async def summarize_tools(
        self,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> list[PolicyToolSummary]:
        """Summarize enforcement decisions by tool."""
        events = await self._get_enforcement_events(window_start, window_end)
        buckets: dict[str, dict[str, int]] = defaultdict(
            lambda: {"allowed": 0, "denied": 0, "approval_required": 0, "total": 0}
        )

        for event in events:
            status = _ENFORCEMENT_EVENT_TYPES.get(event.event_type)
            if status is None:
                continue
            tool_name = event.tool_name or event.data.get("tool_name", "unknown")
            buckets[tool_name][status] += 1
            buckets[tool_name]["total"] += 1

        return [
            PolicyToolSummary(tool_name=k, **v)
            for k, v in sorted(buckets.items())
        ]

    async def approval_latency_summary(
        self,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> ApprovalLatencySummary:
        """Compute approval resolution latency from rollout approval store."""
        if self._rollout_approval_store is None:
            return ApprovalLatencySummary(count=0)

        try:
            approvals = await self._rollout_approval_store.list()
        except Exception:
            return ApprovalLatencySummary(count=0)

        # Filter to resolved approvals
        resolved = [
            a
            for a in approvals
            if a.resolved_at is not None and a.created_at is not None
        ]

        # Apply window filter
        if window_start is not None:
            resolved = [a for a in resolved if a.created_at >= window_start]
        if window_end is not None:
            resolved = [a for a in resolved if a.created_at <= window_end]

        if not resolved:
            return ApprovalLatencySummary(count=0)

        latencies = []
        for a in resolved:
            delta = (a.resolved_at - a.created_at).total_seconds()
            latencies.append(delta)

        return ApprovalLatencySummary(
            count=len(latencies),
            average_seconds=round(sum(latencies) / len(latencies), 2),
            min_seconds=round(min(latencies), 2),
            max_seconds=round(max(latencies), 2),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_enforcement_events(
        self,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> list:
        """Get enforcement audit events, filtered by window."""
        if self._audit_logger is None:
            return []

        try:
            all_events = []
            for event_type in _ENFORCEMENT_EVENT_TYPES:
                events = self._audit_logger.list_events(event_type=event_type)
                all_events.extend(events)
        except Exception:
            return []

        # Apply window filter
        if window_start is not None:
            all_events = [e for e in all_events if e.created_at >= window_start]
        if window_end is not None:
            all_events = [e for e in all_events if e.created_at <= window_end]

        return sorted(all_events, key=lambda e: e.created_at)

    async def _top_denials(
        self,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get top denial reasons."""
        events = await self._get_enforcement_events(window_start, window_end)
        denied = [
            e
            for e in events
            if e.event_type == "policy.runtime.enforcement.denied"
        ]

        # Group by reason
        reason_counts: dict[str, int] = defaultdict(int)
        for e in denied:
            reason = e.data.get("reason", "unknown")
            reason_counts[reason] += 1

        # Sort by count descending
        sorted_reasons = sorted(
            reason_counts.items(), key=lambda x: x[1], reverse=True
        )[:limit]

        return [{"reason": r, "count": c} for r, c in sorted_reasons]
