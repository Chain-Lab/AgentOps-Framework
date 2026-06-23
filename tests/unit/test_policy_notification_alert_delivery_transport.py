"""Tests for Phase 55 Task 3 — HTTP transport abstraction."""
from __future__ import annotations

import pytest

from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_adapters import (
    FakeHttpTransport,
    HttpTransportResult,
    UrllibHttpTransport,
    WebhookAlertDeliveryAdapter,
    _redact_headers,
)
from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryTarget,
)


def _make_target(endpoint: str = "https://hooks.example.com/alerts",
                 webhook_secret: str | None = "secret123") -> AlertDeliveryTarget:
    return AlertDeliveryTarget(
        target_id="ndt_001",
        name="Test Target",
        channel_type="webhook",
        endpoint=endpoint,
        webhook_secret=webhook_secret,
    )


# ---------------------------------------------------------------------------
# Transport tests
# ---------------------------------------------------------------------------


class TestFakeHttpTransport:
    def test_success_response(self):
        transport = FakeHttpTransport(responses=[
            HttpTransportResult(status_code=200, body_preview="OK"),
        ])
        result = transport.post_json("http://example.com", {}, {}, 10)
        assert result.status_code == 200
        assert result.body_preview == "OK"

    def test_http_400(self):
        transport = FakeHttpTransport(fail_always=True)
        result = transport.post_json("http://example.com", {}, {}, 10)
        assert result.status_code == 400
        assert result.error_code == "HTTP_400"

    def test_http_500(self):
        transport = FakeHttpTransport(fail_next=True)
        result = transport.post_json("http://example.com", {}, {}, 10)
        assert result.status_code == 500
        assert result.error_code == "HTTP_500"

    def test_records_calls(self):
        transport = FakeHttpTransport(fail_next=True)
        transport.post_json("http://example.com", {"key": "val"}, {"Authorization": "tok"}, 5, proxy_url="http://proxy")
        assert len(transport.calls) == 1
        call = transport.calls[0]
        assert call["url"] == "http://example.com"
        assert call["timeout_seconds"] == 5
        assert call["proxy_url"] == "http://proxy"
        assert call["payload"] == {"key": "val"}
        # Headers should be redacted
        assert call["headers"].get("Authorization") == "[REDACTED]"

    def test_timeout(self):
        transport = FakeHttpTransport(timeout=True)
        result = transport.post_json("http://example.com", {}, {}, 10)
        assert result.timed_out is True
        assert result.error_code == "TIMEOUT"

    def test_records_calls(self):
        transport = FakeHttpTransport(fail_next=True)
        transport.post_json("http://example.com", {"key": "val"}, {"Authorization": "tok"}, 5, proxy_url="http://proxy")
        assert len(transport.calls) == 1
        call = transport.calls[0]
        assert call["url"] == "http://example.com"
        assert call["timeout_seconds"] == 5
        assert call["proxy_url"] == "http://proxy"
        assert call["payload"] == {"key": "val"}
        # Headers should be redacted
        assert call["headers"].get("Authorization") == "[REDACTED]"

    def test_fail_next(self):
        transport = FakeHttpTransport(fail_next=True)
        r1 = transport.post_json("http://example.com", {}, {}, 10)
        assert r1.error_code == "HTTP_500"
        r2 = transport.post_json("http://example.com", {}, {}, 10)
        assert r2.status_code == 200  # Next call succeeds


# ---------------------------------------------------------------------------
# Adapter tests
# ---------------------------------------------------------------------------


class TestWebhookAdapterTransport:
    def test_dry_run_skips_transport(self):
        adapter = WebhookAlertDeliveryAdapter(dry_run=True)
        result = adapter.deliver(_make_target(), None, {"alert": "data"})
        assert result.success is True
        assert result.response_metadata.get("mode") == "dry_run"

    def test_no_endpoint_returns_error(self):
        adapter = WebhookAlertDeliveryAdapter(dry_run=False)
        target = _make_target(endpoint="")
        result = adapter.deliver(target, None, {"alert": "data"})
        assert result.success is False
        assert result.error_code == "NO_ENDPOINT"
        assert result.retryable is False

    def test_uses_fake_transport(self):
        fake = FakeHttpTransport(responses=[
            HttpTransportResult(status_code=200, body_preview="delivered"),
        ])
        adapter = WebhookAlertDeliveryAdapter(
            dry_run=False, transport=fake, timeout_seconds=5,
        )
        result = adapter.deliver(_make_target(), None, {"alert": "data"})
        assert result.success is True
        assert result.response_metadata.get("status_code") == 200
        assert len(fake.calls) == 1

    def test_transport_500_is_retryable(self):
        fake = FakeHttpTransport(fail_next=True)
        adapter = WebhookAlertDeliveryAdapter(dry_run=False, transport=fake)
        result = adapter.deliver(_make_target(), None, {"alert": "data"})
        assert result.success is False
        assert result.retryable is True

    def test_transport_timeout_is_retryable(self):
        fake = FakeHttpTransport(timeout=True)
        adapter = WebhookAlertDeliveryAdapter(dry_run=False, transport=fake)
        result = adapter.deliver(_make_target(), None, {"alert": "data"})
        assert result.success is False
        assert result.retryable is True

    def test_proxy_url_passed_to_transport(self):
        fake = FakeHttpTransport(fail_next=True)
        adapter = WebhookAlertDeliveryAdapter(
            dry_run=False, transport=fake, proxy_url="http://proxy:8080",
        )
        adapter.deliver(_make_target(), None, {"alert": "data"})
        assert fake.calls[0]["proxy_url"] == "http://proxy:8080"

    def test_user_agent_set_in_headers(self):
        fake = FakeHttpTransport(fail_next=True)
        adapter = WebhookAlertDeliveryAdapter(
            dry_run=False, transport=fake, user_agent="custom-agent/2.0",
        )
        adapter.deliver(_make_target(), None, {"alert": "data"})
        assert fake.calls[0]["headers"]["User-Agent"] == "custom-agent/2.0"

    def test_webhook_secret_adds_signature(self):
        fake = FakeHttpTransport(fail_next=True)
        adapter = WebhookAlertDeliveryAdapter(dry_run=False, transport=fake)
        adapter.deliver(_make_target(webhook_secret="s3cret"), None, {"alert": "data"})
        headers = fake.calls[0]["headers"]
        # X-Signature is present (redacted in recorded call but key exists)
        assert "X-Signature" in headers
        assert "X-Timestamp" in headers

    def test_body_preview_not_leaked_in_error(self):
        fake = FakeHttpTransport()
        adapter = WebhookAlertDeliveryAdapter(dry_run=False, transport=fake)
        result = adapter.deliver(_make_target(), None, {"secret": "value"})
        # Error path doesn't include raw payload in response_metadata
        assert "secret" not in (result.response_metadata.get("body_preview") or "")


class TestRedactHeaders:
    def test_sensitive_keys_redacted(self):
        headers = {
            "Authorization": "Bearer tok",
            "X-Signature": "v1=abc",
            "Content-Type": "application/json",
        }
        redacted = _redact_headers(headers)
        assert redacted["Authorization"] == "[REDACTED]"
        assert redacted["X-Signature"] == "[REDACTED]"
        assert redacted["Content-Type"] == "application/json"

    def test_case_insensitive(self):
        headers = {"X-SIGNATURE": "v1=abc", "x-secret": "shh"}
        redacted = _redact_headers(headers)
        assert redacted["X-SIGNATURE"] == "[REDACTED]"
        assert redacted["x-secret"] == "[REDACTED]"
