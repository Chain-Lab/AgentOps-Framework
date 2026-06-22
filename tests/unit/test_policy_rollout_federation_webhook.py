"""Tests for federation webhook domain models."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_app.governance.policy_rollout_federation_webhook import (
    FederationWebhookReplayResult,
    FederationWebhookRequestSnapshot,
    FederationWebhookSignatureResult,
)


def _ts() -> datetime:
    return datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)


# --- FederationWebhookRequestSnapshot model ---


class TestWebhookRequestSnapshot:
    def test_webhook_request_snapshot_valid(self):
        snap = FederationWebhookRequestSnapshot(
            request_id="fwr_abc123",
            notification_id="fn_001",
            url="https://example.com/webhook",
            body='{"event":"approval.created"}',
            timestamp=_ts(),
            nonce="nonce-xyz",
            payload_digest="sha256abc",
            created_at=_ts(),
        )
        assert snap.request_id == "fwr_abc123"
        assert snap.method == "POST"
        assert snap.content_type == "application/json"
        assert snap.signature_algorithm == "hmac-sha256"
        assert snap.signature_version == "v1"

    def test_request_id_prefix_valid(self):
        snap = FederationWebhookRequestSnapshot(
            request_id="fwr_valid",
            notification_id="fn_001",
            url="https://example.com",
            body="body",
            timestamp=_ts(),
            nonce="n",
            payload_digest="d",
            created_at=_ts(),
        )
        assert snap.request_id == "fwr_valid"

    def test_request_id_prefix_invalid(self):
        with pytest.raises(ValidationError, match="fwr_"):
            FederationWebhookRequestSnapshot(
                request_id="bad_id",
                notification_id="fn_001",
                url="https://example.com",
                body="body",
                timestamp=_ts(),
                nonce="n",
                payload_digest="d",
                created_at=_ts(),
            )

    def test_method_only_post_allowed(self):
        with pytest.raises(ValidationError, match="Only POST method is allowed"):
            FederationWebhookRequestSnapshot(
                request_id="fwr_test",
                notification_id="fn_001",
                url="https://example.com",
                method="GET",
                body="body",
                timestamp=_ts(),
                nonce="n",
                payload_digest="d",
                created_at=_ts(),
            )

    def test_method_post_case_insensitive(self):
        snap = FederationWebhookRequestSnapshot(
            request_id="fwr_test",
            notification_id="fn_001",
            url="https://example.com",
            method="post",
            body="body",
            timestamp=_ts(),
            nonce="n",
            payload_digest="d",
            created_at=_ts(),
        )
        assert snap.method == "POST"

    def test_snapshot_tz_aware_required(self):
        naive = datetime(2026, 6, 20, 12, 0, 0)
        with pytest.raises(ValidationError, match="timezone-aware"):
            FederationWebhookRequestSnapshot(
                request_id="fwr_test",
                notification_id="fn_001",
                url="https://example.com",
                body="body",
                timestamp=naive,
                nonce="n",
                payload_digest="d",
                created_at=_ts(),
            )


# --- FederationWebhookSignatureResult model ---


class TestSignatureResult:
    def test_signature_result_model(self):
        sr = FederationWebhookSignatureResult(
            valid=True,
            matched_key_id="key-1",
            signature_version="v1",
            timestamp_valid=True,
            nonce_valid=True,
        )
        assert sr.valid is True
        assert sr.reason is None
        assert sr.matched_key_id == "key-1"
        assert sr.nonce_valid is True

    def test_signature_result_failure(self):
        sr = FederationWebhookSignatureResult(
            valid=False,
            reason="Signature mismatch",
            timestamp_valid=True,
            nonce_valid=False,
        )
        assert sr.valid is False
        assert sr.reason == "Signature mismatch"
        assert sr.nonce_valid is False


# --- FederationWebhookReplayResult model ---


class TestReplayResult:
    def test_replay_result_model(self):
        rr = FederationWebhookReplayResult(
            replay_id="fwrp_001",
            dlq_id="fdlq_001",
            notification_id="fn_001",
            success=True,
            replay_count=1,
            last_replay_at=_ts(),
        )
        assert rr.replay_id == "fwrp_001"
        assert rr.success is True
        assert rr.error is None
        assert rr.replay_count == 1

    def test_replay_result_id_prefix_invalid(self):
        with pytest.raises(ValidationError, match="fwrp_"):
            FederationWebhookReplayResult(
                replay_id="bad_id",
                dlq_id="fdlq_001",
                notification_id="fn_001",
                success=False,
            )

    def test_replay_result_tz_aware(self):
        naive = datetime(2026, 6, 20, 12, 0, 0)
        with pytest.raises(ValidationError, match="timezone-aware"):
            FederationWebhookReplayResult(
                replay_id="fwrp_001",
                dlq_id="fdlq_001",
                notification_id="fn_001",
                success=True,
                last_replay_at=naive,
            )
