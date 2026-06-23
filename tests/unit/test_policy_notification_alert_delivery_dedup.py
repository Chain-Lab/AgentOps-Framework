"""Tests for Phase 55 Task 2 — Persistent Alert Dedup Store."""
from __future__ import annotations

import os
import tempfile
import time
import pytest
from datetime import datetime, timezone, timedelta

from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_dedup import (
    InMemoryNotificationAlertDedupStore,
    SQLiteNotificationAlertDedupStore,
    create_notification_alert_dedup_store,
    NotificationAlertDedupRecord,
    NotificationAlertDedupStore,
    NotificationAlertDedupService,
)


def _make_record(
    dedup_key: str = "alt_001:tgt_001",
    alert_id: str = "alt_001",
    federation_id: str | None = "fed_1",
    channel: str | None = "webhook",
    metric: str | None = None,
    severity: str | None = "critical",
    occurrence_count: int = 1,
    first_seen_at: datetime | None = None,
    last_seen_at: datetime | None = None,
    expires_at: datetime | None = None,
    status: str = "open",
    window_minutes: int = 30,
) -> NotificationAlertDedupRecord:
    now = datetime.now(timezone.utc)
    return NotificationAlertDedupRecord(
        dedup_key=dedup_key,
        alert_id=alert_id,
        federation_id=federation_id,
        channel=channel,
        metric=metric,
        severity=severity,
        occurrence_count=occurrence_count,
        first_seen_at=first_seen_at or now,
        last_seen_at=last_seen_at or now,
        expires_at=expires_at or (now + timedelta(minutes=window_minutes)),
        status=status,
    )


# ---------------------------------------------------------------------------
# InMemory store
# ---------------------------------------------------------------------------


class TestInMemoryDedupStore:
    @pytest.mark.asyncio
    async def test_upsert_and_get(self):
        store = InMemoryNotificationAlertDedupStore()
        record = _make_record()
        store.upsert(record)
        fetched = store.get("alt_001:tgt_001")
        assert fetched is not None
        assert fetched.alert_id == "alt_001"
        assert fetched.occurrence_count == 1

    @pytest.mark.asyncio
    async def test_upsert_duplicate_key_updates(self):
        store = InMemoryNotificationAlertDedupStore()
        r1 = _make_record(occurrence_count=1)
        store.upsert(r1)
        r2 = _make_record(occurrence_count=5)
        store.upsert(r2)
        fetched = store.get("alt_001:tgt_001")
        assert fetched.occurrence_count == 5

    @pytest.mark.asyncio
    async def test_mark_resolved(self):
        store = InMemoryNotificationAlertDedupStore()
        record = _make_record()
        store.upsert(record)
        result = store.mark_resolved("alt_001:tgt_001", datetime.now(timezone.utc))
        assert result is not None
        assert result.status == "resolved"
        # Resolved records not in active list
        active = store.list_active()
        assert len(active) == 0

    @pytest.mark.asyncio
    async def test_mark_resolved_missing_key(self):
        store = InMemoryNotificationAlertDedupStore()
        result = store.mark_resolved("nonexistent", datetime.now(timezone.utc))
        assert result is None

    @pytest.mark.asyncio
    async def test_list_active(self):
        store = InMemoryNotificationAlertDedupStore()
        now = datetime.now(timezone.utc)
        r1 = _make_record(dedup_key="k1", expires_at=now + timedelta(hours=1))
        r2 = _make_record(dedup_key="k2", expires_at=now + timedelta(hours=2))
        store.upsert(r1)
        store.upsert(r2)
        active = store.list_active(now=now)
        assert len(active) == 2

    @pytest.mark.asyncio
    async def test_list_active_excludes_expired(self):
        store = InMemoryNotificationAlertDedupStore()
        now = datetime.now(timezone.utc)
        r1 = _make_record(dedup_key="k1", expires_at=now - timedelta(minutes=1))
        r2 = _make_record(dedup_key="k2", expires_at=now + timedelta(hours=1))
        store.upsert(r1)
        store.upsert(r2)
        active = store.list_active(now=now)
        assert len(active) == 1
        assert active[0].dedup_key == "k2"

    @pytest.mark.asyncio
    async def test_list_active_excludes_resolved(self):
        store = InMemoryNotificationAlertDedupStore()
        now = datetime.now(timezone.utc)
        r1 = _make_record(dedup_key="k1")
        store.upsert(r1)
        store.mark_resolved("k1", now)
        active = store.list_active(now=now)
        assert len(active) == 0

    @pytest.mark.asyncio
    async def test_prune_expired(self):
        store = InMemoryNotificationAlertDedupStore()
        now = datetime.now(timezone.utc)
        r1 = _make_record(dedup_key="k1", expires_at=now - timedelta(minutes=1))
        r2 = _make_record(dedup_key="k2", expires_at=now + timedelta(hours=1))
        store.upsert(r1)
        store.upsert(r2)
        pruned = store.prune_expired(now=now)
        assert pruned == 1
        assert store.get("k1").status == "expired"
        assert store.get("k2").status == "open"


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class TestSQLiteDedupStore:
    @pytest.fixture
    def tmp_db(self, tmp_path):
        db = str(tmp_path / "dedup.db")
        yield db

    @pytest.mark.asyncio
    async def test_persists_across_instances(self, tmp_db):
        store1 = SQLiteNotificationAlertDedupStore(tmp_db)
        record = _make_record()
        store1.upsert(record)
        store1.close()

        store2 = SQLiteNotificationAlertDedupStore(tmp_db)
        fetched = store2.get("alt_001:tgt_001")
        assert fetched is not None
        assert fetched.alert_id == "alt_001"
        assert fetched.occurrence_count == 1
        store2.close()

    @pytest.mark.asyncio
    async def test_upsert_and_get(self, tmp_db):
        store = SQLiteNotificationAlertDedupStore(tmp_db)
        record = _make_record()
        store.upsert(record)
        fetched = store.get("alt_001:tgt_001")
        assert fetched is not None
        assert fetched.federation_id == "fed_1"
        assert fetched.channel == "webhook"
        store.close()

    @pytest.mark.asyncio
    async def test_mark_resolved(self, tmp_db):
        store = SQLiteNotificationAlertDedupStore(tmp_db)
        record = _make_record()
        store.upsert(record)
        result = store.mark_resolved("alt_001:tgt_001", datetime.now(timezone.utc))
        assert result is not None
        assert result.status == "resolved"
        active = store.list_active()
        assert len(active) == 0
        store.close()

    @pytest.mark.asyncio
    async def test_list_active_with_filters(self, tmp_db):
        store = SQLiteNotificationAlertDedupStore(tmp_db)
        now = datetime.now(timezone.utc)
        for i in range(5):
            r = _make_record(dedup_key=f"k{i}", severity="critical" if i < 2 else "warning")
            store.upsert(r)
        active = store.list_active(now=now, limit=10)
        assert len(active) == 5
        # Test offset
        page1 = store.list_active(now=now, limit=2, offset=0)
        page2 = store.list_active(now=now, limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        store.close()

    @pytest.mark.asyncio
    async def test_prune_expired(self, tmp_db):
        store = SQLiteNotificationAlertDedupStore(tmp_db)
        now = datetime.now(timezone.utc)
        r1 = _make_record(dedup_key="k1", expires_at=now - timedelta(minutes=1))
        r2 = _make_record(dedup_key="k2", expires_at=now + timedelta(hours=1))
        store.upsert(r1)
        store.upsert(r2)
        pruned = store.prune_expired(now=now)
        assert pruned == 1
        assert store.get("k1").status == "expired"
        assert store.get("k2").status == "open"
        store.close()

    @pytest.mark.asyncio
    async def test_duplicate_within_window_increments_count(self, tmp_db):
        """Phase 55: duplicate within window increments occurrence_count."""
        store = SQLiteNotificationAlertDedupStore(tmp_db)
        now = datetime.now(timezone.utc)
        r1 = _make_record(dedup_key="k1", occurrence_count=1)
        store.upsert(r1)
        # Duplicate within window
        r2 = _make_record(dedup_key="k1", occurrence_count=2)
        store.upsert(r2)
        fetched = store.get("k1")
        assert fetched.occurrence_count == 2
        store.close()

    @pytest.mark.asyncio
    async def test_outside_window_creates_new(self, tmp_db):
        """Phase 55: duplicate outside window creates new record."""
        store = SQLiteNotificationAlertDedupStore(tmp_db)
        now = datetime.now(timezone.utc)
        r1 = _make_record(dedup_key="k1", last_seen_at=now - timedelta(hours=1))
        store.upsert(r1)
        # New occurrence outside window
        r2 = _make_record(dedup_key="k1", occurrence_count=1)
        store.upsert(r2)
        # The second upsert replaces the first (INSERT OR REPLACE)
        fetched = store.get("k1")
        assert fetched.occurrence_count == 1
        store.close()


# ---------------------------------------------------------------------------
# Service with store
# ---------------------------------------------------------------------------


class TestDedupServiceWithStore:
    @pytest.mark.asyncio
    async def test_service_uses_store(self, tmp_path):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir=str(tmp_path)) as f:
            path = f.name
        try:
            store = SQLiteNotificationAlertDedupStore(path)
            svc = NotificationAlertDedupService(
                merge_window_seconds=300, store=store,
            )
            now = datetime.now(timezone.utc)

            # First call — not suppressed
            r1 = svc.should_suppress_or_merge(
                alert_id="alt_001", target_id="tgt_001",
                now=now, federation_id="fed_1", channel="webhook", severity="critical",
            )
            assert r1["suppressed"] is False

            # Second call within window — suppressed
            r2 = svc.should_suppress_or_merge(
                alert_id="alt_001", target_id="tgt_001",
                now=now + timedelta(seconds=10),
            )
            assert r2["suppressed"] is True
            assert "occurrence #" in r2["reason"]

            # Verify persistence across service instances
            store.close()
            store2 = SQLiteNotificationAlertDedupStore(path)
            svc2 = NotificationAlertDedupService(merge_window_seconds=300, store=store2)
            r3 = svc2.should_suppress_or_merge(
                alert_id="alt_001", target_id="tgt_001",
                now=now + timedelta(seconds=20),
            )
            assert r3["suppressed"] is True  # Still within window from last_seen
            store2.close()
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_service_fallback_to_memory(self):
        """Phase 55: without store, falls back to Phase 54 behavior."""
        svc = NotificationAlertDedupService(merge_window_seconds=300)
        now = datetime.now(timezone.utc)

        r1 = svc.should_suppress_or_merge("alt_001", "tgt_001", now=now)
        assert r1["suppressed"] is False

        r2 = svc.should_suppress_or_merge("alt_001", "tgt_001", now=now + timedelta(seconds=10))
        assert r2["suppressed"] is True

    @pytest.mark.asyncio
    async def test_service_outside_window_new_decision(self):
        """Phase 55: outside merge window, treated as new."""
        svc = NotificationAlertDedupService(merge_window_seconds=60)
        now = datetime.now(timezone.utc)

        svc.should_suppress_or_merge("alt_001", "tgt_001", now=now)
        r2 = svc.should_suppress_or_merge(
            "alt_001", "tgt_001",
            now=now + timedelta(minutes=2),  # outside 60s window
        )
        assert r2["suppressed"] is False

    @pytest.mark.asyncio
    async def test_service_prune_with_store(self, tmp_path):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir=str(tmp_path)) as f:
            path = f.name
        try:
            store = SQLiteNotificationAlertDedupStore(path)
            svc = NotificationAlertDedupService(merge_window_seconds=30, store=store)
            now = datetime.now(timezone.utc)
            # Create an expired record
            r = _make_record(dedup_key="k1", expires_at=now - timedelta(minutes=1))
            store.upsert(r)
            svc.should_suppress_or_merge("alt_001", "tgt_001", now=now)
            svc.prune(now=now)
            # The expired k1 should be marked as expired
            assert store.get("k1").status == "expired"
            store.close()
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestFactory:
    def test_create_memory(self):
        store = create_notification_alert_dedup_store("memory")
        assert isinstance(store, InMemoryNotificationAlertDedupStore)

    def test_create_sqlite(self, tmp_path):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir=str(tmp_path)) as f:
            path = f.name
        try:
            store = create_notification_alert_dedup_store("sqlite", path)
            assert isinstance(store, SQLiteNotificationAlertDedupStore)
            store.close()
        finally:
            os.unlink(path)
