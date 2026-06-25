"""Enhanced metrics — Phase 16.3 lease metrics + Phase 59 extensions.

Phase 59 Task 737: Adds replay, rate limiter, dead letter, and distributed
lock metrics on top of the existing lease metrics foundation.

All metrics are stdlib-only, thread-safe, in-process counters.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class OperationMetrics:
    """Counters for a generic operation type."""

    attempts: int = 0
    successes: int = 0
    failures: int = 0
    denied: int = 0
    exceptions: int = 0


@dataclass
class ReplayMetrics:
    """Counters for DLQ replay operations."""

    attempts: int = 0
    successes: int = 0
    failures: int = 0
    idempotency_hits: int = 0
    rate_limited: int = 0
    dead_lettered: int = 0


@dataclass
class RateLimiterMetrics:
    """Counters for rate limiter operations."""

    checks: int = 0
    allowed: int = 0
    denied: int = 0
    resets: int = 0


@dataclass
class DeadLetterMetrics:
    """Counters for dead letter policy."""

    evaluated: int = 0
    dead_lettered: int = 0
    passed: int = 0


@dataclass
class DistributedLockMetrics:
    """Counters for distributed lock operations."""

    acquire_attempts: int = 0
    acquire_successes: int = 0
    acquire_denied: int = 0
    renew_attempts: int = 0
    renew_successes: int = 0
    renew_denied: int = 0
    release_attempts: int = 0
    release_successes: int = 0


@dataclass
class MetricsSnapshot:
    """Point-in-time snapshot of all metrics."""

    acquire: OperationMetrics
    renew: OperationMetrics
    release: OperationMetrics
    get: OperationMetrics
    list_expired: OperationMetrics
    replay: ReplayMetrics
    rate_limiter: RateLimiterMetrics
    dead_letter: DeadLetterMetrics
    distributed_lock: DistributedLockMetrics


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


class EnhancedMetrics:
    """Thread-safe in-process metrics collector.

    Extends the Phase 16.3 lease metrics with Phase 59 subsystems:
    replay, rate limiter, dead letter policy, and distributed lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Phase 16.3: lease operation metrics
        self._acquire = OperationMetrics()
        self._renew = OperationMetrics()
        self._release = OperationMetrics()
        self._get = OperationMetrics()
        self._list_expired = OperationMetrics()
        # Phase 59: extended metrics
        self._replay = ReplayMetrics()
        self._rate_limiter = RateLimiterMetrics()
        self._dead_letter = DeadLetterMetrics()
        self._distributed_lock = DistributedLockMetrics()

    # -- Lease: Acquire --

    def record_acquire_success(self) -> None:
        with self._lock:
            self._acquire.attempts += 1
            self._acquire.successes += 1

    def record_acquire_denied(self) -> None:
        with self._lock:
            self._acquire.attempts += 1
            self._acquire.denied += 1

    def record_acquire_failure(self) -> None:
        with self._lock:
            self._acquire.attempts += 1
            self._acquire.failures += 1

    def record_acquire_exception(self) -> None:
        with self._lock:
            self._acquire.attempts += 1
            self._acquire.exceptions += 1

    # -- Lease: Renew --

    def record_renew_success(self) -> None:
        with self._lock:
            self._renew.attempts += 1
            self._renew.successes += 1

    def record_renew_failure(self) -> None:
        with self._lock:
            self._renew.attempts += 1
            self._renew.failures += 1

    def record_renew_exception(self) -> None:
        with self._lock:
            self._renew.attempts += 1
            self._renew.exceptions += 1

    # -- Lease: Release --

    def record_release_success(self) -> None:
        with self._lock:
            self._release.attempts += 1
            self._release.successes += 1

    def record_release_failure(self) -> None:
        with self._lock:
            self._release.attempts += 1
            self._release.failures += 1

    def record_release_exception(self) -> None:
        with self._lock:
            self._release.attempts += 1
            self._release.exceptions += 1

    # -- Lease: Get --

    def record_get_success(self) -> None:
        with self._lock:
            self._get.attempts += 1
            self._get.successes += 1

    def record_get_failure(self) -> None:
        with self._lock:
            self._get.attempts += 1
            self._get.failures += 1

    def record_get_exception(self) -> None:
        with self._lock:
            self._get.attempts += 1
            self._get.exceptions += 1

    # -- Lease: List expired --

    def record_list_expired_success(self) -> None:
        with self._lock:
            self._list_expired.attempts += 1
            self._list_expired.successes += 1

    def record_list_expired_failure(self) -> None:
        with self._lock:
            self._list_expired.attempts += 1
            self._list_expired.failures += 1

    def record_list_expired_exception(self) -> None:
        with self._lock:
            self._list_expired.attempts += 1
            self._list_expired.exceptions += 1

    # -- Phase 59: Replay metrics --

    def record_replay_attempt(self) -> None:
        with self._lock:
            self._replay.attempts += 1

    def record_replay_success(self) -> None:
        with self._lock:
            self._replay.successes += 1

    def record_replay_failure(self) -> None:
        with self._lock:
            self._replay.failures += 1

    def record_replay_idempotency_hit(self) -> None:
        with self._lock:
            self._replay.idempotency_hits += 1

    def record_replay_rate_limited(self) -> None:
        with self._lock:
            self._replay.rate_limited += 1

    def record_replay_dead_lettered(self) -> None:
        with self._lock:
            self._replay.dead_lettered += 1

    # -- Phase 59: Rate limiter metrics --

    def record_rate_limiter_check(self) -> None:
        with self._lock:
            self._rate_limiter.checks += 1

    def record_rate_limiter_allowed(self) -> None:
        with self._lock:
            self._rate_limiter.allowed += 1

    def record_rate_limiter_denied(self) -> None:
        with self._lock:
            self._rate_limiter.denied += 1

    def record_rate_limiter_reset(self) -> None:
        with self._lock:
            self._rate_limiter.resets += 1

    # -- Phase 59: Dead letter metrics --

    def record_dead_letter_evaluated(self) -> None:
        with self._lock:
            self._dead_letter.evaluated += 1

    def record_dead_letter_triggered(self) -> None:
        with self._lock:
            self._dead_letter.dead_lettered += 1

    def record_dead_letter_passed(self) -> None:
        with self._lock:
            self._dead_letter.passed += 1

    # -- Phase 59: Distributed lock metrics --

    def record_lock_acquire_attempt(self) -> None:
        with self._lock:
            self._distributed_lock.acquire_attempts += 1

    def record_lock_acquire_success(self) -> None:
        with self._lock:
            self._distributed_lock.acquire_attempts += 1
            self._distributed_lock.acquire_successes += 1

    def record_lock_acquire_denied(self) -> None:
        with self._lock:
            self._distributed_lock.acquire_attempts += 1
            self._distributed_lock.acquire_denied += 1

    def record_lock_renew_attempt(self) -> None:
        with self._lock:
            self._distributed_lock.renew_attempts += 1

    def record_lock_renew_success(self) -> None:
        with self._lock:
            self._distributed_lock.renew_attempts += 1
            self._distributed_lock.renew_successes += 1

    def record_lock_renew_denied(self) -> None:
        with self._lock:
            self._distributed_lock.renew_attempts += 1
            self._distributed_lock.renew_denied += 1

    def record_lock_release_attempt(self) -> None:
        with self._lock:
            self._distributed_lock.release_attempts += 1

    def record_lock_release_success(self) -> None:
        with self._lock:
            self._distributed_lock.release_attempts += 1
            self._distributed_lock.release_successes += 1

    # -- Snapshot and reset --

    def snapshot(self) -> MetricsSnapshot:
        """Return an immutable copy of current metrics."""
        with self._lock:
            return MetricsSnapshot(
                acquire=OperationMetrics(
                    attempts=self._acquire.attempts,
                    successes=self._acquire.successes,
                    failures=self._acquire.failures,
                    denied=self._acquire.denied,
                    exceptions=self._acquire.exceptions,
                ),
                renew=OperationMetrics(
                    attempts=self._renew.attempts,
                    successes=self._renew.successes,
                    failures=self._renew.failures,
                    denied=self._renew.denied,
                    exceptions=self._renew.exceptions,
                ),
                release=OperationMetrics(
                    attempts=self._release.attempts,
                    successes=self._release.successes,
                    failures=self._release.failures,
                    denied=self._release.denied,
                    exceptions=self._release.exceptions,
                ),
                get=OperationMetrics(
                    attempts=self._get.attempts,
                    successes=self._get.successes,
                    failures=self._get.failures,
                    denied=self._get.denied,
                    exceptions=self._get.exceptions,
                ),
                list_expired=OperationMetrics(
                    attempts=self._list_expired.attempts,
                    successes=self._list_expired.successes,
                    failures=self._list_expired.failures,
                    denied=self._list_expired.denied,
                    exceptions=self._list_expired.exceptions,
                ),
                replay=ReplayMetrics(
                    attempts=self._replay.attempts,
                    successes=self._replay.successes,
                    failures=self._replay.failures,
                    idempotency_hits=self._replay.idempotency_hits,
                    rate_limited=self._replay.rate_limited,
                    dead_lettered=self._replay.dead_lettered,
                ),
                rate_limiter=RateLimiterMetrics(
                    checks=self._rate_limiter.checks,
                    allowed=self._rate_limiter.allowed,
                    denied=self._rate_limiter.denied,
                    resets=self._rate_limiter.resets,
                ),
                dead_letter=DeadLetterMetrics(
                    evaluated=self._dead_letter.evaluated,
                    dead_lettered=self._dead_letter.dead_lettered,
                    passed=self._dead_letter.passed,
                ),
                distributed_lock=DistributedLockMetrics(
                    acquire_attempts=self._distributed_lock.acquire_attempts,
                    acquire_successes=self._distributed_lock.acquire_successes,
                    acquire_denied=self._distributed_lock.acquire_denied,
                    renew_attempts=self._distributed_lock.renew_attempts,
                    renew_successes=self._distributed_lock.renew_successes,
                    renew_denied=self._distributed_lock.renew_denied,
                    release_attempts=self._distributed_lock.release_attempts,
                    release_successes=self._distributed_lock.release_successes,
                ),
            )

    def reset(self) -> None:
        """Reset all counters to zero."""
        with self._lock:
            self._acquire = OperationMetrics()
            self._renew = OperationMetrics()
            self._release = OperationMetrics()
            self._get = OperationMetrics()
            self._list_expired = OperationMetrics()
            self._replay = ReplayMetrics()
            self._rate_limiter = RateLimiterMetrics()
            self._dead_letter = DeadLetterMetrics()
            self._distributed_lock = DistributedLockMetrics()
