"""Tests for Phase 16.5 recovery models."""

from __future__ import annotations

import pytest

from agent_app.runtime.recovery_models import (
    RecoveryCandidate,
    RecoveryCandidateReason,
    RecoveryRecommendation,
    RecoveryScanConfig,
    RecoveryScanResult,
    ManualRecoveryResult,
)


# ---------------------------------------------------------------------------
# RecoveryScanConfig defaults
# ---------------------------------------------------------------------------


class TestRecoveryScanConfigDefaults:
    def test_default_stale_after_seconds(self):
        cfg = RecoveryScanConfig()
        assert cfg.stale_after_seconds == 300

    def test_default_running_after_seconds(self):
        cfg = RecoveryScanConfig()
        assert cfg.running_after_seconds == 300

    def test_default_include_completed(self):
        cfg = RecoveryScanConfig()
        assert cfg.include_completed is False

    def test_default_include_failed(self):
        cfg = RecoveryScanConfig()
        assert cfg.include_failed is True

    def test_default_include_running(self):
        cfg = RecoveryScanConfig()
        assert cfg.include_running is True

    def test_default_include_compensating(self):
        cfg = RecoveryScanConfig()
        assert cfg.include_compensating is True

    def test_default_limit(self):
        cfg = RecoveryScanConfig()
        assert cfg.limit == 100

    def test_custom_values(self):
        cfg = RecoveryScanConfig(
            stale_after_seconds=600,
            running_after_seconds=120,
            include_completed=True,
            limit=50,
            workflow_name="my_dag",
        )
        assert cfg.stale_after_seconds == 600
        assert cfg.running_after_seconds == 120
        assert cfg.include_completed is True
        assert cfg.limit == 50
        assert cfg.workflow_name == "my_dag"


# ---------------------------------------------------------------------------
# RecoveryCandidate serialization
# ---------------------------------------------------------------------------


class TestRecoveryCandidateSerialization:
    def test_minimal_candidate(self):
        c = RecoveryCandidate(
            run_id="wr_123",
            status="failed",
            recommendation=RecoveryRecommendation.RESUME,
        )
        assert c.run_id == "wr_123"
        assert c.status == "failed"
        assert c.reasons == []
        assert c.resumable is None

    def test_full_candidate(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        c = RecoveryCandidate(
            run_id="wr_456",
            workflow_name="test_dag",
            status="running",
            updated_at=now,
            age_seconds=120.5,
            reasons=[
                RecoveryCandidateReason.RUN_STALE,
                RecoveryCandidateReason.LEASE_MISSING,
            ],
            recommendation=RecoveryRecommendation.RESUME,
            lease_present=False,
            lease_owner=None,
            lease_expires_at=None,
            lease_expired=None,
            resumable=True,
            resume_plan_summary={"total_nodes": 3},
            recovery_plan_summary={"resumable": True},
        )
        assert c.workflow_name == "test_dag"
        assert len(c.reasons) == 2
        assert c.resume_plan_summary["total_nodes"] == 3

    def test_candidate_to_dict(self):
        c = RecoveryCandidate(
            run_id="wr_789",
            status="completed",
            recommendation=RecoveryRecommendation.INSPECT_ONLY,
        )
        d = c.model_dump(mode="json")
        assert d["run_id"] == "wr_789"
        assert d["status"] == "completed"
        assert d["recommendation"] == "inspect_only"

    def test_reasons_enum_values(self):
        reasons = [
            RecoveryCandidateReason.RUNNING_TOO_LONG,
            RecoveryCandidateReason.RUN_STALE,
            RecoveryCandidateReason.NODE_INTERRUPTED,
            RecoveryCandidateReason.NODE_FAILED,
            RecoveryCandidateReason.LEASE_EXPIRED,
            RecoveryCandidateReason.LEASE_MISSING,
            RecoveryCandidateReason.COMPENSATION_INCOMPLETE,
            RecoveryCandidateReason.SNAPSHOT_AVAILABLE,
            RecoveryCandidateReason.RESUME_PLAN_AVAILABLE,
            RecoveryCandidateReason.NOT_RESUMABLE,
        ]
        assert len(reasons) == 10
        for r in reasons:
            assert isinstance(r.value, str)
            assert len(r.value) > 0

    def test_recommendation_enum_values(self):
        recs = [
            RecoveryRecommendation.INSPECT_ONLY,
            RecoveryRecommendation.RESUME,
            RecoveryRecommendation.WAIT_FOR_ACTIVE_LEASE,
            RecoveryRecommendation.MANUAL_REVIEW,
            RecoveryRecommendation.DO_NOT_RESUME,
        ]
        assert len(recs) == 5


# ---------------------------------------------------------------------------
# RecoveryScanResult
# ---------------------------------------------------------------------------


class TestRecoveryScanResult:
    def test_empty_result(self):
        result = RecoveryScanResult()
        assert result.total_scanned == 0
        assert result.candidate_count == 0
        assert result.candidates == []
        assert result.errors == []

    def test_with_candidates(self):
        candidates = [
            RecoveryCandidate(
                run_id="wr_1",
                status="failed",
                recommendation=RecoveryRecommendation.RESUME,
            ),
            RecoveryCandidate(
                run_id="wr_2",
                status="running",
                recommendation=RecoveryRecommendation.WAIT_FOR_ACTIVE_LEASE,
            ),
        ]
        result = RecoveryScanResult(
            total_scanned=10,
            candidate_count=2,
            candidates=candidates,
        )
        assert result.total_scanned == 10
        assert result.candidate_count == 2
        assert len(result.candidates) == 2

    def test_with_errors(self):
        result = RecoveryScanResult(
            total_scanned=5,
            errors=[{"run_id": "wr_x", "error": "db timeout"}],
        )
        assert len(result.errors) == 1


# ---------------------------------------------------------------------------
# ManualRecoveryResult
# ---------------------------------------------------------------------------


class TestManualRecoveryResult:
    def test_default_values(self):
        result = ManualRecoveryResult(run_id="wr_123")
        assert result.run_id == "wr_123"
        assert result.attempted is False
        assert result.recovered is False
        assert result.status == ""
        assert result.lease_acquired is False
        assert result.lease_released is False
        assert result.result is None
        assert result.error is None

    def test_success_shape(self):
        result = ManualRecoveryResult(
            run_id="wr_123",
            attempted=True,
            recovered=True,
            status="completed",
            lease_acquired=True,
            lease_released=True,
        )
        assert result.recovered is True
        assert result.lease_acquired is True
        assert result.lease_released is True

    def test_failure_shape(self):
        result = ManualRecoveryResult(
            run_id="wr_123",
            attempted=True,
            recovered=False,
            status="lease_denied",
            lease_acquired=False,
            error={"type": "lease_denied", "message": "Active lease"},
        )
        assert result.error is not None
        assert result.error["type"] == "lease_denied"
