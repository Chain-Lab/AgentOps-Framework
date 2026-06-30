"""Phase 61 Task 5: Prometheus file metrics exporter tests."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from agent_app.runtime.policy_rollout_federation_notification_metrics_exporter import (
    PrometheusFileMetricsExporter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_snapshot():
    """Create a minimal metrics snapshot for testing."""
    lease_snap = MagicMock()
    lease_snap.acquire_attempts = 10
    lease_snap.acquire_successes = 8
    lease_snap.acquire_denied = 2
    lease_snap.renew_attempts = 5
    lease_snap.renew_successes = 5
    lease_snap.release_attempts = 3
    lease_snap.release_successes = 3

    replay_snap = MagicMock()
    replay_snap.attempts = 20
    replay_snap.successes = 18
    replay_snap.failures = 1
    replay_snap.idempotency_hits = 0
    replay_snap.rate_limited = 0
    replay_snap.dead_lettered = 1

    rate_snap = MagicMock()
    rate_snap.checks = 15
    rate_snap.allowed = 12
    rate_snap.denied = 3

    dl_snap = MagicMock()
    dl_snap.evaluated = 5
    dl_snap.dead_lettered = 1
    dl_snap.passed = 4

    lock_snap = MagicMock()
    lock_snap.acquire_attempts = 10
    lock_snap.acquire_successes = 8
    lock_snap.acquire_denied = 2
    lock_snap.renew_attempts = 5
    lock_snap.renew_successes = 5
    lock_snap.release_attempts = 3
    lock_snap.release_successes = 3

    snap = MagicMock()
    snap.acquire = lease_snap
    snap.renew = lease_snap
    snap.release = lease_snap
    snap.replay = replay_snap
    snap.rate_limiter = rate_snap
    snap.dead_letter = dl_snap
    snap.distributed_lock = lock_snap
    return snap


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPrometheusFileMetricsExporter:
    """Tests for PrometheusFileMetricsExporter."""

    def test_export_creates_file(self, tmp_path):
        """export() creates the output file."""
        exporter = PrometheusFileMetricsExporter(str(tmp_path / "metrics.prom"))
        snapshot = make_snapshot()
        import asyncio
        asyncio.run(exporter.export(snapshot))
        assert (tmp_path / "metrics.prom").exists()

    def test_export_atomic_write(self, tmp_path):
        """export() uses atomic write (no partial files)."""
        exporter = PrometheusFileMetricsExporter(str(tmp_path / "metrics.prom"))
        snapshot = make_snapshot()
        import asyncio
        asyncio.run(exporter.export(snapshot))
        # No .tmp file should remain
        assert not (tmp_path / "metrics.prom.tmp").exists()
        assert (tmp_path / "metrics.prom").exists()

    def test_export_outputs_prometheus_format(self, tmp_path):
        """export() outputs valid Prometheus text format."""
        exporter = PrometheusFileMetricsExporter(str(tmp_path / "metrics.prom"))
        snapshot = make_snapshot()
        import asyncio
        asyncio.run(exporter.export(snapshot))
        content = (tmp_path / "metrics.prom").read_text()
        # Should have HELP and TYPE lines
        assert "# HELP" in content
        assert "# TYPE" in content
        # Should have counter values
        assert "agent_notification_lease_acquire_attempts_total" in content

    def test_export_handles_empty_snapshot(self, tmp_path):
        """export() handles snapshot with all zero metrics."""
        snap = MagicMock()
        snap.acquire.attempts = 0
        snap.acquire.successes = 0
        snap.acquire.denied = 0
        snap.renew.attempts = 0
        snap.renew.successes = 0
        snap.release.attempts = 0
        snap.release.successes = 0
        snap.replay.attempts = 0
        snap.replay.successes = 0
        snap.replay.failures = 0
        snap.replay.idempotency_hits = 0
        snap.replay.rate_limited = 0
        snap.replay.dead_lettered = 0
        snap.rate_limiter.checks = 0
        snap.rate_limiter.allowed = 0
        snap.rate_limiter.denied = 0
        snap.dead_letter.evaluated = 0
        snap.dead_letter.dead_lettered = 0
        snap.dead_letter.passed = 0
        snap.distributed_lock.acquire_attempts = 0
        snap.distributed_lock.acquire_successes = 0
        snap.distributed_lock.acquire_denied = 0
        snap.distributed_lock.renew_attempts = 0
        snap.distributed_lock.renew_successes = 0
        snap.distributed_lock.release_attempts = 0
        snap.distributed_lock.release_successes = 0

        exporter = PrometheusFileMetricsExporter(str(tmp_path / "metrics.prom"))
        import asyncio
        asyncio.run(exporter.export(snap))
        # File should exist but be minimal
        content = (tmp_path / "metrics.prom").read_text()
        # With all zeros, no metrics should be emitted
        assert len(content) == 1  # Just trailing newline

    def test_export_overwrites_existing_file(self, tmp_path):
        """export() overwrites existing file atomically."""
        path = tmp_path / "metrics.prom"
        path.write_text("old content")
        exporter = PrometheusFileMetricsExporter(str(path))
        snapshot = make_snapshot()
        import asyncio
        asyncio.run(exporter.export(snapshot))
        content = path.read_text()
        assert "old content" not in content
        assert "agent_notification" in content
