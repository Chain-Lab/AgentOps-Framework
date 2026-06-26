"""Tests for Phase 59 console pages — multi-instance production readiness."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

try:
    from starlette.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="FastAPI not installed")

from conftest import _run_async


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_idempotency_store() -> MagicMock:
    """Create a mock replay idempotency store with _records."""
    store = MagicMock()
    store._records = {
        "idem_001": MagicMock(
            idempotency_key="idem_001",
            original_attempt_id="att_001",
            replay_type="dlq_replay",
            status="completed",
            new_attempt_id="att_002",
            error_message=None,
            created_at=_now(),
            completed_at=_now(),
            expires_at=None,
        ),
        "idem_002": MagicMock(
            idempotency_key="idem_002",
            original_attempt_id="att_003",
            replay_type="dlq_replay",
            status="started",
            new_attempt_id=None,
            error_message=None,
            created_at=_now(),
            completed_at=None,
            expires_at=_now(),
        ),
    }
    return store


def _make_rate_limiter_store() -> MagicMock:
    """Create a mock replay rate limiter store with _records."""
    store = MagicMock()
    store._records = {
        "tgt_001": MagicMock(
            rate_limit_key="tgt_001",
            window_seconds=60,
            max_attempts=10,
            attempt_timestamps=[_now()],
            created_at=_now(),
            updated_at=_now(),
        ),
    }
    return store


def _make_dead_letter_store() -> MagicMock:
    """Create a mock dead letter policy store."""
    store = MagicMock()
    store.list_records = MagicMock(return_value=[
        MagicMock(
            attempt_id="att_dlq_001",
            alert_id="alert_001",
            target_id="tgt_001",
            reason="Max attempts exceeded",
            attempt_count=5,
            created_at=_now(),
        ),
    ])
    return store


def _make_enhanced_metrics() -> MagicMock:
    """Create a mock enhanced metrics service."""
    metrics = MagicMock()
    metrics.snapshot = MagicMock(return_value=MagicMock(
        replay=MagicMock(total_records=10),
        rate_limiter=MagicMock(total_keys=3),
        dead_letter=MagicMock(total_records=2),
        taken_at=_now(),
    ))
    return metrics


def _make_key_rotation_store() -> MagicMock:
    """Create a mock webhook key rotation store."""
    store = MagicMock()
    store.get_last_rotation = MagicMock(return_value=MagicMock(
        rotation_id="rot_001",
        actor_id="admin",
        reason="Scheduled rotation",
        created_at=_now(),
    ))
    store.list_rotations = MagicMock(return_value=[
        MagicMock(
            rotation_id="rot_001",
            actor_id="admin",
            reason="Scheduled rotation",
            created_at=_now(),
        ),
    ])
    return store


def _make_distributed_lock_store() -> MagicMock:
    """Create a mock distributed lock store."""
    store = MagicMock()
    store.get_status = MagicMock(return_value=MagicMock(
        lock_name="replay-lock",
        is_locked=True,
        owner_id="instance-1",
        acquired_at=_now(),
        expires_at=_now(),
        ttl_seconds=30,
    ))
    return store


def _client(
    idempotency_store=None,
    rate_limiter_store=None,
    dead_letter_store=None,
    enhanced_metrics=None,
    key_rotation_store=None,
    distributed_lock_store=None,
):
    """Create a TestClient with Phase 59 console router."""
    from fastapi import FastAPI
    from agent_app.console.router import build_policy_console_router
    from agent_app.config.schema import PolicyConsoleConfig

    app = FastAPI()
    router = build_policy_console_router(
        store=None,
        config=PolicyConsoleConfig(enabled=True),
        replay_idempotency_store=idempotency_store,
        replay_rate_limiter_store=rate_limiter_store,
        dead_letter_policy_store=dead_letter_store,
        enhanced_metrics=enhanced_metrics,
        webhook_key_rotation_store=key_rotation_store,
        distributed_lock_store=distributed_lock_store,
    )
    app.include_router(router, prefix="/policy-console")
    return TestClient(app)


class TestReplayIdempotencyPage:
    """Tests for the replay idempotency console page."""

    def test_page_renders(self):
        """GET /federation/notifications/replay-idempotency returns 200."""
        store = _make_idempotency_store()
        client = _client(idempotency_store=store)
        resp = client.get("/policy-console/federation/notifications/replay-idempotency")
        assert resp.status_code == 200
        assert "Replay Idempotency" in resp.text

    def test_page_shows_records(self):
        """Idempotency page shows records when store has data."""
        store = _make_idempotency_store()
        client = _client(idempotency_store=store)
        resp = client.get("/policy-console/federation/notifications/replay-idempotency")
        assert resp.status_code == 200
        assert "idem_001" in resp.text
        assert "idem_002" in resp.text

    def test_page_no_store(self):
        """GET without store shows empty state."""
        client = _client()
        resp = client.get("/policy-console/federation/notifications/replay-idempotency")
        assert resp.status_code == 200
        assert "not configured" in resp.text.lower() or "empty-state" in resp.text


class TestRateLimitPage:
    """Tests for the rate limiter console page."""

    def test_page_renders(self):
        """GET /federation/notifications/rate-limit returns 200."""
        store = _make_rate_limiter_store()
        client = _client(rate_limiter_store=store)
        resp = client.get("/policy-console/federation/notifications/rate-limit")
        assert resp.status_code == 200
        assert "Rate Limiter" in resp.text or "Rate limit" in resp.text

    def test_page_shows_records(self):
        """Rate limit page shows records when store has data."""
        store = _make_rate_limiter_store()
        client = _client(rate_limiter_store=store)
        resp = client.get("/policy-console/federation/notifications/rate-limit")
        assert resp.status_code == 200
        assert "tgt_001" in resp.text

    def test_page_no_store(self):
        """GET without store shows empty state."""
        client = _client()
        resp = client.get("/policy-console/federation/notifications/rate-limit")
        assert resp.status_code == 200


class TestDeadLetterPage:
    """Tests for the dead letter policy console page."""

    def test_page_renders(self):
        """GET /federation/notifications/dead-letter returns 200."""
        store = _make_dead_letter_store()
        client = _client(dead_letter_store=store)
        resp = client.get("/policy-console/federation/notifications/dead-letter")
        assert resp.status_code == 200
        assert "Dead Letter" in resp.text

    def test_page_shows_records(self):
        """Dead letter page shows records when store has data."""
        store = _make_dead_letter_store()
        client = _client(dead_letter_store=store)
        resp = client.get("/policy-console/federation/notifications/dead-letter")
        assert resp.status_code == 200
        assert "att_dlq_001" in resp.text

    def test_page_no_store(self):
        """GET without store shows empty state."""
        client = _client()
        resp = client.get("/policy-console/federation/notifications/dead-letter")
        assert resp.status_code == 200


class TestEnhancedMetricsPage:
    """Tests for the enhanced metrics console page."""

    def test_page_renders(self):
        """GET /federation/notifications/enhanced-metrics returns 200."""
        metrics = _make_enhanced_metrics()
        client = _client(enhanced_metrics=metrics)
        resp = client.get("/policy-console/federation/notifications/enhanced-metrics")
        assert resp.status_code == 200
        assert "Enhanced Metrics" in resp.text or "Metrics" in resp.text

    def test_page_shows_snapshot(self):
        """Enhanced metrics page shows snapshot data."""
        metrics = _make_enhanced_metrics()
        client = _client(enhanced_metrics=metrics)
        resp = client.get("/policy-console/federation/notifications/enhanced-metrics")
        assert resp.status_code == 200
        assert "10" in resp.text  # replay count

    def test_page_no_service(self):
        """GET without service shows empty state."""
        client = _client()
        resp = client.get("/policy-console/federation/notifications/enhanced-metrics")
        assert resp.status_code == 200


class TestKeyRotationPage:
    """Tests for the webhook key rotation console page."""

    def test_page_renders(self):
        """GET /federation/notifications/key-rotation returns 200."""
        store = _make_key_rotation_store()
        client = _client(key_rotation_store=store)
        resp = client.get("/policy-console/federation/notifications/key-rotation")
        assert resp.status_code == 200
        assert "Key Rotation" in resp.text or "Rotation" in resp.text

    def test_page_shows_history(self):
        """Key rotation page shows history."""
        store = _make_key_rotation_store()
        client = _client(key_rotation_store=store)
        resp = client.get("/policy-console/federation/notifications/key-rotation")
        assert resp.status_code == 200
        assert "rot_001" in resp.text

    def test_page_no_store(self):
        """GET without store shows empty state."""
        client = _client()
        resp = client.get("/policy-console/federation/notifications/key-rotation")
        assert resp.status_code == 200


class TestDistributedLockPage:
    """Tests for the distributed lock console page."""

    def test_page_renders(self):
        """GET /federation/notifications/distributed-lock/{lock}/status returns 200."""
        store = _make_distributed_lock_store()
        client = _client(distributed_lock_store=store)
        resp = client.get("/policy-console/federation/notifications/distributed-lock/replay-lock/status")
        assert resp.status_code == 200
        assert "Distributed Lock" in resp.text or "Lock" in resp.text

    def test_page_shows_status(self):
        """Distributed lock page shows lock status."""
        store = _make_distributed_lock_store()
        client = _client(distributed_lock_store=store)
        resp = client.get("/policy-console/federation/notifications/distributed-lock/replay-lock/status")
        assert resp.status_code == 200
        assert "replay-lock" in resp.text
        assert "instance-1" in resp.text

    def test_page_no_store(self):
        """GET without store shows empty state."""
        client = _client()
        resp = client.get("/policy-console/federation/notifications/distributed-lock/test-lock/status")
        assert resp.status_code == 200
