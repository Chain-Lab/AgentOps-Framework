"""Tests for Phase 48 federation approval CLI commands."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from agent_app.governance.policy_rollout_federation_approval import (
    FederationApprovalRequest,
    FederationApprovalStatus,
)


def _run(coro):
    return asyncio.run(coro)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _request(
    approval_id: str = "fap_test001",
    federation_id: str = "frp_test",
    action: str = "federation.plan.start",
    status: FederationApprovalStatus = FederationApprovalStatus.PENDING,
    requested_by: str = "deployer",
    required_approvers: list[str] | None = None,
) -> FederationApprovalRequest:
    return FederationApprovalRequest(
        approval_id=approval_id,
        federation_id=federation_id,
        action=action,
        status=status,
        requested_by=requested_by,
        required_approvers=required_approvers or ["release_manager"],
        created_at=_now(),
    )


def _app(service=None, store=None):
    app = MagicMock()
    app.federation_approval_service = service
    if service is not None:
        service._store = store
    return app


class TestFederationApprovalListCLI:
    def test_approval_list_returns_requests(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_approval_list

        store = MagicMock()
        store.list = AsyncMock(
            return_value=[
                _request(
                    approval_id="fap_001",
                    action="federation.plan.start",
                    requested_by="deployer",
                ),
                _request(
                    approval_id="fap_002",
                    action="federation.plan.run_all",
                    requested_by="ci_bot",
                ),
            ]
        )
        service = MagicMock()
        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id=None,
            status=None,
            tenant_id=None,
            action=None,
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service, store=store)
        ):
            rc = _run(_cmd_policy_federation_approval_list(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "fap_001" in output
        assert "fap_002" in output
        assert "federation.plan.start" in output
        assert "pending" in output

    def test_approval_list_no_results(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_approval_list

        store = MagicMock()
        store.list = AsyncMock(return_value=[])
        service = MagicMock()
        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id=None,
            status=None,
            tenant_id=None,
            action=None,
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service, store=store)
        ):
            rc = _run(_cmd_policy_federation_approval_list(args))
        assert rc == 0
        assert "No approval requests found" in capsys.readouterr().out

    def test_approval_list_service_not_configured(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_approval_list

        app = MagicMock()
        app.federation_approval_service = None
        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id=None,
            status=None,
            tenant_id=None,
            action=None,
        )
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_approval_list(args))
        assert rc == 1
        assert "not configured" in capsys.readouterr().err

    def test_approval_list_with_filters(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_approval_list

        store = MagicMock()
        store.list = AsyncMock(return_value=[_request(approval_id="fap_filtered")])
        service = MagicMock()
        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id="frp_test",
            status="pending",
            tenant_id="tenant_1",
            action="federation.plan.start",
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service, store=store)
        ):
            rc = _run(_cmd_policy_federation_approval_list(args))
        assert rc == 0
        # Verify filters were passed
        store.list.assert_called_once_with(
            federation_id="frp_test",
            status="pending",
            tenant_id="tenant_1",
            action="federation.plan.start",
        )


class TestFederationApprovalApproveCLI:
    def test_approval_approve_succeeds(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_approval_approve

        service = MagicMock()
        approved = _request(
            approval_id="fap_001",
            status=FederationApprovalStatus.APPROVED,
        )
        service.approve = AsyncMock(return_value=approved)
        args = argparse.Namespace(
            config="agentapp.yaml",
            approval_id="fap_001",
            actor_id="release_manager",
            reason="Looks good",
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_approval_approve(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "Approved: fap_001" in output

    def test_approval_approve_value_error(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_approval_approve

        service = MagicMock()
        service.approve = AsyncMock(side_effect=ValueError("Request not found"))
        args = argparse.Namespace(
            config="agentapp.yaml",
            approval_id="fap_nonexistent",
            actor_id="release_manager",
            reason=None,
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_approval_approve(args))
        assert rc == 1
        assert "Error: Request not found" in capsys.readouterr().err

    def test_approval_approve_permission_error(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_approval_approve

        service = MagicMock()
        service.approve = AsyncMock(
            side_effect=PermissionError("Not authorized to approve")
        )
        args = argparse.Namespace(
            config="agentapp.yaml",
            approval_id="fap_001",
            actor_id="unauthorized_user",
            reason=None,
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_approval_approve(args))
        assert rc == 1
        assert "Error: Not authorized" in capsys.readouterr().err

    def test_approval_approve_service_not_configured(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_approval_approve

        app = MagicMock()
        app.federation_approval_service = None
        args = argparse.Namespace(
            config="agentapp.yaml",
            approval_id="fap_001",
            actor_id="release_manager",
            reason=None,
        )
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_approval_approve(args))
        assert rc == 1
        assert "not configured" in capsys.readouterr().err


class TestFederationApprovalRejectCLI:
    def test_approval_reject_succeeds(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_approval_reject

        service = MagicMock()
        rejected = _request(
            approval_id="fap_001",
            status=FederationApprovalStatus.REJECTED,
        )
        service.reject = AsyncMock(return_value=rejected)
        args = argparse.Namespace(
            config="agentapp.yaml",
            approval_id="fap_001",
            actor_id="release_manager",
            reason="Not ready",
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_approval_reject(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "Rejected: fap_001" in output

    def test_approval_reject_value_error(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_approval_reject

        service = MagicMock()
        service.reject = AsyncMock(side_effect=ValueError("Already resolved"))
        args = argparse.Namespace(
            config="agentapp.yaml",
            approval_id="fap_001",
            actor_id="release_manager",
            reason=None,
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_approval_reject(args))
        assert rc == 1
        assert "Error: Already resolved" in capsys.readouterr().err

    def test_approval_reject_permission_error(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_approval_reject

        service = MagicMock()
        service.reject = AsyncMock(
            side_effect=PermissionError("Not authorized to reject")
        )
        args = argparse.Namespace(
            config="agentapp.yaml",
            approval_id="fap_001",
            actor_id="unauthorized_user",
            reason=None,
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_approval_reject(args))
        assert rc == 1
        assert "Error: Not authorized" in capsys.readouterr().err


class TestFederationApprovalEscalateCLI:
    def test_approval_escalate_succeeds(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_approval_escalate

        service = MagicMock()
        escalated = _request(
            approval_id="fap_001",
            status=FederationApprovalStatus.ESCALATED,
        )
        escalated.escalation_level = 1
        service.escalate = AsyncMock(return_value=escalated)
        args = argparse.Namespace(
            config="agentapp.yaml",
            approval_id="fap_001",
            actor_id="admin",
            reason="Taking too long",
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_approval_escalate(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "Escalated: fap_001" in output
        assert "level 1" in output

    def test_approval_escalate_value_error(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_approval_escalate

        service = MagicMock()
        service.escalate = AsyncMock(
            side_effect=ValueError("Cannot escalate non-pending request")
        )
        args = argparse.Namespace(
            config="agentapp.yaml",
            approval_id="fap_001",
            actor_id="admin",
            reason=None,
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_approval_escalate(args))
        assert rc == 1
        assert "Error: Cannot escalate" in capsys.readouterr().err

    def test_approval_escalate_service_not_configured(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_approval_escalate

        app = MagicMock()
        app.federation_approval_service = None
        args = argparse.Namespace(
            config="agentapp.yaml",
            approval_id="fap_001",
            actor_id="admin",
            reason=None,
        )
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_approval_escalate(args))
        assert rc == 1
        assert "not configured" in capsys.readouterr().err


class TestFederationRunAllApprovalRequired:
    def test_run_all_returns_approval_required(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_plan_run_all

        service = MagicMock()
        service.run_all_available = AsyncMock(
            return_value={
                "status": "approval_required",
                "approval_id": "fap_abc123",
                "action": "federation.plan.run_all",
                "required_approvers": ["release_manager", "security_lead"],
            }
        )
        app = MagicMock()
        app.rollout_federation_service = service
        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id="frp_test",
            actor_id="deployer",
            tenant_id=None,
            environment=None,
            region=None,
            ring=None,
        )
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_plan_run_all(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "Approval required" in output
        assert "fap_abc123" in output
        assert "federation.plan.run_all" in output
        assert "release_manager" in output
        assert "security_lead" in output

    def test_run_all_normal_plan_still_works(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_plan_run_all

        service = MagicMock()
        # Return a non-dict plan object (normal case)
        plan = MagicMock()
        plan.status = "completed"
        service.run_all_available = AsyncMock(return_value=plan)
        app = MagicMock()
        app.rollout_federation_service = service
        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id="frp_test",
            actor_id="deployer",
            tenant_id=None,
            environment=None,
            region=None,
            ring=None,
        )
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_plan_run_all(args))
        assert rc == 0
