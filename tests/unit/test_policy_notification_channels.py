"""Tests for notification channels (Phase 44)."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from agent_app.governance.policy_notification import (
    PolicyNotificationSeverity,
    PolicyNotificationStatus,
    PolicyNotificationMessage,
)
from agent_app.runtime.policy_notification_channels import (
    LogNotificationChannel,
    InMemoryNotificationChannel,
    FailingNotificationChannel,
)


def _make_msg(notification_id="pn_001"):
    return PolicyNotificationMessage(
        notification_id=notification_id,
        event_type="test.event",
        severity=PolicyNotificationSeverity.INFO,
        title="Test",
        body="Body",
        created_at=datetime.now(timezone.utc),
    )


class TestLogNotificationChannel:
    @pytest.mark.asyncio
    async def test_name(self):
        ch = LogNotificationChannel()
        assert ch.name == "log"

    @pytest.mark.asyncio
    async def test_send(self):
        ch = LogNotificationChannel()
        msg = _make_msg()
        result = await ch.send(msg)
        assert result.status == PolicyNotificationStatus.SENT
        assert result.sent_at is not None


class TestInMemoryNotificationChannel:
    @pytest.mark.asyncio
    async def test_name(self):
        ch = InMemoryNotificationChannel()
        assert ch.name == "memory"

    @pytest.mark.asyncio
    async def test_send(self):
        ch = InMemoryNotificationChannel()
        msg = _make_msg()
        result = await ch.send(msg)
        assert result.status == PolicyNotificationStatus.SENT
        assert len(ch.sent) == 1


class TestFailingNotificationChannel:
    @pytest.mark.asyncio
    async def test_send_fails(self):
        ch = FailingNotificationChannel()
        msg = _make_msg()
        result = await ch.send(msg)
        assert result.status == PolicyNotificationStatus.FAILED
        assert result.error is not None
