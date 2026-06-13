"""Tests for PolicyRingRouter (Phase 33)."""
import pytest
from agent_app.core.context import RunContext
from agent_app.runtime.policy_ring_router import PolicyRingRouter
from agent_app.runtime.policy_ring_store import InMemoryReleaseRingStore
from agent_app.governance.policy_ring import ReleaseRing


@pytest.fixture
def router_with_store():
    store = InMemoryReleaseRingStore()
    return PolicyRingRouter(ring_store=store, default_ring="stable"), store


class TestPolicyRingRouter:
    @pytest.mark.asyncio
    async def test_explicit_ring_wins(self, router_with_store):
        router, store = router_with_store
        await store.create(ReleaseRing(ring_id="ring_001", environment="prod", name="stable", is_default=True))
        await store.create(ReleaseRing(ring_id="ring_002", environment="prod", name="canary"))
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", policy_ring="canary")
        result = await router.resolve_ring("prod", ctx)
        assert result == "canary"

    @pytest.mark.asyncio
    async def test_default_ring_used_when_no_context_ring(self, router_with_store):
        router, store = router_with_store
        await store.create(ReleaseRing(ring_id="ring_001", environment="prod", name="stable", is_default=True))
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")
        result = await router.resolve_ring("prod", ctx)
        assert result == "stable"

    @pytest.mark.asyncio
    async def test_disabled_ring_raises(self, router_with_store):
        router, store = router_with_store
        await store.create(ReleaseRing(ring_id="ring_001", environment="prod", name="canary"))
        await store.disable("prod", "canary")
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", policy_ring="canary")
        with pytest.raises(RuntimeError, match="disabled"):
            await router.resolve_ring("prod", ctx)

    @pytest.mark.asyncio
    async def test_missing_ring_raises(self, router_with_store):
        router, store = router_with_store
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", policy_ring="nonexistent")
        with pytest.raises(KeyError, match="does not exist"):
            await router.resolve_ring("prod", ctx)

    @pytest.mark.asyncio
    async def test_no_ring_store_uses_default(self):
        router = PolicyRingRouter(default_ring="stable")
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")
        result = await router.resolve_ring("prod", ctx)
        assert result == "stable"

    @pytest.mark.asyncio
    async def test_fallback_to_configured_default(self, router_with_store):
        router, store = router_with_store
        # No default ring found in store, no context ring -> uses default_ring="stable"
        # but "stable" must exist in the store for validation to pass
        await store.create(ReleaseRing(ring_id="ring_001", environment="prod", name="stable"))
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")
        result = await router.resolve_ring("prod", ctx)
        assert result == "stable"
