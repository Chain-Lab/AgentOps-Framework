"""Federation scheduled worker — orchestrates notification dispatch and escalation on a configurable interval.

Phase 50: Persistent scheduled worker with start/stop/status/tick lifecycle.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from agent_app.runtime.distributed_lock import DistributedLock
    from agent_app.runtime.policy_rollout_federation_escalation_worker import FederationApprovalEscalationWorker
    from agent_app.runtime.policy_rollout_federation_notification_service import FederationNotificationService

logger = logging.getLogger(__name__)


class FederationScheduledWorkerStatus(StrEnum):
    """Status of the federation scheduled worker."""
    STOPPED = "stopped"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


class FederationScheduledWorkerState(BaseModel):
    """Current state of the federation scheduled worker."""
    worker_id: str = Field(..., description="Unique worker identifier")
    status: FederationScheduledWorkerStatus = Field(default=FederationScheduledWorkerStatus.STOPPED, description="Current worker status")
    interval_seconds: int = Field(default=60, description="Tick interval in seconds")
    started_at: datetime | None = Field(default=None, description="Timezone-aware start timestamp")
    stopped_at: datetime | None = Field(default=None, description="Timezone-aware stop timestamp")
    last_tick_at: datetime | None = Field(default=None, description="Timezone-aware last tick timestamp")
    last_error: str | None = Field(default=None, description="Last error message")
    tick_count: int = Field(default=0, description="Total tick count")

    @field_validator("started_at", "stopped_at", "last_tick_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and (v.tzinfo is None or v.tzinfo.utcoffset(v) is None):
            raise ValueError("datetime must be timezone-aware")
        return v


class FederationScheduledWorker:
    """Scheduled worker that runs notification dispatch and escalation ticks on an interval.

    Lifecycle:
    - start(): begins an asyncio task that loops tick+sleep
    - stop(): signals the task to stop gracefully
    - status(): returns current FederationScheduledWorkerState
    - tick(): runs one cycle of notification dispatch + escalation

    The worker acquires a distributed lock before each tick if a lock is provided.
    """

    def __init__(
        self,
        *,
        escalation_worker: FederationApprovalEscalationWorker | None = None,
        notification_service: FederationNotificationService | None = None,
        distributed_lock: DistributedLock | None = None,
        interval_seconds: int = 60,
        worker_id: str | None = None,
    ) -> None:
        self._escalation_worker = escalation_worker
        self._notification_service = notification_service
        self._lock = distributed_lock
        self._interval_seconds = interval_seconds
        self._worker_id = worker_id or f"fsw_{uuid.uuid4().hex}"

        self._status = FederationScheduledWorkerStatus.STOPPED
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None
        self._last_tick_at: datetime | None = None
        self._last_error: str | None = None
        self._tick_count = 0

        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        """Start the scheduled worker loop.

        Raises:
            RuntimeError: If the worker is already running.
        """
        if self._status == FederationScheduledWorkerStatus.RUNNING:
            raise RuntimeError(f"Worker {self._worker_id} is already running")

        self._status = FederationScheduledWorkerStatus.RUNNING
        self._started_at = datetime.now(timezone.utc)
        self._stopped_at = None
        self._last_error = None
        self._stop_event.clear()

        self._task = asyncio.create_task(self._run_loop())
        logger.info("Scheduled worker %s started", self._worker_id)

    async def stop(self) -> None:
        """Signal the worker to stop gracefully."""
        if self._status != FederationScheduledWorkerStatus.RUNNING:
            return

        self._status = FederationScheduledWorkerStatus.STOPPING
        self._stop_event.set()
        logger.info("Scheduled worker %s stopping", self._worker_id)

    async def status(self) -> FederationScheduledWorkerState:
        """Return the current worker state."""
        return FederationScheduledWorkerState(
            worker_id=self._worker_id,
            status=self._status,
            interval_seconds=self._interval_seconds,
            started_at=self._started_at,
            stopped_at=self._stopped_at,
            last_tick_at=self._last_tick_at,
            last_error=self._last_error,
            tick_count=self._tick_count,
        )

    async def tick(self) -> FederationScheduledWorkerState:
        """Execute a single tick: dispatch notifications + escalation check.

        Acquires distributed lock if provided. Records errors without crashing.
        """
        now = datetime.now(timezone.utc)
        lock_owner: str | None = None
        lock_acquired = False

        # Acquire lock if provided
        if self._lock is not None:
            lock_owner = f"sched-worker-{uuid.uuid4().hex[:8]}"
            try:
                lock_acquired = await self._lock.acquire(
                    lock_name="federation:scheduled:worker",
                    owner_id=lock_owner,
                    ttl_seconds=self._interval_seconds * 2,
                )
            except Exception as exc:  # noqa: BLE001
                self._last_error = f"Lock acquisition error: {exc}"
                self._tick_count += 1
                self._last_tick_at = now
                return await self.status()

            if not lock_acquired:
                self._last_error = "Lock unavailable"
                self._tick_count += 1
                self._last_tick_at = now
                return await self.status()

        try:
            # Dispatch pending notifications
            if self._notification_service is not None:
                try:
                    await self._notification_service.dispatch_pending()
                except Exception as exc:  # noqa: BLE001
                    self._last_error = f"Notification dispatch error: {exc}"
                    logger.debug("Notification dispatch failed in tick", exc_info=True)

            # Run escalation tick
            if self._escalation_worker is not None:
                try:
                    await self._escalation_worker.tick(now=now)
                except Exception as exc:  # noqa: BLE001
                    self._last_error = f"Escalation tick error: {exc}"
                    logger.debug("Escalation tick failed in tick", exc_info=True)
        finally:
            # Release lock
            if lock_acquired and self._lock is not None and lock_owner is not None:
                try:
                    await self._lock.release(
                        lock_name="federation:scheduled:worker",
                        owner_id=lock_owner,
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("Lock release failed", exc_info=True)

        self._tick_count += 1
        self._last_tick_at = now
        return await self.status()

    async def _run_loop(self) -> None:
        """Internal loop that runs tick on interval until stopped."""
        try:
            while not self._stop_event.is_set():
                await self.tick()
                # Wait for interval or stop signal
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._interval_seconds,
                    )
                    # If we get here, stop was signaled
                    break
                except asyncio.TimeoutError:
                    # Normal — interval elapsed, continue loop
                    pass
        except asyncio.CancelledError:
            logger.info("Scheduled worker %s cancelled", self._worker_id)
        except Exception as exc:  # noqa: BLE001
            self._status = FederationScheduledWorkerStatus.FAILED
            self._last_error = str(exc)
            logger.error("Scheduled worker %s failed: %s", self._worker_id, exc)
        finally:
            self._status = FederationScheduledWorkerStatus.STOPPED
            self._stopped_at = datetime.now(timezone.utc)
            logger.info("Scheduled worker %s stopped", self._worker_id)
