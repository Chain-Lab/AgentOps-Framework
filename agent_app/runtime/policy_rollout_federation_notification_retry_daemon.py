"""Alert delivery retry daemon.

Phase 55 Task 4: Retry daemon for automatic alert delivery retry.
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
)
from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryRetryPolicy,
)


class AlertDeliveryRetryDaemonConfig(BaseModel):
    """Configuration for the alert delivery retry daemon."""

    enabled: bool = False
    interval_seconds: float = 60.0
    jitter_seconds: float = 5.0
    batch_limit: int = 100
    stop_on_error: bool = False
    run_immediately: bool = True


class AlertDeliveryRetryDaemon:
    """Automatic retry daemon for alert delivery.

    Runs ``NotificationAlertDeliveryService.run_once`` on a configurable
    interval with optional jitter to avoid thundering herd.

    The daemon does NOT start automatically — it must be explicitly started.
    """

    def __init__(
        self,
        scheduler: NotificationAlertDeliveryService,
        config: AlertDeliveryRetryDaemonConfig | None = None,
        audit_logger: Any | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._config = config or AlertDeliveryRetryDaemonConfig()
        self._audit_logger = audit_logger
        self._task: asyncio.Task | None = None
        self._running = False
        self._lock = asyncio.Lock()

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
            self._task = asyncio.create_task(self._loop())
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
                return
            self._running = False
            if self._task is not None:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                self._task = None
            if self._audit_logger:
                try:
                    self._audit_logger("retry_daemon_stopped", {})
                except Exception:
                    pass

    async def run_once(self, dry_run: bool = False) -> AlertDeliveryRetryRunResult:
        """Execute a single retry scheduler run.

        This delegates to ``scheduler.run_once`` and records the result.
        """
        try:
            result = await self._scheduler.run_once(
                limit=self._config.batch_limit,
                dry_run=dry_run,
            )
        except Exception as exc:
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
            raise

        if self._audit_logger:
            try:
                self._audit_logger(
                    "retry_daemon_run_completed",
                    {
                        "dry_run": dry_run,
                        "scanned": result.scanned,
                        "delivered": result.delivered,
                        "retry_scheduled": result.retry_scheduled,
                        "dlq": result.dlq,
                        "failed": result.failed,
                    },
                )
            except Exception:
                pass

        return result

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
