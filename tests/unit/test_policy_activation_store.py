import pytest
from datetime import datetime, timezone
from agent_app.governance.policy_activation import PolicyActivation, PolicyActivationStatus
from agent_app.runtime.policy_activation_store import InMemoryPolicyActivationStore, SQLitePolicyActivationStore, create_policy_activation_store


class TestInMemoryPolicyActivationStore:
    @pytest.mark.asyncio
    async def test_activate_first_bundle(self):
        store = InMemoryPolicyActivationStore()
        act = PolicyActivation(activation_id="pa_1", environment="prod", bundle_id="pb_1", config_hash="h1", activated_by="admin")
        result = await store.activate(act)
        assert result.activation_id == "pa_1"
        assert result.status == PolicyActivationStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_activate_supersedes_previous(self):
        store = InMemoryPolicyActivationStore()
        await store.activate(PolicyActivation(activation_id="pa_1", environment="prod", bundle_id="pb_1", config_hash="h1", activated_by="admin"))
        await store.activate(PolicyActivation(activation_id="pa_2", environment="prod", bundle_id="pb_2", config_hash="h2", activated_by="admin"))
        active = await store.get_active("prod")
        assert active.activation_id == "pa_2"
        first = await store.get("pa_1")
        assert first.status == PolicyActivationStatus.SUPERSEDED
        assert first.superseded_at is not None
        assert first.superseded_by_activation_id == "pa_2"

    @pytest.mark.asyncio
    async def test_get_active_returns_none_when_none(self):
        assert await InMemoryPolicyActivationStore().get_active("prod") is None

    @pytest.mark.asyncio
    async def test_list_by_environment(self):
        store = InMemoryPolicyActivationStore()
        await store.activate(PolicyActivation(activation_id="pa_dev", environment="dev", bundle_id="pb_1", config_hash="h1", activated_by="admin"))
        await store.activate(PolicyActivation(activation_id="pa_prod", environment="prod", bundle_id="pb_1", config_hash="h1", activated_by="admin"))
        devs = await store.list(environment="dev")
        assert len(devs) == 1 and devs[0].activation_id == "pa_dev"
        assert len(await store.list()) == 2

    @pytest.mark.asyncio
    async def test_mark_rolled_back(self):
        store = InMemoryPolicyActivationStore()
        await store.activate(PolicyActivation(activation_id="pa_1", environment="prod", bundle_id="pb_1", config_hash="h1", activated_by="admin"))
        result = await store.mark_rolled_back("pa_1", rolled_back_by="ops")
        assert result.status == PolicyActivationStatus.ROLLED_BACK
        assert result.superseded_at is not None


class TestSQLitePolicyActivationStore:
    @pytest.mark.asyncio
    async def test_activate_and_get_active(self, tmp_path):
        db = tmp_path / "activations.db"
        store = SQLitePolicyActivationStore(str(db))
        await store.activate(PolicyActivation(activation_id="pa_1", environment="prod", bundle_id="pb_1", config_hash="h1", activated_by="admin"))
        active = await store.get_active("prod")
        assert active.activation_id == "pa_1"

    @pytest.mark.asyncio
    async def test_supersedes_across_instances(self, tmp_path):
        db = tmp_path / "activations.db"
        s1 = SQLitePolicyActivationStore(str(db))
        await s1.activate(PolicyActivation(activation_id="pa_1", environment="prod", bundle_id="pb_1", config_hash="h1", activated_by="admin"))
        s2 = SQLitePolicyActivationStore(str(db))
        await s2.activate(PolicyActivation(activation_id="pa_2", environment="prod", bundle_id="pb_2", config_hash="h2", activated_by="admin"))
        assert (await s2.get_active("prod")).activation_id == "pa_2"

    @pytest.mark.asyncio
    async def test_list_by_environment(self, tmp_path):
        db = tmp_path / "activations.db"
        store = SQLitePolicyActivationStore(str(db))
        await store.activate(PolicyActivation(activation_id="pa_dev", environment="dev", bundle_id="pb_1", config_hash="h1", activated_by="admin"))
        await store.activate(PolicyActivation(activation_id="pa_prod", environment="prod", bundle_id="pb_1", config_hash="h1", activated_by="admin"))
        devs = await store.list(environment="dev")
        assert len(devs) == 1 and devs[0].environment == "dev"

    @pytest.mark.asyncio
    async def test_mark_rolled_back(self, tmp_path):
        db = tmp_path / "activations.db"
        store = SQLitePolicyActivationStore(str(db))
        await store.activate(PolicyActivation(activation_id="pa_1", environment="prod", bundle_id="pb_1", config_hash="h1", activated_by="admin"))
        result = await store.mark_rolled_back("pa_1", rolled_back_by="ops")
        assert result.status == PolicyActivationStatus.ROLLED_BACK


def test_create_policy_activation_store_memory():
    assert isinstance(create_policy_activation_store("memory"), InMemoryPolicyActivationStore)


def test_create_policy_activation_store_sqlite(tmp_path):
    assert isinstance(create_policy_activation_store("sqlite", str(tmp_path / "a.db")), SQLitePolicyActivationStore)


def test_create_policy_activation_store_unknown():
    with pytest.raises(ValueError, match="Unknown activation store type"):
        create_policy_activation_store("redis")
