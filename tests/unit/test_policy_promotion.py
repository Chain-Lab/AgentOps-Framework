"""Tests for PromotionRequest model and PromotionRequestStatus enum."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_promotion import (
    PromotionRequest,
    PromotionRequestStatus,
)


class TestPromotionRequestStatus:
    """Test PromotionRequestStatus enum values."""

    def test_status_values(self) -> None:
        """Assert all 5 enum values match expected strings."""
        assert PromotionRequestStatus.PENDING.value == "pending"
        assert PromotionRequestStatus.APPROVED.value == "approved"
        assert PromotionRequestStatus.REJECTED.value == "rejected"
        assert PromotionRequestStatus.EXECUTED.value == "executed"
        assert PromotionRequestStatus.CANCELLED.value == "cancelled"


class TestPromotionRequest:
    """Test PromotionRequest model creation and defaults."""

    def test_promotion_id_prefix(self) -> None:
        """Assert promotion_id starts with 'pr_'."""
        req = PromotionRequest(
            promotion_id="pr_abc123",
            bundle_id="pb_v1",
            requested_by="user1",
        )
        assert req.promotion_id.startswith("pr_")

    def test_default_status_pending(self) -> None:
        """Assert default status is PENDING."""
        req = PromotionRequest(
            promotion_id="pr_abc123",
            bundle_id="pb_v1",
            requested_by="user1",
        )
        assert req.status == PromotionRequestStatus.PENDING

    def test_timezone_aware_datetimes(self) -> None:
        """Assert created_at is timezone-aware, resolved_at and executed_at default to None."""
        req = PromotionRequest(
            promotion_id="pr_abc123",
            bundle_id="pb_v1",
            requested_by="user1",
        )
        assert req.created_at.tzinfo is not None
        assert req.resolved_at is None
        assert req.executed_at is None

    def test_with_gate_result(self) -> None:
        """Create with gate_result_id and assert it."""
        req = PromotionRequest(
            promotion_id="pr_abc123",
            bundle_id="pb_v1",
            gate_result_id="gr_abc",
            requested_by="user1",
        )
        assert req.gate_result_id == "gr_abc"

    def test_without_gate_result(self) -> None:
        """Create with gate_result_id=None and assert None."""
        req = PromotionRequest(
            promotion_id="pr_abc123",
            bundle_id="pb_v1",
            gate_result_id=None,
            requested_by="user1",
        )
        assert req.gate_result_id is None

    def test_optional_fields_default_none(self) -> None:
        """Create with reason=None and tenant_id=None, assert both."""
        req = PromotionRequest(
            promotion_id="pr_abc123",
            bundle_id="pb_v1",
            requested_by="user1",
            reason=None,
            tenant_id=None,
        )
        assert req.reason is None
        assert req.tenant_id is None

    def test_executed_state_has_timestamps(self) -> None:
        """Create with EXECUTED status and all timestamps/actors set, assert all values."""
        now = datetime.now(timezone.utc)
        req = PromotionRequest(
            promotion_id="pr_abc123",
            bundle_id="pb_v1",
            requested_by="user1",
            status=PromotionRequestStatus.EXECUTED,
            resolved_at=now,
            executed_at=now,
            resolved_by="approver1",
            executed_by="executor1",
        )
        assert req.status == PromotionRequestStatus.EXECUTED
        assert req.resolved_at == now
        assert req.executed_at == now
        assert req.resolved_by == "approver1"
        assert req.executed_by == "executor1"
