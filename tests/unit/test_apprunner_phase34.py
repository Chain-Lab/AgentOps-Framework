"""Tests for AppRunner Phase 34: ring router integration and policy metadata."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_app.core.context import RunContext
from agent_app.core.result import AppRunResult
from agent_app.runtime.app_runner import AppRunner


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

class MockBundle:
    """Minimal policy bundle stub."""
    def __init__(self, bundle_id="pb_test", config_hash="hash1"):
        self.bundle_id = bundle_id
        self.config_hash = config_hash


class MockActivation:
    """Minimal activation record stub."""
    def __init__(self, environment, bundle_id, config_hash, status="active", activation_id="pa_test"):
        self.environment = environment
        self.bundle_id = bundle_id
        self.config_hash = config_hash
        self.status = status
        self.activation_id = activation_id


class MockBundleStore:
    def __init__(self, bundles):
        self._bundles = {b.bundle_id: b for b in bundles}

    async def get(self, bundle_id):
        return self._bundles.get(bundle_id)


class MockActivationStore:
    def __init__(self, activations):
        self._activations = {a.activation_id: a for a in activations}

    async def get_active(self, environment):
        for a in self._activations.values():
            if a.environment == environment and a.status == "active":
                return a
        return None

    async def get_active_for_ring(self, environment, ring):
        for a in self._activations.values():
            if a.environment == environment and a.ring == ring and a.status == "active":
                return a
        return None


class StubAgentRegistry:
    """Minimal agent registry stub that returns an AgentSpec-like object."""
    def get(self, name):
        spec = MagicMock()
        spec.name = name
        spec.tools = []
        spec.instructions = "test"
        return spec


class StubToolRegistry:
    """Minimal tool registry stub."""
    def get(self, name):
        raise KeyError(name)


class StubWorkflowRegistry:
    """Minimal workflow registry stub."""
    def get(self, name):
        raise KeyError(name)


class MockRingRouter:
    """Mock ring router for testing."""
    def __init__(self, ring_name="stable"):
        self._ring_name = ring_name
        self.resolve_ring = AsyncMock(return_value=ring_name)


class MockPolicyResolver:
    """Mock policy resolver that supports ring-aware resolution."""
    def __init__(self, bundle=None, ring_bundles=None):
        self._bundle = bundle
        self._ring_bundles = ring_bundles or {}
        self.resolve_active_bundle = AsyncMock(return_value=bundle)
        self.resolve_active_bundle_for_ring = AsyncMock(side_effect=self._resolve_for_ring)

    async def _resolve_for_ring(self, environment, ring):
        key = (environment, ring)
        if key in self._ring_bundles:
            return self._ring_bundles[key]
        raise KeyError(f"No bundle for env={environment}, ring={ring}")


def _make_runner(**kwargs):
    """Create an AppRunner with stub registries and optional overrides."""
    defaults = dict(
        agent_registry=StubAgentRegistry(),
        tool_registry=StubToolRegistry(),
        workflow_registry=StubWorkflowRegistry(),
    )
    defaults.update(kwargs)
    return AppRunner(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAppRunnerPhase34:
    """Phase 34: ring router integration and policy metadata in AppRunner."""

    @pytest.mark.asyncio
    async def test_ring_router_stored_on_apprunner(self):
        """AppRunner stores the ring_router passed to it."""
        router = object()
        runner = AppRunner(
            agent_registry=object(),
            tool_registry=object(),
            workflow_registry=object(),
            ring_router=router,
        )
        assert runner._ring_router is router

    @pytest.mark.asyncio
    async def test_result_metadata_includes_environment(self):
        """AppRunResult.metadata includes policy_environment when set on context."""
        bundle = MockBundle(bundle_id="pb_dev", config_hash="hash_dev")
        resolver = MockPolicyResolver(bundle=bundle)
        runner = _make_runner(policy_resolver=resolver)

        result = await runner.run(
            agent="support",
            input="hello",
            metadata={"policy_environment": "dev"},
        )
        assert result.metadata.get("policy_environment") == "dev"

    @pytest.mark.asyncio
    async def test_result_metadata_includes_ring(self):
        """AppRunResult.metadata includes policy_ring when set on context."""
        bundle = MockBundle(bundle_id="pb_stable", config_hash="hash_stable")
        resolver = MockPolicyResolver(bundle=bundle)
        runner = _make_runner(policy_resolver=resolver)

        result = await runner.run(
            agent="support",
            input="hello",
            metadata={"policy_environment": "dev", "policy_ring": "stable"},
        )
        assert result.metadata.get("policy_ring") == "stable"

    @pytest.mark.asyncio
    async def test_result_metadata_includes_bundle_id(self):
        """AppRunResult.metadata includes policy_bundle_id when bundle resolved."""
        bundle = MockBundle(bundle_id="pb_test", config_hash="hash1")
        resolver = MockPolicyResolver(bundle=bundle)
        runner = _make_runner(policy_resolver=resolver)

        result = await runner.run(
            agent="support",
            input="hello",
            metadata={"policy_environment": "dev"},
        )
        assert result.metadata.get("policy_bundle_id") == "pb_test"
        assert result.metadata.get("policy_config_hash") == "hash1"

    @pytest.mark.asyncio
    async def test_ring_router_used_when_no_explicit_ring(self):
        """Ring router is called when context has no explicit policy_ring."""
        ring_bundle = MockBundle(bundle_id="pb_canary", config_hash="hash_canary")
        resolver = MockPolicyResolver(
            bundle=MockBundle(bundle_id="pb_dev", config_hash="hash_dev"),
            ring_bundles={("dev", "canary"): ring_bundle},
        )
        ring_router = MockRingRouter(ring_name="canary")
        runner = _make_runner(policy_resolver=resolver, ring_router=ring_router)

        result = await runner.run(
            agent="support",
            input="hello",
            metadata={"policy_environment": "dev"},
        )
        # Ring router should have been called
        ring_router.resolve_ring.assert_awaited()
        # The ring should be recorded in metadata
        assert result.metadata.get("policy_ring") == "canary"

    @pytest.mark.asyncio
    async def test_ring_router_not_used_when_explicit_ring_set(self):
        """Ring router is NOT called when context already has policy_ring set."""
        resolver = MockPolicyResolver(bundle=MockBundle(bundle_id="pb_dev", config_hash="hash_dev"))
        ring_router = MockRingRouter(ring_name="canary")
        runner = _make_runner(policy_resolver=resolver, ring_router=ring_router)

        result = await runner.run(
            agent="support",
            input="hello",
            metadata={"policy_environment": "dev", "policy_ring": "stable"},
        )
        # Ring router should NOT have been called since ring was explicit
        ring_router.resolve_ring.assert_not_awaited()
        # The explicit ring should be preserved
        assert result.metadata.get("policy_ring") == "stable"

    @pytest.mark.asyncio
    async def test_ring_router_failure_falls_back_to_env_only(self):
        """When ring router fails, falls back to environment-only resolution."""
        bundle = MockBundle(bundle_id="pb_dev", config_hash="hash_dev")
        resolver = MockPolicyResolver(bundle=bundle)
        ring_router = MockRingRouter(ring_name="canary")
        ring_router.resolve_ring = AsyncMock(side_effect=KeyError("no ring"))

        runner = _make_runner(policy_resolver=resolver, ring_router=ring_router)

        result = await runner.run(
            agent="support",
            input="hello",
            metadata={"policy_environment": "dev"},
        )
        # Should still resolve via env-only fallback
        assert result.metadata.get("policy_bundle_id") == "pb_dev"
        # Ring should not be set since router failed
        assert "policy_ring" not in result.metadata

    @pytest.mark.asyncio
    async def test_no_resolver_no_metadata(self):
        """Without a resolver, no policy metadata is added to result."""
        runner = _make_runner()

        result = await runner.run(
            agent="support",
            input="hello",
        )
        assert "policy_bundle_id" not in result.metadata
        assert "policy_environment" not in result.metadata
        assert "policy_ring" not in result.metadata
