"""Tests for ActivePolicyResolver ring-aware resolution (Phase 33)."""
import pytest
from agent_app.runtime.policy_resolver import ActivePolicyResolver
from agent_app.runtime.policy_ring_assignment_store import InMemoryRingActivationAssignmentStore
from agent_app.runtime.policy_ring_store import InMemoryReleaseRingStore
from agent_app.runtime.policy_activation_store import InMemoryPolicyActivationStore
from agent_app.governance.policy_activation import PolicyActivation
from agent_app.governance.policy_ring import ReleaseRing
from agent_app.governance.policy_ring_assignment import RingActivationAssignment


class _MockBundle:
    def __init__(self, bid, chash):
        self.bundle_id = bid
        self.config_hash = chash


class _MockBundleStore:
    def __init__(self, bundles):
        self._b = {b.bundle_id: b for b in bundles}

    async def get(self, bid):
        return self._b.get(bid)


@pytest.fixture
def ring_resolver():
    bundle = _MockBundle("pb_1", "h1")
    activation_store = InMemoryPolicyActivationStore()
    assignment_store = InMemoryRingActivationAssignmentStore()
    ring_store = InMemoryReleaseRingStore()
    return (
        ActivePolicyResolver(
            bundle_store=_MockBundleStore([bundle]),
            activation_store=activation_store,
            ring_assignment_store=assignment_store,
            ring_store=ring_store,
        ),
        activation_store,
        assignment_store,
        ring_store,
    )


class TestResolverRingResolution:
    @pytest.mark.asyncio
    async def test_resolves_for_ring(self, ring_resolver):
        resolver, act_store, assign_store, ring_store = ring_resolver
        a1 = PolicyActivation(
            activation_id="pa_001",
            environment="prod",
            bundle_id="pb_1",
            config_hash="h1",
            activated_by="admin",
        )
        await act_store.activate(a1)
        await ring_store.create(
            ReleaseRing(
                ring_id="ring_001",
                environment="prod",
                name="stable",
                is_default=True,
            )
        )
        await assign_store.assign(
            RingActivationAssignment(
                assignment_id="ra_001",
                environment="prod",
                ring_name="stable",
                activation_id="pa_001",
                bundle_id="pb_1",
                config_hash="h1",
                assigned_by="admin",
            )
        )
        result = await resolver.resolve_active_bundle_for_ring("prod", "stable")
        assert result is not None
        assert result.bundle_id == "pb_1"

    @pytest.mark.asyncio
    async def test_no_assignment_returns_none(self, ring_resolver):
        resolver, _, _, _ = ring_resolver
        result = await resolver.resolve_active_bundle_for_ring("prod", "stable")
        assert result is None

    @pytest.mark.asyncio
    async def test_require_raises_when_no_assignment(self, ring_resolver):
        resolver, _, _, _ = ring_resolver
        with pytest.raises(KeyError, match="No active policy bundle"):
            await resolver.require_active_bundle_for_ring("prod", "stable")

    @pytest.mark.asyncio
    async def test_disabled_ring_blocks(self, ring_resolver):
        resolver, act_store, assign_store, ring_store = ring_resolver
        a1 = PolicyActivation(
            activation_id="pa_001",
            environment="prod",
            bundle_id="pb_1",
            config_hash="h1",
            activated_by="admin",
        )
        await act_store.activate(a1)
        await ring_store.create(
            ReleaseRing(ring_id="ring_001", environment="prod", name="canary")
        )
        await ring_store.disable("prod", "canary")
        result = await resolver.resolve_active_bundle_for_ring("prod", "canary")
        assert result is None

    @pytest.mark.asyncio
    async def test_require_disabled_ring_raises(self, ring_resolver):
        resolver, _, _, ring_store = ring_resolver
        await ring_store.create(
            ReleaseRing(ring_id="ring_001", environment="prod", name="canary")
        )
        await ring_store.disable("prod", "canary")
        with pytest.raises(RuntimeError, match="disabled"):
            await resolver.require_active_bundle_for_ring("prod", "canary")

    @pytest.mark.asyncio
    async def test_hash_mismatch_raises(self, ring_resolver):
        resolver, act_store, assign_store, ring_store = ring_resolver
        a1 = PolicyActivation(
            activation_id="pa_001",
            environment="prod",
            bundle_id="pb_1",
            config_hash="h1",
            activated_by="admin",
        )
        await act_store.activate(a1)
        await ring_store.create(
            ReleaseRing(ring_id="ring_001", environment="prod", name="canary")
        )
        await assign_store.assign(
            RingActivationAssignment(
                assignment_id="ra_001",
                environment="prod",
                ring_name="canary",
                activation_id="pa_001",
                bundle_id="pb_1",
                config_hash="WRONG",
                assigned_by="admin",
            )
        )
        with pytest.raises(ValueError, match="mismatch"):
            await resolver.resolve_active_bundle_for_ring("prod", "canary")

    @pytest.mark.asyncio
    async def test_backward_compat_without_ring_stores(self):
        """Resolver without ring stores still works with basic resolve."""
        bundle = _MockBundle("pb_1", "h1")
        resolver = ActivePolicyResolver(
            bundle_store=_MockBundleStore([bundle]),
            activation_store=InMemoryPolicyActivationStore(),
        )
        result = await resolver.resolve_active_bundle("prod")
        assert result is None  # no activation, returns None
