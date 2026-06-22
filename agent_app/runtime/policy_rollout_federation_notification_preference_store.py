"""Federation notification preference store — Protocol, InMemory, SQLite, factory.

Phase 51: Preference persistence for notification delivery opt-in/opt-out management.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from agent_app.governance.policy_rollout_federation_notification_preference import (
    FederationNotificationPreference,
    FederationNotificationPreferenceDecision,
    FederationNotificationPreferenceSubjectType,
)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class FederationNotificationPreferenceStore(Protocol):
    """Protocol for persisting federation notification preference rules."""

    async def set_preference(self, preference: FederationNotificationPreference) -> FederationNotificationPreference: ...
    async def get_preference(self, preference_id: str) -> FederationNotificationPreference | None: ...
    async def delete_preference(self, preference_id: str) -> None: ...
    async def list_preferences(
        self,
        subject_type: str | None = None,
        subject_id: str | None = None,
        federation_id: str | None = None,
        channel: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FederationNotificationPreference]: ...
    async def resolve_effective_preference(
        self,
        subject_type: str,
        subject_id: str,
        federation_id: str | None = None,
        approval_id: str | None = None,
        event_type: str | None = None,
        channel: str | None = None,
    ) -> FederationNotificationPreference | None: ...


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryFederationNotificationPreferenceStore:
    """In-memory federation notification preference store."""

    def __init__(self) -> None:
        self._items: dict[str, FederationNotificationPreference] = {}

    async def set_preference(self, preference: FederationNotificationPreference) -> FederationNotificationPreference:
        self._items[preference.preference_id] = preference
        return preference

    async def get_preference(self, preference_id: str) -> FederationNotificationPreference | None:
        return self._items.get(preference_id)

    async def delete_preference(self, preference_id: str) -> None:
        self._items.pop(preference_id, None)

    async def list_preferences(
        self,
        subject_type: str | None = None,
        subject_id: str | None = None,
        federation_id: str | None = None,
        channel: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FederationNotificationPreference]:
        items = list(self._items.values())
        if subject_type is not None:
            items = [i for i in items if i.subject_type == subject_type]
        if subject_id is not None:
            items = [i for i in items if i.subject_id == subject_id]
        if federation_id is not None:
            items = [i for i in items if i.federation_id == federation_id]
        if channel is not None:
            items = [i for i in items if i.channel == channel]
        if event_type is not None:
            items = [i for i in items if i.event_type == event_type]
        items.sort(key=lambda i: i.created_at)
        return items[offset : offset + limit]

    async def resolve_effective_preference(
        self,
        subject_type: str,
        subject_id: str,
        federation_id: str | None = None,
        approval_id: str | None = None,
        event_type: str | None = None,
        channel: str | None = None,
    ) -> FederationNotificationPreference | None:
        """Find the most specific matching preference for the given subject.

        Priority (most specific first):
        1. approval_id + event_type + channel
        2. federation_id + event_type + channel
        3. event_type + channel
        4. channel only
        5. Subject global preference
        6. None

        Conflict: OPT_OUT wins over OPT_IN at same specificity.
        """
        candidates = [
            i for i in self._items.values()
            if i.subject_type == subject_type
            and i.subject_id == subject_id
            and i.decision != FederationNotificationPreferenceDecision.INHERIT
        ]

        if not candidates:
            return None

        # Score each candidate by specificity
        scored: list[tuple[int, FederationNotificationPreference]] = []
        for pref in candidates:
            score = 0
            # Check approval_id match
            if pref.approval_id is not None:
                if approval_id is not None and pref.approval_id == approval_id:
                    score += 4
                else:
                    continue  # preference requires approval_id but doesn't match
            # Check federation_id match
            if pref.federation_id is not None:
                if federation_id is not None and pref.federation_id == federation_id:
                    score += 2
                else:
                    continue  # preference requires federation_id but doesn't match
            # Check event_type match
            if pref.event_type is not None:
                if event_type is not None and pref.event_type == event_type:
                    score += 1
                else:
                    continue  # preference requires event_type but doesn't match
            # Check channel match
            if pref.channel is not None:
                if channel is not None and pref.channel == channel:
                    score += 1
                else:
                    continue  # preference requires channel but doesn't match
            scored.append((score, pref))

        if not scored:
            return None

        # Sort by score descending; within same score, OPT_OUT wins
        scored.sort(key=lambda pair: pair[0], reverse=True)
        max_score = scored[0][0]
        top_candidates = [pref for s, pref in scored if s == max_score]

        # If multiple candidates at same specificity, OPT_OUT wins
        for pref in top_candidates:
            if pref.decision == FederationNotificationPreferenceDecision.OPT_OUT:
                return pref

        return top_candidates[0]


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteFederationNotificationPreferenceStore:
    """SQLite-backed federation notification preference store."""

    def __init__(self, db_path: str = ".agent_app/federation_notification_preference.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS federation_notification_preference (
                preference_id TEXT PRIMARY KEY,
                subject_type TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                federation_id TEXT,
                approval_id TEXT,
                event_type TEXT,
                channel TEXT,
                decision TEXT NOT NULL,
                reason TEXT,
                metadata_json TEXT NOT NULL,
                created_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_fnp_subject_type ON federation_notification_preference(subject_type);
            CREATE INDEX IF NOT EXISTS idx_fnp_subject_id ON federation_notification_preference(subject_id);
            CREATE INDEX IF NOT EXISTS idx_fnp_federation_id ON federation_notification_preference(federation_id);
            CREATE INDEX IF NOT EXISTS idx_fnp_channel ON federation_notification_preference(channel);
            CREATE INDEX IF NOT EXISTS idx_fnp_event_type ON federation_notification_preference(event_type);
        """)
        self._conn.commit()

    async def set_preference(self, preference: FederationNotificationPreference) -> FederationNotificationPreference:
        self._conn.execute(
            """INSERT OR REPLACE INTO federation_notification_preference
               (preference_id, subject_type, subject_id, federation_id, approval_id,
                event_type, channel, decision, reason, metadata_json,
                created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                preference.preference_id,
                preference.subject_type.value,
                preference.subject_id,
                preference.federation_id,
                preference.approval_id,
                preference.event_type,
                preference.channel,
                preference.decision.value,
                preference.reason,
                json.dumps(preference.metadata),
                preference.created_by,
                preference.created_at.isoformat(),
                preference.updated_at.isoformat(),
            ),
        )
        self._conn.commit()
        return preference

    async def get_preference(self, preference_id: str) -> FederationNotificationPreference | None:
        row = self._conn.execute(
            "SELECT * FROM federation_notification_preference WHERE preference_id=?",
            (preference_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_item(row)

    async def delete_preference(self, preference_id: str) -> None:
        self._conn.execute(
            "DELETE FROM federation_notification_preference WHERE preference_id=?",
            (preference_id,),
        )
        self._conn.commit()

    async def list_preferences(
        self,
        subject_type: str | None = None,
        subject_id: str | None = None,
        federation_id: str | None = None,
        channel: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FederationNotificationPreference]:
        conditions: list[str] = []
        params: list[str | int] = []

        if subject_type is not None:
            conditions.append("subject_type=?")
            params.append(subject_type)
        if subject_id is not None:
            conditions.append("subject_id=?")
            params.append(subject_id)
        if federation_id is not None:
            conditions.append("federation_id=?")
            params.append(federation_id)
        if channel is not None:
            conditions.append("channel=?")
            params.append(channel)
        if event_type is not None:
            conditions.append("event_type=?")
            params.append(event_type)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        params.extend([limit, offset])
        rows = self._conn.execute(
            f"SELECT * FROM federation_notification_preference {where} ORDER BY created_at ASC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [self._row_to_item(row) for row in rows]

    async def resolve_effective_preference(
        self,
        subject_type: str,
        subject_id: str,
        federation_id: str | None = None,
        approval_id: str | None = None,
        event_type: str | None = None,
        channel: str | None = None,
    ) -> FederationNotificationPreference | None:
        """Find the most specific matching preference for the given subject.

        Delegates to the same scoring logic used by the InMemory store,
        but querying from SQLite.
        """
        conditions = ["subject_type=?", "subject_id=?", "decision != ?"]
        params: list[str] = [subject_type, subject_id, FederationNotificationPreferenceDecision.INHERIT.value]

        rows = self._conn.execute(
            f"SELECT * FROM federation_notification_preference WHERE {' AND '.join(conditions)}",
            params,
        ).fetchall()

        candidates = [self._row_to_item(row) for row in rows]

        if not candidates:
            return None

        scored: list[tuple[int, FederationNotificationPreference]] = []
        for pref in candidates:
            score = 0
            if pref.approval_id is not None:
                if approval_id is not None and pref.approval_id == approval_id:
                    score += 4
                else:
                    continue
            if pref.federation_id is not None:
                if federation_id is not None and pref.federation_id == federation_id:
                    score += 2
                else:
                    continue
            if pref.event_type is not None:
                if event_type is not None and pref.event_type == event_type:
                    score += 1
                else:
                    continue
            if pref.channel is not None:
                if channel is not None and pref.channel == channel:
                    score += 1
                else:
                    continue
            scored.append((score, pref))

        if not scored:
            return None

        scored.sort(key=lambda pair: pair[0], reverse=True)
        max_score = scored[0][0]
        top_candidates = [pref for s, pref in scored if s == max_score]

        for pref in top_candidates:
            if pref.decision == FederationNotificationPreferenceDecision.OPT_OUT:
                return pref

        return top_candidates[0]

    def _row_to_item(self, row: sqlite3.Row) -> FederationNotificationPreference:
        data = dict(row)
        data["subject_type"] = FederationNotificationPreferenceSubjectType(data["subject_type"])
        data["decision"] = FederationNotificationPreferenceDecision(data["decision"])
        data["metadata"] = json.loads(data.pop("metadata_json"))
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return FederationNotificationPreference(**data)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_federation_notification_preference_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> FederationNotificationPreferenceStore:
    """Factory for creating federation notification preference store instances."""
    if store_type == "memory":
        return InMemoryFederationNotificationPreferenceStore()
    if store_type == "sqlite":
        return SQLiteFederationNotificationPreferenceStore(
            db_path=db_path or ".agent_app/federation_notification_preference.db"
        )
    raise ValueError(f"Unknown preference store type '{store_type}'. Supported: 'memory', 'sqlite'.")
