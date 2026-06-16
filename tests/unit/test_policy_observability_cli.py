"""Phase 39 Task 5: Tests for CLI observability commands."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_app.governance.policy_observability import (
    ApprovalLatencySummary,
    PolicyActionSummary,
    PolicyActorSummary,
    PolicyDecisionCount,
    PolicyObservabilityReport,
    PolicyToolSummary,
)
from tests.conftest import _run_async


def _make_report(**overrides) -> PolicyObservabilityReport:
    """Build a minimal report for testing."""
    defaults = dict(
        report_id="por_test001",
        generated_at=datetime.now(timezone.utc),
        total_decisions=5,
        decisions_by_status=[
            PolicyDecisionCount(status="allowed", count=3),
            PolicyDecisionCount(status="denied", count=2),
        ],
        actions=[
            PolicyActionSummary(
                action_type="tool.execute", allowed=3, denied=2, total=5
            ),
        ],
        actors=[
            PolicyActorSummary(actor_id="user_1", allowed=2, denied=1, total=3),
            PolicyActorSummary(actor_id="user_2", allowed=1, denied=1, total=2),
        ],
        tools=[
            PolicyToolSummary(tool_name="refund.request", allowed=3, denied=2, total=5),
        ],
        approval_latency=ApprovalLatencySummary(
            count=2, average_seconds=45.0, min_seconds=30.0, max_seconds=60.0
        ),
        top_denials=[{"reason": "missing_permission", "count": 2}],
    )
    defaults.update(overrides)
    return PolicyObservabilityReport(**defaults)


def _make_service(report: PolicyObservabilityReport | None = None) -> MagicMock:
    """Create a mock observability service."""
    if report is None:
        report = _make_report()
    service = MagicMock()
    service.generate_report = AsyncMock(return_value=report)
    return service


def _make_app(service=None) -> MagicMock:
    """Create a mock app with observability service."""
    app = MagicMock()
    app.policy_observability_service = service
    return app


def _args(**kwargs) -> argparse.Namespace:
    """Build an argparse Namespace with sensible defaults."""
    defaults = {
        "config": "agentapp.yaml",
        "since": None,
        "until": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# -- Report command tests --


class TestReportHandler:
    def test_report_handler_json_output(self):
        """report --json produces valid JSON with expected fields."""
        from agent_app.cli import _cmd_policy_observability_report

        report = _make_report()
        service = _make_service(report)
        app = _make_app(service)
        args = _args(json=True)

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_observability_report(args))

        assert rc == 0
        service.generate_report.assert_awaited_once()

    def test_report_handler_human_readable(self, capsys):
        """report without --json prints human-readable output."""
        from agent_app.cli import _cmd_policy_observability_report

        report = _make_report()
        service = _make_service(report)
        app = _make_app(service)
        args = _args(json=False)

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_observability_report(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "Policy Observability Report: por_test001" in captured.out
        assert "Total Decisions: 5" in captured.out
        assert "By Status:" in captured.out
        assert "allowed: 3" in captured.out
        assert "denied: 2" in captured.out
        assert "By Action:" in captured.out
        assert "tool.execute" in captured.out
        assert "Top Actors:" in captured.out
        assert "user_1" in captured.out
        assert "Top Tools:" in captured.out
        assert "refund.request" in captured.out
        assert "Approval Latency:" in captured.out
        assert "Top Denials:" in captured.out

    def test_report_with_window_args(self):
        """report --since and --until are parsed and passed to service."""
        from agent_app.cli import _cmd_policy_observability_report

        service = _make_service()
        app = _make_app(service)
        args = _args(
            json=False,
            since="2026-01-01T00:00:00Z",
            until="2026-06-01T00:00:00Z",
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_observability_report(args))

        assert rc == 0
        call_kwargs = service.generate_report.call_args[1]
        assert call_kwargs["window_start"] == datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert call_kwargs["window_end"] == datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)

    def test_report_no_service_returns_1(self, capsys):
        """report returns 1 when service is not configured."""
        from agent_app.cli import _cmd_policy_observability_report

        app = _make_app(service=None)
        app.policy_observability_service = None
        args = _args(json=False)

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_observability_report(args))

        assert rc == 1
        captured = capsys.readouterr()
        assert "not configured" in captured.err

    def test_report_invalid_since_returns_1(self, capsys):
        """report returns 1 for invalid --since datetime."""
        from agent_app.cli import _cmd_policy_observability_report

        service = _make_service()
        app = _make_app(service)
        args = _args(json=False, since="bad-date")

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_observability_report(args))

        assert rc == 1
        captured = capsys.readouterr()
        assert "Invalid datetime" in captured.err

    def test_report_invalid_until_returns_1(self, capsys):
        """report returns 1 for invalid --until datetime."""
        from agent_app.cli import _cmd_policy_observability_report

        service = _make_service()
        app = _make_app(service)
        args = _args(json=False, until="not-a-date")

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_observability_report(args))

        assert rc == 1
        captured = capsys.readouterr()
        assert "Invalid datetime" in captured.err


# -- Export command tests --


class TestExportHandler:
    def test_export_json_works(self, tmp_path):
        """export --format json writes valid JSON to file."""
        from agent_app.cli import _cmd_policy_observability_export

        report = _make_report()
        service = _make_service(report)
        app = _make_app(service)
        output_path = str(tmp_path / "report.json")
        args = _args(format="json", output=output_path)

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_observability_export(args))

        assert rc == 0
        assert os.path.exists(output_path)
        with open(output_path) as f:
            data = json.load(f)
        assert data["report_id"] == "por_test001"
        assert data["total_decisions"] == 5

    def test_export_csv_works(self, tmp_path):
        """export --format csv writes CSV with headers to file."""
        from agent_app.cli import _cmd_policy_observability_export

        report = _make_report()
        service = _make_service(report)
        app = _make_app(service)
        output_path = str(tmp_path / "report.csv")
        args = _args(format="csv", output=output_path)

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_observability_export(args))

        assert rc == 0
        assert os.path.exists(output_path)
        with open(output_path) as f:
            content = f.read()
        assert "section,key,allowed,denied,approval_required,total" in content
        assert "tool.execute" in content

    def test_export_csv_empty_report(self, tmp_path):
        """export --format csv on empty report writes header only."""
        from agent_app.cli import _cmd_policy_observability_export

        report = _make_report(actions=[], actors=[], tools=[])
        service = _make_service(report)
        app = _make_app(service)
        output_path = str(tmp_path / "empty.csv")
        args = _args(format="csv", output=output_path)

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_observability_export(args))

        assert rc == 0
        with open(output_path) as f:
            content = f.read()
        assert content == "section,key,allowed,denied,approval_required,total\n"

    def test_export_no_service_returns_1(self, capsys):
        """export returns 1 when service is not configured."""
        from agent_app.cli import _cmd_policy_observability_export

        app = _make_app(service=None)
        app.policy_observability_service = None
        args = _args(format="json", output="/tmp/out.json")

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_observability_export(args))

        assert rc == 1
        captured = capsys.readouterr()
        assert "not configured" in captured.err


# -- Error handling tests --


class TestObservabilityCLIErrors:
    def test_invalid_datetime_fails(self, capsys):
        """--since with bad date format returns non-zero exit code."""
        from agent_app.cli import _cmd_policy_observability_report

        service = _make_service()
        app = _make_app(service)
        args = _args(json=False, since="2026-13-45T99:99:99")

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_observability_report(args))

        assert rc == 1
        captured = capsys.readouterr()
        assert "Invalid datetime" in captured.err

    def test_unsupported_format_fails(self, capsys, tmp_path):
        """export with unsupported format returns non-zero exit code."""
        from agent_app.cli import _cmd_policy_observability_export

        service = _make_service()
        app = _make_app(service)
        args = _args(format="xml", output=str(tmp_path / "out.xml"))

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_observability_export(args))

        assert rc == 1
        captured = capsys.readouterr()
        assert "Unsupported format" in captured.err

    def test_build_app_failure_returns_1(self, capsys):
        """handler returns 1 when build_app raises."""
        from agent_app.cli import _cmd_policy_observability_report

        args = _args(json=False)

        with patch("agent_app.config.loader.build_app", side_effect=RuntimeError("bad config")):
            rc = _run_async(_cmd_policy_observability_report(args))

        assert rc == 1
        captured = capsys.readouterr()
        assert "Error loading config" in captured.err

    def test_export_write_failure_returns_1(self, capsys, tmp_path):
        """export returns 1 when file write fails."""
        from agent_app.cli import _cmd_policy_observability_export

        service = _make_service()
        app = _make_app(service)
        # Use a path that cannot be written (directory that does not exist)
        args = _args(format="json", output="/nonexistent/dir/report.json")

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_observability_export(args))

        assert rc == 1
        captured = capsys.readouterr()
        assert "Error writing file" in captured.err


# -- Helper function tests --


class TestGetObservabilityService:
    def test_returns_service_when_present(self):
        """_get_observability_service returns the service when app has it."""
        from agent_app.cli import _get_observability_service

        service = MagicMock()
        app = MagicMock()
        app.policy_observability_service = service
        assert _get_observability_service(app) is service

    def test_returns_none_when_missing(self):
        """_get_observability_service returns None when attribute is absent."""
        from agent_app.cli import _get_observability_service

        app = MagicMock(spec=[])  # no attributes
        assert _get_observability_service(app) is None
