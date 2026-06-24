"""Tests for Phase 57: Webhook signing key rotation.

Phase 57 Task 6: Webhook signing secret provider with active/previous/disabled key support.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta

import pytest

from agent_app.runtime.policy_rollout_federation_notification_webhook_signing import (
    WebhookSigningSecret,
    InMemoryWebhookSigningSecretProvider,
    EnvWebhookSigningSecretProvider,
    sign_payload,
    make_signed_headers,
    make_signed_headers_with_provider,
    verify_signed_payload,
    WebhookSignatureVerificationError,
    redact_sensitive,
    redact_headers,
    _SENSITIVE_KEYS,
)


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestWebhookSigningSecret:
    """Phase 57: Webhook signing secret model tests."""

    def test_active_secret_is_valid(self):
        secret = WebhookSigningSecret(
            key_id="k1",
            secret="my-secret",
            status="active",
        )
        assert secret.is_valid() is True

    def test_disabled_secret_not_valid(self):
        secret = WebhookSigningSecret(
            key_id="k1",
            secret="my-secret",
            status="disabled",
        )
        assert secret.is_valid() is False

    def test_previous_secret_not_valid_for_signing(self):
        secret = WebhookSigningSecret(
            key_id="k0",
            secret="old-secret",
            status="previous",
        )
        # previous is not "valid" for new signing (only active is)
        assert secret.is_valid() is False

    def test_expired_secret_not_valid(self):
        past = datetime.now(timezone.utc) - timedelta(days=1)
        secret = WebhookSigningSecret(
            key_id="k1",
            secret="my-secret",
            status="active",
            not_after=past,
        )
        assert secret.is_valid() is False

    def test_future_not_before_not_valid(self):
        future = datetime.now(timezone.utc) + timedelta(days=1)
        secret = WebhookSigningSecret(
            key_id="k1",
            secret="my-secret",
            status="active",
            not_before=future,
        )
        assert secret.is_valid() is False

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError, match="Invalid status"):
            WebhookSigningSecret(key_id="k1", secret="s", status="invalid")

    def test_secret_redacted_in_model_dump(self):
        secret = WebhookSigningSecret(key_id="k1", secret="my-secret")
        d = secret.model_dump()
        assert d["secret"] == "[REDACTED]"

    def test_secret_redacted_in_repr(self):
        secret = WebhookSigningSecret(key_id="k1", secret="my-secret")
        r = repr(secret)
        assert "my-secret" not in r


# ---------------------------------------------------------------------------
# InMemory provider tests
# ---------------------------------------------------------------------------


class TestInMemorySecretProvider:
    """Phase 57: In-memory secret provider tests."""

    def test_get_active_returns_active(self):
        provider = InMemoryWebhookSigningSecretProvider([
            WebhookSigningSecret(key_id="k1", secret="s1", status="active"),
            WebhookSigningSecret(key_id="k0", secret="s0", status="previous"),
        ])
        active = provider.get_active()
        assert active is not None
        assert active.key_id == "k1"
        assert active.status == "active"

    def test_get_active_returns_none_when_none_active(self):
        provider = InMemoryWebhookSigningSecretProvider([
            WebhookSigningSecret(key_id="k0", secret="s0", status="previous"),
            WebhookSigningSecret(key_id="k-1", secret="s-1", status="disabled"),
        ])
        assert provider.get_active() is None

    def test_get_by_key_id(self):
        provider = InMemoryWebhookSigningSecretProvider([
            WebhookSigningSecret(key_id="k1", secret="s1", status="active"),
        ])
        secret = provider.get_by_key_id("k1")
        assert secret is not None
        assert secret.key_id == "k1"
        assert secret.secret == "s1"

    def test_get_by_key_id_missing(self):
        provider = InMemoryWebhookSigningSecretProvider([])
        assert provider.get_by_key_id("nonexistent") is None

    def test_list_valid(self):
        provider = InMemoryWebhookSigningSecretProvider([
            WebhookSigningSecret(key_id="k1", secret="s1", status="active"),
            WebhookSigningSecret(key_id="k0", secret="s0", status="previous"),
            WebhookSigningSecret(key_id="k-1", secret="s-1", status="disabled"),
        ])
        valid = provider.list_valid()
        assert len(valid) == 2  # active + previous
        key_ids = {s.key_id for s in valid}
        assert "k1" in key_ids
        assert "k0" in key_ids
        assert "k-1" not in key_ids

    def test_add_secret(self):
        provider = InMemoryWebhookSigningSecretProvider([])
        provider.add_secret(WebhookSigningSecret(key_id="k1", secret="s1", status="active"))
        assert provider.get_active() is not None
        assert provider.get_active().key_id == "k1"


# ---------------------------------------------------------------------------
# Env provider tests
# ---------------------------------------------------------------------------


class TestEnvSecretProvider:
    """Phase 57: Environment variable secret provider tests."""

    def test_loads_active_key(self):
        env = {
            "AGENTAPP_WEBHOOK_SIGNING_ACTIVE_KEY_ID": "k1",
            "AGENTAPP_WEBHOOK_SIGNING_SECRET_K1": "secret-value-1",
        }
        provider = EnvWebhookSigningSecretProvider(env=env)
        active = provider.get_active()
        assert active is not None
        assert active.key_id == "k1"
        assert active.status == "active"

    def test_loads_previous_key(self):
        env = {
            "AGENTAPP_WEBHOOK_SIGNING_ACTIVE_KEY_ID": "k1",
            "AGENTAPP_WEBHOOK_SIGNING_SECRET_K1": "secret-1",
            "AGENTAPP_WEBHOOK_SIGNING_PREVIOUS_KEY_ID": "k0",
            "AGENTAPP_WEBHOOK_SIGNING_SECRET_K0": "secret-0",
        }
        provider = EnvWebhookSigningSecretProvider(env=env)
        k0 = provider.get_by_key_id("k0")
        assert k0 is not None
        assert k0.status == "previous"

    def test_no_keys_when_no_env(self):
        provider = EnvWebhookSigningSecretProvider(env={})
        assert provider.get_active() is None

    def test_no_keys_when_only_active_id_no_secret(self):
        env = {
            "AGENTAPP_WEBHOOK_SIGNING_ACTIVE_KEY_ID": "k1",
        }
        provider = EnvWebhookSigningSecretProvider(env=env)
        assert provider.get_active() is None


# ---------------------------------------------------------------------------
# Signing tests
# ---------------------------------------------------------------------------


class TestSigningWithKeyRotation:
    """Phase 57: Signing with key_id tests."""

    def test_active_secret_signs_payload(self):
        provider = InMemoryWebhookSigningSecretProvider([
            WebhookSigningSecret(key_id="k1", secret="my-secret", status="active"),
        ])
        headers = make_signed_headers_with_provider(b"test payload", provider)
        assert "X-Signature" in headers
        assert "X-Timestamp" in headers
        assert "X-Key-Id" in headers
        assert headers["X-Key-Id"] == "k1"
        assert headers["X-Signature"].startswith("v1=k1.")

    def test_key_id_header_emitted(self):
        provider = InMemoryWebhookSigningSecretProvider([
            WebhookSigningSecret(key_id="my-key", secret="secret-val", status="active"),
        ])
        headers = make_signed_headers_with_provider(b"payload", provider)
        assert headers["X-Key-Id"] == "my-key"

    def test_no_active_secret_raises(self):
        provider = InMemoryWebhookSigningSecretProvider([])
        with pytest.raises(ValueError, match="No active"):
            make_signed_headers_with_provider(b"payload", provider)

    def test_sign_payload_format(self):
        sig = sign_payload(b"hello", "secret", timestamp=1000000, key_id="k1")
        assert sig.startswith("v1=k1.")
        assert len(sig) > len("v1=k1.")


# ---------------------------------------------------------------------------
# Verification tests
# ---------------------------------------------------------------------------


class TestSignatureVerification:
    """Phase 57: Webhook signature verification tests."""

    def test_verify_valid_signature(self):
        provider = InMemoryWebhookSigningSecretProvider([
            WebhookSigningSecret(key_id="k1", secret="my-secret", status="active"),
        ])
        payload = b'{"alert_id": "a1"}'
        headers = make_signed_headers_with_provider(payload, provider)
        ts = headers["X-Timestamp"]
        sig = headers["X-Signature"]
        assert verify_signed_payload(
            body=payload,
            signature_header=sig,
            timestamp_header=ts,
            nonce_header=None,
            key_id="k1",
            secret_provider=provider,
        ) is True

    def test_verify_wrong_key_id_rejected(self):
        provider = InMemoryWebhookSigningSecretProvider([
            WebhookSigningSecret(key_id="k1", secret="my-secret", status="active"),
        ])
        payload = b'{"alert_id": "a1"}'
        headers = make_signed_headers_with_provider(payload, provider)
        with pytest.raises(WebhookSignatureVerificationError, match="Key ID mismatch"):
            verify_signed_payload(
                body=payload,
                signature_header=headers["X-Signature"],
                timestamp_header=headers["X-Timestamp"],
                nonce_header=None,
                key_id="wrong-key",
                secret_provider=provider,
            )

    def test_verify_disabled_secret_rejected(self):
        provider = InMemoryWebhookSigningSecretProvider([
            WebhookSigningSecret(key_id="k1", secret="my-secret", status="disabled"),
        ])
        payload = b'{"alert_id": "a1"}'
        ts = str(int(time.time()))
        sig = sign_payload(payload, "my-secret", timestamp=int(ts), key_id="k1")
        with pytest.raises(WebhookSignatureVerificationError, match="not valid"):
            verify_signed_payload(
                body=payload,
                signature_header=sig,
                timestamp_header=ts,
                nonce_header=None,
                key_id="k1",
                secret_provider=provider,
            )

    def test_verify_expired_secret_rejected(self):
        past = datetime.now(timezone.utc) - timedelta(days=1)
        provider = InMemoryWebhookSigningSecretProvider([
            WebhookSigningSecret(key_id="k1", secret="my-secret", status="active", not_after=past),
        ])
        payload = b'{"alert_id": "a1"}'
        ts = str(int(time.time()))
        sig = sign_payload(payload, "my-secret", timestamp=int(ts), key_id="k1")
        with pytest.raises(WebhookSignatureVerificationError, match="not valid"):
            verify_signed_payload(
                body=payload,
                signature_header=sig,
                timestamp_header=ts,
                nonce_header=None,
                key_id="k1",
                secret_provider=provider,
            )

    def test_verify_future_not_before_rejected(self):
        future = datetime.now(timezone.utc) + timedelta(days=1)
        provider = InMemoryWebhookSigningSecretProvider([
            WebhookSigningSecret(key_id="k1", secret="my-secret", status="active", not_before=future),
        ])
        payload = b'{"alert_id": "a1"}'
        ts = str(int(time.time()))
        sig = sign_payload(payload, "my-secret", timestamp=int(ts), key_id="k1")
        with pytest.raises(WebhookSignatureVerificationError, match="not valid"):
            verify_signed_payload(
                body=payload,
                signature_header=sig,
                timestamp_header=ts,
                nonce_header=None,
                key_id="k1",
                secret_provider=provider,
            )

    def test_verify_wrong_signature_rejected(self):
        provider = InMemoryWebhookSigningSecretProvider([
            WebhookSigningSecret(key_id="k1", secret="my-secret", status="active"),
        ])
        payload = b'{"alert_id": "a1"}'
        ts = str(int(time.time()))
        bad_sig = "v1=k1.00000000000000000000000000000000"
        with pytest.raises(WebhookSignatureVerificationError, match="Signature mismatch"):
            verify_signed_payload(
                body=payload,
                signature_header=bad_sig,
                timestamp_header=ts,
                nonce_header=None,
                key_id="k1",
                secret_provider=provider,
            )

    def test_verify_timestamp_outside_tolerance_rejected(self):
        provider = InMemoryWebhookSigningSecretProvider([
            WebhookSigningSecret(key_id="k1", secret="my-secret", status="active"),
        ])
        payload = b'{"alert_id": "a1"}'
        old_ts = str(int(time.time()) - 1000)  # 1000 seconds ago
        sig = sign_payload(payload, "my-secret", timestamp=int(old_ts), key_id="k1")
        with pytest.raises(WebhookSignatureVerificationError, match="outside tolerance"):
            verify_signed_payload(
                body=payload,
                signature_header=sig,
                timestamp_header=old_ts,
                nonce_header=None,
                key_id="k1",
                secret_provider=provider,
                tolerance_seconds=300,
            )

    def test_verify_previous_key_verifies(self):
        """Previous keys can verify old signatures."""
        provider = InMemoryWebhookSigningSecretProvider([
            WebhookSigningSecret(key_id="k1", secret="new-secret", status="active"),
            WebhookSigningSecret(key_id="k0", secret="old-secret", status="previous"),
        ])
        payload = b'{"alert_id": "a1"}'
        # Sign with previous key
        ts = str(int(time.time()))
        sig = sign_payload(payload, "old-secret", timestamp=int(ts), key_id="k0")
        # Verify with provider (looks up by key_id)
        assert verify_signed_payload(
            body=payload,
            signature_header=sig,
            timestamp_header=ts,
            nonce_header=None,
            key_id="k0",
            secret_provider=provider,
        ) is True

    def test_verify_nonce_replay_rejected(self):
        """Nonce replay is rejected when nonce_store supports is_replay."""
        provider = InMemoryWebhookSigningSecretProvider([
            WebhookSigningSecret(key_id="k1", secret="my-secret", status="active"),
        ])

        class FakeNonceStore:
            def __init__(self):
                self.seen: set[str] = set()

            def is_replay(self, nonce: str) -> bool:
                if nonce in self.seen:
                    return True
                self.seen.add(nonce)
                return False

        nonce_store = FakeNonceStore()
        payload = b'{"alert_id": "a1"}'
        ts = str(int(time.time()))
        sig = sign_payload(payload, "my-secret", timestamp=int(ts), key_id="k1")
        nonce = "test-nonce-123"

        # First call succeeds
        assert verify_signed_payload(
            body=payload,
            signature_header=sig,
            timestamp_header=ts,
            nonce_header=nonce,
            key_id="k1",
            secret_provider=provider,
            nonce_store=nonce_store,
        ) is True
        # Second call with same nonce fails
        with pytest.raises(WebhookSignatureVerificationError, match="Nonce replay"):
            verify_signed_payload(
                body=payload,
                signature_header=sig,
                timestamp_header=ts,
                nonce_header=nonce,
                key_id="k1",
                secret_provider=provider,
                nonce_store=nonce_store,
            )


# ---------------------------------------------------------------------------
# Redaction tests
# ---------------------------------------------------------------------------


class TestRedaction:
    """Phase 57: Sensitive data redaction tests."""

    def test_redact_sensitive_keys_in_dict(self):
        data = {"authorization": "Bearer xyz", "alert_id": "a1", "count": 42}
        result = redact_sensitive(data)
        assert result["authorization"] == "[REDACTED]"
        assert result["alert_id"] == "a1"
        assert result["count"] == 42

    def test_redact_headers(self):
        headers = {
            "Content-Type": "application/json",
            "X-Signature": "v1=abc",
            "X-Api-Key": "secret-key",
        }
        result = redact_headers(headers)
        assert result["Content-Type"] == "application/json"
        assert result["X-Signature"] == "[REDACTED]"
        assert result["X-Api-Key"] == "[REDACTED]"

    def test_case_insensitive_redaction(self):
        data = {"AUTHORIZATION": "Bearer xyz", "Token": "abc"}
        result = redact_sensitive(data)
        assert result["AUTHORIZATION"] == "[REDACTED]"
        assert result["Token"] == "[REDACTED]"
