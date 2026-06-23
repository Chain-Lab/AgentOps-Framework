"""Resumable archive cleanup service.

Phase 55 Task 6: Archive checkpoint + resumable cleanup for old rollup data.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agent_app.runtime.policy_rollout_federation_notification_archive_cleanup import (
    ArchiveCheckpoint,
    ArchiveCheckpointStore,
    ArchiveCleanupPolicy,
    ArchiveCleanupResult,
)
from agent_app.runtime.policy_rollout_federation_notification_rollup import (
    NotificationMetricsRollup,
)
from agent_app.governance.policy_change_event import PolicyChangeEventType


class ResumableArchiveCleanup:
    """Archive and cleanup old rollup data with resumable checkpoint support.

    Processes records in batches, records a checkpoint after each batch,
    and can resume from the last checkpoint if interrupted.
    """

    def __init__(
        self,
        checkpoint_store: ArchiveCheckpointStore,
        policy: ArchiveCleanupPolicy | None = None,
        rollup_store: Any = None,
        audit_logger: Any = None,
        change_event_store: Any = None,
    ) -> None:
        self._checkpoint_store = checkpoint_store
        self._policy = policy or ArchiveCleanupPolicy()
        self._rollup_store = rollup_store
        self._audit_logger = audit_logger
        self._change_event_store = change_event_store

    def _record_change_event(
        self,
        event_type: PolicyChangeEventType,
        payload: dict[str, Any],
    ) -> None:
        """Best-effort change event recording — never break the caller on failure."""
        if self._change_event_store is None:
            return
        try:
            self._change_event_store.record(
                event_type=event_type,
                payload=payload,
            )
        except Exception:  # noqa: BLE001 — best-effort
            pass

    async def run_cleanup(
        self,
        data_type: str = "rollup",
        dry_run: bool = False,
        now: datetime | None = None,
    ) -> ArchiveCleanupResult:
        """Run resumable archive cleanup for a data type.

        Resumes from the latest checkpoint for the data type if one exists.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        if not self._policy.enabled:
            return ArchiveCleanupResult(dry_run=dry_run, data_type=data_type)

        result = ArchiveCleanupResult(dry_run=dry_run, data_type=data_type)

        # Find existing checkpoint for resumption
        existing = await self._checkpoint_store.get_latest_checkpoint(data_type)
        if existing is not None and not existing.is_complete:
            result.checkpoint_id = existing.checkpoint_id
            last_id = existing.last_processed_id
            last_at = existing.last_processed_at
        else:
            # Start fresh
            checkpoint_id = f"acp_{data_type}_{now.strftime('%Y%m%d%H%M%S')}"
            existing = ArchiveCheckpoint(
                checkpoint_id=checkpoint_id,
                data_type=data_type,
                created_at=now,
                updated_at=now,
                batch_size=self._policy.batch_size,
            )
            await self._checkpoint_store.record_checkpoint(existing)
            result.checkpoint_id = checkpoint_id
            last_id = None
            last_at = None

        try:
            if data_type == "rollup":
                await self._process_rollups(result, last_id, last_at, dry_run, now)
            else:
                result.error = f"Unknown data type: {data_type}"
                return result

            # Mark complete
            existing.is_complete = True
            existing.updated_at = now
            await self._checkpoint_store.record_checkpoint(existing)
            result.is_complete = True

            self._record_change_event(
                event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_ARCHIVE_CLEANUP_COMPLETED,
                payload={
                    "data_type": data_type,
                    "checkpoint_id": result.checkpoint_id,
                    "records_processed": result.records_processed,
                    "is_complete": True,
                },
            )
            self._audit(f"archive_cleanup_complete", {
                "data_type": data_type,
                "checkpoint_id": result.checkpoint_id,
                "records_processed": result.records_processed,
                "is_complete": True,
            })
        except Exception as exc:
            result.error = str(exc)
            existing.updated_at = now
            existing.last_processed_id = last_id
            existing.last_processed_at = last_at
            await self._checkpoint_store.record_checkpoint(existing)
            self._record_change_event(
                event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_ARCHIVE_CLEANUP_FAILED,
                payload={
                    "data_type": data_type,
                    "checkpoint_id": result.checkpoint_id,
                    "error": str(exc),
                },
            )
            self._audit(f"archive_cleanup_error", {
                "data_type": data_type,
                "checkpoint_id": result.checkpoint_id,
                "error": str(exc),
            })

        return result

    async def _process_rollups(
        self,
        result: ArchiveCleanupResult,
        last_id: str | None,
        last_at: datetime | None,
        dry_run: bool,
        now: datetime,
    ) -> None:
        """Process rollup records: archive old ones, then purge."""
        if self._rollup_store is None:
            return

        cutoff = now - timedelta(days=self._policy.rollup_retention_days)

        # Fetch rollups older than cutoff
        rollups = await self._rollup_store.list_rollups(
            limit=self._policy.batch_size,
            offset=0,
        )
        old_rollups = [r for r in rollups if r.window_end < cutoff]

        if last_id is not None:
            old_rollups = [r for r in old_rollups if r.rollup_id > last_id]

        if not old_rollups:
            result.is_complete = True
            return

        # Archive to file
        if not dry_run:
            archive_path = self._archive_rollups(old_rollups, now)
            result.archive_files.append(archive_path)

        result.records_archived = len(old_rollups)
        result.records_processed += len(old_rollups)

        # Update checkpoint
        last_processed = old_rollups[-1]
        checkpoint = await self._checkpoint_store.get_latest_checkpoint("rollup")
        if checkpoint is not None:
            checkpoint.last_processed_id = last_processed.rollup_id
            checkpoint.last_processed_at = last_processed.window_end
            checkpoint.records_processed = result.records_processed
            checkpoint.updated_at = now
            await self._checkpoint_store.record_checkpoint(checkpoint)

        if not dry_run:
            result.records_deleted = len(old_rollups)

        self._record_change_event(
            event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_ARCHIVE_CLEANUP_STARTED,
            payload={
                "data_type": "rollup",
                "batch_size": len(old_rollups),
                "records_processed": result.records_processed,
            },
        )
        self._audit("archive_cleanup_batch", {
            "data_type": "rollup",
            "batch_size": len(old_rollups),
            "records_processed": result.records_processed,
        })

    def _archive_rollups(self, rollups: list[NotificationMetricsRollup], now: datetime) -> str:
        """Archive rollups to a JSONL file."""
        archive_dir = Path(self._policy.archive_dir)
        archive_dir.mkdir(parents=True, exist_ok=True)

        date_str = now.strftime("%Y%m%d")
        filename = f"rollup_archive_{date_str}.{self._policy.archive_format}"
        filepath = archive_dir / filename

        lines = []
        for rollup in rollups:
            data = rollup.model_dump(mode="json")
            lines.append(json.dumps(data, default=str))

        filepath.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(filepath)

    async def prune_old_archives(self, now: datetime | None = None, dry_run: bool = False) -> int:
        """Remove archive files older than the retention policy.

        Returns the number of files removed (or would be removed in dry_run).
        """
        if now is None:
            now = datetime.now(timezone.utc)
        if not self._policy.enabled:
            return 0

        archive_dir = Path(self._policy.archive_dir)
        if not archive_dir.exists():
            return 0

        cutoff = now - timedelta(days=self._policy.checkpoint_retention_days)
        removed = 0

        for filepath in archive_dir.glob(f"*.{self._policy.archive_format}"):
            try:
                mtime = datetime.fromtimestamp(filepath.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    if not dry_run:
                        filepath.unlink()
                    removed += 1
            except OSError:
                pass

        self._audit("archive_pruned", {"files_removed": removed, "dry_run": dry_run})
        return removed

    async def prune_old_checkpoints(self, now: datetime | None = None) -> int:
        """Remove checkpoints older than the retention policy."""
        if now is None:
            now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=self._policy.checkpoint_retention_days)
        return await self._checkpoint_store.prune_old_checkpoints(cutoff)

    def _audit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._audit_logger is None:
            return
        try:
            self._audit_logger(event_type, payload)
        except Exception:
            pass
