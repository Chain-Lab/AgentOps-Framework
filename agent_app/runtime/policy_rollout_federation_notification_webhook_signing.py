"""Webhook signing — HMAC-SHA256 payload signing with secret provider and key rotation.

Phase 54: Webhook signing for alert delivery.
Phase 57: Secret provider with active/previous/disabled key support and key rotation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Sensitive key set (shared across modules)
# ---------------------------------------------------------------------------

_SENSITIVE_KEYS = frozenset({
    "authorization", "token", "secret", "password", "api_key",
    "x-signature", "x-api-key", "x-secret", "x-auth-token",
    "x-webhook-secret", "cookie", "signature", "private_key",
    "access_key",
})


# ---------------------------------------------------------------------------
# Secret model
# ---------------------------------------------------------------------------


class WebhookSigningSecret(BaseModel):
    """A webhook signing secret with lifecycle status."""

    key_id: str = Field(..., description="Unique key identifier")
    secret: str = Field(..., description="The secret value (never logged)")
    status: str = Field(default="active", description="active | previous | disabled")
    not_before: datetime | None = Field(default=None, description="Not valid before this time")
    not_after: datetime | None = Field(default=None, description="Not valid after this time")

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str) -> str:
        if v not in ("active", "previous", "disabled"):
            raise ValueError(f"Invalid status '{v}'. Must be: active, previous, disabled")
        return v

    def is_valid(self, now: datetime | None = None) -> bool:
        """Whether this secret is currently valid for use.

        Only active secrets are valid for signing. Previous secrets
        are kept for verification of old signatures only.
        """
        if self.status != "active":
            return False
        if now is None:
            now = datetime.now(timezone.utc)
        if self.not_before is not None and now < self.not_before:
            return False
        if self.not_after is not None and now > self.not_after:
            return False
        return True

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        """Override to never include the secret value in dumps."""
        d = super().model_dump(**kwargs)
        d["secret"] = "[REDACTED]"
        return d

    def __repr__(self) -> str:
        return f"WebhookSigningSecret(key_id={self.key_id!r}, status={self.status!r})"


# ---------------------------------------------------------------------------
# Secret Provider Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class WebhookSigningSecretProvider(Protocol):
    """Protocol for providing webhook signing secrets."""

    def get_active(self) -> WebhookSigningSecret | None: ...
    def get_by_key_id(self, key_id: str) -> WebhookSigningSecret | None: ...
    def list_valid(self, now: datetime | None = None) -> list[WebhookSigningSecret]: ...


# ---------------------------------------------------------------------------
# In-memory provider
# ---------------------------------------------------------------------------


class InMemoryWebhookSigningSecretProvider:
    """In-memory webhook signing secret provider."""

    def __init__(self, secrets: list[WebhookSigningSecret] | None = None) -> None:
        self._secrets: dict[str, WebhookSigningSecret] = {}
        if secrets:
            for secret in secrets:
                self._secrets[secret.key_id] = secret

    def get_active(self) -> WebhookSigningSecret | None:
        for secret in self._secrets.values():
            if secret.status == "active" and secret.is_valid():
                return secret
        return None

    def get_by_key_id(self, key_id: str) -> WebhookSigningSecret | None:
        return self._secrets.get(key_id)

    def list_valid(self, now: datetime | None = None) -> list[WebhookSigningSecret]:
        """Return secrets valid for verification (active + previous).

        Includes both active and previous secrets that are within their
        time bounds. Used for verifying signatures from old and new keys.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        result = []
        for s in self._secrets.values():
            if s.status not in ("active", "previous"):
                continue
            if s.not_before is not None and now < s.not_before:
                continue
            if s.not_after is not None and now > s.not_after:
                continue
            result.append(s)
        return result

    def add_secret(self, secret: WebhookSigningSecret) -> None:
        self._secrets[secret.key_id] = secret


# ---------------------------------------------------------------------------
# Environment variable provider
# ---------------------------------------------------------------------------


class EnvWebhookSigningSecretProvider:
    """Webhook signing secret provider backed by environment variables.

    Expected environment variables:
        AGENTAPP_WEBHOOK_SIGNING_ACTIVE_KEY_ID=<key_id>
        AGENTAPP_WEBHOOK_SIGNING_SECRET_<KEY_ID_UPPER>=<secret>
        AGENTAPP_WEBHOOK_SIGNING_PREVIOUS_KEY_ID=<key_id>  (optional)
    """

    ENV_PREFIX = "AGENTAPP_WEBHOOK_SIGNING"

    def __init__(
        self,
        env: dict[str, str] | None = None,
        now: datetime | None = None,
    ) -> None:
        self._now = now
        self._secrets: dict[str, WebhookSigningSecret] = {}
        env_vars = env if env is not None else dict(os.environ)
        self._load_from_env(env_vars)

    def _load_from_env(self, env: dict[str, str]) -> None:
        active_key_id = env.get(f"{self.ENV_PREFIX}_ACTIVE_KEY_ID", "").strip()
        if not active_key_id:
            return

        secret_value = env.get(f"{self.ENV_PREFIX}_SECRET_{active_key_id.upper()}", "")
        if secret_value:
            self._secrets[active_key_id] = WebhookSigningSecret(
                key_id=active_key_id,
                secret=secret_value,
                status="active",
            )

        previous_key_id = env.get(f"{self.ENV_PREFIX}_PREVIOUS_KEY_ID", "").strip()
        if previous_key_id:
            prev_secret = env.get(f"{self.ENV_PREFIX}_SECRET_{previous_key_id.upper()}", "")
            if prev_secret:
                self._secrets[previous_key_id] = WebhookSigningSecret(
                    key_id=previous_key_id,
                    secret=prev_secret,
                    status="previous",
                )

    def get_active(self) -> WebhookSigningSecret | None:
        for secret in self._secrets.values():
            if secret.status == "active" and secret.is_valid(self._now):
                return secret
        return None

    def get_by_key_id(self, key_id: str) -> WebhookSigningSecret | None:
        return self._secrets.get(key_id)

    def list_valid(self, now: datetime | None = None) -> list[WebhookSigningSecret]:
        """Return secrets valid for verification (active + previous)."""
        ref = now or self._now or datetime.now(timezone.utc)
        result = []
        for s in self._secrets.values():
            if s.status not in ("active", "previous"):
                continue
            if s.not_before is not None and ref < s.not_before:
                continue
            if s.not_after is not None and ref > s.not_after:
                continue
            result.append(s)
        return result


# ---------------------------------------------------------------------------
# Signing functions
# ---------------------------------------------------------------------------


def sign_payload(
    payload_bytes: bytes,
    secret: str,
    timestamp: int | None = None,
    key_id: str = "default",
) -> str:
    """Create HMAC-SHA256 signature for a payload.

    Returns: "v1=<key_id>.<hex_signature>"
    """
    if timestamp is None:
        timestamp = int(time.time())
    message = f"{timestamp}.{payload_bytes.decode('utf-8', errors='replace')}"
    sig = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"v1={key_id}.{sig}"


def make_signed_headers(
    payload_bytes: bytes,
    secret: str,
    key_id: str = "default",
    base_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build headers dict with HMAC-SHA256 signature.

    Adds:
        X-Signature: v1=<key_id>.<hex>
        X-Timestamp: <unix_ts>
        X-Key-Id: <key_id>
    Sensitive keys are NOT logged.
    """
    timestamp = int(time.time())
    signature = sign_payload(payload_bytes, secret, timestamp, key_id)
    headers = dict(base_headers) if base_headers else {}
    headers["Content-Type"] = "application/json"
    headers["X-Signature"] = signature
    headers["X-Timestamp"] = str(timestamp)
    headers["X-Key-Id"] = key_id
    return headers


def make_signed_headers_with_provider(
    payload_bytes: bytes,
    secret_provider: WebhookSigningSecretProvider,
    base_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build signed headers using a secret provider (preferred for rotation)."""
    secret = secret_provider.get_active()
    if secret is None:
        raise ValueError("No active webhook signing secret available")
    return make_signed_headers(
        payload_bytes,
        secret.secret,
        key_id=secret.key_id,
        base_headers=base_headers,
    )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


class WebhookSignatureVerificationError(Exception):
    """Raised when webhook signature verification fails."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def verify_signed_payload(
    body: bytes,
    signature_header: str,
    timestamp_header: str,
    nonce_header: str | None,
    key_id: str,
    secret_provider: WebhookSigningSecretProvider,
    tolerance_seconds: int = 300,
    nonce_store: Any | None = None,
) -> bool:
    """Verify a signed webhook payload.

    Args:
        body: Raw request body bytes.
        signature_header: Value of X-Signature header (format: v1=<key_id>.<hex>).
        timestamp_header: Value of X-Timestamp header (unix timestamp).
        nonce_header: Optional nonce header for replay protection.
        key_id: Expected key ID to verify against.
        secret_provider: Secret provider to look up the signing secret.
        tolerance_seconds: Maximum age of the timestamp in seconds.
        nonce_store: Optional nonce store for replay protection.

    Returns:
        True if verification succeeds.

    Raises:
        WebhookSignatureVerificationError: If verification fails.
    """
    # Parse signature
    if not signature_header.startswith("v1="):
        raise WebhookSignatureVerificationError("Invalid signature format")
    sig_content = signature_header[3:]  # Remove "v1="

    # Parse key_id from signature
    parts = sig_content.split(".", 1)
    if len(parts) != 2:
        raise WebhookSignatureVerificationError("Malformed signature")
    sig_key_id, sig_hex = parts

    if sig_key_id != key_id:
        raise WebhookSignatureVerificationError(
            f"Key ID mismatch: expected {key_id}, got {sig_key_id}"
        )

    # Look up secret
    secret_obj = secret_provider.get_by_key_id(key_id)
    if secret_obj is None:
        raise WebhookSignatureVerificationError(f"Unknown key_id: {key_id}")

    # Check secret validity — active and previous are accepted for verification
    now = datetime.now(timezone.utc)
    if secret_obj.status == "disabled":
        raise WebhookSignatureVerificationError(
            f"Secret {key_id} is not valid (status={secret_obj.status})"
        )
    if secret_obj.not_before is not None and now < secret_obj.not_before:
        raise WebhookSignatureVerificationError(
            f"Secret {key_id} is not valid (not_before={secret_obj.not_before})"
        )
    if secret_obj.not_after is not None and now > secret_obj.not_after:
        raise WebhookSignatureVerificationError(
            f"Secret {key_id} is not valid (not_after={secret_obj.not_after})"
        )

    # Check timestamp tolerance
    try:
        ts = int(timestamp_header)
    except (ValueError, TypeError):
        raise WebhookSignatureVerificationError("Invalid timestamp")

    ts_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    age = abs((now - ts_dt).total_seconds())
    if age > tolerance_seconds:
        raise WebhookSignatureVerificationError(
            f"Timestamp outside tolerance: {age:.0f}s > {tolerance_seconds}s"
        )

    # Verify signature
    message = f"{timestamp_header}.{body.decode('utf-8', errors='replace')}"
    expected_sig = hmac.new(
        secret_obj.secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, sig_hex):
        raise WebhookSignatureVerificationError("Signature mismatch")

    # Check nonce replay
    if nonce_header and nonce_store is not None:
        if hasattr(nonce_store, "is_replay"):
            if nonce_store.is_replay(nonce_header):
                raise WebhookSignatureVerificationError("Nonce replay detected")

    return True


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def redact_sensitive(data: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive keys from a dict for safe logging."""
    return {
        k: "[REDACTED]" if k.lower() in _SENSITIVE_KEYS else v
        for k, v in data.items()
    }


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Redact sensitive HTTP headers."""
    return {
        k: "[REDACTED]" if k.lower() in _SENSITIVE_KEYS else v
        for k, v in headers.items()
    }
