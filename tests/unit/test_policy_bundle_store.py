"""Tests for PolicyBundle model and InMemoryPolicyBundleStore."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_app.governance.policy_bundle import (
    InMemoryPolicyBundleStore,
    PolicyBundle,
    PolicyBundleStatus,
    compute_config_hash,
)


def _make_bundle(
    bundle_id: str = "pb_test123",
    name: str = "test-bundle",
    version: str = "1.0.0",
    status: str = PolicyBundleStatus.DRAFT,
    config_path: str | None = None,
    config_hash: str = "",
    description: str | None = None,
    created_by: str | None = None,
) -> PolicyBundle:
    """Create a test PolicyBundle."""
    return PolicyBundle(
        bundle_id=bundle_id,
        name=name,
        version=version,
        status=status,
        config_path=config_path,
        config_hash=config_hash or compute_config_hash("test content"),
        description=description,
        created_by=created_by,
        created_at=datetime.now(timezone.utc),
    )


class TestComputeConfigHash:
    """Tests for compute_config_hash."""

    def test_same_content_same_hash(self):
        """Same content produces same hash."""
        h1 = compute_config_hash("policy rules here")
        h2 = compute_config_hash("policy rules here")
        assert h1 == h2

    def test_different_content_different_hash(self):
        """Different content produces different hashes."""
        h1 = compute_config_hash("policy rules v1")
        h2 = compute_config_hash("policy rules v2")
        assert h1 != h2

    def test_hash_is_sha256(self):
        """Hash is a 64-char hex SHA-256 digest."""
        h = compute_config_hash("test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_empty_string_hash(self):
        """Empty string has a valid hash."""
        h = compute_config_hash("")
        assert len(h) == 64


class TestPolicyBundleModel:
    """Tests for PolicyBundle Pydantic model."""

    def test_bundle_id_prefix(self):
        """Bundle ID uses pb_ prefix."""
        b = _make_bundle(bundle_id="pb_abc123")
        assert b.bundle_id == "pb_abc123"

    def test_default_status_draft(self):
        """Default status is draft."""
        b = _make_bundle()
        assert b.status == PolicyBundleStatus.DRAFT

    def test_timezone_aware_datetime(self):
        """created_at is timezone-aware."""
        b = _make_bundle()
        assert b.created_at.tzinfo is not None

    def test_metadata_default_empty(self):
        """metadata defaults to empty dict."""
        b = _make_bundle()
        assert b.metadata == {}

    def test_activated_at_none_by_default(self):
        """activated_at is None for new bundles."""
        b = _make_bundle()
        assert b.activated_at is None

    def test_model_dump_serializable(self):
        """Bundle can be serialized to dict."""
        b = _make_bundle()
        data = b.model_dump(mode="json")
        assert "bundle_id" in data
        assert data["status"] == "draft"


class TestInMemoryPolicyBundleStore:
    """Tests for InMemoryPolicyBundleStore."""

    async def test_create_and_get(self):
        """Create and retrieve a bundle."""
        store = InMemoryPolicyBundleStore()
        bundle = _make_bundle("pb_1")
        saved = await store.create(bundle)
        assert saved.bundle_id == "pb_1"

        fetched = await store.get("pb_1")
        assert fetched is not None
        assert fetched.name == "test-bundle"

    async def test_get_missing_returns_none(self):
        """Getting a non-existent bundle returns None."""
        store = InMemoryPolicyBundleStore()
        result = await store.get("pb_nonexistent")
        assert result is None

    async def test_list_empty(self):
        """List returns empty list for new store."""
        store = InMemoryPolicyBundleStore()
        bundles = await store.list()
        assert bundles == []

    async def test_list_returns_bundles_desc(self):
        """List returns bundles sorted by created_at descending."""
        store = InMemoryPolicyBundleStore()
        b1 = _make_bundle("pb_1")
        b2 = _make_bundle("pb_2")
        b3 = _make_bundle("pb_3")
        import asyncio
        await store.create(b1)
        await asyncio.sleep(0.01)
        await store.create(b2)
        await asyncio.sleep(0.01)
        await store.create(b3)

        bundles = await store.list()
        assert len(bundles) == 3
        # Most recent first
        assert bundles[0].bundle_id == "pb_3"
        assert bundles[1].bundle_id == "pb_2"
        assert bundles[2].bundle_id == "pb_1"

    async def test_list_with_limit(self):
        """List respects limit parameter."""
        store = InMemoryPolicyBundleStore()
        for i in range(5):
            await store.create(_make_bundle(f"pb_{i}"))
        bundles = await store.list(limit=2)
        assert len(bundles) == 2

    async def test_get_active_empty(self):
        """get_active returns None when no active bundle."""
        store = InMemoryPolicyBundleStore()
        result = await store.get_active()
        assert result is None

    async def test_activate_sets_active_bundle(self):
        """Activate a bundle and get_active returns it."""
        store = InMemoryPolicyBundleStore()
        bundle = _make_bundle("pb_active", status=PolicyBundleStatus.DRAFT)
        await store.create(bundle)

        activated = await store.activate("pb_active")
        assert activated.status == PolicyBundleStatus.ACTIVE
        assert activated.activated_at is not None

        active = await store.get_active()
        assert active is not None
        assert active.bundle_id == "pb_active"

    async def test_activate_archives_previous_active(self):
        """Activating a new bundle archives the previous active one."""
        store = InMemoryPolicyBundleStore()
        b1 = _make_bundle("pb_1", status=PolicyBundleStatus.DRAFT)
        b2 = _make_bundle("pb_2", status=PolicyBundleStatus.DRAFT)
        await store.create(b1)
        await store.create(b2)

        # Activate b1
        await store.activate("pb_1")
        # Activate b2 — should archive b1
        await store.activate("pb_2")

        b1_fetched = await store.get("pb_1")
        assert b1_fetched.status == PolicyBundleStatus.ARCHIVED
        assert b1_fetched.archived_at is not None

        b2_fetched = await store.get("pb_2")
        assert b2_fetched.status == PolicyBundleStatus.ACTIVE

    async def test_archive_bundle(self):
        """Archive a bundle directly."""
        store = InMemoryPolicyBundleStore()
        bundle = _make_bundle("pb_arch", status=PolicyBundleStatus.ACTIVE)
        await store.create(bundle)

        archived = await store.archive("pb_arch")
        assert archived.status == PolicyBundleStatus.ARCHIVED
        assert archived.archived_at is not None

    async def test_activate_missing_bundle_raises(self):
        """Activating a non-existent bundle raises KeyError."""
        store = InMemoryPolicyBundleStore()
        with pytest.raises(KeyError, match="not found"):
            await store.activate("pb_nonexistent")

    async def test_archive_missing_bundle_raises(self):
        """Archiving a non-existent bundle raises KeyError."""
        store = InMemoryPolicyBundleStore()
        with pytest.raises(KeyError, match="not found"):
            await store.archive("pb_nonexistent")

    async def test_create_overwrites_existing(self):
        """Creating with existing ID overwrites."""
        store = InMemoryPolicyBundleStore()
        b1 = _make_bundle("pb_1", name="original")
        await store.create(b1)
        b2 = _make_bundle("pb_1", name="updated")
        await store.create(b2)

        fetched = await store.get("pb_1")
        assert fetched.name == "updated"
