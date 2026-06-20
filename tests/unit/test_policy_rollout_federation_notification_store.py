"""Tests for FederationNotificationStore — InMemory, SQLite, and factory."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_app.governance.policy_rollout_federation_notification import (
    FederationNotificationChannel,
    FederationNotificationEventType,
    FederationNotificationMessage,
    FederationNotificationStatus,
)
from agent_app.runtime.policy_rollout_federation_notification_store import (
    FederationNotificationStore,
    InMemoryFederationNotificationStore,
    SQLiteFederationNotificationStore,
    create_federation_notification_store,
)


def _now(offset_seconds: int = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)


def _make_message(
    notification_id: str = "fn_001",
    approval_id: str = "fap_001",
    federation_id: str | None = "fed_a",
    event_type: FederationNotificationEventType = FederationNotificationEventType.APPROVAL_CREATED,
    channel: FederationNotificationChannel = FederationNotificationChannel.EMAIL,
    recipients: list[str] | None = None,
    subject: str | None = "Approval Request",
    body: str = "An approval request has been created.",
    payload: dict | None = None,
    status: FederationNotificationStatus = FederationNotificationStatus.PENDING,
    attempt_count: int = 0,
    max_attempts: int = 3,
    last_error: str | None = None,
    created_at: datetime | None = None,
    sent_at: datetime | None = None,
    next_attempt_at: datetime | None = None,
) -> FederationNotificationMessage:
    return FederationNotificationMessage(
        notification_id=notification_id,
        approval_id=approval_id,
        federation_id=federation_id,
        event_type=event_type,
        channel=channel,
        recipients=recipients or ["admin@example.com"],
        subject=subject,
        body=body,
        payload=payload or {},
        status=status,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        last_error=last_error,
        created_at=created_at or _now(),
        sent_at=sent_at,
        next_attempt_at=next_attempt_at,
    )


# ---------------------------------------------------------------------------
# InMemoryFederationNotificationStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestInMemoryFederationNotificationStore:
    async def test_create_and_get(self) -> None:
        store = InMemoryFederationNotificationStore()
        msg = _make_message()
        result = await store.create(msg)

        assert result == msg
        assert await store.get("fn_001") == msg

    async def test_get_missing_returns_none(self) -> None:
        store = InMemoryFederationNotificationStore()
        assert await store.get("fn_missing") is None

    async def test_list_pending_returns_only_pending(self) -> None:
        store = InMemoryFederationNotificationStore()
        m1 = _make_message(notification_id="fn_001", status=FederationNotificationStatus.PENDING, created_at=_now(0))
        m2 = _make_message(notification_id="fn_002", status=FederationNotificationStatus.SENT, created_at=_now(10))
        m3 = _make_message(notification_id="fn_003", status=FederationNotificationStatus.PENDING, created_at=_now(20))

        await store.create(m1)
        await store.create(m2)
        await store.create(m3)

        result = await store.list_pending()
        assert len(result) == 2
        assert result[0].notification_id == "fn_001"
        assert result[1].notification_id == "fn_003"

    async def test_list_pending_respects_limit(self) -> None:
        store = InMemoryFederationNotificationStore()
        for i in range(5):
            await store.create(_make_message(notification_id=f"fn_{i:03d}", created_at=_now(i)))

        result = await store.list_pending(limit=2)
        assert len(result) == 2
        assert result[0].notification_id == "fn_000"
        assert result[1].notification_id == "fn_001"

    async def test_mark_sent(self) -> None:
        store = InMemoryFederationNotificationStore()
        msg = _make_message(notification_id="fn_001")
        await store.create(msg)

        result = await store.mark_sent("fn_001")

        assert result.status == FederationNotificationStatus.SENT
        assert result.sent_at is not None
        assert result.sent_at.tzinfo is not None

    async def test_mark_failed_without_next_attempt(self) -> None:
        store = InMemoryFederationNotificationStore()
        msg = _make_message(notification_id="fn_001", attempt_count=0)
        await store.create(msg)

        result = await store.mark_failed("fn_001", error="SMTP timeout")

        assert result.status == FederationNotificationStatus.FAILED
        assert result.attempt_count == 1
        assert result.last_error == "SMTP timeout"
        assert result.next_attempt_at is None

    async def test_mark_failed_with_next_attempt_sets_pending(self) -> None:
        store = InMemoryFederationNotificationStore()
        msg = _make_message(notification_id="fn_001", attempt_count=0)
        await store.create(msg)

        retry_at = _now(60)
        result = await store.mark_failed("fn_001", error="SMTP timeout", next_attempt_at=retry_at)

        assert result.status == FederationNotificationStatus.PENDING
        assert result.attempt_count == 1
        assert result.last_error == "SMTP timeout"
        assert result.next_attempt_at == retry_at

    async def test_mark_failed_increments_attempt_count(self) -> None:
        store = InMemoryFederationNotificationStore()
        msg = _make_message(notification_id="fn_001", attempt_count=2)
        await store.create(msg)

        result = await store.mark_failed("fn_001", error="Still failing")

        assert result.attempt_count == 3

    async def test_cancel(self) -> None:
        store = InMemoryFederationNotificationStore()
        msg = _make_message(notification_id="fn_001")
        await store.create(msg)

        result = await store.cancel("fn_001")

        assert result.status == FederationNotificationStatus.CANCELLED

    async def test_list_by_approval(self) -> None:
        store = InMemoryFederationNotificationStore()
        m1 = _make_message(notification_id="fn_001", approval_id="fap_001", created_at=_now(0))
        m2 = _make_message(notification_id="fn_002", approval_id="fap_002", created_at=_now(10))
        m3 = _make_message(notification_id="fn_003", approval_id="fap_001", created_at=_now(20))

        await store.create(m1)
        await store.create(m2)
        await store.create(m3)

        result = await store.list_by_approval("fap_001")
        assert len(result) == 2
        assert result[0].notification_id == "fn_001"
        assert result[1].notification_id == "fn_003"

        result = await store.list_by_approval("fap_002")
        assert len(result) == 1
        assert result[0].notification_id == "fn_002"

    async def test_mark_sent_nonexistent_raises_value_error(self) -> None:
        store = InMemoryFederationNotificationStore()
        with pytest.raises(ValueError, match="not found"):
            await store.mark_sent("fn_missing")

    async def test_mark_failed_nonexistent_raises_value_error(self) -> None:
        store = InMemoryFederationNotificationStore()
        with pytest.raises(ValueError, match="not found"):
            await store.mark_failed("fn_missing", error="x")

    async def test_cancel_nonexistent_raises_value_error(self) -> None:
        store = InMemoryFederationNotificationStore()
        with pytest.raises(ValueError, match="not found"):
            await store.cancel("fn_missing")


# ---------------------------------------------------------------------------
# SQLiteFederationNotificationStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSQLiteFederationNotificationStore:
    async def test_create_and_get(self, tmp_path: Path) -> None:
        db_path = tmp_path / "notifications.db"
        store = SQLiteFederationNotificationStore(str(db_path))
        msg = _make_message()
        await store.create(msg)

        result = await store.get("fn_001")
        assert result is not None
        assert result.notification_id == "fn_001"
        assert result.approval_id == "fap_001"
        assert result.federation_id == "fed_a"
        assert result.event_type == FederationNotificationEventType.APPROVAL_CREATED
        assert result.channel == FederationNotificationChannel.EMAIL
        assert result.recipients == ["admin@example.com"]
        assert result.subject == "Approval Request"
        assert result.body == "An approval request has been created."
        assert result.payload == {}
        assert result.status == FederationNotificationStatus.PENDING
        assert result.attempt_count == 0
        assert result.max_attempts == 3
        assert result.last_error is None
        assert result.sent_at is None
        assert result.next_attempt_at is None
        store.close()

    async def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        db_path = tmp_path / "notifications.db"
        store = SQLiteFederationNotificationStore(str(db_path))
        assert await store.get("fn_missing") is None
        store.close()

    async def test_list_pending(self, tmp_path: Path) -> None:
        db_path = tmp_path / "notifications.db"
        store = SQLiteFederationNotificationStore(str(db_path))

        m1 = _make_message(notification_id="fn_001", status=FederationNotificationStatus.PENDING, created_at=_now(0))
        m2 = _make_message(notification_id="fn_002", status=FederationNotificationStatus.SENT, created_at=_now(10))
        m3 = _make_message(notification_id="fn_003", status=FederationNotificationStatus.PENDING, created_at=_now(20))

        await store.create(m1)
        await store.create(m2)
        await store.create(m3)

        result = await store.list_pending()
        assert len(result) == 2
        assert result[0].notification_id == "fn_001"
        assert result[1].notification_id == "fn_003"
        store.close()

    async def test_list_pending_respects_limit(self, tmp_path: Path) -> None:
        db_path = tmp_path / "notifications.db"
        store = SQLiteFederationNotificationStore(str(db_path))

        for i in range(5):
            await store.create(_make_message(notification_id=f"fn_{i:03d}", created_at=_now(i)))

        result = await store.list_pending(limit=2)
        assert len(result) == 2
        store.close()

    async def test_mark_sent(self, tmp_path: Path) -> None:
        db_path = tmp_path / "notifications.db"
        store = SQLiteFederationNotificationStore(str(db_path))
        msg = _make_message(notification_id="fn_001")
        await store.create(msg)

        result = await store.mark_sent("fn_001")
        assert result.status == FederationNotificationStatus.SENT
        assert result.sent_at is not None

        # Verify persistence
        loaded = await store.get("fn_001")
        assert loaded is not None
        assert loaded.status == FederationNotificationStatus.SENT
        assert loaded.sent_at is not None
        store.close()

    async def test_mark_failed_without_next_attempt(self, tmp_path: Path) -> None:
        db_path = tmp_path / "notifications.db"
        store = SQLiteFederationNotificationStore(str(db_path))
        msg = _make_message(notification_id="fn_001", attempt_count=0)
        await store.create(msg)

        result = await store.mark_failed("fn_001", error="SMTP timeout")
        assert result.status == FederationNotificationStatus.FAILED
        assert result.attempt_count == 1
        assert result.last_error == "SMTP timeout"
        assert result.next_attempt_at is None
        store.close()

    async def test_mark_failed_with_next_attempt_sets_pending(self, tmp_path: Path) -> None:
        db_path = tmp_path / "notifications.db"
        store = SQLiteFederationNotificationStore(str(db_path))
        msg = _make_message(notification_id="fn_001", attempt_count=0)
        await store.create(msg)

        retry_at = _now(60)
        result = await store.mark_failed("fn_001", error="SMTP timeout", next_attempt_at=retry_at)
        assert result.status == FederationNotificationStatus.PENDING
        assert result.attempt_count == 1
        assert result.last_error == "SMTP timeout"
        assert result.next_attempt_at is not None
        store.close()

    async def test_cancel(self, tmp_path: Path) -> None:
        db_path = tmp_path / "notifications.db"
        store = SQLiteFederationNotificationStore(str(db_path))
        msg = _make_message(notification_id="fn_001")
        await store.create(msg)

        result = await store.cancel("fn_001")
        assert result.status == FederationNotificationStatus.CANCELLED

        loaded = await store.get("fn_001")
        assert loaded is not None
        assert loaded.status == FederationNotificationStatus.CANCELLED
        store.close()

    async def test_list_by_approval(self, tmp_path: Path) -> None:
        db_path = tmp_path / "notifications.db"
        store = SQLiteFederationNotificationStore(str(db_path))

        m1 = _make_message(notification_id="fn_001", approval_id="fap_001", created_at=_now(0))
        m2 = _make_message(notification_id="fn_002", approval_id="fap_002", created_at=_now(10))
        m3 = _make_message(notification_id="fn_003", approval_id="fap_001", created_at=_now(20))

        await store.create(m1)
        await store.create(m2)
        await store.create(m3)

        result = await store.list_by_approval("fap_001")
        assert len(result) == 2
        assert result[0].notification_id == "fn_001"
        assert result[1].notification_id == "fn_003"

        result = await store.list_by_approval("fap_002")
        assert len(result) == 1
        assert result[0].notification_id == "fn_002"
        store.close()

    async def test_mark_sent_nonexistent_raises_value_error(self, tmp_path: Path) -> None:
        db_path = tmp_path / "notifications.db"
        store = SQLiteFederationNotificationStore(str(db_path))
        with pytest.raises(ValueError, match="not found"):
            await store.mark_sent("fn_missing")
        store.close()

    async def test_mark_failed_nonexistent_raises_value_error(self, tmp_path: Path) -> None:
        db_path = tmp_path / "notifications.db"
        store = SQLiteFederationNotificationStore(str(db_path))
        with pytest.raises(ValueError, match="not found"):
            await store.mark_failed("fn_missing", error="x")
        store.close()

    async def test_cancel_nonexistent_raises_value_error(self, tmp_path: Path) -> None:
        db_path = tmp_path / "notifications.db"
        store = SQLiteFederationNotificationStore(str(db_path))
        with pytest.raises(ValueError, match="not found"):
            await store.cancel("fn_missing")
        store.close()

    async def test_persistence_across_instances(self, tmp_path: Path) -> None:
        db_path = tmp_path / "notifications.db"
        store = SQLiteFederationNotificationStore(str(db_path))
        msg = _make_message(
            notification_id="fn_001",
            payload={"key": "value"},
            recipients=["a@b.com", "c@d.com"],
        )
        await store.create(msg)
        store.close()

        # Reopen same DB
        store2 = SQLiteFederationNotificationStore(str(db_path))
        loaded = await store2.get("fn_001")

        assert loaded is not None
        assert loaded.notification_id == "fn_001"
        assert loaded.approval_id == "fap_001"
        assert loaded.federation_id == "fed_a"
        assert loaded.event_type == FederationNotificationEventType.APPROVAL_CREATED
        assert loaded.channel == FederationNotificationChannel.EMAIL
        assert loaded.recipients == ["a@b.com", "c@d.com"]
        assert loaded.payload == {"key": "value"}
        assert loaded.status == FederationNotificationStatus.PENDING
        assert loaded.attempt_count == 0
        assert loaded.max_attempts == 3
        store2.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCreateFederationNotificationStore:
    def test_create_memory(self) -> None:
        store = create_federation_notification_store("memory")
        assert isinstance(store, InMemoryFederationNotificationStore)
        assert isinstance(store, FederationNotificationStore)

    def test_create_sqlite(self, tmp_path: Path) -> None:
        db_path = tmp_path / "notifications.db"
        store = create_federation_notification_store("sqlite", str(db_path))
        assert isinstance(store, SQLiteFederationNotificationStore)
        assert isinstance(store, FederationNotificationStore)
        store.close()

    def test_unknown_type_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown federation notification store type"):
            create_federation_notification_store("redis")
