"""Tests for AlertDeliveryRetryDaemon with priority queue store integration.

Phase 56 Task 730: SQLite Priority Queue Store — daemon integration.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryChannelType,
    AlertDeliveryStatus,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_service import (
    NotificationAlertDeliveryService,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_priority_queue_store import (
    AlertPriorityQueueItem,
    InMemoryAlertPriorityQueueStore,
    SQLiteAlertPriorityQueueStore,
)
from agent_app.runtime.policy_rollout_federation_notification_retry_daemon import (
    AlertDeliveryRetryDaemon,
    AlertDeliveryRetryDaemonConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_daemon_config(**kwargs) -> AlertDeliveryRetryDaemonConfig:
    defaults = dict(
        enabled=True,
        interval_seconds=60.0,
        jitter_seconds=0.0,
        batch_limit=100,
        stop_on_error=False,
        run_immediately=False,
    )
    defaults.update(kwargs)
    return AlertDeliveryRetryDaemonConfig(**defaults)


# ---------------------------------------------------------------------------
# Daemon + Priority Queue Store integration tests
# ---------------------------------------------------------------------------


class TestRetryDaemonPriorityQueueIntegration:
    """Tests for daemon integration with AlertPriorityQueueStore."""

    def test_daemon_accepts_priority_queue_store(self) -> None:
        """Daemon accepts an optional priority_queue_store parameter."""
        pq_store = InMemoryAlertPriorityQueueStore()
        daemon = AlertDeliveryRetryDaemon(
            scheduler=None,  # type: ignore[arg-type]
            config=_make_daemon_config(),
            priority_queue_store=pq_store,
        )
        assert daemon._priority_queue_store is pq_store

    def test_daemon_without_priority_queue_store(self) -> None:
        """Daemon works without priority_queue_store (backward compat)."""
        daemon = AlertDeliveryRetryDaemon(
            scheduler=None,  # type: ignore[arg-type]
            config=_make_daemon_config(),
        )
        assert daemon._priority_queue_store is None

    def test_daemon_with_sqlite_priority_queue_store(self, tmp_path) -> None:
        """Daemon accepts SQLiteAlertPriorityQueueStore."""
        pq_store = SQLiteAlertPriorityQueueStore(str(tmp_path / "test.db"))
        daemon = AlertDeliveryRetryDaemon(
            scheduler=None,  # type: ignore[arg-type]
            config=_make_daemon_config(),
            priority_queue_store=pq_store,
        )
        assert daemon._priority_queue_store is pq_store
        pq_store.close()

    def test_daemon_health_status_includes_queue_info(self) -> None:
        """Daemon health status works with priority queue store."""
        pq_store = InMemoryAlertPriorityQueueStore()
        daemon = AlertDeliveryRetryDaemon(
            scheduler=None,  # type: ignore[arg-type]
            config=_make_daemon_config(),
            priority_queue_store=pq_store,
        )

        # Populate queue
        async def _populate() -> None:
            await pq_store.enqueue(
                AlertPriorityQueueItem(
                    attempt_id="nda_t1_a1_1",
                    alert_id="a1",
                    target_id="t1",
                    channel_type=AlertDeliveryChannelType.WEBHOOK,
                    status=AlertDeliveryStatus.RETRY_SCHEDULED,
                    priority=50,
                    created_at=datetime.now(timezone.utc),
                    available_at=datetime.now(timezone.utc),
                )
            )

        asyncio.run(_populate())

        # Verify daemon stores the reference
        assert daemon._priority_queue_store is pq_store

    def test_priority_queue_store_count_integration(self) -> None:
        """Daemon can query priority queue store count."""
        pq_store = InMemoryAlertPriorityQueueStore()

        async def _populate() -> None:
            for i in range(3):
                await pq_store.enqueue(
                    AlertPriorityQueueItem(
                        attempt_id=f"nda_t1_a{i}_1",
                        alert_id=f"a{i}",
                        target_id="t1",
                        channel_type=AlertDeliveryChannelType.WEBHOOK,
                        status=AlertDeliveryStatus.RETRY_SCHEDULED,
                        priority=i * 25,
                        created_at=datetime.now(timezone.utc),
                        available_at=datetime.now(timezone.utc),
                    )
                )

        asyncio.run(_populate())

        daemon = AlertDeliveryRetryDaemon(
            scheduler=None,  # type: ignore[arg-type]
            config=_make_daemon_config(),
            priority_queue_store=pq_store,
        )

        assert asyncio.run(pq_store.count()) == 3
        counts = asyncio.run(pq_store.count_by_priority())
        assert counts == {0: 1, 25: 1, 50: 1}
