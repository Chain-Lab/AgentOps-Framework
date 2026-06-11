import pytest
from agent_app.governance.policy_activation import PolicyActivation, PolicyActivationStatus
from agent_app.governance.policy_bundle import PolicyBundleStatus, compute_config_hash
from agent_app.runtime.policy_activation_store import InMemoryPolicyActivationStore
from agent_app.runtime.policy_resolver import ActivePolicyResolver


def _make_bundle(bundle_id: str):
    from agent_app.governance.policy_bundle import PolicyBundle
    return PolicyBundle(bundle_id=bundle_id, name="test", version="1.0.0",
                        config_hash=compute_config_hash("test content"), created_by="admin")


def _make_activation(bundle_id: str, env: str = "prod"):
    from agent_app.governance.policy_activation import PolicyActivation
    bundle = _make_bundle(bundle_id)
    return PolicyActivation(activation_id=f"pa_{bundle_id}", environment=env, bundle_id=bundle_id,
                            config_hash=bundle.config_hash, activated_by="admin")


class TestActivePolicyResolver:
    @pytest.mark.asyncio
    async def test_resolve_active_bundle(self):
        from agent_app.governance.policy_bundle import InMemoryPolicyBundleStore
        bs = InMemoryPolicyBundleStore()
        b = _make_bundle("pb_1")
        bs._bundles["pb_1"] = b
        bs._order.append("pb_1")
        ac = InMemoryPolicyActivationStore()
        await ac.activate(_make_activation("pb_1"))
        resolver = ActivePolicyResolver(bs, ac)
        result = await resolver.resolve_active_bundle("prod")
        assert result.bundle_id == "pb_1"

    @pytest.mark.asyncio
    async def test_resolve_returns_none_when_no_active(self):
        from agent_app.governance.policy_bundle import InMemoryPolicyBundleStore
        resolver = ActivePolicyResolver(InMemoryPolicyBundleStore(), InMemoryPolicyActivationStore())
        assert await resolver.resolve_active_bundle("prod") is None

    @pytest.mark.asyncio
    async def test_require_active_bundle_raises_when_missing(self):
        from agent_app.governance.policy_bundle import InMemoryPolicyBundleStore
        resolver = ActivePolicyResolver(InMemoryPolicyBundleStore(), InMemoryPolicyActivationStore())
        with pytest.raises(KeyError, match="No active policy"):
            await resolver.require_active_bundle("prod")

    @pytest.mark.asyncio
    async def test_hash_mismatch_raises(self):
        from agent_app.governance.policy_bundle import InMemoryPolicyBundleStore
        bs = InMemoryPolicyBundleStore()
        b = _make_bundle("pb_1")
        bs._bundles["pb_1"] = b
        bs._order.append("pb_1")
        ac = InMemoryPolicyActivationStore()
        await ac.activate(PolicyActivation(activation_id="pa_bad", environment="prod", bundle_id="pb_1", config_hash="wrong", activated_by="admin"))
        resolver = ActivePolicyResolver(bs, ac)
        with pytest.raises(ValueError, match="config_hash mismatch"):
            await resolver.resolve_active_bundle("prod")

    @pytest.mark.asyncio
    async def test_bundle_not_found_raises(self):
        from agent_app.governance.policy_bundle import InMemoryPolicyBundleStore
        ac = InMemoryPolicyActivationStore()
        await ac.activate(PolicyActivation(activation_id="pa_1", environment="prod", bundle_id="pb_missing", config_hash="h1", activated_by="admin"))
        resolver = ActivePolicyResolver(InMemoryPolicyBundleStore(), ac)
        with pytest.raises(KeyError, match="not found"):
            await resolver.resolve_active_bundle("prod")

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        from agent_app.governance.policy_bundle import InMemoryPolicyBundleStore
        bs = InMemoryPolicyBundleStore()
        b = _make_bundle("pb_1")
        bs._bundles["pb_1"] = b
        bs._order.append("pb_1")
        ac = InMemoryPolicyActivationStore()
        await ac.activate(_make_activation("pb_1"))
        resolver = ActivePolicyResolver(bs, ac, cache_ttl_seconds=60)
        r1 = await resolver.resolve_active_bundle("prod")
        r2 = await resolver.resolve_active_bundle("prod")
        assert r1.bundle_id == r2.bundle_id == "pb_1"

    @pytest.mark.asyncio
    async def test_cache_refresh(self):
        from agent_app.governance.policy_bundle import InMemoryPolicyBundleStore
        bs = InMemoryPolicyBundleStore()
        b = _make_bundle("pb_1")
        bs._bundles["pb_1"] = b
        bs._order.append("pb_1")
        ac = InMemoryPolicyActivationStore()
        await ac.activate(_make_activation("pb_1"))
        resolver = ActivePolicyResolver(bs, ac, cache_ttl_seconds=60)
        await resolver.resolve_active_bundle("prod")
        resolver.refresh("prod")
        assert "prod" not in resolver._cache

    def test_clear_cache(self):
        from agent_app.governance.policy_bundle import InMemoryPolicyBundleStore
        resolver = ActivePolicyResolver(InMemoryPolicyBundleStore(), InMemoryPolicyActivationStore(), cache_ttl_seconds=60)
        resolver._cache["prod"] = type("E", (), {"bundle": None, "is_expired": lambda self: False})()
        resolver.clear_cache()
        assert len(resolver._cache) == 0
