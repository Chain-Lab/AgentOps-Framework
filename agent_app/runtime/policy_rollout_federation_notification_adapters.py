"""Federation notification adapters — deliver federation notification messages.

Phase 49: Federation Notification Adapters.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from agent_app.governance.policy_rollout_federation_notification import (
    FederationNotificationChannel,
    FederationNotificationDelivery,
    FederationNotificationMessage,
    FederationNotificationStatus,
)

logger = logging.getLogger(__name__)


@runtime_checkable
class FederationNotificationAdapter(Protocol):
    """Protocol for federation notification delivery adapters."""

    name: str

    async def send(
        self,
        message: FederationNotificationMessage,
    ) -> FederationNotificationDelivery:
        ...


class NoopFederationNotificationAdapter:
    """No-op adapter that silently succeeds."""

    name = "noop"

    async def send(
        self,
        message: FederationNotificationMessage,
    ) -> FederationNotificationDelivery:
        return FederationNotificationDelivery(
            notification_id=message.notification_id,
            channel=FederationNotificationChannel.NOOP,
            status=FederationNotificationStatus.SENT,
            delivered_at=datetime.now(timezone.utc),
        )


class ConsoleFederationNotificationAdapter:
    """Deliver federation notifications via standard library logging."""

    name = "console"

    async def send(
        self,
        message: FederationNotificationMessage,
    ) -> FederationNotificationDelivery:
        logger.info(
            "Federation notification [%s] %s — approval=%s federation=%s: %s",
            message.event_type.value,
            message.notification_id,
            message.approval_id,
            message.federation_id,
            message.body,
        )
        return FederationNotificationDelivery(
            notification_id=message.notification_id,
            channel=FederationNotificationChannel.CONSOLE,
            status=FederationNotificationStatus.SENT,
            delivered_at=datetime.now(timezone.utc),
        )


class FakeFederationNotificationAdapter:
    """Store sent notifications in memory for testing."""

    name = "fake"

    def __init__(self) -> None:
        self.sent: list[FederationNotificationMessage] = []

    async def send(
        self,
        message: FederationNotificationMessage,
    ) -> FederationNotificationDelivery:
        self.sent.append(message)
        return FederationNotificationDelivery(
            notification_id=message.notification_id,
            channel=message.channel,
            status=FederationNotificationStatus.SENT,
            delivered_at=datetime.now(timezone.utc),
        )


class WebhookFederationNotificationAdapter:
    """Deliver federation notifications via HTTP webhook (httpx optional)."""

    name = "webhook"

    def __init__(self, url: str, timeout_seconds: int = 5) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    async def send(
        self,
        message: FederationNotificationMessage,
    ) -> FederationNotificationDelivery:
        try:
            import httpx  # noqa: WPS433 — optional dependency, imported at method level
        except ImportError:
            return FederationNotificationDelivery(
                notification_id=message.notification_id,
                channel=FederationNotificationChannel.WEBHOOK,
                status=FederationNotificationStatus.FAILED,
                error="httpx not available",
            )

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    self.url,
                    json=message.model_dump(mode="json"),
                )
                response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — never crash on network failure
            return FederationNotificationDelivery(
                notification_id=message.notification_id,
                channel=FederationNotificationChannel.WEBHOOK,
                status=FederationNotificationStatus.FAILED,
                error=str(exc),
            )

        return FederationNotificationDelivery(
            notification_id=message.notification_id,
            channel=FederationNotificationChannel.WEBHOOK,
            status=FederationNotificationStatus.SENT,
            delivered_at=datetime.now(timezone.utc),
        )
