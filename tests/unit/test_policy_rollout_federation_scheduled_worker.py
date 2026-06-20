"""Tests for FederationScheduledWorker — Phase 50 Task 4."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_app.runtime.policy_rollout_federation_scheduled_worker import (
    FederationScheduledWorker,
    FederationScheduledWorkerState,
    FederationScheduledWorkerStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_async(coro):
    """Run an async coroutine in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_mock_escalation_worker():
    worker = AsyncMock()
    worker.tick = AsyncMock(
        return_value=MagicMock(
            scanned_count=0, escalated_count=0, skipped_count=0, errors=[],
        ),
    )
    return worker


def _make_mock_notification_service():
    service = AsyncMock()
    service.dispatch_pending = AsyncMock(
        return_value=MagicMock(
            total_dispatched=0, total_sent=0, total_failed=0, total_skipped=0, errors=[],
        ),
    )
    return service


def _make_mock_lock(acquire_return=True):
    lock = AsyncMock()
    lock.acquire = AsyncMock(return_value=acquire_return)
    lock.release = AsyncMock(return_value=True)
    return lock


# ---------------------------------------------------------------------------
# Enum / State tests
# ---------------------------------------------------------------------------

class TestFederationScheduledWorkerStatus:
    def test_worker_status_enum_values(self):
        assert FederationScheduledWorkerStatus.STOPPED == "stopped"
        assert FederationScheduledWorkerStatus.RUNNING == "running"
        assert FederationScheduledWorkerStatus.STOPPING == "stopping"
        assert FederationScheduledWorkerStatus.FAILED == "failed"


class TestFederationScheduledWorkerState:
    def test_worker_state_defaults(self):
        state = FederationScheduledWorkerState(worker_id="test")
        assert state.status == FederationScheduledWorkerStatus.STOPPED
        assert state.tick_count == 0
        assert state.interval_seconds == 60
        assert state.started_at is None
        assert state.stopped_at is None
        assert state.last_tick_at is None
        assert state.last_error is None

    def test_worker_state_tz_validation(self):
        now = datetime.now(timezone.utc)
        state = FederationScheduledWorkerState(
            worker_id="test",
            started_at=now,
            stopped_at=now,
            last_tick_at=now,
        )
        assert state.started_at == now

    def test_worker_state_tz_naive_rejected(self):
        naive = datetime(2026, 1, 1, 0, 0, 0)
        with pytest.raises(ValueError, match="timezone-aware"):
            FederationScheduledWorkerState(worker_id="test", started_at=naive)

    def test_worker_state_model_valid(self):
        now = datetime.now(timezone.utc)
        state = FederationScheduledWorkerState(
            worker_id="w1",
            status=FederationScheduledWorkerStatus.RUNNING,
            interval_seconds=30,
            started_at=now,
            tick_count=5,
            last_error=None,
        )
        assert state.worker_id == "w1"
        assert state.status == FederationScheduledWorkerStatus.RUNNING
        assert state.interval_seconds == 30
        assert state.tick_count == 5


# ---------------------------------------------------------------------------
# Worker lifecycle tests
# ---------------------------------------------------------------------------

class TestFederationScheduledWorkerLifecycle:
    def test_worker_initial_status_stopped(self):
        worker = FederationScheduledWorker()
        assert worker._status == FederationScheduledWorkerStatus.STOPPED

    def test_worker_start_changes_status_running(self):
        worker = FederationScheduledWorker(interval_seconds=3600)

        async def _test():
            await worker.start()
            state = await worker.status()
            assert state.status == FederationScheduledWorkerStatus.RUNNING
            assert state.started_at is not None
            await worker.stop()
            # Give event loop a chance to process
            await asyncio.sleep(0.01)

        _run_async(_test())

    def test_worker_stop_changes_status_stopping(self):
        worker = FederationScheduledWorker(interval_seconds=3600)

        async def _test():
            await worker.start()
            await worker.stop()
            state = await worker.status()
            # After stop() the status is STOPPING; the task will move to STOPPED
            assert state.status in (
                FederationScheduledWorkerStatus.STOPPING,
                FederationScheduledWorkerStatus.STOPPED,
            )
            await asyncio.sleep(0.05)
            state = await worker.status()
            assert state.status == FederationScheduledWorkerStatus.STOPPED

        _run_async(_test())

    def test_worker_start_twice_raises(self):
        worker = FederationScheduledWorker(interval_seconds=3600)

        async def _test():
            await worker.start()
            with pytest.raises(RuntimeError, match="already running"):
                await worker.start()
            await worker.stop()
            await asyncio.sleep(0.01)

        _run_async(_test())

    def test_worker_stop_when_stopped_is_noop(self):
        worker = FederationScheduledWorker()

        async def _test():
            # Should not raise — stopping a stopped worker is fine
            await worker.stop()
            state = await worker.status()
            assert state.status == FederationScheduledWorkerStatus.STOPPED

        _run_async(_test())

    def test_worker_custom_interval(self):
        worker = FederationScheduledWorker(interval_seconds=120)

        async def _test():
            state = await worker.status()
            assert state.interval_seconds == 120

        _run_async(_test())


# ---------------------------------------------------------------------------
# Tick tests
# ---------------------------------------------------------------------------

class TestFederationScheduledWorkerTick:
    def test_worker_tick_increments_count(self):
        worker = FederationScheduledWorker()

        async def _test():
            assert (await worker.status()).tick_count == 0
            await worker.tick()
            assert (await worker.status()).tick_count == 1

        _run_async(_test())

    def test_worker_tick_records_last_tick_at(self):
        worker = FederationScheduledWorker()

        async def _test():
            assert (await worker.status()).last_tick_at is None
            await worker.tick()
            state = await worker.status()
            assert state.last_tick_at is not None
            assert state.last_tick_at.tzinfo is not None

        _run_async(_test())

    def test_worker_tick_without_services(self):
        worker = FederationScheduledWorker()

        async def _test():
            # Should not crash even with no services
            state = await worker.tick()
            assert state.tick_count == 1
            assert state.last_error is None

        _run_async(_test())

    def test_worker_tick_with_notification_service(self):
        svc = _make_mock_notification_service()
        worker = FederationScheduledWorker(notification_service=svc)

        async def _test():
            await worker.tick()
            svc.dispatch_pending.assert_awaited_once()

        _run_async(_test())

    def test_worker_tick_with_escalation_worker(self):
        esc = _make_mock_escalation_worker()
        worker = FederationScheduledWorker(escalation_worker=esc)

        async def _test():
            await worker.tick()
            esc.tick.assert_awaited_once()

        _run_async(_test())

    def test_worker_tick_error_records_last_error(self):
        svc = _make_mock_notification_service()
        svc.dispatch_pending = AsyncMock(side_effect=RuntimeError("dispatch broke"))
        worker = FederationScheduledWorker(notification_service=svc)

        async def _test():
            state = await worker.tick()
            assert state.last_error is not None
            assert "dispatch broke" in state.last_error

        _run_async(_test())

    def test_worker_tick_does_not_crash_on_error(self):
        esc = _make_mock_escalation_worker()
        esc.tick = AsyncMock(side_effect=RuntimeError("escalation broke"))
        worker = FederationScheduledWorker(escalation_worker=esc)

        async def _test():
            # Should return a state, not raise
            state = await worker.tick()
            assert state.tick_count == 1
            assert "escalation broke" in state.last_error

        _run_async(_test())


# ---------------------------------------------------------------------------
# Lock tests
# ---------------------------------------------------------------------------

class TestFederationScheduledWorkerLock:
    def test_worker_lock_acquired_before_tick(self):
        lock = _make_mock_lock(acquire_return=True)
        svc = _make_mock_notification_service()
        worker = FederationScheduledWorker(
            notification_service=svc,
            distributed_lock=lock,
        )

        async def _test():
            await worker.tick()
            lock.acquire.assert_awaited_once()
            lock.release.assert_awaited_once()
            svc.dispatch_pending.assert_awaited_once()

        _run_async(_test())

    def test_worker_lock_unavailable_sets_error(self):
        lock = _make_mock_lock(acquire_return=False)
        worker = FederationScheduledWorker(distributed_lock=lock)

        async def _test():
            state = await worker.tick()
            assert state.last_error == "Lock unavailable"
            # Lock was attempted but not released since it was not acquired
            lock.acquire.assert_awaited_once()
            lock.release.assert_not_awaited()

        _run_async(_test())
