"""Tests for ReleaseGateRequirement model."""
from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_release_gate import (
    ReleaseGateRequirement,
    ReleaseGateRequirementStatus,
)


def test_valid_requirement():
    req = ReleaseGateRequirement(
        requirement_id="rgr_abc123",
        source_type="promotion",
        source_id="pr_xyz789",
    )
    assert req.requirement_id == "rgr_abc123"
    assert req.source_type == "promotion"
    assert req.source_id == "pr_xyz789"
    assert req.required is True
    assert req.status == ReleaseGateRequirementStatus.REQUIRED


def test_id_prefix():
    req = ReleaseGateRequirement(
        requirement_id="rgr_test",
        source_type="rollout_step",
        source_id="rs_001",
    )
    assert req.requirement_id.startswith("rgr_")


def test_default_required_status():
    req = ReleaseGateRequirement(
        requirement_id="rgr_default",
        source_type="promotion",
        source_id="pr_001",
    )
    assert req.status == ReleaseGateRequirementStatus.REQUIRED


def test_timezone_aware_created_at():
    req = ReleaseGateRequirement(
        requirement_id="rgr_tz",
        source_type="promotion",
        source_id="pr_001",
    )
    assert req.created_at.tzinfo is not None


def test_all_status_values():
    assert ReleaseGateRequirementStatus.NOT_REQUIRED == "not_required"
    assert ReleaseGateRequirementStatus.REQUIRED == "required"
    assert ReleaseGateRequirementStatus.SATISFIED == "satisfied"
    assert ReleaseGateRequirementStatus.FAILED == "failed"
    assert ReleaseGateRequirementStatus.EXPIRED == "expired"


def test_optional_fields_default_none():
    req = ReleaseGateRequirement(
        requirement_id="rgr_opt",
        source_type="promotion",
        source_id="pr_001",
    )
    assert req.gate_result_id is None
    assert req.simulation_id is None
    assert req.max_age_seconds is None
    assert req.satisfied_at is None
    assert req.metadata == {}


def test_satisfied_requirement():
    now = datetime.now(timezone.utc)
    req = ReleaseGateRequirement(
        requirement_id="rgr_sat",
        source_type="promotion",
        source_id="pr_001",
        gate_result_id="pg_123",
        simulation_id="psim_456",
        status=ReleaseGateRequirementStatus.SATISFIED,
        satisfied_at=now,
    )
    assert req.status == ReleaseGateRequirementStatus.SATISFIED
    assert req.satisfied_at == now
    assert req.gate_result_id == "pg_123"
