"""Tests for Phase 17 RecoveryDaemon.

Tests cover:
  - dry-run selects resumable candidate but does not recover
  - no-dry-run calls RecoveryService.recover_run()
  - active lease candidate skipped
  - not resumable candidate skipped
  - max_recoveries_per_scan respected
  - max_concurrent_recoveries respected
  - failed recovery recorded but does not crash tick
  - audit events written
  - completed skipped by default
  - workflow_name filter passed to scanner
  - run_forever graceful shutdown
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_app.runtime.recovery_daemon import RecoveryDaemon
from agent_app.runtime.recovery_models import (
    AutoRecoveryPolicy,
    RecoveryCandidate,
    RecoveryCandidateReason,
    RecoveryDaemonTickResult,
    RecoveryRecommendation,
    RecoveryScanResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_candidate(
    run_id: str,
    recommendation: RecoveryRecommendation = RecoveryRecommendation.RESUME,
    reasons: list[RecoveryCandidateReason] | None = None,
    workflow_name: str = "test_wf",
    status: str = "failed",
) -> RecoveryCandidate:
    """Create a RecoveryCandidate for testing."""
    now = datetime.now(timezone.utc)
    return RecoveryCandidate(
        run_id=run_id,
        workflow_name=workflow_name,
        status=status,
        updated_at=now,
        age_seconds=60.0,
        reasons=reasons or [RecoveryCandidateReason.NODE_FAILED],
        recommendation=recommendation,
        lease_present=False,
        lease_expired=None,
        resumable=True,
        resume_plan_summary={"total_nodes": 3, "resumable_nodes": 2},
        recovery_plan_summary={"resumable": True},
    )


def _make_scan_result(candidates: list[RecoveryCandidate]) -> RecoveryScanResult:
    """Create a RecoveryScanResult with the given candidates."""
    return RecoveryScanResult(
        scanned_at=datetime.now(timezone.utc),
        total_scanned=len(candidates) + 5,  # Some runs scanned but not candidates
        candidate_count=len(candidates),
        candidates=candidates,
    )


def _make_daemon(
    policy_kwargs: dict[str, Any] | None = None,
    mock_scanner: Any = None,
    mock_service: Any = None,
    mock_audit: Any = None,
) -> RecoveryDaemon:
    """Create a RecoveryDaemon with mocked dependencies."""
    policy_kwargs = policy_kwargs or {}
    policy = AutoRecoveryPolicy(**policy_kwargs)

    scanner = mock_scanner or MagicMock()
    service = mock_service or MagicMock()
    audit = mock_audit or MagicMock()

    daemon = RecoveryDaemon(
        scanner=scanner,
        recovery_service=service,
        policy=policy,
        audit_logger=audit,
    )
    return daemon


# ---------------------------------------------------------------------------
# Dry-run tests
# ---------------------------------------------------------------------------


class TestDryRun:
    """Dry-run mode selects but does not actually recover."""

    @pytest.mark.asyncio
    async def test_dry_run_selects_resumable(self):
        """dry_run=True selects RESUME candidates without calling recover."""
        candidate = _make_candidate("run-1")
        scanner = MagicMock()
        scanner.scan = AsyncMock(return_value=_make_scan_result([candidate]))
        service = MagicMock()
        service.recover_run = AsyncMock()

        daemon = _make_daemon(
            policy_kwargs={"dry_run": True, "enabled": True},
            mock_scanner=scanner,
            mock_service=service,
        )

        result = await daemon.run_once()

        assert result.dry_run is True
        assert result.selected_count == 1
        assert result.recovered_count == 1
        assert "run-1" in result.recovered_run_ids
        # recover_run should NOT be called in dry-run
        service.recover_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_dry_run_calls_recover(self):
        """dry_run=False calls RecoveryService.recover_run()."""
        candidate = _make_candidate("run-1")
        scanner = MagicMock()
        scanner.scan = AsyncMock(return_value=_make_scan_result([candidate]))
        mock_result = MagicMock()
        mock_result.recovered = True
        mock_result.status = "completed"
        service = MagicMock()
        service.recover_run = AsyncMock(return_value=mock_result)

        daemon = _make_daemon(
            policy_kwargs={"dry_run": False, "enabled": True},
            mock_scanner=scanner,
            mock_service=service,
        )

        result = await daemon.run_once()

        assert result.dry_run is False
        assert result.recovered_count == 1
        service.recover_run.assert_called_once()
        call_args = service.recover_run.call_args
        assert call_args.kwargs["run_id"] == "run-1"


# ---------------------------------------------------------------------------
# Selection / skip tests
# ---------------------------------------------------------------------------


class TestCandidateSelection:
    """Candidate selection and skip logic."""

    @pytest.mark.asyncio
    async def test_active_lease_skipped(self):
        """Candidates with active lease recommendation are skipped."""
        candidate = _make_candidate(
            "run-lease",
            recommendation=RecoveryRecommendation.WAIT_FOR_ACTIVE_LEASE,
            reasons=[RecoveryCandidateReason.LEASE_EXPIRED],
        )
        scanner = MagicMock()
        scanner.scan = AsyncMock(return_value=_make_scan_result([candidate]))
        service = MagicMock()
        service.recover_run = AsyncMock()

        daemon = _make_daemon(
            policy_kwargs={"enabled": True, "dry_run": False},
            mock_scanner=scanner,
            mock_service=service,
        )

        result = await daemon.run_once()

        assert result.selected_count == 0
        assert result.skipped_count == 1
        assert "wait_for_active_lease" in result.skipped[0]["reason"]
        service.recover_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_resumable_skipped(self):
        """Candidates with DO_NOT_RESUME recommendation are skipped."""
        candidate = _make_candidate(
            "run-noresume",
            recommendation=RecoveryRecommendation.DO_NOT_RESUME,
            reasons=[RecoveryCandidateReason.NOT_RESUMABLE],
            status="failed",
        )
        scanner = MagicMock()
        scanner.scan = AsyncMock(return_value=_make_scan_result([candidate]))
        service = MagicMock()
        service.recover_run = AsyncMock()

        daemon = _make_daemon(
            policy_kwargs={"enabled": True, "dry_run": False},
            mock_scanner=scanner,
            mock_service=service,
        )

        result = await daemon.run_once()

        assert result.selected_count == 0
        assert result.skipped_count == 1
        assert "do_not_resume" in result.skipped[0]["reason"]
        service.recover_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_completed_skipped_by_default(self):
        """Completed candidates are skipped by default."""
        candidate = _make_candidate(
            "run-done",
            status="completed",
            reasons=[RecoveryCandidateReason.SNAPSHOT_AVAILABLE],
            recommendation=RecoveryRecommendation.INSPECT_ONLY,
        )
        scanner = MagicMock()
        scanner.scan = AsyncMock(return_value=_make_scan_result([candidate]))
        service = MagicMock()
        service.recover_run = AsyncMock()

        daemon = _make_daemon(
            policy_kwargs={"enabled": True},
            mock_scanner=scanner,
            mock_service=service,
        )

        result = await daemon.run_once()

        # Completed should not appear in scan results because the scanner
        # filters based on include_completed=False by default
        # OR if it does appear, it should be skipped by recommendation
        assert result.selected_count == 0

    @pytest.mark.asyncio
    async def test_workflow_name_filter_passed_to_scanner(self):
        """workflow_name is passed through to scanner.scan()."""
        candidate = _make_candidate("run-wf", workflow_name="target_wf")
        scanner = MagicMock()
        scanner.scan = AsyncMock(return_value=_make_scan_result([candidate]))
        service = MagicMock()

        daemon = _make_daemon(
            policy_kwargs={
                "enabled": True,
                "workflow_name": "target_wf",
            },
            mock_scanner=scanner,
            mock_service=service,
        )

        await daemon.run_once()

        # Verify scanner.scan was called with workflow_name in config
        call_args = scanner.scan.call_args
        config = call_args.args[0] if call_args.args else call_args.kwargs.get("config")
        assert config is not None
        assert config.workflow_name == "target_wf"


# ---------------------------------------------------------------------------
# Limit tests
# ---------------------------------------------------------------------------


class TestLimits:
    """Policy limits are respected."""

    @pytest.mark.asyncio
    async def test_max_recoveries_per_scan_respected(self):
        """Only max_recoveries_per_scan candidates are recovered."""
        candidates = [
            _make_candidate(f"run-{i}", workflow_name="wf")
            for i in range(10)
        ]
        scanner = MagicMock()
        scanner.scan = AsyncMock(return_value=_make_scan_result(candidates))
        service = MagicMock()
        mock_result = MagicMock()
        mock_result.recovered = True
        mock_result.status = "completed"
        service.recover_run = AsyncMock(return_value=mock_result)

        daemon = _make_daemon(
            policy_kwargs={
                "enabled": True,
                "dry_run": False,
                "max_recoveries_per_scan": 3,
            },
            mock_scanner=scanner,
            mock_service=service,
        )

        result = await daemon.run_once()

        assert result.recovered_count == 3
        assert service.recover_run.call_count == 3

    @pytest.mark.asyncio
    async def test_max_candidates_per_scan_respected(self):
        """Only max_candidates_per_scan are evaluated."""
        candidates = [
            _make_candidate(f"run-{i}", workflow_name="wf")
            for i in range(10)
        ]
        scanner = MagicMock()
        scanner.scan = AsyncMock(return_value=_make_scan_result(candidates))
        service = MagicMock()

        daemon = _make_daemon(
            policy_kwargs={
                "enabled": True,
                "max_candidates_per_scan": 4,
            },
            mock_scanner=scanner,
            mock_service=service,
        )

        result = await daemon.run_once()

        # scanner.scan should be called with limit=4
        call_args = scanner.scan.call_args
        config = call_args.args[0] if call_args.args else call_args.kwargs.get("config")
        assert config.limit == 4


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Errors don't crash the daemon tick."""

    @pytest.mark.asyncio
    async def test_failed_recovery_recorded(self):
        """A recovery that fails is recorded in failures list."""
        candidate = _make_candidate("run-fail")
        scanner = MagicMock()
        scanner.scan = AsyncMock(return_value=_make_scan_result([candidate]))
        service = MagicMock()
        mock_result = MagicMock()
        mock_result.recovered = False
        mock_result.status = "resume_failed"
        mock_result.error = {"type": "resume_error", "message": "boom"}
        service.recover_run = AsyncMock(return_value=mock_result)

        daemon = _make_daemon(
            policy_kwargs={"enabled": True, "dry_run": False},
            mock_scanner=scanner,
            mock_service=service,
        )

        result = await daemon.run_once()

        assert result.failed_count == 1
        assert result.failures[0]["run_id"] == "run-fail"
        assert "boom" in str(result.failures[0]["error"])

    @pytest.mark.asyncio
    async def test_recovery_exception_recorded(self):
        """An exception from recover_run is caught and recorded."""
        candidate = _make_candidate("run-exc")
        scanner = MagicMock()
        scanner.scan = AsyncMock(return_value=_make_scan_result([candidate]))
        service = MagicMock()
        service.recover_run = AsyncMock(side_effect=RuntimeError("lease error"))

        daemon = _make_daemon(
            policy_kwargs={"enabled": True, "dry_run": False},
            mock_scanner=scanner,
            mock_service=service,
        )

        result = await daemon.run_once()

        assert result.failed_count == 1
        assert result.failures[0]["run_id"] == "run-exc"


# ---------------------------------------------------------------------------
# Audit tests
# ---------------------------------------------------------------------------


class TestAuditEvents:
    """Audit events are written for daemon actions."""

    @pytest.mark.asyncio
    async def test_daemon_audit_events_written(self):
        """Key audit events are written during a tick."""
        candidate = _make_candidate("run-audit")
        scanner = MagicMock()
        scanner.scan = AsyncMock(return_value=_make_scan_result([candidate]))
        service = MagicMock()
        mock_result = MagicMock()
        mock_result.recovered = True
        mock_result.status = "completed"
        service.recover_run = AsyncMock(return_value=mock_result)

        audit_logger = MagicMock()
        audit_logger.log = AsyncMock()

        daemon = _make_daemon(
            policy_kwargs={"enabled": True, "dry_run": False},
            mock_scanner=scanner,
            mock_service=service,
            mock_audit=audit_logger,
        )

        await daemon.run_once()

        # Check that audit was called for key events
        calls = [c.args[0].event_type for c in audit_logger.log.call_args_list]
        assert "recovery.daemon_tick_started" in calls
        assert "recovery.daemon_tick_completed" in calls
        assert "recovery.daemon_candidate_selected" in calls
        assert "recovery.daemon_recovery_started" in calls
        assert "recovery.daemon_recovery_completed" in calls

    @pytest.mark.asyncio
    async def test_dry_run_audit_event(self):
        """Dry-run produces daemon_dry_run_selected events."""
        candidate = _make_candidate("run-dry-audit")
        scanner = MagicMock()
        scanner.scan = AsyncMock(return_value=_make_scan_result([candidate]))
        service = MagicMock()

        audit_logger = MagicMock()
        audit_logger.log = AsyncMock()

        daemon = _make_daemon(
            policy_kwargs={"enabled": True, "dry_run": True},
            mock_scanner=scanner,
            mock_service=service,
            mock_audit=audit_logger,
        )

        await daemon.run_once()

        calls = [c.args[0].event_type for c in audit_logger.log.call_args_list]
        assert "recovery.daemon_dry_run_selected" in calls


# ---------------------------------------------------------------------------
# Policy flag tests
# ---------------------------------------------------------------------------


class TestPolicyFlags:
    """Policy flags control what gets auto-recovered."""

    @pytest.mark.asyncio
    async def test_recover_failed_disabled_skips_failed(self):
        """When recover_failed=False, failed candidates are skipped."""
        candidate = _make_candidate(
            "run-failed",
            reasons=[RecoveryCandidateReason.NODE_FAILED],
            recommendation=RecoveryRecommendation.RESUME,
        )
        scanner = MagicMock()
        scanner.scan = AsyncMock(return_value=_make_scan_result([candidate]))
        service = MagicMock()

        daemon = _make_daemon(
            policy_kwargs={
                "enabled": True,
                "dry_run": True,
                "recover_failed": False,
            },
            mock_scanner=scanner,
            mock_service=service,
        )

        result = await daemon.run_once()

        assert result.selected_count == 0
        assert result.skipped_count == 1

    @pytest.mark.asyncio
    async def test_recover_stale_running_disabled_skips_stale(self):
        """When recover_stale_running=False, stale running candidates are skipped."""
        candidate = _make_candidate(
            "run-stale",
            reasons=[
                RecoveryCandidateReason.RUN_STALE,
                RecoveryCandidateReason.LEASE_EXPIRED,
            ],
            recommendation=RecoveryRecommendation.RESUME,
            status="running",
        )
        scanner = MagicMock()
        scanner.scan = AsyncMock(return_value=_make_scan_result([candidate]))
        service = MagicMock()

        daemon = _make_daemon(
            policy_kwargs={
                "enabled": True,
                "dry_run": True,
                "recover_stale_running": False,
            },
            mock_scanner=scanner,
            mock_service=service,
        )

        result = await daemon.run_once()

        assert result.selected_count == 0
        assert result.skipped_count == 1


# ---------------------------------------------------------------------------
# Scan config mapping tests
# ---------------------------------------------------------------------------


class TestScanConfigMapping:
    """Policy statuses map correctly to scanner config."""

    @pytest.mark.asyncio
    async def test_policy_statuses_mapped_to_scanner(self):
        """Policy statuses are mapped to scanner include flags."""
        candidate = _make_candidate("run-map", status="compensating")
        scanner = MagicMock()
        scanner.scan = AsyncMock(return_value=_make_scan_result([candidate]))
        service = MagicMock()

        daemon = _make_daemon(
            policy_kwargs={
                "enabled": True,
                "statuses": ["compensating"],
            },
            mock_scanner=scanner,
            mock_service=service,
        )

        await daemon.run_once()

        call_args = scanner.scan.call_args
        config = call_args.args[0]
        assert config.include_compensating is True
        assert config.include_failed is False
        assert config.include_running is False


# ---------------------------------------------------------------------------
# run_forever tests
# ---------------------------------------------------------------------------


class TestRunForever:
    """run_forever behavior."""

    @pytest.mark.asyncio
    async def test_run_forever_stops_on_stop_event(self):
        """run_forever stops when stop_event is set."""
        candidate = _make_candidate("run-fg")
        scanner = MagicMock()
        scanner.scan = AsyncMock(return_value=_make_scan_result([candidate]))
        service = MagicMock()
        mock_result = MagicMock()
        mock_result.recovered = True
        service.recover_run = AsyncMock(return_value=mock_result)

        audit_logger = MagicMock()
        audit_logger.log = AsyncMock()

        daemon = _make_daemon(
            policy_kwargs={"enabled": True, "dry_run": False, "interval_seconds": 0.01},
            mock_scanner=scanner,
            mock_service=service,
            mock_audit=audit_logger,
        )

        stop_event = asyncio.Event()

        async def _stop_soon():
            await asyncio.sleep(0.05)
            stop_event.set()
            daemon.stop()

        asyncio.create_task(_stop_soon())
        await daemon.run_forever(stop_event=stop_event)

        # Should have run at least once
        assert scanner.scan.call_count >= 1

    @pytest.mark.asyncio
    async def test_run_forever_emits_started_stopped(self):
        """run_forever emits daemon_started and daemon_stopped events."""
        scanner = MagicMock()
        scanner.scan = AsyncMock(return_value=_make_scan_result([]))
        service = MagicMock()

        audit_logger = MagicMock()
        audit_logger.log = AsyncMock()

        daemon = _make_daemon(
            policy_kwargs={"enabled": True, "interval_seconds": 0.01},
            mock_scanner=scanner,
            mock_service=service,
            mock_audit=audit_logger,
        )

        stop_event = asyncio.Event()

        async def _stop_soon():
            await asyncio.sleep(0.05)
            stop_event.set()
            daemon.stop()

        asyncio.create_task(_stop_soon())
        await daemon.run_forever(stop_event=stop_event)

        event_types = [c.args[0].event_type for c in audit_logger.log.call_args_list]
        assert "recovery.daemon_started" in event_types
        assert "recovery.daemon_stopped" in event_types
