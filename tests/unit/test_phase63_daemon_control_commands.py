"""Tests for Phase 63 Daemon Control Commands.

Phase 63: Persistent Approval / Control Plane — daemon integration with
control plane store for pause, resume, drain, shutdown, flush_metrics,
release_lock, and health_snapshot commands.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from agent_app.runtime.policy_rollout_federation_notification_control_plane import (
    ControlCommandStatus,
    ControlCommandType,
    ControlPlaneStore,
)
from agent_app.runtime.policy_rollout_federation_notification_retry_daemon import (
    AlertDeliveryRetryDaemon,
    AlertDeliveryRetryDaemonConfig,
)


def _make_daemon_config(**kwargs):
    """Create a daemon config with control plane enabled."""
    defaults = {
        "enabled": True,
        "interval_seconds": 0.1,
        "batch_limit": 10,
        "control_plane_enabled": True,
        "control_plane_db_path": ":memory:",
        "control_command_poll_interval_seconds": 0.1,
        "graceful_shutdown_enabled": True,
        "drain_timeout_seconds": 2.0,
        "cancel_inflight_on_timeout": True,
        "shutdown_timeout_seconds": 2.0,
    }
    defaults.update(kwargs)
    return AlertDeliveryRetryDaemonConfig(**defaults)


def _make_daemon(**kwargs):
    """Create a daemon with minimal dependencies."""
    config = kwargs.pop("config", None)
    if config is None:
        config = _make_daemon_config()
    return AlertDeliveryRetryDaemon(
        scheduler=kwargs.get("scheduler"),
        config=config,
    )


class TestDaemonControlPlaneInit:
    def test_control_plane_stores_initialized(self):
        daemon = _make_daemon()
        # Control plane stores are lazily initialized
        assert daemon._control_plane_store is None
        assert daemon._approval_store is None
        assert daemon._audit_store is None

    def test_control_paused_default_false(self):
        daemon = _make_daemon()
        assert daemon._control_paused is False


class TestDaemonPauseCommand:
    @pytest.mark.asyncio
    async def test_pause_sets_paused_flag(self):
        daemon = _make_daemon()
        await daemon.start()
        try:
            cmd = daemon._control_plane_store.create_command(
                command_id="cmd_pause_001",
                command_type=ControlCommandType.PAUSE,
            )
            daemon._control_plane_store.mark_accepted(cmd.command_id)
            daemon._control_plane_store.mark_running(cmd.command_id)
            daemon._execute_control_command(cmd)
            assert daemon._control_paused is True
            cmd = daemon._control_plane_store.mark_completed(cmd.command_id)
            assert cmd.status == ControlCommandStatus.COMPLETED
        finally:
            await daemon.stop()

    @pytest.mark.asyncio
    async def test_paused_daemon_skips_batch(self):
        daemon = _make_daemon()
        await daemon.start()
        try:
            daemon._control_paused = True
            result = await daemon._run_once_continuous()
            assert result.scanned == 0
            assert result.delivered == 0
        finally:
            await daemon.stop()


class TestDaemonResumeCommand:
    @pytest.mark.asyncio
    async def test_resume_unpauses(self):
        daemon = _make_daemon()
        await daemon.start()
        try:
            daemon._control_paused = True
            cmd = daemon._control_plane_store.create_command(
                command_id="cmd_resume_001",
                command_type=ControlCommandType.RESUME,
            )
            daemon._control_plane_store.mark_accepted(cmd.command_id)
            daemon._control_plane_store.mark_running(cmd.command_id)
            daemon._execute_control_command(cmd)
            assert daemon._control_paused is False
            cmd = daemon._control_plane_store.mark_completed(cmd.command_id)
            assert cmd.status == ControlCommandStatus.COMPLETED
        finally:
            await daemon.stop()


class TestDaemonFlushMetricsCommand:
    @pytest.mark.asyncio
    async def test_flush_metrics_command(self):
        daemon = _make_daemon()
        await daemon.start()
        try:
            daemon.record_metric("test.metric", 42.0)
            cmd = daemon._control_plane_store.create_command(
                command_id="cmd_flush_001",
                command_type=ControlCommandType.FLUSH_METRICS,
            )
            daemon._control_plane_store.mark_accepted(cmd.command_id)
            daemon._control_plane_store.mark_running(cmd.command_id)
            daemon._execute_control_command(cmd)
            cmd = daemon._control_plane_store.mark_completed(cmd.command_id)
            assert cmd.status == ControlCommandStatus.COMPLETED
        finally:
            await daemon.stop()


class TestDaemonHealthSnapshotCommand:
    @pytest.mark.asyncio
    async def test_health_snapshot_command(self):
        daemon = _make_daemon()
        await daemon.start()
        try:
            cmd = daemon._control_plane_store.create_command(
                command_id="cmd_health_001",
                command_type=ControlCommandType.HEALTH_SNAPSHOT,
            )
            daemon._control_plane_store.mark_accepted(cmd.command_id)
            daemon._control_plane_store.mark_running(cmd.command_id)
            daemon._execute_control_command(cmd)
            cmd = daemon._control_plane_store.mark_completed(cmd.command_id)
            assert cmd.status == ControlCommandStatus.COMPLETED
        finally:
            await daemon.stop()


class TestDaemonCommandFailure:
    @pytest.mark.asyncio
    async def test_command_failure_marks_failed(self):
        daemon = _make_daemon()
        await daemon.start()
        try:
            cmd = daemon._control_plane_store.create_command(
                command_id="cmd_fail_001",
                command_type=ControlCommandType.PAUSE,
            )
            daemon._control_plane_store.mark_accepted(cmd.command_id)
            daemon._control_plane_store.mark_running(cmd.command_id)
            error = {"error": "test failure"}
            daemon._control_plane_store.mark_failed(cmd.command_id, error)
            cmd = daemon._control_plane_store.get_command(cmd.command_id)
            assert cmd.status == ControlCommandStatus.FAILED
            assert cmd.error == error
        finally:
            await daemon.stop()


class TestDaemonHealthStatusControlFields:
    @pytest.mark.asyncio
    async def test_health_status_includes_control_fields(self):
        daemon = _make_daemon()
        await daemon.start()
        try:
            status = daemon.get_health_status()
            assert "control_plane_enabled" in status
            assert "control_paused" in status
            assert "last_control_command_id" in status
            assert "last_control_error" in status
            assert status["control_plane_enabled"] is True
            assert status["control_paused"] is False
        finally:
            await daemon.stop()
