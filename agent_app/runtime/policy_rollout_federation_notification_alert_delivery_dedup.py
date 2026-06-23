"""Alert deduplication — suppress or merge duplicate alert delivery events.

Phase 54 Task 7: Alert deduplication/merge service (in-memory).
Phase 55 Task 2: Persistent dedup store (InMemory + SQLite).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator


_SENSITIVE_KEYS = frozenset({
    "authorization", "token", "secret", "password", "api_key",
    "x-signature", "x-api-key", "x-secret", "x-auth-token",
    "x-webhook-secret", "cookie", "signature", "private_key",
    "access_key",
})


# ---------------------------------------------------------------------------
# Dedup Record
# ---------------------------------------------------------------------------


class NotificationAlertDedupRecord(BaseModel):
    """Persistent dedup record."""

    dedup_key: str
    alert_id: str
    federation_id: str | None = None
    channel: str | None = None
    metric: str | None = None
    severity: str | None = None
    occurrence_count: int = 1
    first_seen_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    status: str = "open"  # open | resolved | expired
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("dedup_key")
    @classmethod
    def _validate_dedup_key(cls, v: str) -> str:
        if not v:
            raise ValueError("dedup_key must not be empty")
        return v

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str) -> str:
        if v not in ("open", "resolved", "expired"):
            raise ValueError(f"status must be open/resolved/expired, got '{v}'")
        return v


# ---------------------------------------------------------------------------
# Store Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class NotificationAlertDedupStore(Protocol):
    """Persistent store for alert dedup records."""

    def get(self, dedup_key: str) -> NotificationAlertDedupRecord | None: ...
    def upsert(self, record: NotificationAlertDedupRecord) -> NotificationAlertDedupRecord: ...
    def mark_resolved(self, dedup_key: str, resolved_at: datetime) -> NotificationAlertDedupRecord | None: ...
    def list_active(
        self,
        now: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[NotificationAlertDedupRecord]: ...
    def prune_expired(self, now: datetime | None = None) -> int: ...


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryNotificationAlertDedupStore:
    """In-memory dedup store."""

    def __init__(self) -> None:
        self._records: dict[str, NotificationAlertDedupRecord] = {}

    def get(self, dedup_key: str) -> NotificationAlertDedupRecord | None:
        return self._records.get(dedup_key)

    def upsert(self, record: NotificationAlertDedupRecord) -> NotificationAlertDedupRecord:
        self._records[record.dedup_key] = record
        return record

    def mark_resolved(self, dedup_key: str, resolved_at: datetime) -> NotificationAlertDedupRecord | None:
        record = self._records.get(dedup_key)
        if record is None:
            return None
        record.status = "resolved"
        # Don't modify last_seen_at for resolved records
        return record

    def list_active(
        self,
        now: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[NotificationAlertDedupRecord]:
        if now is None:
            now = datetime.now(timezone.utc)
        active = [r for r in self._records.values() if r.status == "open" and r.expires_at > now]
        active.sort(key=lambda r: r.last_seen_at, reverse=True)
        return active[offset: offset + limit]

    def prune_expired(self, now: datetime | None = None) -> int:
        if now is None:
            now = datetime.now(timezone.utc)
        expired_keys = [
            k for k, r in self._records.items()
            if r.status == "open" and r.expires_at <= now
        ]
        for key in expired_keys:
            self._records[key].status = "expired"
        return len(expired_keys)


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteNotificationAlertDedupStore:
    """SQLite-backed dedup store."""

    def __init__(self, db_path: str) -> None:
        self._db_path = str(Path(db_path).resolve())
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS notification_alert_dedup (
                dedup_key TEXT PRIMARY KEY,
                alert_id TEXT NOT NULL,
                federation_id TEXT,
                channel TEXT,
                metric TEXT,
                severity TEXT,
                occurrence_count INTEGER NOT NULL DEFAULT 1,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                metadata TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_dedup_status_expires
                ON notification_alert_dedup(status, expires_at);
            CREATE INDEX IF NOT EXISTS idx_dedup_last_seen
                ON notification_alert_dedup(last_seen_at);
        """)
        self._conn.commit()

    def get(self, dedup_key: str) -> NotificationAlertDedupRecord | None:
        row = self._conn.execute(
            "SELECT * FROM notification_alert_dedup WHERE dedup_key=?", (dedup_key,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def upsert(self, record: NotificationAlertDedupRecord) -> NotificationAlertDedupRecord:
        self._conn.execute(
            """INSERT OR REPLACE INTO notification_alert_dedup
               (dedup_key, alert_id, federation_id, channel, metric, severity,
                occurrence_count, first_seen_at, last_seen_at, expires_at, status, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.dedup_key,
                record.alert_id,
                record.federation_id,
                record.channel,
                record.metric,
                record.severity,
                record.occurrence_count,
                record.first_seen_at.isoformat(),
                record.last_seen_at.isoformat(),
                record.expires_at.isoformat(),
                record.status,
                _json_dumps(record.metadata),
            ),
        )
        self._conn.commit()
        return record

    def mark_resolved(self, dedup_key: str, resolved_at: datetime) -> NotificationAlertDedupRecord | None:
        record = self.get(dedup_key)
        if record is None:
            return None
        record.status = "resolved"
        self._conn.execute(
            "UPDATE notification_alert_dedup SET status='resolved' WHERE dedup_key=?",
            (dedup_key,),
        )
        self._conn.commit()
        return record

    def list_active(
        self,
        now: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[NotificationAlertDedupRecord]:
        if now is None:
            now = datetime.now(timezone.utc)
        rows = self._conn.execute(
            "SELECT * FROM notification_alert_dedup "
            "WHERE status='open' AND expires_at > ? "
            "ORDER BY last_seen_at DESC LIMIT ? OFFSET ?",
            (now.isoformat(), limit, offset),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def prune_expired(self, now: datetime | None = None) -> int:
        if now is None:
            now = datetime.now(timezone.utc)
        cursor = self._conn.execute(
            "UPDATE notification_alert_dedup SET status='expired' "
            "WHERE status='open' AND expires_at <= ?",
            (now.isoformat(),),
        )
        self._conn.commit()
        return cursor.rowcount

    def _row_to_record(self, row: sqlite3.Row) -> NotificationAlertDedupRecord:
        data = dict(row)
        data["first_seen_at"] = datetime.fromisoformat(data["first_seen_at"])
        data["last_seen_at"] = datetime.fromisoformat(data["last_seen_at"])
        data["expires_at"] = datetime.fromisoformat(data["expires_at"])
        data["metadata"] = _json_loads(data.get("metadata") or "{}")
        return NotificationAlertDedupRecord(**data)

    def close(self) -> None:
        self._conn.close()


def _json_dumps(data: dict[str, Any]) -> str:
    import json
    return json.dumps(data, default=str)


def _json_loads(data: str) -> dict[str, Any]:
    import json
    return json.loads(data)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_notification_alert_dedup_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> NotificationAlertDedupStore:
    """Factory for creating dedup store instances."""
    if store_type == "memory":
        return InMemoryNotificationAlertDedupStore()
    if store_type == "sqlite":
        return SQLiteNotificationAlertDedupStore(
            db_path=db_path or ".agent_app/federation_notification_dedup.db"
        )
    raise ValueError(
        f"Unknown dedup store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )


# ---------------------------------------------------------------------------
# Dedup Service (Phase 54 — now with store support)
# ---------------------------------------------------------------------------


class NotificationAlertDedupService:
    """Suppresses or merges duplicate alert delivery events within a time window.

    Phase 55: If a store is provided, dedup decisions are persisted.
    Otherwise falls back to in-memory _recent dict (Phase 54 behavior).
    """

    def __init__(
        self,
        merge_window_seconds: int = 300,
        key_fields: list[str] | None = None,
        store: NotificationAlertDedupStore | None = None,
    ) -> None:
        self._merge_window = timedelta(seconds=merge_window_seconds)
        self._key_fields = key_fields or ["alert_id", "target_id"]
        self._store = store
        self._recent: dict[str, datetime] = {}

    def _dedup_key(self, alert_id: str, target_id: str) -> str:
        return f"{alert_id}:{target_id}"

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def should_suppress_or_merge(
        self,
        alert_id: str,
        target_id: str,
        now: datetime | None = None,
        federation_id: str | None = None,
        channel: str | None = None,
        metric: str | None = None,
        severity: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Check if an alert delivery should be suppressed or merged.

        Returns a decision dict:
            suppressed: True if this is a duplicate within the merge window
            merged_with: ID of the original event this was merged with (if any)
            reason: Human-readable explanation
        """
        if now is None:
            now = self._now()

        key = self._dedup_key(alert_id, target_id)

        if self._store is not None:
            return self._should_suppress_or_merge_with_store(
                key, alert_id, now, federation_id, channel, metric, severity, metadata,
            )
        return self._should_suppress_or_merge_memory(key, alert_id, now)

    def _should_suppress_or_merge_with_store(
        self,
        key: str,
        alert_id: str,
        now: datetime,
        federation_id: str | None,
        channel: str | None,
        metric: str | None,
        severity: str | None,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        existing = self._store.get(key)
        if existing is not None and existing.status == "open":
            if now - existing.last_seen_at <= self._merge_window:
                # Merge — increment count, update last_seen
                existing.occurrence_count += 1
                existing.last_seen_at = now
                existing.metadata = _redact_metadata(metadata or existing.metadata)
                self._store.upsert(existing)
                return {
                    "alert_id": alert_id,
                    "suppressed": True,
                    "merged_with": key,
                    "reason": f"Duplicate within merge window (occurrence #{existing.occurrence_count})",
                }
            # Outside window but still open — treat as new
            existing.status = "expired"
            self._store.upsert(existing)

        # Create new record
        record = NotificationAlertDedupRecord(
            dedup_key=key,
            alert_id=alert_id,
            federation_id=federation_id,
            channel=channel,
            metric=metric,
            severity=severity,
            occurrence_count=1,
            first_seen_at=now,
            last_seen_at=now,
            expires_at=now + self._merge_window,
            metadata=_redact_metadata(metadata or {}),
        )
        self._store.upsert(record)
        return {
            "alert_id": alert_id,
            "suppressed": False,
            "merged_with": None,
            "reason": "No duplicate found",
        }

    def _should_suppress_or_merge_memory(
        self, key: str, alert_id: str, now: datetime,
    ) -> dict[str, Any]:
        if key in self._recent:
            last_seen = self._recent[key]
            if now - last_seen <= self._merge_window:
                return {
                    "alert_id": alert_id,
                    "suppressed": True,
                    "merged_with": key,
                    "reason": f"Duplicate within merge window (last seen {last_seen.isoformat()})",
                }
        self._recent[key] = now
        return {
            "alert_id": alert_id,
            "suppressed": False,
            "merged_with": None,
            "reason": "No duplicate found",
        }

    def prune(self, now: datetime | None = None) -> None:
        """Remove expired entries from the dedup cache."""
        if now is None:
            now = self._now()
        if self._store is not None:
            self._store.prune_expired(now)
            return
        cutoff = now - self._merge_window
        self._recent = {
            k: v for k, v in self._recent.items() if v > cutoff
        }


def _redact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive keys from metadata."""
    result = {}
    for k, v in metadata.items():
        if k.lower() in _SENSITIVE_KEYS:
            result[k] = "[REDACTED]"
        else:
            result[k] = v
    return result
