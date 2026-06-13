"""Tests for ActivePolicyResolver safety checks (Phase 32)."""
import pytest
from agent_app.runtime.policy_resolver import ActivePolicyResolver
from agent_app.runtime.policy_environment_store import InMemoryPolicyEnvironmentStore


class _MockBundle:
    def __init__(self, bid, chash):
        self.bundle_id = bid
        self.config_hash = chash


class _MockBundleStore:
    def __init__(self, bundles):
        self._b = {b.bundle_id: b for b in bundles}
    async def get(self, bid):
        return self._b.get(bid)


class _MockActivation:
    def __init__(self, env, bid, chash):
        self.environment = env
        self.bundle_id = bid
        self.config_hash = chash
        self.status = "active"


class _MockActivationStore:
    def __init__(self, acts):
        self._a = {a.environment: a for a in acts}
    async def get_active(self, env):
        return self._a.get(env)


@pytest.fixture
def resolver_with_env_store():
    bundle = _MockBundle("pb_1", "h1")
    activation = _MockActivation("prod", "pb_1", "h1")
    env_store = InMemoryPolicyEnvironmentStore()
    return ActivePolicyResolver(
        bundle_store=_MockBundleStore([bundle]),
        activation_store=_MockActivationStore([activation]),
        environment_store=env_store,
    ), env_store


class TestResolverSafety:
    @pytest.mark.asyncio
    async def test_disabled_environment_returns_none(self, resolver_with_env_store):
        resolver, env_store = resolver_with_env_store
        await env_store.disable("prod", disabled_by="admin", reason="Emergency")
        result = await resolver.resolve_active_bundle("prod")
        assert result is None

    @pytest.mark.asyncio
    async def test_disabled_environment_raises_on_require(self, resolver_with_env_store):
        resolver, env_store = resolver_with_env_store
        await env_store.disable("prod", disabled_by="admin", reason="Emergency")
        with pytest.raises(RuntimeError, match="disabled"):
            await resolver.require_active_bundle("prod")

    @pytest.mark.asyncio
    async def test_enabled_environment_resolves(self, resolver_with_env_store):
        resolver, _ = resolver_with_env_store
        result = await resolver.resolve_active_bundle("prod")
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_env_store_still_works(self):
        """Backward compat: resolver without environment store still resolves."""
        bundle = _MockBundle("pb_1", "h1")
        activation = _MockActivation("prod", "pb_1", "h1")
        resolver = ActivePolicyResolver(
            bundle_store=_MockBundleStore([bundle]),
            activation_store=_MockActivationStore([activation]),
        )
        result = await resolver.resolve_active_bundle("prod")
        assert result is not None

    @pytest.mark.asyncio
    async def test_require_disabled_shows_reason(self, resolver_with_env_store):
        resolver, env_store = resolver_with_env_store
        await env_store.disable("prod", disabled_by="admin", reason="Elevated failure rate")
        with pytest.raises(RuntimeError, match="Elevated failure rate"):
            await resolver.require_active_bundle("prod")

    @pytest.mark.asyncio
    async def test_reenable_restores_resolution(self, resolver_with_env_store):
        resolver, env_store = resolver_with_env_store
        await env_store.disable("prod", disabled_by="admin", reason="Emergency")
        result1 = await resolver.resolve_active_bundle("prod")
        assert result1 is None
        await env_store.enable("prod", enabled_by="admin2")
        result2 = await resolver.resolve_active_bundle("prod")
        assert result2 is not None
