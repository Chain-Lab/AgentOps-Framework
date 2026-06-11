"""Tests for PolicyReleaseService Phase 31 — environment-aware activation."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from agent_app.governance.policy_activation import PolicyActivation, PolicyActivationStatus
from agent_app.governance.policy_bundle import PolicyBundleStatus, compute_config_hash
from agent_app.runtime.policy_activation_store import InMemoryPolicyActivationStore
from agent_app.runtime.policy_release import PolicyReleaseService
from agent_app.runtime.policy_resolver import ActivePolicyResolver
from agent_app.runtime.promotion_store import InMemoryPromotionRequestStore, PromotionRequest, PromotionRequestStatus


def _make_bundle(store, bundle_id: str) -> None:
    """Helper to inject a PolicyBundle directly into a store."""
    from agent_app.governance.policy_bundle import PolicyBundle
    bundle = PolicyBundle(
        bundle_id=bundle_id,
        name="test",
        version="1.0.0",
        status=PolicyBundleStatus.ACTIVE,
        config_hash=compute_config_hash("test"),
        created_by="admin",
    )
    store._bundles[bundle_id] = bundle
    store._order.append(bundle_id)


class TestPolicyReleaseServicePhase31:
    def setup_method(self):
        from agent_app.governance.policy_bundle import InMemoryPolicyBundleStore
        self.bundle_store = InMemoryPolicyBundleStore()
        self.activation_store = InMemoryPolicyActivationStore()
        self.resolver = ActivePolicyResolver(self.bundle_store, self.activation_store)
        self.service = PolicyReleaseService(
            bundle_store=self.bundle_store,
            replay_runner=None,
            replay_store=None,
            gate_evaluator=None,
            gate_store=None,
            activation_store=self.activation_store,
            policy_resolver=self.resolver,
        )

    @pytest.mark.asyncio
    async def test_execute_approved_promotion_creates_activation(self):
        _make_bundle(self.bundle_store, "pb_1")
        pr_store = InMemoryPromotionRequestStore()
        pr_store._requests["pr_1"] = PromotionRequest(
            promotion_id="pr_1",
            bundle_id="pb_1",
            requested_by="alice",
            status=PromotionRequestStatus.APPROVED,
            created_at=datetime.now(timezone.utc),
        )
        pr_store._order.append("pr_1")

        from agent_app.core.context import RunContext
        ctx = RunContext(run_id="cli_admin", user_id="admin", tenant_id="default", permissions=["policy.promotion.execute"])

        # Minimal mock gate store so execute_promotion gate check passes
        class MockGateStore:
            async def list(self, **kwargs):
                return []

        service = PolicyReleaseService(
            bundle_store=self.bundle_store,
            replay_runner=None,
            replay_store=None,
            gate_evaluator=None,
            gate_store=MockGateStore(),
            promotion_store=pr_store,
            activation_store=self.activation_store,
            policy_resolver=self.resolver,
        )
        result = await service.execute_promotion(
            promotion_id="pr_1", executed_by="admin", context=ctx, environment="prod"
        )
        assert result is not None
        assert result.environment == "prod"
        assert result.bundle_id == "pb_1"
        assert result.status == PolicyActivationStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_execute_pending_fails(self):
        pr_store = InMemoryPromotionRequestStore()
        pr_store._requests["pr_1"] = PromotionRequest(
            promotion_id="pr_1",
            bundle_id="pb_1",
            requested_by="alice",
            status=PromotionRequestStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        pr_store._order.append("pr_1")

        from agent_app.core.context import RunContext
        ctx = RunContext(run_id="cli_admin", user_id="admin", tenant_id="default", permissions=["policy.promotion.execute"])

        class MockGateStore:
            async def list(self, **kwargs):
                return []

        service = PolicyReleaseService(
            bundle_store=self.bundle_store,
            replay_runner=None,
            replay_store=None,
            gate_evaluator=None,
            gate_store=MockGateStore(),
            promotion_store=pr_store,
            activation_store=self.activation_store,
        )
        with pytest.raises(ValueError, match="must be approved"):
            await service.execute_promotion(
                promotion_id="pr_1", executed_by="admin", context=ctx, environment="prod"
            )

    @pytest.mark.asyncio
    async def test_get_active_policy(self):
        _make_bundle(self.bundle_store, "pb_1")
        await self.activation_store.activate(
            PolicyActivation(
                activation_id="pa_1",
                environment="prod",
                bundle_id="pb_1",
                config_hash=compute_config_hash("test"),
                activated_by="admin",
            )
        )
        result = await self.service.get_active_policy("prod")
        assert result is not None
        assert result.bundle_id == "pb_1"

    @pytest.mark.asyncio
    async def test_list_activations(self):
        await self.activation_store.activate(
            PolicyActivation(
                activation_id="pa_dev",
                environment="dev",
                bundle_id="pb_1",
                config_hash="h1",
                activated_by="admin",
            )
        )
        await self.activation_store.activate(
            PolicyActivation(
                activation_id="pa_prod",
                environment="prod",
                bundle_id="pb_1",
                config_hash="h1",
                activated_by="admin",
            )
        )
        assert len(await self.service.list_activations()) == 2
        prod_acts = await self.service.list_activations(environment="prod")
        assert len(prod_acts) == 1 and prod_acts[0].environment == "prod"

    @pytest.mark.asyncio
    async def test_resolve_active_policy_integration(self):
        _make_bundle(self.bundle_store, "pb_1")
        await self.activation_store.activate(
            PolicyActivation(
                activation_id="pa_1",
                environment="prod",
                bundle_id="pb_1",
                config_hash=compute_config_hash("test"),
                activated_by="admin",
            )
        )
        bundle = await self.resolver.resolve_active_bundle("prod")
        assert bundle.bundle_id == "pb_1"
