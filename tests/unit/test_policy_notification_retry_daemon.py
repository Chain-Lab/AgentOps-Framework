"""Tests for Phase 55 Task 4 — Retry Daemon."""
from __future__ import annotations

import asyncio
import pytest
from datetime import datetime, timezone, timedelta

from agent_app.runtime.policy_rollout_federation_notification_retry_daemon import (
    AlertDeliveryRetryDaemon,
    AlertDeliveryRetryDaemonConfig,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_service import (
    AlertDeliveryRetryRunResult,
    NotificationAlertDeliveryService,
)


class FakeScheduler:
    """Fake scheduler for testing the daemon."""

    def __init__(self, results: list[AlertDeliveryRetryRunResult] | None = None,
                 raise_error: bool = False) -> None:
        self.results = results or [
            AlertDeliveryRetryRunResult(dry_run=False, scanned=0, delivered=0),
        ]
        self.raise_error = raise_error
        self.calls: list[dict[str, Any]] = []

    async def run_once(self, limit: int = 100, dry_run: bool = False) -> AlertDeliveryRetryRunResult:
        self.calls.append({"limit": limit, "dry_run": dry_run})
        if self.raise_error:
            raise RuntimeError("scheduler error")
        if self.results:
            result = self.results.pop(0)
            result.dry_run = dry_run
            return result
        return AlertDeliveryRetryRunResult(dry_run=dry_run, scanned=0, delivered=0)


class FakeAuditLogger:
    """Fake audit logger for testing."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"event_type": event_type, "payload": payload})


class TestRetryDaemon:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        scheduler = FakeScheduler()
        daemon = AlertDeliveryRetryDaemon(scheduler)
        assert daemon.is_running is False
        await daemon.start()
        assert daemon.is_running is True
        await daemon.stop()
        assert daemon.is_running is False

    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        scheduler = FakeScheduler()
        daemon = AlertDeliveryRetryDaemon(scheduler)
        await daemon.start()
        await daemon.start()  # Second start should be no-op
        assert daemon.is_running is True
        await daemon.stop()

    @pytest.mark.asyncio
    async def test_stop_idempotent(self):
        scheduler = FakeScheduler()
        daemon = AlertDeliveryRetryDaemon(scheduler)
        await daemon.stop()  # Stop when not running
        assert daemon.is_running is False
        await daemon.stop()  # Second stop
        assert daemon.is_running is False

    @pytest.mark.asyncio
    async def test_stop_when_running(self):
        scheduler = FakeScheduler()
        daemon = AlertDeliveryRetryDaemon(scheduler)
        await daemon.start()
        assert daemon.is_running is True
        await daemon.stop()
        assert daemon.is_running is False

    @pytest.mark.asyncio
    async def test_run_once_delegates_scheduler(self):
        scheduler = FakeScheduler()
        daemon = AlertDeliveryRetryDaemon(scheduler)
        result = await daemon.run_once(dry_run=True)
        assert len(scheduler.calls) == 1
        assert scheduler.calls[0]["dry_run"] is True
        assert result.dry_run is True

    @pytest.mark.asyncio
    async def test_run_once_with_limit(self):
        scheduler = FakeScheduler()
        daemon = AlertDeliveryRetryDaemon(
            scheduler,
            config=AlertDeliveryRetryDaemonConfig(batch_limit=50),
        )
        await daemon.run_once()
        assert scheduler.calls[0]["limit"] == 50

    @pytest.mark.asyncio
    async def test_daemon_loop_calls_scheduler(self):
        scheduler = FakeScheduler(results=[
            AlertDeliveryRetryRunResult(dry_run=False, scanned=1, delivered=1),
        ])
        daemon = AlertDeliveryRetryDaemon(
            scheduler,
            config=AlertDeliveryRetryDaemonConfig(
                interval_seconds=0.05,
                jitter_seconds=0,
                run_immediately=True,
            ),
        )
        await daemon.start()
        # Wait for at least 2 ticks
        await asyncio.sleep(0.2)
        await daemon.stop()
        # Should have at least 2 calls (initial + at least one loop iteration)
        assert len(scheduler.calls) >= 2

    @pytest.mark.asyncio
    async def test_scheduler_error_continues_loop(self):
        scheduler = FakeScheduler(raise_error=True)
        daemon = AlertDeliveryRetryDaemon(
            scheduler,
            config=AlertDeliveryRetryDaemonConfig(
                interval_seconds=0.05,
                jitter_seconds=0,
                run_immediately=False,
                stop_on_error=False,
            ),
        )
        await daemon.start()
        await asyncio.sleep(0.15)
        await daemon.stop()
        # Should have retried despite errors
        assert len(scheduler.calls) >= 2

    @pytest.mark.asyncio
    async def test_stop_on_error_stops_daemon(self):
        scheduler = FakeScheduler(raise_error=True)
        daemon = AlertDeliveryRetryDaemon(
            scheduler,
            config=AlertDeliveryRetryDaemonConfig(
                interval_seconds=0.05,
                jitter_seconds=0,
                run_immediately=False,
                stop_on_error=True,
            ),
        )
        await daemon.start()
        await asyncio.sleep(0.1)
        # Daemon should have stopped after the error
        assert daemon.is_running is False

    @pytest.mark.asyncio
    async def test_disabled_config_does_not_auto_start(self):
        """Daemon does not auto-start; start() must be called explicitly."""
        scheduler = FakeScheduler()
        config = AlertDeliveryRetryDaemonConfig(enabled=False)
        daemon = AlertDeliveryRetryDaemon(scheduler, config=config)
        assert daemon.is_running is False
        await daemon.start()
        assert daemon.is_running is True
        await daemon.stop()

    @pytest.mark.asyncio
    async def test_no_task_leak_after_stop(self):
        scheduler = FakeScheduler()
        daemon = AlertDeliveryRetryDaemon(
            scheduler,
            config=AlertDeliveryRetryDaemonConfig(
                interval_seconds=0.05,
                jitter_seconds=0,
                run_immediately=False,
            ),
        )
        await daemon.start()
        await daemon.stop()
        assert daemon._task is None

    @pytest.mark.asyncio
    async def test_audit_logger_records_events(self):
        scheduler = FakeScheduler()
        audit = FakeAuditLogger()
        daemon = AlertDeliveryRetryDaemon(scheduler, audit_logger=audit)

        await daemon.start()
        assert any(e["event_type"] == "retry_daemon_started" for e in audit.events)

        await daemon.run_once()
        assert any(e["event_type"] == "retry_daemon_run_completed" for e in audit.events)

        await daemon.stop()
        assert any(e["event_type"] == "retry_daemon_stopped" for e in audit.events)

    @pytest.mark.asyncio
    async def test_audit_logger_records_errors(self):
        scheduler = FakeScheduler(raise_error=True)
        audit = FakeAuditLogger()
        daemon = AlertDeliveryRetryDaemon(
            scheduler,
            config=AlertDeliveryRetryDaemonConfig(stop_on_error=False),
            audit_logger=audit,
        )
        try:
            await daemon.run_once()
        except RuntimeError:
            pass
        assert any(e["event_type"] == "retry_daemon_run_error" for e in audit.events)
