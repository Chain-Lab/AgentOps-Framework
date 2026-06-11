"""Tests for policy replay store."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from agent_app.runtime.policy_replay_store import InMemoryPolicyReplayStore
from agent_app.governance.policy_replay import (
    PolicyReplayDecisionChange,
    PolicyReplayResult,
    PolicyReplayRun,
    PolicyReplayStatus,
)


def _make_result(replay_id: str = "replay_1", count: int = 3) -> PolicyReplayResult:
    run = PolicyReplayRun(
        replay_id=replay_id,
        status=PolicyReplayStatus.COMPLETED,
        source_decision_count=count,
        changed_count=1,
        unchanged_count=count - 1,
        failed_count=0,
        created_at=datetime.now(timezone.utc),
    )
    changes = [
        PolicyReplayDecisionChange(
            decision_id=f"dec_{i}",
            original_action="allow",
            replayed_action="allow" if i > 0 else "deny",
            changed=(i == 0),
        )
        for i in range(count)
    ]
    return PolicyReplayResult(replay=run, changes=changes)


class TestInMemoryPolicyReplayStore:
    @pytest.mark.asyncio
    async def test_save_and_get(self):
        store = InMemoryPolicyReplayStore()
        result = _make_result("replay_1")
        saved = await store.save(result)
        assert saved.replay.replay_id == "replay_1"

        fetched = await store.get("replay_1")
        assert fetched is not None
        assert fetched.replay.source_decision_count == 3
        assert len(fetched.changes) == 3

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self):
        store = InMemoryPolicyReplayStore()
        result = await store.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_empty(self):
        store = InMemoryPolicyReplayStore()
        runs = await store.list()
        assert runs == []

    @pytest.mark.asyncio
    async def test_list_returns_runs_most_recent_first(self):
        store = InMemoryPolicyReplayStore()
        await store.save(_make_result("replay_1"))
        await store.save(_make_result("replay_2"))
        await store.save(_make_result("replay_3"))

        runs = await store.list()
        assert len(runs) == 3
        # Most recent first
        assert runs[0].replay_id == "replay_3"
        assert runs[1].replay_id == "replay_2"
        assert runs[2].replay_id == "replay_1"

    @pytest.mark.asyncio
    async def test_list_respects_limit(self):
        store = InMemoryPolicyReplayStore()
        for i in range(10):
            await store.save(_make_result(f"replay_{i}"))

        runs = await store.list(limit=3)
        assert len(runs) == 3
        assert runs[0].replay_id == "replay_9"
        assert runs[2].replay_id == "replay_7"

    @pytest.mark.asyncio
    async def test_save_overwrites_same_id(self):
        store = InMemoryPolicyReplayStore()
        result1 = _make_result("replay_1", count=3)
        result2 = _make_result("replay_1", count=5)
        await store.save(result1)
        await store.save(result2)

        fetched = await store.get("replay_1")
        assert fetched.replay.source_decision_count == 5
        # Should still have only 1 entry in list
        runs = await store.list()
        assert len(runs) == 1
