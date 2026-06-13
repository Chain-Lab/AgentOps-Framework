"""Tests for PolicyReleaseService Phase 34 Task 5: change event emission."""
import pytest
from agent_app.governance.policy_activation import PolicyActivation, PolicyActivationStatus
from agent_app.governance.policy_ring import ReleaseRing, ReleaseRingStatus
from agent_app.governance.policy_ring_assignment import RingActivationAssignment, RingActivationAssignmentStatus
from agent_app.governance.policy_change_event import PolicyChangeEvent, PolicyChangeEventType
from agent_app.runtime.policy_ring_store import InMemoryReleaseRingStore
from agent_app.runtime.policy_ring_assignment_store import InMemoryRingActivationAssignmentStore
from agent_app.runtime.policy_change_event_store import InMemoryPolicyChangeEventStore
from agent_app.runtime.policy_environment_store import InMemoryPolicyEnvironmentStore
from agent_app.runtime.policy_release import PolicyReleaseService, PolicyReleasePermissionError
from agent_app.core.context import RunContext


def _make_service(event_store=None, reload_manager=None, strict=False):
    """Build a PolicyReleaseService with in-memory stores for testing."""
    bundle_store = _StubBundleStore()
    activation_store = InMemoryPolicyActivationStore()
    ring_store = InMemoryReleaseRingStore()
    ring_assignment_store = InMemoryRingActivationAssignmentStore()
    environment_store = InMemoryPolicyEnvironmentStore()
    service = PolicyReleaseService(
        bundle_store=bundle_store,
        replay_runner=_StubReplayRunner(),
        replay_store=None,
        gate_evaluator=_StubGateEvaluator(),
        gate_store=_StubGateStore(),
        activation_store=activation_store,
        environment_store=environment_store,
        ring_store=ring_store,
        ring_assignment_store=ring_assignment_store,
        event_store=event_store,
        reload_manager=reload_manager,
        strict=strict,
    )
    return service, bundle_store, activation_store, ring_store, ring_assignment_store, environment_store


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

    async def rollback_to_activation(
        self, environment: str, target_activation_id: str, rolled_back_by: str = None, reason: str = None
    ):
        target = self._activations.get(target_activation_id)
        if target is None:
            raise KeyError(f"Activation '{target_activation_id}' not found.")
        # Supersede current active
        for a in self._activations.values():
            if a.environment == environment and a.status == PolicyActivationStatus.ACTIVE:
                a.status = PolicyActivationStatus.SUPERSEDED
        # Create new activation pointing to same bundle
        new_activation = PolicyActivation(
            activation_id=f"pa_rollback_{len(self._activations)}",
            environment=environment,
            bundle_id=target.bundle_id,
            config_hash=target.config_hash,
            activated_by=rolled_back_by,
            reason=reason,
        )
        self._activations[new_activation.activation_id] = new_activation
        return new_activation


class _StubReloadManager:
    """Stub reload manager that tracks refresh_resolver calls."""

    def __init__(self):
        self.refresh_calls = []

    async def refresh_resolver(self, environment=None, ring_name=None):
        self.refresh_calls.append({"environment": environment, "ring_name": ring_name})


class _FailingEventStore:
    """Event store that always raises on append."""

    async def append(self, event):
        raise RuntimeError("Event store is down")

    async def get(self, event_id):
        return None

    async def list(self, **kw):
        return []

    async def latest(self, **kw):
        return None


# -- Helper to set up a full promotion chain --


async def _setup_promotion_chain(service, bundle_store, activation_store):
    """Create a bundle, promotion request, and approve it for execution."""
    from agent_app.governance.policy_promotion import PromotionRequest, PromotionRequestStatus

    b1 = _StubBundle("pb_001", "h1")
    bundle_store.add(b1)

    # We need a promotion store for execute_promotion
    promotion_store = _StubPromotionStore()
    service._promotion_store = promotion_store

    # Create and approve a promotion request
    req = PromotionRequest(
        promotion_id="pr_001",
        bundle_id="pb_001",
        requested_by="admin",
        tenant_id="t1",
        reason="Test promotion",
    )
    req = await promotion_store.create(req)
    await promotion_store.approve(promotion_id="pr_001", approved_by="admin")
    return req


class _StubPromotionStore:
    def __init__(self):
        self._requests = {}

    async def create(self, req):
        self._requests[req.promotion_id] = req
        return req

    async def get(self, promotion_id):
        return self._requests.get(promotion_id)

    async def approve(self, promotion_id, approved_by=None, reason=None):
        from agent_app.governance.policy_promotion import PromotionRequestStatus
        req = self._requests[promotion_id]
        # Use model_copy or direct field assignment for frozen model
        object.__setattr__(req, 'status', PromotionRequestStatus.APPROVED)
        object.__setattr__(req, 'resolved_by', approved_by)
        return req

    async def mark_executed(self, promotion_id, executed_by=None):
        from agent_app.governance.policy_promotion import PromotionRequestStatus
        req = self._requests[promotion_id]
        object.__setattr__(req, 'status', PromotionRequestStatus.EXECUTED)
        object.__setattr__(req, 'executed_by', executed_by)
        return req


# -- Tests --


class TestActivationCreatesChangeEvent:
    @pytest.mark.asyncio
    async def test_activation_creates_change_event(self):
        event_store = InMemoryPolicyChangeEventStore()
        service, bundle_store, activation_store, *_ = _make_service(event_store=event_store)
        req = await _setup_promotion_chain(service, bundle_store, activation_store)

        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.promotion.execute"])
        activation = await service.execute_promotion(
            promotion_id="pr_001",
            executed_by="admin",
            context=ctx,
            environment="prod",
        )

        # Check that ACTIVATION_CREATED event was emitted
        events = await event_store.list(environment="prod")
        activation_events = [e for e in events if e.event_type == PolicyChangeEventType.ACTIVATION_CREATED]
        assert len(activation_events) == 1
        evt = activation_events[0]
        assert evt.environment == "prod"
        assert evt.bundle_id == "pb_001"
        assert evt.actor_id == "admin"
        assert evt.activation_id is not None


class TestRollbackCreatesChangeEvent:
    @pytest.mark.asyncio
    async def test_rollback_creates_change_event(self):
        event_store = InMemoryPolicyChangeEventStore()
        reload_mgr = _StubReloadManager()
        service, bundle_store, activation_store, *_ = _make_service(
            event_store=event_store, reload_manager=reload_mgr
        )
        req = await _setup_promotion_chain(service, bundle_store, activation_store)

        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[
            "policy.promotion.execute", "policy.rollback.execute"
        ])
        # Create first activation
        activation = await service.execute_promotion(
            promotion_id="pr_001", executed_by="admin", context=ctx, environment="prod"
        )
        # Create a second promotion + activation to have something to roll back from
        req2 = await _setup_promotion_chain_with_id(service, bundle_store, "pr_002", "pb_002", "h2")
        activation2 = await service.execute_promotion(
            promotion_id="pr_002", executed_by="admin", context=ctx, environment="prod"
        )

        # Rollback to first activation
        result = await service.rollback_environment(
            environment="prod",
            rolled_back_by="admin",
            context=ctx,
            target_activation_id=activation.activation_id,
            reason="Regressed behavior",
        )

        # Check that ACTIVATION_ROLLED_BACK event was emitted
        events = await event_store.list(environment="prod")
        rollback_events = [e for e in events if e.event_type == PolicyChangeEventType.ACTIVATION_ROLLED_BACK]
        assert len(rollback_events) == 1
        evt = rollback_events[0]
        assert evt.environment == "prod"
        assert evt.bundle_id == "pb_001"
        assert evt.actor_id == "admin"
        assert evt.reason == "Regressed behavior"

        # Also check auto_refresh_resolver was called
        assert len(reload_mgr.refresh_calls) >= 1
        assert reload_mgr.refresh_calls[-1]["environment"] == "prod"


async def _setup_promotion_chain_with_id(service, bundle_store, pr_id, bundle_id, config_hash):
    """Create a bundle and approved promotion request with specific IDs."""
    from agent_app.governance.policy_promotion import PromotionRequest, PromotionRequestStatus

    b = _StubBundle(bundle_id, config_hash)
    bundle_store.add(b)

    promotion_store = service._promotion_store
    req = PromotionRequest(
        promotion_id=pr_id,
        bundle_id=bundle_id,
        requested_by="admin",
        tenant_id="t1",
        reason="Test",
    )
    req = await promotion_store.create(req)
    await promotion_store.approve(promotion_id=pr_id, approved_by="admin")
    return req


class TestRingAssignCreatesChangeEvent:
    @pytest.mark.asyncio
    async def test_ring_assign_creates_change_event(self):
        event_store = InMemoryPolicyChangeEventStore()
        reload_mgr = _StubReloadManager()
        service, bundle_store, activation_store, *_ = _make_service(
            event_store=event_store, reload_manager=reload_mgr
        )
        req = await _setup_promotion_chain(service, bundle_store, activation_store)

        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[
            "policy.promotion.execute", "policy.ring.assign"
        ])
        activation = await service.execute_promotion(
            promotion_id="pr_001", executed_by="admin", context=ctx, environment="prod"
        )

        assignment = await service.assign_activation_to_ring(
            environment="prod",
            ring_name="canary",
            activation_id=activation.activation_id,
            assigned_by="admin",
            context=ctx,
            reason="Initial canary deployment",
        )

        # Check RING_ASSIGNED event
        events = await event_store.list(environment="prod")
        ring_events = [e for e in events if e.event_type == PolicyChangeEventType.RING_ASSIGNED]
        assert len(ring_events) == 1
        evt = ring_events[0]
        assert evt.environment == "prod"
        assert evt.ring_name == "canary"
        assert evt.activation_id == activation.activation_id
        assert evt.bundle_id == "pb_001"
        assert evt.assignment_id == assignment.assignment_id
        assert evt.actor_id == "admin"
        assert evt.reason == "Initial canary deployment"

        # Check auto_refresh_resolver was called
        assert len(reload_mgr.refresh_calls) >= 1
        last_call = reload_mgr.refresh_calls[-1]
        assert last_call["environment"] == "prod"
        assert last_call["ring_name"] == "canary"


class TestRingPromoteCreatesChangeEvent:
    @pytest.mark.asyncio
    async def test_ring_promote_creates_change_event(self):
        event_store = InMemoryPolicyChangeEventStore()
        reload_mgr = _StubReloadManager()
        service, bundle_store, activation_store, *_ = _make_service(
            event_store=event_store, reload_manager=reload_mgr
        )
        req = await _setup_promotion_chain(service, bundle_store, activation_store)

        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[
            "policy.promotion.execute", "policy.ring.assign", "policy.ring.promote"
        ])
        activation = await service.execute_promotion(
            promotion_id="pr_001", executed_by="admin", context=ctx, environment="prod"
        )

        # Assign to canary ring first
        await service.assign_activation_to_ring(
            environment="prod", ring_name="canary", activation_id=activation.activation_id,
            assigned_by="admin", context=ctx, reason="Canary deploy"
        )

        # Promote canary to stable
        stable_assignment = await service.promote_canary_to_stable(
            environment="prod", canary_ring="canary", stable_ring="stable",
            promoted_by="admin", context=ctx, reason="Canary looks good"
        )

        # Check RING_ASSIGNED event (from the inner assign call)
        events = await event_store.list(environment="prod")
        ring_assigned_events = [e for e in events if e.event_type == PolicyChangeEventType.RING_ASSIGNED]
        assert len(ring_assigned_events) == 2  # canary + stable

        # Check RING_PROMOTED event
        ring_promoted_events = [e for e in events if e.event_type == PolicyChangeEventType.RING_PROMOTED]
        assert len(ring_promoted_events) == 1
        evt = ring_promoted_events[0]
        assert evt.environment == "prod"
        assert evt.ring_name == "stable"
        assert evt.activation_id == activation.activation_id
        assert evt.actor_id == "admin"
        assert evt.reason == "Canary looks good"


class TestEnvironmentDisableCreatesChangeEvent:
    @pytest.mark.asyncio
    async def test_environment_disable_creates_change_event(self):
        event_store = InMemoryPolicyChangeEventStore()
        reload_mgr = _StubReloadManager()
        service, bundle_store, activation_store, *_ = _make_service(
            event_store=event_store, reload_manager=reload_mgr
        )

        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[
            "policy.environment.disable"
        ])
        result = await service.disable_policy_environment(
            environment="prod", disabled_by="admin", context=ctx, reason="Security incident"
        )

        # Check ENVIRONMENT_DISABLED event
        events = await event_store.list(environment="prod")
        disable_events = [e for e in events if e.event_type == PolicyChangeEventType.ENVIRONMENT_DISABLED]
        assert len(disable_events) == 1
        evt = disable_events[0]
        assert evt.environment == "prod"
        assert evt.actor_id == "admin"
        assert evt.reason == "Security incident"

        # Check auto_refresh_resolver was called
        assert len(reload_mgr.refresh_calls) >= 1
        assert reload_mgr.refresh_calls[-1]["environment"] == "prod"


class TestNoEventStoreSkipsEmission:
    @pytest.mark.asyncio
    async def test_no_event_store_skips_emission(self):
        service, bundle_store, activation_store, *_ = _make_service(event_store=None)

        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[
            "policy.environment.disable"
        ])
        # Should not raise even though no event_store is configured
        result = await service.disable_policy_environment(
            environment="staging", disabled_by="admin", context=ctx, reason="Maintenance"
        )
        assert result is not None


class TestAutoRefreshCalledIfConfigured:
    @pytest.mark.asyncio
    async def test_auto_refresh_called_if_configured(self):
        reload_mgr = _StubReloadManager()
        service, bundle_store, activation_store, *_ = _make_service(reload_manager=reload_mgr)

        ctx_enable = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[
            "policy.environment.enable"
        ])
        # First disable the environment
        ctx_disable = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[
            "policy.environment.disable"
        ])
        await service.disable_policy_environment(
            environment="prod", disabled_by="admin", context=ctx_disable, reason="Down"
        )
        # Now enable it
        result = await service.enable_policy_environment(
            environment="prod", enabled_by="admin", context=ctx_enable, reason="Restored"
        )

        # Check refresh_resolver was called for both disable and enable
        assert len(reload_mgr.refresh_calls) == 2
        assert reload_mgr.refresh_calls[0]["environment"] == "prod"
        assert reload_mgr.refresh_calls[1]["environment"] == "prod"


class TestEmissionFailureNonStrictContinues:
    @pytest.mark.asyncio
    async def test_emission_failure_non_strict_continues(self):
        failing_store = _FailingEventStore()
        # Non-strict mode (default)
        service, bundle_store, activation_store, *_ = _make_service(
            event_store=failing_store, strict=False
        )

        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[
            "policy.environment.disable"
        ])
        # The main operation should still succeed even though event emission fails
        result = await service.disable_policy_environment(
            environment="prod", disabled_by="admin", context=ctx, reason="Incident"
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_emission_failure_strict_raises(self):
        failing_store = _FailingEventStore()
        service, bundle_store, activation_store, *_ = _make_service(
            event_store=failing_store, strict=True
        )

        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[
            "policy.environment.disable"
        ])
        # In strict mode, the emission failure should propagate
        with pytest.raises(RuntimeError, match="Event store is down"):
            await service.disable_policy_environment(
                environment="prod", disabled_by="admin", context=ctx, reason="Incident"
            )


class TestProperties:
    def test_event_store_property(self):
        event_store = InMemoryPolicyChangeEventStore()
        service, *_ = _make_service(event_store=event_store)
        assert service.event_store is event_store

    def test_reload_manager_property(self):
        reload_mgr = _StubReloadManager()
        service, *_ = _make_service(reload_manager=reload_mgr)
        assert service.reload_manager is reload_mgr

    def test_event_store_default_none(self):
        service, *_ = _make_service(event_store=None)
        assert service.event_store is None

    def test_reload_manager_default_none(self):
        service, *_ = _make_service(reload_manager=None)
        assert service.reload_manager is None
