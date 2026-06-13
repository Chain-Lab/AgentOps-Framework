"""Tests for PolicyReleaseService Phase 32: rollback, disable, enable."""
import pytest
from agent_app.governance.policy_activation import PolicyActivation, PolicyActivationStatus
from agent_app.governance.policy_environment import PolicyEnvironmentStatus
from agent_app.runtime.policy_activation_store import InMemoryPolicyActivationStore
from agent_app.runtime.policy_environment_store import InMemoryPolicyEnvironmentStore
from agent_app.runtime.policy_release import PolicyReleaseService, PolicyReleasePermissionError
from agent_app.core.context import RunContext


def _make_service():
    """Build a PolicyReleaseService with in-memory stores for testing."""
    bundle_store = _StubBundleStore()
    activation_store = InMemoryPolicyActivationStore()
    environment_store = InMemoryPolicyEnvironmentStore()
    service = PolicyReleaseService(
        bundle_store=bundle_store,
        replay_runner=_StubReplayRunner(),
        replay_store=None,
        gate_evaluator=_StubGateEvaluator(),
        gate_store=_StubGateStore(),
        activation_store=activation_store,
        environment_store=environment_store,
    )
    return service, bundle_store, activation_store, environment_store


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


class TestRollbackEnvironment:
    @pytest.mark.asyncio
    async def test_rollback_requires_permission(self):
        service, _, _, _ = _make_service()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[])
        with pytest.raises(PolicyReleasePermissionError):
            await service.rollback_environment("prod", "admin", ctx)

    @pytest.mark.asyncio
    async def test_rollback_to_previous(self):
        service, bundle_store, activation_store, _ = _make_service()
        # Create two bundles and activate them
        b1 = _StubBundle("pb_001", "h1")
        b2 = _StubBundle("pb_002", "h2")
        bundle_store.add(b1)
        bundle_store.add(b2)
        a1 = PolicyActivation(activation_id="pa_001", environment="prod", bundle_id="pb_001", config_hash="h1", activated_by="admin")
        a2 = PolicyActivation(activation_id="pa_002", environment="prod", bundle_id="pb_002", config_hash="h2", activated_by="admin")
        await activation_store.activate(a1)
        await activation_store.activate(a2)
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.rollback.execute"])
        result = await service.rollback_environment("prod", "ops", ctx, reason="Regression detected")
        assert result.status == PolicyActivationStatus.ACTIVE
        assert result.bundle_id == "pb_001"  # rolled back to previous

    @pytest.mark.asyncio
    async def test_rollback_to_explicit_target(self):
        service, bundle_store, activation_store, _ = _make_service()
        b1 = _StubBundle("pb_001", "h1")
        b2 = _StubBundle("pb_002", "h2")
        b3 = _StubBundle("pb_003", "h3")
        bundle_store.add(b1)
        bundle_store.add(b2)
        bundle_store.add(b3)
        a1 = PolicyActivation(activation_id="pa_001", environment="prod", bundle_id="pb_001", config_hash="h1", activated_by="admin")
        a2 = PolicyActivation(activation_id="pa_002", environment="prod", bundle_id="pb_002", config_hash="h2", activated_by="admin")
        a3 = PolicyActivation(activation_id="pa_003", environment="prod", bundle_id="pb_003", config_hash="h3", activated_by="admin")
        await activation_store.activate(a1)
        await activation_store.activate(a2)
        await activation_store.activate(a3)
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.rollback.execute"])
        result = await service.rollback_environment("prod", "ops", ctx, target_activation_id="pa_001")
        assert result.bundle_id == "pb_001"

    @pytest.mark.asyncio
    async def test_rollback_no_previous_fails(self):
        service, bundle_store, activation_store, _ = _make_service()
        b1 = _StubBundle("pb_001", "h1")
        bundle_store.add(b1)
        a1 = PolicyActivation(activation_id="pa_001", environment="prod", bundle_id="pb_001", config_hash="h1", activated_by="admin")
        await activation_store.activate(a1)
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.rollback.execute"])
        with pytest.raises(ValueError, match="No previous activation"):
            await service.rollback_environment("prod", "ops", ctx)

    @pytest.mark.asyncio
    async def test_rollback_target_wrong_environment_fails(self):
        service, bundle_store, activation_store, _ = _make_service()
        b1 = _StubBundle("pb_001", "h1")
        bundle_store.add(b1)
        a1 = PolicyActivation(activation_id="pa_001", environment="staging", bundle_id="pb_001", config_hash="h1", activated_by="admin")
        await activation_store.activate(a1)
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.rollback.execute"])
        with pytest.raises(ValueError, match="environment"):
            await service.rollback_environment("prod", "ops", ctx, target_activation_id="pa_001")

    @pytest.mark.asyncio
    async def test_rollback_target_not_found_fails(self):
        service, bundle_store, activation_store, _ = _make_service()
        b1 = _StubBundle("pb_001", "h1")
        bundle_store.add(b1)
        a1 = PolicyActivation(activation_id="pa_001", environment="prod", bundle_id="pb_001", config_hash="h1", activated_by="admin")
        await activation_store.activate(a1)
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.rollback.execute"])
        with pytest.raises(KeyError):
            await service.rollback_environment("prod", "ops", ctx, target_activation_id="pa_nonexistent")

    @pytest.mark.asyncio
    async def test_rollback_bundle_missing_fails(self):
        service, bundle_store, activation_store, _ = _make_service()
        # Create activations but don't add the bundle for the previous one
        b2 = _StubBundle("pb_002", "h2")
        bundle_store.add(b2)
        a1 = PolicyActivation(activation_id="pa_001", environment="prod", bundle_id="pb_001", config_hash="h1", activated_by="admin")
        a2 = PolicyActivation(activation_id="pa_002", environment="prod", bundle_id="pb_002", config_hash="h2", activated_by="admin")
        await activation_store.activate(a1)
        await activation_store.activate(a2)
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.rollback.execute"])
        with pytest.raises(ValueError, match="bundle"):
            await service.rollback_environment("prod", "ops", ctx)


class TestDisablePolicyEnvironment:
    @pytest.mark.asyncio
    async def test_disable_requires_permission(self):
        service, _, _, _ = _make_service()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[])
        with pytest.raises(PolicyReleasePermissionError):
            await service.disable_policy_environment("prod", "admin", ctx, reason="Emergency")

    @pytest.mark.asyncio
    async def test_disable_without_reason_fails(self):
        service, _, _, _ = _make_service()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.environment.disable"])
        with pytest.raises(ValueError, match="reason"):
            await service.disable_policy_environment("prod", "admin", ctx, reason="")

    @pytest.mark.asyncio
    async def test_disable_succeeds(self):
        service, _, _, env_store = _make_service()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.environment.disable"])
        state = await service.disable_policy_environment("prod", "admin", ctx, reason="Emergency")
        assert state.status == PolicyEnvironmentStatus.DISABLED
        # Verify it's persisted
        stored = await env_store.get("prod")
        assert stored.status == PolicyEnvironmentStatus.DISABLED


class TestEnablePolicyEnvironment:
    @pytest.mark.asyncio
    async def test_enable_requires_permission(self):
        service, _, _, _ = _make_service()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[])
        with pytest.raises(PolicyReleasePermissionError):
            await service.enable_policy_environment("prod", "admin", ctx)

    @pytest.mark.asyncio
    async def test_enable_succeeds(self):
        service, _, _, env_store = _make_service()
        # First disable
        ctx1 = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.environment.disable"])
        await service.disable_policy_environment("prod", "admin", ctx1, reason="Emergency")
        # Then enable
        ctx2 = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.environment.enable"])
        state = await service.enable_policy_environment("prod", "admin", ctx2)
        assert state.status == PolicyEnvironmentStatus.ENABLED


class TestEnvironmentStoreProperty:
    def test_environment_store_property(self):
        service, _, _, env_store = _make_service()
        assert service.environment_store is env_store

    def test_environment_store_default_none(self):
        service = PolicyReleaseService(
            bundle_store=_StubBundleStore(),
            replay_runner=_StubReplayRunner(),
            replay_store=None,
            gate_evaluator=_StubGateEvaluator(),
            gate_store=_StubGateStore(),
        )
        assert service.environment_store is None
