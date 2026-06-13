"""Tests for PolicyReleaseService Phase 33 Task 6: ring management APIs."""
import pytest
from agent_app.governance.policy_activation import PolicyActivation, PolicyActivationStatus
from agent_app.governance.policy_ring import ReleaseRing, ReleaseRingStatus
from agent_app.governance.policy_ring_assignment import RingActivationAssignment, RingActivationAssignmentStatus
from agent_app.runtime.policy_ring_store import InMemoryReleaseRingStore
from agent_app.runtime.policy_ring_assignment_store import InMemoryRingActivationAssignmentStore
from agent_app.runtime.policy_release import PolicyReleaseService, PolicyReleasePermissionError
from agent_app.core.context import RunContext


def _make_service():
    """Build a PolicyReleaseService with in-memory ring stores for testing."""
    bundle_store = _StubBundleStore()
    activation_store = InMemoryPolicyActivationStore()
    ring_store = InMemoryReleaseRingStore()
    ring_assignment_store = InMemoryRingActivationAssignmentStore()
    service = PolicyReleaseService(
        bundle_store=bundle_store,
        replay_runner=_StubReplayRunner(),
        replay_store=None,
        gate_evaluator=_StubGateEvaluator(),
        gate_store=_StubGateStore(),
        activation_store=activation_store,
        ring_store=ring_store,
        ring_assignment_store=ring_assignment_store,
    )
    return service, bundle_store, activation_store, ring_store, ring_assignment_store


# -- Stubs --


class _StubBundleStore:
    def __init__(self):
        self._bundles = {}

    def add(self, b):
        self._bundles[b.bundle_id] = b

    async def get(self, bid):
        return self._bundles.get(bid)

    async def activate(self, bid):
        b = self._bundles.get(bid)
        if b:
            b.status = "active"
        return b


class _StubBundle:
    def __init__(self, bid, chash):
        self.bundle_id = bid
        self.config_hash = chash
        self.status = "draft"


class _StubReplayRunner:
    async def run_replay(self, **kw):
        return type("R", (), {"decisions": [], "total": 0})()


class _StubGateEvaluator:
    async def evaluate(self, **kw):
        return type("R", (), {"passed": True, "status": "passed", "gate_result_id": "gr_1"})()


class _StubGateStore:
    async def save(self, result):
        return result

    async def list(self, **kw):
        return []

    async def get(self, gid):
        return None


class InMemoryPolicyActivationStore:
    """Minimal in-memory activation store for these tests."""

    def __init__(self):
        self._activations: dict[str, PolicyActivation] = {}

    async def activate(self, activation: PolicyActivation) -> PolicyActivation:
        # Supersede previous active for same environment
        for a in self._activations.values():
            if a.environment == activation.environment and a.status == PolicyActivationStatus.ACTIVE:
                a.status = PolicyActivationStatus.SUPERSEDED
        self._activations[activation.activation_id] = activation
        return activation

    async def get(self, activation_id: str) -> PolicyActivation | None:
        return self._activations.get(activation_id)

    async def list(self, environment=None):
        results = list(self._activations.values())
        if environment is not None:
            results = [a for a in results if a.environment == environment]
        return results

    async def get_previous_activation(self, environment: str):
        env_activations = [
            a for a in self._activations.values()
            if a.environment == environment and a.status == PolicyActivationStatus.SUPERSEDED
        ]
        if not env_activations:
            return None
        return env_activations[-1]


# -- Tests --


class TestCreateRing:
    @pytest.mark.asyncio
    async def test_create_ring_requires_permission(self):
        service, *_ = _make_service()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[])
        with pytest.raises(PolicyReleasePermissionError):
            await service.create_ring("prod", "stable", "admin", ctx)

    @pytest.mark.asyncio
    async def test_create_ring_succeeds(self):
        service, _, _, ring_store, _ = _make_service()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.ring.create"])
        ring = await service.create_ring("prod", "canary", "admin", ctx, description="Canary ring")
        assert ring.environment == "prod"
        assert ring.name == "canary"
        assert ring.description == "Canary ring"
        assert ring.ring_id.startswith("ring_")
        # Verify persisted
        stored = await ring_store.get(ring.ring_id)
        assert stored is not None
        assert stored.name == "canary"


class TestAssignActivationToRing:
    @pytest.mark.asyncio
    async def test_assign_requires_permission(self):
        service, _, _, _, _ = _make_service()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[])
        with pytest.raises(PolicyReleasePermissionError):
            await service.assign_activation_to_ring("prod", "stable", "pa_001", "admin", ctx)

    @pytest.mark.asyncio
    async def test_assign_validates_environment(self):
        service, bundle_store, activation_store, _, _ = _make_service()
        b1 = _StubBundle("pb_001", "h1")
        bundle_store.add(b1)
        a1 = PolicyActivation(activation_id="pa_001", environment="staging", bundle_id="pb_001", config_hash="h1", activated_by="admin")
        await activation_store.activate(a1)
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.ring.assign"])
        with pytest.raises(ValueError, match="environment"):
            await service.assign_activation_to_ring("prod", "stable", "pa_001", "admin", ctx)

    @pytest.mark.asyncio
    async def test_assign_validates_bundle_exists(self):
        service, _, activation_store, _, _ = _make_service()
        # Activation references a bundle that doesn't exist in bundle_store
        a1 = PolicyActivation(activation_id="pa_001", environment="prod", bundle_id="pb_missing", config_hash="h1", activated_by="admin")
        await activation_store.activate(a1)
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.ring.assign"])
        with pytest.raises(KeyError, match="pb_missing"):
            await service.assign_activation_to_ring("prod", "stable", "pa_001", "admin", ctx)

    @pytest.mark.asyncio
    async def test_assign_succeeds(self):
        service, bundle_store, activation_store, _, ring_assignment_store = _make_service()
        b1 = _StubBundle("pb_001", "h1")
        bundle_store.add(b1)
        a1 = PolicyActivation(activation_id="pa_001", environment="prod", bundle_id="pb_001", config_hash="h1", activated_by="admin")
        await activation_store.activate(a1)
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.ring.assign"])
        assignment = await service.assign_activation_to_ring("prod", "canary", "pa_001", "admin", ctx, reason="Testing canary")
        assert assignment.environment == "prod"
        assert assignment.ring_name == "canary"
        assert assignment.activation_id == "pa_001"
        assert assignment.bundle_id == "pb_001"
        assert assignment.assignment_id.startswith("ra_")
        # Verify persisted
        stored = await ring_assignment_store.get(assignment.assignment_id)
        assert stored is not None


class TestPromoteCanaryToStable:
    @pytest.mark.asyncio
    async def test_promote_canary_to_stable_requires_permission(self):
        service, _, _, _, _ = _make_service()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[])
        with pytest.raises(PolicyReleasePermissionError):
            await service.promote_canary_to_stable("prod", "canary", "stable", "admin", ctx)

    @pytest.mark.asyncio
    async def test_promote_canary_to_stable_succeeds(self):
        service, bundle_store, activation_store, _, ring_assignment_store = _make_service()
        b1 = _StubBundle("pb_001", "h1")
        bundle_store.add(b1)
        a1 = PolicyActivation(activation_id="pa_001", environment="prod", bundle_id="pb_001", config_hash="h1", activated_by="admin")
        await activation_store.activate(a1)
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.ring.assign", "policy.ring.promote"])
        # First assign to canary
        await service.assign_activation_to_ring("prod", "canary", "pa_001", "admin", ctx, reason="Canary test")
        # Now promote canary to stable
        stable_assignment = await service.promote_canary_to_stable("prod", "canary", "stable", "admin", ctx, reason="Canary looks good")
        assert stable_assignment.ring_name == "stable"
        assert stable_assignment.activation_id == "pa_001"
        assert stable_assignment.bundle_id == "pb_001"

    @pytest.mark.asyncio
    async def test_promote_no_canary_assignment_fails(self):
        service, _, _, _, _ = _make_service()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.ring.promote"])
        with pytest.raises(KeyError, match="canary"):
            await service.promote_canary_to_stable("prod", "canary", "stable", "admin", ctx)


class TestDisableRing:
    @pytest.mark.asyncio
    async def test_disable_ring_succeeds(self):
        service, _, _, ring_store, _ = _make_service()
        # Create a ring first
        ctx_create = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.ring.create"])
        ring = await service.create_ring("prod", "canary", "admin", ctx_create)
        # Disable it
        ctx_disable = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.ring.disable"])
        result = await service.disable_ring("prod", "canary", "admin", ctx_disable, reason="Incident")
        assert result.status == ReleaseRingStatus.DISABLED


class TestEnableRing:
    @pytest.mark.asyncio
    async def test_enable_ring_succeeds(self):
        service, _, _, ring_store, _ = _make_service()
        # Create a ring and disable it
        ctx_create = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.ring.create"])
        await service.create_ring("prod", "canary", "admin", ctx_create)
        ctx_disable = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.ring.disable"])
        await service.disable_ring("prod", "canary", "admin", ctx_disable, reason="Incident")
        # Enable it
        ctx_enable = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.ring.enable"])
        result = await service.enable_ring("prod", "canary", "admin", ctx_enable)
        assert result.status == ReleaseRingStatus.ENABLED


class TestRingProperties:
    def test_ring_store_property(self):
        service, _, _, ring_store, _ = _make_service()
        assert service.ring_store is ring_store

    def test_ring_assignment_store_property(self):
        service, _, _, _, ring_assignment_store = _make_service()
        assert service.ring_assignment_store is ring_assignment_store

    def test_ring_store_default_none(self):
        service = PolicyReleaseService(
            bundle_store=_StubBundleStore(),
            replay_runner=_StubReplayRunner(),
            replay_store=None,
            gate_evaluator=_StubGateEvaluator(),
            gate_store=_StubGateStore(),
        )
        assert service.ring_store is None
        assert service.ring_assignment_store is None
