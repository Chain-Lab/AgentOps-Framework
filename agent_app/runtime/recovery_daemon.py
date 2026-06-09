"""Recovery daemon — Phase 17.

Provides automatic, policy-driven recovery of failed/stale DAG workflow runs.
The daemon is conservative by default (disabled, dry-run, single-threaded)
and must be explicitly started via CLI or API.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from agent_app.runtime.recovery_models import (
    AutoRecoveryPolicy,
    RecoveryCandidate,
    RecoveryCandidateReason,
    RecoveryDaemonTickResult,
    RecoveryRecommendation,
    RecoveryScanConfig,
    RecoveryScanResult,
)
from agent_app.runtime.recovery_scanner import RecoveryScanner
from agent_app.runtime.recovery_service import RecoveryService

if TYPE_CHECKING:
    from agent_app.governance.audit import AuditLogger

logger = logging.getLogger(__name__)

# Status-to-scanner-flag mapping
_STATUS_TO_SCAN_FLAGS: dict[str, tuple[str, ...]] = {
    "failed": ("include_failed",),
    "running": ("include_running",),
    "pending": ("include_running",),
    "started": ("include_running",),
    "compensating": ("include_compensating",),
    "compensation_started": ("include_compensating",),
    "completed": ("include_completed",),
}


class RecoveryDaemon:
    """Automatic recovery daemon with policy-driven selection.

    The daemon scans for recovery candidates on a configurable interval
    and selectively recovers runs that match the auto-recovery policy.

    The daemon is **not** started automatically.  Call ``run_forever()``
    or ``run_once()`` explicitly.

    Args:
        scanner: RecoveryScanner for finding candidates.
        recovery_service: RecoveryService for performing recoveries.
        policy: AutoRecoveryPolicy controlling daemon behavior.
        audit_logger: Optional audit logger for daemon events.
    """

    def __init__(
        self,
        scanner: RecoveryScanner,
        recovery_service: RecoveryService,
        policy: AutoRecoveryPolicy,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._scanner = scanner
        self._recovery_service = recovery_service
        self._policy = policy
        self._audit_logger = audit_logger
        self._concurrency_semaphore: asyncio.Semaphore | None = None
        self._running = False

    @property
    def policy(self) -> AutoRecoveryPolicy:
        """Current auto-recovery policy."""
        return self._policy

    @policy.setter
    def policy(self, value: AutoRecoveryPolicy) -> None:
        self._policy = value

    async def run_once(self) -> RecoveryDaemonTickResult:
        """Execute a single scan-and-recover cycle.

        Returns:
            RecoveryDaemonTickResult with cycle outcome.
        """
        if self._concurrency_semaphore is None:
            self._concurrency_semaphore = asyncio.Semaphore(
                self._policy.max_concurrent_recoveries
            )

        tick_start = time.monotonic()
        await self._audit("recovery.daemon_tick_started", None, None, {
            "dry_run": self._policy.dry_run,
        })

        # -- Step 1: Scan --
        scan_config = self._build_scan_config()
        try:
            scan_result = await self._scanner.scan(scan_config)
        except Exception as exc:
            logger.error("Recovery scan failed: %s", exc)
            await self._audit("recovery.daemon_tick_completed", None, None, {
                "scanned_count": 0,
                "selected_count": 0,
                "recovered_count": 0,
                "dry_run": self._policy.dry_run,
                "error": str(exc),
            })
            return RecoveryDaemonTickResult(
                scanned_count=0,
                dry_run=self._policy.dry_run,
                failures=[{"error": f"Scan failed: {exc}"}],
            )

        candidates = scan_result.candidates

        # -- Step 2: Apply candidate limit --
        candidates = candidates[: self._policy.max_candidates_per_scan]

        # -- Step 3: Select candidates for recovery --
        selected: list[RecoveryCandidate] = []
        skipped: list[dict[str, Any]] = []

        for candidate in candidates:
            skip_reason = self._should_skip(candidate)
            if skip_reason is not None:
                skipped.append({
                    "run_id": candidate.run_id,
                    "reason": skip_reason,
                    "recommendation": candidate.recommendation.value,
                })
                await self._audit(
                    "recovery.daemon_candidate_skipped",
                    candidate.run_id,
                    None,
                    {
                        "reason": skip_reason,
                        "recommendation": candidate.recommendation.value,
                    },
                )
            else:
                selected.append(candidate)
                await self._audit(
                    "recovery.daemon_candidate_selected",
                    candidate.run_id,
                    None,
                    {
                        "workflow": candidate.workflow_name,
                        "recommendation": candidate.recommendation.value,
                        "reasons": [r.value for r in candidate.reasons],
                    },
                )

        # -- Step 4: Recover --
        recovered_ids: list[str] = []
        failures: list[dict[str, Any]] = []
        semaphore = self._concurrency_semaphore

        async def _recover_one(candidate: RecoveryCandidate) -> None:
            """Recover a single candidate with concurrency limiting."""
            async with semaphore:
                if self._policy.dry_run:
                    recovered_ids.append(candidate.run_id)
                    await self._audit(
                        "recovery.daemon_dry_run_selected",
                        candidate.run_id,
                        None,
                        {
                            "workflow": candidate.workflow_name,
                            "recommendation": candidate.recommendation.value,
                            "reasons": [r.value for r in candidate.reasons],
                        },
                    )
                    return

                # Actual recovery via RecoveryService
                await self._audit(
                    "recovery.daemon_recovery_started",
                    candidate.run_id,
                    None,
                    {
                        "workflow": candidate.workflow_name,
                        "recommendation": candidate.recommendation.value,
                    },
                )
                try:
                    result = await self._recovery_service.recover_run(
                        workflow=candidate.workflow_name or "",
                        run_id=candidate.run_id,
                        recovered_by="auto-recovery-daemon",
                    )
                    if result.recovered:
                        recovered_ids.append(candidate.run_id)
                        await self._audit(
                            "recovery.daemon_recovery_completed",
                            candidate.run_id,
                            None,
                            {
                                "workflow": candidate.workflow_name,
                                "status": result.status,
                            },
                        )
                    else:
                        failures.append({
                            "run_id": candidate.run_id,
                            "error": result.error,
                            "status": result.status,
                        })
                        await self._audit(
                            "recovery.daemon_recovery_failed",
                            candidate.run_id,
                            None,
                            {
                                "error": result.error,
                                "status": result.status,
                            },
                        )
                except Exception as exc:
                    failures.append({
                        "run_id": candidate.run_id,
                        "error": {"type": "recovery_exception", "message": str(exc)},
                    })
                    await self._audit(
                        "recovery.daemon_recovery_failed",
                        candidate.run_id,
                        None,
                        {"error": str(exc)},
                    )

        # Limit recoveries per scan
        to_recover = selected[: self._policy.max_recoveries_per_scan]

        # Run recoveries concurrently (bounded by semaphore)
        if to_recover:
            await asyncio.gather(*[_recover_one(c) for c in to_recover])

        tick_result = RecoveryDaemonTickResult(
            scanned_count=scan_result.total_scanned,
            selected_count=len(selected),
            recovered_count=len(recovered_ids),
            skipped_count=len(skipped),
            failed_count=len(failures),
            dry_run=self._policy.dry_run,
            selected_run_ids=[c.run_id for c in selected],
            recovered_run_ids=recovered_ids,
            skipped=skipped,
            failures=failures,
        )

        elapsed = time.monotonic() - tick_start
        await self._audit("recovery.daemon_tick_completed", None, None, {
            "scanned_count": tick_result.scanned_count,
            "selected_count": tick_result.selected_count,
            "recovered_count": tick_result.recovered_count,
            "skipped_count": tick_result.skipped_count,
            "failed_count": tick_result.failed_count,
            "dry_run": tick_result.dry_run,
            "elapsed_seconds": round(elapsed, 3),
        })

        return tick_result

    async def run_forever(
        self,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Run scan cycles until stopped.

        Cycles run at ``policy.interval_seconds`` intervals.  Stops when
        *stop_event* is set or on KeyboardInterrupt.

        Args:
            stop_event: Optional asyncio.Event to signal shutdown.
        """
        self._running = True
        self._concurrency_semaphore = asyncio.Semaphore(
            self._policy.max_concurrent_recoveries
        )
        await self._audit("recovery.daemon_started", None, None, {
            "interval_seconds": self._policy.interval_seconds,
            "dry_run": self._policy.dry_run,
            "max_concurrent_recoveries": self._policy.max_concurrent_recoveries,
        })

        try:
            while self._running:
                cycle_start = time.monotonic()
                try:
                    result = await self.run_once()
                    logger.info(
                        "Recovery daemon tick: scanned=%d selected=%d "
                        "recovered=%d skipped=%d failed=%d dry_run=%s",
                        result.scanned_count,
                        result.selected_count,
                        result.recovered_count,
                        result.skipped_count,
                        result.failed_count,
                        result.dry_run,
                    )
                except Exception as exc:
                    logger.error("Recovery daemon tick failed: %s", exc, exc_info=True)

                # Sleep for the remaining interval, but check stop_event
                elapsed = time.monotonic() - cycle_start
                sleep_time = max(0, self._policy.interval_seconds - elapsed)
                if sleep_time > 0 and not (stop_event and stop_event.is_set()):
                    try:
                        await asyncio.wait_for(
                            stop_event.wait() if stop_event else asyncio.sleep(sleep_time),
                            timeout=sleep_time,
                        )
                        break  # stop_event was set
                    except asyncio.TimeoutError:
                        pass  # Normal: sleep completed
        except asyncio.CancelledError:
            logger.info("Recovery daemon cancelled.")
        finally:
            self._running = False
            await self._audit("recovery.daemon_stopped", None, None, {})

    def stop(self) -> None:
        """Signal the daemon to stop after the current cycle."""
        self._running = False

    # -- Private helpers --

    def _build_scan_config(self) -> RecoveryScanConfig:
        """Build a RecoveryScanConfig from the auto-recovery policy."""
        p = self._policy
        # Map policy statuses to scanner include flags
        include_failed = False
        include_running = False
        include_compensating = False
        include_completed = False

        for status in p.statuses:
            flags = _STATUS_TO_SCAN_FLAGS.get(status.lower(), ())
            for flag in flags:
                if flag == "include_failed":
                    include_failed = True
                elif flag == "include_running":
                    include_running = True
                elif flag == "include_compensating":
                    include_compensating = True
                elif flag == "include_completed":
                    include_completed = True

        # Override with policy.include_completed
        if p.include_completed:
            include_completed = True

        return RecoveryScanConfig(
            stale_after_seconds=int(p.stale_after_seconds),
            include_failed=include_failed,
            include_running=include_running,
            include_compensating=include_compensating,
            include_completed=include_completed,
            limit=p.max_candidates_per_scan,
            workflow_name=p.workflow_name,
            tenant_id=p.tenant_id,
        )

    def _should_skip(self, candidate: RecoveryCandidate) -> str | None:
        """Determine if a candidate should be skipped for auto-recovery.

        Returns a skip reason string, or None if the candidate should be
        selected for recovery.
        """
        p = self._policy

        # Only auto-recover RESUME recommendations
        if candidate.recommendation != RecoveryRecommendation.RESUME:
            return f"recommendation={candidate.recommendation.value}"

        # Check policy flags against candidate reasons
        is_failed = RecoveryCandidateReason.NODE_FAILED in candidate.reasons
        is_stale_running = (
            RecoveryCandidateReason.RUN_STALE in candidate.reasons
            or RecoveryCandidateReason.RUNNING_TOO_LONG in candidate.reasons
        )
        is_compensating = (
            RecoveryCandidateReason.COMPENSATION_INCOMPLETE in candidate.reasons
        )

        if is_failed and not p.recover_failed:
            return "recover_failed disabled"
        if is_stale_running and not p.recover_stale_running:
            return "recover_stale_running disabled"
        if is_compensating and not p.recover_compensating:
            return "recover_compensating disabled"

        # Active lease — never auto-recover
        if candidate.recommendation == RecoveryRecommendation.WAIT_FOR_ACTIVE_LEASE:
            return "active lease"

        # Not resumable — never auto-recover
        if candidate.recommendation == RecoveryRecommendation.DO_NOT_RESUME:
            return "not resumable"

        # LEASE_MISSING recommendation: first version does not auto-recover
        # (scanner may set this when lease is missing but run is running)
        if RecoveryCandidateReason.LEASE_MISSING in candidate.reasons:
            # Only auto-recover LEASE_MISSING if it's a stale/failed run,
            # which is already handled by the policy flags above.
            # If it's purely a lease issue without failure/stale, skip.
            if not (is_failed or is_stale_running or is_compensating):
                return "lease missing without recoverable condition"

        return None

    async def _audit(
        self,
        event_type: str,
        run_id: str | None,
        user_id: str | None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Record an audit event (best-effort, never raises)."""
        if self._audit_logger is None:
            return
        try:
            from agent_app.governance.audit import AuditEvent
            event = AuditEvent(
                event_id=_make_event_id(event_type, run_id),
                run_id=run_id,
                event_type=event_type,
                user_id=user_id,
                data=data or {},
            )
            await self._audit_logger.log(event)
        except Exception as exc:
            logger.debug("Audit log failed for %s: %s", event_type, exc)


def _make_event_id(event_type: str, run_id: str | None) -> str:
    """Generate a simple audit event ID."""
    import time
    rid = run_id or "none"
    return f"{event_type}:{rid}:{int(time.time() * 1000)}"
