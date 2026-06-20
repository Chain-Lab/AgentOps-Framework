"""Distributed lock abstraction — pluggable lock coordination layer.

Phase 49 Task 6: Introduces a ``DistributedLock`` protocol that decouples
lock coordination from any particular storage backend.  The default
implementations are ``InMemoryDistributedLock`` (for development/testing)
and ``SQLiteDistributedLock`` (for single-node persistence).

This is a best-effort coordination layer, NOT a distributed consensus
service, and does NOT provide exactly-once execution guarantees.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

try:
    from typing import runtime_checkable
except ImportError:
    def runtime_checkable(cls):  # type: ignore[misc]
        return cls


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return current UTC datetime with tzinfo."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class DistributedLock(Protocol):
    """Protocol for distributed lock coordination.

    Implementations manage lock acquire / release / refresh operations
    independently of any particular storage backend.
    """

    async def acquire(self, lock_name: str, owner_id: str, ttl_seconds: int) -> bool:
        """Attempt to acquire a lock.

        Args:
            lock_name: The name of the lock to acquire.
            owner_id: The identity of the owner requesting the lock.
            ttl_seconds: Time-to-live in seconds before the lock expires.

        Returns:
            True if the lock was acquired, False if it is held by another
            owner and has not expired.
        """
        ...  # pragma: no cover

    async def release(self, lock_name: str, owner_id: str) -> bool:
        """Release a held lock.

        Args:
            lock_name: The name of the lock to release.
            owner_id: The identity of the owner releasing the lock.

        Returns:
            True if the lock was released, False if the lock does not
            exist or is held by a different owner.
        """
        ...  # pragma: no cover

    async def refresh(self, lock_name: str, owner_id: str, ttl_seconds: int) -> bool:
        """Refresh (extend) a held lock's TTL.

        Args:
            lock_name: The name of the lock to refresh.
            owner_id: The identity of the owner refreshing the lock.
            ttl_seconds: New time-to-live in seconds from now.

        Returns:
            True if the lock was refreshed, False if the lock does not
            exist or is held by a different owner.
        """
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# InMemory distributed lock
# ---------------------------------------------------------------------------


class InMemoryDistributedLock:
    """Standalone in-memory distributed lock.

    Stores locks in a plain dict mapping lock_name to (owner_id, expires_at).
    Suitable for development, testing, and single-process deployments.

    Usage::

        lock = InMemoryDistributedLock()
        acquired = await lock.acquire("my-lock", "owner-1", ttl_seconds=30)
    """

    def __init__(self) -> None:
        self._locks: dict[str, tuple[str, datetime]] = {}

    async def acquire(self, lock_name: str, owner_id: str, ttl_seconds: int) -> bool:
        """Attempt to acquire a lock.

        If the lock exists and has not expired and is held by a different
        owner, returns False.  If the lock has expired or does not exist,
        acquires the lock and returns True.  If the lock is held by the
        same owner, re-acquires (overwrites) and returns True.
        """
        now = _utcnow()
        existing = self._locks.get(lock_name)

        if existing is not None:
            existing_owner, expires_at = existing
            # Lock is still active and held by a different owner
            if expires_at > now and existing_owner != owner_id:
                return False
            # Lock is expired or same owner — can be acquired (overwrite)

        self._locks[lock_name] = (owner_id, now + timedelta(seconds=ttl_seconds))
        return True

    async def release(self, lock_name: str, owner_id: str) -> bool:
        """Release a held lock.

        Returns False if the lock does not exist or is held by a different
        owner.  Returns True and deletes the lock if the owner matches.
        """
        existing = self._locks.get(lock_name)
        if existing is None:
            return False
        existing_owner, _ = existing
        if existing_owner != owner_id:
            return False
        del self._locks[lock_name]
        return True

    async def refresh(self, lock_name: str, owner_id: str, ttl_seconds: int) -> bool:
        """Refresh a held lock's TTL.

        Returns False if the lock does not exist or is held by a different
        owner.  Returns True and updates the expiry if the owner matches.
        """
        existing = self._locks.get(lock_name)
        if existing is None:
            return False
        existing_owner, _ = existing
        if existing_owner != owner_id:
            return False
        now = _utcnow()
        self._locks[lock_name] = (owner_id, now + timedelta(seconds=ttl_seconds))
        return True


# ---------------------------------------------------------------------------
# SQLite distributed lock
# ---------------------------------------------------------------------------


class SQLiteDistributedLock:
    """SQLite-backed distributed lock.

    Persists locks in a SQLite table, making them visible across process
    instances.  Suitable for single-node deployments that need lock
    persistence across restarts.

    Usage::

        lock = SQLiteDistributedLock(".agent_app/distributed_locks.db")
        acquired = await lock.acquire("my-lock", "owner-1", ttl_seconds=30)
    """

    def __init__(self, db_path: str = ".agent_app/distributed_locks.db") -> None:
        self._db_path = str(Path(db_path).expanduser().resolve())
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Create the lock table if it doesn't exist."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS distributed_locks (
                    lock_name TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    acquired_at TEXT NOT NULL
                )
            """)
            conn.commit()
        finally:
            conn.close()

    async def acquire(self, lock_name: str, owner_id: str, ttl_seconds: int) -> bool:
        """Attempt to acquire a lock.

        Checks if the lock exists and is not expired and held by a
        different owner — if so, returns False.  Otherwise inserts or
        replaces the lock row and returns True.
        """
        now = _utcnow()
        now_iso = now.isoformat()
        expires_at_iso = (now + timedelta(seconds=ttl_seconds)).isoformat()

        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        try:
            row = conn.execute(
                "SELECT owner_id, expires_at FROM distributed_locks WHERE lock_name = ?",
                (lock_name,),
            ).fetchone()

            if row is not None:
                existing_owner, existing_expires_at_str = row
                existing_expires_at = datetime.fromisoformat(existing_expires_at_str)
                # Lock is still active and held by a different owner
                if existing_expires_at > now and existing_owner != owner_id:
                    return False
                # Lock is expired or same owner — can be acquired (overwrite)

            conn.execute(
                """
                INSERT OR REPLACE INTO distributed_locks
                    (lock_name, owner_id, expires_at, acquired_at)
                VALUES (?, ?, ?, ?)
                """,
                (lock_name, owner_id, expires_at_iso, now_iso),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    async def release(self, lock_name: str, owner_id: str) -> bool:
        """Release a held lock.

        Deletes the lock row where lock_name and owner_id match.
        Returns True if a row was deleted, False otherwise.
        """
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        try:
            cursor = conn.execute(
                "DELETE FROM distributed_locks WHERE lock_name = ? AND owner_id = ?",
                (lock_name, owner_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    async def refresh(self, lock_name: str, owner_id: str, ttl_seconds: int) -> bool:
        """Refresh a held lock's TTL.

        Updates the expires_at where lock_name and owner_id match.
        Returns True if a row was updated, False otherwise.
        """
        now = _utcnow()
        expires_at_iso = (now + timedelta(seconds=ttl_seconds)).isoformat()

        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        try:
            cursor = conn.execute(
                "UPDATE distributed_locks SET expires_at = ? WHERE lock_name = ? AND owner_id = ?",
                (expires_at_iso, lock_name, owner_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_distributed_lock(
    store_type: str = "memory",
    db_path: str | None = None,
) -> DistributedLock:
    """Create a distributed lock implementation.

    Args:
        store_type: Backend type — ``"memory"`` or ``"sqlite"``.
        db_path: Database file path (required when ``store_type="sqlite"``).
            Defaults to ``".agent_app/distributed_locks.db"`` if not
            provided.

    Returns:
        A ``DistributedLock`` implementation.

    Raises:
        ValueError: If ``store_type`` is unknown.
    """
    if store_type == "memory":
        return InMemoryDistributedLock()
    if store_type == "sqlite":
        return SQLiteDistributedLock(db_path=db_path or ".agent_app/distributed_locks.db")
    raise ValueError(
        f"Unknown distributed lock store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )
