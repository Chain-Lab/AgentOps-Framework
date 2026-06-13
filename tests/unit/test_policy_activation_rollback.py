"""Tests for Phase 32 Task 3 -- activation rollback fields and store methods."""

import pytest
from agent_app.governance.policy_activation import PolicyActivation, PolicyActivationStatus
from agent_app.runtime.policy_activation_store import InMemoryPolicyActivationStore, SQLitePolicyActivationStore


# ---------------------------------------------------------------------------
# 1. Rollback fields default to None
# ---------------------------------------------------------------------------

def test_rollback_fields_default_none():
    a = PolicyActivation(
        activation_id="pa_1", environment="prod", bundle_id="pb_1",
        config_hash="h1", activated_by="admin",
    )
    assert a.rollback_of_activation_id is None
    assert a.rollback_target_activation_id is None


# ---------------------------------------------------------------------------
# 2. get_previous_activation returns previous (superseded) activation
# ---------------------------------------------------------------------------

class TestGetPreviousActivation:
    @pytest.mark.asyncio
    async def test_returns_previous_superseded(self):
        store = InMemoryPolicyActivationStore()
        await store.activate(PolicyActivation(
            activation_id="pa_1", environment="prod", bundle_id="pb_1",
            config_hash="h1", activated_by="admin",
        ))
        await store.activate(PolicyActivation(
            activation_id="pa_2", environment="prod", bundle_id="pb_2",
            config_hash="h2", activated_by="admin",
        ))
        prev = await store.get_previous_activation("prod")
        assert prev is not None
        assert prev.activation_id == "pa_1"
        assert prev.status == PolicyActivationStatus.SUPERSEDED

    @pytest.mark.asyncio
    async def test_returns_none_when_only_one_activation(self):
        store = InMemoryPolicyActivationStore()
        await store.activate(PolicyActivation(
            activation_id="pa_1", environment="prod", bundle_id="pb_1",
            config_hash="h1", activated_by="admin",
        ))
        prev = await store.get_previous_activation("prod")
        assert prev is None

    @pytest.mark.asyncio
    async def test_respects_before_activation_id(self):
        store = InMemoryPolicyActivationStore()
        await store.activate(PolicyActivation(
            activation_id="pa_1", environment="prod", bundle_id="pb_1",
            config_hash="h1", activated_by="admin",
        ))
        await store.activate(PolicyActivation(
            activation_id="pa_2", environment="prod", bundle_id="pb_2",
            config_hash="h2", activated_by="admin",
        ))
        await store.activate(PolicyActivation(
            activation_id="pa_3", environment="prod", bundle_id="pb_3",
            config_hash="h3", activated_by="admin",
        ))
        # With before_activation_id="pa_3", should return pa_2 (most recent superseded before pa_3)
        prev = await store.get_previous_activation("prod", before_activation_id="pa_3")
        assert prev is not None
        assert prev.activation_id == "pa_2"


# ---------------------------------------------------------------------------
# 3-8. rollback_to_activation tests
# ---------------------------------------------------------------------------

class TestRollbackToActivation:
    @pytest.mark.asyncio
    async def test_rollback_creates_new_activation(self):
        store = InMemoryPolicyActivationStore()
        await store.activate(PolicyActivation(
            activation_id="pa_1", environment="prod", bundle_id="pb_1",
            config_hash="h1", activated_by="admin",
        ))
        await store.activate(PolicyActivation(
            activation_id="pa_2", environment="prod", bundle_id="pb_2",
            config_hash="h2", activated_by="admin",
        ))
        result = await store.rollback_to_activation("prod", "pa_1", rolled_back_by="ops")
        assert result.status == PolicyActivationStatus.ACTIVE
        assert result.bundle_id == "pb_1"
        assert result.config_hash == "h1"
        assert result.activated_by == "ops"
        assert result.activation_id.startswith("pa_")
        assert result.activation_id not in ("pa_1", "pa_2")

    @pytest.mark.asyncio
    async def test_rollback_supersedes_current(self):
        store = InMemoryPolicyActivationStore()
        await store.activate(PolicyActivation(
            activation_id="pa_1", environment="prod", bundle_id="pb_1",
            config_hash="h1", activated_by="admin",
        ))
        await store.activate(PolicyActivation(
            activation_id="pa_2", environment="prod", bundle_id="pb_2",
            config_hash="h2", activated_by="admin",
        ))
        new_act = await store.rollback_to_activation("prod", "pa_1", rolled_back_by="ops")
        # The previously active (pa_2) should now be SUPERSEDED
        old_active = await store.get("pa_2")
        assert old_active.status == PolicyActivationStatus.SUPERSEDED
        assert old_active.superseded_by_activation_id == new_act.activation_id

    @pytest.mark.asyncio
    async def test_rollback_wrong_environment_fails(self):
        store = InMemoryPolicyActivationStore()
        await store.activate(PolicyActivation(
            activation_id="pa_1", environment="dev", bundle_id="pb_1",
            config_hash="h1", activated_by="admin",
        ))
        with pytest.raises(ValueError, match="environment"):
            await store.rollback_to_activation("prod", "pa_1", rolled_back_by="ops")

    @pytest.mark.asyncio
    async def test_rollback_nonexistent_activation_fails(self):
        store = InMemoryPolicyActivationStore()
        with pytest.raises(KeyError, match="pa_nonexistent"):
            await store.rollback_to_activation("prod", "pa_nonexistent", rolled_back_by="ops")

    @pytest.mark.asyncio
    async def test_rollback_sets_metadata(self):
        store = InMemoryPolicyActivationStore()
        await store.activate(PolicyActivation(
            activation_id="pa_1", environment="prod", bundle_id="pb_1",
            config_hash="h1", activated_by="admin",
        ))
        await store.activate(PolicyActivation(
            activation_id="pa_2", environment="prod", bundle_id="pb_2",
            config_hash="h2", activated_by="admin",
        ))
        result = await store.rollback_to_activation("prod", "pa_1", rolled_back_by="ops", reason="Bad deploy")
        assert result.rollback_of_activation_id == "pa_2"
        assert result.rollback_target_activation_id == "pa_1"
        assert result.reason == "Bad deploy"

    @pytest.mark.asyncio
    async def test_rollback_default_reason(self):
        store = InMemoryPolicyActivationStore()
        await store.activate(PolicyActivation(
            activation_id="pa_1", environment="prod", bundle_id="pb_1",
            config_hash="h1", activated_by="admin",
        ))
        await store.activate(PolicyActivation(
            activation_id="pa_2", environment="prod", bundle_id="pb_2",
            config_hash="h2", activated_by="admin",
        ))
        result = await store.rollback_to_activation("prod", "pa_1", rolled_back_by="ops")
        assert result.reason == "Rollback"


# ---------------------------------------------------------------------------
# SQLite rollback tests
# ---------------------------------------------------------------------------

class TestSQLiteRollback:
    @pytest.mark.asyncio
    async def test_rollback_creates_new_activation(self, tmp_path):
        db = tmp_path / "activations.db"
        store = SQLitePolicyActivationStore(str(db))
        await store.activate(PolicyActivation(
            activation_id="pa_1", environment="prod", bundle_id="pb_1",
            config_hash="h1", activated_by="admin",
        ))
        await store.activate(PolicyActivation(
            activation_id="pa_2", environment="prod", bundle_id="pb_2",
            config_hash="h2", activated_by="admin",
        ))
        result = await store.rollback_to_activation("prod", "pa_1", rolled_back_by="ops")
        assert result.status == PolicyActivationStatus.ACTIVE
        assert result.bundle_id == "pb_1"
        assert result.rollback_of_activation_id == "pa_2"
        assert result.rollback_target_activation_id == "pa_1"

    @pytest.mark.asyncio
    async def test_get_previous_activation_sqlite(self, tmp_path):
        db = tmp_path / "activations.db"
        store = SQLitePolicyActivationStore(str(db))
        await store.activate(PolicyActivation(
            activation_id="pa_1", environment="prod", bundle_id="pb_1",
            config_hash="h1", activated_by="admin",
        ))
        await store.activate(PolicyActivation(
            activation_id="pa_2", environment="prod", bundle_id="pb_2",
            config_hash="h2", activated_by="admin",
        ))
        prev = await store.get_previous_activation("prod")
        assert prev is not None
        assert prev.activation_id == "pa_1"
