"""Tests for RolloutStepApproval and RolloutStepApprovalStatus models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_rollout_approval import (
    RolloutStepApproval,
    RolloutStepApprovalStatus,
)


def _make_approval(**overrides) -> RolloutStepApproval:
    """Helper to build a RolloutStepApproval with sensible defaults."""
    now = datetime.now(timezone.utc)
    base = dict(
        approval_id="rsa_abc123",
        rollout_id="ro_xyz789",
        step_id="step_1",
        bundle_id="pb_bundle1",
        environment="production",
        ring_name="canary",
        requested_by="user_admin",
        requested_reason="Canary ring promotion requires approval",
        created_at=now,
    )
    base.update(overrides)
    return RolloutStepApproval(**base)


# --- Tests ---


def test_valid_approval_creation():
    """Create a valid RolloutStepApproval with all required fields."""
    now = datetime.now(timezone.utc)
    approval = _make_approval(
        approval_id="rsa_abc123",
        rollout_id="ro_xyz789",
        step_id="step_1",
        bundle_id="pb_bundle1",
        environment="production",
        ring_name="canary",
        requested_by="user_admin",
        requested_reason="Canary ring promotion requires approval",
        created_at=now,
    )

    assert approval.approval_id == "rsa_abc123"
    assert approval.rollout_id == "ro_xyz789"
    assert approval.step_id == "step_1"
    assert approval.bundle_id == "pb_bundle1"
    assert approval.environment == "production"
    assert approval.ring_name == "canary"
    assert approval.requested_by == "user_admin"
    assert approval.requested_reason == "Canary ring promotion requires approval"
    assert approval.created_at == now


def test_default_status_pending():
    """Status defaults to PENDING on creation."""
    approval = _make_approval()
    assert approval.status == RolloutStepApprovalStatus.PENDING
    assert approval.status == "pending"


def test_approval_id_prefix():
    """approval_id uses the rsa_ prefix convention."""
    approval = _make_approval(approval_id="rsa_abc123")
    assert approval.approval_id.startswith("rsa_")

    # Verify the enum values are string-based
    assert RolloutStepApprovalStatus.PENDING == "pending"
    assert RolloutStepApprovalStatus.APPROVED == "approved"
    assert RolloutStepApprovalStatus.REJECTED == "rejected"
    assert RolloutStepApprovalStatus.CANCELLED == "cancelled"


def test_timezone_aware_timestamps():
    """created_at is timezone-aware."""
    now = datetime.now(timezone.utc)
    approval = _make_approval(created_at=now)

    assert approval.created_at.tzinfo is not None

    # resolved_at should also be timezone-aware when set
    resolved = datetime.now(timezone.utc)
    approval_resolved = _make_approval(
        status=RolloutStepApprovalStatus.APPROVED,
        resolved_by="user_reviewer",
        resolved_reason="Looks good",
        resolved_at=resolved,
    )
    assert approval_resolved.resolved_at is not None
    assert approval_resolved.resolved_at.tzinfo is not None


def test_ring_name_optional():
    """ring_name can be None."""
    approval = _make_approval(ring_name=None)
    assert approval.ring_name is None

    # Also verify other optional fields default to None
    assert approval.resolved_by is None
    assert approval.resolved_reason is None
    assert approval.resolved_at is None
