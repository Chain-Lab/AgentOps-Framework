"""Alert delivery retry daemon.

Phase 55 Task 4: Retry daemon for automatic alert delivery retry.
Phase 56 Task 730: Priority queue store integration.
Phase 57 Task 3: Deep priority queue integration + persistent state store.
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_service import (
    AlertDeliveryRetryRunResult,
    NotificationAlertDeliveryService,
    AlertDeliveryAdapterResult,
)
from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryRetryPolicy,
)
from agent_app.governance.policy_change_event import PolicyChangeEventType
from agent_app.runtime.policy_rollout_federation_notification_alert_priority_queue_store import (
    AlertPriorityQueueItem,
    AlertPriorityQueueStore,
    AlertPriorityQueueItemStatus,
)
from agent_app.runtime.policy_rollout_federation_notification_retry_daemon_state import (
    AlertDeliveryRetryDaemonState,
    AlertDeliveryRetryDaemonStateStore,
    create_retry_daemon_state_store,
    _redact_error_message,
)


# ---------------------------------------------------------------------------
# Extended result model
# ---------------------------------------------------------------------------


class AlertDeliveryRetryDaemonRunResult(BaseModel):
    """Extended result from a single retry daemon run.

    Phase 57: Adds priority queue counters and worker identification.
    """

    dry_run: bool
    scanned: int = 0
    delivered: int = 0
    retry_scheduled: int = 0
    dlq: int = 0
    failed: int = 0
    attempt_ids: list[str] = Field(default_factory=list)
    # Phase 57: priority queue counters
    queue_claimed: int = 0
    queue_completed: int = 0
    queue_failed: int = 0
    queue_requeued: int = 0
    fallback_processed: int = 0
    worker_id: str | None = None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class AlertDeliveryRetryDaemonConfig(BaseModel):
    """Configuration for the alert delivery retry daemon."""

    enabled: bool = False
    interval_seconds: float = 60.0
    jitter_seconds: float = 5.0
    batch_limit: int = 100
    stop_on_error: bool = False
    run_immediately: bool = True
    # Phase 57 additions
    daemon_id: str = "default"
    worker_id: str | None = None
    claim_lease_seconds: int = 300
    reset_expired_leases_on_run: bool = True
    # Phase 57: daemon state store config
    state_store: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------


class AlertDeliveryRetryDaemon:
    """Automatic retry daemon for alert delivery.

    Phase 57: Deep priority queue integration with atomic claim/ack/fail/requeue.
    """

    def __init__(
        self,
        scheduler: NotificationAlertDeliveryService,
        config: AlertDeliveryRetryDaemonConfig | None = None,
        audit_logger: Any | None = None,
        change_event_store: Any | None = None,
        priority_queue_store: AlertPriorityQueueStore | None = None,
        daemon_state_store: AlertDeliveryRetryDaemonStateStore | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._config = config or AlertDeliveryRetryDaemonConfig()
        self._audit_logger = audit_logger
        self._change_event_store = change_event_store
        self._priority_queue_store = priority_queue_store
        self._daemon_state_store = daemon_state_store
        self._task: asyncio.Task | None = None
        self._running = False
        self._lock = asyncio.Lock()
        self._started_at: datetime | None = None
        self._last_run_at: datetime | None = None
        self._last_error: str | None = None
        self._consecutive_failures: int = 0

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

    def _get_daemon_state(self) -> AlertDeliveryRetryDaemonState | None:
        """Get persisted daemon state, or None if no store configured."""
        if self._daemon_state_store is None:
            return None
        return self._daemon_state_store.get(self._config.daemon_id)

    def _save_daemon_state(self, **kwargs: Any) -> None:
        """Save daemon state to persistent store."""
        if self._daemon_state_store is None:
            return
        try:
            state = self._daemon_state_store.get(self._config.daemon_id)
            if state is None:
                state = AlertDeliveryRetryDaemonState(
                    daemon_id=self._config.daemon_id,
                    enabled=self._config.enabled,
                )
            for key, value in kwargs.items():
                if hasattr(state, key):
                    setattr(state, key, value)
            self._daemon_state_store.save(state)
        except Exception:  # noqa: BLE001 — best-effort
            pass

    @property
    def is_running(self) -> bool:
        """Whether the daemon loop is currently running."""
        return self._running and self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Start the daemon loop. Idempotent."""
        async with self._lock:
            if self.is_running:
                return
            self._running = True
            self._started_at = datetime.now(timezone.utc)
            self._task = asyncio.create_task(self._loop())
            self._save_daemon_state(
                desired_state="running",
                actual_state="running",
                started_at=self._started_at,
                enabled=self._config.enabled,
            )
            self._record_change_event(
                event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_RETRY_DAEMON_STARTED,
                payload={"interval_seconds": self._config.interval_seconds},
            )
            if self._audit_logger:
                try:
                    self._audit_logger(
                        "retry_daemon_started",
                        {"interval_seconds": self._config.interval_seconds},
                    )
                except Exception:
                    pass

    async def stop(self) -> None:
        """Stop the daemon loop. Idempotent."""
        async with self._lock:
            if not self.is_running:
                self._running = False
                self._save_daemon_state(
                    desired_state="stopped",
                    actual_state="stopped",
                    stopped_at=datetime.now(timezone.utc),
                )
                return
            self._running = False
            if self._task is not None:
                try:
                    self._task.cancel()
                    await self._task
                except (asyncio.CancelledError, RuntimeError):
                    # RuntimeError: event loop is closed (e.g., when stop()
                    # is called from a different event loop than the one
                    # that created the background task).
                    pass
                self._task = None
            self._last_run_at = None
            self._last_error = None
            self._consecutive_failures = 0
            self._save_daemon_state(
                desired_state="stopped",
                actual_state="stopped",
                stopped_at=datetime.now(timezone.utc),
                last_error_message=None,
                consecutive_failures=0,
            )
            self._record_change_event(
                event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_RETRY_DAEMON_STOPPED,
                payload={},
            )
            if self._audit_logger:
                try:
                    self._audit_logger("retry_daemon_stopped", {})
                except Exception:
                    pass

    async def run_once(self, dry_run: bool = False) -> AlertDeliveryRetryDaemonRunResult:
        """Execute a single retry scheduler run with priority queue integration.

        Phase 57: Priority queue first, then fallback to delivery service.
        """
        result = AlertDeliveryRetryDaemonRunResult(dry_run=dry_run, worker_id=self._config.worker_id)

        try:
            # Step 1: Reset expired leases
            if (
                self._priority_queue_store is not None
                and self._config.reset_expired_leases_on_run
            ):
                try:
                    await self._priority_queue_store.reset_expired_leases()
                except Exception:
                    pass

            # Step 2: Claim priority queue items
            claimed_items: list[AlertPriorityQueueItem] = []
            if self._priority_queue_store is not None:
                try:
                    claimed_items = await self._priority_queue_store.claim_next(
                        now=datetime.now(timezone.utc),
                        limit=self._config.batch_limit,
                        worker_id=self._config.worker_id,
                        lease_seconds=self._config.claim_lease_seconds,
                    )
                except Exception:
                    pass

            result.queue_claimed = len(claimed_items)

            # Step 3: Process claimed queue items
            remaining_budget = self._config.batch_limit - len(claimed_items)
            for item in claimed_items:
                try:
                    # Look up the actual attempt via the store
                    attempt = await self._scheduler._store.get_attempt(item.attempt_id)
                    if attempt is None:
                        # Missing attempt — fail the queue item
                        if self._priority_queue_store is not None:
                            await self._priority_queue_store.fail(
                                item.attempt_id,
                                error=f"Attempt {item.attempt_id} not found",
                                worker_id=self._config.worker_id,
                            )
                        result.queue_failed += 1
                        result.attempt_ids.append(item.attempt_id)
                        continue

                    # Get target and adapter
                    target = await self._scheduler._store.get_target(item.target_id)
                    if target is None:
                        if self._priority_queue_store is not None:
                            await self._priority_queue_store.fail(
                                item.attempt_id,
                                error=f"Target {item.target_id} not found",
                                worker_id=self._config.worker_id,
                            )
                        result.queue_failed += 1
                        result.attempt_ids.append(item.attempt_id)
                        continue

                    adapter = self._scheduler._adapters.get(target.channel_type.value)
                    if adapter is None:
                        if self._priority_queue_store is not None:
                            await self._priority_queue_store.fail(
                                item.attempt_id,
                                error=f"No adapter for {target.channel_type.value}",
                                worker_id=self._config.worker_id,
                            )
                        result.queue_failed += 1
                        result.attempt_ids.append(item.attempt_id)
                        continue

                    if dry_run:
                        if self._priority_queue_store is not None:
                            await self._priority_queue_store.acknowledge(
                                item.attempt_id,
                                worker_id=self._config.worker_id,
                            )
                        result.queue_completed += 1
                        result.attempt_ids.append(item.attempt_id)
                        continue

                    # Deliver
                    payload = {"alert_id": item.alert_id, "retry_of": item.attempt_id}
                    try:
                        adapter_result = adapter.deliver(target, None, payload)  # type: ignore
                    except Exception as exc:
                        adapter_result = AlertDeliveryAdapterResult(
                            success=False,
                            error_code="ADAPTER_ERROR",
                            error_message=str(exc),
                            retryable=True,
                        )

                    if adapter_result.success:
                        if self._priority_queue_store is not None:
                            await self._priority_queue_store.acknowledge(
                                item.attempt_id,
                                worker_id=self._config.worker_id,
                            )
                        result.queue_completed += 1
                    elif adapter_result.retryable:
                        if self._priority_queue_store is not None:
                            await self._priority_queue_store.requeue(
                                item.attempt_id,
                                reason=adapter_result.error_message,
                            )
                        result.queue_requeued += 1
                    else:
                        if self._priority_queue_store is not None:
                            await self._priority_queue_store.fail(
                                item.attempt_id,
                                error=adapter_result.error_message,
                                worker_id=self._config.worker_id,
                            )
                        result.queue_failed += 1

                    result.attempt_ids.append(item.attempt_id)

                except Exception:
                    result.queue_failed += 1
                    result.attempt_ids.append(item.attempt_id)

            # Step 4: Fallback to delivery service for remaining budget
            if remaining_budget > 0:
                try:
                    fallback_result = await self._scheduler.run_once(
                        limit=remaining_budget,
                        dry_run=dry_run,
                    )
                    result.fallback_processed = fallback_result.scanned
                    result.delivered += fallback_result.delivered
                    result.retry_scheduled += fallback_result.retry_scheduled
                    result.dlq += fallback_result.dlq
                    result.failed += fallback_result.failed
                    result.scanned += fallback_result.scanned
                    result.attempt_ids.extend(fallback_result.attempt_ids)
                except Exception:
                    pass

        except Exception as exc:
            self._last_error = str(exc)
            self._consecutive_failures += 1
            self._record_change_event(
                event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_RETRY_DAEMON_RUN_FAILED,
                payload={"error": str(exc)},
            )
            if self._audit_logger:
                try:
                    self._audit_logger(
                        "retry_daemon_run_error",
                        {"error": str(exc)},
                    )
                except Exception:
                    pass
            if self._config.stop_on_error:
                await self.stop()
            self._save_daemon_state(
                last_error_at=datetime.now(timezone.utc),
                last_error_message=str(exc),
                consecutive_failures=self._consecutive_failures,
                actual_state="error",
            )
            raise

        self._last_run_at = datetime.now(timezone.utc)
        self._consecutive_failures = 0

        self._record_change_event(
            event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_RETRY_DAEMON_RUN_COMPLETED,
            payload={
                "dry_run": dry_run,
                "queue_claimed": result.queue_claimed,
                "queue_completed": result.queue_completed,
                "queue_failed": result.queue_failed,
                "queue_requeued": result.queue_requeued,
                "fallback_processed": result.fallback_processed,
            },
        )
        if self._audit_logger:
            try:
                self._audit_logger(
                    "retry_daemon_run_completed",
                    {
                        "dry_run": dry_run,
                        "queue_claimed": result.queue_claimed,
                        "queue_completed": result.queue_completed,
                        "queue_failed": result.queue_failed,
                        "queue_requeued": result.queue_requeued,
                        "fallback_processed": result.fallback_processed,
                    },
                )
            except Exception:
                pass

        # Persist success state
        self._save_daemon_state(
            last_run_at=self._last_run_at,
            last_success_at=self._last_run_at,
            consecutive_failures=0,
            last_error_message=None,
            actual_state="running" if self.is_running else "stopped",
            last_result={
                "queue_claimed": result.queue_claimed,
                "queue_completed": result.queue_completed,
                "queue_failed": result.queue_failed,
                "queue_requeued": result.queue_requeued,
                "fallback_processed": result.fallback_processed,
            },
        )

        return result

    def get_health_status(self) -> dict[str, Any]:
        """Return current daemon health status.

        Phase 57: Combines in-memory state with persisted state for restart visibility.
        """
        # Start with persisted state if available
        persisted = self._get_daemon_state()
        if persisted is not None and not self.is_running:
            # After restart, use persisted state for visibility
            return {
                "state": persisted.actual_state,
                "consecutive_failures": persisted.consecutive_failures,
                "last_error": _redact_error_message(persisted.last_error_message) if persisted.last_error_message else None,
                "started_at": persisted.started_at.isoformat() if persisted.started_at else None,
                "last_run_at": persisted.last_run_at.isoformat() if persisted.last_run_at else None,
                "last_success_at": persisted.last_success_at.isoformat() if persisted.last_success_at else None,
                "interval_seconds": self._config.interval_seconds,
                "source": "persisted",
            }

        # In-memory state when running
        if not self.is_running:
            state = "stopped"
        elif self._consecutive_failures == 0:
            state = "healthy"
        elif self._consecutive_failures <= 2:
            state = "degraded"
        else:
            state = "unhealthy"

        return {
            "state": state,
            "consecutive_failures": self._consecutive_failures,
            "last_error": _redact_error_message(self._last_error) if self._last_error else None,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_run_at": self._last_run_at.isoformat() if self._last_run_at else None,
            "interval_seconds": self._config.interval_seconds,
            "source": "memory",
        }

    async def _loop(self) -> None:
        """Internal loop — runs run_once at the configured interval."""
        if self._config.run_immediately:
            try:
                await self.run_once()
            except Exception:
                if self._config.stop_on_error:
                    return

        while self._running:
            # Calculate sleep with jitter
            jitter = random.uniform(0, self._config.jitter_seconds)
            sleep_time = self._config.interval_seconds + jitter

            try:
                await asyncio.sleep(sleep_time)
            except asyncio.CancelledError:
                break

            if not self._running:
                break

            try:
                await self.run_once()
            except Exception:
                if self._config.stop_on_error:
                    break
