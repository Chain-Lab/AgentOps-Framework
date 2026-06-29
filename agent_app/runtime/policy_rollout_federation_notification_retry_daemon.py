"""Alert delivery retry daemon.

Phase 55 Task 4: Retry daemon for automatic alert delivery retry.
Phase 56 Task 730: Priority queue store integration.
Phase 57 Task 3: Deep priority queue integration + persistent state store.
Phase 60 Task 750: Closed-loop integration with Phase 59 stores.
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
# Phase 60: Phase 59 closed-loop stores
from agent_app.runtime.policy_rollout_federation_notification_replay_idempotency import (
    ReplayIdempotencyStore,
    ReplayIdempotencyRecord,
)
from agent_app.runtime.policy_rollout_federation_notification_replay_rate_limiter import (
    ReplayRateLimiterStore,
)
from agent_app.runtime.policy_rollout_federation_notification_dead_letter_policy import (
    DeadLetterPolicyStore,
    DeadLetterPolicyResult,
)
from agent_app.runtime.policy_rollout_federation_notification_distributed_lock import (
    DistributedLockStore,
    DistributedLockStatus,
)
from agent_app.runtime.policy_rollout_federation_notification_webhook_key_rotation import (
    WebhookKeyRotationService,
)
from agent_app.runtime.policy_rollout_federation_notification_metrics_enhanced import (
    EnhancedMetrics,
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
    # Phase 60: closed-loop controls
    distributed_lock_enabled: bool = False
    lock_name: str = "notification-replay-daemon"
    lock_lease_seconds: int = 30
    lock_renew_interval_seconds: int = 10
    key_rotation_enabled: bool = False
    rate_limit_enabled: bool = False
    rate_limit_window_seconds: int = 60
    rate_limit_max_attempts: int = 10
    rate_limit_scope: str = "target"
    idempotency_enabled: bool = False
    idempotency_ttl_hours: int = 24
    dead_letter_enabled: bool = False
    dead_letter_max_retries: int = 5


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------


class AlertDeliveryRetryDaemon:
    """Automatic retry daemon for alert delivery.

    Phase 57: Deep priority queue integration with atomic claim/ack/fail/requeue.
    Phase 60: Closed-loop integration with Phase 59 stores (idempotency, rate limit,
    dead letter, distributed lock, key rotation, enhanced metrics).
    """

    def __init__(
        self,
        scheduler: NotificationAlertDeliveryService,
        config: AlertDeliveryRetryDaemonConfig | None = None,
        audit_logger: Any | None = None,
        change_event_store: Any | None = None,
        priority_queue_store: AlertPriorityQueueStore | None = None,
        daemon_state_store: AlertDeliveryRetryDaemonStateStore | None = None,
        # Phase 60: Phase 59 closed-loop stores
        idempotency_store: ReplayIdempotencyStore | None = None,
        rate_limiter_store: ReplayRateLimiterStore | None = None,
        dead_letter_policy_store: DeadLetterPolicyStore | None = None,
        distributed_lock_store: DistributedLockStore | None = None,
        key_rotation_service: WebhookKeyRotationService | None = None,
        enhanced_metrics: EnhancedMetrics | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._config = config or AlertDeliveryRetryDaemonConfig()
        self._audit_logger = audit_logger
        self._change_event_store = change_event_store
        self._priority_queue_store = priority_queue_store
        self._daemon_state_store = daemon_state_store
        # Phase 60: Phase 59 stores
        self._idempotency_store = idempotency_store
        self._rate_limiter_store = rate_limiter_store
        self._dead_letter_policy_store = dead_letter_policy_store
        self._distributed_lock_store = distributed_lock_store
        self._key_rotation_service = key_rotation_service
        self._enhanced_metrics = enhanced_metrics
        self._task: asyncio.Task | None = None
        self._running = False
        self._lock = asyncio.Lock()
        self._started_at: datetime | None = None
        self._last_run_at: datetime | None = None
        self._last_error: str | None = None
        self._consecutive_failures: int = 0
        # Phase 60: lock tracking
        self._lock_owner_id: str | None = None
        self._lock_fencing_token: int | None = None

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
        Phase 60: Closed-loop with distributed lock, rate limit, idempotency,
        dead letter policy, key rotation, and enhanced metrics.
        """
        result = AlertDeliveryRetryDaemonRunResult(dry_run=dry_run, worker_id=self._config.worker_id)
        lock_acquired = False

        try:
            # Phase 60: Distributed lock leader election
            if (
                self._distributed_lock_store is not None
                and self._config.distributed_lock_enabled
            ):
                try:
                    lock_status = self._distributed_lock_store.acquire(
                        lock_name=self._config.lock_name,
                        owner_id=self._config.worker_id or "default",
                        lease_seconds=self._config.lock_lease_seconds,
                    )
                    if lock_status.acquired:
                        lock_acquired = True
                        self._lock_owner_id = lock_status.owner_id
                        self._lock_fencing_token = lock_status.fencing_token
                        if self._enhanced_metrics:
                            self._enhanced_metrics.record_lock_acquire_success()
                    else:
                        if self._enhanced_metrics:
                            self._enhanced_metrics.record_lock_acquire_denied()
                        # Another instance holds the lock — skip this run
                        return result
                except Exception:
                    if self._enhanced_metrics:
                        self._enhanced_metrics.record_lock_acquire_exception()
                    # Proceed without lock on error (fail-open)
                    pass

            # Phase 60: Scheduled key rotation
            if (
                self._key_rotation_service is not None
                and self._config.key_rotation_enabled
            ):
                try:
                    if self._key_rotation_service.should_rotate():
                        new_key = self._key_rotation_service.generate_new_key()
                        if self._enhanced_metrics:
                            self._enhanced_metrics.record_replay_attempt()  # reuse replay counter for rotation events
                        self._record_change_event(
                            event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_WEBHOOK_KEY_ROTATED,
                            payload={"new_key_id": new_key.key_id, "reason": "scheduled"},
                        )
                except Exception:
                    pass  # Best-effort, never break daemon loop

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

            # Step 3: Process claimed queue items with closed-loop
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

                    # Phase 60: Closed-loop processing for each item
                    idempotency_key = f"replay:{item.attempt_id}:{item.target_id}:{item.alert_id}"
                    rate_limit_key = item.target_id if self._config.rate_limit_scope == "target" else "global"
                    should_process = True

                    # --- Rate limit check ---
                    if should_process and self._rate_limiter_store is not None and self._config.rate_limit_enabled:
                        try:
                            if self._enhanced_metrics:
                                self._enhanced_metrics.record_rate_limiter_check()
                            rl_result = self._rate_limiter_store.check_and_record(
                                rate_limit_key=rate_limit_key,
                                window_seconds=self._config.rate_limit_window_seconds,
                                max_attempts=self._config.rate_limit_max_attempts,
                            )
                            if rl_result.allowed:
                                if self._enhanced_metrics:
                                    self._enhanced_metrics.record_rate_limiter_allowed()
                            else:
                                if self._enhanced_metrics:
                                    self._enhanced_metrics.record_rate_limiter_denied()
                                if self._enhanced_metrics:
                                    self._enhanced_metrics.record_replay_rate_limited()
                                # Requeue with delay
                                if self._priority_queue_store is not None:
                                    await self._priority_queue_store.requeue(
                                        item.attempt_id,
                                        reason=f"Rate limited ({rl_result.remaining} remaining)",
                                    )
                                result.queue_requeued += 1
                                should_process = False
                        except Exception:
                            pass  # Fail-open on rate limiter errors

                    # --- Idempotency check ---
                    if should_process and self._idempotency_store is not None and self._config.idempotency_enabled:
                        try:
                            idem_record = ReplayIdempotencyRecord(
                                idempotency_key=idempotency_key,
                                original_attempt_id=item.attempt_id,
                                replay_type="single",
                                status="started",
                                expires_at=datetime.now(timezone.utc)
                                + __import__("datetime").timedelta(hours=self._config.idempotency_ttl_hours),
                            )
                            existing = self._idempotency_store.begin(idem_record)
                            if existing.status == "completed":
                                # Already replayed — acknowledge and skip
                                if self._priority_queue_store is not None:
                                    await self._priority_queue_store.acknowledge(
                                        item.attempt_id,
                                        worker_id=self._config.worker_id,
                                    )
                                result.queue_completed += 1
                                if self._enhanced_metrics:
                                    self._enhanced_metrics.record_replay_idempotency_hit()
                                should_process = False
                        except Exception:
                            pass  # Fail-open on idempotency errors

                    if not should_process:
                        result.attempt_ids.append(item.attempt_id)
                        continue

                    # --- Deliver ---
                    if self._enhanced_metrics:
                        self._enhanced_metrics.record_replay_attempt()
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
                        # --- Success: complete idempotency + acknowledge ---
                        if self._idempotency_store is not None and self._config.idempotency_enabled:
                            try:
                                new_attempt_id = f"att_{__import__('secrets').token_hex(8)}"
                                self._idempotency_store.complete(idempotency_key, new_attempt_id)
                            except Exception:
                                pass
                        if self._priority_queue_store is not None:
                            await self._priority_queue_store.acknowledge(
                                item.attempt_id,
                                worker_id=self._config.worker_id,
                            )
                        result.queue_completed += 1
                        if self._enhanced_metrics:
                            self._enhanced_metrics.record_replay_success()
                    elif adapter_result.retryable:
                        # --- Retryable failure: fail idempotency + requeue ---
                        if self._idempotency_store is not None and self._config.idempotency_enabled:
                            try:
                                self._idempotency_store.fail(idempotency_key, adapter_result.error_message or "retryable")
                            except Exception:
                                pass
                        if self._priority_queue_store is not None:
                            await self._priority_queue_store.requeue(
                                item.attempt_id,
                                reason=adapter_result.error_message,
                            )
                        result.queue_requeued += 1
                        if self._enhanced_metrics:
                            self._enhanced_metrics.record_replay_failure()
                    else:
                        # --- Non-retryable: dead letter evaluation ---
                        if self._idempotency_store is not None and self._config.idempotency_enabled:
                            try:
                                self._idempotency_store.fail(idempotency_key, adapter_result.error_message or "non-retryable")
                            except Exception:
                                pass
                        if self._dead_letter_policy_store is not None and self._config.dead_letter_enabled:
                            try:
                                if self._enhanced_metrics:
                                    self._enhanced_metrics.record_dead_letter_evaluated()
                                dl_result = self._dead_letter_policy_store.evaluate(item)
                                if dl_result.is_dead_letter and dl_result.record is not None:
                                    self._dead_letter_policy_store.record_dead_letter(dl_result.record)
                                    if self._priority_queue_store is not None:
                                        await self._priority_queue_store.fail(
                                            item.attempt_id,
                                            error=f"Dead letter: {dl_result.reason}",
                                            worker_id=self._config.worker_id,
                                        )
                                    result.queue_failed += 1
                                    if self._enhanced_metrics:
                                        self._enhanced_metrics.record_replay_dead_lettered()
                                        self._enhanced_metrics.record_dead_letter_triggered()
                                    should_process = False
                                elif self._enhanced_metrics:
                                    self._enhanced_metrics.record_dead_letter_passed()
                            except Exception:
                                pass
                        if should_process:
                            if self._priority_queue_store is not None:
                                await self._priority_queue_store.fail(
                                    item.attempt_id,
                                    error=adapter_result.error_message,
                                    worker_id=self._config.worker_id,
                                )
                            result.queue_failed += 1
                            if self._enhanced_metrics:
                                self._enhanced_metrics.record_replay_failure()

                    result.attempt_ids.append(item.attempt_id)

                except Exception:
                    result.queue_failed += 1
                    result.attempt_ids.append(item.attempt_id)

            # Step 4: Fallback to delivery service for remaining budget
            if remaining_budget > 0:
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
                actual_state="error" if self.is_running else "stopped",
            )
            if self._config.stop_on_error:
                raise
            return result

        finally:
            # Phase 60: Release distributed lock
            if lock_acquired and self._distributed_lock_store is not None and self._config.distributed_lock_enabled:
                try:
                    if self._enhanced_metrics:
                        self._enhanced_metrics.record_lock_release_attempt()
                    released = self._distributed_lock_store.release(
                        self._config.lock_name,
                        self._config.worker_id or "default",
                    )
                    if released and self._enhanced_metrics:
                        self._enhanced_metrics.record_lock_release_success()
                    self._lock_owner_id = None
                    self._lock_fencing_token = None
                except Exception:
                    pass

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
                "lock_acquired": lock_acquired,
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
                        "lock_acquired": lock_acquired,
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
                "lock_acquired": lock_acquired,
            },
        )

        return result

    def get_health_status(self) -> dict[str, Any]:
        """Return current daemon health status.

        Phase 57: Combines in-memory state with persisted state for restart visibility.
        Phase 60: Adds distributed lock and key rotation status.
        """
        # Start with persisted state if available
        persisted = self._get_daemon_state()
        if persisted is not None and not self.is_running:
            # After restart, use persisted state for visibility
            status = {
                "state": persisted.actual_state,
                "consecutive_failures": persisted.consecutive_failures,
                "last_error": _redact_error_message(persisted.last_error_message) if persisted.last_error_message else None,
                "started_at": persisted.started_at.isoformat() if persisted.started_at else None,
                "last_run_at": persisted.last_run_at.isoformat() if persisted.last_run_at else None,
                "last_success_at": persisted.last_success_at.isoformat() if persisted.last_success_at else None,
                "interval_seconds": self._config.interval_seconds,
                "source": "persisted",
            }
            if self._key_rotation_service is not None and self._config.key_rotation_enabled:
                try:
                    last_rot = self._key_rotation_service.get_last_rotation()
                    status["last_key_rotation"] = last_rot.rotated_at.isoformat() if last_rot else None
                except Exception:
                    pass
            return status

        # In-memory state when running
        if not self.is_running:
            state = "stopped"
        elif self._consecutive_failures == 0:
            state = "healthy"
        elif self._consecutive_failures <= 2:
            state = "degraded"
        else:
            state = "unhealthy"

        status = {
            "state": state,
            "consecutive_failures": self._consecutive_failures,
            "last_error": _redact_error_message(self._last_error) if self._last_error else None,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_run_at": self._last_run_at.isoformat() if self._last_run_at else None,
            "interval_seconds": self._config.interval_seconds,
            "source": "memory",
        }

        # Phase 60: lock status
        if self._distributed_lock_store is not None and self._config.distributed_lock_enabled:
            try:
                lock_status = self._distributed_lock_store.get_status(self._config.lock_name)
                status["lock"] = {
                    "name": lock_status.lock_name,
                    "acquired": lock_status.acquired,
                    "owner_id": lock_status.owner_id,
                    "fencing_token": lock_status.fencing_token,
                    "lease_expires_at": lock_status.lease_expires_at.isoformat() if lock_status.lease_expires_at else None,
                }
            except Exception:
                pass

        # Phase 60: key rotation status
        if self._key_rotation_service is not None and self._config.key_rotation_enabled:
            try:
                last_rot = self._key_rotation_service.get_last_rotation()
                status["key_rotation"] = {
                    "last_rotation": last_rot.rotated_at.isoformat() if last_rot else None,
                    "rotation_interval_hours": self._config.key_rotation_enabled,
                }
            except Exception:
                pass

        return status

    async def _loop(self) -> None:
        """Internal loop — runs run_once at the configured interval."""
        if self._config.run_immediately:
            try:
                await self.run_once()
            except Exception:
                if self._config.stop_on_error:
                    self._running = False
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
                    self._running = False
                    break
