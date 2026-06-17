"""Tests for PolicyExpirationWorker — start, stop, run_once, and no-auto-start.

Phase 44 Task 5: Notification Hooks and Expiration Workers.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agent_app.runtime.policy_expiration_service import PolicyExpirationService
from agent_app.runtime.policy_expiration_worker import PolicyExpirationWorker


class TestExpirationWorker:
    """Tests for PolicyExpirationWorker."""

    async def test_run_once_calls_sweep(self) -> None:
        """run_once() calls service.sweep() and returns the report."""
        service = AsyncMock(spec=PolicyExpirationService)
        service.sweep.return_value = AsyncMock()

        worker = PolicyExpirationWorker(expiration_service=service)
        report = await worker.run_once()

        service.sweep.assert_called_once()
        assert report == service.sweep.return_value

    async def test_start_stop_safe(self) -> None:
        """Safe to call stop() without having started the worker."""
        service = AsyncMock(spec=PolicyExpirationService)
        worker = PolicyExpirationWorker(expiration_service=service)

        # stop() without start() should not raise
        worker.stop()
        assert not worker.is_running

        # start() then stop() should work
        worker.start()
        assert worker.is_running
        worker.stop()
        assert not worker.is_running

    def test_no_auto_start_on_import(self) -> None:
        """is_running is False after construction — worker must NOT auto-start."""
        service = AsyncMock(spec=PolicyExpirationService)
        worker = PolicyExpirationWorker(expiration_service=service)

        assert not worker.is_running
