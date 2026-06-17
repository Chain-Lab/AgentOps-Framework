"""Policy expiration worker — background task that periodically runs the expiration sweep.

Phase 44: Notification Hooks and Expiration Workers.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from agent_app.runtime.policy_expiration_service import PolicyExpirationService

logger = logging.getLogger(__name__)


class PolicyExpirationWorker:
    """Background worker that periodically runs the expiration sweep.

    Must NOT start automatically on instantiation. Call start() to begin
    the background loop, and stop() to cancel it. Safe to call stop()
    without having called start().
    """

    def __init__(
        self,
        expiration_service: PolicyExpirationService,
        interval_seconds: int = 60,
    ) -> None:
        self._service = expiration_service
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        """Whether the background sweep loop is currently running."""
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Start the background sweep loop as an asyncio task."""
        if self.is_running:
            return
        self._task = asyncio.ensure_future(self._run_loop())
        logger.info(
            "PolicyExpirationWorker started (interval=%ds)", self._interval_seconds
        )

    def stop(self) -> None:
        """Cancel the background sweep task. Safe to call without start()."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            logger.info("PolicyExpirationWorker stopped")
        self._task = None

    async def run_once(self) -> Any:
        """Run a single expiration sweep and return the report.

        This is the preferred entry point for tests and one-shot execution.
        """
        return await self._service.sweep()

    async def _run_loop(self) -> None:
        """Background loop: call sweep, sleep, repeat. Catches exceptions."""
        while True:
            try:
                await self._service.sweep()
            except Exception:
                logger.exception("Error in expiration sweep loop")
            await asyncio.sleep(self._interval_seconds)
