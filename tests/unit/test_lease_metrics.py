"""Tests for Phase 16.3 lease metrics."""

from __future__ import annotations

import threading

import pytest

from agent_app.runtime.lease_metrics import (
    LeaseMetrics,
    LeaseMetricsSnapshot,
    LeaseOperationMetrics,
)


class TestLeaseMetrics:
    """Tests for the LeaseMetrics collector."""

    @pytest.fixture
    def metrics(self):
        return LeaseMetrics()

    def test_initial_snapshot_is_zero(self, metrics):
        """Fresh metrics have all counters at zero."""
        snap = metrics.snapshot()
        assert snap.acquire.attempts == 0
        assert snap.acquire.successes == 0
        assert snap.acquire.denied == 0
        assert snap.acquire.failures == 0
        assert snap.acquire.exceptions == 0
        assert snap.renew.attempts == 0
        assert snap.release.attempts == 0
        assert snap.get.attempts == 0
        assert snap.list_expired.attempts == 0

    def test_record_acquire_success(self, metrics):
        metrics.record_acquire_success()
        snap = metrics.snapshot()
        assert snap.acquire.attempts == 1
        assert snap.acquire.successes == 1
        assert snap.acquire.denied == 0
        assert snap.acquire.failures == 0
        assert snap.acquire.exceptions == 0

    def test_record_acquire_denied(self, metrics):
        metrics.record_acquire_denied()
        snap = metrics.snapshot()
        assert snap.acquire.attempts == 1
        assert snap.acquire.denied == 1
        assert snap.acquire.successes == 0

    def test_record_acquire_failure(self, metrics):
        metrics.record_acquire_failure()
        snap = metrics.snapshot()
        assert snap.acquire.attempts == 1
        assert snap.acquire.failures == 1

    def test_record_acquire_exception(self, metrics):
        metrics.record_acquire_exception()
        snap = metrics.snapshot()
        assert snap.acquire.attempts == 1
        assert snap.acquire.exceptions == 1

    def test_record_renew_success(self, metrics):
        metrics.record_renew_success()
        snap = metrics.snapshot()
        assert snap.renew.attempts == 1
        assert snap.renew.successes == 1

    def test_record_renew_failure(self, metrics):
        metrics.record_renew_failure()
        snap = metrics.snapshot()
        assert snap.renew.attempts == 1
        assert snap.renew.failures == 1

    def test_record_renew_exception(self, metrics):
        metrics.record_renew_exception()
        snap = metrics.snapshot()
        assert snap.renew.attempts == 1
        assert snap.renew.exceptions == 1

    def test_record_release_success(self, metrics):
        metrics.record_release_success()
        snap = metrics.snapshot()
        assert snap.release.attempts == 1
        assert snap.release.successes == 1

    def test_record_release_failure(self, metrics):
        metrics.record_release_failure()
        snap = metrics.snapshot()
        assert snap.release.attempts == 1
        assert snap.release.failures == 1

    def test_record_release_exception(self, metrics):
        metrics.record_release_exception()
        snap = metrics.snapshot()
        assert snap.release.attempts == 1
        assert snap.release.exceptions == 1

    def test_reset_works(self, metrics):
        """Reset clears all counters."""
        metrics.record_acquire_success()
        metrics.record_renew_failure()
        metrics.record_release_exception()
        metrics.reset()
        snap = metrics.snapshot()
        assert snap.acquire.attempts == 0
        assert snap.renew.attempts == 0
        assert snap.release.attempts == 0

    def test_snapshot_is_independent(self, metrics):
        """Snapshot is a copy — mutating it doesn't affect metrics."""
        metrics.record_acquire_success()
        snap1 = metrics.snapshot()
        snap1.acquire.successes = 999
        snap2 = metrics.snapshot()
        assert snap2.acquire.successes == 1

    def test_multiple_operations_accumulate(self, metrics):
        """Multiple operations accumulate correctly."""
        metrics.record_acquire_success()
        metrics.record_acquire_denied()
        metrics.record_acquire_success()
        snap = metrics.snapshot()
        assert snap.acquire.attempts == 3
        assert snap.acquire.successes == 2
        assert snap.acquire.denied == 1

    def test_thread_safety_basic(self, metrics):
        """Basic thread safety: concurrent recordings don't crash."""
        errors = []

        def _record():
            try:
                for _ in range(100):
                    metrics.record_acquire_success()
                    metrics.record_renew_success()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_record) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        snap = metrics.snapshot()
        assert snap.acquire.attempts == 400
        assert snap.renew.attempts == 400
