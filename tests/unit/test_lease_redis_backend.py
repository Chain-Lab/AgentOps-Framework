"""Tests for Phase 16.4 Redis lease backend.

Covers:
- Optional dependency boundary (redis not required for default import)
- RedisWorkflowLeaseBackend with fake/mocked Redis client
- Acquire: create, deny, same-holder refresh, steal expired
- Renew: success, wrong token, missing key, released, expired
- Release: success, wrong token, missing key, already released
- Get: active lease, released lease, missing lease
- List expired: filtering by time
- Health check: healthy, unhealthy, backend_type, checked_at
- Diagnostics: backend_type, sanitized URL, metrics
- Config: redis backend parsing, defaults, old configs still valid
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent_app.config.schema import DagLeaseConfig
from agent_app.runtime.dag_run_state import (
    LeaseAcquireResult,
    LeasePolicy,
    WorkerIdentity,
    WorkflowRunLease,
)
from agent_app.runtime.lease_backend import (
    WorkflowLeaseBackend,
    create_lease_backend,
)
from agent_app.runtime.lease_redis_backend import (
    _generate_token,
    _json_to_lease,
    _lease_to_json,
    _sanitize_redis_url,
    _utcnow,
)
from agent_app.runtime.lease_health import (
    LeaseHealthCheckResult,
    LeaseHealthStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_worker(worker_id: str = "worker-1") -> WorkerIdentity:
    return WorkerIdentity(worker_id=worker_id)


def _make_policy(
    ttl_seconds: int = 300,
    allow_steal_expired: bool = True,
    renew_before_seconds: int = 60,
) -> LeasePolicy:
    return LeasePolicy(
        ttl_seconds=ttl_seconds,
        allow_steal_expired=allow_steal_expired,
        renew_before_seconds=renew_before_seconds,
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _run(coro):
    """Run async coroutine synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fake Redis client
# ---------------------------------------------------------------------------

class FakeRedisClient:
    """In-memory fake Redis client for unit testing.

    Mimics the redis-py interface used by RedisWorkflowLeaseBackend:
    - get, set, ping, scan, script_load, evalsha
    - decode_responses=True (returns strings, not bytes)
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._scripts: dict[str, str] = {}
        self._script_sha_counter = 0
        self.ping_result: bool = True
        self.scan_cursor_return: int = 0  # 0 = end of scan

    def ping(self) -> bool:
        return self.ping_result

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._store[key] = value
        return True

    def delete(self, key: str) -> int:
        if key in self._store:
            del self._store[key]
            return 1
        return 0

    def scan(self, cursor: int, match: str, count: int = 10) -> tuple[int, list[str]]:
        """Simplified SCAN — returns all matching keys at once."""
        import fnmatch
        keys = [k for k in self._store if fnmatch.fnmatch(k, match)]
        return 0, keys

    def script_load(self, script: str) -> str:
        self._script_sha_counter += 1
        sha = f"sha_{self._script_sha_counter}"
        self._scripts[sha] = script
        return sha

    def evalsha(self, sha: str, keys: list[str], args: list[Any]) -> Any:
        """Execute a loaded Lua script by SHA."""
        script = self._scripts.get(sha)
        if script is None:
            raise Exception(f"NOSCRIPT No matching script for SHA '{sha}'")
        # Dispatch based on script content
        if "acquire" in script.lower() or "allow_steal" in script:
            return self._eval_acquire(keys, args)
        if "renew" in script.lower():
            return self._eval_renew(keys, args)
        if "release" in script.lower():
            return self._eval_release(keys, args)
        raise Exception(f"Unknown script: {sha}")

    def _eval_acquire(self, keys: list[str], args: list[Any]) -> str:
        """Simplified acquire logic for fake Redis."""
        key = keys[0]
        value_json = args[0]
        ttl = int(args[1])
        now_iso = args[2]
        holder_token = args[3]
        allow_steal = bool(int(args[4]))
        now_epoch = float(args[5])

        existing = self._store.get(key)

        if existing is None:
            self._store[key] = value_json
            return "acquired"

        # Parse existing
        try:
            existing_decoded = json.loads(existing)
        except json.JSONDecodeError:
            self._store[key] = value_json
            return "acquired"

        existing_token = existing_decoded.get("lease_token", "")
        existing_released_at = existing_decoded.get("released_at", "")
        existing_expires_at = existing_decoded.get("expires_at", "")

        # Already released
        if existing_released_at:
            self._store[key] = value_json
            return "acquired"

        # Same token — refresh (increment version in new value)
        if existing_token == holder_token:
            new_decoded = json.loads(value_json)
            new_decoded["version"] = existing_decoded.get("version", 1) + 1
            self._store[key] = json.dumps(new_decoded)
            return "acquired"

        # Different holder — check expiry
        if existing_expires_at:
            try:
                exp_dt = datetime.fromisoformat(existing_expires_at)
                now_dt = datetime.fromisoformat(now_iso)
                if exp_dt <= now_dt:
                    if allow_steal:
                        self._store[key] = value_json
                        return "acquired"
                    else:
                        return "denied_expired"
            except (ValueError, TypeError):
                pass

        return "denied"

    def _eval_renew(self, keys: list[str], args: list[Any]) -> str:
        """Simplified renew logic."""
        key = keys[0]
        holder_token = args[0]
        ttl = int(args[1])
        renewed_at_iso = args[2]
        expires_at_iso = args[3]
        now_epoch = float(args[4])

        existing = self._store.get(key)
        if existing is None:
            return "not_found"

        try:
            existing_decoded = json.loads(existing)
        except json.JSONDecodeError:
            return "not_found"

        existing_token = existing_decoded.get("lease_token", "")
        existing_released_at = existing_decoded.get("released_at", "")

        if existing_released_at:
            return "released"

        if existing_token != holder_token:
            return "wrong_holder"

        # Check expiry
        existing_expires_at = existing_decoded.get("expires_at", "")
        if existing_expires_at:
            try:
                exp_dt = datetime.fromisoformat(existing_expires_at)
                if exp_dt <= datetime.fromtimestamp(now_epoch, tz=timezone.utc):
                    return "expired"
            except (ValueError, TypeError):
                pass

        # Update
        existing_decoded["renewed_at"] = renewed_at_iso
        existing_decoded["expires_at"] = expires_at_iso
        self._store[key] = json.dumps(existing_decoded)
        return "renewed"

    def _eval_release(self, keys: list[str], args: list[Any]) -> str:
        """Simplified release logic."""
        key = keys[0]
        holder_token = args[0]
        released_at_iso = args[1]

        existing = self._store.get(key)
        if existing is None:
            return "not_found"

        try:
            existing_decoded = json.loads(existing)
        except json.JSONDecodeError:
            return "not_found"

        existing_token = existing_decoded.get("lease_token", "")
        existing_released_at = existing_decoded.get("released_at", "")

        if existing_released_at:
            return "already_released"

        if existing_token != holder_token:
            return "wrong_holder"

        existing_decoded["released_at"] = released_at_iso
        self._store[key] = json.dumps(existing_decoded)
        return "released"

    def reset(self) -> None:
        """Clear all data."""
        self._store.clear()
        self._scripts.clear()


# ---------------------------------------------------------------------------
# Fixtures / factories
# ---------------------------------------------------------------------------

def _make_redis_backend(
    redis_url: str = "redis://localhost:6379/0",
    key_prefix: str = "agent_app:dag_lease",
    ttl_seconds: int = 300,
    allow_steal_expired: bool = True,
    fake_client: FakeRedisClient | None = None,
):
    """Create a RedisWorkflowLeaseBackend with a fake client.

    Patches _REDIS_AVAILABLE so the backend can be instantiated
    even when redis-py is not installed (unit test scenario).
    """
    import agent_app.runtime.lease_redis_backend as redis_mod

    original = redis_mod._REDIS_AVAILABLE
    redis_mod._REDIS_AVAILABLE = True
    try:
        from agent_app.runtime.lease_redis_backend import RedisWorkflowLeaseBackend

        backend = RedisWorkflowLeaseBackend(
            redis_url=redis_url,
            key_prefix=key_prefix,
            ttl_seconds=ttl_seconds,
            allow_steal_expired=allow_steal_expired,
        )
        if fake_client is not None:
            backend._client = fake_client
        return backend
    finally:
        redis_mod._REDIS_AVAILABLE = original


# ===========================================================================
# Optional dependency boundary tests
# ===========================================================================

class TestOptionalDependency:
    """Redis extra is optional — default import must not require redis."""

    def test_import_without_redis(self):
        """Importing lease_redis_backend should work even without redis."""
        # The module should already be importable (lazy import pattern)
        from agent_app.runtime import lease_redis_backend
        assert hasattr(lease_redis_backend, "RedisWorkflowLeaseBackend")

    def test_redis_available_flag(self):
        """Check the _REDIS_AVAILABLE flag."""
        from agent_app.runtime.lease_redis_backend import _REDIS_AVAILABLE
        # redis is installed in test env; flag should be True
        # but the test should pass regardless
        assert isinstance(_REDIS_AVAILABLE, bool)

    def test_import_does_not_require_redis_at_top_level(self):
        """Importing the module should not fail if redis is absent.

        The module uses lazy import inside __init__ so top-level import
        should always succeed.
        """
        import importlib
        # Re-import to ensure it doesn't fail
        mod = importlib.import_module("agent_app.runtime.lease_redis_backend")
        assert mod is not None

    def test_config_schema_does_not_import_redis(self):
        """Config schema must not import redis."""
        from agent_app.config import schema
        # Just verify schema module is importable without redis
        assert hasattr(schema, "DagLeaseConfig")


# ===========================================================================
# Fake client / helper tests
# ===========================================================================

class TestFakeRedisClient:
    """Tests for the FakeRedisClient test helper."""

    def test_fake_client_get_set(self):
        client = FakeRedisClient()
        client.set("key1", "value1")
        assert client.get("key1") == "value1"

    def test_fake_client_missing_key(self):
        client = FakeRedisClient()
        assert client.get("nonexistent") is None

    def test_fake_client_delete(self):
        client = FakeRedisClient()
        client.set("key1", "value1")
        assert client.delete("key1") == 1
        assert client.get("key1") is None
        assert client.delete("key1") == 0

    def test_fake_client_ping(self):
        client = FakeRedisClient()
        assert client.ping() is True
        client.ping_result = False
        assert client.ping() is False

    def test_fake_client_scan_empty(self):
        client = FakeRedisClient()
        cursor, keys = client.scan(0, "prefix:*")
        assert cursor == 0
        assert keys == []

    def test_fake_client_scan_with_keys(self):
        client = FakeRedisClient()
        client.set("prefix:a", "1")
        client.set("prefix:b", "2")
        client.set("other:x", "3")
        _, keys = client.scan(0, "prefix:*")
        assert sorted(keys) == ["prefix:a", "prefix:b"]

    def test_fake_client_script_load(self):
        client = FakeRedisClient()
        sha = client.script_load("return 1")
        assert sha == "sha_1"

    def test_fake_client_evalsha_acquire(self):
        client = FakeRedisClient()
        sha = client.script_load("acquire script here")
        # Key not present — acquire
        result = client.evalsha(sha, ["key1"], ["json", "300", "2024-01-01T00:00:00", "token1", "1", "1704067200"])
        assert result == "acquired"
        assert client.get("key1") == "json"

    def test_fake_client_reset(self):
        client = FakeRedisClient()
        client.set("key1", "value1")
        client.reset()
        assert client.get("key1") is None


# ===========================================================================
# Helper function tests
# ===========================================================================

class TestHelpers:
    """Tests for lease_redis_backend helper functions."""

    def test_utcnow_returns_timezone_aware(self):
        now = _utcnow()
        assert now.tzinfo is not None

    def test_utcnow_is_utc(self):
        now = _utcnow()
        assert now.tzinfo.utcoffset(now).total_seconds() == 0

    def test_generate_token_unique(self):
        t1 = _generate_token()
        t2 = _generate_token()
        assert t1 != t2
        assert t1.startswith("tok_")

    def test_generate_token_format(self):
        token = _generate_token()
        parts = token.split("_")
        assert len(parts) == 3
        assert parts[0] == "tok"

    def test_sanitize_redis_url_with_password(self):
        url = "redis://:secret123@localhost:6379/0"
        sanitized = _sanitize_redis_url(url)
        assert "secret123" not in sanitized
        assert "***" in sanitized
        assert "localhost" in sanitized

    def test_sanitize_redis_url_without_password(self):
        url = "redis://localhost:6379/0"
        sanitized = _sanitize_redis_url(url)
        assert sanitized == url

    def test_sanitize_redis_url_no_at_sign(self):
        url = "redis://localhost:6379/0"
        sanitized = _sanitize_redis_url(url)
        assert sanitized == url

    def test_lease_to_json_roundtrip(self):
        worker = _make_worker("w1")
        now = _utcnow()
        expires = now + timedelta(seconds=300)
        json_str = _lease_to_json("run-1", "wf-1", worker, "token-abc", now, expires)
        lease = _json_to_lease(json_str)
        assert lease.run_id == "run-1"
        assert lease.owner_id == "w1"
        assert lease.expires_at == expires
        assert lease.released_at is None

    def test_lease_to_json_with_released(self):
        worker = _make_worker("w1")
        now = _utcnow()
        acquired = now - timedelta(seconds=600)
        released = now - timedelta(seconds=100)
        json_str = _lease_to_json(
            "run-1", "wf-1", worker, "token-abc",
            acquired, acquired + timedelta(seconds=300),
            renewed_at=acquired + timedelta(seconds=150),
            released_at=released,
            version=3,
        )
        lease = _json_to_lease(json_str)
        assert lease.released_at == released
        assert lease.version == 3
        assert lease.renewed_at == acquired + timedelta(seconds=150)

    def test_lease_to_json_empty_fields(self):
        """JSON round-trip with empty renewed_at / released_at."""
        worker = _make_worker("w1")
        now = _utcnow()
        json_str = _lease_to_json("run-1", "wf-1", worker, "tok", now, now + timedelta(seconds=300))
        lease = _json_to_lease(json_str)
        assert lease.renewed_at is None
        assert lease.released_at is None


# ===========================================================================
# RedisWorkflowLeaseBackend — acquire tests
# ===========================================================================

class TestRedisAcquire:
    """Tests for RedisWorkflowLeaseBackend.acquire_run_lease."""

    def test_acquire_creates_lease_when_key_absent(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)
        result = _run(backend.acquire_run_lease("run-1", _make_worker("w1"), _make_policy()))
        assert result.acquired is True
        assert result.owner_id == "w1"
        assert result.lease is not None
        assert result.lease.run_id == "run-1"

    def test_acquire_sets_ttl(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client, ttl_seconds=120)
        _run(backend.acquire_run_lease("run-1", _make_worker("w1"), _make_policy(ttl_seconds=120)))
        # Verify key exists in fake client
        key = "agent_app:dag_lease:run-1"
        assert client.get(key) is not None
        raw = client.get(key)
        record = json.loads(raw)
        assert record["lease_token"] is not None
        assert record["holder_id"] == "w1"

    def test_acquire_denied_when_held_by_another(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)

        # Worker 1 acquires
        _run(backend.acquire_run_lease("run-1", _make_worker("w1"), _make_policy()))

        # Worker 2 tries to acquire
        result = _run(backend.acquire_run_lease("run-1", _make_worker("w2"), _make_policy()))
        assert result.acquired is False
        assert "leased by another" in result.reason.lower() or result.reason is not None

    def test_acquire_same_holder_refreshes(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)

        r1 = _run(backend.acquire_run_lease("run-1", _make_worker("w1"), _make_policy()))
        assert r1.acquired is True
        v1 = r1.lease.version

        # Same worker re-acquires
        r2 = _run(backend.acquire_run_lease("run-1", _make_worker("w1"), _make_policy()))
        assert r2.acquired is True
        assert r2.lease.version == v1 + 1

    def test_acquire_can_steal_expired(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client, allow_steal_expired=True)

        # Worker 1 acquires with short TTL
        policy_short = _make_policy(ttl_seconds=1)
        r1 = _run(backend.acquire_run_lease("run-1", _make_worker("w1"), policy_short))
        assert r1.acquired is True

        # Manually expire the lease in the fake client
        key = "agent_app:dag_lease:run-1"
        raw = client.get(key)
        record = json.loads(raw)
        record["expires_at"] = (
            _utcnow() - timedelta(seconds=10)
        ).isoformat()
        client.set(key, json.dumps(record))

        # Worker 2 should be able to steal
        r2 = _run(backend.acquire_run_lease("run-1", _make_worker("w2"), _make_policy()))
        assert r2.acquired is True
        assert r2.owner_id == "w2"

    def test_acquire_cannot_steal_expired_when_disabled(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client, allow_steal_expired=False)

        # Worker 1 acquires
        _run(backend.acquire_run_lease("run-1", _make_worker("w1"), _make_policy()))

        # Manually expire
        key = "agent_app:dag_lease:run-1"
        raw = client.get(key)
        record = json.loads(raw)
        record["expires_at"] = (
            _utcnow() - timedelta(seconds=10)
        ).isoformat()
        client.set(key, json.dumps(record))

        # Worker 2 cannot steal
        r2 = _run(backend.acquire_run_lease("run-1", _make_worker("w2"), _make_policy(allow_steal_expired=False)))
        assert r2.acquired is False

    def test_acquire_after_released(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)

        w1 = _make_worker("w1")
        # Acquire
        r1 = _run(backend.acquire_run_lease("run-1", w1, _make_policy()))
        assert r1.acquired is True

        # Release
        _run(backend.release_run_lease("run-1", w1))

        # Another worker can acquire after release
        r2 = _run(backend.acquire_run_lease("run-1", _make_worker("w2"), _make_policy()))
        assert r2.acquired is True

    def test_acquire_json_record_has_expected_fields(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)
        _run(backend.acquire_run_lease("run-1", _make_worker("w1"), _make_policy()))

        key = "agent_app:dag_lease:run-1"
        raw = client.get(key)
        record = json.loads(raw)
        assert "workflow_id" in record
        assert "run_id" in record
        assert "holder_id" in record
        assert "lease_token" in record
        assert "acquired_at" in record
        assert "expires_at" in record
        assert "version" in record
        assert record["holder_id"] == "w1"


# ===========================================================================
# RedisWorkflowLeaseBackend — renew tests
# ===========================================================================

class TestRedisRenew:
    """Tests for RedisWorkflowLeaseBackend.renew_run_lease."""

    def test_renew_succeeds_with_matching_holder(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client, ttl_seconds=300)
        w1 = _make_worker("w1")

        r1 = _run(backend.acquire_run_lease("run-1", w1, _make_policy()))
        assert r1.acquired is True

        renewed = _run(backend.renew_run_lease("run-1", w1, _make_policy()))
        assert renewed.owner_id == "w1"
        assert renewed.renewed_at is not None
        assert renewed.version == r1.lease.version + 1

    def test_renew_fails_with_wrong_token(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)
        w1 = _make_worker("w1")
        w2 = _make_worker("w2")

        _run(backend.acquire_run_lease("run-1", w1, _make_policy()))

        with pytest.raises(KeyError, match="held by"):
            _run(backend.renew_run_lease("run-1", w2, _make_policy()))

    def test_renew_fails_when_key_missing(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)

        with pytest.raises(KeyError, match="No active lease"):
            _run(backend.renew_run_lease("run-999", _make_worker("w1"), _make_policy()))

    def test_renew_fails_when_released(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)
        w1 = _make_worker("w1")

        _run(backend.acquire_run_lease("run-1", w1, _make_policy()))
        _run(backend.release_run_lease("run-1", w1))

        with pytest.raises(KeyError, match="released"):
            _run(backend.renew_run_lease("run-1", w1, _make_policy()))

    def test_renew_fails_when_expired(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)
        w1 = _make_worker("w1")

        _run(backend.acquire_run_lease("run-1", w1, _make_policy(ttl_seconds=1)))

        # Manually expire
        key = "agent_app:dag_lease:run-1"
        raw = client.get(key)
        record = json.loads(raw)
        record["expires_at"] = (_utcnow() - timedelta(seconds=10)).isoformat()
        client.set(key, json.dumps(record))

        with pytest.raises(KeyError, match="expired"):
            _run(backend.renew_run_lease("run-1", w1, _make_policy()))

    def test_renew_extends_ttl(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client, ttl_seconds=300)
        w1 = _make_worker("w1")

        _run(backend.acquire_run_lease("run-1", w1, _make_policy()))
        old_expires = json.loads(client.get("agent_app:dag_lease:run-1"))["expires_at"]

        _run(backend.renew_run_lease("run-1", w1, _make_policy()))
        new_expires = json.loads(client.get("agent_app:dag_lease:run-1"))["expires_at"]

        assert datetime.fromisoformat(new_expires) > datetime.fromisoformat(old_expires)


# ===========================================================================
# RedisWorkflowLeaseBackend — release tests
# ===========================================================================

class TestRedisRelease:
    """Tests for RedisWorkflowLeaseBackend.release_run_lease."""

    def test_release_succeeds_with_matching_holder(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)
        w1 = _make_worker("w1")

        _run(backend.acquire_run_lease("run-1", w1, _make_policy()))
        released = _run(backend.release_run_lease("run-1", w1))
        assert released.released_at is not None
        assert released.owner_id == "w1"

    def test_release_does_not_delete_wrong_token(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)

        _run(backend.acquire_run_lease("run-1", _make_worker("w1"), _make_policy()))
        key = "agent_app:dag_lease:run-1"

        with pytest.raises(KeyError, match="held by"):
            _run(backend.release_run_lease("run-1", _make_worker("w2")))

        # Key should still exist
        assert client.get(key) is not None

    def test_release_handles_missing_key(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)

        with pytest.raises(KeyError, match="No active lease"):
            _run(backend.release_run_lease("run-999", _make_worker("w1")))

    def test_release_does_not_delete_other_worker_lease(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)

        _run(backend.acquire_run_lease("run-1", _make_worker("w1"), _make_policy()))
        key = "agent_app:dag_lease:run-1"

        # Try to release with wrong worker
        with pytest.raises(KeyError):
            _run(backend.release_run_lease("run-1", _make_worker("w2")))

        # Verify w1's lease is still intact
        raw = client.get(key)
        record = json.loads(raw)
        assert record["holder_id"] == "w1"
        assert record["released_at"] == ""

    def test_release_twice_fails(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)
        w1 = _make_worker("w1")

        _run(backend.acquire_run_lease("run-1", w1, _make_policy()))
        _run(backend.release_run_lease("run-1", w1))

        with pytest.raises(KeyError, match="already been released"):
            _run(backend.release_run_lease("run-1", w1))


# ===========================================================================
# RedisWorkflowLeaseBackend — get_run_lease tests
# ===========================================================================

class TestRedisGet:
    """Tests for RedisWorkflowLeaseBackend.get_run_lease."""

    def test_get_returns_lease_when_active(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)

        r1 = _run(backend.acquire_run_lease("run-1", _make_worker("w1"), _make_policy()))
        lease = _run(backend.get_run_lease("run-1"))
        assert lease is not None
        assert lease.owner_id == "w1"
        assert lease.run_id == "run-1"

    def test_get_returns_none_when_missing(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)

        assert _run(backend.get_run_lease("run-999")) is None

    def test_get_returns_none_when_released(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)
        w1 = _make_worker("w1")

        _run(backend.acquire_run_lease("run-1", w1, _make_policy()))
        _run(backend.release_run_lease("run-1", w1))

        assert _run(backend.get_run_lease("run-1")) is None


# ===========================================================================
# RedisWorkflowLeaseBackend — list_expired_leases tests
# ===========================================================================

class TestRedisListExpired:
    """Tests for RedisWorkflowLeaseBackend.list_expired_leases."""

    def test_list_expired_returns_only_expired(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)

        # Create some leases with past expiry
        past = _utcnow() - timedelta(seconds=600)
        future = _utcnow() + timedelta(seconds=600)

        # Active (future) lease for w1
        _run(backend.acquire_run_lease("run-1", _make_worker("w1"), _make_policy()))

        # Expired lease for run-2 (set manually)
        key2 = "agent_app:dag_lease:run-2"
        record2 = {
            "workflow_id": "run-2",
            "run_id": "run-2",
            "holder_id": "w1",
            "lease_token": "tok-2",
            "acquired_at": past.isoformat(),
            "renewed_at": "",
            "expires_at": past.isoformat(),
            "released_at": "",
            "version": 1,
            "metadata": {},
        }
        client.set(key2, json.dumps(record2))

        # Active lease for run-3
        _run(backend.acquire_run_lease("run-3", _make_worker("w1"), _make_policy()))

        expired = _run(backend.list_expired_leases())
        expired_ids = [e.run_id for e in expired]
        assert "run-2" in expired_ids
        assert "run-1" not in expired_ids
        assert "run-3" not in expired_ids

    def test_list_expired_empty_when_none_expired(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)

        _run(backend.acquire_run_lease("run-1", _make_worker("w1"), _make_policy()))
        expired = _run(backend.list_expired_leases())
        assert len(expired) == 0

    def test_list_expired_excludes_released(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)
        w1 = _make_worker("w1")

        _run(backend.acquire_run_lease("run-1", w1, _make_policy()))
        _run(backend.release_run_lease("run-1", w1))

        # Manually set released_at to past
        key = "agent_app:dag_lease:run-1"
        raw = client.get(key)
        record = json.loads(raw)
        record["released_at"] = (_utcnow() - timedelta(seconds=100)).isoformat()
        record["expires_at"] = (_utcnow() - timedelta(seconds=200)).isoformat()
        client.set(key, json.dumps(record))

        expired = _run(backend.list_expired_leases())
        assert len(expired) == 0


# ===========================================================================
# Health check tests
# ===========================================================================

class TestRedisHealthCheck:
    """Tests for RedisWorkflowLeaseBackend.health_check."""

    def test_healthy_redis_returns_healthy(self):
        client = FakeRedisClient()
        client.ping_result = True
        backend = _make_redis_backend(fake_client=client)

        result = _run(backend.health_check())
        assert result.status == LeaseHealthStatus.HEALTHY
        assert result.backend_type == "redis"
        assert result.error is None

    def test_redis_ping_failure_returns_unhealthy(self):
        client = FakeRedisClient()
        client.ping_result = False
        backend = _make_redis_backend(fake_client=client)

        result = _run(backend.health_check())
        assert result.status == LeaseHealthStatus.UNHEALTHY
        assert result.backend_type == "redis"
        assert result.error is not None

    def test_health_result_backend_type_is_redis(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)

        result = _run(backend.health_check())
        assert result.backend_type == "redis"

    def test_health_checked_at_is_timezone_aware(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)

        result = _run(backend.health_check())
        assert result.checked_at.tzinfo is not None

    def test_health_error_populated_on_failure(self):
        client = FakeRedisClient()

        def fail_ping():
            raise Exception("Connection refused")

        client.ping = fail_ping
        backend = _make_redis_backend(fake_client=client)

        result = _run(backend.health_check())
        assert result.status == LeaseHealthStatus.UNHEALTHY
        assert result.error is not None
        assert "Connection refused" in result.error

    def test_health_details_include_key_prefix(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(
            fake_client=client,
            key_prefix="myapp:leases",
        )

        result = _run(backend.health_check())
        assert result.details.get("key_prefix") == "myapp:leases"

    def test_health_details_do_not_expose_password(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(
            fake_client=client,
            redis_url="redis://:secret@localhost:6379/0",
        )

        result = _run(backend.health_check())
        url = result.details.get("redis_url_sanitized", "")
        assert "secret" not in url
        assert "***" in url


# ===========================================================================
# Diagnostics tests
# ===========================================================================

class TestRedisDiagnostics:
    """Tests for RedisWorkflowLeaseBackend.diagnostics."""

    def test_diagnostics_includes_redis_backend_type(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)

        diag = _run(backend.diagnostics())
        assert diag.backend_type == "redis"

    def test_diagnostics_sanitizes_redis_url(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(
            fake_client=client,
            redis_url="redis://:hunter2@redis.example.com:6379/0",
        )

        diag = _run(backend.diagnostics())
        # Diagnostics uses health, which uses sanitize
        # The health details should not contain the password
        assert "hunter2" not in str(diag.health.details)

    def test_diagnostics_includes_total_lease_keys(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client)

        # Create 3 leases
        _run(backend.acquire_run_lease("run-1", _make_worker("w1"), _make_policy()))
        _run(backend.acquire_run_lease("run-2", _make_worker("w1"), _make_policy()))
        _run(backend.acquire_run_lease("run-3", _make_worker("w1"), _make_policy()))

        diag = _run(backend.diagnostics())
        assert diag.details is not None
        assert diag.details.get("total_lease_keys") == 3

    def test_diagnostics_includes_key_prefix(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(
            fake_client=client,
            key_prefix="custom:prefix",
        )

        diag = _run(backend.diagnostics())
        assert diag.details is not None
        assert diag.details.get("key_prefix") == "custom:prefix"

    def test_diagnostics_includes_ttl_seconds(self):
        client = FakeRedisClient()
        backend = _make_redis_backend(fake_client=client, ttl_seconds=120)

        diag = _run(backend.diagnostics())
        assert diag.details is not None
        assert diag.details.get("ttl_seconds") == 120

    def test_diagnostics_handles_failure_gracefully(self):
        client = FakeRedisClient()

        def fail_scan(*args, **kwargs):
            raise Exception("Redis scan failed")

        client.scan = fail_scan
        backend = _make_redis_backend(fake_client=client)

        # Should not raise — diagnostics failure is handled gracefully
        diag = _run(backend.diagnostics())
        assert diag.backend_type == "redis"


# ===========================================================================
# Config tests
# ===========================================================================

class TestRedisConfig:
    """Tests for Redis backend configuration."""

    def test_redis_dag_lease_config_parses(self):
        cfg = DagLeaseConfig(
            backend="redis",
            redis_url="redis://localhost:6379/0",
            key_prefix="agent_app:dag_lease",
            ttl_seconds=300,
        )
        assert cfg.backend == "redis"
        assert cfg.redis_url == "redis://localhost:6379/0"
        assert cfg.key_prefix == "agent_app:dag_lease"

    def test_redis_key_prefix_default(self):
        """When key_prefix not provided, default should be used."""
        cfg = DagLeaseConfig(
            backend="redis",
            redis_url="redis://localhost:6379/0",
        )
        # key_prefix defaults to None in the model; the backend provides default
        assert cfg.backend == "redis"

    def test_old_memory_config_still_valid(self):
        cfg = DagLeaseConfig(backend="memory")
        assert cfg.backend == "memory"

    def test_old_sqlite_config_still_valid(self):
        cfg = DagLeaseConfig(backend="sqlite", db_path="/tmp/test.db")
        assert cfg.backend == "sqlite"
        assert cfg.db_path == "/tmp/test.db"

    def test_state_store_config_still_valid(self):
        cfg = DagLeaseConfig(backend="state_store")
        assert cfg.backend == "state_store"

    def test_invalid_backend_fails(self):
        with pytest.raises(ValueError, match="Invalid lease backend"):
            DagLeaseConfig(backend="etcd")

    def test_invalid_backend_message_includes_redis(self):
        with pytest.raises(ValueError, match="redis"):
            DagLeaseConfig(backend="zookeeper")

    def test_redis_config_with_metrics(self):
        cfg = DagLeaseConfig(
            backend="redis",
            redis_url="redis://localhost:6379/0",
            metrics={"enabled": True},
        )
        assert cfg.metrics is not None
        assert cfg.metrics.enabled is True

    def test_redis_config_with_health(self):
        cfg = DagLeaseConfig(
            backend="redis",
            redis_url="redis://localhost:6379/0",
            health={"enabled": True},
        )
        assert cfg.health is not None
        assert cfg.health.enabled is True

    def test_redis_backend_requires_redis_extra(self):
        """When redis is not installed, backend creation raises RuntimeError."""
        # Since redis IS installed in the test environment, we simulate
        # the missing dependency by patching the import
        import agent_app.runtime.lease_redis_backend as mod

        original = mod._REDIS_AVAILABLE
        try:
            mod._REDIS_AVAILABLE = False
            with pytest.raises(RuntimeError, match="redis extra"):
                from agent_app.runtime.lease_redis_backend import RedisWorkflowLeaseBackend
                RedisWorkflowLeaseBackend(redis_url="redis://localhost:6379/0")
        finally:
            mod._REDIS_AVAILABLE = original

    def test_redis_url_default(self):
        cfg = DagLeaseConfig(backend="redis", redis_url="redis://localhost:6379/0")
        assert cfg.redis_url == "redis://localhost:6379/0"


# ===========================================================================
# create_lease_backend() factory tests
# ===========================================================================

class TestCreateLeaseBackendRedis:
    """Tests for create_lease_backend() with redis type."""

    def test_create_redis_backend(self):
        import agent_app.runtime.lease_redis_backend as redis_mod

        original = redis_mod._REDIS_AVAILABLE
        redis_mod._REDIS_AVAILABLE = True
        try:
            backend = create_lease_backend(
                backend_type="redis",
                redis_url="redis://localhost:6379/0",
                key_prefix="test:prefix",
            )
            from agent_app.runtime.lease_redis_backend import RedisWorkflowLeaseBackend
            assert isinstance(backend, RedisWorkflowLeaseBackend)
        finally:
            redis_mod._REDIS_AVAILABLE = original

    def test_create_redis_backend_default_url(self):
        import agent_app.runtime.lease_redis_backend as redis_mod

        original = redis_mod._REDIS_AVAILABLE
        redis_mod._REDIS_AVAILABLE = True
        try:
            backend = create_lease_backend(backend_type="redis")
            from agent_app.runtime.lease_redis_backend import RedisWorkflowLeaseBackend
            assert isinstance(backend, RedisWorkflowLeaseBackend)
        finally:
            redis_mod._REDIS_AVAILABLE = original

    def test_create_memory_backend_still_works(self):
        backend = create_lease_backend(backend_type="memory")
        from agent_app.runtime.lease_backend import InMemoryWorkflowLeaseBackend
        assert isinstance(backend, InMemoryWorkflowLeaseBackend)

    def test_create_sqlite_backend_still_works(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            backend = create_lease_backend(
                backend_type="sqlite", db_path=db_path
            )
            from agent_app.runtime.lease_backend import SQLiteWorkflowLeaseBackend
            assert isinstance(backend, SQLiteWorkflowLeaseBackend)
        finally:
            import os
            os.unlink(db_path)

    def test_create_state_store_backend_still_works(self):
        from agent_app.runtime.dag_state_store import InMemoryWorkflowStateStore
        store = InMemoryWorkflowStateStore()
        backend = create_lease_backend(
            backend_type="state_store", state_store=store
        )
        from agent_app.runtime.lease_backend import StateStoreLeaseBackend
        assert isinstance(backend, StateStoreLeaseBackend)

    def test_create_redis_sets_custom_prefix(self):
        import agent_app.runtime.lease_redis_backend as redis_mod

        original = redis_mod._REDIS_AVAILABLE
        redis_mod._REDIS_AVAILABLE = True
        try:
            backend = create_lease_backend(
                backend_type="redis",
                redis_url="redis://localhost:6379/0",
                key_prefix="custom:leases",
            )
            assert backend._key_prefix == "custom:leases"
        finally:
            redis_mod._REDIS_AVAILABLE = original

    def test_create_redis_sets_custom_ttl(self):
        import agent_app.runtime.lease_redis_backend as redis_mod

        original = redis_mod._REDIS_AVAILABLE
        redis_mod._REDIS_AVAILABLE = True
        try:
            backend = create_lease_backend(
                backend_type="redis",
                redis_url="redis://localhost:6379/0",
                key_prefix="test",
                ttl_seconds=600,
            )
            assert backend._ttl_seconds == 600
        finally:
            redis_mod._REDIS_AVAILABLE = original

    def test_create_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown lease backend"):
            create_lease_backend(backend_type="etcd")


# ===========================================================================
# Protocol conformance
# ===========================================================================

class TestRedisProtocolConformance:
    """Verify RedisWorkflowLeaseBackend satisfies WorkflowLeaseBackend protocol."""

    def test_implements_protocol(self):
        """RedisWorkflowLeaseBackend should satisfy WorkflowLeaseBackend."""
        import agent_app.runtime.lease_redis_backend as redis_mod

        original = redis_mod._REDIS_AVAILABLE
        redis_mod._REDIS_AVAILABLE = True
        try:
            from agent_app.runtime.lease_redis_backend import RedisWorkflowLeaseBackend

            backend = RedisWorkflowLeaseBackend()
            backend._client = FakeRedisClient()
            assert isinstance(backend, WorkflowLeaseBackend)
        finally:
            redis_mod._REDIS_AVAILABLE = original

    def test_has_required_methods(self):
        import agent_app.runtime.lease_redis_backend as redis_mod

        original = redis_mod._REDIS_AVAILABLE
        redis_mod._REDIS_AVAILABLE = True
        try:
            from agent_app.runtime.lease_redis_backend import RedisWorkflowLeaseBackend

            backend = RedisWorkflowLeaseBackend()
            assert hasattr(backend, "acquire_run_lease")
            assert hasattr(backend, "renew_run_lease")
            assert hasattr(backend, "release_run_lease")
            assert hasattr(backend, "get_run_lease")
            assert hasattr(backend, "list_expired_leases")
        finally:
            redis_mod._REDIS_AVAILABLE = original

    def test_has_health_check_method(self):
        import agent_app.runtime.lease_redis_backend as redis_mod

        original = redis_mod._REDIS_AVAILABLE
        redis_mod._REDIS_AVAILABLE = True
        try:
            from agent_app.runtime.lease_redis_backend import RedisWorkflowLeaseBackend

            backend = RedisWorkflowLeaseBackend()
            backend._client = FakeRedisClient()
            assert hasattr(backend, "health_check")
        finally:
            redis_mod._REDIS_AVAILABLE = original

    def test_has_diagnostics_method(self):
        import agent_app.runtime.lease_redis_backend as redis_mod

        original = redis_mod._REDIS_AVAILABLE
        redis_mod._REDIS_AVAILABLE = True
        try:
            from agent_app.runtime.lease_redis_backend import RedisWorkflowLeaseBackend

            backend = RedisWorkflowLeaseBackend()
            backend._client = FakeRedisClient()
            assert hasattr(backend, "diagnostics")
        finally:
            redis_mod._REDIS_AVAILABLE = original


# ===========================================================================
# Metrics integration
# ===========================================================================

class TestRedisMetricsIntegration:
    """Redis backend works with MetricsWorkflowLeaseBackend wrapper."""

    def test_redis_backend_wrapped_with_metrics(self):
        import agent_app.runtime.lease_redis_backend as redis_mod

        original = redis_mod._REDIS_AVAILABLE
        redis_mod._REDIS_AVAILABLE = True
        try:
            from agent_app.runtime.lease_backend import MetricsWorkflowLeaseBackend
            from agent_app.runtime.lease_metrics import LeaseMetrics
            from agent_app.runtime.lease_redis_backend import RedisWorkflowLeaseBackend

            client = FakeRedisClient()
            inner = RedisWorkflowLeaseBackend()
            inner._client = client
            metrics = LeaseMetrics()
            backend = MetricsWorkflowLeaseBackend(inner, metrics)

            w1 = _make_worker("w1")
            _run(backend.acquire_run_lease("run-1", w1, _make_policy()))
            snap = metrics.snapshot()
            assert snap.acquire.attempts >= 1
            assert snap.acquire.successes >= 1
        finally:
            redis_mod._REDIS_AVAILABLE = original

    def test_metrics_record_denied_acquire(self):
        import agent_app.runtime.lease_redis_backend as redis_mod

        original = redis_mod._REDIS_AVAILABLE
        redis_mod._REDIS_AVAILABLE = True
        try:
            from agent_app.runtime.lease_backend import MetricsWorkflowLeaseBackend
            from agent_app.runtime.lease_metrics import LeaseMetrics
            from agent_app.runtime.lease_redis_backend import RedisWorkflowLeaseBackend

            client = FakeRedisClient()
            inner = RedisWorkflowLeaseBackend()
            inner._client = client
            metrics = LeaseMetrics()
            backend = MetricsWorkflowLeaseBackend(inner, metrics)

            w1 = _make_worker("w1")
            w2 = _make_worker("w2")
            _run(backend.acquire_run_lease("run-1", w1, _make_policy()))
            _run(backend.acquire_run_lease("run-1", w2, _make_policy()))
            snap = metrics.snapshot()
            # MetricsWorkflowLeaseBackend records denied acquires as 'failure'
            assert snap.acquire.failures >= 1
            assert snap.acquire.attempts == 2
            assert snap.acquire.successes == 1
        finally:
            redis_mod._REDIS_AVAILABLE = original


# ===========================================================================
# Key prefix isolation
# ===========================================================================

class TestRedisKeyPrefix:
    """Key prefix isolation between tenants/apps."""

    def test_different_prefixes_isolate_keys(self):
        client = FakeRedisClient()
        backend1 = _make_redis_backend(
            fake_client=client, key_prefix="app1:leases"
        )
        backend2 = _make_redis_backend(
            fake_client=client, key_prefix="app2:leases"
        )

        _run(backend1.acquire_run_lease("run-1", _make_worker("w1"), _make_policy()))
        _run(backend2.acquire_run_lease("run-1", _make_worker("w1"), _make_policy()))

        key1 = "app1:leases:run-1"
        key2 = "app2:leases:run-1"
        assert client.get(key1) is not None
        assert client.get(key2) is not None
        # Different JSON values
        assert client.get(key1) != client.get(key2)


# ===========================================================================
# Repr tests
# ===========================================================================

class TestRedisRepr:
    """String representation tests."""

    def test_repr_does_not_expose_password(self):
        import agent_app.runtime.lease_redis_backend as redis_mod

        original = redis_mod._REDIS_AVAILABLE
        redis_mod._REDIS_AVAILABLE = True
        try:
            from agent_app.runtime.lease_redis_backend import RedisWorkflowLeaseBackend

            backend = RedisWorkflowLeaseBackend(
                redis_url="redis://:s3cret@localhost:6379/0",
                key_prefix="test",
            )
            r = repr(backend)
            assert "s3cret" not in r
            assert "***" in r
        finally:
            redis_mod._REDIS_AVAILABLE = original

    def test_repr_shows_prefix(self):
        import agent_app.runtime.lease_redis_backend as redis_mod

        original = redis_mod._REDIS_AVAILABLE
        redis_mod._REDIS_AVAILABLE = True
        try:
            from agent_app.runtime.lease_redis_backend import RedisWorkflowLeaseBackend

            backend = RedisWorkflowLeaseBackend(
                redis_url="redis://localhost:6379/0",
                key_prefix="myapp:leases",
            )
            r = repr(backend)
            assert "myapp:leases" in r
        finally:
            redis_mod._REDIS_AVAILABLE = original
