"""Redis-backed alert priority queue store.

Phase 59 Task 732: Multi-instance priority queue with atomic claim.
Requires optional dependency: pip install 'agent-app-framework[redis]'
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryChannelType,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_priority_queue_store import (
    AlertPriorityQueueItem,
    AlertPriorityQueueItemStatus,
    _now,
    _redact_error,
)


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------

try:
    import redis as redis_pkg
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Redis key helpers
# ---------------------------------------------------------------------------

def _make_keys(prefix: str, queue_name: str, queue_id: str = "default") -> dict[str, str]:
    """Build Redis keys for a given queue."""
    base = f"{prefix}:pq:{queue_name}:{queue_id}"
    return {
        "items": f"{base}:items",
        "queued": f"{base}:queued",
        "claimed": f"{base}:claimed",
        "completed": f"{base}:completed",
        "failed": f"{base}:failed",
        "requeued": f"{base}:requeued",
        "cancelled": f"{base}:cancelled",
        "expired": f"{base}:expired",
    }


# ---------------------------------------------------------------------------
# Redis store
# ---------------------------------------------------------------------------

class RedisAlertPriorityQueueStore:
    """Redis-backed alert priority queue store.

    Uses sorted sets for ordering and hashes for item data.
    Claim uses per-item Python logic (works with fakeredis; production
    deployments should enable WATCH/MULTI/EXEC or Lua via redis-py pipeline).
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        key_prefix: str = "agentapp:notification:pq",
        queue_name: str = "default",
        queue_id: str = "default",
    ) -> None:
        if not _REDIS_AVAILABLE:
            raise ImportError(
                "Redis priority queue requires optional dependency. Install with:\n"
                "pip install 'agent-app-framework[redis]'"
            )
        self._client = redis_pkg.Redis.from_url(redis_url)
        self._key_prefix = key_prefix
        self._queue_name = queue_name
        self._queue_id = queue_id
        self._keys = _make_keys(key_prefix, queue_name, queue_id)

    async def enqueue(self, item: AlertPriorityQueueItem) -> AlertPriorityQueueItem:
        item_data = item.model_dump(mode="json")
        self._client.hset(self._keys["items"], item.attempt_id, json.dumps(item_data))
        # Use available_at timestamp as score for time-based ordering
        score = item.available_at.timestamp()
        self._client.zadd(self._keys["queued"], {item.attempt_id: score})
        return item

    async def dequeue(
        self,
        status: str | None = None,
        limit: int = 100,
    ) -> list[AlertPriorityQueueItem]:
        if status is not None:
            key = self._keys.get(status, self._keys["queued"])
            attempt_ids = self._client.zrange(key, 0, limit - 1)
        else:
            attempt_ids = self._client.zrange(self._keys["queued"], 0, limit - 1)
        items = []
        for aid in attempt_ids:
            if aid:
                item = await self._get_item(aid)
                if item:
                    items.append(item)
        return items

    async def count(self, status: str | None = None) -> int:
        if status is not None:
            key = self._keys.get(status, self._keys["queued"])
            return self._client.zcard(key)
        total = 0
        for k in self._keys.values():
            if any(k.endswith(f":{s}") for s in (
                "queued", "claimed", "completed", "failed", "requeued", "cancelled", "expired"
            )):
                total += self._client.zcard(k)
        return total

    async def count_by_priority(self, status: str | None = None) -> dict[int, int]:
        items = await self.dequeue(status=status, limit=10000)
        counts: dict[int, int] = {}
        for item in items:
            counts[item.priority] = counts.get(item.priority, 0) + 1
        return counts

    async def update_status(
        self, attempt_id: str, status: str
    ) -> AlertPriorityQueueItem | None:
        item = await self._get_item(attempt_id)
        if item is None:
            return None
        item.status = status  # type: ignore[assignment]
        await self._save_item(item)
        return item

    async def remove(self, attempt_id: str) -> bool:
        # Remove from all sorted set indexes (skip the items hash)
        for key_name, key in self._keys.items():
            if key_name == "items":
                continue
            self._client.zrem(key, attempt_id)
        return self._client.hdel(self._keys["items"], attempt_id) > 0

    async def claim_next(
        self,
        now: datetime | None = None,
        limit: int = 100,
        worker_id: str | None = None,
        lease_seconds: int = 300,
    ) -> list[AlertPriorityQueueItem]:
        """Claim highest-priority claimable items.

        Uses Python-level atomic claim per item (compatible with fakeredis).
        Production: enable WATCH/MULTI/EXEC for full atomicity.
        """
        if now is None:
            now = _now()
        now_ts = now.timestamp()

        # Get candidates from queued sorted set (score = available_at timestamp)
        candidate_ids = self._client.zrangebyscore(
            self._keys["queued"], 0, now_ts
        )

        # Fetch all candidates and sort by priority DESC, available_at ASC, created_at ASC
        candidates: list[tuple[float, float, float, str, str]] = []
        for attempt_id in candidate_ids:
            raw_data = self._client.hget(self._keys["items"], attempt_id)
            if not raw_data:
                continue
            data = json.loads(raw_data)
            if data.get("status") not in ("queued", "requeued"):
                continue
            avail_str = data.get("available_at", "")
            avail_ts = datetime.fromisoformat(avail_str).timestamp() if avail_str else 0
            created_str = data.get("created_at", "")
            created_ts = datetime.fromisoformat(created_str).timestamp() if created_str else 0
            candidates.append((
                -data.get("priority", 0),  # negative for DESC sort
                avail_ts,
                created_ts,
                attempt_id,
                raw_data,
            ))

        # Sort: priority DESC, available_at ASC, created_at ASC
        candidates.sort(key=lambda c: (c[0], c[1], c[2]))

        claimed: list[AlertPriorityQueueItem] = []
        for _, _, _, attempt_id, raw_data in candidates:
            if len(claimed) >= limit:
                break
            data = json.loads(raw_data)

            # Update item fields
            data["status"] = "claimed"
            data["claimed_by"] = worker_id
            data["claimed_at"] = now.isoformat()
            data["lease_expires_at"] = (now + timedelta(seconds=lease_seconds)).isoformat()

            self._client.hset(self._keys["items"], attempt_id, json.dumps(data))
            self._client.zrem(self._keys["queued"], attempt_id)
            lease_ts = (now + timedelta(seconds=lease_seconds)).timestamp()
            self._client.zadd(self._keys["claimed"], {attempt_id: lease_ts})

            # Parse back into model
            data["channel_type"] = AlertDeliveryChannelType(data["channel_type"])
            data["created_at"] = datetime.fromisoformat(data["created_at"])
            if data.get("available_at"):
                data["available_at"] = datetime.fromisoformat(data["available_at"])
            else:
                data["available_at"] = data.get("created_at", _now())
            data["claimed_at"] = now
            data["lease_expires_at"] = now + timedelta(seconds=lease_seconds)
            item = AlertPriorityQueueItem(**data)
            claimed.append(item)
        return claimed

    async def acknowledge(
        self,
        queue_id: str,
        worker_id: str | None = None,
    ) -> AlertPriorityQueueItem | None:
        item = await self._get_item(queue_id)
        if item is None:
            return None
        status = AlertPriorityQueueItemStatus(item.status)
        if status not in (AlertPriorityQueueItemStatus.CLAIMED, AlertPriorityQueueItemStatus.PROCESSING):
            return None
        if worker_id is not None and item.claimed_by is not None and item.claimed_by != worker_id:
            return None
        item.status = AlertPriorityQueueItemStatus.COMPLETED.value
        item.claimed_by = None
        item.claimed_at = None
        item.lease_expires_at = None
        await self._save_item(item)
        self._client.zrem(self._keys["claimed"], queue_id)
        self._client.zadd(self._keys["completed"], {queue_id: item.priority})
        return item

    async def fail(
        self,
        queue_id: str,
        error: str | None = None,
        worker_id: str | None = None,
    ) -> AlertPriorityQueueItem | None:
        item = await self._get_item(queue_id)
        if item is None:
            return None
        status = AlertPriorityQueueItemStatus(item.status)
        if status not in (AlertPriorityQueueItemStatus.CLAIMED, AlertPriorityQueueItemStatus.PROCESSING):
            return None
        if worker_id is not None and item.claimed_by is not None and item.claimed_by != worker_id:
            return None
        item.status = AlertPriorityQueueItemStatus.FAILED.value
        metadata = json.loads(item.metadata_json) if item.metadata_json else {}
        if error:
            metadata["last_error"] = _redact_error(error)
        item.metadata_json = json.dumps(metadata)
        item.claimed_by = None
        item.claimed_at = None
        item.lease_expires_at = None
        await self._save_item(item)
        self._client.zrem(self._keys["claimed"], queue_id)
        self._client.zadd(self._keys["failed"], {queue_id: item.priority})
        return item

    async def requeue(
        self,
        queue_id: str,
        available_at: datetime | None = None,
        priority: int | None = None,
        reason: str | None = None,
        worker_id: str | None = None,
    ) -> AlertPriorityQueueItem | None:
        item = await self._get_item(queue_id)
        if item is None:
            return None
        status = AlertPriorityQueueItemStatus(item.status)
        if status not in (
            AlertPriorityQueueItemStatus.CLAIMED,
            AlertPriorityQueueItemStatus.FAILED,
            AlertPriorityQueueItemStatus.EXPIRED,
        ):
            return None
        item.status = AlertPriorityQueueItemStatus.REQUEUED.value
        if available_at is not None:
            item.available_at = available_at
        else:
            item.available_at = _now()
        if priority is not None:
            item.priority = priority
        item.claimed_by = None
        item.claimed_at = None
        item.lease_expires_at = None
        metadata = json.loads(item.metadata_json) if item.metadata_json else {}
        if reason:
            metadata["last_requeue_reason"] = _redact_error(reason)
        metadata.setdefault("requeue_count", 0)
        metadata["requeue_count"] = metadata.get("requeue_count", 0) + 1
        item.metadata_json = json.dumps(metadata)
        item.attempt += 1
        await self._save_item(item)
        self._client.zrem(self._keys["claimed"], queue_id)
        self._client.zrem(self._keys["failed"], queue_id)
        score = item.available_at.timestamp()
        self._client.zadd(self._keys["requeued"], {queue_id: score})
        # Also add back to queued if available_at <= now
        if item.available_at <= _now():
            self._client.zadd(self._keys["queued"], {queue_id: score})
        return item

    async def reset_expired_leases(
        self,
        now: datetime | None = None,
        limit: int = 100,
    ) -> int:
        """Reset expired leases back to QUEUED status."""
        if now is None:
            now = _now()
        now_ts = now.timestamp()

        # Get expired claimed items (score = claimed_at timestamp)
        expired_ids = self._client.zrangebyscore(
            self._keys["claimed"], 0, now_ts, start=0, num=limit
        )
        reset_count = 0
        for attempt_id in expired_ids:
            item = await self._get_item(attempt_id)
            if item is None:
                continue
            item.status = AlertPriorityQueueItemStatus.QUEUED.value
            item.claimed_by = None
            item.claimed_at = None
            item.lease_expires_at = None
            metadata = json.loads(item.metadata_json) if item.metadata_json else {}
            metadata.setdefault("lease_expired_count", 0)
            metadata["lease_expired_count"] = metadata.get("lease_expired_count", 0) + 1
            item.metadata_json = json.dumps(metadata)
            await self._save_item(item)
            self._client.zrem(self._keys["claimed"], attempt_id)
            score = item.available_at.timestamp()
            self._client.zadd(self._keys["queued"], {attempt_id: score})
            reset_count += 1
        return reset_count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_item(self, attempt_id: str) -> AlertPriorityQueueItem | None:
        raw = self._client.hget(self._keys["items"], attempt_id)
        if raw is None:
            return None
        data = json.loads(raw)
        data["channel_type"] = AlertDeliveryChannelType(data["channel_type"])
        if data.get("created_at"):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if data.get("available_at"):
            data["available_at"] = datetime.fromisoformat(data["available_at"])
        else:
            data["available_at"] = data.get("created_at", _now())
        if data.get("claimed_at"):
            data["claimed_at"] = datetime.fromisoformat(data["claimed_at"])
        if data.get("lease_expires_at"):
            data["lease_expires_at"] = datetime.fromisoformat(data["lease_expires_at"])
        if data.get("next_retry_at"):
            data["next_retry_at"] = datetime.fromisoformat(data["next_retry_at"])
        return AlertPriorityQueueItem(**data)

    async def _save_item(self, item: AlertPriorityQueueItem) -> None:
        data = item.model_dump(mode="json")
        self._client.hset(self._keys["items"], item.attempt_id, json.dumps(data))

    def close(self) -> None:
        self._client.close()


def create_redis_alert_priority_queue_store(
    redis_url: str = "redis://localhost:6379/0",
    key_prefix: str = "agentapp:notification:pq",
    queue_name: str = "default",
    queue_id: str = "default",
) -> RedisAlertPriorityQueueStore:
    """Factory for Redis alert priority queue store."""
    return RedisAlertPriorityQueueStore(
        redis_url=redis_url,
        key_prefix=key_prefix,
        queue_name=queue_name,
        queue_id=queue_id,
    )
