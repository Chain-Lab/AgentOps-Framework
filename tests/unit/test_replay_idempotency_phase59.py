"""Tests for replay idempotency (Phase 59 Task 734)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from agent_app.runtime.policy_rollout_federation_notification_replay_idempotency import (
    InMemoryReplayIdempotencyStore,
    SQLiteReplayIdempotencyStore,
    ReplayIdempotencyRecord,
    ReplayIdempotencyStore,
    create_replay_idempotency_store,
    _make_key,
    _now,
)


def _make_record(
    original_attempt_id: str = "nda_orig_1",
    target_id: str = "t1",
    alert_id: str = "a1",
    status: str = "started",
    **kwargs,
) -> ReplayIdempotencyRecord:
    defaults = dict(
        idempotency_key=_make_key(original_attempt_id, target_id, alert_id),
        original_attempt_id=original_attempt_id,
        replay_type="single",
        status=status,
        new_attempt_id=None,
        error_message=None,
        created_at=_now(),
        completed_at=None,
        expires_at=_now() + timedelta(hours=24),
    )
    defaults.update(kwargs)
    return ReplayIdempotencyRecord(**defaults)


class TestInMemoryReplayIdempotency:
    """In-memory replay idempotency tests."""

    def test_first_replay_creates_record(self):
        """First replay creates idempotency record."""
        store = InMemoryReplayIdempotencyStore()
        record = store.begin(_make_record())
        assert record.status == "started"

    def test_second_replay_returns_existing_completed(self):
        """Second replay returns existing completed record."""
        store = InMemoryReplayIdempotencyStore()
        r1 = store.begin(_make_record())
        store.complete(r1.idempotency_key, "nda_new_1")
        r2 = store.begin(_make_record())
        assert r2.status == "completed"
        assert r2.new_attempt_id == "nda_new_1"

    def test_in_progress_replay_conflicts(self):
        """In-progress replay returns existing started record."""
        store = InMemoryReplayIdempotencyStore()
        r1 = store.begin(_make_record())
        r2 = store.begin(_make_record())
        assert r2.status == "started"
        assert r2.idempotency_key == r1.idempotency_key

    def test_failed_replay_can_retry(self):
        """Failed replay allows retry by overwriting."""
        store = InMemoryReplayIdempotencyStore()
        r1 = store.begin(_make_record())
        store.fail(r1.idempotency_key, "error")
        # After failure, a new begin overwrites
        r2 = store.begin(_make_record(status="started"))
        assert r2.status == "started"

    def test_complete_sets_new_attempt_id(self):
        """Complete stores new_attempt_id."""
        store = InMemoryReplayIdempotencyStore()
        r1 = store.begin(_make_record())
        result = store.complete(r1.idempotency_key, "nda_new_42")
        assert result is not None
        assert result.new_attempt_id == "nda_new_42"
        assert result.status == "completed"
        assert result.completed_at is not None

    def test_fail_sets_error(self):
        """Fail stores redacted error."""
        store = InMemoryReplayIdempotencyStore()
        r1 = store.begin(_make_record())
        result = store.fail(r1.idempotency_key, "api_key=sk-12345")
        assert result is not None
        assert result.status == "failed"
        assert "api_key=[REDACTED]" in (result.error_message or "")

    def test_get_returns_record(self):
        """Get returns the record by key."""
        store = InMemoryReplayIdempotencyStore()
        r1 = store.begin(_make_record())
        result = store.get(r1.idempotency_key)
        assert result is not None
        assert result.idempotency_key == r1.idempotency_key

    def test_get_nonexistent_returns_none(self):
        """Get nonexistent returns None."""
        store = InMemoryReplayIdempotencyStore()
        assert store.get("replay:nonexistent") is None

    def test_ttl_expiry_allows_replay(self):
        """Expired records are pruned and allow new replay."""
        store = InMemoryReplayIdempotencyStore()
        past = _now() - timedelta(hours=25)
        record = _make_record(expires_at=past)
        store.begin(record)
        # After expiry, get returns None
        result = store.get(record.idempotency_key)
        assert result is None

    def test_prune_expired_removes_old_records(self):
        """Prune removes expired records."""
        store = InMemoryReplayIdempotencyStore()
        fixed_now = _now()
        old = _make_record(expires_at=fixed_now - timedelta(hours=25))
        old.created_at = fixed_now
        store.begin(old)
        # Add an active record with a different key
        active = _make_record(
            original_attempt_id="nda_orig_2",
            target_id="t2",
            alert_id="a2",
            expires_at=fixed_now + timedelta(hours=24),
        )
        store.begin(active)
        count = store.prune_expired(now=fixed_now + timedelta(hours=1))
        assert count == 1
        assert store.get(old.idempotency_key) is None
        assert store.get(active.idempotency_key) is not None
        assert store.get(old.idempotency_key) is None


class TestSQLiteReplayIdempotency:
    """SQLite replay idempotency tests."""

    def test_begin_and_get(self, tmp_path):
        """Begin and get record."""
        db = str(tmp_path / "idempotency.db")
        store = SQLiteReplayIdempotencyStore(db_path=db)
        record = _make_record()
        store.begin(record)
        result = store.get(record.idempotency_key)
        assert result is not None
        assert result.status == "started"
        store.close()

    def test_complete_and_get(self, tmp_path):
        """Complete updates record."""
        db = str(tmp_path / "idempotency.db")
        store = SQLiteReplayIdempotencyStore(db_path=db)
        record = _make_record()
        store.begin(record)
        store.complete(record.idempotency_key, "nda_new_1")
        result = store.get(record.idempotency_key)
        assert result is not None
        assert result.new_attempt_id == "nda_new_1"
        assert result.status == "completed"
        store.close()

    def test_fail_and_get(self, tmp_path):
        """Fail updates record with redacted error."""
        db = str(tmp_path / "idempotency.db")
        store = SQLiteReplayIdempotencyStore(db_path=db)
        record = _make_record()
        store.begin(record)
        store.fail(record.idempotency_key, "token=secret123")
        result = store.get(record.idempotency_key)
        assert result is not None
        assert result.status == "failed"
        assert "token=[REDACTED]" in (result.error_message or "")
        store.close()

    def test_persists_across_instances(self, tmp_path):
        """Record persists across SQLite store instances."""
        db = str(tmp_path / "idempotency.db")
        store1 = SQLiteReplayIdempotencyStore(db_path=db)
        record = _make_record()
        store1.begin(record)
        store1.close()

        store2 = SQLiteReplayIdempotencyStore(db_path=db)
        result = store2.get(record.idempotency_key)
        assert result is not None
        assert result.status == "started"
        store2.close()

    def test_prune_expired(self, tmp_path):
        """Prune removes expired records."""
        db = str(tmp_path / "idempotency.db")
        store = SQLiteReplayIdempotencyStore(db_path=db)
        old = _make_record(expires_at=_now() - timedelta(hours=25))
        store.begin(old)
        count = store.prune_expired()
        assert count == 1
        assert store.get(old.idempotency_key) is None
        store.close()

    def test_second_completed_returns_existing(self, tmp_path):
        """Second begin after complete returns existing completed record."""
        db = str(tmp_path / "idempotency.db")
        store = SQLiteReplayIdempotencyStore(db_path=db)
        record = _make_record()
        store.begin(record)
        store.complete(record.idempotency_key, "nda_new_1")
        # Second begin should return existing completed
        r2 = store.begin(_make_record())
        assert r2.status == "completed"
        assert r2.new_attempt_id == "nda_new_1"
        store.close()


class TestReplayIdempotencyFactory:
    """Factory function tests."""

    def test_memory_factory(self):
        store = create_replay_idempotency_store("memory")
        assert isinstance(store, InMemoryReplayIdempotencyStore)

    def test_sqlite_factory(self, tmp_path):
        db = str(tmp_path / "idempotency.db")
        store = create_replay_idempotency_store("sqlite", db_path=db)
        assert isinstance(store, SQLiteReplayIdempotencyStore)

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown replay idempotency store type"):
            create_replay_idempotency_store("redis")


class TestReplayIdempotencyKey:
    """Idempotency key generation tests."""

    def test_default_key_format(self):
        key = _make_key("nda_orig_1", "t1", "a1")
        assert key == "replay:nda_orig_1:t1:a1"

    def test_custom_key_accepted(self):
        record = _make_record(idempotency_key="custom:key:123")
        assert record.idempotency_key == "custom:key:123"
