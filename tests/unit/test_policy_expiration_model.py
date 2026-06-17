"""Tests for policy expiration models."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_app.governance.policy_expiration import (
    PolicyExpirationAction,
    PolicyExpirationResult,
    PolicyExpirationSweepReport,
    PolicyExpirationTargetType,
)


class TestPolicyExpirationTargetType:
    def test_values(self):
        assert PolicyExpirationTargetType.ROLLOUT_APPROVAL == "rollout_approval"
        assert PolicyExpirationTargetType.PROMOTION_GATE_REQUIREMENT == "promotion_gate_requirement"
        assert PolicyExpirationTargetType.ROLLOUT_GATE_REQUIREMENT == "rollout_gate_requirement"


class TestPolicyExpirationAction:
    def test_values(self):
        assert PolicyExpirationAction.EXPIRED == "expired"
        assert PolicyExpirationAction.SKIPPED == "skipped"
        assert PolicyExpirationAction.ERROR == "error"


class TestPolicyExpirationResult:
    def test_valid_result(self):
        now = datetime.now(timezone.utc)
        result = PolicyExpirationResult(
            result_id="per_abc123",
            target_type=PolicyExpirationTargetType.ROLLOUT_APPROVAL,
            target_id="ra_001",
            action=PolicyExpirationAction.EXPIRED,
            created_at=now,
        )
        assert result.result_id == "per_abc123"
        assert result.target_type == PolicyExpirationTargetType.ROLLOUT_APPROVAL
        assert result.target_id == "ra_001"
        assert result.action == PolicyExpirationAction.EXPIRED
        assert result.reason is None
        assert result.error is None
        assert result.created_at == now

    def test_result_id_prefix(self):
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError, match="per_"):
            PolicyExpirationResult(
                result_id="bad_id",
                target_type=PolicyExpirationTargetType.ROLLOUT_APPROVAL,
                target_id="ra_001",
                action=PolicyExpirationAction.EXPIRED,
                created_at=now,
            )

    def test_tz_aware_created_at(self):
        with pytest.raises(ValidationError, match="timezone-aware"):
            PolicyExpirationResult(
                result_id="per_abc123",
                target_type=PolicyExpirationTargetType.ROLLOUT_APPROVAL,
                target_id="ra_001",
                action=PolicyExpirationAction.EXPIRED,
                created_at=datetime(2026, 1, 1, 0, 0, 0),
            )

    def test_with_error(self):
        now = datetime.now(timezone.utc)
        result = PolicyExpirationResult(
            result_id="per_err1",
            target_type=PolicyExpirationTargetType.PROMOTION_GATE_REQUIREMENT,
            target_id="pgr_001",
            action=PolicyExpirationAction.ERROR,
            reason="Database connection failed",
            error={"code": "DB_ERROR", "message": "Connection refused"},
            created_at=now,
        )
        assert result.action == PolicyExpirationAction.ERROR
        assert result.reason == "Database connection failed"
        assert result.error == {"code": "DB_ERROR", "message": "Connection refused"}


class TestPolicyExpirationSweepReport:
    def test_valid_report(self):
        now = datetime.now(timezone.utc)
        report = PolicyExpirationSweepReport(
            sweep_id="pes_sweep1",
            started_at=now,
        )
        assert report.sweep_id == "pes_sweep1"
        assert report.started_at == now
        assert report.completed_at is None
        assert report.results == []
        assert report.metadata == {}

    def test_sweep_id_prefix(self):
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError, match="pes_"):
            PolicyExpirationSweepReport(
                sweep_id="bad_id",
                started_at=now,
            )

    def test_with_results(self):
        now = datetime.now(timezone.utc)
        result1 = PolicyExpirationResult(
            result_id="per_r1",
            target_type=PolicyExpirationTargetType.ROLLOUT_APPROVAL,
            target_id="ra_001",
            action=PolicyExpirationAction.EXPIRED,
            created_at=now,
        )
        result2 = PolicyExpirationResult(
            result_id="per_r2",
            target_type=PolicyExpirationTargetType.ROLLOUT_GATE_REQUIREMENT,
            target_id="rgr_001",
            action=PolicyExpirationAction.SKIPPED,
            reason="Not yet due",
            created_at=now,
        )
        report = PolicyExpirationSweepReport(
            sweep_id="pes_sweep2",
            started_at=now,
            completed_at=now,
            results=[result1, result2],
            metadata={"total_scanned": 10},
        )
        assert len(report.results) == 2
        assert report.results[0].result_id == "per_r1"
        assert report.results[1].result_id == "per_r2"
        assert report.completed_at == now
        assert report.metadata == {"total_scanned": 10}
