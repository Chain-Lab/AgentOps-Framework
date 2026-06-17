"""Phase 44 Task 7: Tests for CLI notification and expiration commands."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import _run_async


# -- Test fixtures and helpers --


def _make_notification(
    notification_id: str = "pn_test001",
    event_type: str = "policy.rollout.approval.expired",
    severity: str = "warning",
    title: str = "Test Notification",
    status: str = "pending",
) -> MagicMock:
    """Build a mock notification."""
    n = MagicMock()
    n.notification_id = notification_id
    n.event_type = event_type
    n.severity = MagicMock()
    n.severity.value = severity
    n.title = title
    n.status = MagicMock()
    n.status.value = status
    n.created_at = datetime.now(timezone.utc)
    n.sent_at = None
    return n


def _make_rule(
    rule_id: str = "pnr_test001",
    name: str = "Test Rule",
    status: str = "enabled",
) -> MagicMock:
    """Build a mock notification rule."""
    r = MagicMock()
    r.rule_id = rule_id
    r.name = name
    r.event_types = ["policy.rollout.approval.expired"]
    r.severity = MagicMock()
    r.severity.value = "warning"
    r.channels = ["log"]
    r.status = MagicMock()
    r.status.value = status
    return r


def _make_sweep_report() -> MagicMock:
    """Build a mock expiration sweep report."""
    from agent_app.governance.policy_expiration import (
        PolicyExpirationAction,
        PolicyExpirationResult,
        PolicyExpirationSweepReport,
        PolicyExpirationTargetType,
    )
    result = PolicyExpirationResult(
        result_id="per_test001",
        target_type=PolicyExpirationTargetType.ROLLOUT_APPROVAL,
        target_id="ap_test001",
        action=PolicyExpirationAction.EXPIRED,
        reason="Approval expired",
        created_at=datetime.now(timezone.utc),
    )
    report = PolicyExpirationSweepReport(
        sweep_id="pes_test001",
        started_at=datetime.now(timezone.utc),
        results=[result],
    )
    report.completed_at = datetime.now(timezone.utc)
    return report


def _make_app(
    notification_service=None,
    expiration_service=None,
) -> MagicMock:
    """Create a mock app with notification/expiration service attributes."""
    app = MagicMock()
    app.notification_service = notification_service
    app.expiration_service = expiration_service
    return app


# -- Tests --


class TestNotificationList:
    def test_list_runs_without_error(self, capsys):
        """policy notification list runs without error."""
        from agent_app.cli import _cmd_policy_notification_list

        mock_service = MagicMock()
        mock_service.list_notifications = AsyncMock(return_value=[])

        app = _make_app(notification_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            status=None,
            event_type=None,
            limit=20,
            json=False,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_notification_list(args))

        assert rc == 0

    def test_list_with_notifications(self, capsys):
        """policy notification list shows notifications."""
        from agent_app.cli import _cmd_policy_notification_list

        notifications = [_make_notification()]
        mock_service = MagicMock()
        mock_service.list_notifications = AsyncMock(return_value=notifications)

        app = _make_app(notification_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            status=None,
            event_type=None,
            limit=20,
            json=False,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_notification_list(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "pn_test001" in captured.out

    def test_list_json_output(self, capsys):
        """policy notification list --json outputs JSON."""
        from agent_app.cli import _cmd_policy_notification_list

        notifications = [_make_notification()]
        mock_service = MagicMock()
        mock_service.list_notifications = AsyncMock(return_value=notifications)

        app = _make_app(notification_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            status=None,
            event_type=None,
            limit=20,
            json=True,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_notification_list(args))

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 1
        assert data[0]["notification_id"] == "pn_test001"

    def test_list_missing_service_exits_nonzero(self, capsys):
        """policy notification list with no service exits non-zero."""
        from agent_app.cli import _cmd_policy_notification_list

        app = _make_app(notification_service=None)

        args = argparse.Namespace(
            config="agentapp.yaml",
            status=None,
            event_type=None,
            limit=20,
            json=False,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_notification_list(args))

        assert rc != 0
        captured = capsys.readouterr()
        assert "not configured" in captured.err.lower() or "not configured" in captured.out.lower()


class TestNotificationSendPending:
    def test_send_pending_runs_without_error(self, capsys):
        """policy notification send-pending runs without error."""
        from agent_app.cli import _cmd_policy_notification_send_pending

        mock_service = MagicMock()
        mock_service.send_pending = AsyncMock(return_value=[])

        app = _make_app(notification_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            limit=None,
            json=False,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_notification_send_pending(args))

        assert rc == 0


class TestNotificationRuleList:
    def test_rule_list_runs_without_error(self, capsys):
        """policy notification rule list runs without error."""
        from agent_app.cli import _cmd_policy_notification_rule_list

        mock_rule_store = MagicMock()
        mock_rule_store.list = AsyncMock(return_value=[])

        mock_service = MagicMock()
        mock_service._rule_store = mock_rule_store

        app = _make_app(notification_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            json=False,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_notification_rule_list(args))

        assert rc == 0

    def test_rule_list_with_rules(self, capsys):
        """policy notification rule list shows rules."""
        from agent_app.cli import _cmd_policy_notification_rule_list

        rules = [_make_rule()]
        mock_rule_store = MagicMock()
        mock_rule_store.list = AsyncMock(return_value=rules)

        mock_service = MagicMock()
        mock_service._rule_store = mock_rule_store

        app = _make_app(notification_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            json=False,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_notification_rule_list(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "pnr_test001" in captured.out


class TestExpirationSweep:
    def test_sweep_runs_without_error(self, capsys):
        """policy expiration sweep runs without error."""
        from agent_app.cli import _cmd_policy_expiration_sweep

        report = _make_sweep_report()
        mock_service = MagicMock()
        mock_service.sweep = AsyncMock(return_value=report)

        app = _make_app(expiration_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            json=False,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_expiration_sweep(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "pes_test001" in captured.out
        assert "Expired: 1" in captured.out

    def test_sweep_json_output(self, capsys):
        """policy expiration sweep --json outputs JSON."""
        from agent_app.cli import _cmd_policy_expiration_sweep

        report = _make_sweep_report()
        mock_service = MagicMock()
        mock_service.sweep = AsyncMock(return_value=report)

        app = _make_app(expiration_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            json=True,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_expiration_sweep(args))

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["sweep_id"] == "pes_test001"
        assert data["total_results"] == 1

    def test_sweep_missing_service_exits_nonzero(self, capsys):
        """policy expiration sweep with no service exits non-zero."""
        from agent_app.cli import _cmd_policy_expiration_sweep

        app = _make_app(expiration_service=None)

        args = argparse.Namespace(
            config="agentapp.yaml",
            json=False,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_expiration_sweep(args))

        assert rc != 0
        captured = capsys.readouterr()
        assert "not configured" in captured.err.lower() or "not configured" in captured.out.lower()


class TestExpirationRunOnce:
    def test_run_once_delegates_to_sweep(self, capsys):
        """policy expiration run-once delegates to sweep handler."""
        from agent_app.cli import _cmd_policy_expiration_run_once

        report = _make_sweep_report()
        mock_service = MagicMock()
        mock_service.sweep = AsyncMock(return_value=report)

        app = _make_app(expiration_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            json=False,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_expiration_run_once(args))

        assert rc == 0
