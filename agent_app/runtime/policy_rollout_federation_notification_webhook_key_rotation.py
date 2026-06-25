"""Webhook key rotation service for automatic secret lifecycle management.

Phase 59 Task 738: Automates webhook signing key rotation with configurable
intervals, key generation, and active/previous/disabled lifecycle.
"""
from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from agent_app.runtime.policy_rollout_federation_notification_webhook_signing import (
    WebhookSigningSecret,
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class WebhookKeyRotationConfig(BaseModel):
    """Configuration for key rotation."""

    rotation_interval_hours: int = Field(default=24, description="Hours between rotations")
    keep_previous_count: int = Field(default=1, description="Number of previous keys to retain")
    key_bits: int = Field(default=256, description="Entropy bits for generated keys")


class WebhookKeyRotationRecord(BaseModel):
    """Record of a key rotation event."""

    rotation_id: str = Field(..., description="Unique rotation event ID")
    old_key_id: str = Field(..., description="Previous active key ID")
    new_key_id: str = Field(..., description="New active key ID")
    rotated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str = Field(default="scheduled", description="Rotation reason: scheduled, manual, forced")


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class WebhookKeyRotationStore(Protocol):
    """Protocol for webhook key rotation storage."""

    def record_rotation(self, record: WebhookKeyRotationRecord) -> WebhookKeyRotationRecord: ...

    def get_last_rotation(self) -> WebhookKeyRotationRecord | None: ...

    def list_rotations(self, limit: int = 50) -> list[WebhookKeyRotationRecord]: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _generate_key_id() -> str:
    """Generate a unique key ID."""
    return f"whk_{secrets.token_hex(8)}"


def _generate_secret(bits: int = 256) -> str:
    """Generate a cryptographically random secret."""
    byte_count = max(32, bits // 8)
    return secrets.token_hex(byte_count)


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryWebhookKeyRotationStore:
    """In-memory webhook key rotation store."""

    def __init__(self) -> None:
        self._rotations: list[WebhookKeyRotationRecord] = []

    def record_rotation(self, record: WebhookKeyRotationRecord) -> WebhookKeyRotationRecord:
        self._rotations.append(record)
        return record

    def get_last_rotation(self) -> WebhookKeyRotationRecord | None:
        if not self._rotations:
            return None
        return self._rotations[-1]

    def list_rotations(self, limit: int = 50) -> list[WebhookKeyRotationRecord]:
        return list(reversed(self._rotations[-limit:]))


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteWebhookKeyRotationStore:
    """SQLite-backed webhook key rotation store."""

    def __init__(self, db_path: str = ".agent_app/webhook_key_rotation.db") -> None:
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

    def _init_db(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS webhook_key_rotations (
                rotation_id TEXT PRIMARY KEY,
                old_key_id TEXT NOT NULL,
                new_key_id TEXT NOT NULL,
                rotated_at TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT 'scheduled'
            )
        """)
        self._conn.commit()

    def record_rotation(self, record: WebhookKeyRotationRecord) -> WebhookKeyRotationRecord:
        self._conn.execute(
            """INSERT INTO webhook_key_rotations
               (rotation_id, old_key_id, new_key_id, rotated_at, reason)
               VALUES (?, ?, ?, ?, ?)""",
            (
                record.rotation_id,
                record.old_key_id,
                record.new_key_id,
                record.rotated_at.isoformat(),
                record.reason,
            ),
        )
        self._conn.commit()
        return record

    def get_last_rotation(self) -> WebhookKeyRotationRecord | None:
        row = self._conn.execute(
            "SELECT * FROM webhook_key_rotations ORDER BY rotated_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def list_rotations(self, limit: int = 50) -> list[WebhookKeyRotationRecord]:
        rows = self._conn.execute(
            "SELECT * FROM webhook_key_rotations ORDER BY rotated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def _row_to_record(self, row: sqlite3.Row) -> WebhookKeyRotationRecord:
        data = dict(row)
        data["rotated_at"] = datetime.fromisoformat(data["rotated_at"])
        return WebhookKeyRotationRecord(**data)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Rotation Service
# ---------------------------------------------------------------------------


class WebhookKeyRotationService:
    """Service for managing webhook signing key rotation.

    Manages the lifecycle of webhook signing keys:
    - Generates new keys with secure random values
    - Rotates active key to previous on schedule
    - Retains configurable number of previous keys
    - Disables old keys beyond retention count
    """

    def __init__(
        self,
        config: WebhookKeyRotationConfig | None = None,
        store: WebhookKeyRotationStore | None = None,
    ) -> None:
        self._config = config or WebhookKeyRotationConfig()
        self._store = store or InMemoryWebhookKeyRotationStore()
        self._secrets: dict[str, WebhookSigningSecret] = {}

    @property
    def config(self) -> WebhookKeyRotationConfig:
        return self._config

    def add_secret(self, secret: WebhookSigningSecret) -> None:
        """Add a secret to the managed key store."""
        self._secrets[secret.key_id] = secret

    def get_active(self) -> WebhookSigningSecret | None:
        """Get the current active secret."""
        for secret in self._secrets.values():
            if secret.status == "active":
                return secret
        return None

    def get_previous(self) -> list[WebhookSigningSecret]:
        """Get previous secrets (still valid for verification)."""
        return [s for s in self._secrets.values() if s.status == "previous"]

    def generate_new_key(self) -> WebhookSigningSecret:
        """Generate a new active signing key.

        The new key becomes active; the current active key becomes previous.
        Previous keys beyond retention count are disabled.
        """
        new_key_id = _generate_key_id()
        new_secret = _generate_secret(self._config.key_bits)
        now = _now()

        # Get current active
        current_active = self.get_active()
        old_key_id = current_active.key_id if current_active else "none"

        # Demote current active to previous
        if current_active is not None:
            current_active.status = "previous"
            current_active.not_after = now + timedelta(hours=self._config.rotation_interval_hours)

        # Collect all previous keys
        previous_keys = [s for s in self._secrets.values() if s.status == "previous"]
        previous_keys.sort(key=lambda s: s.not_after or datetime.min.replace(tzinfo=timezone.utc))

        # Disable excess previous keys
        excess = previous_keys[self._config.keep_previous_count:]
        for key in excess:
            key.status = "disabled"

        # Create new active key
        new_secret_obj = WebhookSigningSecret(
            key_id=new_key_id,
            secret=new_secret,
            status="active",
            not_before=now,
        )
        self._secrets[new_key_id] = new_secret_obj

        # Record rotation
        rotation = WebhookKeyRotationRecord(
            rotation_id=f"rot_{secrets.token_hex(6)}",
            old_key_id=old_key_id,
            new_key_id=new_key_id,
            rotated_at=now,
            reason="scheduled",
        )
        self._store.record_rotation(rotation)

        return new_secret_obj

    def force_rotate(self, reason: str = "manual") -> WebhookSigningSecret:
        """Force immediate key rotation."""
        new_key_id = _generate_key_id()
        new_secret = _generate_secret(self._config.key_bits)
        now = _now()

        current_active = self.get_active()
        old_key_id = current_active.key_id if current_active else "none"

        if current_active is not None:
            current_active.status = "previous"
            current_active.not_after = now + timedelta(hours=self._config.rotation_interval_hours)

        new_secret_obj = WebhookSigningSecret(
            key_id=new_key_id,
            secret=new_secret,
            status="active",
            not_before=now,
        )
        self._secrets[new_key_id] = new_secret_obj

        rotation = WebhookKeyRotationRecord(
            rotation_id=f"rot_{secrets.token_hex(6)}",
            old_key_id=old_key_id,
            new_key_id=new_key_id,
            rotated_at=now,
            reason=reason,
        )
        self._store.record_rotation(rotation)

        return new_secret_obj

    def should_rotate(self) -> bool:
        """Check if rotation is due based on last rotation time."""
        last = self._store.get_last_rotation()
        if last is None:
            return True
        elapsed = _now() - last.rotated_at
        return elapsed >= timedelta(hours=self._config.rotation_interval_hours)

    def get_valid_for_verification(self, now: datetime | None = None) -> list[WebhookSigningSecret]:
        """Get secrets valid for signature verification (active + previous)."""
        if now is None:
            now = _now()
        result = []
        for s in self._secrets.values():
            if s.status not in ("active", "previous"):
                continue
            if s.not_before is not None and now < s.not_before:
                continue
            if s.not_after is not None and now > s.not_after:
                continue
            result.append(s)
        return result


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_webhook_key_rotation_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> WebhookKeyRotationStore:
    """Factory for creating webhook key rotation store instances."""
    if store_type == "memory":
        return InMemoryWebhookKeyRotationStore()
    if store_type == "sqlite":
        return SQLiteWebhookKeyRotationStore(
            db_path=db_path or ".agent_app/webhook_key_rotation.db"
        )
    raise ValueError(
        f"Unknown webhook key rotation store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )
