"""Tests for Phase 16.5 CLI recovery commands.

Tests cover:
  - recovery scan exits 0
  - recovery inspect exits 0
  - recovery recover success exits 0
  - recovery recover blocked by active lease exits non-zero
  - missing config exits non-zero
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_app.runtime.recovery_models import (
    RecoveryCandidate,
    RecoveryDaemonTickResult,
    RecoveryScanResult,
    RecoveryRecommendation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(**kwargs: Any) -> MagicMock:
    """Create a mock argparse namespace."""
    ns = MagicMock()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


class MockApp:
    """Mock AgentApp for CLI tests."""

    def __init__(self):
        self._dag_state_store = MagicMock()
        self._dag_lease_backend = MagicMock()


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestRecoveryScanCLI:
    """agentapp recovery scan"""

    @pytest.mark.asyncio
    async def test_scan_exits_zero(self):
        from agent_app.cli import _cmd_recovery_scan

        mock_app = MagicMock()
        mock_app.scan_recovery_candidates = AsyncMock(
            return_value=RecoveryScanResult(
                total_scanned=5,
                candidate_count=2,
                candidates=[
                    RecoveryCandidate(
                        run_id="wr-1",
                        status="failed",
                        recommendation=RecoveryRecommendation.RESUME,
                    ),
                ],
            )
        )

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            args = _make_args(config="test.yaml", limit=100, workflow=None, json=False)
            rc = await _cmd_recovery_scan(args)

        assert rc == 0

    @pytest.mark.asyncio
    async def test_scan_no_config_exits_nonzero(self):
        from agent_app.cli import _cmd_recovery_scan

        with patch("agent_app.config.loader.build_app", side_effect=FileNotFoundError("no config")):
            args = _make_args(config="missing.yaml", limit=100, workflow=None, json=False)
            rc = await _cmd_recovery_scan(args)

        assert rc == 1

    @pytest.mark.asyncio
    async def test_scan_json_output(self, capsys):
        from agent_app.cli import _cmd_recovery_scan

        mock_app = MagicMock()
        mock_app.scan_recovery_candidates = AsyncMock(
            return_value=RecoveryScanResult(
                total_scanned=1,
                candidate_count=1,
                candidates=[
                    RecoveryCandidate(
                        run_id="wr-json",
                        status="failed",
                        recommendation=RecoveryRecommendation.RESUME,
                    ),
                ],
            )
        )

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            args = _make_args(config="test.yaml", limit=100, workflow=None, json=True)
            rc = await _cmd_recovery_scan(args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "wr-json" in captured.out


class TestRecoveryInspectCLI:
    """agentapp recovery inspect"""

    @pytest.mark.asyncio
    async def test_inspect_exits_zero(self):
        from agent_app.cli import _cmd_recovery_inspect

        mock_app = MagicMock()
        mock_app.inspect_recovery_candidate = AsyncMock(
            return_value=RecoveryCandidate(
                run_id="wr-ins",
                status="failed",
                recommendation=RecoveryRecommendation.RESUME,
            )
        )

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            args = _make_args(run_id="wr-ins", config="test.yaml", json=False)
            rc = await _cmd_recovery_inspect(args)

        assert rc == 0
        mock_app.inspect_recovery_candidate.assert_called_once_with("wr-ins")

    @pytest.mark.asyncio
    async def test_inspect_missing_run_exits_nonzero(self):
        from agent_app.cli import _cmd_recovery_inspect

        mock_app = MagicMock()
        mock_app.inspect_recovery_candidate = AsyncMock(
            side_effect=KeyError("not found")
        )

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            args = _make_args(run_id="missing", config="test.yaml", json=False)
            rc = await _cmd_recovery_inspect(args)

        assert rc == 1


class TestRecoveryRecoverCLI:
    """agentapp recovery recover"""

    @pytest.mark.asyncio
    async def test_recover_success_exits_zero(self):
        from agent_app.cli import _cmd_recovery_recover

        mock_result = MagicMock()
        mock_result.run_id = "wr-ok"
        mock_result.attempted = True
        mock_result.recovered = True
        mock_result.status = "completed"
        mock_result.error = None

        mock_app = MagicMock()
        mock_app.recover_workflow_run = AsyncMock(return_value=mock_result)

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            args = _make_args(
                run_id="wr-ok",
                workflow="test_dag",
                recovered_by="operator-1",
                config="test.yaml",
                json=False,
            )
            rc = await _cmd_recovery_recover(args)

        assert rc == 0
        mock_app.recover_workflow_run.assert_called_once_with(
            workflow="test_dag",
            run_id="wr-ok",
            recovered_by="operator-1",
        )

    @pytest.mark.asyncio
    async def test_recover_blocked_by_active_lease_exits_nonzero(self):
        from agent_app.cli import _cmd_recovery_recover

        mock_result = MagicMock()
        mock_result.run_id = "wr-blocked"
        mock_result.attempted = False
        mock_result.recovered = False
        mock_result.status = "blocked_active_lease"
        mock_result.error = {
            "type": "active_lease",
            "message": "Lease held by other-worker",
        }

        mock_app = MagicMock()
        mock_app.recover_workflow_run = AsyncMock(return_value=mock_result)

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            args = _make_args(
                run_id="wr-blocked",
                workflow="test_dag",
                recovered_by="operator-1",
                config="test.yaml",
                json=False,
            )
            rc = await _cmd_recovery_recover(args)

        assert rc == 1

    @pytest.mark.asyncio
    async def test_recover_missing_config_exits_nonzero(self):
        from agent_app.cli import _cmd_recovery_recover

        with patch("agent_app.config.loader.build_app", side_effect=FileNotFoundError("no config")):
            args = _make_args(
                run_id="wr-x",
                workflow="test_dag",
                recovered_by="operator-1",
                config="missing.yaml",
                json=False,
            )
            rc = await _cmd_recovery_recover(args)

        assert rc == 1


# ---------------------------------------------------------------------------
# Phase 17: Daemon CLI tests
# ---------------------------------------------------------------------------


class TestRecoveryDaemonCLI:
    """agentapp recovery daemon"""

    @pytest.mark.asyncio
    async def test_daemon_once_dry_run_works(self):
        """--once --dry-run executes one tick and exits 0."""
        from agent_app.cli import _cmd_recovery_daemon

        mock_app = MagicMock()
        mock_app.create_recovery_daemon = MagicMock()
        mock_daemon = MagicMock()
        mock_daemon.run_once = AsyncMock(
            return_value=RecoveryDaemonTickResult(
                scanned_count=10,
                selected_count=2,
                recovered_count=2,
                skipped_count=0,
                failed_count=0,
                dry_run=True,
                recovered_run_ids=["run-a", "run-b"],
            )
        )
        mock_app.create_recovery_daemon.return_value = mock_daemon

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            args = _make_args(
                config="test.yaml",
                once=True,
                dry_run=True,
                no_dry_run=False,
                interval_seconds=30.0,
                max_recoveries_per_scan=5,
                max_concurrent_recoveries=1,
                workflow_name=None,
                tenant_id=None,
                json=False,
            )
            rc = await _cmd_recovery_daemon(args)

        assert rc == 0
        mock_daemon.run_once.assert_called_once()

    @pytest.mark.asyncio
    async def test_daemon_once_no_dry_run(self):
        """--once --no-dry-run executes one live tick."""
        from agent_app.cli import _cmd_recovery_daemon

        mock_app = MagicMock()
        mock_app.create_recovery_daemon = MagicMock()
        mock_daemon = MagicMock()
        mock_daemon.run_once = AsyncMock(
            return_value=RecoveryDaemonTickResult(
                scanned_count=5,
                selected_count=1,
                recovered_count=1,
                dry_run=False,
                recovered_run_ids=["run-live"],
            )
        )
        mock_app.create_recovery_daemon.return_value = mock_daemon

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            args = _make_args(
                config="test.yaml",
                once=True,
                dry_run=False,
                no_dry_run=True,
                interval_seconds=30.0,
                max_recoveries_per_scan=5,
                max_concurrent_recoveries=1,
                workflow_name=None,
                tenant_id=None,
                json=False,
            )
            rc = await _cmd_recovery_daemon(args)

        assert rc == 0
        # Policy should have dry_run=False
        policy = mock_daemon.policy
        assert policy.dry_run is False

    @pytest.mark.asyncio
    async def test_daemon_default_is_dry_run(self):
        """Without --no-dry-run, policy.dry_run defaults to True."""
        from agent_app.cli import _cmd_recovery_daemon

        mock_app = MagicMock()
        mock_app.create_recovery_daemon = MagicMock()
        mock_daemon = MagicMock()
        mock_daemon.run_once = AsyncMock(
            return_value=RecoveryDaemonTickResult(dry_run=True)
        )
        mock_app.create_recovery_daemon.return_value = mock_daemon

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            args = _make_args(
                config="test.yaml",
                once=True,
                dry_run=True,
                no_dry_run=False,
                interval_seconds=30.0,
                max_recoveries_per_scan=5,
                max_concurrent_recoveries=1,
                workflow_name=None,
                tenant_id=None,
                json=False,
            )
            rc = await _cmd_recovery_daemon(args)

        assert rc == 0
        policy = mock_daemon.policy
        assert policy.dry_run is True

    @pytest.mark.asyncio
    async def test_daemon_invalid_config_exits_nonzero(self):
        """Invalid config gives non-zero exit."""
        from agent_app.cli import _cmd_recovery_daemon

        with patch("agent_app.config.loader.build_app", side_effect=FileNotFoundError("no config")):
            args = _make_args(
                config="missing.yaml",
                once=True,
                dry_run=True,
                no_dry_run=False,
                interval_seconds=30.0,
                max_recoveries_per_scan=5,
                max_concurrent_recoveries=1,
                workflow_name=None,
                tenant_id=None,
                json=False,
            )
            rc = await _cmd_recovery_daemon(args)

        assert rc == 1

    @pytest.mark.asyncio
    async def test_daemon_missing_deps_exits_nonzero(self):
        """Missing state store/lease backend gives non-zero exit."""
        from agent_app.cli import _cmd_recovery_daemon

        mock_app = MagicMock()
        # No _dag_state_store → RuntimeError
        del mock_app._dag_state_store
        mock_app.create_recovery_daemon = MagicMock(
            side_effect=RuntimeError("No state store")
        )

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            args = _make_args(
                config="test.yaml",
                once=True,
                dry_run=True,
                no_dry_run=False,
                interval_seconds=30.0,
                max_recoveries_per_scan=5,
                max_concurrent_recoveries=1,
                workflow_name=None,
                tenant_id=None,
                json=False,
            )
            rc = await _cmd_recovery_daemon(args)

        assert rc == 1

    @pytest.mark.asyncio
    async def test_daemon_failures_exit_nonzero(self):
        """Tick with failures exits non-zero."""
        from agent_app.cli import _cmd_recovery_daemon

        mock_app = MagicMock()
        mock_app.create_recovery_daemon = MagicMock()
        mock_daemon = MagicMock()
        mock_daemon.run_once = AsyncMock(
            return_value=RecoveryDaemonTickResult(
                scanned_count=5,
                selected_count=1,
                recovered_count=0,
                failed_count=1,
                failures=[{"run_id": "run-f", "error": "boom"}],
            )
        )
        mock_app.create_recovery_daemon.return_value = mock_daemon

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            args = _make_args(
                config="test.yaml",
                once=True,
                dry_run=True,
                no_dry_run=False,
                interval_seconds=30.0,
                max_recoveries_per_scan=5,
                max_concurrent_recoveries=1,
                workflow_name=None,
                tenant_id=None,
                json=False,
            )
            rc = await _cmd_recovery_daemon(args)

        assert rc == 1

    @pytest.mark.asyncio
    async def test_daemon_cli_args_override_policy(self):
        """CLI args override policy settings."""
        from agent_app.cli import _cmd_recovery_daemon

        mock_app = MagicMock()
        mock_app.create_recovery_daemon = MagicMock()
        mock_daemon = MagicMock()
        mock_daemon.run_once = AsyncMock(
            return_value=RecoveryDaemonTickResult(dry_run=True)
        )
        mock_app.create_recovery_daemon.return_value = mock_daemon

        with patch("agent_app.config.loader.build_app", return_value=mock_app):
            args = _make_args(
                config="test.yaml",
                once=True,
                dry_run=False,
                no_dry_run=True,
                interval_seconds=120.0,
                max_recoveries_per_scan=20,
                max_concurrent_recoveries=3,
                workflow_name="custom_wf",
                tenant_id="tenant-x",
                json=False,
            )
            rc = await _cmd_recovery_daemon(args)

        assert rc == 0
        policy = mock_daemon.policy
        assert policy.dry_run is False
        assert policy.interval_seconds == 120.0
        assert policy.max_recoveries_per_scan == 20
        assert policy.max_concurrent_recoveries == 3
        assert policy.workflow_name == "custom_wf"
        assert policy.tenant_id == "tenant-x"
