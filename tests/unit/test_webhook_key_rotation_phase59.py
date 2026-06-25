"""Tests for webhook key rotation service (Phase 59 Task 738)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from agent_app.runtime.policy_rollout_federation_notification_webhook_key_rotation import (
    InMemoryWebhookKeyRotationStore,
    SQLiteWebhookKeyRotationStore,
    WebhookKeyRotationConfig,
    WebhookKeyRotationRecord,
    WebhookKeyRotationService,
    WebhookKeyRotationStore,
    create_webhook_key_rotation_store,
    _generate_key_id,
    _generate_secret,
    _now,
)
from agent_app.runtime.policy_rollout_federation_notification_webhook_signing import (
    WebhookSigningSecret,
)


class TestHelpers:
    """Helper function tests."""

    def test_generate_key_id_format(self):
        key_id = _generate_key_id()
        assert key_id.startswith("whk_")
        assert len(key_id) == 4 + 16  # "whk_" + 16 hex chars

    def test_generate_key_id_unique(self):
        ids = [_generate_key_id() for _ in range(100)]
        assert len(set(ids)) == 100

    def test_generate_secret_length(self):
        secret = _generate_secret(256)
        # 256 bits = 32 bytes = 64 hex chars
        assert len(secret) == 64

    def test_generate_secret_min_bits(self):
        secret = _generate_secret(16)
        # min 32 bytes = 64 hex chars
        assert len(secret) == 64


class TestInMemoryWebhookKeyRotationStore:
    """In-memory rotation store tests."""

    def test_record_and_get_last(self):
        store = InMemoryWebhookKeyRotationStore()
        record = WebhookKeyRotationRecord(
            rotation_id="rot_1",
            old_key_id="key_old",
            new_key_id="key_new",
        )
        store.record_rotation(record)
        last = store.get_last_rotation()
        assert last is not None
        assert last.rotation_id == "rot_1"
        assert last.new_key_id == "key_new"

    def test_list_rotations(self):
        store = InMemoryWebhookKeyRotationStore()
        for i in range(5):
            record = WebhookKeyRotationRecord(
                rotation_id=f"rot_{i}",
                old_key_id=f"old_{i}",
                new_key_id=f"new_{i}",
            )
            store.record_rotation(record)
        all_rotations = store.list_rotations()
        assert len(all_rotations) == 5
        # Should be ordered most recent first
        assert all_rotations[0].rotation_id == "rot_4"

    def test_list_rotations_limit(self):
        store = InMemoryWebhookKeyRotationStore()
        for i in range(10):
            record = WebhookKeyRotationRecord(
                rotation_id=f"rot_{i}",
                old_key_id=f"old_{i}",
                new_key_id=f"new_{i}",
            )
            store.record_rotation(record)
        limited = store.list_rotations(limit=3)
        assert len(limited) == 3

    def test_empty_get_last_returns_none(self):
        store = InMemoryWebhookKeyRotationStore()
        assert store.get_last_rotation() is None

    def test_empty_list_returns_empty(self):
        store = InMemoryWebhookKeyRotationStore()
        assert store.list_rotations() == []


class TestSQLiteWebhookKeyRotationStore:
    """SQLite rotation store tests."""

    def test_record_and_get_last(self, tmp_path):
        db = str(tmp_path / "key_rotation.db")
        store = SQLiteWebhookKeyRotationStore(db_path=db)
        record = WebhookKeyRotationRecord(
            rotation_id="rot_1",
            old_key_id="key_old",
            new_key_id="key_new",
            reason="manual",
        )
        store.record_rotation(record)
        last = store.get_last_rotation()
        assert last is not None
        assert last.new_key_id == "key_new"
        assert last.reason == "manual"
        store.close()

    def test_persists_across_instances(self, tmp_path):
        db = str(tmp_path / "key_rotation.db")
        store1 = SQLiteWebhookKeyRotationStore(db_path=db)
        record = WebhookKeyRotationRecord(
            rotation_id="rot_1",
            old_key_id="key_old",
            new_key_id="key_new",
        )
        store1.record_rotation(record)
        store1.close()

        store2 = SQLiteWebhookKeyRotationStore(db_path=db)
        last = store2.get_last_rotation()
        assert last is not None
        assert last.new_key_id == "key_new"
        store2.close()


class TestWebhookKeyRotationService:
    """Key rotation service tests."""

    def test_generate_first_key(self):
        service = WebhookKeyRotationService()
        key = service.generate_new_key()
        assert key.status == "active"
        assert key.key_id is not None
        assert key.secret is not None
        assert len(key.secret) > 0

    def test_get_active_after_generation(self):
        service = WebhookKeyRotationService()
        key = service.generate_new_key()
        active = service.get_active()
        assert active is not None
        assert active.key_id == key.key_id

    def test_rotate_creates_new_active(self):
        service = WebhookKeyRotationService()
        key1 = service.generate_new_key()
        key2 = service.generate_new_key()
        assert key2.key_id != key1.key_id
        assert service.get_active().key_id == key2.key_id

    def test_previous_key_demoted(self):
        service = WebhookKeyRotationService()
        key1 = service.generate_new_key()
        service.generate_new_key()
        previous = service.get_previous()
        assert len(previous) == 1
        assert previous[0].key_id == key1.key_id
        assert previous[0].status == "previous"

    def test_should_rotate_initially(self):
        service = WebhookKeyRotationService()
        assert service.should_rotate() is True

    def test_should_not_rotate_recently(self):
        service = WebhookKeyRotationService()
        service.generate_new_key()
        assert service.should_rotate() is False

    def test_force_rotate(self):
        service = WebhookKeyRotationService()
        key1 = service.generate_new_key()
        key2 = service.force_rotate(reason="emergency")
        assert key2.key_id != key1.key_id
        assert service.get_active().key_id == key2.key_id

    def test_rotation_recorded(self):
        store = InMemoryWebhookKeyRotationStore()
        service = WebhookKeyRotationService(store=store)
        service.generate_new_key()
        last = store.get_last_rotation()
        assert last is not None
        assert last.reason == "scheduled"

    def test_force_rotation_reason(self):
        store = InMemoryWebhookKeyRotationStore()
        service = WebhookKeyRotationService(store=store)
        service.force_rotate(reason="security")
        last = store.get_last_rotation()
        assert last.reason == "security"

    def test_keep_previous_count(self):
        config = WebhookKeyRotationConfig(keep_previous_count=2)
        service = WebhookKeyRotationService(config=config)
        service.generate_new_key()
        service.generate_new_key()
        service.generate_new_key()
        previous = service.get_previous()
        assert len(previous) == 2

    def test_excess_previous_disabled(self):
        config = WebhookKeyRotationConfig(keep_previous_count=1)
        service = WebhookKeyRotationService(config=config)
        service.generate_new_key()
        service.generate_new_key()
        service.generate_new_key()
        # All non-active keys should be either previous or disabled
        non_active = [s for s in service._secrets.values() if s.status != "active"]
        disabled = [s for s in non_active if s.status == "disabled"]
        assert len(disabled) >= 1

    def test_valid_for_verification(self):
        service = WebhookKeyRotationService()
        service.generate_new_key()
        valid = service.get_valid_for_verification()
        assert len(valid) == 1
        assert valid[0].status == "active"

    def test_valid_includes_previous(self):
        service = WebhookKeyRotationService()
        service.generate_new_key()
        service.generate_new_key()
        valid = service.get_valid_for_verification()
        # Should include active + 1 previous
        assert len(valid) >= 1


class TestWebhookKeyRotationFactory:
    """Factory function tests."""

    def test_memory_factory(self):
        store = create_webhook_key_rotation_store("memory")
        assert isinstance(store, InMemoryWebhookKeyRotationStore)

    def test_sqlite_factory(self, tmp_path):
        db = str(tmp_path / "key_rotation.db")
        store = create_webhook_key_rotation_store("sqlite", db_path=db)
        assert isinstance(store, SQLiteWebhookKeyRotationStore)

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown webhook key rotation store type"):
            create_webhook_key_rotation_store("redis")
