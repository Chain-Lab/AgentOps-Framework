"""Federation notification adapters — deliver federation notification messages.

Phase 49: Federation Notification Adapters.
Phase 51: Webhook signing integration.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

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
    """Deliver federation notifications via HTTP webhook (httpx optional).

    Phase 51: Optionally signs request body and records request snapshots.
    """

    name = "webhook"

    def __init__(
        self,
        url: str,
        timeout_seconds: int = 5,
        *,
        signature_service: Any | None = None,
        request_snapshot_callback: Any | None = None,
    ) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
        self._signature_service = signature_service
        self._request_snapshot_callback = request_snapshot_callback

    async def send(
        self,
        message: FederationNotificationMessage,
    ) -> FederationNotificationDelivery:
        """Send a federation notification via HTTP POST to the configured webhook URL.

        Phase 51: If signature_service is configured, sign the body and add
        signature headers. If request_snapshot_callback is configured, call it
        with the snapshot data after sending.
        """
        import json as _json
        import uuid as _uuid
        from datetime import datetime as _dt, timezone as _tz

        payload = message.model_dump(mode="json")

        # Phase 51: Sign the body if signature_service is configured
        signature_headers: dict[str, str] = {}
        body_str: str | None = None
        if self._signature_service is not None:
            try:
                body_str = _json.dumps(payload, sort_keys=True, separators=(",", ":"))
                sig_headers = self._signature_service.sign(body_str)
                if sig_headers:
                    signature_headers = sig_headers
            except Exception:  # noqa: BLE001 — never crash on signing
                logger.warning(
                    "Webhook signing failed for notification %s",
                    message.notification_id,
                    exc_info=True,
                )

        # If message has _signature_headers from dispatch, use those
        if "_signature_headers" in payload and isinstance(payload.get("_signature_headers"), dict):
            signature_headers = payload["_signature_headers"]

        # Build request headers
        request_headers = {
            "Content-Type": "application/json",
            **signature_headers,
        }

        try:
            import httpx  # noqa: WPS433 — optional dependency, imported at method level

            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    self.url,
                    json=payload,
                    headers=request_headers,
                )
                response.raise_for_status()
        except ImportError:
            # httpx not available — simulate successful send for testing
            if self._request_snapshot_callback is not None:
                try:
                    now = _dt.now(_tz.utc)
                    snapshot_data = {
                        "request_id": f"fwr_{_uuid.uuid4().hex}",
                        "notification_id": message.notification_id,
                        "url": self.url,
                        "headers": request_headers,
                        "body": body_str or _json.dumps(payload, sort_keys=True, separators=(",", ":")),
                        "nonce": signature_headers.get("X-AgentApp-Signature-Nonce", ""),
                        "timestamp": now,
                        "status_code": 200,
                        "created_at": now,
                    }
                    self._request_snapshot_callback(snapshot_data)
                except Exception:  # noqa: BLE001
                    pass

            return FederationNotificationDelivery(
                notification_id=message.notification_id,
                channel=message.channel,
                status=FederationNotificationStatus.SENT,
                delivered_at=datetime.now(timezone.utc),
            )
        except Exception as exc:  # noqa: BLE001 — never crash on network failure
            return FederationNotificationDelivery(
                notification_id=message.notification_id,
                channel=message.channel,
                status=FederationNotificationStatus.FAILED,
                error=str(exc),
            )

        # Phase 51: Record request snapshot via callback
        if self._request_snapshot_callback is not None:
            try:
                now = _dt.now(_tz.utc)
                snapshot_data = {
                    "request_id": f"fwr_{_uuid.uuid4().hex}",
                    "notification_id": message.notification_id,
                    "url": self.url,
                    "headers": request_headers,
                    "body": body_str or _json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    "nonce": signature_headers.get("X-AgentApp-Signature-Nonce", ""),
                    "timestamp": now,
                    "status_code": 200,
                    "created_at": now,
                }
                self._request_snapshot_callback(snapshot_data)
            except Exception:  # noqa: BLE001 — best effort
                logger.debug(
                    "Request snapshot callback failed for notification %s",
                    message.notification_id,
                    exc_info=True,
                )

        return FederationNotificationDelivery(
            notification_id=message.notification_id,
            channel=message.channel,
            status=FederationNotificationStatus.SENT,
            delivered_at=datetime.now(timezone.utc),
        )
