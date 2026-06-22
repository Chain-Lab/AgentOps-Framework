"""SLA evaluation service — evaluates notification delivery metrics against SLA policies.

Phase 52 Task 3: SLA policy and evaluation service.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from agent_app.governance.policy_rollout_federation_notification_observability import (
    NotificationMetricWindow,
)
from agent_app.governance.policy_rollout_federation_notification_sla import (
    NotificationChannelSlaOverride,
    NotificationSlaPolicy,
    NotificationSlaViolation,
)


class NotificationSlaService:
    """Evaluates notification delivery metrics against SLA policy thresholds.

    Produces a list of NotificationSlaViolation records for any metric
    that exceeds its configured threshold, with severity determined by
    how far the observed value exceeds the threshold.
    """

    def __init__(
        self,
        observability_store: Any,  # NotificationObservabilityStore (Protocol)
        sla_policy: NotificationSlaPolicy | None = None,
    ) -> None:
        self._observability_store = observability_store
        self._sla_policy = sla_policy or NotificationSlaPolicy()

    async def evaluate(
        self,
        federation_id: str | None = None,
        channel: str | None = None,
        now: datetime | None = None,
    ) -> list[NotificationSlaViolation]:
        """Evaluate SLA compliance for the given federation/channel scope.

        Args:
            federation_id: Optional federation ID to filter metrics.
            channel: Optional channel to filter metrics (also used for override lookup).
            now: Optional current time for window calculation (defaults to UTC now).

        Returns:
            List of NotificationSlaViolation for any breached thresholds.
            Empty list if policy is disabled, no data, or all metrics pass.
        """
        if not self._sla_policy.enabled:
            return []

        # Determine effective window (policy default or channel override)
        window_minutes = self._sla_policy.window_minutes
        if channel and channel in self._sla_policy.channels:
            override = self._sla_policy.channels[channel]
            if override.window_minutes is not None:
                window_minutes = override.window_minutes

        # Fetch metrics from observability store
        metrics = await self._observability_store.aggregate_metrics(
            federation_id=federation_id,
            channel=channel,
            window_minutes=window_minutes,
            now=now,
        )

        # No data means no violations
        if metrics.total == 0:
            return []

        # Resolve effective thresholds (channel override takes precedence)
        effective = self._resolve_thresholds(channel)

        violations: list[NotificationSlaViolation] = []
        violations.extend(self._check_latency(metrics, effective))
        violations.extend(self._check_success_rate(metrics, effective))
        violations.extend(self._check_failure_rate(metrics, effective))
        violations.extend(self._check_dlq_rate(metrics, effective))

        return violations

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_thresholds(self, channel: str | None) -> dict[str, float | int]:
        """Resolve effective threshold values, applying channel overrides."""
        override: NotificationChannelSlaOverride | None = None
        if channel and channel in self._sla_policy.channels:
            override = self._sla_policy.channels[channel]

        return {
            "max_delivery_latency_ms": (
                override.max_delivery_latency_ms
                if override is not None and override.max_delivery_latency_ms is not None
                else self._sla_policy.max_delivery_latency_ms
            ),
            "min_success_rate": (
                override.min_success_rate
                if override is not None and override.min_success_rate is not None
                else self._sla_policy.min_success_rate
            ),
            "max_failure_rate": (
                override.max_failure_rate
                if override is not None and override.max_failure_rate is not None
                else self._sla_policy.max_failure_rate
            ),
            "max_dlq_rate": (
                override.max_dlq_rate
                if override is not None and override.max_dlq_rate is not None
                else self._sla_policy.max_dlq_rate
            ),
        }

    def _check_latency(
        self, metrics: NotificationMetricWindow, effective: dict[str, float | int]
    ) -> list[NotificationSlaViolation]:
        """Check avg_latency_ms against threshold."""
        if metrics.avg_latency_ms is None:
            return []

        threshold = effective["max_delivery_latency_ms"]
        observed = metrics.avg_latency_ms
        if observed <= threshold:
            return []

        severity = "critical" if observed > (2 * threshold) else "warning"
        return [self._make_violation(
            metric="avg_latency_ms",
            observed_value=observed,
            threshold=float(threshold),
            severity=severity,
            metrics=metrics,
            channel=metrics.channel,
            federation_id=metrics.federation_id,
        )]

    def _check_success_rate(
        self, metrics: NotificationMetricWindow, effective: dict[str, float | int]
    ) -> list[NotificationSlaViolation]:
        """Check success_rate against threshold."""
        threshold = effective["min_success_rate"]
        observed = metrics.success_rate
        if observed >= threshold:
            return []

        severity = "critical" if observed < (threshold * 0.5) else "warning"
        return [self._make_violation(
            metric="success_rate",
            observed_value=observed,
            threshold=threshold,
            severity=severity,
            metrics=metrics,
            channel=metrics.channel,
            federation_id=metrics.federation_id,
        )]

    def _check_failure_rate(
        self, metrics: NotificationMetricWindow, effective: dict[str, float | int]
    ) -> list[NotificationSlaViolation]:
        """Check failure_rate against threshold."""
        threshold = effective["max_failure_rate"]
        observed = metrics.failure_rate
        if observed <= threshold:
            return []

        severity = "critical" if observed > (2 * threshold) else "warning"
        return [self._make_violation(
            metric="failure_rate",
            observed_value=observed,
            threshold=threshold,
            severity=severity,
            metrics=metrics,
            channel=metrics.channel,
            federation_id=metrics.federation_id,
        )]

    def _check_dlq_rate(
        self, metrics: NotificationMetricWindow, effective: dict[str, float | int]
    ) -> list[NotificationSlaViolation]:
        """Check dlq_rate against threshold."""
        threshold = effective["max_dlq_rate"]
        observed = metrics.dlq_rate
        if observed <= threshold:
            return []

        severity = "critical" if observed > (2 * threshold) else "warning"
        return [self._make_violation(
            metric="dlq_rate",
            observed_value=observed,
            threshold=threshold,
            severity=severity,
            metrics=metrics,
            channel=metrics.channel,
            federation_id=metrics.federation_id,
        )]

    def _make_violation(
        self,
        *,
        metric: str,
        observed_value: float,
        threshold: float,
        severity: str,
        metrics: NotificationMetricWindow,
        channel: str | None,
        federation_id: str | None,
    ) -> NotificationSlaViolation:
        """Create a NotificationSlaViolation with a descriptive message."""
        violation_id = f"nsv_{uuid.uuid4().hex[:12]}"
        channel_label = channel or "unknown"

        # Build descriptive message
        if metric == "avg_latency_ms":
            message = (
                f"Channel {channel_label} avg_latency_ms {observed_value:.0f} "
                f"exceeds threshold {threshold:.0f}"
            )
        elif metric == "success_rate":
            message = (
                f"Channel {channel_label} success_rate {observed_value:.4f} "
                f"below threshold {threshold:.4f}"
            )
        elif metric == "failure_rate":
            message = (
                f"Channel {channel_label} failure_rate {observed_value:.4f} "
                f"exceeds threshold {threshold:.4f}"
            )
        elif metric == "dlq_rate":
            message = (
                f"Channel {channel_label} dlq_rate {observed_value:.4f} "
                f"exceeds threshold {threshold:.4f}"
            )
        else:
            message = (
                f"Channel {channel_label} {metric} {observed_value} "
                f"breaches threshold {threshold}"
            )

        now = datetime.now(timezone.utc)
        return NotificationSlaViolation(
            violation_id=violation_id,
            federation_id=federation_id,
            channel=channel,
            metric=metric,
            observed_value=observed_value,
            threshold=threshold,
            severity=severity,
            window_start=metrics.window_start,
            window_end=metrics.window_end,
            message=message,
            created_at=now,
        )
