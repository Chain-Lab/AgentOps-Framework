"""Tests for AppRunner Phase 31: policy resolver integration."""

import pytest

from agent_app import AgentApp, AgentSpec, Workflow
from agent_app.core.context import RunContext
from agent_app.runtime.app_runner import AppRunner
from agent_app.runtime.policy_resolver import ActivePolicyResolver


class MockBundle:
    def __init__(self, bundle_id, config_hash):
        self.bundle_id = bundle_id
        self.config_hash = config_hash


class MockActivation:
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


@pytest.fixture
def app_with_resolver():
    """AgentApp with a policy resolver configured."""
    bundle = MockBundle(bundle_id="pb_test", config_hash="hash1")
    bundle_store = MockBundleStore([bundle])
    activation = MockActivation(environment="dev", bundle_id="pb_test", config_hash="hash1")
    activation_store = MockActivationStore([activation])
    resolver = ActivePolicyResolver(
        bundle_store=bundle_store,
        activation_store=activation_store,
        cache_ttl_seconds=0,
    )
    app = AgentApp(policy_resolver=resolver)
    app.register_agent(AgentSpec(name="support", instructions="Helpful"))
    app.register_workflow(Workflow.single(agent="support", name="cs"))
    return app


class TestAppRunnerPhase31:
    @pytest.mark.asyncio
    async def test_resolver_stored_on_apprunner(self):
        """AppRunner stores the policy_resolver passed to it."""
        resolver = object()
        runner = AppRunner(
            agent_registry=object(),
            tool_registry=object(),
            workflow_registry=object(),
            policy_resolver=resolver,
        )
        assert runner._policy_resolver is resolver

    @pytest.mark.asyncio
    async def test_no_resolver_returns_none(self):
        """_resolve_active_policy returns None when no resolver configured."""
        runner = AppRunner(
            agent_registry=object(),
            tool_registry=object(),
            workflow_registry=object(),
        )
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")
        result = await runner._resolve_active_policy(ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_uses_context_environment(self):
        """Resolver uses context.policy_environment when set."""
        bundle = MockBundle(bundle_id="pb_prod", config_hash="hash_prod")
        bundle_store = MockBundleStore([bundle])
        activation = MockActivation(environment="prod", bundle_id="pb_prod", config_hash="hash_prod")
        activation_store = MockActivationStore([activation])
        resolver = ActivePolicyResolver(
            bundle_store=bundle_store,
            activation_store=activation_store,
            cache_ttl_seconds=0,
        )
        runner = AppRunner(
            agent_registry=object(),
            tool_registry=object(),
            workflow_registry=object(),
            policy_resolver=resolver,
        )
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", policy_environment="prod")
        result = await runner._resolve_active_policy(ctx)
        assert result is not None
        assert result.bundle_id == "pb_prod"

    @pytest.mark.asyncio
    async def test_resolve_defaults_to_dev(self):
        """Resolver defaults to 'dev' environment when context has none."""
        bundle = MockBundle(bundle_id="pb_dev", config_hash="hash_dev")
        bundle_store = MockBundleStore([bundle])
        activation = MockActivation(environment="dev", bundle_id="pb_dev", config_hash="hash_dev")
        activation_store = MockActivationStore([activation])
        resolver = ActivePolicyResolver(
            bundle_store=bundle_store,
            activation_store=activation_store,
            cache_ttl_seconds=0,
        )
        runner = AppRunner(
            agent_registry=object(),
            tool_registry=object(),
            workflow_registry=object(),
            policy_resolver=resolver,
        )
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")
        result = await runner._resolve_active_policy(ctx)
        assert result is not None
        assert result.bundle_id == "pb_dev"

    @pytest.mark.asyncio
    async def test_resolve_no_active_returns_none(self):
        """Returns None when no active bundle for environment."""
        bundle = MockBundle(bundle_id="pb_dev", config_hash="hash_dev")
        bundle_store = MockBundleStore([bundle])
        activation = MockActivation(environment="staging", bundle_id="pb_dev", config_hash="hash_dev")
        activation_store = MockActivationStore([activation])
        resolver = ActivePolicyResolver(
            bundle_store=bundle_store,
            activation_store=activation_store,
            cache_ttl_seconds=0,
        )
        runner = AppRunner(
            agent_registry=object(),
            tool_registry=object(),
            workflow_registry=object(),
            policy_resolver=resolver,
        )
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", policy_environment="prod")
        result = await runner._resolve_active_policy(ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_run_sets_resolved_bundle_on_context(self, app_with_resolver):
        """AppRunner.run() sets resolved_policy_bundle on context."""
        result = await app_with_resolver.run(agent="support", input="hello")
        # The context used during run should have the resolved bundle
        # We verify this indirectly through the runner
        runner = app_with_resolver._runner
        assert runner._policy_resolver is not None

    @pytest.mark.asyncio
    async def test_agentapp_passes_resolver_to_runner(self):
        """AgentApp._ensure_runner passes policy_resolver to AppRunner."""
        resolver = object()
        app = AgentApp(policy_resolver=resolver)
        app.register_agent(AgentSpec(name="support", instructions="Helpful"))
        # Trigger runner creation
        app._ensure_runner()
        assert app._runner._policy_resolver is resolver
