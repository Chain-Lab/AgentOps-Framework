"""Alert delivery retry daemon.

Phase 55 Task 4: Retry daemon for automatic alert delivery retry.
Phase 56 Task 730: Priority queue store integration.
Phase 57 Task 3: Deep priority queue integration + persistent state store.
Phase 60 Task 750: Closed-loop integration with Phase 59 stores.
Phase 62 Task P62: Production operations hardening (graceful drain, metrics buffer,
    lock lease extension, health HTTP server).
"""
from __future__ import annotations

import asyncio
import contextlib
import random
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Coroutine, Generator

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
    evaluate_async,
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
from agent_app.runtime.policy_rollout_federation_notification_metrics_buffer import (
    MetricsEvent,
    MetricsRingBuffer,
)
# Phase 63: control plane
from agent_app.runtime.policy_rollout_federation_notification_control_plane import (
    ControlCommandType,
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
    # Phase 61: continuous loop controls
    poll_interval_seconds: float = 1.0
    idle_sleep_seconds: float = 1.0
    error_sleep_seconds: float = 5.0
    max_consecutive_errors: int = 10
    shutdown_timeout_seconds: float = 10.0
    # Phase 62: graceful shutdown / drain
    graceful_shutdown_enabled: bool = True
    drain_timeout_seconds: float = 30.0
    cancel_inflight_on_timeout: bool = True
    # Phase 62: metrics ring buffer
    metrics_buffer_enabled: bool = True
    metrics_buffer_max_size: int = 1000
    metrics_flush_interval_seconds: float = 10.0
    flush_metrics_on_stop: bool = True
    # Phase 62: lock lease extension for long batches
    renew_lock_during_batch: bool = True
    lock_renewal_failure_policy: str = "standby"
    # Phase 62: health HTTP server
    health_http_enabled: bool = False
    health_http_host: str = "127.0.0.1"
    health_http_port: int = 8080
    ready_requires_leader: bool = False
    # Phase 63: persistent control plane
    control_plane_enabled: bool = False
    control_plane_db_path: str = ".agent_app/control_plane.db"
    control_command_poll_interval_seconds: float = 1.0
    control_command_max_age_seconds: int = 86400
    control_http_enabled: bool = False
    control_http_host: str = "127.0.0.1"
    control_http_port: int = 8090
    control_http_token: str | None = None
    control_http_token_env: str | None = "AGENT_APP_CONTROL_TOKEN"


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
        # Phase 61: continuous loop state
        self._leader_mode: bool = False
        self._last_lock_renew_at: datetime | None = None
        # Phase 62: graceful shutdown / drain state
        self._draining: bool = False
        self._inflight_count: int = 0
        self._inflight_tasks: set[asyncio.Task] = set()
        self._shutdown_started_at: datetime | None = None
        self._last_drain_duration_seconds: float | None = None
        # Phase 62: metrics buffer (lazy init)
        self._metrics_buffer: Any = None
        # Phase 62: health HTTP server (lazy init)
        self._health_server: Any = None
        # Phase 63: persistent control plane
        self._control_paused: bool = False
        self._control_plane_store: Any = None
        self._approval_store: Any = None
        self._audit_store: Any = None
        self._control_poll_task: asyncio.Task | None = None
        self._control_http_server: Any = None
        self._last_control_command_id: str | None = None
        self._last_control_error: str | None = None

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

    # ------------------------------------------------------------------
    # Phase 62: In-flight tracking
    # ------------------------------------------------------------------

    @property
    def inflight_count(self) -> int:
        """Number of currently in-flight items."""
        return self._inflight_count

    @property
    def draining(self) -> bool:
        """Whether the daemon is currently draining on shutdown."""
        return self._draining

    def _create_tracked_task(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
        """Create an asyncio Task that is tracked for in-flight counting.

        The task is added to the inflight set while it is running and
        automatically removed when it completes.  This is used for
        background batch-processing tasks that must be drained on stop.
        """
        task = asyncio.ensure_future(coro)

        def _on_done(t: asyncio.Task) -> None:
            self._inflight_tasks.discard(t)

        self._inflight_tasks.add(task)
        task.add_done_callback(_on_done)
        return task

    @contextmanager
    def _track_inflight(self) -> Generator[None, None, None]:
        """Context manager that increments/decrements the inflight counter.

        Usage::

            with self._track_inflight():
                await process_item(item)
        """
        self._inflight_count += 1
        try:
            yield
        finally:
            self._inflight_count = max(0, self._inflight_count - 1)

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
            self._consecutive_failures = 0
            self._last_error = None
            # Phase 61: Try to acquire distributed lock for leader mode
            self._leader_mode = self._acquire_distributed_lock()
            self._task = asyncio.create_task(self._loop())
            # Phase 62: Start health HTTP server if enabled
            if self._config.health_http_enabled:
                try:
                    server = self._ensure_health_server()
                    if server is not None:
                        server.start()
                except Exception:  # noqa: BLE001 — best-effort
                    pass
            # Phase 63: Start control plane if enabled
            if self._config.control_plane_enabled:
                try:
                    self._ensure_control_plane_store()
                    self._ensure_approval_store()
                    self._ensure_audit_store()
                    self._control_poll_task = asyncio.create_task(
                        self._control_poll_loop()
                    )
                    if self._config.control_http_enabled:
                        server = self._ensure_control_http_server()
                        if server is not None:
                            self._control_http_server = server
                            server.start()
                except Exception:  # noqa: BLE001 — best-effort
                    pass
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
        """Stop the daemon loop with graceful drain.

        Phase 62: If graceful_shutdown_enabled, enters draining state and
        waits for in-flight items to complete before cancelling the loop.
        """
        async with self._lock:
            if not self.is_running:
                self._running = False
                self._save_daemon_state(
                    desired_state="stopped",
                    actual_state="stopped",
                    stopped_at=datetime.now(timezone.utc),
                )
                return
            # Phase 62: Mark draining BEFORE clearing _running so the loop
            # can observe draining and wait for in-flight items.
            self._draining = True
            self._shutdown_started_at = datetime.now(timezone.utc)
            self._running = False
            # Phase 62: Wait for in-flight items to drain
            if self._config.graceful_shutdown_enabled:
                drain_deadline = datetime.now(timezone.utc).timestamp() + self._config.drain_timeout_seconds
                while self._inflight_count > 0:
                    now_ts = datetime.now(timezone.utc).timestamp()
                    remaining = drain_deadline - now_ts
                    if remaining <= 0:
                        break
                    try:
                        await asyncio.wait_for(
                            self._wait_inflight_empty(),
                            timeout=min(remaining, 0.5),
                        )
                        break
                    except asyncio.TimeoutError:
                        continue
                # Cancel remaining inflight tasks on timeout
                if (
                    self._inflight_count > 0
                    and self._config.cancel_inflight_on_timeout
                ):
                    for task in list(self._inflight_tasks):
                        if not task.done():
                            task.cancel()
                    if self._inflight_tasks:
                        try:
                            await asyncio.wait(
                                list(self._inflight_tasks),
                                timeout=min(self._config.shutdown_timeout_seconds, 2.0),
                            )
                        except asyncio.CancelledError:
                            pass
            self._draining = False
            self._last_drain_duration_seconds = (
                datetime.now(timezone.utc) - self._shutdown_started_at
            ).total_seconds()
            self._shutdown_started_at = None
            # Phase 61: Release distributed lock if we own it
            self._release_distributed_lock()
            # Phase 61: Cancel main loop task
            if self._task is not None:
                try:
                    self._task.cancel()
                    await asyncio.wait_for(
                        self._task,
                        timeout=self._config.shutdown_timeout_seconds,
                    )
                except (asyncio.CancelledError, asyncio.TimeoutError, RuntimeError):
                    # RuntimeError: event loop is closed
                    pass
                self._task = None
            # Phase 62: Stop health HTTP server
            if self._health_server is not None:
                try:
                    self._health_server.stop()
                except Exception:  # noqa: BLE001 — best-effort
                    pass
                self._health_server = None
            # Phase 63: Stop control HTTP server
            if self._control_http_server is not None:
                try:
                    self._control_http_server.stop()
                except Exception:  # noqa: BLE001 — best-effort
                    pass
                self._control_http_server = None
            # Phase 63: Cancel control poll task
            if self._control_poll_task is not None:
                try:
                    self._control_poll_task.cancel()
                except Exception:  # noqa: BLE001 — best-effort
                    pass
                self._control_poll_task = None
            # Phase 63: Close control plane stores
            for store_attr in ("_control_plane_store", "_approval_store", "_audit_store"):
                store = getattr(self, store_attr, None)
                if store is not None:
                    try:
                        store.close()
                    except Exception:  # noqa: BLE001 — best-effort
                        pass
                    setattr(self, store_attr, None)
            # Phase 62: Flush metrics on shutdown
            if self._config.flush_metrics_on_stop:
                self._flush_metrics()
            self._last_run_at = None
            self._last_error = None
            self._consecutive_failures = 0
            self._inflight_count = 0
            self._inflight_tasks.clear()
            self._save_daemon_state(
                desired_state="stopped",
                actual_state="stopped",
                stopped_at=datetime.now(timezone.utc),
                last_error_message=None,
                consecutive_failures=0,
            )
            self._record_change_event(
                event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_RETRY_DAEMON_STOPPED,
                payload={
                    "drain_duration_seconds": self._last_drain_duration_seconds,
                },
            )
            if self._audit_logger:
                try:
                    self._audit_logger(
                        "retry_daemon_stopped",
                        {
                            "drain_duration_seconds": self._last_drain_duration_seconds,
                        },
                    )
                except Exception:
                    pass

    async def _wait_inflight_empty(self) -> None:
        """Wait until inflight_count reaches zero.

        Polls every event-loop iteration.  Exits early if the drain deadline
        has passed so the caller can cancel remaining tasks.
        """
        while self._inflight_count > 0:
            if self._shutdown_started_at is not None:
                elapsed = datetime.now(timezone.utc).timestamp() - self._shutdown_started_at.timestamp()
                if elapsed >= self._config.drain_timeout_seconds:
                    # Deadline passed — exit and let caller cancel tasks
                    break
            await asyncio.sleep(0)

    async def _run_once_continuous(self) -> AlertDeliveryRetryDaemonRunResult:
        """Run once in leader mode — skips lock acquire/release.

        Phase 61: Used by the continuous loop when the daemon already
        holds the distributed lock.
        """
        return await self.run_once(dry_run=False, _leader_mode=True)

    async def run_once(self, dry_run: bool = False, _leader_mode: bool = False) -> AlertDeliveryRetryDaemonRunResult:
        """Execute a single retry scheduler run with priority queue integration.

        Phase 57: Priority queue first, then fallback to delivery service.
        Phase 60: Closed-loop with distributed lock, rate limit, idempotency,
        dead letter policy, key rotation, and enhanced metrics.
        """
        result = AlertDeliveryRetryDaemonRunResult(dry_run=dry_run, worker_id=self._config.worker_id)
        lock_acquired = False

        try:
            # Phase 61: Skip lock management when called from continuous loop
            # (lock is already held by the loop's leader mode)
            if not _leader_mode:
                # Phase 60: Distributed lock leader election (standalone run_once)
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
            else:
                lock_acquired = True  # Loop already holds the lock

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

            with self._track_inflight():
                # Phase 62: Renew lock during long batch if needed
                if (
                    self._config.renew_lock_during_batch
                    and self._leader_mode
                    and self._should_renew_lock()
                ):
                    renewed = self._renew_distributed_lock()
                    if not renewed:
                        self._leader_mode = False
                        if self._enhanced_metrics:
                            self._enhanced_metrics.record_lock_renew_failed()

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
                                dl_result = await evaluate_async(self._dead_letter_policy_store, item)
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
            # Phase 61: Skip lock release when in continuous leader mode
            # (lock is managed by the loop, released on stop)
            if lock_acquired and not _leader_mode:
                if self._distributed_lock_store is not None and self._config.distributed_lock_enabled:
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

    def _acquire_distributed_lock(self) -> bool:
        """Try to acquire the distributed lock. Returns True on success."""
        if (
            self._distributed_lock_store is None
            or not self._config.distributed_lock_enabled
        ):
            return True  # No lock configured — treat as always acquired
        try:
            status = self._distributed_lock_store.acquire(
                lock_name=self._config.lock_name,
                owner_id=self._config.worker_id or "default",
                lease_seconds=self._config.lock_lease_seconds,
            )
            if status.acquired:
                self._lock_owner_id = status.owner_id
                self._lock_fencing_token = status.fencing_token
                self._last_lock_renew_at = datetime.now(timezone.utc)
                if self._enhanced_metrics:
                    self._enhanced_metrics.record_lock_acquire_success()
                return True
            else:
                if self._enhanced_metrics:
                    self._enhanced_metrics.record_lock_acquire_denied()
                self._lock_owner_id = None
                self._lock_fencing_token = None
                return False
        except Exception:
            if self._enhanced_metrics:
                self._enhanced_metrics.record_lock_acquire_exception()
            return False

    def _renew_distributed_lock(self) -> bool:
        """Renew the distributed lock if needed. Returns True on success."""
        if (
            self._distributed_lock_store is None
            or not self._config.distributed_lock_enabled
            or self._lock_owner_id is None
        ):
            return True
        try:
            status = self._distributed_lock_store.renew(
                lock_name=self._config.lock_name,
                owner_id=self._lock_owner_id,
                lease_seconds=self._config.lock_lease_seconds,
            )
            if status.acquired:
                self._lock_fencing_token = status.fencing_token
                self._last_lock_renew_at = datetime.now(timezone.utc)
                if self._enhanced_metrics:
                    self._enhanced_metrics.record_lock_renew_success()
                return True
            else:
                if self._enhanced_metrics:
                    self._enhanced_metrics.record_lock_renew_failed()
                self._lock_owner_id = None
                self._lock_fencing_token = None
                self._leader_mode = False
                return False
        except Exception:
            if self._enhanced_metrics:
                self._enhanced_metrics.record_lock_renew_failed()
            self._lock_owner_id = None
            self._lock_fencing_token = None
            self._leader_mode = False
            return False

    def _release_distributed_lock(self) -> None:
        """Release the distributed lock if we own it."""
        if (
            self._distributed_lock_store is None
            or not self._config.distributed_lock_enabled
            or self._lock_owner_id is None
        ):
            return
        try:
            if self._enhanced_metrics:
                self._enhanced_metrics.record_lock_release_attempt()
            released = self._distributed_lock_store.release(
                self._config.lock_name,
                self._lock_owner_id,
            )
            if released and self._enhanced_metrics:
                self._enhanced_metrics.record_lock_release_success()
        except Exception:
            pass
        finally:
            self._lock_owner_id = None
            self._lock_fencing_token = None
            self._leader_mode = False

    def _should_renew_lock(self) -> bool:
        """Whether the lock should be renewed now."""
        if self._last_lock_renew_at is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self._last_lock_renew_at).total_seconds()
        return elapsed >= (self._config.lock_renew_interval_seconds * 0.8)

    def _flush_metrics(self) -> None:
        """Flush metrics buffer to exporter if configured. Best-effort."""
        if not self._config.metrics_buffer_enabled:
            return
        buffer = self._ensure_metrics_buffer()
        if buffer is None:
            return
        exporter = self._enhanced_metrics
        if exporter is None:
            return
        try:
            from agent_app.runtime.policy_rollout_federation_notification_metrics_exporter import (
                PrometheusFileMetricsExporter,
            )
            # Try to find the output path from the existing exporter or config
            path = getattr(exporter, "_path", None) or getattr(
                self._config, "metrics_export_path", "/tmp/agent_daemon_metrics.prom"
            )
            prom_exporter = PrometheusFileMetricsExporter(path=path)
            buffer.flush_to_exporter(prom_exporter)
        except Exception:  # noqa: BLE001 — best-effort
            pass

    def _ensure_metrics_buffer(self) -> Any:
        """Lazily create the metrics ring buffer if enabled."""
        if not self._config.metrics_buffer_enabled:
            return None
        if self._metrics_buffer is None:
            try:
                from agent_app.runtime.policy_rollout_federation_notification_metrics_buffer import (
                    MetricsRingBuffer,
                )
                self._metrics_buffer = MetricsRingBuffer(
                    max_size=self._config.metrics_buffer_max_size,
                )
            except Exception:  # noqa: BLE001 — best-effort
                return None
        return self._metrics_buffer

    def record_metric(
        self,
        name: str,
        value: float | int,
        labels: dict[str, str] | None = None,
    ) -> None:
        """Record a metric event to the ring buffer. Best-effort."""
        buffer = self._ensure_metrics_buffer()
        if buffer is None:
            return
        try:
            buffer.append(
                MetricsEvent(name=name, value=value, labels=labels or {})
            )
        except Exception:  # noqa: BLE001 — best-effort
            pass

    def _ensure_health_server(self) -> Any:
        """Lazily create the health HTTP server if enabled."""
        if not self._config.health_http_enabled:
            return None
        if self._health_server is None:
            try:
                from agent_app.runtime.policy_rollout_federation_notification_health_server import (
                    HealthHTTPServer,
                )
                self._health_server = HealthHTTPServer(
                    host=self._config.health_http_host,
                    port=self._config.health_http_port,
                    health_fn=self.get_health_status,
                    ready_fn=self.get_health_status,
                )
            except Exception:  # noqa: BLE001 — best-effort
                return None
        return self._health_server

    # ------------------------------------------------------------------
    # Phase 63: Persistent Control Plane
    # ------------------------------------------------------------------

    def _ensure_control_plane_store(self) -> Any:
        """Lazily create the control plane store if enabled."""
        if not self._config.control_plane_enabled:
            return None
        if self._control_plane_store is None:
            try:
                from agent_app.runtime.policy_rollout_federation_notification_control_plane import (
                    ControlPlaneStore,
                )
                self._control_plane_store = ControlPlaneStore(
                    self._config.control_plane_db_path,
                )
            except Exception:  # noqa: BLE001 — best-effort
                return None
        return self._control_plane_store

    def _ensure_approval_store(self) -> Any:
        """Lazily create the approval store if control plane is enabled."""
        if not self._config.control_plane_enabled:
            return None
        if self._approval_store is None:
            try:
                from agent_app.runtime.policy_rollout_federation_notification_approval_store import (
                    PersistentApprovalStore,
                )
                self._approval_store = PersistentApprovalStore(
                    self._config.control_plane_db_path,
                )
            except Exception:  # noqa: BLE001 — best-effort
                return None
        return self._approval_store

    def _ensure_audit_store(self) -> Any:
        """Lazily create the audit store if control plane is enabled."""
        if not self._config.control_plane_enabled:
            return None
        if self._audit_store is None:
            try:
                from agent_app.runtime.policy_rollout_federation_notification_audit_store import (
                    PersistentAuditStore,
                )
                self._audit_store = PersistentAuditStore(
                    self._config.control_plane_db_path,
                )
            except Exception:  # noqa: BLE001 — best-effort
                return None
        return self._audit_store

    def _execute_control_command(self, cmd: Any) -> None:
        """Execute a control command and update daemon state.

        Handles: pause, resume, drain, shutdown, flush_metrics,
        release_lock, health_snapshot.
        """
        command_type = cmd.command_type
        if command_type == ControlCommandType.PAUSE:
            self._control_paused = True
            self._last_control_command_id = cmd.command_id
            self._last_control_error = None
        elif command_type == ControlCommandType.RESUME:
            self._control_paused = False
            self._last_control_command_id = cmd.command_id
            self._last_control_error = None
        elif command_type == ControlCommandType.DRAIN:
            self._control_paused = False
            self._last_control_command_id = cmd.command_id
            self._last_control_error = None
        elif command_type == ControlCommandType.SHUTDOWN:
            self._control_paused = False
            self._last_control_command_id = cmd.command_id
            self._last_control_error = None
        elif command_type == ControlCommandType.FLUSH_METRICS:
            self._flush_metrics()
            self._last_control_command_id = cmd.command_id
            self._last_control_error = None
        elif command_type == ControlCommandType.RELEASE_LOCK:
            if self._leader_mode:
                self._release_distributed_lock()
            self._last_control_command_id = cmd.command_id
            self._last_control_error = None
        elif command_type == ControlCommandType.HEALTH_SNAPSHOT:
            self._last_control_command_id = cmd.command_id
            self._last_control_error = None

    async def _control_poll_loop(self) -> None:
        """Background loop that polls for pending control commands."""
        while self._running:
            store = self._control_plane_store
            if store is None:
                await asyncio.sleep(self._config.control_command_poll_interval_seconds)
                continue
            try:
                pending = store.list_pending_commands(limit=100)
                for cmd in pending:
                    if not self._running:
                        break
                    try:
                        store.mark_accepted(cmd.command_id)
                        store.mark_running(cmd.command_id)
                        self._execute_control_command(cmd)
                        store.mark_completed(cmd.command_id)
                        # Audit event
                        audit = self._ensure_audit_store()
                        if audit is not None:
                            try:
                                audit.append(
                                    event_id=f"evt_{cmd.command_id}_completed",
                                    event_type="control.command.completed",
                                    command_id=cmd.command_id,
                                    daemon_id=self._config.daemon_id,
                                    actor=cmd.requested_by,
                                    data={"command_type": cmd.command_type.value},
                                )
                            except Exception:
                                pass
                    except Exception as exc:
                        store.mark_failed(cmd.command_id, {"error": str(exc)})
                        self._last_control_error = str(exc)
            except Exception:
                pass
            try:
                await asyncio.sleep(self._config.control_command_poll_interval_seconds)
            except asyncio.CancelledError:
                break

    def _ensure_control_http_server(self) -> Any:
        """Lazily create the control HTTP server if enabled."""
        if not self._config.control_http_enabled:
            return None
        try:
            from agent_app.runtime.policy_rollout_federation_notification_control_server import (
                _ControlHTTPServer,
            )
            server = _ControlHTTPServer(
                host=self._config.control_http_host,
                port=self._config.control_http_port,
                auth_token=self._config.control_http_token,
                status_fn=self.get_health_status,
                create_command_fn=self._http_create_command,
                list_commands_fn=self._http_list_commands,
                get_command_fn=self._http_get_command,
                list_approvals_fn=self._http_list_approvals,
                approve_fn=self._http_approve,
                reject_fn=self._http_reject,
                audit_fn=self._http_audit_events,
            )
            return server
        except Exception:  # noqa: BLE001 — best-effort
            return None

    def _http_create_command(self, body: dict[str, Any]) -> dict[str, Any]:
        store = self._ensure_control_plane_store()
        if store is None:
            raise RuntimeError("Control plane not enabled")
        cmd_type = body.get("command_type")
        if cmd_type is None:
            raise ValueError("command_type is required")
        try:
            command_type = ControlCommandType(cmd_type)
        except ValueError:
            raise ValueError(f"Invalid command_type: {cmd_type}")
        cmd = store.create_command(
            command_id=f"cmd_{__import__('secrets').token_hex(8)}",
            command_type=command_type,
            requested_by=body.get("requested_by"),
            reason=body.get("reason"),
            payload=body.get("payload", {}),
        )
        return {
            "command_id": cmd.command_id,
            "command_type": cmd.command_type.value,
            "status": cmd.status.value,
            "requested_by": cmd.requested_by,
            "reason": cmd.reason,
            "payload": cmd.payload,
        }

    def _http_list_commands(self) -> list[dict[str, Any]]:
        store = self._control_plane_store
        if store is None:
            return []
        commands = store.list_commands(limit=100)
        return [
            {
                "command_id": c.command_id,
                "command_type": c.command_type.value,
                "status": c.status.value,
                "requested_by": c.requested_by,
                "reason": c.reason,
                "payload": c.payload,
                "created_at": c.created_at.isoformat(),
            }
            for c in commands
        ]

    def _http_get_command(self, command_id: str) -> dict[str, Any] | None:
        store = self._control_plane_store
        if store is None:
            return None
        cmd = store.get_command(command_id)
        if cmd is None:
            return None
        return {
            "command_id": cmd.command_id,
            "command_type": cmd.command_type.value,
            "status": cmd.status.value,
            "requested_by": cmd.requested_by,
            "reason": cmd.reason,
            "payload": cmd.payload,
            "error": cmd.error,
            "created_at": cmd.created_at.isoformat(),
            "accepted_at": cmd.accepted_at.isoformat() if cmd.accepted_at else None,
            "completed_at": cmd.completed_at.isoformat() if cmd.completed_at else None,
        }

    def _http_list_approvals(self) -> list[dict[str, Any]]:
        store = self._ensure_approval_store()
        if store is None:
            return []
        approvals = store.list_pending(limit=100)
        return [
            {
                "approval_id": a.approval_id,
                "approval_type": a.approval_type,
                "status": a.status.value,
                "requested_by": a.requested_by,
                "resolved_by": a.resolved_by,
                "reason": a.reason,
                "daemon_id": a.daemon_id,
                "created_at": a.created_at.isoformat(),
            }
            for a in approvals
        ]

    def _http_approve(self, approval_id: str, body: dict[str, Any]) -> dict[str, Any]:
        store = self._ensure_approval_store()
        if store is None:
            raise RuntimeError("Approval store not enabled")
        approval = store.approve(
            approval_id=approval_id,
            approved_by=body.get("resolved_by", "unknown"),
            reason=body.get("reason"),
        )
        return {
            "approval_id": approval.approval_id,
            "status": approval.status.value,
            "resolved_by": approval.resolved_by,
            "reason": approval.reason,
        }

    def _http_reject(self, approval_id: str, body: dict[str, Any]) -> dict[str, Any]:
        store = self._ensure_approval_store()
        if store is None:
            raise RuntimeError("Approval store not enabled")
        approval = store.reject(
            approval_id=approval_id,
            rejected_by=body.get("resolved_by", "unknown"),
            reason=body.get("reason"),
        )
        return {
            "approval_id": approval.approval_id,
            "status": approval.status.value,
            "resolved_by": approval.resolved_by,
            "reason": approval.reason,
        }

    def _http_audit_events(self) -> list[dict[str, Any]]:
        store = self._ensure_audit_store()
        if store is None:
            return []
        events = store.list_recent(limit=100)
        return [
            {
                "event_id": e.event_id,
                "event_type": e.event_type,
                "command_id": e.command_id,
                "approval_id": e.approval_id,
                "daemon_id": e.daemon_id,
                "actor": e.actor,
                "data": e.data,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ]

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
            "running": self.is_running,
            "leader": self._leader_mode,
            "consecutive_failures": self._consecutive_failures,
            "last_error": _redact_error_message(self._last_error) if self._last_error else None,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_run_at": self._last_run_at.isoformat() if self._last_run_at else None,
            "interval_seconds": self._config.interval_seconds,
            "source": "memory",
            # Phase 62: drain / inflight fields
            "draining": self._draining,
            "inflight_count": self._inflight_count,
            "shutdown_started_at": self._shutdown_started_at.isoformat() if self._shutdown_started_at else None,
            "last_drain_duration_seconds": self._last_drain_duration_seconds,
            # Phase 63: control plane fields
            "control_plane_enabled": self._config.control_plane_enabled,
            "control_paused": self._control_paused,
            "control_db_path": self._config.control_plane_db_path,
            "last_control_command_id": self._last_control_command_id,
            "last_control_error": self._last_control_error,
            "pending_control_commands": 0,
            "pending_approvals": 0,
        }

        # Phase 63: control plane counts
        if self._config.control_plane_enabled:
            try:
                store = self._control_plane_store
                if store is not None:
                    pending_cmds = store.list_pending_commands(limit=100)
                    status["pending_control_commands"] = len(pending_cmds)
            except Exception:
                pass
            try:
                approval_store = self._ensure_approval_store()
                if approval_store is not None:
                    pending_approvals = approval_store.list_pending(limit=100)
                    status["pending_approvals"] = len(pending_approvals)
            except Exception:
                pass

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
        """Internal continuous loop with leader/standby lock management.

        Phase 61: Supports distributed lock acquisition, renewal, standby mode,
        idle sleep on empty queue, and max consecutive error handling.
        """
        # Phase 61: Initial run if leader and run_immediately
        if self._config.run_immediately and self._leader_mode:
            try:
                await self._run_once_continuous()
            except Exception:
                if self._config.stop_on_error:
                    self._running = False
                    return

        while self._running:
            if not self._leader_mode:
                # --- Standby mode: retry lock acquisition ---
                try:
                    await asyncio.sleep(self._config.poll_interval_seconds)
                except asyncio.CancelledError:
                    break
                if not self._running:
                    break
                if self._acquire_distributed_lock():
                    self._leader_mode = True
                    self._consecutive_failures = 0
                continue

            # --- Leader mode: renew lock if needed ---
            if self._should_renew_lock() and not self._renew_distributed_lock():
                # Renew failed — lost leadership, enter standby
                self._leader_mode = False
                continue

            # Phase 63: skip batch if paused
            if self._control_paused:
                try:
                    await asyncio.sleep(self._config.idle_sleep_seconds)
                except asyncio.CancelledError:
                    break
                continue

            # --- Run one iteration ---
            try:
                await self._run_once_continuous()
                self._consecutive_failures = 0
            except asyncio.CancelledError:
                break
            except Exception:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self._config.max_consecutive_errors:
                    # Too many errors — enter standby to avoid split-brain
                    self._leader_mode = False
                    self._release_distributed_lock()
                    continue
                await asyncio.sleep(self._config.error_sleep_seconds)
                continue

            # --- Idle sleep between iterations ---
            try:
                await asyncio.sleep(self._config.idle_sleep_seconds)
            except asyncio.CancelledError:
                break

    async def run_forever(self) -> None:
        """Run the daemon loop in the current task (blocking).

        Useful for CLI ``daemon start``. Caller should handle signals for
        graceful shutdown.
        """
        self._running = True
        try:
            await self._loop()
        finally:
            self._running = False
            self._release_distributed_lock()
            self._flush_metrics()
