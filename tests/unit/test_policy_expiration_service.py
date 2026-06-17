"""Tests for PolicyExpirationService — sweep, approval expiration, gate requirement expiration.

Phase 44 Task 5: Notification Hooks and Expiration Workers.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_app.governance.policy_expiration import (
    PolicyExpirationAction,
    PolicyExpirationTargetType,
)
from agent_app.governance.policy_release_gate import (
    ReleaseGateRequirement,
    ReleaseGateRequirementStatus,
)
from agent_app.runtime.policy_expiration_service import PolicyExpirationService


# ---------------------------------------------------------------------------
# TestExpireRolloutApprovals
# ---------------------------------------------------------------------------

class TestExpireRolloutApprovals:
    """Tests for expire_rollout_approvals method."""

    async def test_expired_approvals_produce_results(self) -> None:
        """When approval_store.expire_pending returns expired approvals,
        results should be created for each one."""
        approval = MagicMock()
        approval.approval_id = "rsa_abc123"
        approval.rollout_id = "rpl_001"
        approval.step_id = "step_1"

        approval_store = AsyncMock()
        approval_store.expire_pending.return_value = [approval]

        service = PolicyExpirationService(rollout_approval_store=approval_store)
        now = datetime.now(timezone.utc)

        results = await service.expire_rollout_approvals(now)

        assert len(results) == 1
        result = results[0]
        assert result.target_type == PolicyExpirationTargetType.ROLLOUT_APPROVAL
        assert result.target_id == "rsa_abc123"
        assert result.action == PolicyExpirationAction.EXPIRED
        assert result.result_id.startswith("per_")
        approval_store.expire_pending.assert_called_once_with(now)

    async def test_missing_store_skipped(self) -> None:
        """When no approval_store is provided, returns empty list."""
        service = PolicyExpirationService(rollout_approval_store=None)
        results = await service.expire_rollout_approvals()
        assert results == []


# ---------------------------------------------------------------------------
# TestExpireGateRequirements
# ---------------------------------------------------------------------------

class TestExpireGateRequirements:
    """Tests for expire_gate_requirements method."""

    async def test_expired_requirements_produce_results(self) -> None:
        """When a gate requirement has exceeded max_age_seconds, it should be expired."""
        now = datetime.now(timezone.utc)
        stale_req = ReleaseGateRequirement(
            requirement_id="rgr_stale1",
            source_type="promotion",
            source_id="promo_001",
            status=ReleaseGateRequirementStatus.REQUIRED,
            max_age_seconds=60,
            created_at=now - timedelta(seconds=120),
        )

        gate_store = AsyncMock()
        gate_store.list.return_value = [stale_req]
        gate_store.update.return_value = stale_req

        service = PolicyExpirationService(release_gate_requirement_store=gate_store)
        results = await service.expire_gate_requirements(now)

        assert len(results) == 1
        result = results[0]
        assert result.target_type == PolicyExpirationTargetType.PROMOTION_GATE_REQUIREMENT
        assert result.target_id == "rgr_stale1"
        assert result.action == PolicyExpirationAction.EXPIRED
        assert result.result_id.startswith("per_")
        assert stale_req.status == ReleaseGateRequirementStatus.EXPIRED
        gate_store.update.assert_called_once_with(stale_req)

    async def test_missing_store_skipped(self) -> None:
        """When no gate_store is provided, returns empty list."""
        service = PolicyExpirationService(release_gate_requirement_store=None)
        results = await service.expire_gate_requirements()
        assert results == []

    async def test_fresh_requirements_skipped(self) -> None:
        """When a gate requirement has NOT exceeded max_age_seconds, it is not expired."""
        now = datetime.now(timezone.utc)
        fresh_req = ReleaseGateRequirement(
            requirement_id="rgr_fresh1",
            source_type="promotion",
            source_id="promo_002",
            status=ReleaseGateRequirementStatus.REQUIRED,
            max_age_seconds=600,
            created_at=now - timedelta(seconds=10),
        )

        gate_store = AsyncMock()
        gate_store.list.return_value = [fresh_req]

        service = PolicyExpirationService(release_gate_requirement_store=gate_store)
        results = await service.expire_gate_requirements(now)

        assert results == []
        gate_store.update.assert_not_called()
        assert fresh_req.status == ReleaseGateRequirementStatus.REQUIRED


# ---------------------------------------------------------------------------
# TestSweep
# ---------------------------------------------------------------------------

class TestSweep:
    """Tests for sweep method."""

    async def test_sweep_returns_report(self) -> None:
        """sweep() returns a PolicyExpirationSweepReport with pes_ prefix and timestamps."""
        service = PolicyExpirationService()
        now = datetime.now(timezone.utc)

        report = await service.sweep(now)

        assert report.sweep_id.startswith("pes_")
        assert report.started_at == now
        assert report.completed_at is not None
        assert report.results == []

    async def test_sweep_with_both_stores(self) -> None:
        """When both stores are present but have nothing to expire, report has empty results."""
        approval_store = AsyncMock()
        approval_store.expire_pending.return_value = []

        gate_store = AsyncMock()
        gate_store.list.return_value = []

        service = PolicyExpirationService(
            rollout_approval_store=approval_store,
            release_gate_requirement_store=gate_store,
        )

        report = await service.sweep()

        assert report.results == []
        assert report.completed_at is not None

    async def test_expiration_triggers_notification(self) -> None:
        """When approvals are expired, notification_service.notify_event is called."""
        approval = MagicMock()
        approval.approval_id = "rsa_notif1"
        approval.rollout_id = "rpl_001"
        approval.step_id = "step_1"

        approval_store = AsyncMock()
        approval_store.expire_pending.return_value = [approval]

        notification_service = AsyncMock()

        service = PolicyExpirationService(
            rollout_approval_store=approval_store,
            notification_service=notification_service,
        )

        await service.sweep()

        notification_service.notify_event.assert_called_once()
        call_kwargs = notification_service.notify_event.call_args
        assert call_kwargs.kwargs["event_type"] == "policy.rollout.approval.expired"
        assert call_kwargs.kwargs["data"]["approval_id"] == "rsa_notif1"

    async def test_errors_captured(self) -> None:
        """When approval_store raises, error is captured in results instead of crashing."""
        approval_store = AsyncMock()
        approval_store.expire_pending.side_effect = RuntimeError("DB connection lost")

        service = PolicyExpirationService(rollout_approval_store=approval_store)
        report = await service.sweep()

        # Should have an ERROR result for rollout approvals
        error_results = [
            r for r in report.results
            if r.action == PolicyExpirationAction.ERROR
            and r.target_type == PolicyExpirationTargetType.ROLLOUT_APPROVAL
        ]
        assert len(error_results) == 1
        assert error_results[0].error is not None
        assert "DB connection lost" in error_results[0].error.get("message", "")
