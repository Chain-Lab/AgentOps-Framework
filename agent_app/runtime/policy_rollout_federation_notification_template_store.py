"""Federation notification template store — Protocol, InMemory, SQLite, factory.

Phase 51: Template persistence with version conflict checking and effective template lookup.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from agent_app.governance.policy_rollout_federation_notification_template import (
    FederationNotificationTemplate,
    FederationNotificationTemplateFormat,
)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class FederationNotificationTemplateStore(Protocol):
    """Protocol for persisting federation notification templates."""

    async def create(self, template: FederationNotificationTemplate) -> FederationNotificationTemplate: ...
    async def get(self, template_id: str) -> FederationNotificationTemplate | None: ...
    async def update(self, template: FederationNotificationTemplate) -> FederationNotificationTemplate: ...
    async def delete(self, template_id: str) -> None: ...
    async def list(
        self,
        name: str | None = None,
        event_type: str | None = None,
        channel: str | None = None,
        federation_id: str | None = None,
        enabled: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FederationNotificationTemplate]: ...
    async def find_effective_template(
        self,
        federation_id: str | None = None,
        event_type: str | None = None,
        channel: str | None = None,
    ) -> FederationNotificationTemplate | None: ...


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryFederationNotificationTemplateStore:
    """In-memory federation notification template store."""

    def __init__(self) -> None:
        self._items: dict[str, FederationNotificationTemplate] = {}

    async def create(self, template: FederationNotificationTemplate) -> FederationNotificationTemplate:
        self._items[template.template_id] = template
        return template

    async def get(self, template_id: str) -> FederationNotificationTemplate | None:
        return self._items.get(template_id)

    async def update(self, template: FederationNotificationTemplate) -> FederationNotificationTemplate:
        existing = self._items.get(template.template_id)
        if existing is None:
            raise ValueError(f"Template '{template.template_id}' not found")
        if existing.version != template.version - 1:
            raise ValueError(
                f"Version conflict: existing version is {existing.version}, "
                f"but template has version {template.version} (expected {existing.version + 1})"
            )
        self._items[template.template_id] = template
        return template

    async def delete(self, template_id: str) -> None:
        existing = self._items.get(template_id)
        if existing is not None:
            existing.enabled = False
            existing.updated_at = datetime.now(timezone.utc)

    async def list(
        self,
        name: str | None = None,
        event_type: str | None = None,
        channel: str | None = None,
        federation_id: str | None = None,
        enabled: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FederationNotificationTemplate]:
        items = list(self._items.values())
        if name is not None:
            items = [i for i in items if i.name == name]
        if event_type is not None:
            items = [i for i in items if i.event_type == event_type]
        if channel is not None:
            items = [i for i in items if i.channel == channel]
        if federation_id is not None:
            items = [i for i in items if i.federation_id == federation_id]
        if enabled is not None:
            items = [i for i in items if i.enabled == enabled]
        items.sort(key=lambda i: i.created_at)
        return items[offset : offset + limit]

    async def find_effective_template(
        self,
        federation_id: str | None = None,
        event_type: str | None = None,
        channel: str | None = None,
    ) -> FederationNotificationTemplate | None:
        """Find the most specific enabled template using priority matching.

        Priority: federation+event+channel > event+channel > channel > global.
        """
        candidates = [t for t in self._items.values() if t.enabled]

        # Priority 1: federation + event_type + channel
        if federation_id and event_type and channel:
            for t in candidates:
                if (
                    t.federation_id == federation_id
                    and t.event_type == event_type
                    and t.channel == channel
                ):
                    return t

        # Priority 2: event_type + channel
        if event_type and channel:
            for t in candidates:
                if (
                    t.federation_id is None
                    and t.event_type == event_type
                    and t.channel == channel
                ):
                    return t

        # Priority 3: channel only
        if channel:
            for t in candidates:
                if (
                    t.federation_id is None
                    and t.event_type is None
                    and t.channel == channel
                ):
                    return t

        # Priority 4: global (no federation, no event, no channel)
        for t in candidates:
            if t.federation_id is None and t.event_type is None and t.channel is None:
                return t

        return None


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteFederationNotificationTemplateStore:
    """SQLite-backed federation notification template store."""

    def __init__(self, db_path: str = ".agent_app/federation_notification_templates.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS federation_notification_templates (
                template_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                event_type TEXT,
                channel TEXT,
                federation_id TEXT,
                subject_template TEXT,
                body_template TEXT NOT NULL,
                format TEXT NOT NULL DEFAULT 'text',
                enabled INTEGER NOT NULL DEFAULT 1,
                version INTEGER NOT NULL DEFAULT 1,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_fnt_event_type ON federation_notification_templates(event_type);
            CREATE INDEX IF NOT EXISTS idx_fnt_channel ON federation_notification_templates(channel);
            CREATE INDEX IF NOT EXISTS idx_fnt_federation_id ON federation_notification_templates(federation_id);
            CREATE INDEX IF NOT EXISTS idx_fnt_event_channel_fed ON federation_notification_templates(event_type, channel, federation_id);
        """)
        self._conn.commit()

    async def create(self, template: FederationNotificationTemplate) -> FederationNotificationTemplate:
        self._conn.execute(
            """INSERT INTO federation_notification_templates
               (template_id, name, description, event_type, channel, federation_id,
                subject_template, body_template, format, enabled, version,
                metadata_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                template.template_id,
                template.name,
                template.description,
                template.event_type,
                template.channel,
                template.federation_id,
                template.subject_template,
                template.body_template,
                template.format.value,
                1 if template.enabled else 0,
                template.version,
                json.dumps(template.metadata),
                template.created_at.isoformat(),
                template.updated_at.isoformat(),
            ),
        )
        self._conn.commit()
        return template

    async def get(self, template_id: str) -> FederationNotificationTemplate | None:
        row = self._conn.execute(
            "SELECT * FROM federation_notification_templates WHERE template_id=?",
            (template_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_template(row)

    async def update(self, template: FederationNotificationTemplate) -> FederationNotificationTemplate:
        row = self._conn.execute(
            "SELECT * FROM federation_notification_templates WHERE template_id=?",
            (template.template_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Template '{template.template_id}' not found")
        existing = self._row_to_template(row)
        if existing.version != template.version - 1:
            raise ValueError(
                f"Version conflict: existing version is {existing.version}, "
                f"but template has version {template.version} (expected {existing.version + 1})"
            )
        self._conn.execute(
            """UPDATE federation_notification_templates
               SET name=?, description=?, event_type=?, channel=?, federation_id=?,
                   subject_template=?, body_template=?, format=?, enabled=?, version=?,
                   metadata_json=?, updated_at=?
               WHERE template_id=?""",
            (
                template.name,
                template.description,
                template.event_type,
                template.channel,
                template.federation_id,
                template.subject_template,
                template.body_template,
                template.format.value,
                1 if template.enabled else 0,
                template.version,
                json.dumps(template.metadata),
                template.updated_at.isoformat(),
                template.template_id,
            ),
        )
        self._conn.commit()
        return template

    async def delete(self, template_id: str) -> None:
        row = self._conn.execute(
            "SELECT * FROM federation_notification_templates WHERE template_id=?",
            (template_id,),
        ).fetchone()
        if row is not None:
            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "UPDATE federation_notification_templates SET enabled=0, updated_at=? WHERE template_id=?",
                (now, template_id),
            )
            self._conn.commit()

    async def list(
        self,
        name: str | None = None,
        event_type: str | None = None,
        channel: str | None = None,
        federation_id: str | None = None,
        enabled: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FederationNotificationTemplate]:
        conditions: list[str] = []
        params: list[str | int] = []

        if name is not None:
            conditions.append("name=?")
            params.append(name)
        if event_type is not None:
            conditions.append("event_type=?")
            params.append(event_type)
        if channel is not None:
            conditions.append("channel=?")
            params.append(channel)
        if federation_id is not None:
            conditions.append("federation_id=?")
            params.append(federation_id)
        if enabled is not None:
            conditions.append("enabled=?")
            params.append(1 if enabled else 0)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        params.extend([limit, offset])
        rows = self._conn.execute(
            f"SELECT * FROM federation_notification_templates {where} ORDER BY created_at ASC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [self._row_to_template(row) for row in rows]

    async def find_effective_template(
        self,
        federation_id: str | None = None,
        event_type: str | None = None,
        channel: str | None = None,
    ) -> FederationNotificationTemplate | None:
        """Find the most specific enabled template using SQL with ORDER BY specificity.

        Specificity score:
          federation_id + event_type + channel = 3
          event_type + channel (no federation) = 2
          channel only (no federation, no event) = 1
          global (no federation, no event, no channel) = 0
        """
        # Build specificity-based query: only match enabled templates,
        # compute a specificity score, and return the highest match.
        conditions: list[str] = ["enabled=1"]
        params: list[str] = []

        # Each candidate must match the requested filters or be None (wildcard)
        if federation_id is not None:
            conditions.append("(federation_id=? OR federation_id IS NULL)")
            params.append(federation_id)
        else:
            conditions.append("federation_id IS NULL")

        if event_type is not None:
            conditions.append("(event_type=? OR event_type IS NULL)")
            params.append(event_type)
        else:
            conditions.append("event_type IS NULL")

        if channel is not None:
            conditions.append("(channel=? OR channel IS NULL)")
            params.append(channel)
        else:
            conditions.append("channel IS NULL")

        where = "WHERE " + " AND ".join(conditions)

        # Compute specificity: more non-NULL fields = more specific
        specificity = """
            (CASE WHEN federation_id IS NOT NULL THEN 1 ELSE 0 END) +
            (CASE WHEN event_type IS NOT NULL THEN 1 ELSE 0 END) +
            (CASE WHEN channel IS NOT NULL THEN 1 ELSE 0 END)
        """

        row = self._conn.execute(
            f"SELECT * FROM federation_notification_templates {where} ORDER BY {specificity} DESC LIMIT 1",
            params,
        ).fetchone()
        if row is None:
            return None
        return self._row_to_template(row)

    def _row_to_template(self, row: sqlite3.Row) -> FederationNotificationTemplate:
        data = dict(row)
        data["format"] = FederationNotificationTemplateFormat(data["format"])
        data["enabled"] = bool(data["enabled"])
        data["metadata"] = json.loads(data.pop("metadata_json"))
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return FederationNotificationTemplate(**data)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_federation_notification_template_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> FederationNotificationTemplateStore:
    """Factory for creating federation notification template store instances."""
    if store_type == "memory":
        return InMemoryFederationNotificationTemplateStore()
    if store_type == "sqlite":
        return SQLiteFederationNotificationTemplateStore(
            db_path=db_path or ".agent_app/federation_notification_templates.db"
        )
    raise ValueError(
        f"Unknown template store type '{store_type}'. Supported: 'memory', 'sqlite'."
    )
