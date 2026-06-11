"""Tests for SQLite-backed policy replay store."""

from __future__ import annotations

import json
import os
import tempfile

import pytest
from datetime import datetime, timezone

from agent_app.runtime.policy_replay_store import SQLitePolicyReplayStore
from agent_app.governance.policy_replay import (
    PolicyReplayDecisionChange,
    PolicyReplayResult,
    PolicyReplayRun,
    PolicyReplayStatus,
)


def _make_result(
    replay_id: str = "replay_1",
    count: int = 3,
    failed: int = 0,
) -> PolicyReplayResult:
    """Create a test replay result."""
    run = PolicyReplayRun(
        replay_id=replay_id,
        status=PolicyReplayStatus.COMPLETED,
        source_decision_count=count,
        changed_count=1,
        unchanged_count=count - 1 - failed,
        failed_count=failed,
        created_at=datetime.now(timezone.utc),
    )
    changes = [
        PolicyReplayDecisionChange(
            decision_id=f"dec_{i}",
            original_action="allow",
            replayed_action="deny" if i == 0 else "allow",
            changed=(i == 0),
            original_rule_id="old_rule" if i == 0 else None,
            replayed_rule_id="new_rule" if i == 0 else None,
            reason="policy updated" if i == 0 else None,
        )
        for i in range(count)
    ]
    # Mark some as failed if needed
    if failed > 0:
        for i in range(count - failed, count):
            changes[i] = PolicyReplayDecisionChange(
                decision_id=f"dec_{i}",
                original_action="allow",
                replayed_action="error",
                changed=False,
                reason=f"Missing tool_name for dec_{i}",
            )
    return PolicyReplayResult(replay=run, changes=changes)


def _make_db_path(tmp_path):
    """Create a temp db path."""
    return str(tmp_path / "test_replays.db")


class TestSQLitePolicyReplayStore:
    """Tests for SQLitePolicyReplayStore."""

    async def test_save_and_get(self, tmp_path):
        """Save and retrieve a replay result."""
        store = SQLitePolicyReplayStore(db_path=_make_db_path(tmp_path))
        result = _make_result("replay_1")
        saved = await store.save(result)
        assert saved.replay.replay_id == "replay_1"

        fetched = await store.get("replay_1")
        assert fetched is not None
        assert fetched.replay.source_decision_count == 3
        assert len(fetched.changes) == 3
        assert fetched.changes[0].changed
        store.close()

    async def test_get_missing_raises_key_error(self, tmp_path):
        """Getting a non-existent replay raises KeyError."""
        store = SQLitePolicyReplayStore(db_path=_make_db_path(tmp_path))
        with pytest.raises(KeyError, match="not found"):
            await store.get("nonexistent")
        store.close()

    async def test_list_empty(self, tmp_path):
        """List returns empty list for new store."""
        store = SQLitePolicyReplayStore(db_path=_make_db_path(tmp_path))
        runs = await store.list()
        assert runs == []
        store.close()

    async def test_list_returns_runs_most_recent_first(self, tmp_path):
        """List returns runs ordered by created_at descending."""
        store = SQLitePolicyReplayStore(db_path=_make_db_path(tmp_path))
        await store.save(_make_result("replay_1"))
        await store.save(_make_result("replay_2"))
        await store.save(_make_result("replay_3"))

        runs = await store.list()
        assert len(runs) == 3
        assert runs[0].replay_id == "replay_3"
        assert runs[1].replay_id == "replay_2"
        assert runs[2].replay_id == "replay_1"
        store.close()

    async def test_list_respects_limit(self, tmp_path):
        """List respects the limit parameter."""
        store = SQLitePolicyReplayStore(db_path=_make_db_path(tmp_path))
        for i in range(10):
            await store.save(_make_result(f"replay_{i}"))

        runs = await store.list(limit=3)
        assert len(runs) == 3
        assert runs[0].replay_id == "replay_9"
        assert runs[2].replay_id == "replay_7"
        store.close()

    async def test_save_overwrites_same_id(self, tmp_path):
        """Saving with same replay_id overwrites, no duplicates in list."""
        store = SQLitePolicyReplayStore(db_path=_make_db_path(tmp_path))
        result1 = _make_result("replay_1", count=3)
        result2 = _make_result("replay_1", count=5)
        await store.save(result1)
        await store.save(result2)

        fetched = await store.get("replay_1")
        assert fetched.replay.source_decision_count == 5

        runs = await store.list()
        assert len(runs) == 1
        store.close()

    async def test_persists_across_instances(self, tmp_path):
        """Data persists when store is re-opened."""
        db_path = _make_db_path(tmp_path)
        store1 = SQLitePolicyReplayStore(db_path=db_path)
        await store1.save(_make_result("replay_persist"))
        store1.close()

        # Re-open
        store2 = SQLitePolicyReplayStore(db_path=db_path)
        fetched = await store2.get("replay_persist")
        assert fetched is not None
        assert fetched.replay.source_decision_count == 3
        store2.close()

    async def test_list_changes(self, tmp_path):
        """list_changes returns changes for a replay."""
        store = SQLitePolicyReplayStore(db_path=_make_db_path(tmp_path))
        result = _make_result("replay_changes", count=5)
        await store.save(result)

        changes = await store.list_changes("replay_changes")
        assert len(changes) == 5
        assert changes[0].decision_id == "dec_0"
        assert changes[0].changed
        store.close()

    async def test_list_changes_missing_replay_raises(self, tmp_path):
        """list_changes raises KeyError for missing replay."""
        store = SQLitePolicyReplayStore(db_path=_make_db_path(tmp_path))
        with pytest.raises(KeyError, match="not found"):
            await store.list_changes("nonexistent")
        store.close()

    async def test_list_changes_changed_only(self, tmp_path):
        """list_changes with changed_only=True filters to changed decisions."""
        store = SQLitePolicyReplayStore(db_path=_make_db_path(tmp_path))
        result = _make_result("replay_filter", count=5)
        await store.save(result)

        changes = await store.list_changes("replay_filter", changed_only=True)
        assert len(changes) == 1
        assert changes[0].changed
        store.close()

    async def test_list_changes_failed_only(self, tmp_path):
        """list_changes with failed_only=True filters to failed decisions."""
        store = SQLitePolicyReplayStore(db_path=_make_db_path(tmp_path))
        result = _make_result("replay_failed", count=5, failed=2)
        await store.save(result)

        changes = await store.list_changes("replay_failed", failed_only=True)
        assert len(changes) == 2
        for c in changes:
            assert c.replayed_action == "error"
        store.close()

    async def test_auto_creates_parent_directory(self, tmp_path):
        """Store creates parent directories automatically."""
        deep_path = str(tmp_path / "deep" / "nested" / "replays.db")
        store = SQLitePolicyReplayStore(db_path=deep_path)
        await store.save(_make_result("replay_deep"))
        fetched = await store.get("replay_deep")
        assert fetched is not None
        store.close()

    async def test_datetime_serialization_roundtrip(self, tmp_path):
        """Datetimes survive save/load roundtrip with timezone info."""
        store = SQLitePolicyReplayStore(db_path=_make_db_path(tmp_path))
        created = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        run = PolicyReplayRun(
            replay_id="replay_dt",
            status=PolicyReplayStatus.COMPLETED,
            source_decision_count=1,
            changed_count=0,
            unchanged_count=1,
            failed_count=0,
            created_at=created,
        )
        result = PolicyReplayResult(
            replay=run,
            changes=[
                PolicyReplayDecisionChange(
                    decision_id="dec_1",
                    original_action="allow",
                    replayed_action="allow",
                    changed=False,
                )
            ],
        )
        await store.save(result)
        fetched = await store.get("replay_dt")
        assert fetched.replay.created_at == created
        store.close()
