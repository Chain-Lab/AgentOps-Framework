"""Alert delivery service — match targets, build payloads, call adapters, record attempts, retry.

Phase 53 Task 3: Alert delivery service.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from pydantic import BaseModel, Field

from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryAttempt,
    AlertDeliveryChannelType,
    AlertDeliveryRetryPolicy,
    AlertDeliveryStatus,
    AlertDeliveryTarget,
)
from agent_app.governance.policy_rollout_federation_notification_observability import (
    NotificationAlertEvent,
)


class AlertDeliveryAdapterResult(BaseModel):
    """Result from a delivery adapter."""

    success: bool
    error_code: str | None = None
    error_message: str | None = None
    response_metadata: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False


class NotificationAlertDeliveryService:
    """Delivers alert events to configured targets via adapters."""

    def __init__(
        self,
        store: Any,
        adapters: dict[str, Any],
        retry_policy: AlertDeliveryRetryPolicy | None = None,
    ) -> None:
        self._store = store
        self._adapters = adapters
        self._retry_policy = retry_policy or AlertDeliveryRetryPolicy()

    async def deliver_alert(
        self,
        alert: NotificationAlertEvent,
        dry_run: bool = False,
        now: datetime | None = None,
    ) -> list[AlertDeliveryAttempt]:
        """Deliver an alert to all matching enabled targets."""
        if now is None:
            now = datetime.now(timezone.utc)

        targets = await self._store.list_targets(enabled=True)
        attempts: list[AlertDeliveryAttempt] = []

        for target in targets:
            if not self._match_target(target, alert):
                continue

            adapter = self._adapters.get(target.channel_type.value)
            if adapter is None:
                continue

            payload = self._build_payload(alert, target)

            if dry_run:
                attempt = AlertDeliveryAttempt(
                    attempt_id=f"nda_dryrun_{target.target_id}_{alert.alert_id}",
                    alert_id=alert.alert_id,
                    target_id=target.target_id,
                    channel_type=target.channel_type,
                    status=AlertDeliveryStatus.SUPPRESSED,
                    attempt=1,
                    payload_preview=payload,
                    created_at=now,
                )
                await self._store.record_attempt(attempt)
                attempts.append(attempt)
                continue

            try:
                result = adapter.deliver(target, alert, payload)
            except Exception as exc:
                result = AlertDeliveryAdapterResult(
                    success=False,
                    error_code="ADAPTER_ERROR",
                    error_message=str(exc),
                    retryable=True,
                )

            recorded = await self._record_attempt_result(
                target, alert, result, now, attempt_num=1,
            )
            attempts.append(recorded)

        return attempts

    async def retry_failed(
        self,
        now: datetime | None = None,
        limit: int = 100,
    ) -> list[AlertDeliveryAttempt]:
        """Retry RETRY_SCHEDULED attempts past their next_retry_at."""
        if now is None:
            now = datetime.now(timezone.utc)

        due_attempts = await self._store.list_attempts(
            status=AlertDeliveryStatus.RETRY_SCHEDULED, limit=limit,
        )

        retried: list[AlertDeliveryAttempt] = []
        for attempt in due_attempts:
            if attempt.next_retry_at is not None and attempt.next_retry_at > now:
                continue

            target = await self._store.get_target(attempt.target_id)
            if target is None:
                continue

            adapter = self._adapters.get(target.channel_type.value)
            if adapter is None:
                continue

            payload = {"alert_id": attempt.alert_id, "retry_of": attempt.attempt_id}

            try:
                result = adapter.deliver(target, None, payload)  # type: ignore
            except Exception as exc:
                result = AlertDeliveryAdapterResult(
                    success=False, error_code="ADAPTER_ERROR",
                    error_message=str(exc), retryable=True,
                )

            new_attempt = await self._record_attempt_result(
                target, None, result, now, attempt_num=attempt.attempt + 1,
            )
            retried.append(new_attempt)

        return retried

    def _match_target(self, target: AlertDeliveryTarget, alert: NotificationAlertEvent) -> bool:
        """Check if a target matches an alert."""
        if target.severity_filter and alert.severity not in target.severity_filter:
            return False
        if target.channel_filter and alert.channel is not None and alert.channel not in target.channel_filter:
            return False
        if target.federation_filter and alert.federation_id is not None and alert.federation_id not in target.federation_filter:
            return False
        return True

    def _build_payload(self, alert: NotificationAlertEvent, target: AlertDeliveryTarget) -> dict[str, Any]:
        """Build a payload dict for delivery."""
        return {
            "alert_id": alert.alert_id,
            "rule_id": alert.rule_id,
            "name": alert.name,
            "severity": alert.severity,
            "metric": alert.metric,
            "observed_value": alert.observed_value,
            "threshold": alert.threshold,
            "channel": alert.channel or "",
            "federation_id": alert.federation_id or "",
            "message": alert.message,
            "status": alert.status,
            "created_at": alert.created_at.isoformat(),
        }

    async def _record_attempt_result(
        self,
        target: AlertDeliveryTarget,
        alert: NotificationAlertEvent | None,
        result: AlertDeliveryAdapterResult,
        now: datetime,
        attempt_num: int,
    ) -> AlertDeliveryAttempt:
        """Record the result of a delivery attempt."""
        if result.success:
            status = AlertDeliveryStatus.DELIVERED
        elif result.retryable and attempt_num < self._retry_policy.max_attempts:
            status = AlertDeliveryStatus.RETRY_SCHEDULED
            delay = min(
                self._retry_policy.base_delay_seconds * (2 ** (attempt_num - 1)),
                self._retry_policy.max_delay_seconds,
            )
            next_retry = now + timedelta(seconds=delay)
        else:
            status = AlertDeliveryStatus.DLQ
            next_retry = None

        payload = self._build_payload(alert, target) if alert else {}

        attempt = AlertDeliveryAttempt(
            attempt_id=f"nda_{target.target_id}_{alert.alert_id if alert else 'unknown'}_{attempt_num}",
            alert_id=alert.alert_id if alert else "unknown",
            target_id=target.target_id,
            channel_type=target.channel_type,
            status=status,
            attempt=attempt_num,
            next_retry_at=next_retry if status == AlertDeliveryStatus.RETRY_SCHEDULED else None,
            error_code=result.error_code,
            error_message=result.error_message,
            payload_preview=payload,
            created_at=now,
            delivered_at=now if status == AlertDeliveryStatus.DELIVERED else None,
        )
        return await self._store.record_attempt(attempt)
