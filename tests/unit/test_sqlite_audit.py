"""Tests for SQLiteAuditLogger."""

import os
import pytest

from agent_app.governance.audit import AuditEvent, SQLiteAuditLogger


def _make_event(**kwargs):
    defaults = {
        "event_id": "evt_001",
        "event_type": "tool.executed",
        "run_id": "run_001",
        "user_id": "u1",
        "tenant_id": "t1",
        "tool_name": "order.query",
        "data": {"status": "completed"},
    }
    defaults.update(kwargs)
    return AuditEvent(**defaults)


class TestSQLiteAuditLogger:
    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / "test_audit.db")

    @pytest.fixture
    def logger(self, db_path):
        return SQLiteAuditLogger(db_path=db_path)

    @pytest.mark.asyncio
    async def test_creates_db_file(self, db_path):
        SQLiteAuditLogger(db_path=db_path)
        assert os.path.exists(db_path)

    @pytest.mark.asyncio
    async def test_log_and_list(self, logger):
        ev = _make_event(event_id="evt_1")
        await logger.log(ev)
        events = logger.list_events()
        assert len(events) == 1
        assert events[0].event_id == "evt_1"

    @pytest.mark.asyncio
    async def test_filter_by_run_id(self, logger):
        await logger.log(_make_event(event_id="evt_1", run_id="run_a"))
        await logger.log(_make_event(event_id="evt_2", run_id="run_b"))
        await logger.log(_make_event(event_id="evt_3", run_id="run_a"))
        filtered = logger.list_events(run_id="run_a")
        assert len(filtered) == 2

    @pytest.mark.asyncio
    async def test_filter_by_tenant_id(self, logger):
        await logger.log(_make_event(event_id="evt_1", tenant_id="tenant_a"))
        await logger.log(_make_event(event_id="evt_2", tenant_id="tenant_b"))
        filtered = logger.list_events(tenant_id="tenant_a")
        assert len(filtered) == 1

    @pytest.mark.asyncio
    async def test_filter_by_event_type(self, logger):
        await logger.log(_make_event(event_id="evt_1", event_type="tool.executed"))
        await logger.log(_make_event(event_id="evt_2", event_type="tool.approval_required"))
        filtered = logger.list_events(event_type="tool.executed")
        assert len(filtered) == 1

    @pytest.mark.asyncio
    async def test_combined_filters(self, logger):
        await logger.log(_make_event(event_id="evt_1", run_id="r1", event_type="tool.executed"))
        await logger.log(_make_event(event_id="evt_2", run_id="r1", event_type="tool.approval_required"))
        await logger.log(_make_event(event_id="evt_3", run_id="r2", event_type="tool.executed"))
        filtered = logger.list_events(run_id="r1", event_type="tool.executed")
        assert len(filtered) == 1

    @pytest.mark.asyncio
    async def test_persists_across_instances(self, db_path):
        logger1 = SQLiteAuditLogger(db_path=db_path)
        await logger1.log(_make_event(event_id="evt_1"))
        logger1.close()

        logger2 = SQLiteAuditLogger(db_path=db_path)
        events = logger2.list_events()
        assert len(events) == 1
        logger2.close()

    @pytest.mark.asyncio
    async def test_sorted_by_created_at(self, logger):
        import time as _time
        ev1 = _make_event(event_id="evt_1")
        await logger.log(ev1)
        _time.sleep(0.01)
        ev2 = _make_event(event_id="evt_2")
        await logger.log(ev2)
        events = logger.list_events()
        assert events[0].event_id == "evt_1"
        assert events[1].event_id == "evt_2"
