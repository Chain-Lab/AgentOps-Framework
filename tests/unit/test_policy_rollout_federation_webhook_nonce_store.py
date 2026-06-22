"""Tests for FederationWebhookNonceStore — InMemory, SQLite, and factory."""
from __future__ import annotations

import asyncio
from pathlib import Path

from agent_app.runtime.policy_rollout_federation_webhook_nonce_store import (
    FederationWebhookNonceStore,
    InMemoryFederationWebhookNonceStore,
    SQLiteFederationWebhookNonceStore,
    create_federation_webhook_nonce_store,
)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# InMemoryFederationWebhookNonceStore
# ---------------------------------------------------------------------------


class TestInMemoryFederationWebhookNonceStore:
    def test_inmemory_register_and_exists(self) -> None:
        store = InMemoryFederationWebhookNonceStore()
        _run_async(store.register("nonce-001", ttl_seconds=600))
        assert _run_async(store.exists("nonce-001")) is True

    def test_inmemory_nonce_not_exists(self) -> None:
        store = InMemoryFederationWebhookNonceStore()
        assert _run_async(store.exists("nonce-missing")) is False

    def test_inmemory_purge_expired(self) -> None:
        store = InMemoryFederationWebhookNonceStore()
        # Register with very short TTL so it expires immediately
        store.register_sync("nonce-expired", ttl_seconds=0)
        # Register a fresh one
        store.register_sync("nonce-fresh", ttl_seconds=600)

        purged = _run_async(store.purge_expired())
        assert purged >= 1
        assert _run_async(store.exists("nonce-fresh")) is True


# ---------------------------------------------------------------------------
# SQLiteFederationWebhookNonceStore
# ---------------------------------------------------------------------------


class TestSQLiteFederationWebhookNonceStore:
    def test_sqlite_register_and_exists(self, tmp_path: Path) -> None:
        db_path = tmp_path / "nonce.db"
        store = SQLiteFederationWebhookNonceStore(str(db_path))
        _run_async(store.register("nonce-001", ttl_seconds=600))
        assert _run_async(store.exists("nonce-001")) is True
        store.close()

    def test_sqlite_nonce_not_exists(self, tmp_path: Path) -> None:
        db_path = tmp_path / "nonce.db"
        store = SQLiteFederationWebhookNonceStore(str(db_path))
        assert _run_async(store.exists("nonce-missing")) is False
        store.close()

    def test_sqlite_purge_expired(self, tmp_path: Path) -> None:
        db_path = tmp_path / "nonce.db"
        store = SQLiteFederationWebhookNonceStore(str(db_path))
        # Register with very short TTL
        store.register_sync("nonce-expired", ttl_seconds=0)
        store.register_sync("nonce-fresh", ttl_seconds=600)

        purged = _run_async(store.purge_expired())
        assert purged >= 1
        assert _run_async(store.exists("nonce-fresh")) is True
        store.close()

    def test_sqlite_duplicate_nonce_raises(self, tmp_path: Path) -> None:
        db_path = tmp_path / "nonce.db"
        store = SQLiteFederationWebhookNonceStore(str(db_path))
        _run_async(store.register("nonce-dup", ttl_seconds=600))
        try:
            _run_async(store.register("nonce-dup", ttl_seconds=600))
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "already registered" in str(e)
        store.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCreateFederationWebhookNonceStore:
    def test_factory(self) -> None:
        store = create_federation_webhook_nonce_store("memory")
        assert isinstance(store, InMemoryFederationWebhookNonceStore)
        assert isinstance(store, FederationWebhookNonceStore)

        db_store = create_federation_webhook_nonce_store("sqlite", ":memory:")
        assert isinstance(db_store, SQLiteFederationWebhookNonceStore)
        assert isinstance(db_store, FederationWebhookNonceStore)
        db_store.close()

        try:
            create_federation_webhook_nonce_store("redis")
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "Unknown nonce store type" in str(e)
