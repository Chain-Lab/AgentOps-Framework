"""Phase 65 — OpenTelemetry trace collector tests."""
from __future__ import annotations

import pytest

from agent_app.observability.events import RunEvent, RunEventType


def _has_otel() -> bool:
    try:
        import opentelemetry.sdk.trace  # noqa: F401
        return True
    except ImportError:
        return False


class TestOpenTelemetryNotInstalledError:
    def test_error_message_has_install_hint(self):
        from agent_app.observability.otel import OpenTelemetryNotInstalledError
        err = OpenTelemetryNotInstalledError()
        assert "pip install" in str(err)
        assert "otel" in str(err)


@pytest.mark.skipif(not _has_otel(), reason="opentelemetry not installed")
class TestOtelTraceCollector:
    @pytest.mark.asyncio
    async def test_record_and_get_events_roundtrip(self):
        """Protocol conformance: record() then get_events() must return it back,
        proving the dual-write design (OTel export + in-memory read-back buffer)."""
        from agent_app.observability.otel import OtelTraceCollector

        collector = OtelTraceCollector(service_name="test-service", exporter="console")
        event = RunEvent(
            trace_id="trace-1",
            event_type=RunEventType.RUN_STARTED,
            run_id="run-1",
            status="started",
        )
        await collector.record(event)
        events = await collector.get_events("trace-1")
        assert len(events) == 1
        assert events[0].event_id == event.event_id

    @pytest.mark.asyncio
    async def test_list_traces_returns_recorded_trace_ids(self):
        from agent_app.observability.otel import OtelTraceCollector

        collector = OtelTraceCollector(service_name="test-service", exporter="console")
        await collector.record(RunEvent(trace_id="trace-a", event_type=RunEventType.RUN_STARTED))
        await collector.record(RunEvent(trace_id="trace-b", event_type=RunEventType.RUN_STARTED))
        traces = await collector.list_traces()
        assert set(traces) == {"trace-a", "trace-b"}

    @pytest.mark.asyncio
    async def test_deterministic_otel_trace_id_for_same_run_event_trace_id(self):
        """Two events sharing the same RunEvent.trace_id must map to the same
        OTel trace ID, so span-correlation in an external backend works."""
        from agent_app.observability.otel import _otel_trace_id_from_string

        tid1 = _otel_trace_id_from_string("trace-xyz")
        tid2 = _otel_trace_id_from_string("trace-xyz")
        tid3 = _otel_trace_id_from_string("trace-different")
        assert tid1 == tid2
        assert tid1 != tid3

    @pytest.mark.asyncio
    async def test_respects_max_traces_retention(self):
        from agent_app.observability.otel import OtelTraceCollector

        collector = OtelTraceCollector(
            service_name="test-service", exporter="console", max_traces=2
        )
        await collector.record(RunEvent(trace_id="t1", event_type=RunEventType.RUN_STARTED))
        await collector.record(RunEvent(trace_id="t2", event_type=RunEventType.RUN_STARTED))
        await collector.record(RunEvent(trace_id="t3", event_type=RunEventType.RUN_STARTED))
        traces = await collector.list_traces(limit=100)
        assert len(traces) == 2


def test_otel_import_failure_raises_clear_error(monkeypatch):
    """When opentelemetry packages are absent, constructing OtelTraceCollector
    must raise OpenTelemetryNotInstalledError, not a bare ImportError."""
    import sys
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("opentelemetry"):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    for mod in list(sys.modules):
        if mod.startswith("opentelemetry"):
            monkeypatch.delitem(sys.modules, mod, raising=False)

    from agent_app.observability.otel import OtelTraceCollector, OpenTelemetryNotInstalledError

    with pytest.raises(OpenTelemetryNotInstalledError):
        OtelTraceCollector(service_name="test-service")
