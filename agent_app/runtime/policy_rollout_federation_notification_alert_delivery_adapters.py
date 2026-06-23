"""Alert delivery adapters — Memory, Webhook, Console.

Phase 53 Task 4: Alert delivery adapters.
Phase 55 Task 3: HTTP transport abstraction for webhook adapter.
"""
from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable
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


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class AlertDeliveryAdapterResult(BaseModel):
    """Result from a delivery adapter."""

    success: bool
    error_code: str | None = None
    error_message: str | None = None
    response_metadata: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False


class HttpTransportResult(BaseModel):
    """Result from an HTTP transport."""

    status_code: int | None = None
    body_preview: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    timed_out: bool = False


# ---------------------------------------------------------------------------
# Transport Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class HttpTransport(Protocol):
    """Injectable HTTP transport for webhook delivery."""

    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: int,
        proxy_url: str | None = None,
    ) -> HttpTransportResult:
        ...


# ---------------------------------------------------------------------------
# Transport implementations
# ---------------------------------------------------------------------------


class UrllibHttpTransport:
    """HTTP transport using stdlib urllib."""

    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: int,
        proxy_url: str | None = None,
    ) -> HttpTransportResult:
        try:
            data_bytes = json.dumps(payload).encode("utf-8")
            req = Request(url, data=data_bytes, headers=headers, method="POST")
            with urlopen(req, timeout=timeout_seconds) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                preview = body[:500] if body else ""
                return HttpTransportResult(
                    status_code=resp.status,
                    body_preview=preview,
                    headers=dict(resp.headers),
                )
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            preview = body[:500] if body else ""
            return HttpTransportResult(
                status_code=exc.code,
                body_preview=preview,
                error_code=f"HTTP_{exc.code}",
                error_message=str(exc),
                retryable=exc.code >= 500,
            )
        except Exception as exc:
            return HttpTransportResult(
                error_code="NETWORK_ERROR",
                error_message=str(exc),
                timed_out="timed out" in str(exc).lower(),
            )


class FakeHttpTransport:
    """Test double for HTTP transport."""

    def __init__(
        self,
        responses: list[HttpTransportResult] | None = None,
        fail_next: bool = False,
        fail_always: bool = False,
        timeout: bool = False,
    ) -> None:
        self.responses = responses or []
        self.fail_next = fail_next
        self.fail_always = fail_always
        self.timeout = timeout
        self.calls: list[dict[str, Any]] = []

    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: int,
        proxy_url: str | None = None,
    ) -> HttpTransportResult:
        self.calls.append({
            "url": url,
            "payload": payload,
            "headers": _redact_headers(headers),
            "timeout_seconds": timeout_seconds,
            "proxy_url": proxy_url,
        })
        if self.fail_always or self.fail_next:
            if self.fail_next:
                self.fail_next = False
            code = "HTTP_500" if not self.fail_always else "HTTP_400"
            return HttpTransportResult(
                status_code=500 if not self.fail_always else 400,
                error_code=code,
                error_message="Simulated transport failure",
                retryable=not self.fail_always,
            )
        if self.timeout:
            return HttpTransportResult(
                timed_out=True,
                error_code="TIMEOUT",
                error_message="Simulated timeout",
            )
        if self.responses:
            return self.responses.pop(0)
        return HttpTransportResult(status_code=200, body_preview="OK")


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    _SENSITIVE_KEYS = frozenset({
        "authorization", "token", "secret", "password", "api_key",
        "x-signature", "x-api-key", "x-secret", "x-auth-token",
        "x-webhook-secret", "cookie", "signature", "private_key",
        "access_key",
    })
    return {
        k: "[REDACTED]" if k.lower() in _SENSITIVE_KEYS else v
        for k, v in headers.items()
    }


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


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
    """Webhook adapter — HTTP POST via injectable transport.

    Phase 55: Uses HttpTransport abstraction instead of direct urllib calls.
    Defaults to UrllibHttpTransport if none provided.
    """

    def __init__(
        self,
        dry_run: bool = True,
        timeout_seconds: int = 10,
        transport: HttpTransport | None = None,
        proxy_url: str | None = None,
        user_agent: str = "agent-app-framework-alert-delivery/1.0",
    ) -> None:
        self.dry_run = dry_run
        self.timeout_seconds = timeout_seconds
        self._transport = transport or UrllibHttpTransport()
        self._proxy_url = proxy_url
        self._user_agent = user_agent

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
            data_bytes = json.dumps(payload).encode("utf-8")

            headers = {"Content-Type": "application/json", "User-Agent": self._user_agent}
            if target.webhook_secret:
                headers = make_signed_headers(data_bytes, target.webhook_secret, base_headers=headers)

            result = self._transport.post_json(
                url=target.endpoint,
                payload=payload,
                headers=headers,
                timeout_seconds=self.timeout_seconds,
                proxy_url=self._proxy_url,
            )

            if result.error_code:
                return AlertDeliveryAdapterResult(
                    success=False,
                    error_code=result.error_code,
                    error_message=result.error_message or "Transport error",
                    response_metadata={"body_preview": result.body_preview or ""},
                    retryable=result.error_code.startswith("HTTP_5") or result.timed_out,
                )

            return AlertDeliveryAdapterResult(
                success=True,
                response_metadata={
                    "status_code": result.status_code,
                    "body_preview": result.body_preview or "",
                },
            )
        except Exception as exc:
            return AlertDeliveryAdapterResult(
                success=False,
                error_code="TRANSPORT_EXCEPTION",
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
