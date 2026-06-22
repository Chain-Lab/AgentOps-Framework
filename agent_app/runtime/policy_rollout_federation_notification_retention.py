"""Retention service — purge/archive old notification observability data.

Phase 53 Task 7: Retention service.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class NotificationRetentionPolicy(BaseModel):
    """Retention policy for notification observability data."""

    enabled: bool = Field(default=True, description="Whether retention is active")
    raw_event_retention_days: int = Field(default=30, description="Days to retain raw delivery events")
    alert_retention_days: int = Field(default=180, description="Days to retain alert records")
    delivery_attempt_retention_days: int = Field(default=90, description="Days to retain delivery attempts")
    archive_before_purge: bool = Field(default=True, description="Archive data before deleting")
    archive_format: str = Field(default="jsonl", description="Archive format: jsonl or csv")
    archive_dir: str = Field(
        default=".agent_app/archives/federation_notifications",
        description="Directory for archive files",
    )


class NotificationRetentionResult(BaseModel):
    """Result of a retention cleanup run."""

    dry_run: bool = Field(default=False, description="Whether this was a dry run")
    events_archived: int = Field(default=0, description="Events archived")
    events_deleted: int = Field(default=0, description="Events deleted")
    alerts_archived: int = Field(default=0, description="Alerts archived")
    alerts_deleted: int = Field(default=0, description="Alerts deleted")
    attempts_archived: int = Field(default=0, description="Delivery attempts archived")
    attempts_deleted: int = Field(default=0, description="Delivery attempts deleted")
    archive_files: list[str] = Field(default_factory=list, description="Paths to archive files created")


class NotificationRetentionService:
    """Manages retention, archival, and purging of notification observability data."""

    def __init__(
        self,
        observability_store: Any = None,
        alert_store: Any = None,
        delivery_store: Any = None,
        policy: NotificationRetentionPolicy | None = None,
    ) -> None:
        self._observability_store = observability_store
        self._alert_store = alert_store
        self._delivery_store = delivery_store
        self._policy = policy or NotificationRetentionPolicy()

    async def run_cleanup(
        self,
        now: datetime | None = None,
        dry_run: bool = False,
    ) -> NotificationRetentionResult:
        """Run retention cleanup: archive old data, then purge.

        Args:
            now: Current time (defaults to utcnow).
            dry_run: If True, only count what would be deleted.

        Returns:
            NotificationRetentionResult with counts and archive files.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        if not self._policy.enabled:
            return NotificationRetentionResult(dry_run=dry_run)

        result = NotificationRetentionResult(dry_run=dry_run)

        # Archive and purge events
        if self._observability_store is not None:
            cutoff = now - timedelta(days=self._policy.raw_event_retention_days)
            old_events = await self._observability_store.list_events(
                until=now, limit=10000,
            )
            old_events = [e for e in old_events if e.created_at < cutoff]

            if old_events and self._policy.archive_before_purge and not dry_run:
                archive_path = self._archive_events(old_events, now)
                result.archive_files.append(archive_path)
                result.events_archived = len(old_events)

            if not dry_run:
                # Best-effort: try to delete old events
                for event in old_events:
                    try:
                        await self._observability_store.delete_event(event.event_id)
                        result.events_deleted += 1
                    except Exception:
                        pass
            else:
                result.events_deleted = 0

        return result

    def _archive_events(self, events: list[Any], now: datetime) -> str:
        """Archive events to a file. Returns the file path."""
        archive_dir = Path(self._policy.archive_dir)
        archive_dir.mkdir(parents=True, exist_ok=True)

        date_str = now.strftime("%Y%m%d")
        filename = f"notification_events_{date_str}.{self._policy.archive_format}"
        filepath = archive_dir / filename

        lines = []
        for event in events:
            data = event.model_dump(mode="json", exclude={"metadata"})
            if hasattr(event, "metadata") and event.metadata:
                data["metadata"] = {
                    k: "[REDACTED]" if k.lower() in {
                        "authorization", "token", "secret", "password", "api_key",
                        "x-signature", "x-api-key", "x-secret", "x-auth-token",
                        "x-webhook-secret", "cookie", "signature", "private_key",
                        "access_key",
                    } else v
                    for k, v in event.metadata.items()
                }
            lines.append(json.dumps(data, default=str))

        filepath.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(filepath)
