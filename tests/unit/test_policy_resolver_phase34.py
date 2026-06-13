"""Tests for ActivePolicyResolver cache improvements (Phase 34 Task 4)."""
import pytest

from agent_app.governance.policy_activation import PolicyActivation
from agent_app.governance.policy_bundle import InMemoryPolicyBundleStore, PolicyBundle, PolicyBundleStatus, compute_config_hash
from agent_app.governance.policy_ring import ReleaseRing
from agent_app.governance.policy_ring_assignment import RingActivationAssignment
from agent_app.runtime.policy_activation_store import InMemoryPolicyActivationStore
from agent_app.runtime.policy_environment_store import InMemoryPolicyEnvironmentStore
from agent_app.runtime.policy_resolver import ActivePolicyResolver
from agent_app.runtime.policy_ring_assignment_store import InMemoryRingActivationAssignmentStore
from agent_app.runtime.policy_ring_store import InMemoryReleaseRingStore


def _make_bundle(bundle_id: str, content: str = "test content"):
    return PolicyBundle(
        bundle_id=bundle_id,
        name="test",
        version="1.0.0",
        config_hash=compute_config_hash(content),
        created_by="admin",
    )


def _make_activation(bundle_id: str, env: str = "prod", config_hash: str | None = None):
    ch = config_hash or compute_config_hash("test content")
    return PolicyActivation(
        activation_id=f"pa_{bundle_id}",
        environment=env,
        bundle_id=bundle_id,
        config_hash=ch,
        activated_by="admin",
    )


class TestCacheStatus:
    @pytest.mark.asyncio
    async def test_cache_status_reports_entries(self):
        """After resolving a bundle, cache_status should report entries=1, keys, and ttl."""
        bs = InMemoryPolicyBundleStore()
        b = _make_bundle("pb_1")
        bs._bundles["pb_1"] = b
        bs._order.append("pb_1")
        ac = InMemoryPolicyActivationStore()
        await ac.activate(_make_activation("pb_1", env="dev"))
        resolver = ActivePolicyResolver(bs, ac, cache_ttl_seconds=60)

        await resolver.resolve_active_bundle("dev")

        status = resolver.cache_status()
        assert status["entries"] == 1
        assert "dev" in status["keys"]
        assert status["ttl"] == 60

    @pytest.mark.asyncio
    async def test_cache_status_empty(self):
        """A fresh resolver with no resolves should report entries=0."""
        resolver = ActivePolicyResolver(
            InMemoryPolicyBundleStore(),
            InMemoryPolicyActivationStore(),
            cache_ttl_seconds=60,
        )
        status = resolver.cache_status()
        assert status["entries"] == 0
        assert status["keys"] == []
        assert status["ttl"] == 60

    @pytest.mark.asyncio
    async def test_cache_status_ring_key_format(self):
        """Ring cache keys should be formatted as 'env:ring'."""
        bs = InMemoryPolicyBundleStore()
        b = _make_bundle("pb_1")
        bs._bundles["pb_1"] = b
        bs._order.append("pb_1")
        ac = InMemoryPolicyActivationStore()
        await ac.activate(_make_activation("pb_1", env="prod"))
        assign_store = InMemoryRingActivationAssignmentStore()
        ring_store = InMemoryReleaseRingStore()
        await ring_store.create(ReleaseRing(ring_id="r1", environment="prod", name="canary", is_default=True))
        await assign_store.assign(
            RingActivationAssignment(
                assignment_id="ra_1",
                environment="prod",
                ring_name="canary",
                activation_id="pa_pb_1",
                bundle_id="pb_1",
                config_hash=b.config_hash,
                assigned_by="admin",
            )
        )
        resolver = ActivePolicyResolver(
            bs, ac, cache_ttl_seconds=60,
            ring_assignment_store=assign_store,
            ring_store=ring_store,
        )

        await resolver.resolve_active_bundle_for_ring("prod", "canary")

        status = resolver.cache_status()
        assert status["entries"] == 1
        assert "prod:canary" in status["keys"]


class TestClearCache:
    @pytest.mark.asyncio
    async def test_clear_cache_specific_environment(self):
        """clear_cache(environment='dev') should clear only that env's cache entries."""
        bs = InMemoryPolicyBundleStore()
        b1 = _make_bundle("pb_dev", content="dev content")
        b2 = _make_bundle("pb_staging", content="staging content")
        bs._bundles["pb_dev"] = b1
        bs._order.append("pb_dev")
        bs._bundles["pb_staging"] = b2
        bs._order.append("pb_staging")
        ac = InMemoryPolicyActivationStore()
        await ac.activate(_make_activation("pb_dev", env="dev", config_hash=b1.config_hash))
        await ac.activate(_make_activation("pb_staging", env="staging", config_hash=b2.config_hash))
        resolver = ActivePolicyResolver(bs, ac, cache_ttl_seconds=60)

        await resolver.resolve_active_bundle("dev")
        await resolver.resolve_active_bundle("staging")

        assert resolver.cache_status()["entries"] == 2

        resolver.clear_cache(environment="dev")

        status = resolver.cache_status()
        assert status["entries"] == 1
        assert "staging" in status["keys"]
        assert "dev" not in status["keys"]

    @pytest.mark.asyncio
    async def test_clear_cache_all(self):
        """clear_cache() with no args should clear all cache entries."""
        bs = InMemoryPolicyBundleStore()
        b1 = _make_bundle("pb_dev", content="dev content")
        b2 = _make_bundle("pb_staging", content="staging content")
        bs._bundles["pb_dev"] = b1
        bs._order.append("pb_dev")
        bs._bundles["pb_staging"] = b2
        bs._order.append("pb_staging")
        ac = InMemoryPolicyActivationStore()
        await ac.activate(_make_activation("pb_dev", env="dev", config_hash=b1.config_hash))
        await ac.activate(_make_activation("pb_staging", env="staging", config_hash=b2.config_hash))
        resolver = ActivePolicyResolver(bs, ac, cache_ttl_seconds=60)

        await resolver.resolve_active_bundle("dev")
        await resolver.resolve_active_bundle("staging")

        assert resolver.cache_status()["entries"] == 2

        resolver.clear_cache()

        assert resolver.cache_status()["entries"] == 0

    @pytest.mark.asyncio
    async def test_clear_cache_specific_env_and_ring(self):
        """clear_cache(environment='prod', ring_name='canary') should clear only that specific ring entry."""
        bs = InMemoryPolicyBundleStore()
        b = _make_bundle("pb_1")
        bs._bundles["pb_1"] = b
        bs._order.append("pb_1")
        ac = InMemoryPolicyActivationStore()
        await ac.activate(_make_activation("pb_1", env="prod"))
        assign_store = InMemoryRingActivationAssignmentStore()
        ring_store = InMemoryReleaseRingStore()
        await ring_store.create(ReleaseRing(ring_id="r1", environment="prod", name="canary", is_default=True))
        await ring_store.create(ReleaseRing(ring_id="r2", environment="prod", name="stable", is_default=False))
        await assign_store.assign(
            RingActivationAssignment(
                assignment_id="ra_1",
                environment="prod",
                ring_name="canary",
                activation_id="pa_pb_1",
                bundle_id="pb_1",
                config_hash=b.config_hash,
                assigned_by="admin",
            )
        )
        await assign_store.assign(
            RingActivationAssignment(
                assignment_id="ra_2",
                environment="prod",
                ring_name="stable",
                activation_id="pa_pb_1",
                bundle_id="pb_1",
                config_hash=b.config_hash,
                assigned_by="admin",
            )
        )
        resolver = ActivePolicyResolver(
            bs, ac, cache_ttl_seconds=60,
            ring_assignment_store=assign_store,
            ring_store=ring_store,
        )

        await resolver.resolve_active_bundle("prod")
        await resolver.resolve_active_bundle_for_ring("prod", "canary")
        await resolver.resolve_active_bundle_for_ring("prod", "stable")

        assert resolver.cache_status()["entries"] == 3

        resolver.clear_cache(environment="prod", ring_name="canary")

        status = resolver.cache_status()
        assert status["entries"] == 2
        assert "prod:canary" not in status["keys"]
        assert "prod:stable" in status["keys"]
        assert "prod" in status["keys"]


class TestRefresh:
    @pytest.mark.asyncio
    async def test_refresh_specific_target(self):
        """refresh(environment, ring_name) should clear that specific ring cache entry."""
        bs = InMemoryPolicyBundleStore()
        b = _make_bundle("pb_1")
        bs._bundles["pb_1"] = b
        bs._order.append("pb_1")
        ac = InMemoryPolicyActivationStore()
        await ac.activate(_make_activation("pb_1", env="prod"))
        assign_store = InMemoryRingActivationAssignmentStore()
        ring_store = InMemoryReleaseRingStore()
        await ring_store.create(ReleaseRing(ring_id="r1", environment="prod", name="canary", is_default=True))
        await ring_store.create(ReleaseRing(ring_id="r2", environment="prod", name="stable", is_default=False))
        await assign_store.assign(
            RingActivationAssignment(
                assignment_id="ra_1",
                environment="prod",
                ring_name="canary",
                activation_id="pa_pb_1",
                bundle_id="pb_1",
                config_hash=b.config_hash,
                assigned_by="admin",
            )
        )
        await assign_store.assign(
            RingActivationAssignment(
                assignment_id="ra_2",
                environment="prod",
                ring_name="stable",
                activation_id="pa_pb_1",
                bundle_id="pb_1",
                config_hash=b.config_hash,
                assigned_by="admin",
            )
        )
        resolver = ActivePolicyResolver(
            bs, ac, cache_ttl_seconds=60,
            ring_assignment_store=assign_store,
            ring_store=ring_store,
        )

        await resolver.resolve_active_bundle_for_ring("prod", "canary")
        await resolver.resolve_active_bundle_for_ring("prod", "stable")

        assert resolver.cache_status()["entries"] == 2

        resolver.refresh(environment="prod", ring_name="canary")

        status = resolver.cache_status()
        assert status["entries"] == 1
        assert "prod:stable" in status["keys"]

    @pytest.mark.asyncio
    async def test_refresh_clears_env_and_ring_keys(self):
        """refresh(environment) should clear both env key and all ring keys for that env."""
        bs = InMemoryPolicyBundleStore()
        b = _make_bundle("pb_1")
        bs._bundles["pb_1"] = b
        bs._order.append("pb_1")
        ac = InMemoryPolicyActivationStore()
        await ac.activate(_make_activation("pb_1", env="prod"))
        assign_store = InMemoryRingActivationAssignmentStore()
        ring_store = InMemoryReleaseRingStore()
        await ring_store.create(ReleaseRing(ring_id="r1", environment="prod", name="canary", is_default=True))
        await assign_store.assign(
            RingActivationAssignment(
                assignment_id="ra_1",
                environment="prod",
                ring_name="canary",
                activation_id="pa_pb_1",
                bundle_id="pb_1",
                config_hash=b.config_hash,
                assigned_by="admin",
            )
        )
        resolver = ActivePolicyResolver(
            bs, ac, cache_ttl_seconds=60,
            ring_assignment_store=assign_store,
            ring_store=ring_store,
        )

        await resolver.resolve_active_bundle("prod")
        await resolver.resolve_active_bundle_for_ring("prod", "canary")

        assert resolver.cache_status()["entries"] == 2

        resolver.refresh(environment="prod")

        assert resolver.cache_status()["entries"] == 0

    @pytest.mark.asyncio
    async def test_refresh_no_args_clears_all(self):
        """refresh() with no args should clear all cache entries."""
        bs = InMemoryPolicyBundleStore()
        b1 = _make_bundle("pb_dev", content="dev content")
        b2 = _make_bundle("pb_staging", content="staging content")
        bs._bundles["pb_dev"] = b1
        bs._order.append("pb_dev")
        bs._bundles["pb_staging"] = b2
        bs._order.append("pb_staging")
        ac = InMemoryPolicyActivationStore()
        await ac.activate(_make_activation("pb_dev", env="dev", config_hash=b1.config_hash))
        await ac.activate(_make_activation("pb_staging", env="staging", config_hash=b2.config_hash))
        resolver = ActivePolicyResolver(bs, ac, cache_ttl_seconds=60)

        await resolver.resolve_active_bundle("dev")
        await resolver.resolve_active_bundle("staging")

        assert resolver.cache_status()["entries"] == 2

        resolver.refresh()

        assert resolver.cache_status()["entries"] == 0


class TestDisabledEnvNoStaleCache:
    @pytest.mark.asyncio
    async def test_disabled_env_does_not_serve_stale_cache(self):
        """After disabling an environment, resolve should return None, not stale cached bundle."""
        bs = InMemoryPolicyBundleStore()
        b = _make_bundle("pb_1")
        bs._bundles["pb_1"] = b
        bs._order.append("pb_1")
        ac = InMemoryPolicyActivationStore()
        await ac.activate(_make_activation("pb_1", env="prod"))
        env_store = InMemoryPolicyEnvironmentStore()
        resolver = ActivePolicyResolver(
            bs, ac, cache_ttl_seconds=60, environment_store=env_store,
        )

        # Resolve while enabled — should get the bundle
        result1 = await resolver.resolve_active_bundle("prod")
        assert result1 is not None
        assert result1.bundle_id == "pb_1"

        # Cache should now have an entry
        assert resolver.cache_status()["entries"] >= 1

        # Disable the environment
        await env_store.disable("prod", disabled_by="admin", reason="Emergency")

        # Resolve again — should return None, not the stale cached bundle
        result2 = await resolver.resolve_active_bundle("prod")
        assert result2 is None
