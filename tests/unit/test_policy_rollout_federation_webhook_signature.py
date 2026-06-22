"""Tests for FederationWebhookSignatureService — signing, verification, key rotation."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from agent_app.governance.policy_rollout_federation_webhook import FederationWebhookSignatureResult
from agent_app.runtime.policy_rollout_federation_webhook_signature import (
    FederationWebhookSignatureService,
)


def _make_service(**overrides) -> FederationWebhookSignatureService:
    defaults = dict(
        active_key_id="default",
        keys={"default": "test-secret-key"},
        signature_version="v1",
        timestamp_tolerance_seconds=300,
    )
    defaults.update(overrides)
    return FederationWebhookSignatureService(**defaults)


class TestSignReturnsHeaders:
    def test_sign_returns_headers(self) -> None:
        svc = _make_service()
        result = svc.sign('{"event":"test"}')
        assert "X-AgentApp-Signature" in result
        assert "X-AgentApp-Signature-Timestamp" in result
        assert "X-AgentApp-Signature-Nonce" in result
        assert "X-AgentApp-Signature-Version" in result
        assert "X-AgentApp-Delivery-ID" in result
        assert "X-AgentApp-Key-ID" in result


class TestSignHmacSha256:
    def test_sign_hmac_sha256(self) -> None:
        svc = _make_service(keys={"default": "my-secret"})
        body = '{"event":"test"}'
        nonce = "abc123"
        ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = svc.sign(body, timestamp=ts, nonce=nonce)

        # Manually compute expected HMAC
        ts_str = "2026-01-01T12:00:00Z"
        sig_input = f"{ts_str}.{nonce}.{body}"
        expected_digest = hmac.new(
            b"my-secret",
            sig_input.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert result["X-AgentApp-Signature"] == f"v1={expected_digest}"


class TestSignCustomTimestamp:
    def test_sign_custom_timestamp(self) -> None:
        svc = _make_service()
        ts = datetime(2025, 6, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = svc.sign("body", timestamp=ts)
        assert result["X-AgentApp-Signature-Timestamp"] == "2025-06-15T10:30:00Z"


class TestSignCustomNonce:
    def test_sign_custom_nonce(self) -> None:
        svc = _make_service()
        result = svc.sign("body", nonce="mynonce123")
        assert result["X-AgentApp-Signature-Nonce"] == "mynonce123"


class TestSignMissingKeyRaises:
    def test_sign_missing_key_raises(self) -> None:
        svc = _make_service(keys={"default": "key1"})
        try:
            svc.sign("body", key_id="nonexistent")
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "not found" in str(e)


class TestVerifyValidSignature:
    def test_verify_valid_signature(self) -> None:
        svc = _make_service(keys={"default": "secret1"})
        body = '{"event":"push"}'
        ts = datetime.now(timezone.utc)
        nonce = "nonce123"
        headers = svc.sign(body, timestamp=ts, nonce=nonce)

        result = svc.verify(
            body,
            headers["X-AgentApp-Signature"],
            headers["X-AgentApp-Signature-Timestamp"],
            nonce,
        )
        assert result.valid is True
        assert result.matched_key_id == "default"
        assert result.timestamp_valid is True
        assert result.nonce_valid is None  # no nonce store


class TestVerifyInvalidSignature:
    def test_verify_invalid_signature(self) -> None:
        svc = _make_service(keys={"default": "secret1"})
        result = svc.verify(
            "body",
            "v1=0000000000000000000000000000000000000000000000000000000000000000",
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "nonce123",
        )
        assert result.valid is False
        assert result.reason == "signature_mismatch"


class TestVerifyWrongVersion:
    def test_verify_wrong_version(self) -> None:
        svc = _make_service(signature_version="v1")
        result = svc.verify(
            "body",
            "v2=somedigest",
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "nonce123",
        )
        assert result.valid is False
        assert result.reason == "signature_version_mismatch"


class TestVerifyExpiredTimestamp:
    def test_verify_expired_timestamp(self) -> None:
        svc = _make_service(timestamp_tolerance_seconds=300)
        old_ts = datetime.now(timezone.utc) - timedelta(seconds=600)
        ts_str = old_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        result = svc.verify("body", "v1=somehex", ts_str, "nonce123")
        assert result.valid is False
        assert result.reason == "timestamp_expired"
        assert result.timestamp_valid is False


class TestVerifyFutureTimestampWithinTolerance:
    def test_future_timestamp_within_tolerance(self) -> None:
        svc = _make_service(keys={"default": "secret1"}, timestamp_tolerance_seconds=300)
        body = "test-body"
        future_ts = datetime.now(timezone.utc) + timedelta(seconds=60)
        nonce = "nonce456"
        headers = svc.sign(body, timestamp=future_ts, nonce=nonce)

        result = svc.verify(
            body,
            headers["X-AgentApp-Signature"],
            headers["X-AgentApp-Signature-Timestamp"],
            nonce,
        )
        assert result.valid is True
        assert result.timestamp_valid is True


class TestVerifyInvalidTimestampFormat:
    def test_verify_invalid_timestamp_format(self) -> None:
        svc = _make_service()
        result = svc.verify("body", "v1=somehex", "not-a-timestamp", "nonce123")
        assert result.valid is False
        assert result.reason == "invalid_timestamp_format"
        assert result.timestamp_valid is False


class TestVerifyNonceReuseDetected:
    def test_verify_nonce_reuse_detected(self) -> None:
        from agent_app.runtime.policy_rollout_federation_webhook_nonce_store import (
            InMemoryFederationWebhookNonceStore,
        )

        svc = _make_service(keys={"default": "secret1"})
        nonce_store = InMemoryFederationWebhookNonceStore()
        body = "body"
        nonce = "reused-nonce"
        ts = datetime.now(timezone.utc)
        headers = svc.sign(body, timestamp=ts, nonce=nonce)

        # First verify should succeed and register the nonce
        result1 = svc.verify(
            body,
            headers["X-AgentApp-Signature"],
            headers["X-AgentApp-Signature-Timestamp"],
            nonce,
            nonce_store=nonce_store,
        )
        assert result1.valid is True

        # Second verify with same nonce should fail
        result2 = svc.verify(
            body,
            headers["X-AgentApp-Signature"],
            headers["X-AgentApp-Signature-Timestamp"],
            nonce,
            nonce_store=nonce_store,
        )
        assert result2.valid is False
        assert result2.reason == "nonce_reuse_detected"
        assert result2.nonce_valid is False


class TestVerifyKeyRotationOldKeyValid:
    def test_verify_key_rotation_old_key_valid(self) -> None:
        svc = _make_service(
            active_key_id="new",
            keys={"old": "old-secret", "new": "new-secret"},
        )
        body = "test-body"
        ts = datetime.now(timezone.utc)
        nonce = "nonce-old"
        # Sign with old key
        headers = svc.sign(body, timestamp=ts, nonce=nonce, key_id="old")

        result = svc.verify(
            body,
            headers["X-AgentApp-Signature"],
            headers["X-AgentApp-Signature-Timestamp"],
            nonce,
        )
        assert result.valid is True
        assert result.matched_key_id == "old"


class TestVerifyKeyRotationNewKeyValid:
    def test_verify_key_rotation_new_key_valid(self) -> None:
        svc = _make_service(
            active_key_id="new",
            keys={"old": "old-secret", "new": "new-secret"},
        )
        body = "test-body"
        ts = datetime.now(timezone.utc)
        nonce = "nonce-new"
        headers = svc.sign(body, timestamp=ts, nonce=nonce, key_id="new")

        result = svc.verify(
            body,
            headers["X-AgentApp-Signature"],
            headers["X-AgentApp-Signature-Timestamp"],
            nonce,
        )
        assert result.valid is True
        assert result.matched_key_id == "new"


class TestVerifyConstantTimeComparison:
    def test_verify_constant_time_comparison(self) -> None:
        """Verify that hmac.compare_digest is used, not ==."""
        svc = _make_service(keys={"default": "secret1"})
        body = "body"
        ts = datetime.now(timezone.utc)
        nonce = "nonce-ct"
        headers = svc.sign(body, timestamp=ts, nonce=nonce)

        # Patch hmac.compare_digest to ensure it is called
        with patch("agent_app.runtime.policy_rollout_federation_webhook_signature.hmac.compare_digest", wraps=hmac.compare_digest) as mock_cmp:
            result = svc.verify(
                body,
                headers["X-AgentApp-Signature"],
                headers["X-AgentApp-Signature-Timestamp"],
                nonce,
            )
            assert result.valid is True
            assert mock_cmp.called


class TestBodyChangeInvalidatesSignature:
    def test_body_change_invalidates_signature(self) -> None:
        svc = _make_service(keys={"default": "secret1"})
        body = "original-body"
        ts = datetime.now(timezone.utc)
        nonce = "nonce-body"
        headers = svc.sign(body, timestamp=ts, nonce=nonce)

        result = svc.verify(
            "tampered-body",
            headers["X-AgentApp-Signature"],
            headers["X-AgentApp-Signature-Timestamp"],
            nonce,
        )
        assert result.valid is False
        assert result.reason == "signature_mismatch"


class TestDeterministicJsonSerialize:
    def test_deterministic_json_serialize(self) -> None:
        data = {"z": 1, "a": 2, "m": 3}
        result = FederationWebhookSignatureService.deterministic_json_serialize(data)
        assert result == '{"a":2,"m":3,"z":1}'


class TestDeterministicJsonKeyOrder:
    def test_deterministic_json_key_order(self) -> None:
        data1 = {"b": 2, "a": 1}
        data2 = {"a": 1, "b": 2}
        s1 = FederationWebhookSignatureService.deterministic_json_serialize(data1)
        s2 = FederationWebhookSignatureService.deterministic_json_serialize(data2)
        assert s1 == s2


class TestComputeDigest:
    def test_compute_digest(self) -> None:
        body = "test-body"
        expected = hashlib.sha256(body.encode("utf-8")).hexdigest()
        assert FederationWebhookSignatureService.compute_digest(body) == expected


class TestSignWithCustomKeyId:
    def test_sign_with_custom_key_id(self) -> None:
        svc = _make_service(keys={"default": "key1", "custom": "key2"})
        result = svc.sign("body", key_id="custom")
        assert result["X-AgentApp-Key-ID"] == "custom"


class TestSignDefaultKeyId:
    def test_sign_default_key_id(self) -> None:
        svc = _make_service(active_key_id="primary", keys={"primary": "key1"})
        result = svc.sign("body")
        assert result["X-AgentApp-Key-ID"] == "primary"


class TestVerifyReturnsMatchedKeyId:
    def test_verify_returns_matched_key_id(self) -> None:
        svc = _make_service(keys={"key_a": "secret_a", "key_b": "secret_b"})
        body = "body"
        ts = datetime.now(timezone.utc)
        nonce = "nonce-key-id"
        headers = svc.sign(body, timestamp=ts, nonce=nonce, key_id="key_b")

        result = svc.verify(
            body,
            headers["X-AgentApp-Signature"],
            headers["X-AgentApp-Signature-Timestamp"],
            nonce,
        )
        assert result.valid is True
        assert result.matched_key_id == "key_b"


class TestVerifyNoMatchingKey:
    def test_verify_no_matching_key(self) -> None:
        svc = _make_service(keys={"key_a": "secret_a"})
        body = "body"
        ts = datetime.now(timezone.utc)
        # Sign with a different key not in the service
        other_svc = _make_service(keys={"key_other": "different_secret"})
        headers = other_svc.sign(body, timestamp=ts, nonce="nonce-nomatch", key_id="key_other")

        result = svc.verify(
            body,
            headers["X-AgentApp-Signature"],
            headers["X-AgentApp-Signature-Timestamp"],
            "nonce-nomatch",
        )
        assert result.valid is False
        assert result.reason == "signature_mismatch"


class TestVerifyNonceValidNoneWhenNoStore:
    def test_verify_nonce_valid_none_when_no_store(self) -> None:
        svc = _make_service(keys={"default": "secret1"})
        body = "body"
        ts = datetime.now(timezone.utc)
        nonce = "nonce-no-store"
        headers = svc.sign(body, timestamp=ts, nonce=nonce)

        result = svc.verify(
            body,
            headers["X-AgentApp-Signature"],
            headers["X-AgentApp-Signature-Timestamp"],
            nonce,
        )
        assert result.valid is True
        assert result.nonce_valid is None


class TestSignDeliveryIdFormat:
    def test_sign_delivery_id_format(self) -> None:
        svc = _make_service()
        result = svc.sign("body")
        delivery_id = result["X-AgentApp-Delivery-ID"]
        assert delivery_id.startswith("fwd_")
        assert len(delivery_id) > len("fwd_")
