"""Tests for FederationNotificationTemplateStore — InMemory, SQLite, and factory."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_app.governance.policy_rollout_federation_notification_template import (
    FederationNotificationTemplate,
    FederationNotificationTemplateFormat,
)
from agent_app.runtime.policy_rollout_federation_notification_template_store import (
    FederationNotificationTemplateStore,
    InMemoryFederationNotificationTemplateStore,
    SQLiteFederationNotificationTemplateStore,
    create_federation_notification_template_store,
)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _now(offset_seconds: int = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)


def _make_template(**overrides) -> FederationNotificationTemplate:
    now = _now()
    defaults = dict(
        template_id=f"fnt_{uuid.uuid4().hex}",
        name="Test Template",
        description="A test template",
        body_template="Approval {{ approval.id }} created",
        format=FederationNotificationTemplateFormat.TEXT,
        version=1,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return FederationNotificationTemplate(**defaults)


# ---------------------------------------------------------------------------
# InMemoryFederationNotificationTemplateStore
# ---------------------------------------------------------------------------


class TestInMemoryFederationNotificationTemplateStore:
    def test_inmemory_create_and_get(self) -> None:
        store = InMemoryFederationNotificationTemplateStore()
        template = _make_template(template_id="fnt_001")
        result = _run_async(store.create(template))
        assert result == template
        assert _run_async(store.get("fnt_001")) == template

    def test_inmemory_get_nonexistent(self) -> None:
        store = InMemoryFederationNotificationTemplateStore()
        assert _run_async(store.get("fnt_missing")) is None

    def test_inmemory_list_all(self) -> None:
        store = InMemoryFederationNotificationTemplateStore()
        t1 = _make_template(template_id="fnt_001", created_at=_now(0))
        t2 = _make_template(template_id="fnt_002", created_at=_now(10))
        _run_async(store.create(t1))
        _run_async(store.create(t2))

        result = _run_async(store.list())
        assert len(result) == 2
        assert result[0].template_id == "fnt_001"
        assert result[1].template_id == "fnt_002"

    def test_inmemory_list_by_event_type(self) -> None:
        store = InMemoryFederationNotificationTemplateStore()
        t1 = _make_template(template_id="fnt_001", event_type="approval.created", created_at=_now(0))
        t2 = _make_template(template_id="fnt_002", event_type="approval.rejected", created_at=_now(10))
        t3 = _make_template(template_id="fnt_003", event_type="approval.created", created_at=_now(20))
        _run_async(store.create(t1))
        _run_async(store.create(t2))
        _run_async(store.create(t3))

        result = _run_async(store.list(event_type="approval.created"))
        assert len(result) == 2
        assert result[0].template_id == "fnt_001"
        assert result[1].template_id == "fnt_003"

    def test_inmemory_list_by_channel(self) -> None:
        store = InMemoryFederationNotificationTemplateStore()
        t1 = _make_template(template_id="fnt_001", channel="webhook", created_at=_now(0))
        t2 = _make_template(template_id="fnt_002", channel="email", created_at=_now(10))
        t3 = _make_template(template_id="fnt_003", channel="webhook", created_at=_now(20))
        _run_async(store.create(t1))
        _run_async(store.create(t2))
        _run_async(store.create(t3))

        result = _run_async(store.list(channel="webhook"))
        assert len(result) == 2
        assert result[0].template_id == "fnt_001"
        assert result[1].template_id == "fnt_003"

    def test_inmemory_list_by_enabled(self) -> None:
        store = InMemoryFederationNotificationTemplateStore()
        t1 = _make_template(template_id="fnt_001", enabled=True, created_at=_now(0))
        t2 = _make_template(template_id="fnt_002", enabled=False, created_at=_now(10))
        t3 = _make_template(template_id="fnt_003", enabled=True, created_at=_now(20))
        _run_async(store.create(t1))
        _run_async(store.create(t2))
        _run_async(store.create(t3))

        result = _run_async(store.list(enabled=True))
        assert len(result) == 2
        assert result[0].template_id == "fnt_001"
        assert result[1].template_id == "fnt_003"

    def test_inmemory_list_pagination(self) -> None:
        store = InMemoryFederationNotificationTemplateStore()
        for i in range(5):
            _run_async(store.create(_make_template(template_id=f"fnt_{i:03d}", created_at=_now(i))))

        # Limit
        result = _run_async(store.list(limit=2))
        assert len(result) == 2
        assert result[0].template_id == "fnt_000"
        assert result[1].template_id == "fnt_001"

        # Offset
        result = _run_async(store.list(offset=2))
        assert len(result) == 3
        assert result[0].template_id == "fnt_002"

    def test_inmemory_update(self) -> None:
        store = InMemoryFederationNotificationTemplateStore()
        template = _make_template(template_id="fnt_001", version=1, name="Original")
        _run_async(store.create(template))

        updated = template.model_copy(update={"version": 2, "name": "Updated", "updated_at": _now(100)})
        result = _run_async(store.update(updated))
        assert result.name == "Updated"
        assert result.version == 2

        # Verify persistence
        loaded = _run_async(store.get("fnt_001"))
        assert loaded is not None
        assert loaded.name == "Updated"
        assert loaded.version == 2

    def test_inmemory_update_version_conflict(self) -> None:
        store = InMemoryFederationNotificationTemplateStore()
        template = _make_template(template_id="fnt_001", version=1)
        _run_async(store.create(template))

        # Try to update with version 3 (skipping 2) — should conflict
        conflicting = template.model_copy(update={"version": 3, "updated_at": _now(100)})
        try:
            _run_async(store.update(conflicting))
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "Version conflict" in str(e)

    def test_inmemory_delete_soft_deletes(self) -> None:
        store = InMemoryFederationNotificationTemplateStore()
        template = _make_template(template_id="fnt_001", enabled=True)
        _run_async(store.create(template))

        _run_async(store.delete("fnt_001"))
        # Template still exists but is disabled
        loaded = _run_async(store.get("fnt_001"))
        assert loaded is not None
        assert loaded.enabled is False

    def test_inmemory_find_effective_template_specific(self) -> None:
        store = InMemoryFederationNotificationTemplateStore()
        # Global template
        t_global = _make_template(template_id="fnt_global", event_type=None, channel=None, federation_id=None, enabled=True)
        # Channel-only template
        t_channel = _make_template(template_id="fnt_channel", event_type=None, channel="webhook", federation_id=None, enabled=True)
        # Event+channel template
        t_event_channel = _make_template(template_id="fnt_event_channel", event_type="approval.created", channel="webhook", federation_id=None, enabled=True)
        # Most specific: federation+event+channel
        t_specific = _make_template(template_id="fnt_specific", event_type="approval.created", channel="webhook", federation_id="frp_alpha", enabled=True)

        _run_async(store.create(t_global))
        _run_async(store.create(t_channel))
        _run_async(store.create(t_event_channel))
        _run_async(store.create(t_specific))

        # Most specific match
        result = _run_async(store.find_effective_template(federation_id="frp_alpha", event_type="approval.created", channel="webhook"))
        assert result is not None
        assert result.template_id == "fnt_specific"

    def test_inmemory_find_effective_template_fallback(self) -> None:
        store = InMemoryFederationNotificationTemplateStore()
        # Global template
        t_global = _make_template(template_id="fnt_global", event_type=None, channel=None, federation_id=None, enabled=True)
        # Channel-only template
        t_channel = _make_template(template_id="fnt_channel", event_type=None, channel="webhook", federation_id=None, enabled=True)
        # Event+channel template
        t_event_channel = _make_template(template_id="fnt_event_channel", event_type="approval.created", channel="webhook", federation_id=None, enabled=True)

        _run_async(store.create(t_global))
        _run_async(store.create(t_channel))
        _run_async(store.create(t_event_channel))

        # No federation+event+channel match, should fall back to event+channel
        result = _run_async(store.find_effective_template(federation_id="frp_alpha", event_type="approval.created", channel="webhook"))
        assert result is not None
        assert result.template_id == "fnt_event_channel"

        # No event+channel match for different event, should fall back to channel
        result = _run_async(store.find_effective_template(federation_id="frp_alpha", event_type="approval.rejected", channel="webhook"))
        assert result is not None
        assert result.template_id == "fnt_channel"

        # No channel match, should fall back to global
        result = _run_async(store.find_effective_template(federation_id="frp_alpha", event_type="approval.created", channel="email"))
        assert result is not None
        assert result.template_id == "fnt_global"


# ---------------------------------------------------------------------------
# SQLiteFederationNotificationTemplateStore
# ---------------------------------------------------------------------------


class TestSQLiteFederationNotificationTemplateStore:
    def test_sqlite_create_and_get(self, tmp_path: Path) -> None:
        db_path = tmp_path / "templates.db"
        store = SQLiteFederationNotificationTemplateStore(str(db_path))
        template = _make_template(template_id="fnt_001")
        _run_async(store.create(template))

        result = _run_async(store.get("fnt_001"))
        assert result is not None
        assert result.template_id == "fnt_001"
        assert result.name == "Test Template"
        assert result.description == "A test template"
        assert result.body_template == "Approval {{ approval.id }} created"
        assert result.format == FederationNotificationTemplateFormat.TEXT
        assert result.enabled is True
        assert result.version == 1
        assert result.metadata == {}
        store.close()

    def test_sqlite_list_by_filters(self, tmp_path: Path) -> None:
        db_path = tmp_path / "templates.db"
        store = SQLiteFederationNotificationTemplateStore(str(db_path))
        t1 = _make_template(template_id="fnt_001", event_type="approval.created", channel="webhook", enabled=True, created_at=_now(0))
        t2 = _make_template(template_id="fnt_002", event_type="approval.rejected", channel="email", enabled=False, created_at=_now(10))
        t3 = _make_template(template_id="fnt_003", event_type="approval.created", channel="email", enabled=True, created_at=_now(20))
        _run_async(store.create(t1))
        _run_async(store.create(t2))
        _run_async(store.create(t3))

        # Filter by event_type
        result = _run_async(store.list(event_type="approval.created"))
        assert len(result) == 2

        # Filter by channel
        result = _run_async(store.list(channel="email"))
        assert len(result) == 2

        # Filter by enabled
        result = _run_async(store.list(enabled=True))
        assert len(result) == 2

        # Combined filter
        result = _run_async(store.list(event_type="approval.created", enabled=True))
        assert len(result) == 2
        store.close()

    def test_sqlite_update(self, tmp_path: Path) -> None:
        db_path = tmp_path / "templates.db"
        store = SQLiteFederationNotificationTemplateStore(str(db_path))
        template = _make_template(template_id="fnt_001", version=1, name="Original")
        _run_async(store.create(template))

        updated = template.model_copy(update={"version": 2, "name": "Updated", "updated_at": _now(100)})
        result = _run_async(store.update(updated))
        assert result.name == "Updated"
        assert result.version == 2

        # Verify persistence
        loaded = _run_async(store.get("fnt_001"))
        assert loaded is not None
        assert loaded.name == "Updated"
        assert loaded.version == 2
        store.close()

    def test_sqlite_update_version_conflict(self, tmp_path: Path) -> None:
        db_path = tmp_path / "templates.db"
        store = SQLiteFederationNotificationTemplateStore(str(db_path))
        template = _make_template(template_id="fnt_001", version=1)
        _run_async(store.create(template))

        # Try to update with version 3 (skipping 2) — should conflict
        conflicting = template.model_copy(update={"version": 3, "updated_at": _now(100)})
        try:
            _run_async(store.update(conflicting))
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "Version conflict" in str(e)
        store.close()

    def test_sqlite_delete(self, tmp_path: Path) -> None:
        db_path = tmp_path / "templates.db"
        store = SQLiteFederationNotificationTemplateStore(str(db_path))
        template = _make_template(template_id="fnt_001", enabled=True)
        _run_async(store.create(template))

        _run_async(store.delete("fnt_001"))
        # Template still exists but is disabled
        loaded = _run_async(store.get("fnt_001"))
        assert loaded is not None
        assert loaded.enabled is False
        store.close()

    def test_sqlite_persists_across_instances(self, tmp_path: Path) -> None:
        db_path = tmp_path / "templates.db"
        store = SQLiteFederationNotificationTemplateStore(str(db_path))
        template = _make_template(
            template_id="fnt_001",
            name="Persisted Template",
            description="Cross-instance test",
            event_type="approval.created",
            channel="webhook",
            federation_id="frp_alpha",
            subject_template="Subject {{ event }}",
            body_template="Body {{ approval.id }}",
            format=FederationNotificationTemplateFormat.HTML,
            enabled=True,
            version=1,
            metadata={"priority": "high", "tags": ["urgent"]},
        )
        _run_async(store.create(template))
        store.close()

        # Reopen same DB
        store2 = SQLiteFederationNotificationTemplateStore(str(db_path))
        loaded = _run_async(store2.get("fnt_001"))

        assert loaded is not None
        assert loaded.template_id == "fnt_001"
        assert loaded.name == "Persisted Template"
        assert loaded.description == "Cross-instance test"
        assert loaded.event_type == "approval.created"
        assert loaded.channel == "webhook"
        assert loaded.federation_id == "frp_alpha"
        assert loaded.subject_template == "Subject {{ event }}"
        assert loaded.body_template == "Body {{ approval.id }}"
        assert loaded.format == FederationNotificationTemplateFormat.HTML
        assert loaded.enabled is True
        assert loaded.version == 1
        assert loaded.metadata == {"priority": "high", "tags": ["urgent"]}
        store2.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCreateFederationNotificationTemplateStore:
    def test_factory_memory_type(self) -> None:
        store = create_federation_notification_template_store("memory")
        assert isinstance(store, InMemoryFederationNotificationTemplateStore)
        assert isinstance(store, FederationNotificationTemplateStore)

    def test_factory_sqlite_type(self, tmp_path: Path) -> None:
        db_path = tmp_path / "templates.db"
        store = create_federation_notification_template_store("sqlite", str(db_path))
        assert isinstance(store, SQLiteFederationNotificationTemplateStore)
        assert isinstance(store, FederationNotificationTemplateStore)
        store.close()
