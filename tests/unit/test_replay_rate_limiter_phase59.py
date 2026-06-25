"""Tests for replay rate limiter (Phase 59 Task 735)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from agent_app.runtime.policy_rollout_federation_notification_replay_rate_limiter import (
    InMemoryReplayRateLimiterStore,
    SQLiteReplayRateLimiterStore,
    ReplayRateLimiterRecord,
    ReplayRateLimiterResult,
    ReplayRateLimiterStore,
    create_replay_rate_limiter_store,
    _now,
)


def _make_key(parts: str | list[str]) -> str:
    if isinstance(parts, str):
        return parts
    return ":".join(parts)


class TestInMemoryReplayRateLimiter:
    """In-memory rate limiter tests."""

    def test_first_attempt_allowed(self):
        """First attempt is allowed."""
        store = InMemoryReplayRateLimiterStore()
        result = store.check_and_record("alert:1", window_seconds=60, max_attempts=3)
        assert result.allowed is True
        assert result.remaining == 2
        assert result.current_count == 1

    def test_within_limit_allowed(self):
        """Attempts within limit are allowed."""
        store = InMemoryReplayRateLimiterStore()
        store.check_and_record("alert:1", window_seconds=60, max_attempts=3)
        result = store.check_and_record("alert:1", window_seconds=60, max_attempts=3)
        assert result.allowed is True
        assert result.remaining == 1
        assert result.current_count == 2

    def test_at_limit_denied(self):
        """Attempts at limit are denied."""
        store = InMemoryReplayRateLimiterStore()
        store.check_and_record("alert:1", window_seconds=60, max_attempts=3)
        store.check_and_record("alert:1", window_seconds=60, max_attempts=3)
        store.check_and_record("alert:1", window_seconds=60, max_attempts=3)
        result = store.check_and_record("alert:1", window_seconds=60, max_attempts=3)
        assert result.allowed is False
        assert result.remaining == 0
        assert result.current_count == 3
        assert result.reset_at is not None

    def test_different_keys_independent(self):
        """Different keys have independent limits."""
        store = InMemoryReplayRateLimiterStore()
        store.check_and_record("alert:1", window_seconds=60, max_attempts=1)
        store.check_and_record("alert:1", window_seconds=60, max_attempts=1)
        result = store.check_and_record("alert:2", window_seconds=60, max_attempts=1)
        assert result.allowed is True

    def test_window_expiry_allows_retry(self):
        """Expired timestamps are pruned, allowing retry."""
        store = InMemoryReplayRateLimiterStore()
        past = _now() - timedelta(seconds=120)
        future = _now()
        store.check_and_record("alert:1", window_seconds=60, max_attempts=1, now=past)
        result = store.check_and_record("alert:1", window_seconds=60, max_attempts=1, now=future)
        assert result.allowed is True
        assert result.current_count == 1

    def test_reset_clears_key(self):
        """Reset clears rate limit for a key."""
        store = InMemoryReplayRateLimiterStore()
        store.check_and_record("alert:1", window_seconds=60, max_attempts=1)
        assert store.reset("alert:1") is True
        result = store.check_and_record("alert:1", window_seconds=60, max_attempts=1)
        assert result.allowed is True
        assert result.current_count == 1

    def test_reset_unknown_key_returns_false(self):
        """Reset on unknown key returns False."""
        store = InMemoryReplayRateLimiterStore()
        assert store.reset("nonexistent") is False

    def test_get_record_returns_state(self):
        """Get record returns current state."""
        store = InMemoryReplayRateLimiterStore()
        store.check_and_record("alert:1", window_seconds=60, max_attempts=3)
        record = store.get_record("alert:1")
        assert record is not None
        assert record.rate_limit_key == "alert:1"
        assert record.window_seconds == 60
        assert record.max_attempts == 3
        assert len(record.attempt_timestamps) == 1

    def test_get_nonexistent_returns_none(self):
        """Get nonexistent returns None."""
        store = InMemoryReplayRateLimiterStore()
        assert store.get_record("nonexistent") is None

    def test_reset_at_calculation(self):
        """Reset at is based on oldest attempt + window."""
        store = InMemoryReplayRateLimiterStore()
        t1 = _now() - timedelta(seconds=30)
        t2 = _now() - timedelta(seconds=10)
        store.check_and_record("alert:1", window_seconds=60, max_attempts=2, now=t1)
        store.check_and_record("alert:1", window_seconds=60, max_attempts=2, now=t2)
        result = store.check_and_record("alert:1", window_seconds=60, max_attempts=2)
        assert result.allowed is False
        assert result.reset_at == t1 + timedelta(seconds=60)

    def test_max_attempts_one(self):
        """Single attempt limit works."""
        store = InMemoryReplayRateLimiterStore()
        result = store.check_and_record("alert:1", window_seconds=60, max_attempts=1)
        assert result.allowed is True
        assert result.remaining == 0
        result = store.check_and_record("alert:1", window_seconds=60, max_attempts=1)
        assert result.allowed is False

    def test_large_window(self):
        """Large window works correctly."""
        store = InMemoryReplayRateLimiterStore()
        result = store.check_and_record(
            "alert:1", window_seconds=86400, max_attempts=100
        )
        assert result.allowed is True
        assert result.remaining == 99


class TestSQLiteReplayRateLimiter:
    """SQLite rate limiter tests."""

    def test_first_attempt_allowed(self, tmp_path):
        """First attempt allowed in SQLite."""
        db = str(tmp_path / "rate_limiter.db")
        store = SQLiteReplayRateLimiterStore(db_path=db)
        result = store.check_and_record("alert:1", window_seconds=60, max_attempts=3)
        assert result.allowed is True
        assert result.remaining == 2
        store.close()

    def test_at_limit_denied(self, tmp_path):
        """At limit denied in SQLite."""
        db = str(tmp_path / "rate_limiter.db")
        store = SQLiteReplayRateLimiterStore(db_path=db)
        for _ in range(3):
            store.check_and_record("alert:1", window_seconds=60, max_attempts=3)
        result = store.check_and_record("alert:1", window_seconds=60, max_attempts=3)
        assert result.allowed is False
        store.close()

    def test_window_expiry_allows_retry(self, tmp_path):
        """Expired timestamps pruned in SQLite."""
        db = str(tmp_path / "rate_limiter.db")
        store = SQLiteReplayRateLimiterStore(db_path=db)
        past = _now() - timedelta(seconds=120)
        store.check_and_record("alert:1", window_seconds=60, max_attempts=1, now=past)
        result = store.check_and_record("alert:1", window_seconds=60, max_attempts=1)
        assert result.allowed is True
        store.close()

    def test_persists_across_instances(self, tmp_path):
        """Record persists across SQLite store instances."""
        db = str(tmp_path / "rate_limiter.db")
        store1 = SQLiteReplayRateLimiterStore(db_path=db)
        store1.check_and_record("alert:1", window_seconds=60, max_attempts=3)
        store1.close()

        store2 = SQLiteReplayRateLimiterStore(db_path=db)
        result = store2.check_and_record("alert:1", window_seconds=60, max_attempts=3)
        assert result.allowed is True
        assert result.current_count == 2
        store2.close()

    def test_reset_works(self, tmp_path):
        """Reset clears record in SQLite."""
        db = str(tmp_path / "rate_limiter.db")
        store = SQLiteReplayRateLimiterStore(db_path=db)
        store.check_and_record("alert:1", window_seconds=60, max_attempts=1)
        assert store.reset("alert:1") is True
        result = store.check_and_record("alert:1", window_seconds=60, max_attempts=1)
        assert result.allowed is True
        store.close()

    def test_get_record(self, tmp_path):
        """Get record returns state from SQLite."""
        db = str(tmp_path / "rate_limiter.db")
        store = SQLiteReplayRateLimiterStore(db_path=db)
        store.check_and_record("alert:1", window_seconds=60, max_attempts=3)
        record = store.get_record("alert:1")
        assert record is not None
        assert record.max_attempts == 3
        assert len(record.attempt_timestamps) == 1
        store.close()


class TestReplayRateLimiterFactory:
    """Factory function tests."""

    def test_memory_factory(self):
        store = create_replay_rate_limiter_store("memory")
        assert isinstance(store, InMemoryReplayRateLimiterStore)

    def test_sqlite_factory(self, tmp_path):
        db = str(tmp_path / "rate_limiter.db")
        store = create_replay_rate_limiter_store("sqlite", db_path=db)
        assert isinstance(store, SQLiteReplayRateLimiterStore)

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown replay rate limiter store type"):
            create_replay_rate_limiter_store("redis")
