"""Tests for enhanced metrics (Phase 59 Task 737)."""
from __future__ import annotations

import threading

import pytest

from agent_app.runtime.policy_rollout_federation_notification_metrics_enhanced import (
    DeadLetterMetrics,
    DistributedLockMetrics,
    EnhancedMetrics,
    MetricsSnapshot,
    OperationMetrics,
    RateLimiterMetrics,
    ReplayMetrics,
)


class TestLeaseMetricsBackwardCompatibility:
    """Phase 16.3 lease metrics still work."""

    def test_acquire_success(self):
        m = EnhancedMetrics()
        m.record_acquire_success()
        snap = m.snapshot()
        assert snap.acquire.successes == 1
        assert snap.acquire.attempts == 1

    def test_renew_failure(self):
        m = EnhancedMetrics()
        m.record_renew_failure()
        snap = m.snapshot()
        assert snap.renew.failures == 1

    def test_release_exception(self):
        m = EnhancedMetrics()
        m.record_release_exception()
        snap = m.snapshot()
        assert snap.release.exceptions == 1

    def test_reset_clears_all(self):
        m = EnhancedMetrics()
        m.record_acquire_success()
        m.record_replay_attempt()
        m.reset()
        snap = m.snapshot()
        assert snap.acquire.attempts == 0
        assert snap.replay.attempts == 0


class TestReplayMetrics:
    """Phase 59 replay metrics."""

    def test_replay_attempt(self):
        m = EnhancedMetrics()
        m.record_replay_attempt()
        snap = m.snapshot()
        assert snap.replay.attempts == 1

    def test_replay_success(self):
        m = EnhancedMetrics()
        m.record_replay_success()
        snap = m.snapshot()
        assert snap.replay.successes == 1

    def test_replay_failure(self):
        m = EnhancedMetrics()
        m.record_replay_failure()
        snap = m.snapshot()
        assert snap.replay.failures == 1

    def test_replay_idempotency_hit(self):
        m = EnhancedMetrics()
        m.record_replay_idempotency_hit()
        snap = m.snapshot()
        assert snap.replay.idempotency_hits == 1

    def test_replay_rate_limited(self):
        m = EnhancedMetrics()
        m.record_replay_rate_limited()
        snap = m.snapshot()
        assert snap.replay.rate_limited == 1

    def test_replay_dead_lettered(self):
        m = EnhancedMetrics()
        m.record_replay_dead_lettered()
        snap = m.snapshot()
        assert snap.replay.dead_lettered == 1

    def test_full_replay_lifecycle(self):
        m = EnhancedMetrics()
        m.record_replay_attempt()
        m.record_replay_idempotency_hit()
        m.record_replay_attempt()
        m.record_replay_success()
        m.record_replay_attempt()
        m.record_replay_rate_limited()
        m.record_replay_attempt()
        m.record_replay_dead_lettered()
        snap = m.snapshot()
        assert snap.replay.attempts == 4
        assert snap.replay.successes == 1
        assert snap.replay.idempotency_hits == 1
        assert snap.replay.rate_limited == 1
        assert snap.replay.dead_lettered == 1


class TestRateLimiterMetrics:
    """Phase 59 rate limiter metrics."""

    def test_check_recorded(self):
        m = EnhancedMetrics()
        m.record_rate_limiter_check()
        snap = m.snapshot()
        assert snap.rate_limiter.checks == 1

    def test_allowed_recorded(self):
        m = EnhancedMetrics()
        m.record_rate_limiter_allowed()
        snap = m.snapshot()
        assert snap.rate_limiter.allowed == 1

    def test_denied_recorded(self):
        m = EnhancedMetrics()
        m.record_rate_limiter_denied()
        snap = m.snapshot()
        assert snap.rate_limiter.denied == 1

    def test_reset_recorded(self):
        m = EnhancedMetrics()
        m.record_rate_limiter_reset()
        snap = m.snapshot()
        assert snap.rate_limiter.resets == 1


class TestDeadLetterMetrics:
    """Phase 59 dead letter metrics."""

    def test_evaluated(self):
        m = EnhancedMetrics()
        m.record_dead_letter_evaluated()
        snap = m.snapshot()
        assert snap.dead_letter.evaluated == 1

    def test_dead_lettered(self):
        m = EnhancedMetrics()
        m.record_dead_letter_triggered()
        snap = m.snapshot()
        assert snap.dead_letter.dead_lettered == 1

    def test_passed(self):
        m = EnhancedMetrics()
        m.record_dead_letter_passed()
        snap = m.snapshot()
        assert snap.dead_letter.passed == 1


class TestDistributedLockMetrics:
    """Phase 59 distributed lock metrics."""

    def test_acquire_success(self):
        m = EnhancedMetrics()
        m.record_lock_acquire_success()
        snap = m.snapshot()
        assert snap.distributed_lock.acquire_successes == 1
        assert snap.distributed_lock.acquire_attempts == 1

    def test_acquire_denied(self):
        m = EnhancedMetrics()
        m.record_lock_acquire_denied()
        snap = m.snapshot()
        assert snap.distributed_lock.acquire_denied == 1
        assert snap.distributed_lock.acquire_attempts == 1

    def test_renew_success(self):
        m = EnhancedMetrics()
        m.record_lock_renew_success()
        snap = m.snapshot()
        assert snap.distributed_lock.renew_successes == 1

    def test_renew_denied(self):
        m = EnhancedMetrics()
        m.record_lock_renew_denied()
        snap = m.snapshot()
        assert snap.distributed_lock.renew_denied == 1

    def test_release_success(self):
        m = EnhancedMetrics()
        m.record_lock_release_success()
        snap = m.snapshot()
        assert snap.distributed_lock.release_successes == 1
        assert snap.distributed_lock.release_attempts == 1


class TestThreadSafety:
    """Thread safety of metrics."""

    def test_concurrent_records(self):
        m = EnhancedMetrics()
        errors = []

        def recorder():
            try:
                for _ in range(100):
                    m.record_replay_attempt()
                    m.record_replay_success()
                    m.record_rate_limiter_check()
                    m.record_dead_letter_evaluated()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=recorder) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        snap = m.snapshot()
        assert snap.replay.attempts == 400
        assert snap.replay.successes == 400
        assert snap.rate_limiter.checks == 400
        assert snap.dead_letter.evaluated == 400
