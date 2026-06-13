"""PolicyReloadManager — orchestrates manual policy reload requests.

Phase 34 Task 3: Models, hook protocol, and manager that:
1. Records MANUAL_RELOAD_REQUESTED events in the event store
2. Refreshes the ActivePolicyResolver cache
3. Invokes registered reload hooks
4. Collects all results, capturing hook failures gracefully
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Protocol

from pydantic import BaseModel, Field


class PolicyReloadTarget(BaseModel):
    """Target environment/ring for a policy reload."""

    environment: str | None = Field(
        default=None, description="Environment to reload (None = all)"
    )
    ring_name: str | None = Field(
        default=None, description="Ring to reload (None = all rings in env)"
    )


class PolicyReloadResult(BaseModel):
    """Outcome of a single reload step (resolver refresh or hook invocation)."""

    target: PolicyReloadTarget = Field(
        ..., description="Reload target that was processed"
    )
    refreshed: bool = Field(
        ..., description="Whether the reload succeeded"
    )
    event_id: str | None = Field(
        default=None, description="Event ID if an event was recorded"
    )
    error: str | None = Field(
        default=None, description="Error message if the reload failed"
    )
    refreshed_at: datetime = Field(
        ..., description="Timezone-aware timestamp of the reload attempt"
    )


class PolicyReloadHook(Protocol):
    """Protocol for reload hooks — plug-in extensions invoked during reload."""

    async def reload_policy(self, target: PolicyReloadTarget) -> PolicyReloadResult: ...


class PolicyReloadManager:
    """Orchestrates manual policy reload requests.

    Steps on ``request_reload``:
    1. Append ``MANUAL_RELOAD_REQUESTED`` event to event_store (if configured).
    2. Refresh the resolver cache for the target environment.
    3. Invoke all registered hooks.
    4. Collect all results; hook failures are captured in the ``error`` field.

    Args:
        resolver: ActivePolicyResolver whose cache will be refreshed.
        event_store: Optional PolicyChangeEventStore for recording reload events.
    """

    def __init__(
        self,
        resolver: object,
        event_store: object | None = None,
    ) -> None:
        self._resolver = resolver
        self._event_store = event_store
        self._hooks: dict[str, PolicyReloadHook] = {}

    def register_hook(self, name: str, hook: PolicyReloadHook) -> None:
        """Register a named reload hook."""
        self._hooks[name] = hook

    async def request_reload(
        self,
        environment: str | None = None,
        ring_name: str | None = None,
        requested_by: str | None = None,
        reason: str | None = None,
    ) -> list[PolicyReloadResult]:
        """Request a policy reload for the target environment/ring.

        1. Append MANUAL_RELOAD_REQUESTED event to event_store (if configured)
        2. Call refresh_resolver for the target
        3. Call all registered hooks
        4. Collect all results; hook failures captured in error field
        5. Return all results
        """
        target = PolicyReloadTarget(environment=environment, ring_name=ring_name)

        # Step 1: Append event
        event_id: str | None = None
        if self._event_store is not None:
            from agent_app.governance.policy_change_event import (
                PolicyChangeEvent,
                PolicyChangeEventType,
            )

            event_id = f"pce_{uuid.uuid4().hex[:12]}"
            event = PolicyChangeEvent(
                event_id=event_id,
                event_type=PolicyChangeEventType.MANUAL_RELOAD_REQUESTED,
                environment=environment,
                ring_name=ring_name,
                actor_id=requested_by,
                reason=reason,
                created_at=datetime.now(timezone.utc),
            )
            try:
                await self._event_store.append(event)
            except Exception:
                event_id = None  # Event store failure shouldn't crash reload

        # Step 2: Refresh resolver
        results: list[PolicyReloadResult] = []
        resolver_result = await self.refresh_resolver(environment, ring_name)
        if event_id is not None:
            resolver_result.event_id = event_id
        results.append(resolver_result)

        # Step 3: Call hooks
        for name, hook in self._hooks.items():
            try:
                hook_result = await hook.reload_policy(target)
                results.append(hook_result)
            except Exception as e:
                error_result = PolicyReloadResult(
                    target=target,
                    refreshed=False,
                    error=f"Hook '{name}' failed: {e}",
                    refreshed_at=datetime.now(timezone.utc),
                )
                results.append(error_result)

        return results

    async def refresh_resolver(
        self,
        environment: str | None = None,
        ring_name: str | None = None,
    ) -> PolicyReloadResult:
        """Refresh the resolver cache for the target.

        If *environment* is provided, call ``resolver.refresh(environment)``.
        Otherwise, call ``resolver.clear_cache()`` to clear all cached entries.
        """
        try:
            if environment is not None:
                self._resolver.refresh(environment)
            else:
                self._resolver.clear_cache()
            return PolicyReloadResult(
                target=PolicyReloadTarget(environment=environment, ring_name=ring_name),
                refreshed=True,
                refreshed_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            return PolicyReloadResult(
                target=PolicyReloadTarget(environment=environment, ring_name=ring_name),
                refreshed=False,
                error=str(e),
                refreshed_at=datetime.now(timezone.utc),
            )
