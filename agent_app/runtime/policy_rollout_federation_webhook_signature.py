"""Federation webhook signature service — HMAC-SHA256 signing and verification.

Phase 51: Deterministic signing, key rotation, constant-time comparison.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from agent_app.governance.policy_rollout_federation_webhook import FederationWebhookSignatureResult


class FederationWebhookSignatureService:
    """Service for signing and verifying webhook requests.

    Signature format: v1=<hex_digest>
    Signature input: {timestamp}.{nonce}.{raw_body}

    Supports key rotation with active_key_id and keys dict.
    Uses constant-time comparison (hmac.compare_digest).
    """

    def __init__(
        self,
        *,
        active_key_id: str = "default",
        keys: dict[str, str] | None = None,
        signature_version: str = "v1",
        timestamp_tolerance_seconds: int = 300,
    ) -> None:
        if keys is None:
            keys = {"default": "default-secret-key-change-me"}
        self._active_key_id = active_key_id
        self._keys = keys
        self._signature_version = signature_version
        self._timestamp_tolerance = timestamp_tolerance_seconds

    @property
    def active_key_id(self) -> str:
        return self._active_key_id

    def sign(
        self,
        body: str,
        *,
        timestamp: datetime | None = None,
        nonce: str | None = None,
        key_id: str | None = None,
    ) -> dict[str, str]:
        """Sign a webhook request body.

        Returns dict with signature headers:
        - X-AgentApp-Signature: v1=<hex_digest>
        - X-AgentApp-Signature-Timestamp: ISO timestamp
        - X-AgentApp-Signature-Nonce: unique nonce
        - X-AgentApp-Signature-Version: signature version
        - X-AgentApp-Delivery-ID: unique delivery identifier
        - X-AgentApp-Key-ID: key used for signing
        """
        kid = key_id or self._active_key_id
        key = self._keys.get(kid)
        if key is None:
            raise ValueError(f"Key '{kid}' not found")

        ts = timestamp or datetime.now(timezone.utc)
        nonce = nonce or uuid.uuid4().hex
        delivery_id = f"fwd_{uuid.uuid4().hex}"

        ts_str = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        signature_input = f"{ts_str}.{nonce}.{body}"
        digest = hmac.new(
            key.encode("utf-8"),
            signature_input.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return {
            "X-AgentApp-Signature": f"{self._signature_version}={digest}",
            "X-AgentApp-Signature-Timestamp": ts_str,
            "X-AgentApp-Signature-Nonce": nonce,
            "X-AgentApp-Signature-Version": self._signature_version,
            "X-AgentApp-Delivery-ID": delivery_id,
            "X-AgentApp-Key-ID": kid,
        }

    def verify(
        self,
        body: str,
        signature: str,
        timestamp_str: str,
        nonce: str,
        *,
        nonce_store: Any | None = None,
        signature_version: str | None = None,
    ) -> FederationWebhookSignatureResult:
        """Verify a webhook request signature.

        Checks:
        1. Signature version matches
        2. Timestamp is within tolerance
        3. Nonce is unique (if nonce_store provided)
        4. Signature matches for some key
        """
        # Check version
        sv = signature_version or self._signature_version
        if not signature.startswith(f"{sv}="):
            return FederationWebhookSignatureResult(
                valid=False,
                reason="signature_version_mismatch",
                signature_version=sv,
                timestamp_valid=False,
            )

        # Check timestamp
        try:
            ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return FederationWebhookSignatureResult(
                valid=False,
                reason="invalid_timestamp_format",
                signature_version=sv,
                timestamp_valid=False,
            )

        now = datetime.now(timezone.utc)
        delta = abs((now - ts).total_seconds())
        timestamp_valid = delta <= self._timestamp_tolerance

        if not timestamp_valid:
            return FederationWebhookSignatureResult(
                valid=False,
                reason="timestamp_expired",
                signature_version=sv,
                timestamp_valid=False,
            )

        # Check nonce uniqueness
        nonce_valid: bool | None = None
        if nonce_store is not None:
            try:
                exists = nonce_store.exists_sync(nonce) if hasattr(nonce_store, "exists_sync") else False
                nonce_valid = not exists
            except Exception:  # noqa: BLE001
                nonce_valid = None  # Can't determine

        if nonce_valid is False:
            return FederationWebhookSignatureResult(
                valid=False,
                reason="nonce_reuse_detected",
                signature_version=sv,
                timestamp_valid=True,
                nonce_valid=False,
            )

        # Verify signature against all keys
        signature_input = f"{timestamp_str}.{nonce}.{body}"
        expected_prefix = f"{sv}="

        for key_id, key in self._keys.items():
            expected_digest = hmac.new(
                key.encode("utf-8"),
                signature_input.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            expected_sig = f"{expected_prefix}{expected_digest}"

            if hmac.compare_digest(signature, expected_sig):
                # Register nonce if store provided
                if nonce_store is not None and hasattr(nonce_store, "register_sync"):
                    try:
                        nonce_store.register_sync(nonce, ttl_seconds=self._timestamp_tolerance * 2)
                    except Exception:  # noqa: BLE001
                        pass

                return FederationWebhookSignatureResult(
                    valid=True,
                    matched_key_id=key_id,
                    signature_version=sv,
                    timestamp_valid=True,
                    nonce_valid=nonce_valid,
                )

        return FederationWebhookSignatureResult(
            valid=False,
            reason="signature_mismatch",
            signature_version=sv,
            timestamp_valid=True,
            nonce_valid=nonce_valid,
        )

    @staticmethod
    def deterministic_json_serialize(data: Any) -> str:
        """Serialize data to deterministic JSON (sorted keys, no whitespace)."""
        return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def compute_digest(body: str) -> str:
        """Compute SHA-256 digest of body."""
        return hashlib.sha256(body.encode("utf-8")).hexdigest()
