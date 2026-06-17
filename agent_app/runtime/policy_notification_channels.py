"""Notification channels — deliver notification messages.

Phase 44: Notification Hooks and Expiration Workers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from agent_app.governance.policy_notification import (
    PolicyNotificationMessage,
    PolicyNotificationStatus,
)

logger = logging.getLogger(__name__)


@runtime_checkable
class PolicyNotificationChannel(Protocol):
    """Protocol for notification delivery channels."""
    name: str

    async def send(
        self,
        message: PolicyNotificationMessage,
    ) -> PolicyNotificationMessage:
        ...


class LogNotificationChannel:
    """Deliver notifications via standard library logging."""
    name = "log"

    async def send(
        self,
        message: PolicyNotificationMessage,
    ) -> PolicyNotificationMessage:
        logger.info(
            "Notification [%s] %s: %s",
            message.severity.value,
            message.title,
            message.body,
        )
        message.status = PolicyNotificationStatus.SENT
        message.sent_at = datetime.now(timezone.utc)
        return message


class InMemoryNotificationChannel:
    """Store sent notifications in memory for testing."""
    name = "memory"

    def __init__(self) -> None:
        self.sent: list[PolicyNotificationMessage] = []

    async def send(
        self,
        message: PolicyNotificationMessage,
    ) -> PolicyNotificationMessage:
        self.sent.append(message)
        message.status = PolicyNotificationStatus.SENT
        message.sent_at = datetime.now(timezone.utc)
        return message


class FailingNotificationChannel:
    """Channel that always fails — for testing error handling."""
    name = "failing"

    async def send(
        self,
        message: PolicyNotificationMessage,
    ) -> PolicyNotificationMessage:
        message.status = PolicyNotificationStatus.FAILED
        message.error = {"type": "channel_error", "message": "FailingChannel always fails"}
        return message
