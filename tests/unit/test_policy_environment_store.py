import pytest
from datetime import datetime, timezone
from agent_app.governance.policy_environment import PolicyEnvironmentState, PolicyEnvironmentStatus
from agent_app.runtime.policy_environment_store import (
    InMemoryPolicyEnvironmentStore,
    SQLitePolicyEnvironmentStore,
    create_policy_environment_store,
)


class TestInMemoryPolicyEnvironmentStore:
    @pytest.mark.asyncio
    async def test_get_returns_default_enabled_for_unknown(self):
        store = InMemoryPolicyEnvironmentStore()
        state = await store.get("prod")
        assert state.environment == "prod"
        assert state.status == PolicyEnvironmentStatus.ENABLED
        assert state.disabled_reason is None

    @pytest.mark.asyncio
    async def test_disable_sets_status_and_metadata(self):
        store = InMemoryPolicyEnvironmentStore()
        result = await store.disable("prod", disabled_by="admin", reason="maintenance")
        assert result.status == PolicyEnvironmentStatus.DISABLED
        assert result.disabled_by == "admin"
        assert result.disabled_reason == "maintenance"
        assert result.disabled_at is not None

    @pytest.mark.asyncio
    async def test_enable_sets_status_and_metadata(self):
        store = InMemoryPolicyEnvironmentStore()
        await store.disable("prod", disabled_by="admin", reason="maintenance")
        result = await store.enable("prod", enabled_by="ops")
        assert result.status == PolicyEnvironmentStatus.ENABLED
        assert result.enabled_by == "ops"
        assert result.enabled_at is not None
        assert result.disabled_reason is None

    @pytest.mark.asyncio
    async def test_list_returns_all_states(self):
        store = InMemoryPolicyEnvironmentStore()
        await store.disable("prod", disabled_by="admin", reason="maintenance")
        await store.enable("dev", enabled_by="ops")
        states = await store.list()
        assert len(states) == 2
        envs = {s.environment for s in states}
        assert envs == {"prod", "dev"}


class TestSQLitePolicyEnvironmentStore:
    @pytest.mark.asyncio
    async def test_get_returns_default_enabled_for_unknown(self, tmp_path):
        db = tmp_path / "envs.db"
        store = SQLitePolicyEnvironmentStore(str(db))
        state = await store.get("staging")
        assert state.environment == "staging"
        assert state.status == PolicyEnvironmentStatus.ENABLED

    @pytest.mark.asyncio
    async def test_disable_and_get(self, tmp_path):
        db = tmp_path / "envs.db"
        store = SQLitePolicyEnvironmentStore(str(db))
        result = await store.disable("prod", disabled_by="admin", reason="incident")
        assert result.status == PolicyEnvironmentStatus.DISABLED
        assert result.disabled_reason == "incident"
        fetched = await store.get("prod")
        assert fetched.status == PolicyEnvironmentStatus.DISABLED

    @pytest.mark.asyncio
    async def test_enable_and_get(self, tmp_path):
        db = tmp_path / "envs.db"
        store = SQLitePolicyEnvironmentStore(str(db))
        await store.disable("prod", disabled_by="admin", reason="incident")
        result = await store.enable("prod", enabled_by="ops")
        assert result.status == PolicyEnvironmentStatus.ENABLED
        fetched = await store.get("prod")
        assert fetched.enabled_by == "ops"

    @pytest.mark.asyncio
    async def test_persists_across_instances(self, tmp_path):
        db = tmp_path / "envs.db"
        s1 = SQLitePolicyEnvironmentStore(str(db))
        await s1.disable("prod", disabled_by="admin", reason="incident")
        s2 = SQLitePolicyEnvironmentStore(str(db))
        fetched = await s2.get("prod")
        assert fetched.status == PolicyEnvironmentStatus.DISABLED
        assert fetched.disabled_by == "admin"


def test_create_policy_environment_store_memory():
    assert isinstance(create_policy_environment_store("memory"), InMemoryPolicyEnvironmentStore)


def test_create_policy_environment_store_sqlite(tmp_path):
    assert isinstance(
        create_policy_environment_store("sqlite", str(tmp_path / "env.db")),
        SQLitePolicyEnvironmentStore,
    )


def test_create_policy_environment_store_unknown():
    with pytest.raises(ValueError, match="Unknown environment store type"):
        create_policy_environment_store("redis")
