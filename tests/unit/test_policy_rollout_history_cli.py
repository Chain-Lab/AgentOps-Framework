"""Phase 45 Task 6: Tests for CLI rollout history/timeline/analytics commands."""

from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import _run_async


# -- Test fixtures and helpers --


def _make_history_event(
    history_event_id: str = "rhe_test001",
    rollout_id: str = "ro_test001",
    event_type: str = "rollout.step.started",
    step_id: str = "s1",
    actor_id: str = "actor1",
    message: str = "Step started",
) -> MagicMock:
    """Build a mock rollout history event."""
    from agent_app.governance.policy_rollout_history import RolloutHistoryEventType
    e = MagicMock()
    e.history_event_id = history_event_id
    e.rollout_id = rollout_id
    e.event_type = RolloutHistoryEventType(event_type)
    e.step_id = step_id
    e.environment = "default"
    e.ring_name = None
    e.actor_id = actor_id
    e.source_type = None
    e.source_id = None
    e.message = message
    e.metadata = {}
    e.created_at = datetime.now(timezone.utc)
    return e


def _make_timeline(
    rollout_id: str = "ro_test001",
    name: str = "Test Rollout",
    status: str = "active",
) -> MagicMock:
    """Build a mock rollout timeline."""
    tl = MagicMock()
    tl.rollout_id = rollout_id
    tl.name = name
    tl.bundle_id = "pb_test001"
    tl.status = status
    tl.created_at = datetime.now(timezone.utc)
    tl.started_at = datetime.now(timezone.utc)
    tl.completed_at = None
    tl.duration_seconds = None
    tl.steps = []
    tl.events = []
    tl.model_dump_json = MagicMock(return_value=json.dumps({
        "rollout_id": rollout_id,
        "name": name,
        "status": status,
        "steps": [],
    }))
    return tl


def _make_analytics_report() -> MagicMock:
    """Build a mock rollout analytics report."""
    from agent_app.governance.policy_rollout_history import (
        RolloutGateOutcomeSummary,
        RolloutApprovalOutcomeSummary,
        RolloutAnalyticsReport,
    )
    report = RolloutAnalyticsReport(
        report_id="rar_test001",
        generated_at=datetime.now(timezone.utc),
        total_rollouts=3,
        completed_rollouts=1,
        failed_rollouts=1,
        cancelled_rollouts=0,
        blocked_rollouts=1,
        gate_outcomes=RolloutGateOutcomeSummary(
            total=5,
            satisfied=3,
            blocked=1,
            failed=1,
            skipped=0,
            expired=0,
        ),
        approval_outcomes=RolloutApprovalOutcomeSummary(
            total=4,
            pending=1,
            approved=2,
            rejected=1,
            expired=0,
            average_latency_seconds=120.0,
        ),
        top_blocked_steps=[{"step_id": "s3", "count": 2}],
        top_failed_gates=[{"step_id": "s2", "count": 1}],
    )
    return report


def _make_app(rollout_history_service=None) -> MagicMock:
    """Create a mock app with rollout_history_service attribute."""
    app = MagicMock()
    app.rollout_history_service = rollout_history_service
    return app


# -- Tests --


class TestRolloutHistoryCommand:
    def test_rollout_history_command(self, capsys):
        """policy rollout history shows events for rollout."""
        from agent_app.cli import _cmd_policy_rollout_history

        events = [_make_history_event()]
        mock_service = MagicMock()
        mock_service.list_history_events = AsyncMock(return_value=events)

        app = _make_app(rollout_history_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            rollout_id="ro_test001",
            limit=50,
            event_type=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_rollout_history(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "rhe_test001" in captured.out

    def test_rollout_history_missing_rollout(self, capsys):
        """policy rollout history shows no events for missing rollout."""
        from agent_app.cli import _cmd_policy_rollout_history

        mock_service = MagicMock()
        mock_service.list_history_events = AsyncMock(return_value=[])

        app = _make_app(rollout_history_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            rollout_id="ro_missing",
            limit=50,
            event_type=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_rollout_history(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "No history events" in captured.out

    def test_rollout_history_not_configured(self, capsys):
        """policy rollout history exits non-zero when service not configured."""
        from agent_app.cli import _cmd_policy_rollout_history

        app = _make_app(rollout_history_service=None)

        args = argparse.Namespace(
            config="agentapp.yaml",
            rollout_id="ro_test001",
            limit=50,
            event_type=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_rollout_history(args))

        assert rc != 0
        captured = capsys.readouterr()
        assert "not configured" in captured.err.lower()


class TestRolloutTimelineCommand:
    def test_rollout_timeline_command(self, capsys):
        """policy rollout timeline shows timeline."""
        from agent_app.cli import _cmd_policy_rollout_timeline

        timeline = _make_timeline()
        mock_service = MagicMock()
        mock_service.get_timeline = AsyncMock(return_value=timeline)

        app = _make_app(rollout_history_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            rollout_id="ro_test001",
            json=False,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_rollout_timeline(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "Test Rollout" in captured.out
        assert "active" in captured.out

    def test_rollout_timeline_json(self, capsys):
        """policy rollout timeline --json outputs JSON."""
        from agent_app.cli import _cmd_policy_rollout_timeline

        timeline = _make_timeline()
        mock_service = MagicMock()
        mock_service.get_timeline = AsyncMock(return_value=timeline)

        app = _make_app(rollout_history_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            rollout_id="ro_test001",
            json=True,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_rollout_timeline(args))

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["rollout_id"] == "ro_test001"
        assert data["name"] == "Test Rollout"


class TestRolloutAnalyticsCommand:
    def test_rollout_analytics_command(self, capsys):
        """policy rollout analytics shows analytics report."""
        from agent_app.cli import _cmd_policy_rollout_analytics

        report = _make_analytics_report()
        mock_service = MagicMock()
        mock_service.generate_report = AsyncMock(return_value=report)

        app = _make_app(rollout_history_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            since=None,
            until=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_rollout_analytics(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "rar_test001" in captured.out
        assert "Total: 3" in captured.out
        assert "Completed: 1" in captured.out
        assert "Failed: 1" in captured.out
        assert "Blocked: 1" in captured.out

    def test_rollout_analytics_with_window(self, capsys):
        """policy rollout analytics with --since and --until."""
        from agent_app.cli import _cmd_policy_rollout_analytics

        report = _make_analytics_report()
        mock_service = MagicMock()
        mock_service.generate_report = AsyncMock(return_value=report)

        app = _make_app(rollout_history_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            since="2026-06-01T00:00:00+00:00",
            until="2026-06-15T23:59:59+00:00",
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_rollout_analytics(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "rar_test001" in captured.out

    def test_rollout_analytics_invalid_datetime(self, capsys):
        """policy rollout analytics exits non-zero for invalid datetime."""
        from agent_app.cli import _cmd_policy_rollout_analytics

        mock_service = MagicMock()
        app = _make_app(rollout_history_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            since="not-a-datetime",
            until=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_rollout_analytics(args))

        assert rc != 0
        captured = capsys.readouterr()
        assert "Invalid" in captured.err


class TestRolloutAnalyticsExportCommand:
    def test_rollout_analytics_export_json(self, capsys):
        """policy rollout analytics export exports to JSON file."""
        from agent_app.cli import _cmd_policy_rollout_analytics_export

        report = _make_analytics_report()
        mock_service = MagicMock()
        mock_service.generate_report = AsyncMock(return_value=report)

        app = _make_app(rollout_history_service=mock_service)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            output_path = tmp.name

        try:
            args = argparse.Namespace(
                config="agentapp.yaml",
                format="json",
                output=output_path,
                since=None,
                until=None,
            )

            with patch("agent_app.config.loader.build_app", return_value=app):
                rc = _run_async(_cmd_policy_rollout_analytics_export(args))

            assert rc == 0
            with open(output_path) as f:
                data = json.load(f)
            assert data["report_id"] == "rar_test001"
            assert data["total_rollouts"] == 3
        finally:
            os.unlink(output_path)

    def test_rollout_analytics_export_csv(self, capsys):
        """policy rollout analytics export exports to CSV file."""
        from agent_app.cli import _cmd_policy_rollout_analytics_export

        report = _make_analytics_report()
        mock_service = MagicMock()
        mock_service.generate_report = AsyncMock(return_value=report)

        app = _make_app(rollout_history_service=mock_service)

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            output_path = tmp.name

        try:
            args = argparse.Namespace(
                config="agentapp.yaml",
                format="csv",
                output=output_path,
                since=None,
                until=None,
            )

            with patch("agent_app.config.loader.build_app", return_value=app):
                rc = _run_async(_cmd_policy_rollout_analytics_export(args))

            assert rc == 0
            with open(output_path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            # Should have multiple rows (summary, gate_outcomes, approval_outcomes, etc.)
            assert len(rows) > 0
            # Check summary row
            summary_rows = [r for r in rows if r["section"] == "summary"]
            assert len(summary_rows) == 1
            assert summary_rows[0]["total_rollouts"] == "3"
        finally:
            os.unlink(output_path)

    def test_rollout_analytics_export_unsupported_format(self, capsys):
        """policy rollout analytics export exits non-zero for bad format."""
        from agent_app.cli import _cmd_policy_rollout_analytics_export

        report = _make_analytics_report()
        mock_service = MagicMock()
        mock_service.generate_report = AsyncMock(return_value=report)

        app = _make_app(rollout_history_service=mock_service)

        # argparse choices will reject invalid format at parse time,
        # so we simulate the scenario by bypassing argparse validation
        args = argparse.Namespace(
            config="agentapp.yaml",
            format="xml",  # Not in choices, but we bypass argparse
            output="/tmp/test.xml",
            since=None,
            until=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_rollout_analytics_export(args))

        assert rc != 0
        captured = capsys.readouterr()
        assert "Unsupported" in captured.err or "unsupported" in captured.err.lower()
