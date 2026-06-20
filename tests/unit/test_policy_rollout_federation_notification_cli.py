"""Tests for Phase 49 federation notification and worker CLI commands."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from agent_app.governance.policy_rollout_federation_notification import (
    FederationNotificationChannel,
    FederationNotificationDispatchResult,
    FederationNotificationEventType,
    FederationNotificationMessage,
    FederationNotificationStatus,
)
from agent_app.runtime.policy_rollout_federation_escalation_worker import (
    FederationApprovalEscalationWorkerResult,
)


def _run(coro):
    return asyncio.run(coro)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _notification(
    notification_id: str = "fn_test001",
    approval_id: str = "fap_test001",
    event_type: FederationNotificationEventType = FederationNotificationEventType.APPROVAL_CREATED,
    channel: FederationNotificationChannel = FederationNotificationChannel.EMAIL,
    status: FederationNotificationStatus = FederationNotificationStatus.PENDING,
    attempt_count: int = 0,
) -> FederationNotificationMessage:
    return FederationNotificationMessage(
        notification_id=notification_id,
        approval_id=approval_id,
        event_type=event_type,
        channel=channel,
        recipients=["admin@example.com"],
        subject="Test notification",
        body="Test body",
        status=status,
        attempt_count=attempt_count,
        created_at=_now(),
    )


def _app_with_notification_store(store=None):
    app = MagicMock()
    app.federation_notification_store = store
    return app


def _app_with_notification_service(service=None):
    app = MagicMock()
    app.federation_notification_service = service
    return app


def _app_with_escalation_worker(worker=None):
    app = MagicMock()
    app.federation_escalation_worker = worker
    return app


# ---------------------------------------------------------------------------
# _cmd_policy_federation_notification_list
# ---------------------------------------------------------------------------


class TestFederationNotificationListCLI:
    def test_notification_list_with_pending_notifications(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_list

        store = MagicMock()
        store.list_pending = AsyncMock(
            return_value=[
                _notification(notification_id="fn_001", approval_id="fap_001"),
                _notification(
                    notification_id="fn_002",
                    approval_id="fap_002",
                    event_type=FederationNotificationEventType.APPROVAL_ESCALATED,
                    channel=FederationNotificationChannel.SLACK,
                ),
            ]
        )
        args = argparse.Namespace(config="agentapp.yaml", status="pending", limit=100)
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app_with_notification_store(store),
        ):
            rc = _run(_cmd_policy_federation_notification_list(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "fn_001" in output
        assert "fn_002" in output
        assert "approval.created" in output
        assert "approval.escalated" in output

    def test_notification_list_no_notifications(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_list

        store = MagicMock()
        store.list_pending = AsyncMock(return_value=[])
        args = argparse.Namespace(config="agentapp.yaml", status="pending", limit=100)
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app_with_notification_store(store),
        ):
            rc = _run(_cmd_policy_federation_notification_list(args))
        assert rc == 0
        assert "No notifications found" in capsys.readouterr().out

    def test_notification_list_store_not_configured(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_list

        app = MagicMock()
        app.federation_notification_store = None
        args = argparse.Namespace(config="agentapp.yaml", status="pending", limit=100)
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_list(args))
        assert rc == 1
        assert "not configured" in capsys.readouterr().err

    def test_notification_list_with_non_pending_status(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_list

        store = MagicMock()
        store.list = AsyncMock(
            return_value=[
                _notification(
                    notification_id="fn_sent001",
                    status=FederationNotificationStatus.SENT,
                ),
            ]
        )
        args = argparse.Namespace(config="agentapp.yaml", status="sent", limit=100)
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app_with_notification_store(store),
        ):
            rc = _run(_cmd_policy_federation_notification_list(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "fn_sent001" in output
        # list() should be called with status=None for non-pending filter
        store.list.assert_called_once_with(status=None, limit=100)

    def test_notification_list_error_loading_config(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_list

        args = argparse.Namespace(config="agentapp.yaml", status="pending", limit=100)
        with patch(
            "agent_app.config.loader.build_app",
            side_effect=RuntimeError("Config file not found"),
        ):
            rc = _run(_cmd_policy_federation_notification_list(args))
        assert rc == 1
        assert "Error loading config" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _cmd_policy_federation_notification_dispatch
# ---------------------------------------------------------------------------


class TestFederationNotificationDispatchCLI:
    def test_notification_dispatch_with_pending(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_dispatch

        service = MagicMock()
        service.dispatch_pending = AsyncMock(
            return_value=FederationNotificationDispatchResult(
                total_dispatched=5,
                total_sent=3,
                total_failed=1,
                total_skipped=1,
                errors=["Timeout on fn_004"],
            )
        )
        args = argparse.Namespace(config="agentapp.yaml", limit=100)
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app_with_notification_service(service),
        ):
            rc = _run(_cmd_policy_federation_notification_dispatch(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "Dispatched: 5" in output
        assert "Sent:       3" in output
        assert "Failed:     1" in output
        assert "Skipped:    1" in output
        assert "Timeout on fn_004" in output

    def test_notification_dispatch_service_not_configured(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_dispatch

        app = MagicMock()
        app.federation_notification_service = None
        args = argparse.Namespace(config="agentapp.yaml", limit=100)
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_dispatch(args))
        assert rc == 1
        assert "not configured" in capsys.readouterr().err

    def test_notification_dispatch_no_errors(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_dispatch

        service = MagicMock()
        service.dispatch_pending = AsyncMock(
            return_value=FederationNotificationDispatchResult(
                total_dispatched=2,
                total_sent=2,
                total_failed=0,
                total_skipped=0,
                errors=[],
            )
        )
        args = argparse.Namespace(config="agentapp.yaml", limit=50)
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app_with_notification_service(service),
        ):
            rc = _run(_cmd_policy_federation_notification_dispatch(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "Dispatched: 2" in output
        assert "Errors" not in output

    def test_notification_dispatch_error_from_service(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_dispatch

        service = MagicMock()
        service.dispatch_pending = AsyncMock(side_effect=RuntimeError("Store unavailable"))
        args = argparse.Namespace(config="agentapp.yaml", limit=100)
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app_with_notification_service(service),
        ):
            rc = _run(_cmd_policy_federation_notification_dispatch(args))
        assert rc == 1
        assert "Error dispatching" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _cmd_policy_federation_notification_by_approval
# ---------------------------------------------------------------------------


class TestFederationNotificationByApprovalCLI:
    def test_notification_by_approval_with_notifications(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_by_approval

        store = MagicMock()
        store.list_by_approval = AsyncMock(
            return_value=[
                _notification(notification_id="fn_001", approval_id="fap_abc"),
                _notification(
                    notification_id="fn_002",
                    approval_id="fap_abc",
                    event_type=FederationNotificationEventType.APPROVAL_APPROVED,
                ),
            ]
        )
        args = argparse.Namespace(config="agentapp.yaml", approval_id="fap_abc")
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app_with_notification_store(store),
        ):
            rc = _run(_cmd_policy_federation_notification_by_approval(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "fn_001" in output
        assert "fn_002" in output
        store.list_by_approval.assert_called_once_with(approval_id="fap_abc")

    def test_notification_by_approval_no_notifications(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_by_approval

        store = MagicMock()
        store.list_by_approval = AsyncMock(return_value=[])
        args = argparse.Namespace(config="agentapp.yaml", approval_id="fap_nonexistent")
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app_with_notification_store(store),
        ):
            rc = _run(_cmd_policy_federation_notification_by_approval(args))
        assert rc == 0
        assert "No notifications found" in capsys.readouterr().out

    def test_notification_by_approval_store_not_configured(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_by_approval

        app = MagicMock()
        app.federation_notification_store = None
        args = argparse.Namespace(config="agentapp.yaml", approval_id="fap_abc")
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_by_approval(args))
        assert rc == 1
        assert "not configured" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _cmd_policy_federation_approval_escalate_due
# ---------------------------------------------------------------------------


class TestFederationApprovalEscalateDueCLI:
    def test_escalate_due_with_due_approvals(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_approval_escalate_due

        worker = MagicMock()
        worker._dry_run = False
        worker.tick = AsyncMock(
            return_value=FederationApprovalEscalationWorkerResult(
                scanned_count=10,
                escalated_count=3,
                skipped_count=7,
                errors=[],
            )
        )
        args = argparse.Namespace(config="agentapp.yaml", dry_run=False)
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app_with_escalation_worker(worker),
        ):
            rc = _run(_cmd_policy_federation_approval_escalate_due(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "Scanned:   10" in output
        assert "Escalated: 3" in output
        assert "Skipped:   7" in output

    def test_escalate_due_worker_not_configured(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_approval_escalate_due

        app = MagicMock()
        app.federation_escalation_worker = None
        args = argparse.Namespace(config="agentapp.yaml", dry_run=False)
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_approval_escalate_due(args))
        assert rc == 1
        assert "not configured" in capsys.readouterr().err

    def test_escalate_due_with_dry_run(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_approval_escalate_due

        worker = MagicMock()
        worker._dry_run = False
        worker.tick = AsyncMock(
            return_value=FederationApprovalEscalationWorkerResult(
                scanned_count=5,
                escalated_count=2,
                skipped_count=3,
                errors=[],
            )
        )
        args = argparse.Namespace(config="agentapp.yaml", dry_run=True)
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app_with_escalation_worker(worker),
        ):
            rc = _run(_cmd_policy_federation_approval_escalate_due(args))
        assert rc == 0
        # Verify dry_run was temporarily set to True
        assert worker._dry_run is False  # Should be restored after tick

    def test_escalate_due_with_errors(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_approval_escalate_due

        worker = MagicMock()
        worker._dry_run = False
        worker.tick = AsyncMock(
            return_value=FederationApprovalEscalationWorkerResult(
                scanned_count=3,
                escalated_count=1,
                skipped_count=1,
                errors=["Escalation failed for fap_001: ValueError"],
            )
        )
        args = argparse.Namespace(config="agentapp.yaml", dry_run=False)
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app_with_escalation_worker(worker),
        ):
            rc = _run(_cmd_policy_federation_approval_escalate_due(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "Escalation failed" in output


# ---------------------------------------------------------------------------
# _cmd_policy_federation_worker_tick
# ---------------------------------------------------------------------------


class TestFederationWorkerTickCLI:
    def test_worker_tick_with_approvals(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_worker_tick

        worker = MagicMock()
        worker.tick = AsyncMock(
            return_value=FederationApprovalEscalationWorkerResult(
                scanned_count=8,
                escalated_count=2,
                skipped_count=6,
                errors=[],
            )
        )
        args = argparse.Namespace(config="agentapp.yaml")
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app_with_escalation_worker(worker),
        ):
            rc = _run(_cmd_policy_federation_worker_tick(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "Scanned:   8" in output
        assert "Escalated: 2" in output
        assert "Skipped:   6" in output

    def test_worker_tick_worker_not_configured(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_worker_tick

        app = MagicMock()
        app.federation_escalation_worker = None
        args = argparse.Namespace(config="agentapp.yaml")
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_worker_tick(args))
        assert rc == 1
        assert "not configured" in capsys.readouterr().err

    def test_worker_tick_error(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_worker_tick

        worker = MagicMock()
        worker.tick = AsyncMock(side_effect=RuntimeError("Lock unavailable"))
        args = argparse.Namespace(config="agentapp.yaml")
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app_with_escalation_worker(worker),
        ):
            rc = _run(_cmd_policy_federation_worker_tick(args))
        assert rc == 1
        assert "Error running worker tick" in capsys.readouterr().err

    def test_worker_tick_with_errors_in_result(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_worker_tick

        worker = MagicMock()
        worker.tick = AsyncMock(
            return_value=FederationApprovalEscalationWorkerResult(
                scanned_count=4,
                escalated_count=0,
                skipped_count=3,
                errors=["Lock unavailable"],
            )
        )
        args = argparse.Namespace(config="agentapp.yaml")
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app_with_escalation_worker(worker),
        ):
            rc = _run(_cmd_policy_federation_worker_tick(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "Lock unavailable" in output
