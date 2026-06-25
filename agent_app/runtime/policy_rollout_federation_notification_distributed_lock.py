"""Distributed lock for retry daemon multi-instance coordination.

Phase 59 Task 733: Ensures only one daemon instance runs at a time.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class DistributedLockStatus(BaseModel):
    """Current status of a distributed lock."""

    lock_name: str
    owner_id: str | None = None
    acquired: bool = False
    lease_expires_at: datetime | None = None
    fencing_token: int | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class DistributedLockStore(Protocol):
    """Protocol for distributed lock storage."""

    def acquire(
        self,
        lock_name: str,
        owner_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> DistributedLockStatus: ...

    def renew(
        self,
        lock_name: str,
        owner_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> DistributedLockStatus: ...

    def release(
        self,
        lock_name: str,
        owner_id: str,
    ) -> bool: ...

    def get_status(
        self,
        lock_name: str,
    ) -> DistributedLockStatus: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _redact_error(error: str | None) -> str | None:
    """Redact sensitive patterns from error messages."""
    if not error:
        return error
    import re
    _patterns = ["token=", "secret=", "api_key=", "password="]
    redacted = error
    for pattern in _patterns:
        if pattern.lower() in redacted.lower():
            regex = re.escape(pattern) + r'[^\s,;}]*'
            redacted = re.sub(regex, f'{pattern}[REDACTED]', redacted, flags=re.IGNORECASE)
    return redacted


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryDistributedLockStore:
    """In-memory distributed lock store.

    Suitable for single-process deployments. Not safe for multi-process.
    """

    def __init__(self) -> None:
        self._locks: dict[str, dict] = {}
        self._next_token = 1

    def acquire(
        self,
        lock_name: str,
        owner_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> DistributedLockStatus:
        if now is None:
            now = _now()
        existing = self._locks.get(lock_name)
        if existing is not None:
            # Check if lock is expired
            if existing["lease_expires_at"] > now:
                # Lock is held by another owner
                return DistributedLockStatus(
                    lock_name=lock_name,
                    owner_id=existing["owner_id"],
                    acquired=False,
                    lease_expires_at=existing["lease_expires_at"],
                    fencing_token=existing.get("fencing_token"),
                    updated_at=existing["updated_at"],
                )
            # Expired lock — allow takeover
            # Fall through to acquire

        # Acquire or takeover
        token = self._next_token
        self._next_token += 1
        expires = now + __import__("datetime").timedelta(seconds=lease_seconds)
        self._locks[lock_name] = {
            "owner_id": owner_id,
            "lease_expires_at": expires,
            "fencing_token": token,
            "updated_at": now,
        }
        return DistributedLockStatus(
            lock_name=lock_name,
            owner_id=owner_id,
            acquired=True,
            lease_expires_at=expires,
            fencing_token=token,
            updated_at=now,
        )

    def renew(
        self,
        lock_name: str,
        owner_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> DistributedLockStatus:
        if now is None:
            now = _now()
        existing = self._locks.get(lock_name)
        if existing is None:
            return DistributedLockStatus(
                lock_name=lock_name,
                acquired=False,
                updated_at=now,
            )
        if existing["owner_id"] != owner_id:
            return DistributedLockStatus(
                lock_name=lock_name,
                owner_id=existing["owner_id"],
                acquired=False,
                lease_expires_at=existing["lease_expires_at"],
                fencing_token=existing.get("fencing_token"),
                updated_at=existing["updated_at"],
            )
        expires = now + __import__("datetime").timedelta(seconds=lease_seconds)
        existing["lease_expires_at"] = expires
        existing["updated_at"] = now
        return DistributedLockStatus(
            lock_name=lock_name,
            owner_id=owner_id,
            acquired=True,
            lease_expires_at=expires,
            fencing_token=existing.get("fencing_token"),
            updated_at=now,
        )

    def release(
        self,
        lock_name: str,
        owner_id: str,
    ) -> bool:
        existing = self._locks.get(lock_name)
        if existing is None:
            return False
        if existing["owner_id"] != owner_id:
            return False
        del self._locks[lock_name]
        return True

    def get_status(self, lock_name: str) -> DistributedLockStatus:
        existing = self._locks.get(lock_name)
        if existing is None:
            return DistributedLockStatus(lock_name=lock_name, acquired=False)
        now = _now()
        if existing["lease_expires_at"] < now:
            # Expired
            return DistributedLockStatus(
                lock_name=lock_name,
                owner_id=existing["owner_id"],
                acquired=False,
                lease_expires_at=existing["lease_expires_at"],
                fencing_token=existing.get("fencing_token"),
                updated_at=existing["updated_at"],
            )
        return DistributedLockStatus(
            lock_name=lock_name,
            owner_id=existing["owner_id"],
            acquired=True,
            lease_expires_at=existing["lease_expires_at"],
            fencing_token=existing.get("fencing_token"),
            updated_at=existing["updated_at"],
        )


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteDistributedLockStore:
    """SQLite-backed distributed lock store.

    Suitable for multi-process deployments on shared filesystem.
    """

    def __init__(self, db_path: str = ".agent_app/distributed_locks.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_db()
        self._next_token = self._load_next_token()

    def _load_next_token(self) -> int:
        row = self._conn.execute(
            "SELECT MAX(fencing_token) AS max_token FROM distributed_locks"
        ).fetchone()
        max_token = row["max_token"] if row and row["max_token"] is not None else 0
        return max_token + 1

    def _init_db(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS distributed_locks (
                lock_name TEXT PRIMARY KEY,
                owner_id TEXT,
                lease_expires_at TEXT,
                fencing_token INTEGER,
                updated_at TEXT
            )
        """)
        self._conn.commit()

    def acquire(
        self,
        lock_name: str,
        owner_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> DistributedLockStatus:
        if now is None:
            now = _now()
        now_iso = now.isoformat()
        expires_iso = (now + __import__("datetime").timedelta(seconds=lease_seconds)).isoformat()

        # Check existing
        row = self._conn.execute(
            "SELECT * FROM distributed_locks WHERE lock_name=?", (lock_name,)
        ).fetchone()

        if row is not None:
            lease_exp = datetime.fromisoformat(row["lease_expires_at"])
            if lease_exp > now:
                # Lock held by another owner
                return DistributedLockStatus(
                    lock_name=lock_name,
                    owner_id=row["owner_id"],
                    acquired=False,
                    lease_expires_at=lease_exp,
                    fencing_token=row["fencing_token"],
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                )
            # Expired — takeover
            token = self._next_token
            self._next_token += 1
            self._conn.execute(
                """UPDATE distributed_locks
                   SET owner_id=?, lease_expires_at=?, fencing_token=?, updated_at=?
                   WHERE lock_name=?""",
                (owner_id, expires_iso, token, now_iso, lock_name),
            )
            self._conn.commit()
            return DistributedLockStatus(
                lock_name=lock_name,
                owner_id=owner_id,
                acquired=True,
                lease_expires_at=now + __import__("datetime").timedelta(seconds=lease_seconds),
                fencing_token=token,
                updated_at=now,
            )

        # New lock
        token = self._next_token
        self._next_token += 1
        self._conn.execute(
            "INSERT INTO distributed_locks (lock_name, owner_id, lease_expires_at, fencing_token, updated_at) VALUES (?, ?, ?, ?, ?)",
            (lock_name, owner_id, expires_iso, token, now_iso),
        )
        self._conn.commit()
        return DistributedLockStatus(
            lock_name=lock_name,
            owner_id=owner_id,
            acquired=True,
            lease_expires_at=now + __import__("datetime").timedelta(seconds=lease_seconds),
            fencing_token=token,
            updated_at=now,
        )

    def renew(
        self,
        lock_name: str,
        owner_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> DistributedLockStatus:
        if now is None:
            now = _now()
        now_iso = now.isoformat()
        expires_iso = (now + __import__("datetime").timedelta(seconds=lease_seconds)).isoformat()

        row = self._conn.execute(
            "SELECT * FROM distributed_locks WHERE lock_name=?", (lock_name,)
        ).fetchone()

        if row is None:
            return DistributedLockStatus(lock_name=lock_name, acquired=False, updated_at=now)

        if row["owner_id"] != owner_id:
            return DistributedLockStatus(
                lock_name=lock_name,
                owner_id=row["owner_id"],
                acquired=False,
                lease_expires_at=datetime.fromisoformat(row["lease_expires_at"]),
                fencing_token=row["fencing_token"],
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

        self._conn.execute(
            "UPDATE distributed_locks SET lease_expires_at=?, updated_at=? WHERE lock_name=?",
            (expires_iso, now_iso, lock_name),
        )
        self._conn.commit()
        return DistributedLockStatus(
            lock_name=lock_name,
            owner_id=owner_id,
            acquired=True,
            lease_expires_at=now + __import__("datetime").timedelta(seconds=lease_seconds),
            fencing_token=row["fencing_token"],
            updated_at=now,
        )

    def release(
        self,
        lock_name: str,
        owner_id: str,
    ) -> bool:
        row = self._conn.execute(
            "SELECT * FROM distributed_locks WHERE lock_name=?", (lock_name,)
        ).fetchone()
        if row is None:
            return False
        if row["owner_id"] != owner_id:
            return False
        self._conn.execute("DELETE FROM distributed_locks WHERE lock_name=?", (lock_name,))
        self._conn.commit()
        return True

    def get_status(self, lock_name: str) -> DistributedLockStatus:
        row = self._conn.execute(
            "SELECT * FROM distributed_locks WHERE lock_name=?", (lock_name,)
        ).fetchone()
        if row is None:
            return DistributedLockStatus(lock_name=lock_name, acquired=False)
        now = _now()
        lease_exp = datetime.fromisoformat(row["lease_expires_at"])
        if lease_exp < now:
            return DistributedLockStatus(
                lock_name=lock_name,
                owner_id=row["owner_id"],
                acquired=False,
                lease_expires_at=lease_exp,
                fencing_token=row["fencing_token"],
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
        return DistributedLockStatus(
            lock_name=lock_name,
            owner_id=row["owner_id"],
            acquired=True,
            lease_expires_at=lease_exp,
            fencing_token=row["fencing_token"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_distributed_lock_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> DistributedLockStore:
    """Factory for creating distributed lock store instances."""
    if store_type == "memory":
        return InMemoryDistributedLockStore()
    if store_type == "sqlite":
        return SQLiteDistributedLockStore(
            db_path=db_path or ".agent_app/distributed_locks.db"
        )
    raise ValueError(
        f"Unknown distributed lock store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )
