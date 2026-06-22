"""Tests for NotificationAlertStore — InMemory, SQLite, and factory."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_app.governance.policy_rollout_federation_notification_observability import (
    NotificationAlertEvent,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_store import (
    InMemoryNotificationAlertStore,
    NotificationAlertStore,
    SQLiteNotificationAlertStore,
    create_notification_alert_store,
)


def _now(offset_seconds: int = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)


def _make_alert(**overrides) -> NotificationAlertEvent:
    now = _now()
    defaults = dict(
        alert_id=f"nae_{uuid.uuid4().hex[:12]}",
        rule_id="nar_001",
        name="High failure rate",
        severity="critical",
        metric="failure_rate",
        observed_value=0.25,
        threshold=0.05,
        federation_id="fed_a",
        channel="webhook",
        message="Failure rate exceeded threshold",
        status="open",
        created_at=now,
        acknowledged_at=None,
        acknowledged_by=None,
        resolved_at=None,
        resolved_by=None,
    )
    defaults.update(overrides)
    return NotificationAlertEvent(**defaults)


# ---------------------------------------------------------------------------
# InMemoryNotificationAlertStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestInMemoryNotificationAlertStore:
    async def test_create_and_get_alert(self) -> None:
        store = InMemoryNotificationAlertStore()
        alert = _make_alert(alert_id="nae_001")
        result = await store.create_alert(alert)
        assert result == alert

        loaded = await store.get_alert("nae_001")
        assert loaded == alert

    async def test_get_nonexistent_returns_none(self) -> None:
        store = InMemoryNotificationAlertStore()
        assert await store.get_alert("nae_missing") is None

    async def test_list_by_status(self) -> None:
        store = InMemoryNotificationAlertStore()
        a1 = _make_alert(alert_id="nae_001", rule_id="nar_status", status="open", created_at=_now(0))
        a2 = _make_alert(alert_id="nae_002", rule_id="nar_status_ack", status="acknowledged", created_at=_now(10))
        a3 = _make_alert(alert_id="nae_003", rule_id="nar_status_open", status="open", created_at=_now(20))
        await store.create_alert(a1, now=_now(0))
        await store.create_alert(a2, now=_now(10))
        await store.create_alert(a3, now=_now(20))

        result = await store.list_alerts(status="open")
        assert len(result) == 2
        assert result[0].alert_id == "nae_003"  # newest first
        assert result[1].alert_id == "nae_001"

    async def test_list_by_severity(self) -> None:
        store = InMemoryNotificationAlertStore()
        a1 = _make_alert(alert_id="nae_001", rule_id="nar_sev_crit1", severity="critical", created_at=_now(0))
        a2 = _make_alert(alert_id="nae_002", rule_id="nar_sev_warn", severity="warning", created_at=_now(10))
        a3 = _make_alert(alert_id="nae_003", rule_id="nar_sev_crit2", severity="critical", created_at=_now(20))
        await store.create_alert(a1, now=_now(0))
        await store.create_alert(a2, now=_now(10))
        await store.create_alert(a3, now=_now(20))

        result = await store.list_alerts(severity="critical")
        assert len(result) == 2
        assert result[0].alert_id == "nae_003"
        assert result[1].alert_id == "nae_001"

    async def test_list_by_channel(self) -> None:
        store = InMemoryNotificationAlertStore()
        a1 = _make_alert(alert_id="nae_001", rule_id="nar_ch_wh1", channel="webhook", created_at=_now(0))
        a2 = _make_alert(alert_id="nae_002", rule_id="nar_ch_em", channel="email", created_at=_now(10))
        a3 = _make_alert(alert_id="nae_003", rule_id="nar_ch_wh2", channel="webhook", created_at=_now(20))
        await store.create_alert(a1, now=_now(0))
        await store.create_alert(a2, now=_now(10))
        await store.create_alert(a3, now=_now(20))

        result = await store.list_alerts(channel="webhook")
        assert len(result) == 2
        assert result[0].alert_id == "nae_003"
        assert result[1].alert_id == "nae_001"

    async def test_list_by_federation_id(self) -> None:
        store = InMemoryNotificationAlertStore()
        a1 = _make_alert(alert_id="nae_001", rule_id="nar_fed_a1", federation_id="fed_a", created_at=_now(0))
        a2 = _make_alert(alert_id="nae_002", rule_id="nar_fed_b", federation_id="fed_b", created_at=_now(10))
        a3 = _make_alert(alert_id="nae_003", rule_id="nar_fed_a2", federation_id="fed_a", created_at=_now(20))
        await store.create_alert(a1, now=_now(0))
        await store.create_alert(a2, now=_now(10))
        await store.create_alert(a3, now=_now(20))

        result = await store.list_alerts(federation_id="fed_a")
        assert len(result) == 2
        assert result[0].alert_id == "nae_003"
        assert result[1].alert_id == "nae_001"

    async def test_list_pagination(self) -> None:
        store = InMemoryNotificationAlertStore()
        for i in range(5):
            await store.create_alert(
                _make_alert(alert_id=f"nae_{i:03d}", rule_id=f"nar_pag_{i}", created_at=_now(i)),
                now=_now(i),
            )

        result = await store.list_alerts(limit=2)
        assert len(result) == 2
        assert result[0].alert_id == "nae_004"  # newest first
        assert result[1].alert_id == "nae_003"

        result = await store.list_alerts(offset=2)
        assert len(result) == 3
        assert result[0].alert_id == "nae_002"

    async def test_acknowledge_alert(self) -> None:
        store = InMemoryNotificationAlertStore()
        ack_by = "admin_user"
        ack_at = _now(100)
        alert = _make_alert(alert_id="nae_001", status="open")
        await store.create_alert(alert)

        result = await store.acknowledge("nae_001", ack_by, now=ack_at)
        assert result is not None
        assert result.status == "acknowledged"
        assert result.acknowledged_by == ack_by
        assert result.acknowledged_at == ack_at

    async def test_resolve_alert(self) -> None:
        store = InMemoryNotificationAlertStore()
        resolve_by = "admin_user"
        resolve_at = _now(100)
        alert = _make_alert(alert_id="nae_001", status="open")
        await store.create_alert(alert)

        result = await store.resolve("nae_001", resolve_by, now=resolve_at)
        assert result is not None
        assert result.status == "resolved"
        assert result.resolved_by == resolve_by
        assert result.resolved_at == resolve_at

    async def test_cannot_acknowledge_resolved_alert(self) -> None:
        store = InMemoryNotificationAlertStore()
        alert = _make_alert(alert_id="nae_001", status="open")
        await store.create_alert(alert)
        await store.resolve("nae_001", "admin", now=_now(50))

        result = await store.acknowledge("nae_001", "admin", now=_now(100))
        assert result is None

    async def test_cannot_resolve_already_resolved_alert(self) -> None:
        store = InMemoryNotificationAlertStore()
        alert = _make_alert(alert_id="nae_001", status="open")
        await store.create_alert(alert)
        await store.resolve("nae_001", "admin", now=_now(50))

        result = await store.resolve("nae_001", "admin", now=_now(100))
        assert result is None

    async def test_cooldown_prevents_duplicate_alert(self) -> None:
        store = InMemoryNotificationAlertStore()
        cooldown_at = _now(0)
        alert1 = _make_alert(
            alert_id="nae_001",
            rule_id="nar_001",
            created_at=cooldown_at,
        )
        await store.create_alert(alert1, now=cooldown_at)

        # Same rule_id within cooldown should be skipped
        alert2 = _make_alert(
            alert_id="nae_002",
            rule_id="nar_001",
            created_at=_now(5),
        )
        result = await store.create_alert(alert2, now=_now(5))
        assert result is None  # cooldown blocked

        # After cooldown window (30 min), new alert should be created
        later = cooldown_at + timedelta(minutes=31)
        alert3 = _make_alert(
            alert_id="nae_003",
            rule_id="nar_001",
            created_at=later,
        )
        result = await store.create_alert(alert3, now=later)
        assert result is not None
        assert result.alert_id == "nae_003"


# ---------------------------------------------------------------------------
# SQLiteNotificationAlertStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSQLiteNotificationAlertStore:
    async def test_create_and_get_alert(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "alerts.db")
        store = SQLiteNotificationAlertStore(db_path)
        alert = _make_alert(alert_id="nae_001")
        result = await store.create_alert(alert)
        assert result == alert

        loaded = await store.get_alert("nae_001")
        assert loaded is not None
        assert loaded.alert_id == "nae_001"
        assert loaded.rule_id == "nar_001"
        assert loaded.name == "High failure rate"
        assert loaded.severity == "critical"
        assert loaded.metric == "failure_rate"
        assert loaded.observed_value == 0.25
        assert loaded.threshold == 0.05
        assert loaded.federation_id == "fed_a"
        assert loaded.channel == "webhook"
        assert loaded.message == "Failure rate exceeded threshold"
        assert loaded.status == "open"
        assert loaded.created_at is not None
        assert loaded.acknowledged_by is None
        assert loaded.resolved_by is None
        store.close()

    async def test_list_with_filters(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "alerts.db")
        store = SQLiteNotificationAlertStore(db_path)
        now = _now()
        a1 = _make_alert(alert_id="nae_001", rule_id="nar_sq_crit", severity="critical", channel="webhook", federation_id="fed_a", created_at=now)
        a2 = _make_alert(alert_id="nae_002", rule_id="nar_sq_warn", severity="warning", channel="email", federation_id="fed_b", created_at=_now(10))
        a3 = _make_alert(alert_id="nae_003", rule_id="nar_sq_crit2", severity="critical", channel="webhook", federation_id="fed_a", created_at=_now(20))
        await store.create_alert(a1, now=now)
        await store.create_alert(a2, now=_now(10))
        await store.create_alert(a3, now=_now(20))

        # Filter by severity
        result = await store.list_alerts(severity="critical")
        assert len(result) == 2
        assert result[0].alert_id == "nae_003"  # newest first

        # Filter by channel
        result = await store.list_alerts(channel="webhook")
        assert len(result) == 2

        # Filter by federation_id
        result = await store.list_alerts(federation_id="fed_a")
        assert len(result) == 2

        # Filter by status
        result = await store.list_alerts(status="open")
        assert len(result) == 3

        # Combined filters
        result = await store.list_alerts(severity="critical", channel="webhook")
        assert len(result) == 2
        assert result[0].alert_id == "nae_003"

        store.close()

    async def test_acknowledge_and_resolve(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "alerts.db")
        store = SQLiteNotificationAlertStore(db_path)
        alert = _make_alert(alert_id="nae_001")
        await store.create_alert(alert)

        ack_at = _now(100)
        result = await store.acknowledge("nae_001", "admin_user", now=ack_at)
        assert result is not None
        assert result.status == "acknowledged"
        assert result.acknowledged_by == "admin_user"
        assert result.acknowledged_at == ack_at

        resolve_at = _now(200)
        result = await store.resolve("nae_001", "admin_user", now=resolve_at)
        assert result is not None
        assert result.status == "resolved"
        assert result.resolved_by == "admin_user"
        assert result.resolved_at == resolve_at

        store.close()

    async def test_persists_across_instances(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "alerts.db")
        store1 = SQLiteNotificationAlertStore(db_path)
        alert = _make_alert(
            alert_id="nae_001",
            rule_id="nar_persist",
            severity="warning",
            channel="email",
            federation_id="fed_persist",
            message="Persistent alert",
            created_at=_now(),
        )
        await store1.create_alert(alert)
        await store1.acknowledge("nae_001", "admin", now=_now(50))
        store1.close()

        # Reopen same DB
        store2 = SQLiteNotificationAlertStore(db_path)
        loaded = await store2.get_alert("nae_001")
        assert loaded is not None
        assert loaded.alert_id == "nae_001"
        assert loaded.rule_id == "nar_persist"
        assert loaded.severity == "warning"
        assert loaded.channel == "email"
        assert loaded.federation_id == "fed_persist"
        assert loaded.message == "Persistent alert"
        assert loaded.status == "acknowledged"
        assert loaded.acknowledged_by == "admin"
        assert loaded.resolved_by is None
        store2.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCreateNotificationAlertStore:
    def test_factory_memory_type(self) -> None:
        store = create_notification_alert_store("memory")
        assert isinstance(store, InMemoryNotificationAlertStore)
        assert isinstance(store, NotificationAlertStore)

    def test_factory_sqlite_type(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "alerts.db")
        store = create_notification_alert_store("sqlite", db_path)
        assert isinstance(store, SQLiteNotificationAlertStore)
        assert isinstance(store, NotificationAlertStore)
        store.close()

    def test_unknown_type_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown alert store type"):
            create_notification_alert_store("redis")
