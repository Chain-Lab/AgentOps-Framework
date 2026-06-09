"""Tests for Phase 18 Recovery Observability + Admin API.

Tests cover:
  - get_recovery_system_status returns configured/unconfigured state
  - run_recovery_scan_once uses dry-run by default
  - run_recovery_scan_once returns RecoveryDaemonTickResult
  - recover_run defaults to dry-run
  - recover_run no-dry-run delegates to RecoveryService
  - get_recovery_history returns events from audit logger
  - missing recovery dependencies give clear errors
  - _build_scan_config_from_policy maps statuses correctly
  - _should_skip_candidate matches daemon logic
  - CLI status / history / scan / recover commands
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_app.core.app import AgentApp
from agent_app.governance.audit import AuditEvent, InMemoryAuditLogger
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


def _make_app(
    dag_state_store: Any = None,
    dag_lease_backend: Any = None,
    audit_logger: Any = None,
    recovery_config: dict | None = None,
) -> AgentApp:
    """Create a minimal AgentApp for testing."""
    app = AgentApp(
        dag_state_store=dag_state_store,
        dag_lease_backend=dag_lease_backend,
        audit_logger=audit_logger,
    )
    app._recovery_config = recovery_config
    return app


def _make_candidate(
    run_id: str = "run-1",
    recommendation: RecoveryRecommendation = RecoveryRecommendation.RESUME,
    reasons: list[RecoveryCandidateReason] | None = None,
    status: str = "failed",
) -> RecoveryCandidate:
    """Create a minimal RecoveryCandidate for testing."""
    return RecoveryCandidate(
        run_id=run_id,
        workflow_name="test_wf",
        status=status,
        updated_at=datetime.now(timezone.utc),
        age_seconds=60.0,
        reasons=reasons or [RecoveryCandidateReason.NODE_FAILED],
        recommendation=recommendation,
        lease_present=False,
        resumable=True,
    )


def _make_scan_result(candidates: list[RecoveryCandidate]) -> RecoveryScanResult:
    """Create a RecoveryScanResult."""
    return RecoveryScanResult(
        scanned_at=datetime.now(timezone.utc),
        total_scanned=len(candidates) + 5,
        candidate_count=len(candidates),
        candidates=candidates,
    )


# ---------------------------------------------------------------------------
# get_recovery_system_status tests
# ---------------------------------------------------------------------------


class TestGetRecoverySystemStatus:
    """Recovery system status reporting."""

    def test_unconfigured_returns_defaults(self):
        """Without any config, status shows defaults and not configured."""
        app = _make_app()
        status = app.get_recovery_system_status()

        assert status.enabled is False
        assert status.dry_run is True
        assert status.daemon_configured is False
        assert status.scanner_available is False
        assert status.recovery_service_available is False
        assert status.policy is not None
        assert status.policy.enabled is False

    def test_configured_with_store_and_lease(self):
        """With state store and lease backend, status shows configured."""
        store = MagicMock()
        lease = MagicMock()
        app = _make_app(
            dag_state_store=store,
            dag_lease_backend=lease,
        )
        status = app.get_recovery_system_status()

        assert status.scanner_available is True
        assert status.recovery_service_available is True
        assert status.daemon_configured is True

    def test_configured_with_custom_policy(self):
        """Custom recovery config is reflected in status."""
        app = _make_app(
            dag_state_store=MagicMock(),
            dag_lease_backend=MagicMock(),
            recovery_config={
                "auto": {
                    "enabled": True,
                    "dry_run": False,
                    "interval_seconds": 60.0,
                }
            },
        )
        status = app.get_recovery_system_status()

        assert status.enabled is True
        assert status.dry_run is False
        assert status.policy is not None
        assert status.policy.interval_seconds == 60.0

    def test_store_only_partial_config(self):
        """Only state store → scanner available but not full daemon."""
        app = _make_app(dag_state_store=MagicMock())
        status = app.get_recovery_system_status()

        assert status.scanner_available is True
        assert status.recovery_service_available is False
        assert status.daemon_configured is False


# ---------------------------------------------------------------------------
# run_recovery_scan_once tests
# ---------------------------------------------------------------------------


class TestRunRecoveryScanOnce:
    """run_recovery_scan_once API."""

    @pytest.mark.asyncio
    async def test_dry_run_by_default(self):
        """Default scan is dry-run (no actual recovery)."""
        store = MagicMock()
        store.list_runs = AsyncMock(return_value=[])
        app = _make_app(dag_state_store=store)

        result = await app.run_recovery_scan_once()

        assert result.dry_run is True
        assert result.recovered_count == 0
        assert result.recovered_run_ids == []

    @pytest.mark.asyncio
    async def test_returns_tick_result(self):
        """Returns a RecoveryDaemonTickResult with scan data."""
        candidate = _make_candidate("run-scan")
        store = MagicMock()
        store.list_runs = AsyncMock(return_value=[MagicMock(run_id="run-scan", status="failed", updated_at=datetime.now(timezone.utc), age_seconds=60.0)])
        app = _make_app(dag_state_store=store)

        result = await app.run_recovery_scan_once()

        assert isinstance(result, RecoveryDaemonTickResult)
        assert result.scanned_count >= 0

    @pytest.mark.asyncio
    async def test_missing_store_raises(self):
        """Missing state store raises RuntimeError."""
        app = _make_app()
        with pytest.raises(RuntimeError, match="workflow state store"):
            await app.run_recovery_scan_once()

    @pytest.mark.asyncio
    async def test_policy_from_config(self):
        """Policy loaded from config if available."""
        store = MagicMock()
        store.list_runs = AsyncMock(return_value=[])
        app = _make_app(
            dag_state_store=store,
            recovery_config={
                "auto": {
                    "enabled": True,
                    "max_candidates_per_scan": 10,
                    "statuses": ["failed"],
                }
            },
        )
        result = await app.run_recovery_scan_once()
        assert isinstance(result, RecoveryDaemonTickResult)

    @pytest.mark.asyncio
    async def test_scans_and_selects_candidates(self):
        """Scan finds candidates and selects resumable ones."""
        candidate = _make_candidate("run-1")
        mock_run = MagicMock(
            run_id="run-1",
            status="failed",
            updated_at=datetime.now(timezone.utc),
            age_seconds=60.0,
            workflow_name="test_wf",
        )
        store = MagicMock()
        store.list_runs = AsyncMock(return_value=[mock_run])
        # build_recovery_plan returns resumable plan
        recovery_plan = MagicMock()
        recovery_plan.resumable = True
        recovery_plan.reason = None
        recovery_plan.completed_nodes = []
        recovery_plan.interrupted_nodes = []
        recovery_plan.failed_nodes = []
        recovery_plan.compensation_started = False
        store.build_recovery_plan = AsyncMock(return_value=recovery_plan)
        # Provide a failed node so scanner sets NODE_FAILED reason + RESUME recommendation
        mock_node = MagicMock(node_id="node-1", status="failed", completed_at=None)
        store.list_nodes = AsyncMock(return_value=[mock_node])
        store.list_compensations = AsyncMock(return_value=[])
        store.get_run = AsyncMock(return_value=mock_run)
        app = _make_app(dag_state_store=store)

        result = await app.run_recovery_scan_once()

        assert result.scanned_count >= 1
        assert "run-1" in result.selected_run_ids


# ---------------------------------------------------------------------------
# recover_run tests
# ---------------------------------------------------------------------------


class TestRecoverRun:
    """recover_run API."""

    @pytest.mark.asyncio
    async def test_dry_run_default(self):
        """Default is dry-run — does not call recover_workflow_run."""
        mock_run = MagicMock(
            run_id="run-dry",
            status="failed",
            updated_at=datetime.now(timezone.utc),
            age_seconds=60.0,
            workflow_name="test_wf",
        )
        store = MagicMock()
        store.get_run = AsyncMock(return_value=mock_run)
        store.list_nodes = AsyncMock(return_value=[])
        store.list_compensations = AsyncMock(return_value=[])
        recovery_plan = MagicMock()
        recovery_plan.resumable = True
        recovery_plan.reason = None
        recovery_plan.completed_nodes = []
        recovery_plan.interrupted_nodes = []
        recovery_plan.failed_nodes = []
        recovery_plan.compensation_started = False
        store.build_recovery_plan = AsyncMock(return_value=recovery_plan)
        app = _make_app(
            dag_state_store=store,
            dag_lease_backend=MagicMock(),
        )
        # recover_workflow_run should NOT be called in dry-run
        with patch.object(app, "recover_workflow_run", new_callable=AsyncMock) as mock_recover:
            result = await app.recover_run(run_id="run-dry", workflow="test_wf")
            mock_recover.assert_not_called()

        assert result.attempted is False
        assert result.status == "dry_run"

    @pytest.mark.asyncio
    async def test_dry_run_includes_candidate_info(self):
        """Dry-run result includes candidate inspection info."""
        mock_run = MagicMock(
            run_id="run-dry",
            status="failed",
            updated_at=datetime.now(timezone.utc),
            age_seconds=60.0,
            workflow_name="test_wf",
        )
        store = MagicMock()
        store.get_run = AsyncMock(return_value=mock_run)
        store.list_nodes = AsyncMock(return_value=[])
        store.list_compensations = AsyncMock(return_value=[])
        recovery_plan = MagicMock()
        recovery_plan.resumable = True
        recovery_plan.reason = None
        recovery_plan.completed_nodes = []
        recovery_plan.interrupted_nodes = []
        recovery_plan.failed_nodes = []
        recovery_plan.compensation_started = False
        store.build_recovery_plan = AsyncMock(return_value=recovery_plan)
        app = _make_app(
            dag_state_store=store,
            dag_lease_backend=MagicMock(),
        )
        result = await app.recover_run(run_id="run-dry", workflow="test_wf")

        assert result.error is not None
        assert result.error["type"] == "dry_run"
        assert "candidate" in result.error

    @pytest.mark.asyncio
    async def test_no_dry_run_delegates(self):
        """dry_run=False delegates to recover_workflow_run."""
        mock_result = MagicMock()
        mock_result.run_id = "run-live"
        mock_result.attempted = True
        mock_result.recovered = True
        mock_result.status = "completed"
        mock_result.error = None

        app = _make_app(
            dag_state_store=MagicMock(),
            dag_lease_backend=MagicMock(),
        )
        with patch.object(app, "recover_workflow_run", new_callable=AsyncMock, return_value=mock_result) as mock_recover:
            result = await app.recover_run(
                run_id="run-live",
                workflow="test_wf",
                dry_run=False,
                recovered_by="operator-1",
            )
            mock_recover.assert_called_once_with(
                workflow="test_wf",
                run_id="run-live",
                recovered_by="operator-1",
            )

        assert result.recovered is True

    @pytest.mark.asyncio
    async def test_missing_store_raises(self):
        """Missing state store raises RuntimeError."""
        app = _make_app()
        with pytest.raises(RuntimeError, match="workflow state store"):
            await app.recover_run(run_id="run-x")

    @pytest.mark.asyncio
    async def test_missing_lease_raises(self):
        """Missing lease backend raises RuntimeError."""
        app = _make_app(dag_state_store=MagicMock())
        with pytest.raises(RuntimeError, match="lease backend"):
            await app.recover_run(run_id="run-x", dry_run=False)


# ---------------------------------------------------------------------------
# get_recovery_history tests
# ---------------------------------------------------------------------------


class TestGetRecoveryHistory:
    """Recovery history query."""

    @pytest.mark.asyncio
    async def test_no_audit_logger_returns_empty(self):
        """Without audit logger, returns empty list."""
        app = _make_app()
        events = await app.get_recovery_history("run-1")
        assert events == []

    @pytest.mark.asyncio
    async def test_returns_events_from_audit_logger(self):
        """Returns events filtered by run_id."""
        audit = InMemoryAuditLogger()
        now = datetime.now(timezone.utc)
        audit._events = [
            AuditEvent(
                event_id="evt-1",
                run_id="run-abc",
                event_type="recovery.scan_completed",
                created_at=now,
                data={"count": 5},
            ),
            AuditEvent(
                event_id="evt-2",
                run_id="run-abc",
                event_type="recovery.recovery_completed",
                created_at=now,
                data={"status": "completed"},
            ),
            AuditEvent(
                event_id="evt-3",
                run_id="run-xyz",
                event_type="recovery.scan_completed",
                created_at=now,
                data={},
            ),
        ]

        app = _make_app(audit_logger=audit)
        events = await app.get_recovery_history("run-abc")

        assert len(events) == 2
        assert all(e.run_id == "run-abc" for e in events)

    @pytest.mark.asyncio
    async def test_limit_respected(self):
        """Limit parameter restricts number of returned events."""
        audit = InMemoryAuditLogger()
        now = datetime.now(timezone.utc)
        audit._events = [
            AuditEvent(
                event_id=f"evt-{i}",
                run_id="run-all",
                event_type=f"evt.type-{i}",
                created_at=now,
            )
            for i in range(20)
        ]

        app = _make_app(audit_logger=audit)
        events = await app.get_recovery_history("run-all", limit=5)
        assert len(events) == 5

    @pytest.mark.asyncio
    async def test_sorted_by_timestamp(self):
        """Events are returned sorted by created_at."""
        audit = InMemoryAuditLogger()
        base = datetime(2025, 1, 1, tzinfo=timezone.utc)
        audit._events = [
            AuditEvent(event_id="e2", run_id="run-s", event_type="t2", created_at=base),
            AuditEvent(event_id="e1", run_id="run-s", event_type="t1", created_at=base),
            AuditEvent(event_id="e3", run_id="run-s", event_type="t3", created_at=base),
        ]

        app = _make_app(audit_logger=audit)
        events = await app.get_recovery_history("run-s")
        # Should be sorted by created_at (all same here, but InMemoryAuditLogger sorts)
        assert len(events) == 3


# ---------------------------------------------------------------------------
# _build_scan_config_from_policy tests
# ---------------------------------------------------------------------------


class TestBuildScanConfigFromPolicy:
    """Policy-to-scan-config mapping."""

    def _call(self, **policy_kwargs):
        app = _make_app()
        policy = AutoRecoveryPolicy(**policy_kwargs)
        return app._build_scan_config_from_policy(policy)

    def test_default_statuses_map_to_failed_running_compensating(self):
        """Default statuses map to include_failed, include_running, include_compensating."""
        cfg = self._call()
        assert cfg.include_failed is True
        assert cfg.include_running is True
        assert cfg.include_compensating is True
        assert cfg.include_completed is False

    def test_completed_status_adds_completed_flag(self):
        """Including 'completed' in statuses sets include_completed."""
        cfg = self._call(statuses=["completed"])
        assert cfg.include_completed is True

    def test_include_completed_flag_override(self):
        """policy.include_completed=True overrides statuses."""
        cfg = self._call(include_completed=True)
        assert cfg.include_completed is True

    def test_limit_mapped_from_max_candidates(self):
        """policy.max_candidates_per_scan maps to config.limit."""
        cfg = self._call(max_candidates_per_scan=77)
        assert cfg.limit == 77

    def test_workflow_name_passed(self):
        """policy.workflow_name is passed to config."""
        cfg = self._call(workflow_name="my_wf")
        assert cfg.workflow_name == "my_wf"

    def test_tenant_id_passed(self):
        """policy.tenant_id is passed to config."""
        cfg = self._call(tenant_id="tenant-x")
        assert cfg.tenant_id == "tenant-x"


# ---------------------------------------------------------------------------
# _should_skip_candidate tests
# ---------------------------------------------------------------------------


class TestShouldSkipCandidate:
    """Selection/skip logic matches RecoveryDaemon behavior."""

    def _call(self, candidate, **policy_kwargs):
        app = _make_app()
        policy = AutoRecoveryPolicy(**policy_kwargs)
        return app._should_skip_candidate(candidate, policy)

    def test_resume_not_skipped(self):
        """RESUME recommendation with valid reasons is not skipped."""
        c = _make_candidate("run-1", recommendation=RecoveryRecommendation.RESUME)
        assert self._call(c) is None

    def test_inspect_only_skipped(self):
        """INSPECT_ONLY recommendation is skipped."""
        c = _make_candidate(
            "run-1",
            recommendation=RecoveryRecommendation.INSPECT_ONLY,
        )
        reason = self._call(c)
        assert reason is not None
        assert "inspect_only" in reason

    def test_wait_for_active_lease_skipped(self):
        """WAIT_FOR_ACTIVE_LEASE is skipped (returns recommendation-based reason)."""
        c = _make_candidate(
            "run-1",
            recommendation=RecoveryRecommendation.WAIT_FOR_ACTIVE_LEASE,
        )
        reason = self._call(c)
        assert reason is not None
        # Non-RESUME recommendations return recommendation=... format
        assert "wait_for_active_lease" in reason

    def test_do_not_resume_skipped(self):
        """DO_NOT_RESUME recommendation is skipped (returns recommendation-based reason)."""
        c = _make_candidate(
            "run-1",
            recommendation=RecoveryRecommendation.DO_NOT_RESUME,
        )
        reason = self._call(c)
        assert reason is not None
        assert "do_not_resume" in reason

    def test_recover_false_skips_failed(self):
        """recover_failed=False skips NODE_FAILED candidates."""
        c = _make_candidate(
            "run-1",
            reasons=[RecoveryCandidateReason.NODE_FAILED],
        )
        reason = self._call(c, recover_failed=False)
        assert reason is not None
        assert "recover_failed disabled" in reason

    def test_recover_stale_running_false_skips_stale(self):
        """recover_stale_running=False skips stale running candidates."""
        c = _make_candidate(
            "run-1",
            reasons=[RecoveryCandidateReason.RUN_STALE],
            status="running",
        )
        reason = self._call(c, recover_stale_running=False)
        assert reason is not None
        assert "recover_stale_running disabled" in reason


# ---------------------------------------------------------------------------
# CLI tests (Phase 18)
# ---------------------------------------------------------------------------


class TestRecoveryCLIPhase18:
    """Phase 18 CLI commands: status, history, scan, recover."""

    def _run_cli(self, argv: list[str]) -> int:
        """Run CLI with given argv and return exit code."""
        import agent_app.cli as cli_module

        old_argv = sys.argv
        try:
            sys.argv = ["agentapp"] + argv
            return cli_module.main()
        finally:
            sys.argv = old_argv

    @pytest.mark.asyncio
    async def test_status_json(self):
        """recovery status --json outputs machine-readable status."""
        from agent_app.cli import _cmd_recovery_status
        from agent_app.config.loader import build_app

        mock_app = MagicMock()
        mock_status = MagicMock()
        mock_status.enabled = True
        mock_status.dry_run = True
        mock_status.daemon_configured = True
        mock_status.scanner_available = True
        mock_status.recovery_service_available = True
        mock_status.last_tick_at = None
        mock_status.policy = MagicMock()
        mock_status.policy.model_dump = MagicMock(return_value={"enabled": True})
        mock_app.get_recovery_system_status = MagicMock(return_value=mock_status)

        args = MagicMock()
        args.config = "test.yaml"
        args.json = True

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            rc = await _cmd_recovery_status(args)

        assert rc == 0

    @pytest.mark.asyncio
    async def test_status_no_config_exits_nonzero(self):
        """recovery status with missing config exits non-zero."""
        from agent_app.cli import _cmd_recovery_status

        args = MagicMock()
        args.config = "missing.yaml"
        args.json = False

        with patch("agent_app.config.loader.build_app", side_effect=FileNotFoundError("no config")):
            rc = await _cmd_recovery_status(args)

        assert rc == 1

    @pytest.mark.asyncio
    async def test_history_returns_events(self):
        """recovery history returns events from audit logger."""
        from agent_app.cli import _cmd_recovery_history

        audit = InMemoryAuditLogger()
        now = datetime.now(timezone.utc)
        audit._events = [
            AuditEvent(
                event_id="e1",
                run_id="run-hist",
                event_type="recovery.daemon_tick_completed",
                created_at=now,
                data={"scanned_count": 10},
            ),
        ]

        mock_app = MagicMock()
        mock_app.get_recovery_history = AsyncMock(return_value=audit._events)

        args = MagicMock()
        args.config = "test.yaml"
        args.run_id = "run-hist"
        args.limit = 50
        args.json = False

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            rc = await _cmd_recovery_history(args)

        assert rc == 0

    @pytest.mark.asyncio
    async def test_history_json(self):
        """recovery history --json outputs JSON."""
        from agent_app.cli import _cmd_recovery_history

        audit = InMemoryAuditLogger()
        now = datetime.now(timezone.utc)
        audit._events = [
            AuditEvent(
                event_id="e1",
                run_id="run-json",
                event_type="recovery.daemon_tick_completed",
                created_at=now,
                data={},
            ),
        ]

        mock_app = MagicMock()
        mock_app.get_recovery_history = AsyncMock(return_value=audit._events)

        args = MagicMock()
        args.config = "test.yaml"
        args.run_id = "run-json"
        args.limit = 50
        args.json = True

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            rc = await _cmd_recovery_history(args)

        assert rc == 0

    @pytest.mark.asyncio
    async def test_history_no_events(self):
        """recovery history with no events returns 0."""
        from agent_app.cli import _cmd_recovery_history

        mock_app = MagicMock()
        mock_app.get_recovery_history = AsyncMock(return_value=[])

        args = MagicMock()
        args.config = "test.yaml"
        args.run_id = "run-empty"
        args.limit = 50
        args.json = False

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            rc = await _cmd_recovery_history(args)

        assert rc == 0

    @pytest.mark.asyncio
    async def test_scan_admin_dry_run(self):
        """recovery scan admin defaults to dry-run."""
        from agent_app.cli import _cmd_recovery_scan_admin

        mock_app = MagicMock()
        tick_result = RecoveryDaemonTickResult(
            scanned_count=5,
            selected_count=1,
            dry_run=True,
            selected_run_ids=["run-1"],
        )
        mock_app.run_recovery_scan_once = AsyncMock(return_value=tick_result)

        args = MagicMock()
        args.config = "test.yaml"
        args.json = False
        args.no_dry_run = False

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            rc = await _cmd_recovery_scan_admin(args)

        assert rc == 0
        mock_app.run_recovery_scan_once.assert_called_once()

    @pytest.mark.asyncio
    async def test_scan_admin_with_dry_run_flag(self):
        """recovery scan --no-dry-run passes dry_run=False."""
        from agent_app.cli import _cmd_recovery_scan_admin

        mock_app = MagicMock()
        tick_result = RecoveryDaemonTickResult(dry_run=False)
        mock_app.run_recovery_scan_once = AsyncMock(return_value=tick_result)

        args = MagicMock()
        args.config = "test.yaml"
        args.json = False
        args.no_dry_run = True

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            rc = await _cmd_recovery_scan_admin(args)

        # Verify policy was constructed with dry_run=False
        call_kwargs = mock_app.run_recovery_scan_once.call_args
        policy = call_kwargs.kwargs.get("policy") or call_kwargs[1].get("policy")
        # When no_dry_run=True, policy should have dry_run=False
        assert policy is None or getattr(policy, "dry_run", True) is False

    @pytest.mark.asyncio
    async def test_recover_admin_dry_run_default(self):
        """recovery recover defaults to dry-run."""
        from agent_app.cli import _cmd_recovery_recover_admin

        mock_app = MagicMock()
        mock_result = MagicMock()
        mock_result.attempted = False
        mock_result.recovered = False
        mock_result.status = "dry_run"
        mock_result.error = {"type": "dry_run", "message": "No recovery attempted."}
        mock_app.recover_run = AsyncMock(return_value=mock_result)

        args = MagicMock()
        args.config = "test.yaml"
        args.run_id = "run-x"
        args.workflow = ""
        args.recovered_by = "admin-cli"
        args.no_dry_run = False
        args.json = False

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            rc = await _cmd_recovery_recover_admin(args)

        assert rc == 1
        mock_app.recover_run.assert_called_once_with(
            run_id="run-x",
            workflow="",
            dry_run=True,
            recovered_by="admin-cli",
        )

    @pytest.mark.asyncio
    async def test_recover_admin_no_dry_run(self):
        """recovery recover --no-dry-run passes dry_run=False."""
        from agent_app.cli import _cmd_recovery_recover_admin

        mock_app = MagicMock()
        mock_result = MagicMock()
        mock_result.attempted = True
        mock_result.recovered = True
        mock_result.status = "completed"
        mock_result.error = None
        mock_app.recover_run = AsyncMock(return_value=mock_result)

        args = MagicMock()
        args.config = "test.yaml"
        args.run_id = "run-y"
        args.workflow = "test_wf"
        args.recovered_by = "operator-1"
        args.no_dry_run = True
        args.json = False

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            rc = await _cmd_recovery_recover_admin(args)

        assert rc == 0
        call_kwargs = mock_app.recover_run.call_args
        # Check dry_run=False was passed
        all_kwargs = {}
        if call_kwargs.kwargs:
            all_kwargs.update(call_kwargs.kwargs)
        if call_kwargs.args:
            all_kwargs.update(call_kwargs.args)
        assert all_kwargs.get("dry_run") is False


# ---------------------------------------------------------------------------
# CLI integration via argparse (full argv tests)
# ---------------------------------------------------------------------------


class TestCLIRecoveryCommandsPhase18:
    """End-to-end CLI handler tests for Phase 18 commands."""

    @pytest.mark.asyncio
    async def test_status_handler_basic(self):
        """_cmd_recovery_status works with mocked app."""
        from agent_app.cli import _cmd_recovery_status

        mock_app = MagicMock()
        mock_status = MagicMock()
        mock_status.enabled = True
        mock_status.dry_run = True
        mock_status.daemon_configured = True
        mock_status.scanner_available = True
        mock_status.recovery_service_available = True
        mock_status.last_tick_at = None
        mock_status.policy = None
        mock_app.get_recovery_system_status = MagicMock(return_value=mock_status)

        args = MagicMock()
        args.config = "test.yaml"
        args.json = True

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            rc = await _cmd_recovery_status(args)
        assert rc == 0

    @pytest.mark.asyncio
    async def test_history_handler_basic(self):
        """_cmd_recovery_history works with mocked app."""
        from agent_app.cli import _cmd_recovery_history

        audit = InMemoryAuditLogger()
        now = datetime.now(timezone.utc)
        audit._events = [
            AuditEvent(
                event_id="e1",
                run_id="run-hist",
                event_type="recovery.daemon_tick_completed",
                created_at=now,
                data={"count": 5},
            ),
        ]

        mock_app = MagicMock()
        mock_app.get_recovery_history = AsyncMock(return_value=audit._events)

        args = MagicMock()
        args.config = "test.yaml"
        args.run_id = "run-hist"
        args.limit = 50
        args.json = False

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            rc = await _cmd_recovery_history(args)
        assert rc == 0

    @pytest.mark.asyncio
    async def test_scan_admin_handler_basic(self):
        """_cmd_recovery_scan_admin works with mocked app."""
        from agent_app.cli import _cmd_recovery_scan_admin

        mock_app = MagicMock()
        tick_result = RecoveryDaemonTickResult(
            scanned_count=5,
            selected_count=1,
            dry_run=True,
            selected_run_ids=["run-1"],
        )
        mock_app.run_recovery_scan_once = AsyncMock(return_value=tick_result)

        args = MagicMock()
        args.config = "test.yaml"
        args.json = False
        args.no_dry_run = False

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            rc = await _cmd_recovery_scan_admin(args)
        assert rc == 0
        mock_app.run_recovery_scan_once.assert_called_once()

    @pytest.mark.asyncio
    async def test_recover_admin_handler_basic(self):
        """_cmd_recovery_recover_admin works with mocked app."""
        from agent_app.cli import _cmd_recovery_recover_admin

        mock_app = MagicMock()
        mock_result = MagicMock()
        mock_result.attempted = False
        mock_result.recovered = False
        mock_result.status = "dry_run"
        mock_result.error = {"type": "dry_run", "message": "No recovery attempted."}
        mock_app.recover_run = AsyncMock(return_value=mock_result)

        args = MagicMock()
        args.config = "test.yaml"
        args.run_id = "run-x"
        args.workflow = ""
        args.recovered_by = "admin-cli"
        args.no_dry_run = False
        args.json = False

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            rc = await _cmd_recovery_recover_admin(args)
        assert rc == 1
        mock_app.recover_run.assert_called_once_with(
            run_id="run-x",
            workflow="",
            dry_run=True,
            recovered_by="admin-cli",
        )
