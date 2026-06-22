"""Phase 44 Task 7: Tests for CLI notification and expiration commands."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
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


# -- Phase 52: Notification observability CLI tests --


def _make_obs_config(store_type="memory", path=".agent_app/test.db"):
    """Build a mock observability config."""
    cfg = MagicMock()
    cfg.store = MagicMock()
    cfg.store.type = store_type
    cfg.store.path = path
    return cfg


def _make_alert_config(store_type="memory", path=".agent_app/test_alerts.db"):
    """Build a mock alert config."""
    cfg = MagicMock()
    cfg.store = MagicMock()
    cfg.store.type = store_type
    cfg.store.path = path
    return cfg


def _make_sla_config():
    """Build a mock SLA config."""
    from agent_app.governance.policy_rollout_federation_notification_sla import (
        NotificationSlaPolicy,
    )
    return NotificationSlaPolicy()


def _make_delivery_event(
    event_id="nde_test001",
    notification_id="fn_test001",
    event_type=None,
    channel="console",
    status="sent",
    attempt=1,
    latency_ms=120,
    federation_id="frp_test001",
    approval_id="ap_test001",
):
    """Build a mock delivery event."""
    from agent_app.governance.policy_rollout_federation_notification_observability import (
        NotificationDeliveryEvent,
        NotificationDeliveryEventType,
    )
    if event_type is None:
        event_type = NotificationDeliveryEventType.SENT
    return NotificationDeliveryEvent(
        event_id=event_id,
        notification_id=notification_id,
        approval_id=approval_id,
        federation_id=federation_id,
        channel=channel,
        event_type=event_type,
        status=status,
        attempt=attempt,
        latency_ms=latency_ms,
        error_code=None,
        error_message=None,
        adapter_name="console",
        template_id=None,
        preference_decision=None,
        metadata={},
        created_at=datetime.now(timezone.utc),
    )


def _make_alert_event(
    alert_id="nae_test001",
    rule_id="nar_test001",
    name="Test Alert",
    severity="warning",
    metric="failure_rate",
    observed_value=0.15,
    threshold=0.05,
    federation_id="frp_test001",
    channel="console",
    message="High failure rate",
    status="open",
):
    """Build a mock alert event."""
    from agent_app.governance.policy_rollout_federation_notification_observability import (
        NotificationAlertEvent,
    )
    return NotificationAlertEvent(
        alert_id=alert_id,
        rule_id=rule_id,
        name=name,
        severity=severity,
        metric=metric,
        observed_value=observed_value,
        threshold=threshold,
        federation_id=federation_id,
        channel=channel,
        message=message,
        status=status,
        created_at=datetime.now(timezone.utc),
        acknowledged_at=None,
        acknowledged_by=None,
        resolved_at=None,
        resolved_by=None,
    )


def _make_metric_window(
    federation_id="frp_test001",
    channel="console",
    total=100,
    sent=95,
    failed=3,
    suppressed=1,
    dlq=1,
    retry_scheduled=0,
    success_rate=0.95,
    failure_rate=0.03,
    dlq_rate=0.01,
    avg_latency_ms=150.0,
    p95_latency_ms=300.0,
):
    """Build a mock metric window."""
    from agent_app.governance.policy_rollout_federation_notification_observability import (
        NotificationMetricWindow,
    )
    now = datetime.now(timezone.utc)
    return NotificationMetricWindow(
        window_start=now - timedelta(minutes=60),
        window_end=now,
        federation_id=federation_id,
        channel=channel,
        total=total,
        sent=sent,
        failed=failed,
        suppressed=suppressed,
        dlq=dlq,
        retry_scheduled=retry_scheduled,
        success_rate=success_rate,
        failure_rate=failure_rate,
        dlq_rate=dlq_rate,
        avg_latency_ms=avg_latency_ms,
        p95_latency_ms=p95_latency_ms,
    )


class TestNotificationEventsList:
    def test_list_runs_without_error(self, capsys):
        """notification events list runs without error."""
        from agent_app.cli import _cmd_policy_federation_notification_events_list

        mock_store = MagicMock()
        mock_store.list_events = AsyncMock(return_value=[])

        app = MagicMock()
        app._federation_notification_observability_config = _make_obs_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            notification_id=None,
            approval_id=None,
            federation_id=None,
            channel=None,
            event_type=None,
            since=None,
            until=None,
            limit=100,
            offset=0,
            format="table",
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_observability_store.create_notification_observability_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_events_list(args))

        assert rc == 0

    def test_list_with_events_table(self, capsys):
        """notification events list shows events in table format."""
        from agent_app.cli import _cmd_policy_federation_notification_events_list

        events = [_make_delivery_event()]
        mock_store = MagicMock()
        mock_store.list_events = AsyncMock(return_value=events)

        app = MagicMock()
        app._federation_notification_observability_config = _make_obs_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            notification_id=None,
            approval_id=None,
            federation_id=None,
            channel=None,
            event_type=None,
            since=None,
            until=None,
            limit=100,
            offset=0,
            format="table",
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_observability_store.create_notification_observability_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_events_list(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "nde_test001" in captured.out

    def test_list_with_events_json(self, capsys):
        """notification events list --format json outputs JSON."""
        from agent_app.cli import _cmd_policy_federation_notification_events_list

        events = [_make_delivery_event()]
        mock_store = MagicMock()
        mock_store.list_events = AsyncMock(return_value=events)

        app = MagicMock()
        app._federation_notification_observability_config = _make_obs_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            notification_id=None,
            approval_id=None,
            federation_id=None,
            channel=None,
            event_type=None,
            since=None,
            until=None,
            limit=100,
            offset=0,
            format="json",
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_observability_store.create_notification_observability_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_events_list(args))

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 1
        assert data[0]["event_id"] == "nde_test001"

    def test_list_missing_config_exits_nonzero(self, capsys):
        """notification events list with no config exits non-zero."""
        from agent_app.cli import _cmd_policy_federation_notification_events_list

        app = MagicMock()
        app._federation_notification_observability_config = None

        args = argparse.Namespace(
            config="agentapp.yaml",
            notification_id=None,
            approval_id=None,
            federation_id=None,
            channel=None,
            event_type=None,
            since=None,
            until=None,
            limit=100,
            offset=0,
            format="table",
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_federation_notification_events_list(args))

        assert rc != 0
        captured = capsys.readouterr()
        assert "not configured" in captured.err.lower()


class TestNotificationMetrics:
    def test_metrics_runs_without_error(self, capsys):
        """notification metrics runs without error."""
        from agent_app.cli import _cmd_policy_federation_notification_metrics

        mock_store = MagicMock()
        mock_store.aggregate_metrics = AsyncMock(return_value=_make_metric_window())

        app = MagicMock()
        app._federation_notification_observability_config = _make_obs_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id=None,
            channel=None,
            window_minutes=60,
            format="table",
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_observability_store.create_notification_observability_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_metrics(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "Total:" in captured.out
        assert "Success Rate:" in captured.out

    def test_metrics_json_output(self, capsys):
        """notification metrics --format json outputs JSON."""
        from agent_app.cli import _cmd_policy_federation_notification_metrics

        mock_store = MagicMock()
        mock_store.aggregate_metrics = AsyncMock(return_value=_make_metric_window())

        app = MagicMock()
        app._federation_notification_observability_config = _make_obs_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id=None,
            channel=None,
            window_minutes=60,
            format="json",
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_observability_store.create_notification_observability_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_metrics(args))

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data[0]["total"] == 100
        assert data[0]["sent"] == 95

    def test_metrics_missing_config_exits_nonzero(self, capsys):
        """notification metrics with no config exits non-zero."""
        from agent_app.cli import _cmd_policy_federation_notification_metrics

        app = MagicMock()
        app._federation_notification_observability_config = None

        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id=None,
            channel=None,
            window_minutes=60,
            format="table",
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_federation_notification_metrics(args))

        assert rc != 0
        captured = capsys.readouterr()
        assert "not configured" in captured.err.lower()


class TestNotificationHealth:
    def test_health_runs_without_error(self, capsys):
        """notification health runs without error."""
        from agent_app.cli import _cmd_policy_federation_notification_health

        mock_store = MagicMock()
        mock_store.aggregate_metrics = AsyncMock(return_value=_make_metric_window())
        mock_store.list_events = AsyncMock(return_value=[_make_delivery_event()])

        app = MagicMock()
        app._federation_notification_observability_config = _make_obs_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            format="table",
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_observability_store.create_notification_observability_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_health(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "Status:" in captured.out

    def test_health_json_output(self, capsys):
        """notification health --format json outputs JSON."""
        from agent_app.cli import _cmd_policy_federation_notification_health

        mock_store = MagicMock()
        mock_store.aggregate_metrics = AsyncMock(return_value=_make_metric_window())
        mock_store.list_events = AsyncMock(return_value=[_make_delivery_event()])

        app = MagicMock()
        app._federation_notification_observability_config = _make_obs_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            format="json",
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_observability_store.create_notification_observability_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_health(args))

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "status" in data
        assert "success_rate" in data

    def test_health_no_data(self, capsys):
        """notification health with no data shows message."""
        from agent_app.cli import _cmd_policy_federation_notification_health

        empty_window = _make_metric_window(total=0, sent=0, failed=0)
        mock_store = MagicMock()
        mock_store.aggregate_metrics = AsyncMock(return_value=empty_window)
        mock_store.list_events = AsyncMock(return_value=[])

        app = MagicMock()
        app._federation_notification_observability_config = _make_obs_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            format="table",
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_observability_store.create_notification_observability_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_health(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "No data" in captured.out

    def test_health_missing_config_exits_nonzero(self, capsys):
        """notification health with no config exits non-zero."""
        from agent_app.cli import _cmd_policy_federation_notification_health

        app = MagicMock()
        app._federation_notification_observability_config = None

        args = argparse.Namespace(
            config="agentapp.yaml",
            format="table",
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_federation_notification_health(args))

        assert rc != 0
        captured = capsys.readouterr()
        assert "not configured" in captured.err.lower()


class TestNotificationSlaCheck:
    def test_sla_check_runs_without_error(self, capsys):
        """notification sla check runs without error."""
        from agent_app.cli import _cmd_policy_federation_notification_sla_check

        mock_store = MagicMock()
        mock_store.aggregate_metrics = AsyncMock(return_value=_make_metric_window())

        app = MagicMock()
        app._federation_notification_observability_config = _make_obs_config()
        app._federation_notification_sla_config = _make_sla_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id=None,
            channel=None,
            format="table",
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_observability_store.create_notification_observability_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_sla_check(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "No SLA violations found" in captured.out

    def test_sla_check_with_violations(self, capsys):
        """notification sla check shows violations."""
        from agent_app.cli import _cmd_policy_federation_notification_sla_check

        # Create a metric window that violates SLA
        bad_window = _make_metric_window(
            success_rate=0.8,
            failure_rate=0.15,
            dlq_rate=0.05,
        )
        mock_store = MagicMock()
        mock_store.aggregate_metrics = AsyncMock(return_value=bad_window)

        app = MagicMock()
        app._federation_notification_observability_config = _make_obs_config()
        app._federation_notification_sla_config = _make_sla_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id=None,
            channel=None,
            format="table",
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_observability_store.create_notification_observability_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_sla_check(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "Violation ID" in captured.out

    def test_sla_check_json_output(self, capsys):
        """notification sla check --format json outputs JSON."""
        from agent_app.cli import _cmd_policy_federation_notification_sla_check

        bad_window = _make_metric_window(
            success_rate=0.8,
            failure_rate=0.15,
            dlq_rate=0.05,
        )
        mock_store = MagicMock()
        mock_store.aggregate_metrics = AsyncMock(return_value=bad_window)

        app = MagicMock()
        app._federation_notification_observability_config = _make_obs_config()
        app._federation_notification_sla_config = _make_sla_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id=None,
            channel=None,
            format="json",
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_observability_store.create_notification_observability_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_sla_check(args))

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)

    def test_sla_check_missing_observability_config_exits_nonzero(self, capsys):
        """notification sla check with no observability config exits non-zero."""
        from agent_app.cli import _cmd_policy_federation_notification_sla_check

        app = MagicMock()
        app._federation_notification_observability_config = None
        app._federation_notification_sla_config = _make_sla_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id=None,
            channel=None,
            format="table",
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_federation_notification_sla_check(args))

        assert rc != 0
        captured = capsys.readouterr()
        assert "not configured" in captured.err.lower()


class TestNotificationAlertsList:
    def test_alerts_list_runs_without_error(self, capsys):
        """notification alerts list runs without error."""
        from agent_app.cli import _cmd_policy_federation_notification_alerts_list

        mock_store = MagicMock()
        mock_store.list_alerts = AsyncMock(return_value=[])

        app = MagicMock()
        app._federation_notification_alert_config = _make_alert_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            status=None,
            severity=None,
            channel=None,
            federation_id=None,
            limit=100,
            offset=0,
            format="table",
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_alert_store.create_notification_alert_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_alerts_list(args))

        assert rc == 0

    def test_alerts_list_with_alerts_table(self, capsys):
        """notification alerts list shows alerts in table format."""
        from agent_app.cli import _cmd_policy_federation_notification_alerts_list

        alerts = [_make_alert_event()]
        mock_store = MagicMock()
        mock_store.list_alerts = AsyncMock(return_value=alerts)

        app = MagicMock()
        app._federation_notification_alert_config = _make_alert_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            status=None,
            severity=None,
            channel=None,
            federation_id=None,
            limit=100,
            offset=0,
            format="table",
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_alert_store.create_notification_alert_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_alerts_list(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "nae_test001" in captured.out

    def test_alerts_list_json_output(self, capsys):
        """notification alerts list --format json outputs JSON."""
        from agent_app.cli import _cmd_policy_federation_notification_alerts_list

        alerts = [_make_alert_event()]
        mock_store = MagicMock()
        mock_store.list_alerts = AsyncMock(return_value=alerts)

        app = MagicMock()
        app._federation_notification_alert_config = _make_alert_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            status=None,
            severity=None,
            channel=None,
            federation_id=None,
            limit=100,
            offset=0,
            format="json",
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_alert_store.create_notification_alert_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_alerts_list(args))

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 1
        assert data[0]["alert_id"] == "nae_test001"

    def test_alerts_list_missing_config_exits_nonzero(self, capsys):
        """notification alerts list with no config exits non-zero."""
        from agent_app.cli import _cmd_policy_federation_notification_alerts_list

        app = MagicMock()
        app._federation_notification_alert_config = None

        args = argparse.Namespace(
            config="agentapp.yaml",
            status=None,
            severity=None,
            channel=None,
            federation_id=None,
            limit=100,
            offset=0,
            format="table",
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_federation_notification_alerts_list(args))

        assert rc != 0
        captured = capsys.readouterr()
        assert "not configured" in captured.err.lower()


class TestNotificationAlertsAck:
    def test_alerts_ack_success(self, capsys):
        """notification alerts ack succeeds."""
        from agent_app.cli import _cmd_policy_federation_notification_alerts_ack

        alert = _make_alert_event(status="open")
        alert.acknowledged_at = datetime.now(timezone.utc)
        alert.acknowledged_by = "cli-user"
        alert.status = "acknowledged"

        mock_store = MagicMock()
        mock_store.acknowledge = AsyncMock(return_value=alert)

        app = MagicMock()
        app._federation_notification_alert_config = _make_alert_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            alert_id="nae_test001",
            by="cli-user",
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_alert_store.create_notification_alert_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_alerts_ack(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "acknowledged" in captured.out

    def test_alerts_ack_not_found(self, capsys):
        """notification alerts ack with unknown alert exits non-zero."""
        from agent_app.cli import _cmd_policy_federation_notification_alerts_ack

        mock_store = MagicMock()
        mock_store.acknowledge = AsyncMock(return_value=None)

        app = MagicMock()
        app._federation_notification_alert_config = _make_alert_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            alert_id="nae_unknown",
            by="cli-user",
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_alert_store.create_notification_alert_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_alerts_ack(args))

        assert rc != 0


class TestNotificationAlertsResolve:
    def test_alerts_resolve_success(self, capsys):
        """notification alerts resolve succeeds."""
        from agent_app.cli import _cmd_policy_federation_notification_alerts_resolve

        alert = _make_alert_event(status="open")
        alert.resolved_at = datetime.now(timezone.utc)
        alert.resolved_by = "cli-user"
        alert.status = "resolved"

        mock_store = MagicMock()
        mock_store.resolve = AsyncMock(return_value=alert)

        app = MagicMock()
        app._federation_notification_alert_config = _make_alert_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            alert_id="nae_test001",
            by="cli-user",
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_alert_store.create_notification_alert_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_alerts_resolve(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "resolved" in captured.out

    def test_alerts_resolve_not_found(self, capsys):
        """notification alerts resolve with unknown alert exits non-zero."""
        from agent_app.cli import _cmd_policy_federation_notification_alerts_resolve

        mock_store = MagicMock()
        mock_store.resolve = AsyncMock(return_value=None)

        app = MagicMock()
        app._federation_notification_alert_config = _make_alert_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            alert_id="nae_unknown",
            by="cli-user",
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_alert_store.create_notification_alert_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_alerts_resolve(args))

        assert rc != 0


class TestNotificationReportExport:
    def test_report_export_events_json(self, capsys):
        """notification report export --type events --format json outputs JSON."""
        from agent_app.cli import _cmd_policy_federation_notification_report_export

        events = [_make_delivery_event()]
        mock_store = MagicMock()
        mock_store.list_events = AsyncMock(return_value=events)

        app = MagicMock()
        app._federation_notification_observability_config = _make_obs_config()
        app._federation_notification_alert_config = _make_alert_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            type="events",
            format="json",
            federation_id=None,
            channel=None,
            window_minutes=60,
            output=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_observability_store.create_notification_observability_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_report_export(args))

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 1
        assert data[0]["event_id"] == "nde_test001"

    def test_report_export_metrics_table(self, capsys):
        """notification report export --type metrics outputs metrics."""
        from agent_app.cli import _cmd_policy_federation_notification_report_export

        mock_store = MagicMock()
        mock_store.aggregate_metrics = AsyncMock(return_value=_make_metric_window())

        app = MagicMock()
        app._federation_notification_observability_config = _make_obs_config()
        app._federation_notification_alert_config = _make_alert_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            type="metrics",
            format="json",
            federation_id=None,
            channel=None,
            window_minutes=60,
            output=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_observability_store.create_notification_observability_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_report_export(args))

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data[0]["total"] == 100

    def test_report_export_alerts_table(self, capsys):
        """notification report export --type alerts outputs alerts."""
        from agent_app.cli import _cmd_policy_federation_notification_report_export

        alerts = [_make_alert_event()]
        mock_store = MagicMock()
        mock_store.list_alerts = AsyncMock(return_value=alerts)

        app = MagicMock()
        app._federation_notification_observability_config = _make_obs_config()
        app._federation_notification_alert_config = _make_alert_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            type="alerts",
            format="json",
            federation_id=None,
            channel=None,
            window_minutes=60,
            output=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_alert_store.create_notification_alert_store", return_value=mock_store):
            rc = _run_async(_cmd_policy_federation_notification_report_export(args))

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 1
        assert data[0]["alert_id"] == "nae_test001"

    def test_report_export_missing_observability_config_exits_nonzero(self, capsys):
        """notification report export --type events with no config exits non-zero."""
        from agent_app.cli import _cmd_policy_federation_notification_report_export

        app = MagicMock()
        app._federation_notification_observability_config = None
        app._federation_notification_alert_config = _make_alert_config()

        args = argparse.Namespace(
            config="agentapp.yaml",
            type="events",
            format="json",
            federation_id=None,
            channel=None,
            window_minutes=60,
            output=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_federation_notification_report_export(args))

        assert rc != 0


# ---------------------------------------------------------------------------
# Phase 53: Alert delivery CLI
# ---------------------------------------------------------------------------


class TestPhase53AlertDeliveryCLI:
    def test_alert_deliver_dry_run(self, capsys):
        """alerts deliver --alert-id --dry-run calls service."""
        from agent_app.cli import _cmd_policy_federation_notification_alert_deliver
        from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_service import (
            NotificationAlertDeliveryService,
        )

        app = MagicMock()
        app._federation_notification_alert_delivery_config = MagicMock()
        app._federation_notification_alert_delivery_config.enabled = True
        app._federation_notification_alert_delivery_config.store.type = "memory"
        app._federation_notification_alert_delivery_config.store.path = None
        app._federation_notification_alert_delivery_config.retry.max_attempts = 3
        app._federation_notification_observability_store = MagicMock()
        app._federation_notification_observability_store.list_events = AsyncMock(return_value=[])

        captured_results = []

        async def mock_deliver(self, alert_id, dry_run=False):
            from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
                AlertDeliveryAttempt,
                AlertDeliveryStatus,
                AlertDeliveryChannelType,
            )
            from datetime import datetime, timezone
            attempt = AlertDeliveryAttempt(
                attempt_id="nda_1",
                alert_id=alert_id,
                target_id="ndt_1",
                channel_type=AlertDeliveryChannelType.CONSOLE,
                status=AlertDeliveryStatus.SUPPRESSED if dry_run else AlertDeliveryStatus.DELIVERED,
                created_at=datetime.now(timezone.utc),
            )
            captured_results.append(attempt)
            return [attempt]

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch.object(NotificationAlertDeliveryService, "deliver_alert", mock_deliver):
            args = argparse.Namespace(
                config="agentapp.yaml",
                alert_id="nae_1",
                dry_run=True,
            )
            rc = _run_async(_cmd_policy_federation_notification_alert_deliver(args))

        assert rc == 0
        assert len(captured_results) == 1
        assert captured_results[0].status.value == "suppressed"

    def test_alert_deliver_missing_config_exits_nonzero(self, capsys):
        """alerts deliver with no config exits non-zero."""
        from agent_app.cli import _cmd_policy_federation_notification_alert_deliver

        app = MagicMock()
        app._federation_notification_alert_delivery_config = None

        args = argparse.Namespace(
            config="agentapp.yaml",
            alert_id="nae_1",
            dry_run=False,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_federation_notification_alert_deliver(args))

        assert rc != 0

    def test_alert_delivery_targets_list(self, capsys):
        """alerts delivery targets list prints targets."""
        from agent_app.cli import _cmd_policy_federation_notification_alert_delivery_targets_list
        from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_store import (
            InMemoryAlertDeliveryStore,
        )
        from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
            AlertDeliveryTarget,
            AlertDeliveryChannelType,
        )

        store = InMemoryAlertDeliveryStore()
        target = AlertDeliveryTarget(
            target_id="ndt_1",
            name="Ops",
            channel_type=AlertDeliveryChannelType.CONSOLE,
            enabled=True,
        )
        _run_async(store.create_target(target))

        app = MagicMock()
        app._federation_notification_alert_delivery_config = MagicMock()
        app._federation_notification_alert_delivery_config.enabled = True
        app._federation_notification_alert_delivery_config.store.type = "memory"
        app._federation_notification_alert_delivery_config.store.path = None

        args = argparse.Namespace(config="agentapp.yaml")

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_alert_delivery_store.create_alert_delivery_store", return_value=store):
            rc = _run_async(_cmd_policy_federation_notification_alert_delivery_targets_list(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "ndt_1" in captured.out
        assert "Ops" in captured.out


# ---------------------------------------------------------------------------
# Phase 53: Prometheus CLI
# ---------------------------------------------------------------------------


class TestPhase53PrometheusCLI:
    def test_prometheus_export(self, capsys):
        """prometheus export prints metrics."""
        from agent_app.cli import _cmd_policy_federation_notification_prometheus_export
        from agent_app.runtime.policy_rollout_federation_notification_observability_store import (
            InMemoryNotificationObservabilityStore,
        )

        store = InMemoryNotificationObservabilityStore()
        from agent_app.governance.policy_rollout_federation_notification_observability import (
            NotificationDeliveryEvent,
            NotificationDeliveryEventType,
        )
        from datetime import datetime, timezone
        event = NotificationDeliveryEvent(
            event_id="nde_1",
            event_type=NotificationDeliveryEventType.SENT,
            channel="webhook",
            created_at=datetime.now(timezone.utc),
        )
        _run_async(store.record_event(event))

        app = MagicMock()
        app._federation_notification_observability_config = MagicMock()
        app._federation_notification_observability_config.enabled = True
        app._federation_notification_observability_config.store.type = "memory"
        app._federation_notification_observability_config.store.path = None

        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id=None,
            channel=None,
            window_minutes=60,
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_observability_store.create_notification_observability_store", return_value=store):
            rc = _run_async(_cmd_policy_federation_notification_prometheus_export(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "# HELP" in captured.out or "# TYPE" in captured.out


# ---------------------------------------------------------------------------
# Phase 53: JSONL CLI
# ---------------------------------------------------------------------------


class TestPhase53JsonlCLI:
    def test_jsonl_export_events(self, capsys):
        """jsonl export --type events prints JSONL."""
        from agent_app.cli import _cmd_policy_federation_notification_jsonl_export
        from agent_app.runtime.policy_rollout_federation_notification_observability_store import (
            InMemoryNotificationObservabilityStore,
        )

        store = InMemoryNotificationObservabilityStore()
        from agent_app.governance.policy_rollout_federation_notification_observability import (
            NotificationDeliveryEvent,
            NotificationDeliveryEventType,
        )
        from datetime import datetime, timezone
        event = NotificationDeliveryEvent(
            event_id="nde_1",
            event_type=NotificationDeliveryEventType.SENT,
            channel="webhook",
            created_at=datetime.now(timezone.utc),
        )
        _run_async(store.record_event(event))

        app = MagicMock()
        app._federation_notification_observability_config = MagicMock()
        app._federation_notification_observability_config.enabled = True
        app._federation_notification_observability_config.store.type = "memory"
        app._federation_notification_observability_config.store.path = None
        app._federation_notification_alert_config = None

        args = argparse.Namespace(
            config="agentapp.yaml",
            type="events",
            federation_id=None,
            channel=None,
            window_minutes=60,
            output=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_observability_store.create_notification_observability_store", return_value=store):
            rc = _run_async(_cmd_policy_federation_notification_jsonl_export(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "nde_1" in captured.out


# ---------------------------------------------------------------------------
# Phase 53: Retention CLI
# ---------------------------------------------------------------------------


class TestPhase53RetentionCLI:
    def test_retention_cleanup_dry_run(self, capsys):
        """retention cleanup --dry-run runs without deleting."""
        from agent_app.cli import _cmd_policy_federation_notification_retention_cleanup

        app = MagicMock()
        retention_cfg = MagicMock()
        retention_cfg.enabled = True
        retention_cfg.raw_event_retention_days = 30
        retention_cfg.archive_before_purge = True
        retention_cfg.archive_format = "jsonl"
        retention_cfg.archive_dir = ".agent_app/archives/federation_notifications"
        app._federation_notification_retention_config = retention_cfg
        obs_store = MagicMock()
        obs_store.list_events = AsyncMock(return_value=[])
        app._federation_notification_observability_store = obs_store

        args = argparse.Namespace(
            config="agentapp.yaml",
            dry_run=True,
            yes=True,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_federation_notification_retention_cleanup(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "Dry run: True" in captured.out

    def test_retention_cleanup_not_configured_exits_nonzero(self):
        """retention cleanup with no config exits non-zero."""
        from agent_app.cli import _cmd_policy_federation_notification_retention_cleanup

        app = MagicMock()
        app._federation_notification_retention_config = None

        args = argparse.Namespace(
            config="agentapp.yaml",
            dry_run=True,
            yes=True,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_federation_notification_retention_cleanup(args))

        assert rc != 0


# ---------------------------------------------------------------------------
# Phase 53: Rollup CLI
# ---------------------------------------------------------------------------


class TestPhase53RollupCLI:
    def test_rollup_build(self, capsys):
        """rollup build --granularity hourly builds rollups."""
        from agent_app.cli import _cmd_policy_federation_notification_rollup_build
        from agent_app.runtime.policy_rollout_federation_notification_observability_store import (
            InMemoryNotificationObservabilityStore,
        )
        from agent_app.runtime.policy_rollout_federation_notification_rollup import (
            InMemoryNotificationRollupStore,
        )

        obs_store = InMemoryNotificationObservabilityStore()
        rollup_store = InMemoryNotificationRollupStore()
        from agent_app.governance.policy_rollout_federation_notification_observability import (
            NotificationDeliveryEvent,
            NotificationDeliveryEventType,
        )
        from datetime import datetime, timezone
        event = NotificationDeliveryEvent(
            event_id="nde_1",
            event_type=NotificationDeliveryEventType.SENT,
            channel="webhook",
            created_at=datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0),
            latency_ms=100,
        )
        _run_async(obs_store.record_event(event))

        app = MagicMock()
        app._federation_notification_rollup_config = MagicMock()
        app._federation_notification_rollup_config.enabled = True
        app._federation_notification_rollup_config.store.type = "memory"
        app._federation_notification_rollup_config.store.path = None
        app._federation_notification_observability_store = obs_store

        args = argparse.Namespace(
            config="agentapp.yaml",
            granularity="hourly",
            since=None,
            until=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_rollup.create_notification_rollup_store", return_value=rollup_store):
            rc = _run_async(_cmd_policy_federation_notification_rollup_build(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "Built" in captured.out

    def test_rollup_list(self, capsys):
        """rollup list prints rollup entries."""
        from agent_app.cli import _cmd_policy_federation_notification_rollup_list

        store = MagicMock()
        store.list_rollups = AsyncMock(return_value=[])

        app = MagicMock()
        app._federation_notification_rollup_config = MagicMock()
        app._federation_notification_rollup_config.enabled = True
        app._federation_notification_rollup_config.store.type = "memory"
        app._federation_notification_rollup_config.store.path = None

        args = argparse.Namespace(
            config="agentapp.yaml",
            granularity=None,
            channel=None,
            limit=100,
        )

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_rollout_federation_notification_rollup.create_notification_rollup_store", return_value=store):
            rc = _run_async(_cmd_policy_federation_notification_rollup_list(args))

        assert rc == 0
