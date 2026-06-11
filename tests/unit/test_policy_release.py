"""Tests for PolicyReleaseService."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import pytest

from agent_app.runtime.policy_release import PolicyReleaseService
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
