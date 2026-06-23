"""Alert delivery adapters — Memory, Webhook, Console.

Phase 53 Task 4: Alert delivery adapters.
"""
from __future__ import annotations

from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from pydantic import BaseModel, Field

from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryChannelType,
    AlertDeliveryTarget,
)
from agent_app.governance.policy_rollout_federation_notification_observability import (
    NotificationAlertEvent,
)
from agent_app.runtime.policy_rollout_federation_notification_webhook_signing import (
    make_signed_headers,
)


class AlertDeliveryAdapterResult(BaseModel):
    """Result from a delivery adapter."""

    success: bool
    error_code: str | None = None
    error_message: str | None = None
    response_metadata: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False


class AlertDeliveryAdapter:
    """Protocol class for alert delivery adapters."""

    def deliver(
        self,
        target: AlertDeliveryTarget,
        alert: NotificationAlertEvent | None,
        payload: dict[str, Any],
    ) -> AlertDeliveryAdapterResult:
        raise NotImplementedError


class MemoryAlertDeliveryAdapter:
    """In-memory adapter for testing. Captures delivered payloads."""

    def __init__(
        self,
        fail_next: bool = False,
        fail_always: bool = False,
        retryable: bool = True,
    ) -> None:
        self.fail_next = fail_next
        self.fail_always = fail_always
        self.retryable = retryable
        self.delivered: list[dict[str, Any]] = []

    def deliver(
        self,
        target: AlertDeliveryTarget,
        alert: NotificationAlertEvent | None,
        payload: dict[str, Any],
    ) -> AlertDeliveryAdapterResult:
        if self.fail_always or self.fail_next:
            if self.fail_next:
                self.fail_next = False
            return AlertDeliveryAdapterResult(
                success=False,
                error_code="MEMORY_FAIL_NEXT" if not self.fail_always else "MEMORY_FAIL_ALWAYS",
                error_message="Simulated delivery failure",
                retryable=self.retryable,
            )

        # Sanitize and store — only redact clearly sensitive key names
        _PAYLOAD_SENSITIVE = {"authorization", "token", "secret", "password",
                               "api_key", "x-signature", "x-api-key", "x-secret",
                               "x-auth-token", "x-webhook-secret", "cookie",
                               "signature", "private_key", "access_key"}
        sanitized = {
            k: "[REDACTED]" if k.lower() in _PAYLOAD_SENSITIVE else v
            for k, v in payload.items()
        }
        self.delivered.append(sanitized)
        return AlertDeliveryAdapterResult(
            success=True,
            response_metadata={"stored_count": len(self.delivered)},
        )


class WebhookAlertDeliveryAdapter:
    """Webhook adapter — real HTTP POST with optional HMAC-SHA256 signing."""

    def __init__(
        self,
        dry_run: bool = True,
        timeout_seconds: int = 10,
    ) -> None:
        self.dry_run = dry_run
        self.timeout_seconds = timeout_seconds

    def deliver(
        self,
        target: AlertDeliveryTarget,
        alert: NotificationAlertEvent | None,
        payload: dict[str, Any],
    ) -> AlertDeliveryAdapterResult:
        if self.dry_run:
            return AlertDeliveryAdapterResult(
                success=True,
                response_metadata={"mode": "dry_run", "endpoint": target.endpoint or ""},
            )

        if not target.endpoint:
            return AlertDeliveryAdapterResult(
                success=False,
                error_code="NO_ENDPOINT",
                error_message="Target has no endpoint configured",
                retryable=False,
            )

        try:
            import json
            data_bytes = json.dumps(payload).encode("utf-8")

            headers = {"Content-Type": "application/json"}
            if target.webhook_secret:
                headers = make_signed_headers(data_bytes, target.webhook_secret)

            req = Request(
                target.endpoint,
                data=data_bytes,
                headers=headers,
                method="POST",
            )
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                return AlertDeliveryAdapterResult(
                    success=True,
                    response_metadata={"status_code": resp.status},
                )
        except HTTPError as exc:
            return AlertDeliveryAdapterResult(
                success=False,
                error_code=f"HTTP_{exc.code}",
                error_message=str(exc),
                retryable=exc.code >= 500,
            )
        except Exception as exc:
            return AlertDeliveryAdapterResult(
                success=False,
                error_code="NETWORK_ERROR",
                error_message=str(exc),
                retryable=True,
            )


class ConsoleAlertDeliveryAdapter:
    """Console adapter — always succeeds, no network call."""

    def deliver(
        self,
        target: AlertDeliveryTarget,
        alert: NotificationAlertEvent | None,
        payload: dict[str, Any],
    ) -> AlertDeliveryAdapterResult:
        return AlertDeliveryAdapterResult(
            success=True,
            response_metadata={"channel": "console", "target": target.target_id},
        )
