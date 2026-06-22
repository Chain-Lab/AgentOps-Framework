"""Federation webhook nonce store — Protocol, InMemory, SQLite, factory.

Phase 51: Replay-attack protection via nonce uniqueness tracking.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class FederationWebhookNonceStore(Protocol):
    """Protocol for webhook nonce storage (replay-attack protection)."""

    async def register(self, nonce: str, ttl_seconds: int = 600) -> None: ...
    async def exists(self, nonce: str) -> bool: ...
    async def purge_expired(self) -> int: ...

    # Sync versions for signature service
    def register_sync(self, nonce: str, ttl_seconds: int = 600) -> None: ...
    def exists_sync(self, nonce: str) -> bool: ...


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryFederationWebhookNonceStore:
    """In-memory federation webhook nonce store."""

    def __init__(self) -> None:
        self._nonces: dict[str, datetime] = {}

    async def register(self, nonce: str, ttl_seconds: int = 600) -> None:
        self.register_sync(nonce, ttl_seconds)

    async def exists(self, nonce: str) -> bool:
        return self.exists_sync(nonce)

    async def purge_expired(self) -> int:
        now = datetime.now(timezone.utc)
        expired = [n for n, exp in self._nonces.items() if exp <= now]
        for n in expired:
            del self._nonces[n]
        return len(expired)

    def register_sync(self, nonce: str, ttl_seconds: int = 600) -> None:
        if nonce in self._nonces:
            raise ValueError(f"Nonce '{nonce}' already registered")
        expires_at = datetime.now(timezone.utc) + __import__("datetime").timedelta(seconds=ttl_seconds)
        self._nonces[nonce] = expires_at

    def exists_sync(self, nonce: str) -> bool:
        if nonce not in self._nonces:
            return False
        if self._nonces[nonce] <= datetime.now(timezone.utc):
            del self._nonces[nonce]
            return False
        return True


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteFederationWebhookNonceStore:
    """SQLite-backed federation webhook nonce store."""

    def __init__(self, db_path: str = ".agent_app/federation_webhook_nonce.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS federation_webhook_nonce (
                nonce TEXT PRIMARY KEY,
                expires_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_fwn_expires_at
                ON federation_webhook_nonce(expires_at);
        """)
        self._conn.commit()

    async def register(self, nonce: str, ttl_seconds: int = 600) -> None:
        self.register_sync(nonce, ttl_seconds)

    async def exists(self, nonce: str) -> bool:
        return self.exists_sync(nonce)

    async def purge_expired(self) -> int:
        now_str = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            "DELETE FROM federation_webhook_nonce WHERE expires_at <= ?",
            (now_str,),
        )
        self._conn.commit()
        return cursor.rowcount

    def register_sync(self, nonce: str, ttl_seconds: int = 600) -> None:
        expires_at = datetime.now(timezone.utc) + __import__("datetime").timedelta(seconds=ttl_seconds)
        try:
            self._conn.execute(
                "INSERT INTO federation_webhook_nonce (nonce, expires_at) VALUES (?, ?)",
                (nonce, expires_at.isoformat()),
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError(f"Nonce '{nonce}' already registered") from None

    def exists_sync(self, nonce: str) -> bool:
        now_str = datetime.now(timezone.utc).isoformat()
        # Clean up expired and check
        self._conn.execute(
            "DELETE FROM federation_webhook_nonce WHERE expires_at <= ?",
            (now_str,),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT 1 FROM federation_webhook_nonce WHERE nonce = ?",
            (nonce,),
        ).fetchone()
        return row is not None

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_federation_webhook_nonce_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> FederationWebhookNonceStore:
    """Factory for creating federation webhook nonce store instances."""
    if store_type == "memory":
        return InMemoryFederationWebhookNonceStore()
    if store_type == "sqlite":
        return SQLiteFederationWebhookNonceStore(db_path=db_path or ".agent_app/federation_webhook_nonce.db")
    raise ValueError(f"Unknown nonce store type '{store_type}'. Supported: 'memory', 'sqlite'.")
