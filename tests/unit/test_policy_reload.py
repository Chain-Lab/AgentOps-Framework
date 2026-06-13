"""Tests for PolicyReloadManager — Phase 34 Task 3."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_change_event import PolicyChangeEventType
from agent_app.runtime.policy_change_event_store import InMemoryPolicyChangeEventStore
from agent_app.runtime.policy_reload import (
    PolicyReloadHook,
    PolicyReloadManager,
    PolicyReloadResult,
    PolicyReloadTarget,
)


# -- Stubs -------------------------------------------------------------------

class StubResolver:
    """Minimal stub for ActivePolicyResolver."""

    def __init__(self) -> None:
        self.refreshed_env: str | None = None
        self.cache_cleared = False

    def refresh(self, environment: str | None = None) -> None:
        self.refreshed_env = environment

    def clear_cache(self) -> None:
        self.cache_cleared = True


class FailingResolver:
    """Resolver that raises on refresh / clear_cache."""

    def refresh(self, environment: str | None = None) -> None:
        raise RuntimeError("resolver refresh failed")

    def clear_cache(self) -> None:
        raise RuntimeError("resolver clear_cache failed")


class StubHook:
    """Simple reload hook for testing."""

    def __init__(self, result: PolicyReloadResult | None = None) -> None:
        self.called = False
        self.call_count = 0
        self.last_target: PolicyReloadTarget | None = None
        self._result = result

    async def reload_policy(self, target: PolicyReloadTarget) -> PolicyReloadResult:
        self.called = True
        self.call_count += 1
        self.last_target = target
        if self._result is not None:
            return self._result
        return PolicyReloadResult(
            target=target,
            refreshed=True,
            refreshed_at=datetime.now(timezone.utc),
        )


class FailingHook:
    """Hook that always raises."""

    async def reload_policy(self, target: PolicyReloadTarget) -> PolicyReloadResult:
        raise RuntimeError("hook failed")


# -- Tests -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_reload_appends_event() -> None:
    """request_reload with event_store creates MANUAL_RELOAD_REQUESTED event."""
    resolver = StubResolver()
    store = InMemoryPolicyChangeEventStore()
    manager = PolicyReloadManager(resolver=resolver, event_store=store)

    results = await manager.request_reload(
        environment="staging",
        ring_name="canary",
        requested_by="admin",
        reason="manual refresh",
    )

    # Resolver result should have an event_id
    resolver_result = results[0]
    assert resolver_result.refreshed is True
    assert resolver_result.event_id is not None
    assert resolver_result.event_id.startswith("pce_")

    # Event should be stored with correct type
    events = await store.list(environment="staging")
    assert len(events) == 1
    event = events[0]
    assert event.event_type == PolicyChangeEventType.MANUAL_RELOAD_REQUESTED
    assert event.environment == "staging"
    assert event.ring_name == "canary"
    assert event.actor_id == "admin"
    assert event.reason == "manual refresh"


@pytest.mark.asyncio
async def test_refresh_resolver_clears_cache() -> None:
    """refresh_resolver calls resolver.refresh() with environment."""
    resolver = StubResolver()
    manager = PolicyReloadManager(resolver=resolver)

    # With environment
    result = await manager.refresh_resolver(environment="prod")
    assert result.refreshed is True
    assert result.target.environment == "prod"
    assert resolver.refreshed_env == "prod"
    assert not resolver.cache_cleared  # should NOT call clear_cache

    # Without environment — should call clear_cache
    resolver2 = StubResolver()
    manager2 = PolicyReloadManager(resolver=resolver2)
    result2 = await manager2.refresh_resolver()
    assert result2.refreshed is True
    assert result2.target.environment is None
    assert resolver2.cache_cleared is True


@pytest.mark.asyncio
async def test_hook_called() -> None:
    """Registered hook is called during request_reload."""
    resolver = StubResolver()
    manager = PolicyReloadManager(resolver=resolver)
    hook = StubHook()
    manager.register_hook("test_hook", hook)

    results = await manager.request_reload(environment="prod")

    assert hook.called is True
    assert hook.call_count == 1
    assert hook.last_target is not None
    assert hook.last_target.environment == "prod"
    # Should have resolver result + hook result
    assert len(results) == 2


@pytest.mark.asyncio
async def test_hook_failure_captured() -> None:
    """Hook that raises doesn't crash; error captured in result."""
    resolver = StubResolver()
    manager = PolicyReloadManager(resolver=resolver)
    manager.register_hook("bad_hook", FailingHook())

    results = await manager.request_reload(environment="prod")

    # Should not crash — first result is resolver, second is hook error
    assert len(results) == 2
    resolver_result = results[0]
    assert resolver_result.refreshed is True

    hook_result = results[1]
    assert hook_result.refreshed is False
    assert hook_result.error is not None
    assert "bad_hook" in hook_result.error
    assert "hook failed" in hook_result.error


@pytest.mark.asyncio
async def test_multiple_hooks_all_invoked() -> None:
    """Multiple hooks all called; results collected."""
    resolver = StubResolver()
    manager = PolicyReloadManager(resolver=resolver)

    hook_a = StubHook()
    hook_b = StubHook()
    manager.register_hook("hook_a", hook_a)
    manager.register_hook("hook_b", hook_b)

    results = await manager.request_reload(environment="staging")

    assert hook_a.called is True
    assert hook_b.called is True
    # resolver + 2 hooks = 3 results
    assert len(results) == 3


@pytest.mark.asyncio
async def test_no_event_store_still_works() -> None:
    """request_reload works without event_store."""
    resolver = StubResolver()
    manager = PolicyReloadManager(resolver=resolver, event_store=None)

    results = await manager.request_reload(environment="prod")

    assert len(results) >= 1
    resolver_result = results[0]
    assert resolver_result.refreshed is True
    # No event_id when no store configured
    assert resolver_result.event_id is None
