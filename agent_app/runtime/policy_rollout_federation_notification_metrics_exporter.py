"""Prometheus text-format metrics exporter for notification daemon.

Phase 61 Task 5: Lightweight file export without Prometheus client dependency.
Outputs Prometheus exposition format for scraping or file_sd targets.
"""
from __future__ import annotations

import os
from typing import Any


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------


class PrometheusFileMetricsExporter:
    """Export EnhancedMetrics snapshot to a Prometheus text-format file.

    Atomic write: writes to a temp file then renames to avoid partial
    writes on crash.
    """

    def __init__(self, path: str) -> None:
        self._path = path

    async def export(self, snapshot: Any) -> None:
        """Export metrics snapshot to file in Prometheus text format.

        Args:
            snapshot: MetricsSnapshot from EnhancedMetrics.snapshot().
        """
        lines = self._render(snapshot)
        content = "\n".join(lines) + "\n"
        tmp_path = f"{self._path}.tmp"
        try:
            with open(tmp_path, "w") as fh:
                fh.write(content)
            os.replace(tmp_path, self._path)
        except OSError:
            pass  # Best-effort

    def _render(self, snapshot: Any) -> list[str]:
        """Render snapshot to Prometheus text format lines."""
        lines: list[str] = []

        # Lease acquire metrics
        if snapshot.acquire.attempts:
            lines.append("# HELP agent_notification_lease_acquire_attempts_total Total lease acquire attempts")
            lines.append("# TYPE agent_notification_lease_acquire_attempts_total counter")
            lines.append(f"agent_notification_lease_acquire_attempts_total {snapshot.acquire.attempts}")
        if snapshot.acquire.successes:
            lines.append("# HELP agent_notification_lease_acquire_successes_total Successful lease acquires")
            lines.append("# TYPE agent_notification_lease_acquire_successes_total counter")
            lines.append(f"agent_notification_lease_acquire_successes_total {snapshot.acquire.successes}")
        if snapshot.acquire.denied:
            lines.append("# HELP agent_notification_lease_acquire_denied_total Denied lease acquires")
            lines.append("# TYPE agent_notification_lease_acquire_denied_total counter")
            lines.append(f"agent_notification_lease_acquire_denied_total {snapshot.acquire.denied}")

        # Lease renew metrics
        if snapshot.renew.attempts:
            lines.append("# HELP agent_notification_lease_renew_attempts_total Total lease renew attempts")
            lines.append("# TYPE agent_notification_lease_renew_attempts_total counter")
            lines.append(f"agent_notification_lease_renew_attempts_total {snapshot.renew.attempts}")
        if snapshot.renew.successes:
            lines.append("# HELP agent_notification_lease_renew_successes_total Successful lease renews")
            lines.append("# TYPE agent_notification_lease_renew_successes_total counter")
            lines.append(f"agent_notification_lease_renew_successes_total {snapshot.renew.successes}")

        # Lease release metrics
        if snapshot.release.attempts:
            lines.append("# HELP agent_notification_lease_release_attempts_total Total lease release attempts")
            lines.append("# TYPE agent_notification_lease_release_attempts_total counter")
            lines.append(f"agent_notification_lease_release_attempts_total {snapshot.release.attempts}")
        if snapshot.release.successes:
            lines.append("# HELP agent_notification_lease_release_successes_total Successful lease releases")
            lines.append("# TYPE agent_notification_lease_release_successes_total counter")
            lines.append(f"agent_notification_lease_release_successes_total {snapshot.release.successes}")

        # Replay metrics
        if snapshot.replay.attempts:
            lines.append("# HELP agent_notification_replay_attempts_total Total replay attempts")
            lines.append("# TYPE agent_notification_replay_attempts_total counter")
            lines.append(f"agent_notification_replay_attempts_total {snapshot.replay.attempts}")
        if snapshot.replay.successes:
            lines.append("# HELP agent_notification_replay_successes_total Successful replays")
            lines.append("# TYPE agent_notification_replay_successes_total counter")
            lines.append(f"agent_notification_replay_successes_total {snapshot.replay.successes}")
        if snapshot.replay.failures:
            lines.append("# HELP agent_notification_replay_failures_total Failed replays")
            lines.append("# TYPE agent_notification_replay_failures_total counter")
            lines.append(f"agent_notification_replay_failures_total {snapshot.replay.failures}")
        if snapshot.replay.idempotency_hits:
            lines.append("# HELP agent_notification_replay_idempotency_hits_total Replay idempotency hits")
            lines.append("# TYPE agent_notification_replay_idempotency_hits_total counter")
            lines.append(f"agent_notification_replay_idempotency_hits_total {snapshot.replay.idempotency_hits}")
        if snapshot.replay.rate_limited:
            lines.append("# HELP agent_notification_replay_rate_limited_total Rate-limited replays")
            lines.append("# TYPE agent_notification_replay_rate_limited_total counter")
            lines.append(f"agent_notification_replay_rate_limited_total {snapshot.replay.rate_limited}")
        if snapshot.replay.dead_lettered:
            lines.append("# HELP agent_notification_replay_dead_lettered_total Dead-lettered replays")
            lines.append("# TYPE agent_notification_replay_dead_lettered_total counter")
            lines.append(f"agent_notification_replay_dead_lettered_total {snapshot.replay.dead_lettered}")

        # Rate limiter metrics
        if snapshot.rate_limiter.checks:
            lines.append("# HELP agent_notification_rate_limiter_checks_total Rate limiter checks")
            lines.append("# TYPE agent_notification_rate_limiter_checks_total counter")
            lines.append(f"agent_notification_rate_limiter_checks_total {snapshot.rate_limiter.checks}")
        if snapshot.rate_limiter.allowed:
            lines.append("# HELP agent_notification_rate_limiter_allowed_total Allowed by rate limiter")
            lines.append("# TYPE agent_notification_rate_limiter_allowed_total counter")
            lines.append(f"agent_notification_rate_limiter_allowed_total {snapshot.rate_limiter.allowed}")
        if snapshot.rate_limiter.denied:
            lines.append("# HELP agent_notification_rate_limiter_denied_total Denied by rate limiter")
            lines.append("# TYPE agent_notification_rate_limiter_denied_total counter")
            lines.append(f"agent_notification_rate_limiter_denied_total {snapshot.rate_limiter.denied}")

        # Dead letter metrics
        if snapshot.dead_letter.evaluated:
            lines.append("# HELP agent_notification_dead_letter_evaluated_total Dead letter evaluations")
            lines.append("# TYPE agent_notification_dead_letter_evaluated_total counter")
            lines.append(f"agent_notification_dead_letter_evaluated_total {snapshot.dead_letter.evaluated}")
        if snapshot.dead_letter.dead_lettered:
            lines.append("# HELP agent_notification_dead_lettered_total Items sent to dead letter")
            lines.append("# TYPE agent_notification_dead_lettered_total counter")
            lines.append(f"agent_notification_dead_lettered_total {snapshot.dead_letter.dead_lettered}")
        if snapshot.dead_letter.passed:
            lines.append("# HELP agent_notification_dead_letter_passed_total Items passed dead letter check")
            lines.append("# TYPE agent_notification_dead_letter_passed_total counter")
            lines.append(f"agent_notification_dead_letter_passed_total {snapshot.dead_letter.passed}")

        # Distributed lock metrics
        if snapshot.distributed_lock.acquire_attempts:
            lines.append("# HELP agent_notification_lock_acquire_attempts_total Lock acquire attempts")
            lines.append("# TYPE agent_notification_lock_acquire_attempts_total counter")
            lines.append(f"agent_notification_lock_acquire_attempts_total {snapshot.distributed_lock.acquire_attempts}")
        if snapshot.distributed_lock.acquire_successes:
            lines.append("# HELP agent_notification_lock_acquire_successes_total Successful lock acquires")
            lines.append("# TYPE agent_notification_lock_acquire_successes_total counter")
            lines.append(f"agent_notification_lock_acquire_successes_total {snapshot.distributed_lock.acquire_successes}")
        if snapshot.distributed_lock.acquire_denied:
            lines.append("# HELP agent_notification_lock_acquire_denied_total Denied lock acquires")
            lines.append("# TYPE agent_notification_lock_acquire_denied_total counter")
            lines.append(f"agent_notification_lock_acquire_denied_total {snapshot.distributed_lock.acquire_denied}")
        if snapshot.distributed_lock.renew_attempts:
            lines.append("# HELP agent_notification_lock_renew_attempts_total Lock renew attempts")
            lines.append("# TYPE agent_notification_lock_renew_attempts_total counter")
            lines.append(f"agent_notification_lock_renew_attempts_total {snapshot.distributed_lock.renew_attempts}")
        if snapshot.distributed_lock.renew_successes:
            lines.append("# HELP agent_notification_lock_renew_successes_total Successful lock renews")
            lines.append("# TYPE agent_notification_lock_renew_successes_total counter")
            lines.append(f"agent_notification_lock_renew_successes_total {snapshot.distributed_lock.renew_successes}")
        if snapshot.distributed_lock.release_attempts:
            lines.append("# HELP agent_notification_lock_release_attempts_total Lock release attempts")
            lines.append("# TYPE agent_notification_lock_release_attempts_total counter")
            lines.append(f"agent_notification_lock_release_attempts_total {snapshot.distributed_lock.release_attempts}")
        if snapshot.distributed_lock.release_successes:
            lines.append("# HELP agent_notification_lock_release_successes_total Successful lock releases")
            lines.append("# TYPE agent_notification_lock_release_successes_total counter")
            lines.append(f"agent_notification_lock_release_successes_total {snapshot.distributed_lock.release_successes}")

        return lines
