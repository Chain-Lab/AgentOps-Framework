"""Redis lease backend — distributed lease coordination via Redis.

Phase 16.4: Provides a ``RedisWorkflowLeaseBackend`` that implements the
``WorkflowLeaseBackend`` protocol using Redis as the coordination store.
This enables cross-process / cross-worker lease coordination.

Redis is an optional dependency.  The default installation does NOT require
redis-py.  Install with::

    pip install -e ".[redis]"

This is NOT a distributed lock service, NOT exactly-once, and does NOT
provide self-healing recovery.  It is a best-effort coordination layer
that uses Redis TTL for automatic lease expiry.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from agent_app.runtime.dag_run_state import (
    LeaseAcquireResult,
    LeasePolicy,
    WorkerIdentity,
    WorkflowRunLease,
)
from agent_app.runtime.lease_backend import WorkflowLeaseBackend

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional redis import
# ---------------------------------------------------------------------------

try:
    import redis as _redis_pkg  # type: ignore[import-untyped]

    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False

# ---------------------------------------------------------------------------
# Lua scripts (loaded into Redis once per connection)
# ---------------------------------------------------------------------------

_ACQUIRE_SCRIPT = """
local key = KEYS[1]
local value_json = ARGV[1]
local ttl_seconds = tonumber(ARGV[2])
local now_iso = ARGV[3]
local holder_token = ARGV[4]

local existing = redis.call('GET', key)
if existing == false then
    -- Key does not exist: create lease
    redis.call('SET', key, value_json, 'EX', ttl_seconds)
    return 'acquired'
end

-- Parse existing value
local existing_decoded = cjson.decode(existing)
local existing_token = existing_decoded.lease_token or ''
local existing_expires_at = existing_decoded.expires_at or ''
local existing_released_at = existing_decoded.released_at or ''

-- Already released: allow new acquire
if existing_released_at ~= '' and existing_released_at ~= nil then
    redis.call('SET', key, value_json, 'EX', ttl_seconds)
    return 'acquired'
end

-- Compare tokens for same-holder refresh
if existing_token == holder_token then
    redis.call('SET', key, value_json, 'EX', ttl_seconds)
    return 'acquired'
end

-- Different holder: check expiry using stored timestamp
if existing_expires_at ~= '' and existing_expires_at ~= nil then
    local exp_ts = 0
    -- Parse ISO timestamp to epoch (basic split approach)
    local y,mo,d,h,mi,s = existing_expires_at:match('(%d+)%D(%d+)%D(%d+)T(%d+)%D(%d+)%D(%d+)')
    if y then
        exp_ts = os.time({year=tonumber(y), month=tonumber(mo), day=tonumber(d),
                          hour=tonumber(h), min=tonumber(mi), sec=tonumber(s)})
    end
    local allow_steal = tonumber(ARGV[5])
    if now_iso ~= '' then
        local ny,nmo,nd,nh,nmi,ns = now_iso:match('(%d+)%D(%d+)%D(%d+)T(%d+)%D(%d+)%D(%d+)')
        if ny then
            local now_ts = os.time({year=tonumber(ny), month=tonumber(nmo), day=tonumber(nd),
                                    hour=tonumber(nh), min=tonumber(nmi), sec=tonumber(ns)})
            if exp_ts < now_ts then
                -- Expired
                if allow_steal == 1 then
                    redis.call('SET', key, value_json, 'EX', ttl_seconds)
                    return 'acquired'
                else
                    return 'denied_expired'
                end
            end
        end
    end
    if exp_ts < tonumber(ARGV[6]) then
        if allow_steal == 1 then
            redis.call('SET', key, value_json, 'EX', ttl_seconds)
            return 'acquired'
        else
            return 'denied_expired'
        end
    end
end

-- Active lease held by different holder
return 'denied'
"""

_RENEW_SCRIPT = """
local key = KEYS[1]
local holder_token = ARGV[1]
local ttl_seconds = tonumber(ARGV[2])
local renewed_at_iso = ARGV[3]
local expires_at_iso = ARGV[4]
local now_epoch = tonumber(ARGV[5])

local existing = redis.call('GET', key)
if existing == false then
    return 'not_found'
end

local existing_decoded = cjson.decode(existing)
local existing_token = existing_decoded.lease_token or ''
local existing_released_at = existing_decoded.released_at or ''

-- Check release
if existing_released_at ~= '' and existing_released_at ~= nil then
    return 'released'
end

-- Check token match
if existing_token ~= holder_token then
    return 'wrong_holder'
end

-- Check expiry
local existing_expires_at = existing_decoded.expires_at or ''
if existing_expires_at ~= '' and existing_expires_at ~= nil then
    local y,mo,d,h,mi,s = existing_expires_at:match('(%d+)%D(%d+)%D(%d+)T(%d+)%D(%d+)%D(%d+)')
    if y then
        local exp_ts = os.time({year=tonumber(y), month=tonumber(mo), day=tonumber(d),
                                hour=tonumber(h), min=tonumber(mi), sec=tonumber(s)})
        if exp_ts < now_epoch then
            return 'expired'
        end
    end
end

-- Update the value
existing_decoded.renewed_at = renewed_at_iso
existing_decoded.expires_at = expires_at_iso
redis.call('SET', key, cjson.encode(existing_decoded), 'EX', ttl_seconds)
return 'renewed'
"""

_RELEASE_SCRIPT = """
local key = KEYS[1]
local holder_token = ARGV[1]

local existing = redis.call('GET', key)
if existing == false then
    return 'not_found'
end

local existing_decoded = cjson.decode(existing)
local existing_token = existing_decoded.lease_token or ''
local existing_released_at = existing_decoded.released_at or ''

-- Check if already released
if existing_released_at ~= '' and existing_released_at ~= nil then
    return 'already_released'
end

-- Check token match
if existing_token ~= holder_token then
    return 'wrong_holder'
end

-- Mark as released (set released_at instead of deleting, for diagnostics)
existing_decoded.released_at = ARGV[2]
redis.call('SET', key, cjson.encode(existing_decoded), 'EX', 60)
return 'released'
"""

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _generate_token() -> str:
    """Generate a unique lease token."""
    return f"tok_{int(time.time() * 1000000)}_{id(object())}"


def _make_lease_key(
    prefix: str,
    workflow_id: str,
    run_id: str,
) -> str:
    """Build the Redis key for a lease."""
    return f"{prefix}:{workflow_id}:{run_id}"


def _lease_to_json(
    run_id: str,
    workflow_id: str,
    worker: WorkerIdentity,
    lease_token: str,
    acquired_at: datetime,
    expires_at: datetime,
    renewed_at: datetime | None = None,
    released_at: datetime | None = None,
    version: int = 1,
) -> str:
    """Serialize a lease record to JSON for Redis storage."""
    record = {
        "workflow_id": workflow_id,
        "run_id": run_id,
        "holder_id": worker.worker_id,
        "lease_token": lease_token,
        "acquired_at": acquired_at.isoformat(),
        "renewed_at": renewed_at.isoformat() if renewed_at else "",
        "expires_at": expires_at.isoformat(),
        "released_at": released_at.isoformat() if released_at else "",
        "version": version,
        "metadata": {},
    }
    return json.dumps(record)


def _json_to_lease(data: str) -> WorkflowRunLease:
    """Deserialize a JSON lease record from Redis."""
    record = json.loads(data)
    return WorkflowRunLease(
        run_id=record["run_id"],
        owner_id=record["holder_id"],
        acquired_at=datetime.fromisoformat(record["acquired_at"]),
        expires_at=datetime.fromisoformat(record["expires_at"]),
        renewed_at=(
            datetime.fromisoformat(record["renewed_at"])
            if record.get("renewed_at")
            else None
        ),
        released_at=(
            datetime.fromisoformat(record["released_at"])
            if record.get("released_at")
            else None
        ),
        version=record.get("version", 1),
    )


def _sanitize_redis_url(url: str) -> str:
    """Remove password from Redis URL for safe logging."""
    try:
        # redis://:password@host:port/db
        if "@" in url:
            return url.split("@")[0].split("://")[0] + "://***@" + url.split("@")[1]
        return url
    except Exception:
        return "redis://***"


# ---------------------------------------------------------------------------
# Redis lease backend
# ---------------------------------------------------------------------------


class RedisWorkflowLeaseBackend:
    """Redis-backed workflow lease backend.

    Implements the ``WorkflowLeaseBackend`` protocol using Redis for
    distributed lease coordination.  Uses Lua scripts for atomic
    acquire / renew / release operations.

    Redis must be installed as an optional extra::

        pip install -e ".[redis]"

    Usage::

        backend = RedisWorkflowLeaseBackend(
            redis_url="redis://localhost:6379/0",
            key_prefix="agent_app:dag_lease",
            ttl_seconds=300,
        )
        result = await backend.acquire_run_lease(run_id, worker, policy)

    Attributes:
        redis_url: Redis connection URL.
        key_prefix: Prefix for all Redis keys (for multi-tenant isolation).
        ttl_seconds: Default lease TTL in seconds.
        allow_steal_expired: Whether to allow stealing expired leases.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        key_prefix: str = "agent_app:dag_lease",
        ttl_seconds: int = 300,
        allow_steal_expired: bool = True,
        **kwargs: Any,
    ) -> None:
        if not _REDIS_AVAILABLE:
            raise RuntimeError(
                "Redis lease backend requires the redis extra. "
                "Install with: pip install -e '.[redis]'"
            )
        self._redis_url = redis_url
        self._key_prefix = key_prefix
        self._ttl_seconds = ttl_seconds
        self._allow_steal_expired = allow_steal_expired
        self._client: Any = None  # redis.Redis — lazily initialized
        # Lua script SHA1s (for EVALSHA fallback)
        self._acquire_sha: str | None = None
        self._renew_sha: str | None = None
        self._release_sha: str | None = None

    def _get_client(self) -> Any:
        """Get or create the Redis client (lazy initialization)."""
        if self._client is None:
            self._client = _redis_pkg.Redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
        return self._client

    def _load_scripts(self, client: Any) -> None:
        """Load Lua scripts into Redis (idempotent)."""
        if self._acquire_sha is None:
            self._acquire_sha = client.script_load(_ACQUIRE_SCRIPT)
        if self._renew_sha is None:
            self._renew_sha = client.script_load(_RENEW_SCRIPT)
        if self._release_sha is None:
            self._release_sha = client.script_load(_RELEASE_SCRIPT)

    def _lease_key(self, run_id: str) -> str:
        """Build Redis key for a workflow run lease."""
        # key_prefix format: "agent_app:dag_lease"
        # We don't have workflow_id here; use run_id only
        # The caller should include workflow_id in the key if needed
        return f"{self._key_prefix}:{run_id}"

    # -- WorkflowLeaseBackend protocol --

    async def acquire_run_lease(
        self,
        run_id: str,
        worker: WorkerIdentity,
        policy: LeasePolicy | None = None,
    ) -> LeaseAcquireResult:
        """Acquire a lease on a workflow run via Redis.

        Uses an atomic Lua script for compare-and-set semantics.

        If the same holder already holds the lease, the existing token
        is reused so the Lua script can detect the same-holder refresh.

        Args:
            run_id: The workflow run to lease.
            worker: The worker requesting the lease.
            policy: Optional lease policy.

        Returns:
            LeaseAcquireResult indicating success or denial.
        """
        policy = policy or LeasePolicy()
        ttl = policy.ttl_seconds if policy.ttl_seconds > 0 else self._ttl_seconds
        now = _utcnow()
        expires_at = now + __import__("datetime").timedelta(seconds=ttl)

        key = self._lease_key(run_id)
        client = self._get_client()

        # Look up existing lease to check if same-holder refresh
        existing_raw = client.get(key)
        existing_token = ""
        if existing_raw is not None:
            try:
                existing_decoded = json.loads(existing_raw)
                existing_token = existing_decoded.get("lease_token", "")
                # Only reuse token if same holder is re-acquiring
                if existing_decoded.get("holder_id") == worker.worker_id:
                    lease_token = existing_token
                else:
                    lease_token = _generate_token()
            except (json.JSONDecodeError, KeyError):
                lease_token = _generate_token()
        else:
            lease_token = _generate_token()

        value_json = _lease_to_json(
            run_id=run_id,
            workflow_id=run_id,
            worker=worker,
            lease_token=lease_token,
            acquired_at=now,
            expires_at=expires_at,
        )

        try:
            self._load_scripts(client)
            result = client.evalsha(
                self._acquire_sha,
                keys=[key],
                args=[
                    value_json,
                    ttl,
                    now.isoformat(),
                    lease_token,
                    1 if self._allow_steal_expired else 0,
                    time.time(),
                ],
            )
        except _redis_pkg.exceptions.ResponseError as exc:
            if "NOSCRIPT" in str(exc):
                self._acquire_sha = None
                self._load_scripts(client)
                result = client.evalsha(
                    self._acquire_sha,
                    keys=[key],
                    args=[
                        value_json,
                        ttl,
                        now.isoformat(),
                        lease_token,
                        1 if self._allow_steal_expired else 0,
                        time.time(),
                    ],
                )
            else:
                raise
        except Exception:
            raise

        if result == "acquired":
            # Read back the stored value to get the correct version
            # (especially important for same-holder refresh where version increments)
            raw = client.get(key)
            version = 1
            renewed_at = None
            if raw is not None:
                try:
                    stored = json.loads(raw)
                    version = stored.get("version", 1)
                    renewed_at_str = stored.get("renewed_at", "")
                    if renewed_at_str:
                        renewed_at = datetime.fromisoformat(renewed_at_str)
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass
            lease = WorkflowRunLease(
                run_id=run_id,
                owner_id=worker.worker_id,
                acquired_at=now,
                expires_at=expires_at,
                renewed_at=renewed_at,
                version=version,
            )
            return LeaseAcquireResult(
                acquired=True,
                run_id=run_id,
                owner_id=worker.worker_id,
                lease=lease,
            )

        if result == "denied_expired":
            # Expired but steal not allowed
            return LeaseAcquireResult(
                acquired=False,
                run_id=run_id,
                owner_id=worker.worker_id,
                reason="Existing lease expired but allow_steal_expired=False.",
            )

        # denied — different holder
        return LeaseAcquireResult(
            acquired=False,
            run_id=run_id,
            owner_id=worker.worker_id,
            reason="Run is currently leased by another worker.",
        )

    async def renew_run_lease(
        self,
        run_id: str,
        worker: WorkerIdentity,
        policy: LeasePolicy | None = None,
    ) -> WorkflowRunLease:
        """Renew an existing lease via Redis.

        Uses an atomic Lua script for compare-and-refresh semantics.
        The Lua script handles all validation (token match, expiry, release).

        Args:
            run_id: The workflow run to renew.
            worker: The worker requesting renewal.
            policy: Optional lease policy.

        Returns:
            The renewed WorkflowRunLease.

        Raises:
            KeyError: If the run has no lease, wrong holder, or expired.
        """
        policy = policy or LeasePolicy()
        ttl = policy.ttl_seconds if policy.ttl_seconds > 0 else self._ttl_seconds
        now = _utcnow()
        expires_at = now + __import__("datetime").timedelta(seconds=ttl)
        now_epoch = time.time()

        key = self._lease_key(run_id)
        client = self._get_client()

        # Get the token from the stored value
        raw = client.get(key)
        if raw is None:
            raise KeyError(f"No active lease for workflow run '{run_id}'.")
        try:
            stored = json.loads(raw)
        except (json.JSONDecodeError, KeyError):
            raise KeyError(f"No active lease for workflow run '{run_id}'.")
        lease_token = stored.get("lease_token", "")

        # Pre-check: verify holder matches (before calling Lua)
        existing_holder = stored.get("holder_id", "")
        if existing_holder != worker.worker_id:
            raise KeyError(
                f"Lease for workflow run '{run_id}' is held by "
                f"'{existing_holder}', not '{worker.worker_id}'."
            )

        renewed_at_iso = now.isoformat()
        expires_at_iso = expires_at.isoformat()

        try:
            self._load_scripts(client)
            result = client.evalsha(
                self._renew_sha,
                keys=[key],
                args=[
                    lease_token,
                    ttl,
                    renewed_at_iso,
                    expires_at_iso,
                    now_epoch,
                ],
            )
        except _redis_pkg.exceptions.ResponseError as exc:
            if "NOSCRIPT" in str(exc):
                self._renew_sha = None
                self._load_scripts(client)
                result = client.evalsha(
                    self._renew_sha,
                    keys=[key],
                    args=[
                        lease_token,
                        ttl,
                        renewed_at_iso,
                        expires_at_iso,
                        now_epoch,
                    ],
                )
            else:
                raise

        if result == "renewed":
            return WorkflowRunLease(
                run_id=run_id,
                owner_id=worker.worker_id,
                acquired_at=datetime.fromisoformat(stored["acquired_at"]),
                expires_at=expires_at,
                renewed_at=now,
                version=stored.get("version", 1) + 1,
            )

        if result == "not_found":
            raise KeyError(f"No active lease for workflow run '{run_id}'.")
        if result == "released":
            raise KeyError(f"Lease for workflow run '{run_id}' has been released.")
        if result == "wrong_holder":
            raise KeyError(
                f"Lease for workflow run '{run_id}' is held by "
                f"'{existing_holder}', not '{worker.worker_id}'."
            )
        if result == "expired":
            raise KeyError(
                f"Lease for workflow run '{run_id}' has expired "
                f"(expired at {stored.get('expires_at', 'unknown')})."
            )

        raise KeyError(f"Unexpected renew result: {result}")

    async def release_run_lease(
        self,
        run_id: str,
        worker: WorkerIdentity,
    ) -> WorkflowRunLease:
        """Release a held lease via Redis.

        Uses an atomic Lua script for compare-and-delete semantics.
        The Lua script handles all validation (token match, already released).

        Args:
            run_id: The workflow run to release.
            worker: The worker releasing the lease.

        Returns:
            The released WorkflowRunLease.

        Raises:
            KeyError: If the run has no lease or is leased by a different worker.
        """
        key = self._lease_key(run_id)
        now_iso = _utcnow().isoformat()
        client = self._get_client()

        # Get the current lease data for the return value and token
        raw = client.get(key)
        if raw is None:
            raise KeyError(f"No active lease for workflow run '{run_id}'.")
        try:
            stored = json.loads(raw)
        except (json.JSONDecodeError, KeyError):
            raise KeyError(f"No active lease for workflow run '{run_id}'.")

        lease_token = stored.get("lease_token", "")
        existing_holder = stored.get("holder_id", "")

        # Pre-check: verify holder matches
        if existing_holder != worker.worker_id:
            raise KeyError(
                f"Lease for workflow run '{run_id}' is held by "
                f"'{existing_holder}', not '{worker.worker_id}'."
            )

        try:
            self._load_scripts(client)
            result = client.evalsha(
                self._release_sha,
                keys=[key],
                args=[lease_token, now_iso],
            )
        except _redis_pkg.exceptions.ResponseError as exc:
            if "NOSCRIPT" in str(exc):
                self._release_sha = None
                self._load_scripts(client)
                result = client.evalsha(
                    self._release_sha,
                    keys=[key],
                    args=[lease_token, now_iso],
                )
            else:
                raise

        if result == "released":
            return WorkflowRunLease(
                run_id=run_id,
                owner_id=existing_holder,
                acquired_at=datetime.fromisoformat(stored["acquired_at"]),
                expires_at=datetime.fromisoformat(stored["expires_at"]),
                renewed_at=(
                    datetime.fromisoformat(stored["renewed_at"])
                    if stored.get("renewed_at")
                    else None
                ),
                released_at=_utcnow(),
                version=stored.get("version", 1),
            )

        if result == "not_found":
            raise KeyError(f"No active lease for workflow run '{run_id}'.")
        if result == "already_released":
            raise KeyError(f"Lease for workflow run '{run_id}' has already been released.")
        if result == "wrong_holder":
            raise KeyError(
                f"Lease for workflow run '{run_id}' is held by "
                f"'{existing_holder}', not '{worker.worker_id}'."
            )

        raise KeyError(f"Unexpected release result: {result}")

    async def get_run_lease(
        self,
        run_id: str,
    ) -> WorkflowRunLease | None:
        """Get the current lease for a workflow run from Redis.

        Args:
            run_id: The workflow run ID.

        Returns:
            WorkflowRunLease if an active lease exists, None otherwise.
        """
        key = self._lease_key(run_id)
        client = self._get_client()
        raw = client.get(key)
        if raw is None:
            return None
        try:
            lease = _json_to_lease(raw)
            if lease.released_at is not None:
                return None
            return lease
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    async def list_expired_leases(
        self,
        before: datetime | None = None,
    ) -> list[WorkflowRunLease]:
        """List leases that have expired from Redis.

        Uses SCAN to find keys matching the prefix, then filters by expiry.
        Limited to avoid scanning the entire Redis keyspace.

        Args:
            before: Optional cutoff datetime.  Defaults to now.

        Returns:
            List of expired WorkflowRunLease objects.
        """
        cutoff = before or _utcnow()
        client = self._get_client()
        prefix = f"{self._key_prefix}:*"
        expired: list[WorkflowRunLease] = []
        # Use SCAN with a limit to avoid full keyspace scan
        scan_limit = 1000
        cursor = 0
        while True:
            cursor, keys = client.scan(
                cursor=cursor, match=prefix, count=min(scan_limit, 100)
            )
            for key in keys:
                raw = client.get(key)
                if raw is None:
                    continue
                try:
                    lease = _json_to_lease(raw)
                    if (
                        lease.released_at is None
                        and lease.expires_at <= cutoff
                    ):
                        expired.append(lease)
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
            if cursor == 0:
                break
        return expired

    # -- Health check (Phase 16.3) --

    def _detect_backend_type(self) -> str:
        return "redis"

    async def health_check(self) -> Any:
        """Perform a lightweight Redis health check.

        Uses PING to verify connectivity.  Never raises.
        """
        from agent_app.runtime.lease_health import (
            LeaseHealthCheckResult,
            LeaseHealthStatus,
        )

        checked_at = _utcnow()
        try:
            client = self._get_client()
            pong = client.ping()
            if pong:
                return LeaseHealthCheckResult(
                    status=LeaseHealthStatus.HEALTHY,
                    backend_type="redis",
                    details={
                        "redis_url_sanitized": _sanitize_redis_url(self._redis_url),
                        "key_prefix": self._key_prefix,
                        "ping": "ok",
                    },
                    checked_at=checked_at,
                )
            return LeaseHealthCheckResult(
                status=LeaseHealthStatus.UNHEALTHY,
                backend_type="redis",
                details={"ping": "no_response"},
                checked_at=checked_at,
                error="Redis PING returned no response.",
            )
        except Exception as exc:
            return LeaseHealthCheckResult(
                status=LeaseHealthStatus.UNHEALTHY,
                backend_type="redis",
                details={"error": str(exc)},
                checked_at=checked_at,
                error=str(exc),
            )

    # -- Diagnostics (Phase 16.3) --

    async def diagnostics(self, **kwargs: Any) -> Any:
        """Collect diagnostic information about the Redis lease backend.

        Returns:
            LeaseDiagnostics with health, config, and metrics info.
        """
        from agent_app.runtime.lease_health import LeaseBackendHealthChecker

        health = await self.health_check()
        details: dict[str, Any] = {
            "backend_type": "redis",
            "key_prefix": self._key_prefix,
            "ttl_seconds": self._ttl_seconds,
            "allow_steal_expired": self._allow_steal_expired,
            "redis_url_sanitized": _sanitize_redis_url(self._redis_url),
        }
        # Count leases (lightweight)
        try:
            client = self._get_client()
            prefix = f"{self._key_prefix}:*"
            cursor = 0
            total = 0
            while True:
                cursor, keys = client.scan(
                    cursor=cursor, match=prefix, count=100
                )
                total += len(keys)
                if cursor == 0:
                    break
            details["total_lease_keys"] = total
        except Exception:
            details["total_lease_keys"] = None

        from agent_app.runtime.lease_coordinator import LeaseDiagnostics

        return LeaseDiagnostics(
            backend_type="redis",
            health=health,
            metrics=None,
            expired_leases_count=None,
            sample_expired_leases=[],
            details=details,
        )

    def __repr__(self) -> str:
        url = getattr(self, "_redis_url", "unknown")
        prefix = getattr(self, "_key_prefix", "unknown")
        ttl = getattr(self, "_ttl_seconds", "unknown")
        return (
            f"RedisWorkflowLeaseBackend("
            f"redis_url={_sanitize_redis_url(url)!r}, "
            f"key_prefix={prefix!r}, "
            f"ttl_seconds={ttl})"
        )
