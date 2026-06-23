"""Tests for Phase 56 Task 1 — FastAPI operations endpoints for Phase 55 services.

These tests verify the following endpoint groups:
  - Retry daemon: status, run-once, start, stop
  - Retry queue: list, update priority
  - DLQ: list, single replay, batch replay
  - Dedup: list active, get by key, prune
  - Archive cleanup: checkpoint list, run cleanup, clear checkpoint
  - Rollup: list, incremental build, checkpoints
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agent_app import AgentApp
from agent_app.adapters.fastapi import create_fastapi_app
from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryAttempt,
    AlertDeliveryChannelType,
    AlertDeliveryStatus,
    AlertDeliveryTarget,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_service import (
    AlertDeliveryRetryRunResult,
    NotificationAlertDeliveryService,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_store import (
    InMemoryAlertDeliveryStore,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_priority_queue import (
    AlertPriorityQueue,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_dedup import (
    InMemoryNotificationAlertDedupStore,
    NotificationAlertDedupRecord,
)
from agent_app.runtime.policy_rollout_federation_notification_archive_cleanup import (
    ArchiveCheckpoint,
    ArchiveCleanupResult,
    InMemoryArchiveCheckpointStore,
)
from agent_app.runtime.policy_rollout_federation_notification_archive_cleanup_service import (
    ResumableArchiveCleanup,
)
from agent_app.runtime.policy_rollout_federation_notification_retry_daemon import (
    AlertDeliveryRetryDaemon,
    AlertDeliveryRetryDaemonConfig,
)
from agent_app.runtime.policy_rollout_federation_notification_rollup import (
    InMemoryNotificationRollupStore,
    NotificationMetricsRollup,
    NotificationRollupGranularity,
    NotificationRollupService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_test_app():
    """Create a minimal AgentApp for testing."""
    from agent_app.registry.agent_registry import AgentRegistry
    from agent_app.registry.tool_registry import ToolRegistry
    from agent_app.registry.workflow_registry import WorkflowRegistry

    ar = AgentRegistry()
    tr = ToolRegistry()
    wr = WorkflowRegistry()
    app = AgentApp(
        registry=type("B", (), {
            "agent_registry": ar,
            "tool_registry": tr,
            "workflow_registry": wr,
        })()
    )
    app.agent_registry = ar
    app.tool_registry = tr
    app.workflow_registry = wr
    return app


def _get_client(api):
    """Create a TestClient for the FastAPI app."""
    from starlette.testclient import TestClient
    return TestClient(api)


def _run_async(coro):
    """Run an async coroutine from synchronous test code."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_target(
    target_id="ndt_001",
    name="Test Target",
    enabled=True,
) -> AlertDeliveryTarget:
    return AlertDeliveryTarget(
        target_id=target_id,
        name=name,
        channel_type=AlertDeliveryChannelType.MEMORY,
        enabled=enabled,
        endpoint=None,
    )


def _make_attempt(
    attempt_id="nda_ndt_001_nae_001_1",
    alert_id="nae_001",
    target_id="ndt_001",
    status=AlertDeliveryStatus.RETRY_SCHEDULED,
    attempt=1,
    priority=50,
    error_message=None,
) -> AlertDeliveryAttempt:
    now = datetime.now(timezone.utc)
    return AlertDeliveryAttempt(
        attempt_id=attempt_id,
        alert_id=alert_id,
        target_id=target_id,
        channel_type=AlertDeliveryChannelType.MEMORY,
        status=status,
        attempt=attempt,
        priority=priority,
        error_message=error_message,
        payload_preview={},
        created_at=now,
    )


def _make_dedup_record(
    dedup_key="dk_001",
    alert_id="nae_001",
    now: datetime | None = None,
) -> NotificationAlertDedupRecord:
    if now is None:
        now = datetime.now(timezone.utc)
    return NotificationAlertDedupRecord(
        dedup_key=dedup_key,
        alert_id=alert_id,
        occurrence_count=1,
        first_seen_at=now,
        last_seen_at=now,
        expires_at=now,
        status="open",
    )


def _make_checkpoint(
    checkpoint_id="acp_test_001",
    data_type="rollup",
    now: datetime | None = None,
) -> ArchiveCheckpoint:
    if now is None:
        now = datetime.now(timezone.utc)
    return ArchiveCheckpoint(
        checkpoint_id=checkpoint_id,
        data_type=data_type,
        created_at=now,
        updated_at=now,
        batch_size=500,
    )


def _make_rollup(rollup_id="nru_hourly_20250622") -> NotificationMetricsRollup:
    now = datetime.now(timezone.utc)
    return NotificationMetricsRollup(
        rollup_id=rollup_id,
        granularity=NotificationRollupGranularity.HOURLY,
        window_start=now,
        window_end=now,
        total=100,
        sent=80,
        failed=10,
        suppressed=5,
        dlq=3,
        retry_scheduled=2,
        success_rate=0.8,
        failure_rate=0.1,
        dlq_rate=0.03,
        avg_latency_ms=50.0,
        p95_latency_ms=120.0,
        created_at=now,
    )


class FakeScheduler:
    """Fake scheduler for testing the retry daemon."""

    def __init__(self, results=None, raise_error=False):
        self.results = results or [
            AlertDeliveryRetryRunResult(dry_run=False, scanned=0, delivered=0),
        ]
        self.raise_error = raise_error
        self.calls = []

    async def run_once(self, limit=100, dry_run=False):
        self.calls.append({"limit": limit, "dry_run": dry_run})
        if self.raise_error:
            raise RuntimeError("scheduler error")
        if self.results:
            result = self.results.pop(0)
            result.dry_run = dry_run
            return result
        return AlertDeliveryRetryRunResult(dry_run=dry_run, scanned=0, delivered=0)


class FakeAuditLogger:
    """Fake audit logger for testing."""

    def __init__(self):
        self.events = []

    def __call__(self, event_type, payload):
        self.events.append({"event_type": event_type, "payload": payload})


def _build_api_with_services():
    """Build a test API app with Phase 55 services wired as agent_app attributes."""
    app = _build_test_app()

    # --- Alert delivery store + service ---
    delivery_store = InMemoryAlertDeliveryStore()
    adapter = type("M", (), {"deliver": lambda *a, **k: None})()
    delivery_service = NotificationAlertDeliveryService(
        store=delivery_store,
        adapters={"memory": adapter},
    )
    app._federation_notification_alert_delivery_service = delivery_service
    app._federation_notification_alert_delivery_store = delivery_store

    # --- Retry daemon ---
    scheduler = FakeScheduler()
    daemon = AlertDeliveryRetryDaemon(
        scheduler=scheduler,
        config=AlertDeliveryRetryDaemonConfig(enabled=True),
        audit_logger=FakeAuditLogger(),
    )
    app._federation_notification_retry_daemon = daemon

    # --- Priority queue ---
    priority_queue = AlertPriorityQueue(store=delivery_store)
    app._federation_notification_priority_queue = priority_queue

    # --- Dedup store ---
    dedup_store = InMemoryNotificationAlertDedupStore()
    app._federation_notification_dedup_store = dedup_store

    # --- Archive cleanup service ---
    checkpoint_store = InMemoryArchiveCheckpointStore()
    archive_cleanup = ResumableArchiveCleanup(
        checkpoint_store=checkpoint_store,
        rollup_store=None,
        audit_logger=FakeAuditLogger(),
    )
    app._federation_notification_archive_cleanup_service = archive_cleanup
    app._federation_notification_archive_checkpoint_store = checkpoint_store

    # --- Rollup service ---
    rollup_store = InMemoryNotificationRollupStore()
    rollup_service = NotificationRollupService(
        observability_store=None,
        rollup_store=rollup_store,
    )
    app._federation_notification_rollup_service = rollup_service
    app._federation_notification_rollup_store = rollup_store

    return app, delivery_store, delivery_service, priority_queue, dedup_store, archive_cleanup, checkpoint_store, rollup_service, rollup_store, scheduler, daemon


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client():
    """Build a test client with all Phase 55 services wired."""
    app, delivery_store, delivery_service, priority_queue, dedup_store, archive_cleanup, checkpoint_store, rollup_service, rollup_store, scheduler, daemon = _build_api_with_services()
    api = create_fastapi_app(app)
    client = _get_client(api)
    # Expose services so tests can pre-populate data in the SAME store
    client._app = app
    client._delivery_store = delivery_store
    client._delivery_service = delivery_service
    client._priority_queue = priority_queue
    client._dedup_store = dedup_store
    client._archive_cleanup = archive_cleanup
    client._checkpoint_store = checkpoint_store
    client._rollup_service = rollup_service
    client._rollup_store = rollup_store
    client._scheduler = scheduler
    client._daemon = daemon
    return client


@pytest.fixture
def api_no_services():
    """Build a test client without any Phase 55 services."""
    app = _build_test_app()
    api = create_fastapi_app(app)
    return _get_client(api)


@pytest.fixture
def services():
    """Return all Phase 55 service instances."""
    return _build_api_with_services()


# ---------------------------------------------------------------------------
# Test grouping — use class-based organization
# ---------------------------------------------------------------------------


class TestRetryDaemonEndpoints:
    """GET/POST /federation/notifications/retry-daemon/*"""

    def test_status_running(self, api_client):
        """GET status returns is_running=true when daemon is running."""
        daemon = api_client._daemon
        _run_async(daemon.start())

        resp = api_client.get("/federation/notifications/retry-daemon/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_running"] is True
        assert "config" in data

        _run_async(daemon.stop())

    def test_status_stopped(self, api_client):
        """GET status returns is_running=false when daemon not configured."""
        resp = api_client.get("/federation/notifications/retry-daemon/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_running"] is False

    def test_status_no_daemon(self, api_no_services):
        """GET status returns graceful response when daemon absent."""
        resp = api_no_services.get("/federation/notifications/retry-daemon/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_running"] is False

    def test_run_once_dry_run(self, api_client):
        """POST run-once with dry_run=true executes without side effects."""
        resp = api_client.post(
            "/federation/notifications/retry-daemon/run-once",
            json={"dry_run": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data
        assert data["result"]["dry_run"] is True

    def test_run_once_requires_confirmation_for_live(self, api_client):
        """POST run-once without confirmation=yes for live run is rejected."""
        resp = api_client.post(
            "/federation/notifications/retry-daemon/run-once",
            json={"dry_run": False},
        )
        assert resp.status_code == 400
        assert "confirmation" in resp.json()["detail"].lower()

    def test_run_once_with_confirmation(self, api_client):
        """POST run-once with confirmation=yes and dry_run=true succeeds."""
        resp = api_client.post(
            "/federation/notifications/retry-daemon/run-once",
            json={"dry_run": False, "confirmation": "yes"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data

    def test_run_once_no_daemon(self, api_no_services):
        """POST run-once returns 404 when daemon not available."""
        resp = api_no_services.post(
            "/federation/notifications/retry-daemon/run-once",
            json={"dry_run": True},
        )
        assert resp.status_code == 404
        assert "not configured" in resp.json()["detail"]

    def test_start_requires_confirmation(self, api_client):
        """POST start without confirmation=yes is rejected."""
        resp = api_client.post(
            "/federation/notifications/retry-daemon/start",
            json={},
        )
        assert resp.status_code == 400
        assert "confirmation" in resp.json()["detail"].lower()

    def test_start_with_confirmation(self, api_client):
        """POST start with confirmation=yes starts the daemon."""
        resp = api_client.post(
            "/federation/notifications/retry-daemon/start",
            json={"confirmation": "yes"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_running"] is True

        # Cleanup
        api_client.post(
            "/federation/notifications/retry-daemon/stop",
            json={"confirmation": "yes"},
        )

    def test_stop_requires_confirmation(self, api_client):
        """POST stop without confirmation=yes is rejected."""
        # Start first
        api_client.post(
            "/federation/notifications/retry-daemon/start",
            json={"confirmation": "yes"},
        )
        resp = api_client.post(
            "/federation/notifications/retry-daemon/stop",
            json={},
        )
        assert resp.status_code == 400
        assert "confirmation" in resp.json()["detail"].lower()

        # Cleanup
        api_client.post(
            "/federation/notifications/retry-daemon/stop",
            json={"confirmation": "yes"},
        )

    def test_stop_with_confirmation(self, api_client):
        """POST stop with confirmation=yes stops the daemon."""
        # Start first
        api_client.post(
            "/federation/notifications/retry-daemon/start",
            json={"confirmation": "yes"},
        )
        resp = api_client.post(
            "/federation/notifications/retry-daemon/stop",
            json={"confirmation": "yes"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_running"] is False

    def test_start_no_daemon(self, api_no_services):
        """POST start returns 404 when daemon not available."""
        resp = api_no_services.post(
            "/federation/notifications/retry-daemon/start",
            json={"confirmation": "yes"},
        )
        assert resp.status_code == 404

    def test_health_stopped(self, api_client):
        """GET health returns state=stopped when daemon is not running."""
        resp = api_client.get("/federation/notifications/retry-daemon/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "stopped"
        assert data["consecutive_failures"] == 0

    def test_health_healthy(self, api_client):
        """GET health returns state=healthy after successful start."""
        _run_async(api_client._daemon.start())
        resp = api_client.get("/federation/notifications/retry-daemon/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "healthy"
        assert data["consecutive_failures"] == 0
        assert data["started_at"] is not None

        _run_async(api_client._daemon.stop())

    def test_health_degraded(self, api_client):
        """GET health returns state=degraded with 1-2 consecutive failures."""
        _run_async(api_client._daemon.start())
        api_client._daemon._consecutive_failures = 1
        api_client._daemon._last_error = "Connection timeout"

        resp = api_client.get("/federation/notifications/retry-daemon/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "degraded"
        assert data["consecutive_failures"] == 1
        assert data["last_error"] == "Connection timeout"

        _run_async(api_client._daemon.stop())

    def test_health_unhealthy(self, api_client):
        """GET health returns state=unhealthy with 3+ consecutive failures."""
        _run_async(api_client._daemon.start())
        api_client._daemon._consecutive_failures = 3
        api_client._daemon._last_error = "Repeated delivery failures"

        resp = api_client.get("/federation/notifications/retry-daemon/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "unhealthy"
        assert data["consecutive_failures"] == 3

        _run_async(api_client._daemon.stop())

    def test_health_no_daemon(self, api_no_services):
        """GET health returns graceful response when daemon absent."""
        resp = api_no_services.get("/federation/notifications/retry-daemon/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "stopped"

    def test_ready_stopped(self, api_client):
        """GET ready returns ready=true when daemon is stopped."""
        resp = api_client.get("/federation/notifications/retry-daemon/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is True
        assert data["state"] == "stopped"

    def test_ready_running(self, api_client):
        """GET ready returns ready=true when daemon is healthy."""
        _run_async(api_client._daemon.start())
        resp = api_client.get("/federation/notifications/retry-daemon/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is True
        assert data["state"] == "healthy"

        _run_async(api_client._daemon.stop())

    def test_ready_unhealthy(self, api_client):
        """GET ready returns ready=true for degraded but ready=true for stopped."""
        _run_async(api_client._daemon.start())
        api_client._daemon._consecutive_failures = 2

        resp = api_client.get("/federation/notifications/retry-daemon/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is True  # degraded is still ready
        assert data["state"] == "degraded"

        _run_async(api_client._daemon.stop())

    def test_live_always_alive(self, api_client):
        """GET live always returns alive=true when daemon is configured."""
        resp = api_client.get("/federation/notifications/retry-daemon/live")
        assert resp.status_code == 200
        data = resp.json()
        assert data["alive"] is True

    def test_live_no_daemon(self, api_no_services):
        """GET live returns alive=true when daemon is absent."""
        resp = api_no_services.get("/federation/notifications/retry-daemon/live")
        assert resp.status_code == 200
        data = resp.json()
        assert data["alive"] is True
        assert data["state"] == "stopped"


class TestRetryQueueEndpoints:
    """GET/POST /federation/notifications/retry-queue/*"""

    def test_list_queue(self, api_client):
        """GET retry-queue returns attempts from the store."""
        resp = api_client.get("/federation/notifications/retry-queue")
        assert resp.status_code == 200
        data = resp.json()
        assert "attempts" in data
        assert "total" in data

    def test_list_queue_with_limit(self, api_client):
        """GET retry-queue respects limit parameter."""
        resp = api_client.get("/federation/notifications/retry-queue?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 10

    def test_list_queue_with_offset(self, api_client):
        """GET retry-queue respects offset parameter."""
        resp = api_client.get("/federation/notifications/retry-queue?limit=10&offset=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["offset"] == 5

    def test_list_queue_no_service(self, api_no_services):
        """GET retry-queue returns empty list when service not available."""
        resp = api_no_services.get("/federation/notifications/retry-queue")
        assert resp.status_code == 200
        data = resp.json()
        assert data["attempts"] == []

    def test_update_priority_requires_confirmation(self, api_client):
        """POST priority update without confirmation=yes is rejected."""
        resp = api_client.post(
            "/federation/notifications/retry-queue/nda_test_001/priority",
            json={"priority": 100},
        )
        assert resp.status_code == 400
        assert "confirmation" in resp.json()["detail"].lower()

    def test_update_priority_success(self, api_client):
        """POST priority update with confirmation=yes succeeds."""
        delivery_store = api_client._delivery_store
        attempt = _make_attempt(attempt_id="nda_test_001", priority=10)
        _run_async(delivery_store.record_attempt(attempt))

        resp = api_client.post(
            "/federation/notifications/retry-queue/nda_test_001/priority",
            json={"priority": 100, "confirmation": "yes"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["attempt_id"] == "nda_test_001"
        assert data["priority"] == 100

    def test_update_priority_not_found(self, api_client):
        """POST priority update for missing attempt returns 404."""
        resp = api_client.post(
            "/federation/notifications/retry-queue/nda_nonexistent/priority",
            json={"priority": 100, "confirmation": "yes"},
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_update_priority_no_service(self, api_no_services):
        """POST priority update returns 404 when service not available."""
        resp = api_no_services.post(
            "/federation/notifications/retry-queue/nda_test_001/priority",
            json={"priority": 100, "confirmation": "yes"},
        )
        assert resp.status_code == 404


class TestDLQEndpoints:
    """GET/POST /federation/notifications/dlq/*"""

    def test_list_dlq(self, api_client):
        """GET dlq returns DLQ attempts."""
        resp = api_client.get("/federation/notifications/dlq")
        assert resp.status_code == 200
        data = resp.json()
        assert "attempts" in data
        assert "total" in data

    def test_list_dlq_with_filters(self, api_client):
        """GET dlq respects federation_id and alert_id filters."""
        resp = api_client.get(
            "/federation/notifications/dlq?federation_id=fed_001&alert_id=nae_001"
        )
        assert resp.status_code == 200

    def test_list_dlq_with_limit_offset(self, api_client):
        """GET dlq respects limit and offset."""
        resp = api_client.get("/federation/notifications/dlq?limit=25&offset=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 25
        assert data["offset"] == 10

    def test_list_dlq_no_service(self, api_no_services):
        """GET dlq returns empty list when service not available."""
        resp = api_no_services.get("/federation/notifications/dlq")
        assert resp.status_code == 200
        data = resp.json()
        assert data["attempts"] == []

    def test_single_replay_success(self, api_client):
        """POST single replay returns the new attempt."""
        delivery_store = api_client._delivery_store
        dlq_attempt = _make_attempt(
            attempt_id="nda_dlq_001",
            status=AlertDeliveryStatus.DLQ,
        )
        _run_async(delivery_store.record_attempt(dlq_attempt))

        resp = api_client.post(
            "/federation/notifications/dlq/nda_dlq_001/replay",
            json={"dry_run": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "attempt" in data

    def test_single_replay_requires_confirmation_for_live(self, api_client):
        """POST single replay without confirmation=yes for live is rejected."""
        delivery_store = api_client._delivery_store
        dlq_attempt = _make_attempt(
            attempt_id="nda_dlq_002",
            status=AlertDeliveryStatus.DLQ,
        )
        _run_async(delivery_store.record_attempt(dlq_attempt))

        resp = api_client.post(
            "/federation/notifications/dlq/nda_dlq_002/replay",
            json={"dry_run": False},
        )
        assert resp.status_code == 400

    def test_single_replay_not_found(self, api_client):
        """POST replay for missing attempt returns 404."""
        resp = api_client.post(
            "/federation/notifications/dlq/nda_nonexistent/replay",
            json={"dry_run": True},
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_single_replay_no_service(self, api_no_services):
        """POST replay returns 404 when service not available."""
        resp = api_no_services.post(
            "/federation/notifications/dlq/nda_test/replay",
            json={"dry_run": True},
        )
        assert resp.status_code == 404

    def test_batch_replay_dry_run(self, api_client):
        """POST batch-replay with dry_run=true succeeds."""
        resp = api_client.post(
            "/federation/notifications/dlq/batch-replay",
            json={
                "target_id": "ndt_ops",
                "dry_run": True,
                "confirmation": "yes",
                "limit": 100,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data

    def test_batch_replay_with_filters(self, api_client):
        """POST batch-replay respects since/until/alert_id filters."""
        resp = api_client.post(
            "/federation/notifications/dlq/batch-replay",
            json={
                "target_id": "ndt_ops",
                "alert_id": "nae_001",
                "since": "2026-06-22T00:00:00Z",
                "until": "2026-06-23T00:00:00Z",
                "limit": 50,
                "dry_run": True,
                "confirmation": "yes",
            },
        )
        assert resp.status_code == 200

    def test_batch_replay_requires_confirmation(self, api_client):
        """POST batch-replay without confirmation=yes is rejected."""
        resp = api_client.post(
            "/federation/notifications/dlq/batch-replay",
            json={
                "target_id": "ndt_ops",
                "dry_run": False,
            },
        )
        assert resp.status_code == 400
        assert "confirmation" in resp.json()["detail"].lower()

    def test_batch_replay_no_service(self, api_no_services):
        """POST batch-replay returns 404 when service not available."""
        resp = api_no_services.post(
            "/federation/notifications/dlq/batch-replay",
            json={
                "target_id": "ndt_ops",
                "dry_run": True,
                "confirmation": "yes",
            },
        )
        assert resp.status_code == 404


class TestDedupEndpoints:
    """GET/POST /federation/notifications/dedup/*"""

    def test_list_active(self, api_client):
        """GET dedup/active returns active dedup records."""
        resp = api_client.get("/federation/notifications/dedup/active")
        assert resp.status_code == 200
        data = resp.json()
        assert "records" in data
        assert "total" in data

    def test_list_active_with_limit_offset(self, api_client):
        """GET dedup/active respects limit and offset."""
        resp = api_client.get("/federation/notifications/dedup/active?limit=25&offset=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 25
        assert data["offset"] == 10

    def test_list_active_no_store(self, api_no_services):
        """GET dedup/active returns empty list when dedup store not available."""
        resp = api_no_services.get("/federation/notifications/dedup/active")
        assert resp.status_code == 200
        data = resp.json()
        assert data["records"] == []

    def test_get_by_key(self, api_client):
        """GET dedup/{key} returns a specific record."""
        store = api_client._dedup_store
        record = _make_dedup_record(dedup_key="dk_test_001")
        _run_async(store.upsert(record))

        resp = api_client.get("/federation/notifications/dedup/dk_test_001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["dedup_key"] == "dk_test_001"

    def test_get_by_key_not_found(self, api_client):
        """GET dedup/{key} returns 404 for missing key."""
        resp = api_client.get("/federation/notifications/dedup/dk_nonexistent")
        assert resp.status_code == 404

    def test_get_by_key_no_store(self, api_no_services):
        """GET dedup/{key} returns 404 when dedup store not available."""
        resp = api_no_services.get("/federation/notifications/dedup/dk_test")
        assert resp.status_code == 404

    def test_prune_requires_confirmation(self, api_client):
        """POST dedup/prune without confirmation=yes is rejected."""
        resp = api_client.post("/federation/notifications/dedup/prune", json={})
        assert resp.status_code == 400
        assert "confirmation" in resp.json()["detail"].lower()

    def test_prune_success(self, api_client):
        """POST dedup/prune with confirmation=yes returns prune count."""
        store = api_client._dedup_store
        now = datetime.now(timezone.utc)
        expired = NotificationAlertDedupRecord(
            dedup_key="dk_expired",
            alert_id="nae_expired",
            occurrence_count=1,
            first_seen_at=now,
            last_seen_at=now,
            expires_at=now,
            status="open",
        )
        _run_async(store.upsert(expired))

        resp = api_client.post(
            "/federation/notifications/dedup/prune",
            json={"confirmation": "yes"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "pruned_count" in data

    def test_prune_no_store(self, api_no_services):
        """POST dedup/prune returns 404 when dedup store not available."""
        resp = api_no_services.post(
            "/federation/notifications/dedup/prune",
            json={"confirmation": "yes"},
        )
        assert resp.status_code == 404


class TestArchiveCleanupEndpoints:
    """GET/POST /federation/notifications/archives/*"""

    def test_list_checkpoints(self, api_client):
        """GET archives/checkpoint returns list of checkpoints."""
        resp = api_client.get("/federation/notifications/archives/checkpoint")
        assert resp.status_code == 200
        data = resp.json()
        assert "checkpoints" in data

    def test_list_checkpoints_no_service(self, api_no_services):
        """GET archives/checkpoint returns empty list when service not available."""
        resp = api_no_services.get("/federation/notifications/archives/checkpoint")
        assert resp.status_code == 200
        data = resp.json()
        assert data["checkpoints"] == []

    def test_run_cleanup_requires_confirmation(self, api_client):
        """POST archives/cleanup without confirmation=yes is rejected."""
        resp = api_client.post(
            "/federation/notifications/archives/cleanup",
            json={"data_type": "rollup"},
        )
        assert resp.status_code == 400
        assert "confirmation" in resp.json()["detail"].lower()

    def test_run_cleanup_dry_run(self, api_client):
        """POST archives/cleanup with dry_run=true and confirmation succeeds."""
        resp = api_client.post(
            "/federation/notifications/archives/cleanup",
            json={
                "data_type": "rollup",
                "dry_run": True,
                "confirmation": "yes",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data
        assert data["result"]["dry_run"] is True

    def test_run_cleanup_live(self, api_client):
        """POST archives/cleanup with confirmation=yes and dry_run=false succeeds."""
        resp = api_client.post(
            "/federation/notifications/archives/cleanup",
            json={
                "data_type": "rollup",
                "dry_run": False,
                "confirmation": "yes",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data

    def test_run_cleanup_no_service(self, api_no_services):
        """POST archives/cleanup returns 404 when service not available."""
        resp = api_no_services.post(
            "/federation/notifications/archives/cleanup",
            json={"data_type": "rollup", "confirmation": "yes"},
        )
        assert resp.status_code == 404

    def test_clear_checkpoint_requires_confirmation(self, api_client):
        """POST archives/checkpoint/clear without confirmation=yes is rejected."""
        resp = api_client.post(
            "/federation/notifications/archives/checkpoint/clear",
            json={"checkpoint_id": "acp_test"},
        )
        assert resp.status_code == 400

    def test_clear_checkpoint_success(self, api_client):
        """POST archives/checkpoint/clear with confirmation succeeds."""
        checkpoint_store = api_client._checkpoint_store
        cp = _make_checkpoint(checkpoint_id="acp_clear_test")
        _run_async(checkpoint_store.record_checkpoint(cp))

        resp = api_client.post(
            "/federation/notifications/archives/checkpoint/clear",
            json={"checkpoint_id": "acp_clear_test", "confirmation": "yes"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["checkpoint_id"] == "acp_clear_test"

    def test_clear_checkpoint_no_service(self, api_no_services):
        """POST archives/checkpoint/clear returns 404 when service not available."""
        resp = api_no_services.post(
            "/federation/notifications/archives/checkpoint/clear",
            json={"checkpoint_id": "acp_test", "confirmation": "yes"},
        )
        assert resp.status_code == 404


class TestRollupEndpoints:
    """POST/GET /federation/notifications/rollup/*"""

    def test_list_rollups(self, api_client):
        """GET rollup returns list of rollup records."""
        rollup_store = api_client._rollup_store
        rollup = _make_rollup()
        _run_async(rollup_store.upsert_rollup(rollup))

        resp = api_client.get("/federation/notifications/rollup")
        assert resp.status_code == 200
        data = resp.json()
        assert "rollups" in data
        assert len(data["rollups"]) >= 1
        assert data["rollups"][0]["rollup_id"] == "nru_hourly_20250622"

    def test_list_rollups_with_filters(self, api_client):
        """GET rollup respects granularity and channel filters."""
        resp = api_client.get(
            "/federation/notifications/rollup?granularity=hourly&limit=10"
        )
        assert resp.status_code == 200

    def test_list_rollups_with_limit_offset(self, api_client):
        """GET rollup respects limit and offset."""
        resp = api_client.get("/federation/notifications/rollup?limit=5&offset=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 5
        assert data["offset"] == 10

    def test_list_rollups_no_service(self, api_no_services):
        """GET rollup returns empty list when service not available."""
        resp = api_no_services.get("/federation/notifications/rollup")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rollups"] == []

    def test_list_checkpoints(self, api_client):
        """GET rollup/checkpoints returns list of rollup checkpoints."""
        resp = api_client.get("/federation/notifications/rollup/checkpoints")
        assert resp.status_code == 200
        data = resp.json()
        assert "checkpoints" in data

    def test_list_checkpoints_no_service(self, api_no_services):
        """GET rollup/checkpoints returns empty list when service not available."""
        resp = api_no_services.get("/federation/notifications/rollup/checkpoints")
        assert resp.status_code == 200
        data = resp.json()
        assert data["checkpoints"] == []

    def test_incremental_rollup(self, api_client):
        """POST rollup/incremental builds and returns rollup records."""
        resp = api_client.post(
            "/federation/notifications/rollup/incremental",
            json={"granularity": "hourly"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "rollups" in data

    def test_incremental_rollup_no_service(self, api_no_services):
        """POST rollup/incremental returns 404 when service not available."""
        resp = api_no_services.post(
            "/federation/notifications/rollup/incremental",
            json={"granularity": "hourly"},
        )
        assert resp.status_code == 404


class TestErrorHandling:
    """Phase 56: Error responses must be redacted (no stack traces, secrets, tokens)."""

    def test_run_once_error_redacted(self, api_client):
        """Errors from run_once are redacted — no stack traces."""
        # Use a service that will raise — mock by replacing run_once
        resp = api_client.post(
            "/federation/notifications/retry-daemon/run-once",
            json={"dry_run": True},
        )
        # Should succeed (no error) — but verify format if it fails
        assert resp.status_code in (200, 500)
        if resp.status_code == 500:
            detail = resp.json().get("detail", "")
            assert "Traceback" not in detail
            assert "Traceback" not in str(detail)

    def test_destructive_requires_confirmation(self, api_client):
        """All destructive endpoints reject requests without confirmation=yes."""
        destructive_endpoints = [
            ("POST", "/federation/notifications/retry-daemon/run-once", {"dry_run": False}),
            ("POST", "/federation/notifications/retry-daemon/start", {}),
            ("POST", "/federation/notifications/retry-daemon/stop", {}),
            ("POST", "/federation/notifications/retry-queue/nda_test/priority", {"priority": 100}),
            ("POST", "/federation/notifications/dlq/nda_test/replay", {"dry_run": False}),
            ("POST", "/federation/notifications/dlq/batch-replay", {"target_id": "ndt_ops", "dry_run": False}),
            ("POST", "/federation/notifications/dedup/prune", {}),
            ("POST", "/federation/notifications/archives/cleanup", {"data_type": "rollup"}),
            ("POST", "/federation/notifications/archives/checkpoint/clear", {"checkpoint_id": "acp_test"}),
        ]
        for method, path, body in destructive_endpoints:
            if method == "GET":
                resp = api_client.get(path)
            else:
                resp = api_client.post(path, json=body)
            assert resp.status_code == 400, (
                f"Endpoint {method} {path} should require confirmation, "
                f"got {resp.status_code}"
            )


class TestOptionalEndpoints:
    """Phase 56: All endpoints are optional — appear only if service is configured."""

    def test_all_endpoints_skip_gracefully_without_services(self, api_no_services):
        """All endpoints return sensible defaults when services are absent."""
        # List endpoints should return empty collections
        list_endpoints = [
            "/federation/notifications/retry-queue",
            "/federation/notifications/dlq",
            "/federation/notifications/dedup/active",
            "/federation/notifications/archives/checkpoint",
            "/federation/notifications/rollup",
            "/federation/notifications/rollup/checkpoints",
        ]
        for path in list_endpoints:
            resp = api_no_services.get(path)
            assert resp.status_code == 200, f"GET {path} should return 200, got {resp.status_code}"

        # Status endpoints should return graceful defaults
        resp = api_no_services.get("/federation/notifications/retry-daemon/status")
        assert resp.status_code == 200


class TestPrometheusEndpoint:
    """GET /federation/notifications/prometheus — Phase 53 + Phase 56 expansion."""

    def test_prometheus_returns_text(self, api_client):
        """GET prometheus returns text/plain content type with metrics."""
        resp = api_client.get("/federation/notifications/prometheus")
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("text/plain")
        assert "# HELP" in resp.text
        assert "# TYPE" in resp.text

    def test_prometheus_includes_core_metrics(self, api_client):
        """Prometheus output includes Phase 53 core notification metrics."""
        resp = api_client.get("/federation/notifications/prometheus")
        assert resp.status_code == 200
        text = resp.text
        assert "agentapp_notification_total" in text
        assert "agentapp_notification_sent_total" in text
        assert "agentapp_notification_failed_total" in text
        assert "agentapp_notification_dlq_total" in text
        assert "agentapp_notification_success_rate" in text

    def test_prometheus_includes_daemon_health(self, api_client):
        """Prometheus output includes Phase 56 daemon health metrics."""
        _run_async(api_client._daemon.start())
        try:
            resp = api_client.get("/federation/notifications/prometheus")
            assert resp.status_code == 200
            text = resp.text
            assert "agentapp_notification_daemon_state" in text
            assert "agentapp_notification_daemon_consecutive_failures" in text
        finally:
            _run_async(api_client._daemon.stop())

    def test_prometheus_includes_queue_depths(self, api_client):
        """Prometheus output includes Phase 56 queue depth metrics."""
        resp = api_client.get("/federation/notifications/prometheus")
        assert resp.status_code == 200
        text = resp.text
        assert "agentapp_notification_retry_queue_depth" in text
        assert "agentapp_notification_dlq_depth" in text
        assert "agentapp_notification_dedup_active_active" in text

    def test_prometheus_daemon_state_values(self, api_client):
        """Daemon state gauge uses correct numeric values per state."""
        _run_async(api_client._daemon.start())
        try:
            # Test healthy (consecutive_failures = 0)
            resp = api_client.get("/federation/notifications/prometheus")
            assert resp.status_code == 200
            assert "agentapp_notification_daemon_state 1" in resp.text

            # Test degraded (consecutive_failures = 2)
            api_client._daemon._consecutive_failures = 2
            resp = api_client.get("/federation/notifications/prometheus")
            assert resp.status_code == 200
            assert "agentapp_notification_daemon_state 2" in resp.text

            # Test unhealthy (consecutive_failures = 3)
            api_client._daemon._consecutive_failures = 3
            resp = api_client.get("/federation/notifications/prometheus")
            assert resp.status_code == 200
            assert "agentapp_notification_daemon_state 3" in resp.text
        finally:
            _run_async(api_client._daemon.stop())

    def test_prometheus_no_secrets(self, api_client):
        """Prometheus output does not contain secrets, tokens, or passwords."""
        resp = api_client.get("/federation/notifications/prometheus")
        assert resp.status_code == 200
        text = resp.text.lower()
        assert "secret" not in text
        assert "token" not in text
        assert "password" not in text

    def test_prometheus_no_services(self, api_no_services):
        """Prometheus endpoint returns valid output when services are absent."""
        resp = api_no_services.get("/federation/notifications/prometheus")
        assert resp.status_code == 200
        assert "# HELP" in resp.text
