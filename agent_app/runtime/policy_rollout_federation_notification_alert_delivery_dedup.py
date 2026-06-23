"""Alert deduplication — suppress or merge duplicate alert delivery events.

Phase 54 Task 7: Alert deduplication/merge service.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any


_SENSITIVE_KEYS = frozenset({
    "authorization", "token", "secret", "password", "api_key",
    "x-signature", "x-api-key", "x-secret", "x-auth-token",
    "x-webhook-secret", "cookie", "signature", "private_key",
    "access_key",
})


class NotificationAlertDedupService:
    """Suppresses or merges duplicate alert delivery events within a time window."""

    def __init__(
        self,
        merge_window_seconds: int = 300,
        key_fields: list[str] | None = None,
    ) -> None:
        self._merge_window = timedelta(seconds=merge_window_seconds)
        self._key_fields = key_fields or ["alert_id", "target_id"]
        self._recent: dict[str, datetime] = {}

    def _dedup_key(self, alert_id: str, target_id: str) -> str:
        return f"{alert_id}:{target_id}"

    def should_suppress_or_merge(
        self,
        alert_id: str,
        target_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Check if an alert delivery should be suppressed or merged.

        Returns a decision dict:
            suppressed: True if this is a duplicate within the merge window
            merged_with: ID of the original event this was merged with (if any)
            reason: Human-readable explanation
        """
        if now is None:
            now = datetime.now(timezone.utc)

        key = self._dedup_key(alert_id, target_id)

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
            now = datetime.now(timezone.utc)
        cutoff = now - self._merge_window
        self._recent = {
            k: v for k, v in self._recent.items() if v > cutoff
        }
