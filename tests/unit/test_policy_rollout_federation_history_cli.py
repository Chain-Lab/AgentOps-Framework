"""Tests for Phase 47 federation history/timeline/analytics CLI commands."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from agent_app.governance.policy_rollout_federation_history import (
    FederationAnalyticsReport,
    FederationHistoryEvent,
    FederationHistoryEventType,
    FederationTargetHealthSummary,
    FederationTimeline,
    FederationWaveOutcomeSummary,
    FederationConflictSummary,
)


def _run(coro):
    return asyncio.run(coro)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event(
    event_type: FederationHistoryEventType = FederationHistoryEventType.FEDERATION_STARTED,
    target_id: str | None = None,
    wave_id: str | None = None,
    message: str | None = None,
) -> FederationHistoryEvent:
    return FederationHistoryEvent(
        history_event_id=f"fhe_test_{event_type.value}",
        federation_id="frp_test",
        target_id=target_id,
        wave_id=wave_id,
        event_type=event_type,
        message=message,
        created_at=_now(),
    )


def _timeline() -> FederationTimeline:
    return FederationTimeline(
        federation_id="frp_test",
        name="global rollout",
        bundle_id="pb_123",
        strategy="sequential",
        status="completed",
        created_at=_now(),
        started_at=_now(),
        completed_at=_now(),
        duration_seconds=42.0,
    )


def _report() -> FederationAnalyticsReport:
    return FederationAnalyticsReport(
        report_id="far_test",
        generated_at=_now(),
        total_federations=5,
        active_federations=1,
        completed_federations=3,
        failed_federations=1,
        cancelled_federations=0,
        blocked_federations=0,
        target_health=FederationTargetHealthSummary(
            total_targets=10,
            enabled_targets=10,
            succeeded_targets=8,
            failed_targets=1,
            blocked_targets=1,
        ),
        wave_outcomes=FederationWaveOutcomeSummary(
            total_waves=6,
            succeeded_waves=5,
            failed_waves=1,
        ),
        conflicts=FederationConflictSummary(
            total_conflicts=2,
            error_conflicts=1,
            warning_conflicts=1,
        ),
        top_failed_targets=[{"target_id": "frt_bad", "count": 3}],
        top_blocked_targets=[{"target_id": "frt_stuck", "count": 2}],
    )


def _app(service=None):
    app = MagicMock()
    app.federation_observability_service = service
    return app


class TestFederationHistoryCLI:
    def test_federation_history_lists_events(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_history

        service = MagicMock()
        service.list_history_events = AsyncMock(
            return_value=[
                _event(
                    FederationHistoryEventType.TARGET_EXECUTION_STARTED,
                    target_id="frt_test",
                    wave_id="wave_1",
                    message="started target",
                ),
                _event(
                    FederationHistoryEventType.TARGET_EXECUTION_SUCCEEDED,
                    target_id="frt_test",
                    wave_id="wave_1",
                    message="succeeded target",
                ),
            ]
        )
        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id="frp_test",
            limit=50,
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_history(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "target_execution.started" in output
        assert "target_execution.succeeded" in output
        assert "frt_test" in output
        assert "wave_1" in output

    def test_federation_history_no_events(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_history

        service = MagicMock()
        service.list_history_events = AsyncMock(return_value=[])
        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id="frp_empty",
            limit=50,
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_history(args))
        assert rc == 0
        assert "No history events" in capsys.readouterr().out

    def test_federation_history_missing_id_exits(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_history

        service = MagicMock()
        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id="",
            limit=50,
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_history(args))
        assert rc == 1

    def test_federation_history_service_not_configured(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_history

        app = MagicMock()
        app.federation_observability_service = None
        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id="frp_test",
            limit=50,
        )
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_history(args))
        assert rc == 1
        assert "not configured" in capsys.readouterr().err


class TestFederationTimelineCLI:
    def test_federation_timeline_shows_timeline(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_timeline

        service = MagicMock()
        service.get_timeline = AsyncMock(return_value=_timeline())
        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id="frp_test",
            json=False,
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_timeline(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "global rollout" in output
        assert "completed" in output
        assert "pb_123" in output

    def test_federation_timeline_json_output(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_timeline

        service = MagicMock()
        service.get_timeline = AsyncMock(return_value=_timeline())
        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id="frp_test",
            json=True,
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_timeline(args))
        assert rc == 0
        output = capsys.readouterr().out
        parsed = json.loads(output)
        assert parsed["federation_id"] == "frp_test"
        assert parsed["name"] == "global rollout"

    def test_federation_timeline_missing_id_exits(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_timeline

        service = MagicMock()
        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id="",
            json=False,
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_timeline(args))
        assert rc == 1


class TestFederationAnalyticsCLI:
    def test_federation_analytics_shows_report(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_analytics

        service = MagicMock()
        service.generate_report = AsyncMock(return_value=_report())
        args = argparse.Namespace(
            config="agentapp.yaml",
            since=None,
            until=None,
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_analytics(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "Federation Analytics Report" in output
        assert "far_test" in output
        assert "frt_bad" in output
        assert "frt_stuck" in output

    def test_federation_analytics_with_time_window(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_analytics

        service = MagicMock()
        service.generate_report = AsyncMock(return_value=_report())
        args = argparse.Namespace(
            config="agentapp.yaml",
            since="2025-01-01T00:00:00+00:00",
            until="2025-12-31T23:59:59+00:00",
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_analytics(args))
        assert rc == 0
        assert "Federation Analytics Report" in capsys.readouterr().out

    def test_federation_analytics_service_not_configured(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_analytics

        app = MagicMock()
        app.federation_observability_service = None
        args = argparse.Namespace(
            config="agentapp.yaml",
            since=None,
            until=None,
        )
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_analytics(args))
        assert rc == 1


class TestFederationAnalyticsExportCLI:
    def test_federation_analytics_export_json(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_analytics_export

        service = MagicMock()
        service.generate_report = AsyncMock(return_value=_report())
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            output_path = f.name
        args = argparse.Namespace(
            config="agentapp.yaml",
            format="json",
            output=output_path,
            since=None,
            until=None,
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_analytics_export(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "exported" in output
        with open(output_path) as f:
            data = json.load(f)
        assert data["report_id"] == "far_test"
        assert data["total_federations"] == 5

    def test_federation_analytics_export_csv(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_analytics_export

        service = MagicMock()
        service.generate_report = AsyncMock(return_value=_report())
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            output_path = f.name
        args = argparse.Namespace(
            config="agentapp.yaml",
            format="csv",
            output=output_path,
            since=None,
            until=None,
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_analytics_export(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "exported" in output
        with open(output_path) as f:
            content = f.read()
        assert "summary" in content
        assert "target_health" in content

    def test_federation_analytics_export_unsupported_format(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_analytics_export

        service = MagicMock()
        service.generate_report = AsyncMock(return_value=_report())
        args = argparse.Namespace(
            config="agentapp.yaml",
            format="xml",
            output="/tmp/test_export.xml",
            since=None,
            until=None,
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_analytics_export(args))
        assert rc == 1
        assert "Unsupported" in capsys.readouterr().err

    def test_federation_analytics_export_write_failure(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_analytics_export

        service = MagicMock()
        service.generate_report = AsyncMock(return_value=_report())
        args = argparse.Namespace(
            config="agentapp.yaml",
            format="json",
            output="/nonexistent_dir/impossible/output.json",
            since=None,
            until=None,
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_analytics_export(args))
        assert rc == 1
        assert "Error writing" in capsys.readouterr().err


class TestFederationCLIErrorHandling:
    def test_invalid_datetime_exits_nonzero(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_analytics

        service = MagicMock()
        args = argparse.Namespace(
            config="agentapp.yaml",
            since="not-a-datetime",
            until=None,
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_analytics(args))
        assert rc == 1
        assert "Invalid --since" in capsys.readouterr().err

    def test_invalid_until_datetime_exits_nonzero(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_analytics

        service = MagicMock()
        args = argparse.Namespace(
            config="agentapp.yaml",
            since=None,
            until="bad-date",
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_analytics(args))
        assert rc == 1
        assert "Invalid --until" in capsys.readouterr().err

    def test_config_load_error_exits_nonzero(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_history

        args = argparse.Namespace(
            config="nonexistent.yaml",
            federation_id="frp_test",
            limit=50,
        )
        with patch(
            "agent_app.config.loader.build_app",
            side_effect=FileNotFoundError("Config not found"),
        ):
            rc = _run(_cmd_policy_federation_history(args))
        assert rc == 1
        assert "Error loading config" in capsys.readouterr().err
