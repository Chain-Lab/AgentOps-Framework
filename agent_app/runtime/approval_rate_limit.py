"""Approval rate limiting — lightweight in-memory protection."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from agent_app.governance.audit import AuditEvent, AuditLogger


@dataclass
class RateLimitConfig:
    """Configuration for approval rate limiting."""

    max_requests: int = 10
    window_seconds: int = 60


class ApprovalRateLimiter:
    """Protocol for approval rate limiters."""

    async def check_allowed(
        self,
        tenant_id: str | None,
        user_id: str | None,
        tool_name: str,
    ) -> bool:
        """Return True if an approval request is allowed."""
        raise NotImplementedError


class InMemoryApprovalRateLimiter(ApprovalRateLimiter):
    """In-memory sliding-window rate limiter for approval creation.

    Tracks requests per (tenant, user, tool) key.  When a key exceeds
    ``max_requests`` within ``window_seconds``, further requests are
    blocked until the window slides.
    """

    def __init__(
        self,
        max_requests: int = 10,
        window_seconds: int = 60,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._audit_logger = audit_logger
        # key -> list of timestamps (monotonic)
        self._hits: dict[str, list[float]] = {}

    def _key(self, tenant_id: str | None, user_id: str | None, tool_name: str) -> str:
        parts = [str(tenant_id or "_anon"), str(user_id or "_anon"), tool_name]
        return "|".join(parts)

    def _purge_expired(self, key: str, now: float) -> None:
        cutoff = now - self._window_seconds
        self._hits[key] = [t for t in self._hits.get(key, []) if t > cutoff]

    async def check_allowed(
        self,
        tenant_id: str | None,
        user_id: str | None,
        tool_name: str,
    ) -> bool:
        key = self._key(tenant_id, user_id, tool_name)
        now = time.monotonic()
        self._purge_expired(key, now)
        hits = self._hits.get(key, [])
        if len(hits) >= self._max_requests:
            await self._log_rate_limited(tenant_id, user_id, tool_name)
            return False
        hits.append(now)
        self._hits[key] = hits
        return True

    async def _log_rate_limited(
        self,
        tenant_id: str | None,
        user_id: str | None,
        tool_name: str,
    ) -> None:
        if self._audit_logger is None:
            return
        try:
            await self._audit_logger.log(AuditEvent(
                event_id=_make_event_id(),
                run_id=None,
                event_type="approval.rate_limited",
                user_id=user_id,
                tenant_id=tenant_id,
                tool_name=tool_name,
                data={
                    "max_requests": self._max_requests,
                    "window_seconds": self._window_seconds,
                },
            ))
        except Exception:
            pass


def _make_event_id() -> str:
    import uuid
    return str(uuid.uuid4())
