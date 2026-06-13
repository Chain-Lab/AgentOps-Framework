"""Tests for ReleaseRing model and ReleaseRingStatus enum."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_app.governance.policy_ring import ReleaseRing, ReleaseRingStatus


class TestReleaseRingStatus:
    """Tests for ReleaseRingStatus enum."""

    def test_enabled_value(self) -> None:
        assert ReleaseRingStatus.ENABLED == "enabled"

    def test_disabled_value(self) -> None:
        assert ReleaseRingStatus.DISABLED == "disabled"

    def test_all_statuses(self) -> None:
        assert set(ReleaseRingStatus) == {"enabled", "disabled"}


class TestReleaseRing:
    """Tests for ReleaseRing model."""

    def test_default_status_enabled(self) -> None:
        ring = ReleaseRing(ring_id="ring_01", environment="production", name="stable")
        assert ring.status is ReleaseRingStatus.ENABLED

    def test_is_default_false(self) -> None:
        ring = ReleaseRing(ring_id="ring_01", environment="production", name="stable")
        assert ring.is_default is False

    def test_requires_ring_id_environment_name(self) -> None:
        with pytest.raises(ValidationError):
            ReleaseRing()  # type: ignore[call-arg]

    def test_timestamps_timezone_aware(self) -> None:
        ring = ReleaseRing(ring_id="ring_01", environment="production", name="stable")
        assert ring.created_at.tzinfo is not None
        assert ring.updated_at.tzinfo is not None
