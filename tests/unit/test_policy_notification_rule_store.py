"""Tests for PolicyNotificationRuleStore -- Protocol, InMemory, SQLite, factory."""
from __future__ import annotations

import os
import tempfile

import pytest

from agent_app.governance.policy_notification import (
    PolicyNotificationRule,
    PolicyNotificationRuleStatus,
    PolicyNotificationSeverity,
)
from agent_app.runtime.policy_notification_rule_store import (
    InMemoryPolicyNotificationRuleStore,
    PolicyNotificationRuleStore,
    SQLitePolicyNotificationRuleStore,
    create_policy_notification_rule_store,
)


def _make_rule(
    rule_id: str = "pnr_001",
    name: str = "test_rule",
    event_types: list[str] | None = None,
    status: PolicyNotificationRuleStatus | None = None,
) -> PolicyNotificationRule:
    return PolicyNotificationRule(
        rule_id=rule_id,
        name=name,
        event_types=event_types or ["test.event"],
        severity=PolicyNotificationSeverity.INFO,
        status=status or PolicyNotificationRuleStatus.ENABLED,
    )


# -- InMemory tests --


class TestInMemoryPolicyNotificationRuleStore:
    @pytest.mark.asyncio
    async def test_create_and_get(self):
        store = InMemoryPolicyNotificationRuleStore()
        rule = _make_rule(rule_id="pnr_001")
        created = await store.create(rule)
        assert created.rule_id == "pnr_001"
        fetched = await store.get("pnr_001")
        assert fetched is not None
        assert fetched.rule_id == "pnr_001"
        assert fetched.name == "test_rule"

    @pytest.mark.asyncio
    async def test_list_all(self):
        store = InMemoryPolicyNotificationRuleStore()
        await store.create(_make_rule(rule_id="pnr_l1", name="rule1"))
        await store.create(_make_rule(rule_id="pnr_l2", name="rule2"))
        results = await store.list()
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_list_by_status(self):
        store = InMemoryPolicyNotificationRuleStore()
        await store.create(_make_rule(rule_id="pnr_s1", status=PolicyNotificationRuleStatus.ENABLED))
        await store.create(_make_rule(rule_id="pnr_s2", status=PolicyNotificationRuleStatus.DISABLED))
        await store.create(_make_rule(rule_id="pnr_s3", status=PolicyNotificationRuleStatus.ENABLED))
        enabled = await store.list(status=PolicyNotificationRuleStatus.ENABLED)
        assert len(enabled) == 2
        assert all(r.status == PolicyNotificationRuleStatus.ENABLED for r in enabled)
        disabled = await store.list(status=PolicyNotificationRuleStatus.DISABLED)
        assert len(disabled) == 1

    @pytest.mark.asyncio
    async def test_enable(self):
        store = InMemoryPolicyNotificationRuleStore()
        rule = _make_rule(rule_id="pnr_en1", status=PolicyNotificationRuleStatus.DISABLED)
        await store.create(rule)
        enabled = await store.enable("pnr_en1")
        assert enabled.status == PolicyNotificationRuleStatus.ENABLED
        fetched = await store.get("pnr_en1")
        assert fetched is not None
        assert fetched.status == PolicyNotificationRuleStatus.ENABLED

    @pytest.mark.asyncio
    async def test_disable(self):
        store = InMemoryPolicyNotificationRuleStore()
        rule = _make_rule(rule_id="pnr_dis1", status=PolicyNotificationRuleStatus.ENABLED)
        await store.create(rule)
        disabled = await store.disable("pnr_dis1")
        assert disabled.status == PolicyNotificationRuleStatus.DISABLED
        fetched = await store.get("pnr_dis1")
        assert fetched is not None
        assert fetched.status == PolicyNotificationRuleStatus.DISABLED


# -- SQLite tests --


class TestSQLitePolicyNotificationRuleStore:
    @pytest.mark.asyncio
    async def test_create_and_get(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            store = SQLitePolicyNotificationRuleStore(db_path=db_path)
            rule = _make_rule(rule_id="pnr_sql_001")
            created = await store.create(rule)
            assert created.rule_id == "pnr_sql_001"
            fetched = await store.get("pnr_sql_001")
            assert fetched is not None
            assert fetched.rule_id == "pnr_sql_001"
            assert fetched.name == "test_rule"
            store.close()
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_persists_across_instances(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            store = SQLitePolicyNotificationRuleStore(db_path=db_path)
            rule = _make_rule(rule_id="pnr_persist_001", name="persist_rule")
            await store.create(rule)
            store.close()
            # Read with a new instance
            store2 = SQLitePolicyNotificationRuleStore(db_path=db_path)
            fetched = await store2.get("pnr_persist_001")
            assert fetched is not None
            assert fetched.rule_id == "pnr_persist_001"
            assert fetched.name == "persist_rule"
            assert fetched.status == PolicyNotificationRuleStatus.ENABLED
            store2.close()
        finally:
            os.unlink(db_path)


# -- Factory tests --


class TestCreatePolicyNotificationRuleStore:
    def test_memory(self):
        store = create_policy_notification_rule_store("memory")
        assert isinstance(store, InMemoryPolicyNotificationRuleStore)

    def test_sqlite(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            store = create_policy_notification_rule_store("sqlite", db_path)
            assert isinstance(store, SQLitePolicyNotificationRuleStore)
            store.close()
        finally:
            os.unlink(db_path)
