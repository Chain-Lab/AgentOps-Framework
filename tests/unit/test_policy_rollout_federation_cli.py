from __future__ import annotations

import argparse
import asyncio
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType
from agent_app.governance.policy_rollout_federation import (
    FederatedRolloutPlan,
    FederatedRolloutPlanStatus,
    FederatedRolloutTarget,
    FederatedRolloutTargetExecution,
    FederatedRolloutTargetExecutionStatus,
    FederatedTargetStatus,
    FederationExecutionStrategy,
    RolloutConflict,
    RolloutConflictSeverity,
    RolloutConflictType,
)


def _run(coro):
    return asyncio.run(coro)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _target() -> FederatedRolloutTarget:
    return FederatedRolloutTarget(
        target_id="frt_test",
        name="prod-us-canary",
        tenant_id="tenant_a",
        environment="prod",
        ring_name="canary",
        region="us-east",
        created_at=_now(),
    )


def _step() -> RolloutStep:
    return RolloutStep(
        step_id="step_activate",
        step_type=RolloutStepType.ACTIVATE,
        environment="prod",
        ring_name="canary",
    )


def _plan() -> FederatedRolloutPlan:
    return FederatedRolloutPlan(
        federation_id="frp_test",
        name="global rollout",
        bundle_id="pb_123",
        strategy=FederationExecutionStrategy.SEQUENTIAL,
        status=FederatedRolloutPlanStatus.ACTIVE,
        target_ids=["frt_test"],
        executions=[
            FederatedRolloutTargetExecution(
                execution_id="fre_test",
                target_id="frt_test",
                rollout_id="ro_child",
                status=FederatedRolloutTargetExecutionStatus.SUCCEEDED,
            )
        ],
        rollout_template_steps=[_step()],
        created_by="release_manager",
        created_at=_now(),
        updated_at=_now(),
    )


def _app(service=None, target_store=None, plan_store=None):
    app = MagicMock()
    app.rollout_federation_service = service
    app.federated_rollout_target_store = target_store
    app.federated_rollout_plan_store = plan_store
    return app


class TestFederationTargetCLI:
    def test_target_create(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_target_create

        service = MagicMock()
        service.create_target = AsyncMock(return_value=_target())
        args = argparse.Namespace(
            config="agentapp.yaml",
            name="prod-us-canary",
            environment="prod",
            ring="canary",
            region="us-east",
            tenant_id="tenant_a",
            label=["tier=gold"],
            actor_id="admin",
            permissions="policy.federation.target.create",
        )
        with patch(
            "agent_app.config.loader.build_app", return_value=_app(service=service)
        ):
            rc = _run(_cmd_policy_federation_target_create(args))
        assert rc == 0
        captured = capsys.readouterr()
        assert "frt_test" in captured.out
        assert "prod-us-canary" in captured.out

    def test_target_list(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_target_list

        target_store = MagicMock()
        target_store.list = AsyncMock(return_value=[_target()])
        args = argparse.Namespace(
            config="agentapp.yaml",
            tenant_id=None,
            environment=None,
            ring=None,
            status=None,
        )
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app(target_store=target_store),
        ):
            rc = _run(_cmd_policy_federation_target_list(args))
        assert rc == 0
        assert "prod-us-canary" in capsys.readouterr().out

    def test_target_disable_and_enable(self, capsys) -> None:
        from agent_app.cli import (
            _cmd_policy_federation_target_disable,
            _cmd_policy_federation_target_enable,
        )

        target_store = MagicMock()
        disabled = _target().model_copy(
            update={"status": FederatedTargetStatus.DISABLED}
        )
        target_store.disable = AsyncMock(return_value=disabled)
        target_store.enable = AsyncMock(return_value=_target())
        args = argparse.Namespace(
            config="agentapp.yaml",
            target_id="frt_test",
            actor_id="admin",
            permissions="policy.federation.target.disable",
        )
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app(target_store=target_store),
        ):
            disable_rc = _run(_cmd_policy_federation_target_disable(args))
        args.permissions = "policy.federation.target.enable"
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app(target_store=target_store),
        ):
            enable_rc = _run(_cmd_policy_federation_target_enable(args))
        assert disable_rc == 0
        assert enable_rc == 0
        output = capsys.readouterr().out
        assert "disabled" in output
        assert "enabled" in output


class TestFederationPlanCLI:
    def test_plan_create_from_yaml_files(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_plan_create

        service = MagicMock()
        service.create_federated_plan = AsyncMock(return_value=_plan())
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml"
        ) as targets_file, tempfile.NamedTemporaryFile("w", suffix=".yaml") as steps_file:
            yaml.safe_dump(["frt_test"], targets_file)
            targets_file.flush()
            yaml.safe_dump(
                [
                    {
                        "step_id": "step_activate",
                        "step_type": "activate",
                        "environment": "prod",
                        "ring_name": "canary",
                    }
                ],
                steps_file,
            )
            steps_file.flush()
            args = argparse.Namespace(
                config="agentapp.yaml",
                name="global rollout",
                bundle_id="pb_123",
                targets_file=targets_file.name,
                steps_file=steps_file.name,
                strategy="sequential",
                actor_id="release_manager",
                permissions="policy.federation.plan.create",
                reason="release",
            )
            with patch(
                "agent_app.config.loader.build_app",
                return_value=_app(service=service),
            ):
                rc = _run(_cmd_policy_federation_plan_create(args))
        assert rc == 0
        assert "frp_test" in capsys.readouterr().out
        assert service.create_federated_plan.await_args.kwargs["target_ids"] == [
            "frt_test"
        ]

    def test_plan_start_run_next_run_all_cancel(self, capsys) -> None:
        from agent_app.cli import (
            _cmd_policy_federation_plan_cancel,
            _cmd_policy_federation_plan_run_all,
            _cmd_policy_federation_plan_run_next,
            _cmd_policy_federation_plan_start,
        )

        service = MagicMock()
        service.start_federated_plan = AsyncMock(return_value=_plan())
        service.run_next_target = AsyncMock(return_value=_plan())
        service.run_all_available = AsyncMock(return_value=_plan())
        service.cancel_federated_plan = AsyncMock(
            return_value=_plan().model_copy(
                update={"status": FederatedRolloutPlanStatus.CANCELLED}
            )
        )
        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id="frp_test",
            actor_id="release_manager",
            permissions="policy.federation.plan.start",
            reason="stop",
        )
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app(service=service),
        ):
            assert _run(_cmd_policy_federation_plan_start(args)) == 0
        args.permissions = "policy.federation.plan.execute"
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app(service=service),
        ):
            assert _run(_cmd_policy_federation_plan_run_next(args)) == 0
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app(service=service),
        ):
            assert _run(_cmd_policy_federation_plan_run_all(args)) == 0
        args.permissions = "policy.federation.plan.cancel"
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app(service=service),
        ):
            assert _run(_cmd_policy_federation_plan_cancel(args)) == 0
        output = capsys.readouterr().out
        assert "frp_test" in output
        assert "cancelled" in output

    def test_plan_conflicts(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_plan_conflicts

        service = MagicMock()
        service.detect_conflicts = AsyncMock(
            return_value=[
                RolloutConflict(
                    conflict_id="frc_test",
                    conflict_type=RolloutConflictType.DISABLED_TARGET,
                    severity=RolloutConflictSeverity.ERROR,
                    target_id="frt_test",
                    message="disabled",
                )
            ]
        )
        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id="frp_test",
        )
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app(service=service),
        ):
            rc = _run(_cmd_policy_federation_plan_conflicts(args))
        assert rc == 1
        output = capsys.readouterr().out
        assert "disabled_target" in output
        assert "ERROR" in output

    def test_permission_denied_exits_nonzero(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_plan_start

        service = MagicMock()
        service.start_federated_plan = AsyncMock(
            side_effect=PermissionError("Permission denied")
        )
        args = argparse.Namespace(
            config="agentapp.yaml",
            federation_id="frp_test",
            actor_id="release_manager",
            permissions="",
            reason=None,
        )
        with patch(
            "agent_app.config.loader.build_app",
            return_value=_app(service=service),
        ):
            rc = _run(_cmd_policy_federation_plan_start(args))
        assert rc != 0
        assert "Permission denied" in capsys.readouterr().err
