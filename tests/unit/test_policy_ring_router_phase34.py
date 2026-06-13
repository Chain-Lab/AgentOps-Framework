"""Tests for PolicyRingRouter Phase 34 — deterministic canary percentage routing."""
import hashlib

import pytest

from agent_app.core.context import RunContext
from agent_app.runtime.policy_ring_router import PolicyRingRouter, RingRoutingConfig
from agent_app.runtime.policy_ring_store import InMemoryReleaseRingStore
from agent_app.governance.policy_ring import ReleaseRing


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(**overrides) -> RunContext:
    """Create a RunContext with sensible defaults, allowing overrides."""
    defaults = dict(run_id="r1", user_id="user_123", tenant_id="tenant_1")
    defaults.update(overrides)
    return RunContext(**defaults)


def _compute_bucket(environment: str, key_value: str) -> int:
    """Compute the deterministic bucket for a given environment:key_value pair."""
    hash_input = f"{environment}:{key_value}"
    hash_bytes = hashlib.sha256(hash_input.encode("utf-8")).digest()
    return int.from_bytes(hash_bytes[:8], byteorder="big") % 100


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def store_with_rings():
    """In-memory store with stable (default, enabled) and canary (enabled) rings."""
    store = InMemoryReleaseRingStore()
    await store.create(ReleaseRing(ring_id="ring_stable", environment="prod", name="stable", is_default=True))
    await store.create(ReleaseRing(ring_id="ring_canary", environment="prod", name="canary"))
    return store


@pytest.fixture
async def router_50pct(store_with_rings):
    """Router with 50% canary routing enabled."""
    config = RingRoutingConfig(enabled=True, canary_percentage=50, canary_ring="canary", stable_ring="stable")
    return PolicyRingRouter(ring_store=store_with_rings, default_ring="stable", routing_config=config)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDeterministicRouting:
    """Tests for deterministic canary percentage routing."""

    @pytest.mark.asyncio
    async def test_explicit_policy_ring_wins(self, router_50pct):
        """Even with routing enabled, context.policy_ring overrides everything."""
        ctx = _make_context(policy_ring="canary")
        result = await router_50pct.resolve_ring("prod", ctx)
        assert result == "canary"

        # Also test with stable override
        ctx2 = _make_context(policy_ring="stable")
        result2 = await router_50pct.resolve_ring("prod", ctx2)
        assert result2 == "stable"

    @pytest.mark.asyncio
    async def test_canary_0_routes_stable(self, store_with_rings):
        """canary_percentage=0 routes everyone to stable."""
        config = RingRoutingConfig(enabled=True, canary_percentage=0, canary_ring="canary", stable_ring="stable")
        router = PolicyRingRouter(ring_store=store_with_rings, default_ring="stable", routing_config=config)

        # Try multiple users — all should go to stable
        for uid in ["user_a", "user_b", "user_c", "user_d"]:
            ctx = _make_context(user_id=uid)
            result = await router.resolve_ring("prod", ctx)
            assert result == "stable", f"user_id={uid} should route to stable"

    @pytest.mark.asyncio
    async def test_canary_100_routes_canary(self, store_with_rings):
        """canary_percentage=100 routes everyone to canary."""
        config = RingRoutingConfig(enabled=True, canary_percentage=100, canary_ring="canary", stable_ring="stable")
        router = PolicyRingRouter(ring_store=store_with_rings, default_ring="stable", routing_config=config)

        # Try multiple users — all should go to canary
        for uid in ["user_a", "user_b", "user_c", "user_d"]:
            ctx = _make_context(user_id=uid)
            result = await router.resolve_ring("prod", ctx)
            assert result == "canary", f"user_id={uid} should route to canary"

    @pytest.mark.asyncio
    async def test_same_actor_routes_consistently(self, router_50pct):
        """Same user_id always gets the same ring across multiple calls."""
        ctx = _make_context(user_id="user_123")
        results = [await router_50pct.resolve_ring("prod", ctx) for _ in range(10)]
        assert len(set(results)) == 1, "Same actor should always route to the same ring"

        # Verify the expected ring by computing bucket manually
        bucket = _compute_bucket("prod", "user_123")
        expected = "canary" if bucket < 50 else "stable"
        assert results[0] == expected

    @pytest.mark.asyncio
    async def test_different_actors_distribute_deterministically(self, store_with_rings):
        """Multiple actors distribute between canary/stable at 50%."""
        config = RingRoutingConfig(enabled=True, canary_percentage=50, canary_ring="canary", stable_ring="stable")
        router = PolicyRingRouter(ring_store=store_with_rings, default_ring="stable", routing_config=config)

        canary_count = 0
        stable_count = 0
        for i in range(100):
            uid = f"user_{i:03d}"
            ctx = _make_context(user_id=uid)
            result = await router.resolve_ring("prod", ctx)
            if result == "canary":
                canary_count += 1
            else:
                stable_count += 1

        # With 100 users and 50% canary, we expect roughly 50/50
        # Allow wide margin (20-80) since hash distribution isn't perfectly uniform
        assert 20 <= canary_count <= 80, f"canary_count={canary_count} outside expected range"
        assert 20 <= stable_count <= 80, f"stable_count={stable_count} outside expected range"

    @pytest.mark.asyncio
    async def test_missing_hash_key_routes_stable(self, store_with_rings):
        """No user_id (empty string) routes to stable."""
        config = RingRoutingConfig(enabled=True, canary_percentage=50, canary_ring="canary", stable_ring="stable")
        router = PolicyRingRouter(ring_store=store_with_rings, default_ring="stable", routing_config=config)

        # user_id is required by RunContext, but we can pass empty string
        ctx = _make_context(user_id="")
        result = await router.resolve_ring("prod", ctx)
        assert result == "stable"

    @pytest.mark.asyncio
    async def test_disabled_selected_ring_raises(self, store_with_rings):
        """When routing selects a canary ring that's disabled, raises RuntimeError."""
        config = RingRoutingConfig(enabled=True, canary_percentage=100, canary_ring="canary", stable_ring="stable")
        router = PolicyRingRouter(ring_store=store_with_rings, default_ring="stable", routing_config=config)

        # Disable the canary ring
        await store_with_rings.disable("prod", "canary")

        ctx = _make_context(user_id="user_123")
        with pytest.raises(RuntimeError, match="disabled"):
            await router.resolve_ring("prod", ctx)

    @pytest.mark.asyncio
    async def test_missing_selected_ring_raises(self):
        """When routing selects a ring that doesn't exist, raises KeyError."""
        store = InMemoryReleaseRingStore()
        # Only create stable ring — canary does not exist
        await store.create(ReleaseRing(ring_id="ring_stable", environment="prod", name="stable", is_default=True))

        config = RingRoutingConfig(enabled=True, canary_percentage=100, canary_ring="canary", stable_ring="stable")
        router = PolicyRingRouter(ring_store=store, default_ring="stable", routing_config=config)

        ctx = _make_context(user_id="user_123")
        with pytest.raises(KeyError, match="does not exist"):
            await router.resolve_ring("prod", ctx)

    def test_invalid_percentage_rejected(self):
        """RingRoutingConfig with canary_percentage > 100 raises validation error."""
        with pytest.raises(Exception):
            RingRoutingConfig(enabled=True, canary_percentage=101)

    def test_negative_percentage_rejected(self):
        """RingRoutingConfig with canary_percentage < 0 raises validation error."""
        with pytest.raises(Exception):
            RingRoutingConfig(enabled=True, canary_percentage=-1)


class TestSimulateRouting:
    """Tests for simulate_routing method."""

    @pytest.mark.asyncio
    async def test_simulate_routing_explicit(self, router_50pct):
        """simulate_routing returns correct dict for explicit override."""
        ctx = _make_context(policy_ring="canary")
        result = await router_50pct.simulate_routing("prod", ctx)

        assert result["environment"] == "prod"
        assert result["selected_ring"] == "canary"
        assert result["routing_mode"] == "explicit"
        assert result["reason"] == "Explicit policy_ring override"

    @pytest.mark.asyncio
    async def test_simulate_routing_deterministic(self, router_50pct):
        """simulate_routing returns correct dict for deterministic routing."""
        ctx = _make_context(user_id="user_123")
        result = await router_50pct.simulate_routing("prod", ctx)

        assert result["environment"] == "prod"
        assert result["routing_mode"] == "deterministic"
        assert result["canary_percentage"] == 50
        assert result["hash_key"] == "actor_id"
        assert result["bucket"] is not None

        # Verify bucket matches manual computation
        expected_bucket = _compute_bucket("prod", "user_123")
        assert result["bucket"] == expected_bucket

        # Verify selected ring matches bucket logic
        if expected_bucket < 50:
            assert result["selected_ring"] == "canary"
        else:
            assert result["selected_ring"] == "stable"

    @pytest.mark.asyncio
    async def test_simulate_routing_default(self):
        """simulate_routing returns correct dict when no routing config."""
        router = PolicyRingRouter(default_ring="stable")
        ctx = _make_context()
        result = await router.simulate_routing("prod", ctx)

        assert result["routing_mode"] == "default"
        assert result["selected_ring"] == "stable"
        assert result["reason"] == "Default ring (no deterministic routing)"

    @pytest.mark.asyncio
    async def test_simulate_routing_no_key_value(self, store_with_rings):
        """simulate_routing handles missing hash key value."""
        config = RingRoutingConfig(enabled=True, canary_percentage=50, canary_ring="canary", stable_ring="stable")
        router = PolicyRingRouter(ring_store=store_with_rings, default_ring="stable", routing_config=config)

        ctx = _make_context(user_id="")
        result = await router.simulate_routing("prod", ctx)

        assert result["routing_mode"] == "deterministic"
        assert result["selected_ring"] == "stable"
        assert "No actor_id value" in result["reason"]
