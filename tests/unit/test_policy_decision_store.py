"""Tests for PolicyDecisionStore protocol and implementations."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from datetime import datetime, timezone

import pytest

from agent_app.governance.policy import PolicyAction, PolicyDecisionTrace
from agent_app.governance.policy_decision_store import (
    InMemoryPolicyDecisionStore,
    PolicyDecisionStore,
    SQLitePolicyDecisionStore,
)


def _make_trace(**overrides) -> PolicyDecisionTrace:
    """Create a PolicyDecisionTrace with defaults."""
    now = datetime.now(timezone.utc)
    defaults = {
        "decision_id": f"dec_{now.timestamp()}",
        "run_id": "run_001",
        "rule_name": "test_rule",
        "action": PolicyAction.ALLOW,
        "reason": "Test reason",
        "matched_conditions": {"tool_name": "test.tool"},
        "context_summary": {"tool_name": "test.tool", "risk_level": "low"},
        "created_at": now,
    }
    defaults.update(overrides)
    return PolicyDecisionTrace(**defaults)


class TestInMemoryPolicyDecisionStore:
    """Tests for InMemoryPolicyDecisionStore."""

    @pytest.mark.asyncio
    async def test_record_and_get(self):
        """record stores trace, get retrieves it by decision_id."""
        store = InMemoryPolicyDecisionStore()
        trace = _make_trace(decision_id="dec_1")
        stored = await store.record(trace)
        assert stored == trace
        retrieved = await store.get("dec_1")
        assert retrieved == trace

    @pytest.mark.asyncio
    async def test_get_missing_raises_error(self):
        """get raises KeyError for unknown decision_id."""
        store = InMemoryPolicyDecisionStore()
        with pytest.raises(KeyError, match="dec_unknown"):
            await store.get("dec_unknown")

    @pytest.mark.asyncio
    async def test_query_empty_store_returns_empty(self):
        """query on empty store returns empty list."""
        store = InMemoryPolicyDecisionStore()
        results = await store.query()
        assert results == []

    @pytest.mark.asyncio
    async def test_query_all_returns_all_sorted_newest_first(self):
        """query returns all traces sorted by created_at descending."""
        store = InMemoryPolicyDecisionStore()
        t1 = _make_trace(decision_id="dec_1", created_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
        t2 = _make_trace(decision_id="dec_2", created_at=datetime(2024, 1, 2, tzinfo=timezone.utc))
        t3 = _make_trace(decision_id="dec_3", created_at=datetime(2024, 1, 3, tzinfo=timezone.utc))
        await store.record(t1)
        await store.record(t2)
        await store.record(t3)
        results = await store.query()
        assert len(results) == 3
        # Newest first
        assert results[0].decision_id == "dec_3"
        assert results[1].decision_id == "dec_2"
        assert results[2].decision_id == "dec_1"

    @pytest.mark.asyncio
    async def test_query_by_run_id(self):
        """query filters by run_id."""
        store = InMemoryPolicyDecisionStore()
        await store.record(_make_trace(decision_id="dec_1", run_id="run_a"))
        await store.record(_make_trace(decision_id="dec_2", run_id="run_b"))
        await store.record(_make_trace(decision_id="dec_3", run_id="run_a"))
        results = await store.query(run_id="run_a")
        assert len(results) == 2
        assert all(r.run_id == "run_a" for r in results)

    @pytest.mark.asyncio
    async def test_query_by_tenant_id(self):
        """query filters by tenant_id from context_summary."""
        store = InMemoryPolicyDecisionStore()
        await store.record(_make_trace(
            decision_id="dec_1",
            context_summary={"tenant_id": "tenant_a"},
        ))
        await store.record(_make_trace(
            decision_id="dec_2",
            context_summary={"tenant_id": "tenant_b"},
        ))
        results = await store.query(tenant_id="tenant_a")
        assert len(results) == 1
        assert results[0].decision_id == "dec_1"

    @pytest.mark.asyncio
    async def test_query_by_agent_name(self):
        """query filters by agent_name from context_summary."""
        store = InMemoryPolicyDecisionStore()
        await store.record(_make_trace(
            decision_id="dec_1",
            context_summary={"agent_name": "refund"},
        ))
        await store.record(_make_trace(
            decision_id="dec_2",
            context_summary={"agent_name": "billing"},
        ))
        results = await store.query(agent_name="refund")
        assert len(results) == 1
        assert results[0].decision_id == "dec_1"

    @pytest.mark.asyncio
    async def test_query_by_tool_name(self):
        """query filters by tool_name."""
        store = InMemoryPolicyDecisionStore()
        await store.record(_make_trace(decision_id="dec_1", tool_name="refund.request"))
        await store.record(_make_trace(decision_id="dec_2", tool_name="billing.query"))
        results = await store.query(tool_name="refund.request")
        assert len(results) == 1
        assert results[0].decision_id == "dec_1"

    @pytest.mark.asyncio
    async def test_query_by_rule_name(self):
        """query filters by rule_name."""
        store = InMemoryPolicyDecisionStore()
        await store.record(_make_trace(decision_id="dec_1", rule_name="rule_a"))
        await store.record(_make_trace(decision_id="dec_2", rule_name="rule_b"))
        results = await store.query(rule_name="rule_a")
        assert len(results) == 1
        assert results[0].decision_id == "dec_1"

    @pytest.mark.asyncio
    async def test_query_by_action(self):
        """query filters by action."""
        store = InMemoryPolicyDecisionStore()
        await store.record(_make_trace(decision_id="dec_1", action=PolicyAction.DENY))
        await store.record(_make_trace(decision_id="dec_2", action=PolicyAction.ALLOW))
        results = await store.query(action="deny")
        assert len(results) == 1
        assert results[0].decision_id == "dec_1"

    @pytest.mark.asyncio
    async def test_query_limit(self):
        """query respects limit parameter."""
        store = InMemoryPolicyDecisionStore()
        for i in range(5):
            await store.record(_make_trace(
                decision_id=f"dec_{i}",
                created_at=datetime(2024, 1, 1 + i, tzinfo=timezone.utc),
            ))
        results = await store.query(limit=2)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_query_offset(self):
        """query respects offset parameter."""
        store = InMemoryPolicyDecisionStore()
        for i in range(5):
            await store.record(_make_trace(
                decision_id=f"dec_{i}",
                created_at=datetime(2024, 1, 1 + i, tzinfo=timezone.utc),
            ))
        results = await store.query(offset=2)
        assert len(results) == 3
        # Newest first: dec_4 (Jan 5), dec_3 (Jan 4), dec_2 (Jan 3), dec_1 (Jan 2), dec_0 (Jan 1)
        # offset=2 skips dec_4 and dec_3
        assert results[0].decision_id == "dec_2"
        assert results[1].decision_id == "dec_1"
        assert results[2].decision_id == "dec_0"

    @pytest.mark.asyncio
    async def test_query_limit_and_offset(self):
        """query supports both limit and offset."""
        store = InMemoryPolicyDecisionStore()
        for i in range(5):
            await store.record(_make_trace(
                decision_id=f"dec_{i}",
                created_at=datetime(2024, 1, 1 + i, tzinfo=timezone.utc),
            ))
        results = await store.query(limit=2, offset=1)
        assert len(results) == 2
        # Sorted newest first: dec_4, dec_3, dec_2, dec_1, dec_0
        # offset=1 skips dec_4, limit=2 takes dec_3 and dec_2
        assert results[0].decision_id == "dec_3"
        assert results[1].decision_id == "dec_2"

    @pytest.mark.asyncio
    async def test_count_empty_store(self):
        """count on empty store returns 0."""
        store = InMemoryPolicyDecisionStore()
        assert await store.count() == 0

    @pytest.mark.asyncio
    async def test_count_all(self):
        """count returns total number of traces."""
        store = InMemoryPolicyDecisionStore()
        for i in range(3):
            await store.record(_make_trace(decision_id=f"dec_{i}"))
        assert await store.count() == 3

    @pytest.mark.asyncio
    async def test_count_with_filters(self):
        """count respects filter parameters."""
        store = InMemoryPolicyDecisionStore()
        await store.record(_make_trace(decision_id="dec_1", action=PolicyAction.DENY))
        await store.record(_make_trace(decision_id="dec_2", action=PolicyAction.ALLOW))
        await store.record(_make_trace(decision_id="dec_3", action=PolicyAction.DENY))
        assert await store.count(action="deny") == 2
        assert await store.count(action="allow") == 1


class TestSQLitePolicyDecisionStore:
    """Tests for SQLitePolicyDecisionStore."""

    @pytest.mark.asyncio
    async def test_record_and_get(self):
        """record stores trace, get retrieves it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = SQLitePolicyDecisionStore(db_path)
            trace = _make_trace(decision_id="dec_sql_1")
            stored = await store.record(trace)
            assert stored == trace
            retrieved = await store.get("dec_sql_1")
            assert retrieved.decision_id == "dec_sql_1"
            assert retrieved.action == PolicyAction.ALLOW

    @pytest.mark.asyncio
    async def test_get_missing_raises_error(self):
        """get raises KeyError for unknown decision_id."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = SQLitePolicyDecisionStore(db_path)
            with pytest.raises(KeyError, match="dec_unknown"):
                await store.get("dec_unknown")

    @pytest.mark.asyncio
    async def test_persists_across_instances(self):
        """Data persists when store is reopened."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            # Write
            store1 = SQLitePolicyDecisionStore(db_path)
            await store1.record(_make_trace(decision_id="dec_persist"))
            store1.close()
            # Read back
            store2 = SQLitePolicyDecisionStore(db_path)
            retrieved = await store2.get("dec_persist")
            assert retrieved.decision_id == "dec_persist"
            store2.close()

    @pytest.mark.asyncio
    async def test_query_with_filters(self):
        """query supports multiple filters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = SQLitePolicyDecisionStore(db_path)
            await store.record(_make_trace(
                decision_id="dec_1", run_id="run_a", action=PolicyAction.DENY,
                context_summary={"tenant_id": "t1", "agent_name": "refund"},
            ))
            await store.record(_make_trace(
                decision_id="dec_2", run_id="run_b", action=PolicyAction.ALLOW,
                context_summary={"tenant_id": "t2", "agent_name": "billing"},
            ))
            results = await store.query(run_id="run_a", action="deny")
            assert len(results) == 1
            assert results[0].decision_id == "dec_1"

    @pytest.mark.asyncio
    async def test_count_with_filters(self):
        """count respects filters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = SQLitePolicyDecisionStore(db_path)
            await store.record(_make_trace(
                decision_id="dec_1", action=PolicyAction.DENY,
                context_summary={"tenant_id": "t1"},
            ))
            await store.record(_make_trace(
                decision_id="dec_2", action=PolicyAction.ALLOW,
                context_summary={"tenant_id": "t1"},
            ))
            assert await store.count(tenant_id="t1") == 2
            assert await store.count(tenant_id="t1", action="deny") == 1

    @pytest.mark.asyncio
    async def test_query_limit_offset(self):
        """query supports limit and offset."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = SQLitePolicyDecisionStore(db_path)
            for i in range(5):
                await store.record(_make_trace(
                    decision_id=f"dec_{i}",
                    created_at=datetime(2024, 1, 1 + i, tzinfo=timezone.utc),
                ))
            results = await store.query(limit=2, offset=1)
            assert len(results) == 2

    @pytest.mark.asyncio
    async def test_json_fields_roundtrip(self):
        """JSON fields (matched_conditions, context_summary) survive roundtrip."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = SQLitePolicyDecisionStore(db_path)
            trace = _make_trace(
                decision_id="dec_json",
                matched_conditions={"tool_name": "test.tool", "risk_level": "high"},
                context_summary={"tenant_id": "t1", "roles": ["admin"], "permissions": ["read"]},
            )
            await store.record(trace)
            retrieved = await store.get("dec_json")
            assert retrieved.matched_conditions == {
                "tool_name": "test.tool", "risk_level": "high"
            }
            assert retrieved.context_summary == {
                "tenant_id": "t1", "roles": ["admin"], "permissions": ["read"]
            }

    @pytest.mark.asyncio
    async def test_sorted_by_created_at_desc(self):
        """Results are sorted by created_at descending."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = SQLitePolicyDecisionStore(db_path)
            await store.record(_make_trace(
                decision_id="dec_1",
                created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            ))
            await store.record(_make_trace(
                decision_id="dec_2",
                created_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
            ))
            await store.record(_make_trace(
                decision_id="dec_3",
                created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            ))
            results = await store.query()
            assert results[0].decision_id == "dec_2"
            assert results[1].decision_id == "dec_3"
            assert results[2].decision_id == "dec_1"


# ---------------------------------------------------------------------------
# Phase 25: PolicyReportingService tests
# ---------------------------------------------------------------------------

from agent_app.governance.policy_decision_store import PolicyReport, PolicyReportingService


class TestPolicyReportingService:
    """Tests for PolicyReportingService."""

    def _make_store_with_traces(self, traces):
        """Create an InMemoryPolicyDecisionStore pre-populated with traces."""
        store = InMemoryPolicyDecisionStore()
        for trace in traces:
            store._traces.append(trace)
        return store

    @pytest.mark.asyncio
    async def test_empty_report(self):
        """Report on empty store shows zero counts."""
        store = self._make_store_with_traces([])
        service = PolicyReportingService(store)
        report = await service.generate_report()
        assert report.total_decisions == 0
        assert report.action_breakdown == {}
        assert report.rule_breakdown == {}
        assert report.tool_breakdown == {}

    @pytest.mark.asyncio
    async def test_action_breakdown(self):
        """Report counts decisions by action."""
        traces = [
            _make_trace(decision_id="d1", action=PolicyAction.ALLOW),
            _make_trace(decision_id="d2", action=PolicyAction.ALLOW),
            _make_trace(decision_id="d3", action=PolicyAction.DENY),
            _make_trace(decision_id="d4", action=PolicyAction.REQUIRE_APPROVAL),
        ]
        store = self._make_store_with_traces(traces)
        service = PolicyReportingService(store)
        report = await service.generate_report()
        assert report.total_decisions == 4
        assert report.action_breakdown == {
            "allow": 2,
            "deny": 1,
            "require_approval": 1,
        }

    @pytest.mark.asyncio
    async def test_rule_breakdown(self):
        """Report counts decisions by rule name."""
        traces = [
            _make_trace(decision_id="d1", rule_name="rule_a"),
            _make_trace(decision_id="d2", rule_name="rule_a"),
            _make_trace(decision_id="d3", rule_name="rule_b"),
            _make_trace(decision_id="d4", rule_name=None),  # default
        ]
        store = self._make_store_with_traces(traces)
        service = PolicyReportingService(store)
        report = await service.generate_report()
        assert report.rule_breakdown == {
            "rule_a": 2,
            "rule_b": 1,
            "(default)": 1,
        }

    @pytest.mark.asyncio
    async def test_tool_breakdown(self):
        """Report counts decisions by tool name."""
        traces = [
            _make_trace(decision_id="d1", tool_name="tool.a"),
            _make_trace(decision_id="d2", tool_name="tool.a"),
            _make_trace(decision_id="d3", tool_name="tool.b"),
            _make_trace(decision_id="d4", tool_name=None),  # unknown
        ]
        store = self._make_store_with_traces(traces)
        service = PolicyReportingService(store)
        report = await service.generate_report()
        assert report.tool_breakdown == {
            "tool.a": 2,
            "tool.b": 1,
            "(unknown)": 1,
        }

    @pytest.mark.asyncio
    async def test_time_range_populated(self):
        """Report includes time range when traces exist."""
        t1 = _make_trace(
            decision_id="d1",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        t2 = _make_trace(
            decision_id="d2",
            created_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
        )
        traces = [t1, t2]
        store = self._make_store_with_traces(traces)
        service = PolicyReportingService(store)
        report = await service.generate_report()
        assert report.time_range["start"] == datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert report.time_range["end"] == datetime(2024, 1, 3, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_report_with_filters(self):
        """Report respects filter parameters."""
        traces = [
            _make_trace(decision_id="d1", run_id="run_a", action=PolicyAction.ALLOW),
            _make_trace(decision_id="d2", run_id="run_b", action=PolicyAction.DENY),
            _make_trace(decision_id="d3", run_id="run_a", action=PolicyAction.DENY),
        ]
        store = self._make_store_with_traces(traces)
        service = PolicyReportingService(store)
        report = await service.generate_report(run_id="run_a")
        assert report.total_decisions == 2
        assert report.action_breakdown == {"allow": 1, "deny": 1}

    @pytest.mark.asyncio
    async def test_export_jsonl(self, tmp_path):
        """Export to JSONL format."""
        traces = [
            _make_trace(decision_id="d1", action=PolicyAction.ALLOW),
            _make_trace(decision_id="d2", action=PolicyAction.DENY),
        ]
        store = self._make_store_with_traces(traces)
        service = PolicyReportingService(store)
        output = str(tmp_path / "report.jsonl")
        count = await service.export_jsonl(output)
        assert count == 2
        lines = Path(output).read_text().strip().split("\n")
        assert len(lines) == 2
        import json
        data = [json.loads(line) for line in lines]
        # Sorted newest-first
        assert data[0]["decision_id"] == "d2"
        assert data[1]["decision_id"] == "d1"

    @pytest.mark.asyncio
    async def test_export_csv(self, tmp_path):
        """Export to CSV format."""
        traces = [
            _make_trace(decision_id="d1", action=PolicyAction.ALLOW),
            _make_trace(decision_id="d2", action=PolicyAction.DENY),
        ]
        store = self._make_store_with_traces(traces)
        service = PolicyReportingService(store)
        output = str(tmp_path / "report.csv")
        count = await service.export_csv(output)
        assert count == 2
        import csv
        with open(output, newline="") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
        assert len(rows) == 2
        # Sorted newest-first by created_at
        assert rows[0]["decision_id"] == "d2"
        assert rows[1]["decision_id"] == "d1"
        # CSV should not include matched_conditions/context_summary
        assert "matched_conditions" not in reader.fieldnames
