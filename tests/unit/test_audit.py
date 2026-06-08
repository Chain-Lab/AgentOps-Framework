"""Tests for audit logger."""

import pytest

from agent_app.governance.audit import AuditEvent, InMemoryAuditLogger


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


class TestAuditEvent:
    def test_create(self) -> None:
        ev = _make_event()
        assert ev.event_id == "evt_001"
        assert ev.event_type == "tool.executed"
        assert ev.run_id == "run_001"

    def test_defaults(self) -> None:
        ev = _make_event(approval_id=None, user_id=None, tenant_id=None)
        assert ev.approval_id is None
        assert ev.data == {"status": "completed"}


class TestInMemoryAuditLogger:
    @pytest.fixture
    def logger(self) -> InMemoryAuditLogger:
        return InMemoryAuditLogger()

    @pytest.mark.asyncio
    async def test_log_and_list(self, logger) -> None:
        ev = _make_event(event_id="evt_1")
        await logger.log(ev)
        events = logger.list_events()
        assert len(events) == 1
        assert events[0].event_id == "evt_1"

    @pytest.mark.asyncio
    async def test_list_by_run_id(self, logger) -> None:
        await logger.log(_make_event(event_id="evt_1", run_id="run_a"))
        await logger.log(_make_event(event_id="evt_2", run_id="run_b"))
        await logger.log(_make_event(event_id="evt_3", run_id="run_a"))

        run_a_events = logger.list_events(run_id="run_a")
        assert len(run_a_events) == 2

    @pytest.mark.asyncio
    async def test_list_by_tenant_id(self, logger) -> None:
        await logger.log(_make_event(event_id="evt_1", tenant_id="tenant_a"))
        await logger.log(_make_event(event_id="evt_2", tenant_id="tenant_b"))

        ta_events = logger.list_events(tenant_id="tenant_a")
        assert len(ta_events) == 1

    @pytest.mark.asyncio
    async def test_list_by_event_type(self, logger) -> None:
        await logger.log(_make_event(event_id="evt_1", event_type="tool.executed"))
        await logger.log(_make_event(event_id="evt_2", event_type="tool.approval_required"))

        executed = logger.list_events(event_type="tool.executed")
        assert len(executed) == 1

    @pytest.mark.asyncio
    async def test_combined_filters(self, logger) -> None:
        await logger.log(_make_event(event_id="evt_1", run_id="r1", event_type="tool.executed"))
        await logger.log(_make_event(event_id="evt_2", run_id="r1", event_type="tool.approval_required"))
        await logger.log(_make_event(event_id="evt_3", run_id="r2", event_type="tool.executed"))

        filtered = logger.list_events(run_id="r1", event_type="tool.executed")
        assert len(filtered) == 1
        assert filtered[0].event_id == "evt_1"

    @pytest.mark.asyncio
    async def test_clear(self, logger) -> None:
        await logger.log(_make_event(event_id="evt_1"))
        logger.clear()
        assert logger.list_events() == []

    @pytest.mark.asyncio
    async def test_sorted_by_created_at(self, logger) -> None:
        import time as _time
        ev1 = _make_event(event_id="evt_1")
        await logger.log(ev1)
        _time.sleep(0.01)
        ev2 = _make_event(event_id="evt_2")
        await logger.log(ev2)

        events = logger.list_events()
        assert events[0].event_id == "evt_1"
        assert events[1].event_id == "evt_2"
