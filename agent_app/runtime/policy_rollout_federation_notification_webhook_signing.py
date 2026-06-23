"""Webhook signing — HMAC-SHA256 payload signing with timestamp and nonce.

Phase 54: Webhook signing for alert delivery.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any


_SENSITIVE_KEYS = frozenset({
    "authorization", "token", "secret", "password", "api_key",
    "x-signature", "x-api-key", "x-secret", "x-auth-token",
    "x-webhook-secret", "cookie", "signature", "private_key",
    "access_key",
})


def sign_payload(
    payload_bytes: bytes,
    secret: str,
    timestamp: int | None = None,
) -> str:
    """Create HMAC-SHA256 signature for a payload.

    Returns: "v1=<hex_signature>"
    """
    if timestamp is None:
        timestamp = int(time.time())
    message = f"{timestamp}.{payload_bytes.decode('utf-8', errors='replace')}"
    sig = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"v1={sig}"


def make_signed_headers(
    payload_bytes: bytes,
    secret: str,
    base_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build headers dict with HMAC-SHA256 signature.

    Adds:
        X-Signature: v1=<hex>
        X-Timestamp: <unix_ts>
    Sensitive keys are NOT logged.
    """
    timestamp = int(time.time())
    signature = sign_payload(payload_bytes, secret, timestamp)
    headers = dict(base_headers) if base_headers else {}
    headers["Content-Type"] = "application/json"
    headers["X-Signature"] = signature
    headers["X-Timestamp"] = str(timestamp)
    return headers


def redact_sensitive(data: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive keys from a dict for safe logging."""
    return {
        k: "[REDACTED]" if k.lower() in _SENSITIVE_KEYS else v
        for k, v in data.items()
    }
