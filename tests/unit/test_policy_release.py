"""Tests for PolicyReleaseService."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import pytest

from agent_app.runtime.policy_release import PolicyReleaseService, PolicyReleasePermissionError
from agent_app.governance.policy_bundle import (
    InMemoryPolicyBundleStore,
    PolicyBundle,
    PolicyBundleStatus,
    SQLitePolicyBundleStore,
    compute_config_hash,
)
from agent_app.runtime.policy_gate_store import (
    InMemoryPolicyGateStore,
    SQLitePolicyGateStore,
    create_gate_store,
)
from agent_app.governance.policy_gate import PolicyGateEvaluator, PolicyGateRule
from agent_app.governance.policy_replay import (
    PolicyReplayResult,
    PolicyReplayRun,
    PolicyReplayStatus,
    PolicyReplayDecisionChange,
)
from agent_app.core.context import RunContext
from agent_app.governance.policy_rbac import PolicyReleasePermission, PolicyReleasePermissionChecker
from agent_app.runtime.promotion_store import InMemoryPromotionRequestStore, PromotionRequest, PromotionRequestStatus
from agent_app.governance.audit import InMemoryAuditLogger


def _make_bundle(
    bundle_id: str = "pb_1",
    name: str = "test-bundle",
    version: str = "1.0.0",
    config_hash: str = "",
) -> PolicyBundle:
    """Create a test PolicyBundle."""
    return PolicyBundle(
        bundle_id=bundle_id,
        name=name,
        version=version,
        config_hash=config_hash or compute_config_hash("test policy content"),
        created_at=datetime.now(timezone.utc),
    )


def _make_replay_result(
    replay_id: str = "replay_1",
    total: int = 100,
    changed: int = 5,
    failed: int = 0,
) -> PolicyReplayResult:
    """Create a test PolicyReplayResult."""
    run = PolicyReplayRun(
        replay_id=replay_id,
        status=PolicyReplayStatus.COMPLETED,
        source_decision_count=total,
        changed_count=changed,
        unchanged_count=total - changed - failed,
        failed_count=failed,
        created_at=datetime.now(timezone.utc),
    )
    changes = []
    for i in range(total):
        action = "error" if i < failed else ("deny" if i < changed else "allow")
        changes.append(PolicyReplayDecisionChange(
            decision_id=f"dec_{i}",
            original_action="allow",
            replayed_action=action,
            changed=(action != "allow"),
        ))
    return PolicyReplayResult(replay=run, changes=changes)


def _make_service(
    bundle_store=None,
    gate_store=None,
    evaluator=None,
):
    """Create a PolicyReleaseService with default in-memory stores."""
    if bundle_store is None:
        bundle_store = InMemoryPolicyBundleStore()
    if gate_store is None:
        gate_store = InMemoryPolicyGateStore()
    if evaluator is None:
        evaluator = PolicyGateEvaluator(rules=[
            PolicyGateRule(name="safe_default", max_changed_ratio=0.10, max_failed_replays=0),
        ])

    # Mock replay runner that returns a fixed result
    class MockReplayRunner:
        async def run_replay(self, **kwargs):
            return _make_replay_result()

    class MockReplayStore:
        async def save(self, result):
            return result

    return PolicyReleaseService(
        bundle_store=bundle_store,
        replay_runner=MockReplayRunner(),
        replay_store=MockReplayStore(),
        gate_evaluator=evaluator,
        gate_store=gate_store,
    )


def _make_mock_replay_runner(total=100, changed=5, failed=0):
    class MockRunner:
        async def run_replay(self, **kwargs):
            return _make_replay_result(total=total, changed=changed, failed=failed)
    return MockRunner()


def _make_mock_replay_store():
    class MockStore:
        async def save(self, result):
            return result
    return MockStore()


def _make_default_evaluator():
    return PolicyGateEvaluator(rules=[
        PolicyGateRule(name="safe_default", max_changed_ratio=0.10, max_failed_replays=0),
    ])


def _make_context(permissions: list[str], user_id: str = "alice", tenant_id: str = "tenant_1") -> RunContext:
    return RunContext(run_id="run_1", user_id=user_id, tenant_id=tenant_id, permissions=permissions)


class TestPolicyReleaseService:
    """Tests for PolicyReleaseService."""

    async def test_create_bundle_computes_config_hash(self):
        """create_bundle computes config hash from content."""
        service = _make_service()
        bundle = await service.create_bundle(
            name="test-bundle",
            version="1.0.0",
            config_path="nonexistent_config.yaml",
            description="Test bundle",
            created_by="admin",
        )
        assert bundle.bundle_id.startswith("pb_")
        # When file doesn't exist, hash is computed from the path string
        expected_hash = compute_config_hash("nonexistent_config.yaml")
        assert bundle.config_hash == expected_hash
        assert bundle.description == "Test bundle"
        assert bundle.created_by == "admin"

    async def test_create_bundle_default_status_draft(self):
        """New bundles start as DRAFT."""
        service = _make_service()
        bundle = await service.create_bundle(
            name="test",
            version="1.0.0",
            config_path="test.yaml",
        )
        assert bundle.status == PolicyBundleStatus.DRAFT

    async def test_run_gate_stores_result(self):
        """run_gate evaluates and stores the gate result."""
        service = _make_service()
        bundle = await service.create_bundle(
            name="test",
            version="1.0.0",
            config_path="test.yaml",
        )

        result = await service.run_gate(
            bundle_id=bundle.bundle_id,
            created_by="admin",
        )
        assert result.bundle_id == bundle.bundle_id
        assert result.replay_id == "replay_1"
        assert result.passed is True
        assert result.status == "passed"

    async def test_run_gate_passes_replay_filters(self):
        """run_gate passes filter parameters to the replay runner."""
        service = _make_service()
        bundle = await service.create_bundle(
            name="test",
            version="1.0.0",
            config_path="test.yaml",
        )

        result = await service.run_gate(
            bundle_id=bundle.bundle_id,
            limit=50,
            tenant_id="tenant_1",
            tool_name="send_email",
        )
        # Result should be stored
        assert result.bundle_id == bundle.bundle_id

    async def test_promote_requires_passing_gate(self):
        """promote succeeds when the latest gate passed."""
        service = _make_service()
        bundle = await service.create_bundle(
            name="test",
            version="1.0.0",
            config_path="test.yaml",
        )
        # Run gate (passes with default low thresholds)
        await service.run_gate(bundle_id=bundle.bundle_id)

        promoted = await service.promote(bundle_id=bundle.bundle_id, promoted_by="admin")
        assert promoted.status == PolicyBundleStatus.ACTIVE
        assert promoted.activated_at is not None

    async def test_promote_fails_on_failed_gate(self):
        """promote fails when the latest gate result is FAILED."""
        bundle_store = InMemoryPolicyBundleStore()
        gate_store = InMemoryPolicyGateStore()

        # Evaluator with strict thresholds that will fail
        strict_evaluator = PolicyGateEvaluator(rules=[
            PolicyGateRule(name="strict", max_changed_ratio=0.001, max_failed_replays=0),
        ])

        service = _make_service(
            bundle_store=bundle_store,
            gate_store=gate_store,
            evaluator=strict_evaluator,
        )

        bundle = await service.create_bundle(
            name="test",
            version="1.0.0",
            config_path="test.yaml",
        )

        # Run gate (will fail because changed ratio > 0.001)
        await service.run_gate(bundle_id=bundle.bundle_id)

        with pytest.raises(ValueError, match="gate"):
            await service.promote(bundle_id=bundle.bundle_id)

    async def test_promote_archives_previous_active(self):
        """promote archives any previously active bundle."""
        bundle_store = InMemoryPolicyBundleStore()
        gate_store = InMemoryPolicyGateStore()
        service = _make_service(bundle_store=bundle_store, gate_store=gate_store)

        # Create and activate b1
        b1 = await service.create_bundle(name="b1", version="1.0.0", config_path="test.yaml")
        await service.run_gate(bundle_id=b1.bundle_id)
        await service.promote(bundle_id=b1.bundle_id)

        # Create and promote b2
        b2 = await service.create_bundle(name="b2", version="2.0.0", config_path="test.yaml")
        await service.run_gate(bundle_id=b2.bundle_id)
        await service.promote(bundle_id=b2.bundle_id)

        b1_fetched = await bundle_store.get(b1.bundle_id)
        assert b1_fetched.status == PolicyBundleStatus.ARCHIVED

        b2_fetched = await bundle_store.get(b2.bundle_id)
        assert b2_fetched.status == PolicyBundleStatus.ACTIVE

    async def test_rollback_activates_target_bundle(self):
        """rollback re-activates the target bundle."""
        bundle_store = InMemoryPolicyBundleStore()
        gate_store = InMemoryPolicyGateStore()
        service = _make_service(bundle_store=bundle_store, gate_store=gate_store)

        b1 = await service.create_bundle(name="b1", version="1.0.0", config_path="test.yaml")
        await service.run_gate(bundle_id=b1.bundle_id)
        await service.promote(bundle_id=b1.bundle_id)

        b2 = await service.create_bundle(name="b2", version="2.0.0", config_path="test.yaml")
        await service.run_gate(bundle_id=b2.bundle_id)
        await service.promote(bundle_id=b2.bundle_id)

        # Rollback to b1
        rolled_back = await service.rollback(
            target_bundle_id=b1.bundle_id,
            rolled_back_by="admin",
        )
        assert rolled_back.status == PolicyBundleStatus.ACTIVE
        assert rolled_back.activated_at is not None

        # b2 should be archived
        b2_fetched = await bundle_store.get(b2.bundle_id)
        assert b2_fetched.status == PolicyBundleStatus.ARCHIVED

    async def test_promote_missing_bundle_raises(self):
        """promote raises KeyError for missing bundle."""
        service = _make_service()
        with pytest.raises(KeyError, match="not found"):
            await service.promote(bundle_id="pb_nonexistent")

    async def test_rollback_missing_bundle_raises(self):
        """rollback raises KeyError for missing bundle."""
        service = _make_service()
        with pytest.raises(KeyError, match="not found"):
            await service.rollback(target_bundle_id="pb_nonexistent")


class TestPolicyReleaseServiceSQLite:
    """SQLite persistence tests for PolicyReleaseService."""

    async def test_full_lifecycle_sqlite(self, tmp_path):
        """Full bundle lifecycle with SQLite stores."""
        db_path_bundles = str(tmp_path / "bundles.db")
        db_path_gates = str(tmp_path / "gates.db")

        bundle_store = SQLitePolicyBundleStore(db_path_bundles)
        gate_store = SQLitePolicyGateStore(db_path_gates)
        evaluator = PolicyGateEvaluator(rules=[
            PolicyGateRule(name="safe_default", max_changed_ratio=0.10),
        ])

        service = PolicyReleaseService(
            bundle_store=bundle_store,
            replay_runner=_MockReplayRunner(),
            replay_store=None,
            gate_evaluator=evaluator,
            gate_store=gate_store,
        )

        # Create bundle
        bundle = await service.create_bundle(
            name="test",
            version="1.0.0",
            config_path="test.yaml",
        )
        assert bundle.bundle_id.startswith("pb_")

        # Run gate
        result = await service.run_gate(bundle_id=bundle.bundle_id)
        assert result.bundle_id == bundle.bundle_id

        # Promote
        promoted = await service.promote(bundle_id=bundle.bundle_id)
        assert promoted.status == PolicyBundleStatus.ACTIVE

        bundle_store.close()
        gate_store.close()


class _MockReplayRunner:
    """Mock replay runner for integration tests."""

    async def run_replay(self, **kwargs):
        return _make_replay_result()


class TestPolicyReleaseServiceRBAC:
    """Tests for RBAC and promotion lifecycle."""

    def _make_service(self, promotion_store=None, permission_checker=None, audit_logger=None):
        if promotion_store is None:
            promotion_store = InMemoryPromotionRequestStore()
        if permission_checker is None:
            permission_checker = PolicyReleasePermissionChecker()
        return PolicyReleaseService(
            bundle_store=InMemoryPolicyBundleStore(),
            replay_runner=_make_mock_replay_runner(),
            replay_store=_make_mock_replay_store(),
            gate_evaluator=_make_default_evaluator(),
            gate_store=InMemoryPolicyGateStore(),
            promotion_store=promotion_store,
            permission_checker=permission_checker,
            audit_logger=audit_logger,
        )

    async def test_request_promotion_requires_permission(self):
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx = _make_context(permissions=[])
        with pytest.raises(PolicyReleasePermissionError, match="policy.promotion.request"):
            await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx)

    async def test_request_promotion_success(self):
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx = _make_context(permissions=["policy.promotion.request"])
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx, reason="release")
        assert req.status == PromotionRequestStatus.PENDING
        assert req.bundle_id == bundle.bundle_id
        assert req.reason == "release"
        assert req.promotion_id.startswith("pr_")

    async def test_approve_promotion_requires_permission(self):
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx_req = _make_context(permissions=["policy.promotion.request"])
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx_req)
        ctx_approve = _make_context(permissions=[])
        with pytest.raises(PolicyReleasePermissionError, match="policy.promotion.approve"):
            await service.approve_promotion(promotion_id=req.promotion_id, approved_by="reviewer", context=ctx_approve)

    async def test_approve_promotion_success(self):
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx = _make_context(permissions=["policy.promotion.request", "policy.promotion.approve"])
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx)
        updated = await service.approve_promotion(promotion_id=req.promotion_id, approved_by="reviewer", context=ctx, reason="ok")
        assert updated.status == PromotionRequestStatus.APPROVED
        assert updated.resolved_by == "reviewer"

    async def test_reject_promotion_requires_permission(self):
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx_req = _make_context(permissions=["policy.promotion.request"])
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx_req)
        ctx_reject = _make_context(permissions=[])
        with pytest.raises(PolicyReleasePermissionError, match="policy.promotion.reject"):
            await service.reject_promotion(promotion_id=req.promotion_id, rejected_by="reviewer", context=ctx_reject)

    async def test_reject_promotion_success(self):
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx = _make_context(permissions=["policy.promotion.request", "policy.promotion.reject"])
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx)
        updated = await service.reject_promotion(promotion_id=req.promotion_id, rejected_by="reviewer", context=ctx, reason="too risky")
        assert updated.status == PromotionRequestStatus.REJECTED

    async def test_execute_pending_fails(self):
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx = _make_context(permissions=["policy.promotion.request", "policy.promotion.execute"])
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx)
        with pytest.raises(ValueError, match="must be approved"):
            await service.execute_promotion(promotion_id=req.promotion_id, executed_by="rm", context=ctx)

    async def test_execute_rejected_fails(self):
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx = _make_context(permissions=["policy.promotion.request", "policy.promotion.reject", "policy.promotion.execute"])
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx)
        await service.reject_promotion(promotion_id=req.promotion_id, rejected_by="reviewer", context=ctx)
        with pytest.raises(ValueError, match="must be approved"):
            await service.execute_promotion(promotion_id=req.promotion_id, executed_by="rm", context=ctx)

    async def test_execute_approved_activates_bundle(self):
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        await service.run_gate(bundle_id=bundle.bundle_id, created_by="admin")
        ctx = _make_context(permissions=["policy.promotion.request", "policy.promotion.approve", "policy.promotion.execute"])
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx)
        await service.approve_promotion(promotion_id=req.promotion_id, approved_by="reviewer", context=ctx)
        result = await service.execute_promotion(promotion_id=req.promotion_id, executed_by="rm", context=ctx)
        assert result.status == PolicyBundleStatus.ACTIVE

    async def test_execute_requires_permission(self):
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx = _make_context(permissions=["policy.promotion.request", "policy.promotion.approve"])
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx)
        await service.approve_promotion(promotion_id=req.promotion_id, approved_by="reviewer", context=ctx)
        ctx_exec = _make_context(permissions=[])
        with pytest.raises(PolicyReleasePermissionError, match="policy.promotion.execute"):
            await service.execute_promotion(promotion_id=req.promotion_id, executed_by="rm", context=ctx_exec)

    async def test_bypass_gate_requires_config_and_permission(self):
        bundle_store = InMemoryPolicyBundleStore()
        gate_store = InMemoryPolicyGateStore()
        promo_store = InMemoryPromotionRequestStore()
        checker = PolicyReleasePermissionChecker()
        evaluator = PolicyGateEvaluator(rules=[PolicyGateRule(name="always_fail", max_changed_ratio=0.0)])
        service = PolicyReleaseService(
            bundle_store=bundle_store,
            replay_runner=_make_mock_replay_runner(changed=1, total=10),
            replay_store=_make_mock_replay_store(),
            gate_evaluator=evaluator,
            gate_store=gate_store,
            promotion_store=promo_store,
            permission_checker=checker,
            allow_gate_bypass=True,
        )
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        await service.run_gate(bundle_id=bundle.bundle_id, created_by="admin")
        ctx = _make_context(permissions=["policy.promotion.request", "policy.promotion.approve", "policy.promotion.execute", "policy.gate.bypass"])
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx)
        await service.approve_promotion(promotion_id=req.promotion_id, approved_by="reviewer", context=ctx)
        # Without bypass reason should fail
        with pytest.raises(ValueError, match="bypass_reason"):
            await service.execute_promotion(promotion_id=req.promotion_id, executed_by="rm", context=ctx, bypass_gate=True, bypass_reason=None)
        # With bypass reason should succeed
        result = await service.execute_promotion(promotion_id=req.promotion_id, executed_by="rm", context=ctx, bypass_gate=True, bypass_reason="Emergency release")
        assert result.status == PolicyBundleStatus.ACTIVE

    async def test_audit_events_written(self):
        audit = InMemoryAuditLogger()
        service = PolicyReleaseService(
            bundle_store=InMemoryPolicyBundleStore(),
            replay_runner=_make_mock_replay_runner(),
            replay_store=_make_mock_replay_store(),
            gate_evaluator=_make_default_evaluator(),
            gate_store=InMemoryPolicyGateStore(),
            promotion_store=InMemoryPromotionRequestStore(),
            permission_checker=PolicyReleasePermissionChecker(),
            audit_logger=audit,
        )
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx = _make_context(permissions=["policy.promotion.request", "policy.promotion.approve", "policy.promotion.execute"])
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx, reason="release")
        await service.approve_promotion(promotion_id=req.promotion_id, approved_by="reviewer", context=ctx)
        await service.run_gate(bundle_id=bundle.bundle_id, created_by="admin")
        await service.execute_promotion(promotion_id=req.promotion_id, executed_by="rm", context=ctx)
        event_types = [e.event_type for e in audit.list_events()]
        assert "policy.promotion.requested" in event_types
        assert "policy.promotion.approved" in event_types
        assert "policy.promotion.executed" in event_types
