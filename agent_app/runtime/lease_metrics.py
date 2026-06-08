"""Lease backend metrics — lightweight in-process counters.

Phase 16.3: Provides stdlib-only, thread-safe metrics collection for
lease backend operations.  Metrics are opt-in and do NOT imply
exactly-once or distributed observability.  They are local counters
intended for operator visibility and diagnostics.

This is NOT Prometheus, NOT OpenTelemetry, and does NOT export metrics
to external systems.  Exporters can be added later by consuming
``LeaseMetricsSnapshot``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class LeaseOperationMetrics:
    """Counters for a single lease operation type."""

    attempts: int = 0
    successes: int = 0
    failures: int = 0
    denied: int = 0
    exceptions: int = 0


@dataclass
class LeaseMetricsSnapshot:
    """Point-in-time snapshot of all lease metrics.

    Immutable view — callers can store or compare snapshots without
    worrying about concurrent mutation.
    """

    acquire: LeaseOperationMetrics
    renew: LeaseOperationMetrics
    release: LeaseOperationMetrics
    get: LeaseOperationMetrics
    list_expired: LeaseOperationMetrics


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


class LeaseMetrics:
    """Thread-safe in-process lease metrics collector.

    All public methods are safe to call from async contexts.  Uses a
    ``threading.Lock`` for mutation; ``snapshot()`` returns an
    immutable copy so callers get a consistent view.

    Usage::

        metrics = LeaseMetrics()
        # ... after operations ...
        snap = metrics.snapshot()
        print(snap.acquire.successes)
        metrics.reset()
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._acquire = LeaseOperationMetrics()
        self._renew = LeaseOperationMetrics()
        self._release = LeaseOperationMetrics()
        self._get = LeaseOperationMetrics()
        self._list_expired = LeaseOperationMetrics()

    # -- Acquire recording --

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

    # -- Renew recording --

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

    # -- Release recording --

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

    # -- Get recording --

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

    # -- List expired recording --

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

    # -- Snapshot and reset --

    def snapshot(self) -> LeaseMetricsSnapshot:
        """Return an immutable copy of current metrics."""
        with self._lock:
            return LeaseMetricsSnapshot(
                acquire=LeaseOperationMetrics(
                    attempts=self._acquire.attempts,
                    successes=self._acquire.successes,
                    failures=self._acquire.failures,
                    denied=self._acquire.denied,
                    exceptions=self._acquire.exceptions,
                ),
                renew=LeaseOperationMetrics(
                    attempts=self._renew.attempts,
                    successes=self._renew.successes,
                    failures=self._renew.failures,
                    denied=self._renew.denied,
                    exceptions=self._renew.exceptions,
                ),
                release=LeaseOperationMetrics(
                    attempts=self._release.attempts,
                    successes=self._release.successes,
                    failures=self._release.failures,
                    denied=self._release.denied,
                    exceptions=self._release.exceptions,
                ),
                get=LeaseOperationMetrics(
                    attempts=self._get.attempts,
                    successes=self._get.successes,
                    failures=self._get.failures,
                    denied=self._get.denied,
                    exceptions=self._get.exceptions,
                ),
                list_expired=LeaseOperationMetrics(
                    attempts=self._list_expired.attempts,
                    successes=self._list_expired.successes,
                    failures=self._list_expired.failures,
                    denied=self._list_expired.denied,
                    exceptions=self._list_expired.exceptions,
                ),
            )

    def reset(self) -> None:
        """Reset all counters to zero."""
        with self._lock:
            self._acquire = LeaseOperationMetrics()
            self._renew = LeaseOperationMetrics()
            self._release = LeaseOperationMetrics()
            self._get = LeaseOperationMetrics()
            self._list_expired = LeaseOperationMetrics()
