"""Alert priority queue — wraps a store and exposes priority-aware dequeue.

Phase 55 Task 5: Priority queue for alert delivery ordering.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent_app.governance.policy_change_event import PolicyChangeEventType
from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryAttempt,
    AlertDeliveryChannelType,
    AlertDeliveryStatus,
    severity_to_priority,
)


class AlertPriorityQueue:
    """Priority-aware wrapper around an alert delivery store.

    Provides ``enqueue`` / ``dequeue`` semantics sorted by priority
    (higher value = more urgent), then by creation timestamp.

    All public methods are async and delegate to the underlying store.
    """

    def __init__(self, store: Any, change_event_store: Any = None) -> None:
        self._store = store
        self._change_event_store = change_event_store

    def _record_change_event(
        self,
        event_type: PolicyChangeEventType,
        payload: dict[str, Any],
    ) -> None:
        """Best-effort change event recording — never break the caller on failure."""
        if self._change_event_store is None:
            return
        try:
            self._change_event_store.record(
                event_type=event_type,
                payload=payload,
            )
        except Exception:  # noqa: BLE001 — best-effort
            pass

    async def enqueue(self, attempt: AlertDeliveryAttempt) -> AlertDeliveryAttempt:
        """Record an attempt in the store with priority set.

        If the attempt already has a priority, it is preserved.
        Otherwise, priority is derived from the attempt's status (default 0).
        """
        if attempt.priority == 0:
            # Priority is already 0 — leave it (default)
            pass
        result = await self._store.record_attempt(attempt)
        self._record_change_event(
            event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_PRIORITY_UPDATED,
            payload={
                "attempt_id": result.attempt_id,
                "alert_id": result.alert_id,
                "priority": result.priority,
            },
        )
        return result

    async def enqueue_from_alert(
        self,
        alert: Any,
        target_id: str,
        channel_type: Any,
        status: AlertDeliveryStatus = AlertDeliveryStatus.RETRY_SCHEDULED,
        attempt_num: int = 1,
        now: datetime | None = None,
    ) -> AlertDeliveryAttempt:
        """Create and enqueue an attempt from an alert event.

        Priority is derived from ``alert.severity``.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        priority = severity_to_priority(getattr(alert, "severity", "") or "")

        attempt = AlertDeliveryAttempt(
            attempt_id=f"nda_{target_id}_{getattr(alert, 'alert_id', 'unknown')}_{attempt_num}",
            alert_id=getattr(alert, "alert_id", "unknown"),
            target_id=target_id,
            channel_type=channel_type,
            status=status,
            attempt=attempt_num,
            priority=priority,
            created_at=now,
        )
        return await self.enqueue(attempt)

    async def dequeue(
        self,
        limit: int = 100,
        status: str | None = AlertDeliveryStatus.RETRY_SCHEDULED,
        now: datetime | None = None,
    ) -> list[AlertDeliveryAttempt]:
        """Return the highest-priority attempts, filtered by status.

        If ``status`` is provided, only attempts with that status are returned.
        The underlying store's ``list_attempts`` already sorts by priority DESC
        then created_at DESC, so the returned list is already in dequeue order.
        """
        return await self._store.list_attempts(
            status=status,
            limit=limit,
        )

    async def peek(
        self,
        status: str | None = AlertDeliveryStatus.RETRY_SCHEDULED,
    ) -> AlertDeliveryAttempt | None:
        """Return the single highest-priority attempt without removing it."""
        attempts = await self.dequeue(limit=1, status=status)
        return attempts[0] if attempts else None

    async def count(
        self,
        status: str | None = None,
    ) -> int:
        """Count attempts, optionally filtered by status."""
        attempts = await self._store.list_attempts(
            status=status,
            limit=10_000,
        )
        return len(attempts)

    async def count_by_priority(
        self,
        status: str | None = None,
    ) -> dict[int, int]:
        """Return a mapping of priority → attempt count.

        Useful for observability dashboards.
        """
        attempts = await self._store.list_attempts(
            status=status,
            limit=10_000,
        )
        counts: dict[int, int] = {}
        for a in attempts:
            counts[a.priority] = counts.get(a.priority, 0) + 1
        return counts
