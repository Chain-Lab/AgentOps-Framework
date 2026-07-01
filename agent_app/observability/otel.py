"""Optional OpenTelemetry bridge for RunEvent export.

This module is an **experimental** optional dependency.  Install with:

    pip install 'agent-app-framework[otel]'

If OpenTelemetry is not installed, importing this module succeeds, but
instantiating ``OpenTelemetryTraceExporter`` raises ``OpenTelemetryNotInstalledError``
with a clear install message.

The exporter maps ``RunEvent`` instances to OpenTelemetry spans.  It does
not yet implement OTLP export, distributed trace propagation, or a running
collector service — those are planned for a future phase.
"""

from __future__ import annotations

from typing import Any

from agent_app.observability.events import RunEvent


class OpenTelemetryNotInstalledError(RuntimeError):
    """Raised when OpenTelemetry is required but not installed."""

    def __init__(self) -> None:
        super().__init__(
            "OpenTelemetry is not installed. "
            "Install with: pip install 'agent-app-framework[otel]'"
        )


class OpenTelemetryTraceExporter:
    """Minimal RunEvent → OpenTelemetry span mapper.

    This is a lightweight bridge.  Each ``RunEvent`` becomes a span with:
    - ``span.name`` = ``event.event_type``
    - ``span.set_attribute("agent_app.trace_id", event.trace_id)``
    - ``span.set_attribute("agent_app.run_id", event.run_id)``
    - ``span.set_attribute("agent_app.event_id", event.event_id)``
    - ``span.set_attribute("agent_app.status", event.status)`` when set

    Args:
        service_name: Service name for the OTLP resource.

    Raises:
        OpenTelemetryNotInstalledError: If OpenTelemetry packages are missing.
    """

    def __init__(self, service_name: str = "agent-app") -> None:
        self._service_name = service_name
        self._tracer: Any = None
        self._import_opentelemetry()

    def _import_opentelemetry(self) -> None:
        """Lazily import OpenTelemetry; raise with clear message if missing."""
        try:
            from opentelemetry import trace  # noqa: F401
            from opentelemetry.sdk.resources import Resource  # noqa: F401
            from opentelemetry.sdk.trace import TracerProvider  # noqa: F401
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: F401
            from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
                InMemorySpanExporter,  # noqa: F401
            )
        except ImportError as exc:
            raise OpenTelemetryNotInstalledError() from exc

        # Set up a tracer provider with an in-memory exporter (no OTLP yet)
        from opentelemetry import trace as _trace
        resource = Resource.create({"service.name": self._service_name})
        provider = TracerProvider(resource=resource)
        self._exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(self._exporter))
        _trace.set_tracer_provider(provider)
        self._tracer = _trace.get_tracer("agent_app")

    async def export_events(self, events: list[RunEvent]) -> None:
        """Export a batch of RunEvents as OpenTelemetry spans.

        Args:
            events: RunEvents to export.

        Raises:
            OpenTelemetryNotInstalledError: If OpenTelemetry packages are missing.
        """
        if self._tracer is None:
            self._import_opentelemetry()

        from opentelemetry import trace as _trace

        for event in events:
            event_type = str(event.event_type.value if hasattr(event.event_type, "value") else event.event_type)
            with self._tracer.start_as_current_span(event_type) as span:
                span.set_attribute("agent_app.trace_id", event.trace_id)
                span.set_attribute("agent_app.run_id", event.run_id or "")
                span.set_attribute("agent_app.event_id", event.event_id)
                span.set_attribute("agent_app.user_id", event.user_id or "")
                span.set_attribute("agent_app.tenant_id", event.tenant_id or "")
                if event.status:
                    span.set_attribute("agent_app.status", event.status)
                if event.tool_name:
                    span.set_attribute("agent_app.tool_name", event.tool_name)
                if event.agent_name:
                    span.set_attribute("agent_app.agent_name", event.agent_name)
                if event.error:
                    span.set_attribute("agent_app.error_type", event.error.get("type", ""))
                    span.record_exception(Exception(event.error.get("message", "")))

        # Force flush to the in-memory exporter
        if hasattr(self, "_exporter"):
            self._exporter.clear()

    def get_spans(self) -> list[Any]:
        """Return spans from the in-memory exporter (for testing).

        Returns empty list if OpenTelemetry is not installed.
        """
        if hasattr(self, "_exporter"):
            return list(self._exporter.get_finished_spans())
        return []


def _otel_trace_id_from_string(s: str) -> int:
    """Deterministically derive a 128-bit OTel trace ID from a RunEvent.trace_id string."""
    import hashlib
    digest = hashlib.md5(s.encode("utf-8")).hexdigest()
    return int(digest, 16)


def _otel_span_id_from_string(s: str) -> int:
    """Deterministically derive a 64-bit OTel span ID from a RunEvent.event_id string."""
    import hashlib
    digest = hashlib.md5(s.encode("utf-8")).hexdigest()[:16]
    return int(digest, 16)


class OtelTraceCollector:
    """TraceCollector Protocol implementation backed by OpenTelemetry.

    Dual-writes every recorded RunEvent:
    1. Converts it to an OTel span and exports via the configured exporter
       (console or OTLP HTTP).
    2. Buffers it in an internal InMemoryTraceCollector so existing
       get_events()/list_traces() callers (FastAPI trace endpoints, CLI
       trace commands) keep working even though OTLP export itself is
       fire-and-forget and not readable back locally.

    Args:
        service_name: OTel resource service.name.
        exporter: "console" (OTel SDK ConsoleSpanExporter, no extra deps
                  beyond opentelemetry-sdk) or "otlp" (requires
                  opentelemetry-exporter-otlp-proto-http and otlp_endpoint).
        otlp_endpoint: Required when exporter="otlp".
        max_traces: Passed through to the internal InMemoryTraceCollector.
        max_events_per_trace: Passed through to the internal InMemoryTraceCollector.

    Raises:
        OpenTelemetryNotInstalledError: If OpenTelemetry packages are missing.
    """

    def __init__(
        self,
        service_name: str = "agent-app",
        exporter: str = "console",
        otlp_endpoint: str | None = None,
        max_traces: int | None = None,
        max_events_per_trace: int | None = None,
    ) -> None:
        from agent_app.observability.collector import InMemoryTraceCollector

        self._service_name = service_name
        self._exporter_type = exporter
        self._otlp_endpoint = otlp_endpoint
        self._buffer = InMemoryTraceCollector(
            max_traces=max_traces, max_events_per_trace=max_events_per_trace
        )
        self._tracer: Any = None
        self._setup_tracer()

    def _setup_tracer(self) -> None:
        try:
            from opentelemetry import trace as _trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter
        except ImportError as exc:
            raise OpenTelemetryNotInstalledError() from exc

        resource = Resource.create({"service.name": self._service_name})
        provider = TracerProvider(resource=resource)

        if self._exporter_type == "otlp":
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )
            except ImportError as exc:
                raise OpenTelemetryNotInstalledError() from exc
            span_exporter = OTLPSpanExporter(endpoint=self._otlp_endpoint)
        else:
            span_exporter = ConsoleSpanExporter()

        provider.add_span_processor(SimpleSpanProcessor(span_exporter))
        _trace.set_tracer_provider(provider)
        self._tracer = _trace.get_tracer("agent_app")

    async def record(self, event: RunEvent) -> None:
        """Record an event: buffer for read-back and export as an OTel span."""
        await self._buffer.record(event)
        self._export_span(event)

    def _export_span(self, event: RunEvent) -> None:
        from opentelemetry import trace as _trace
        from opentelemetry.trace import SpanContext, TraceFlags, NonRecordingSpan

        event_type = str(event.event_type.value if hasattr(event.event_type, "value") else event.event_type)
        trace_id = _otel_trace_id_from_string(event.trace_id)
        span_id = _otel_span_id_from_string(event.event_id)

        parent_context = _trace.set_span_in_context(
            NonRecordingSpan(SpanContext(
                trace_id=trace_id,
                span_id=span_id,
                is_remote=False,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            ))
        )

        with self._tracer.start_as_current_span(event_type, context=parent_context) as span:
            span.set_attribute("agent_app.trace_id", event.trace_id)
            span.set_attribute("agent_app.event_id", event.event_id)
            if event.run_id:
                span.set_attribute("agent_app.run_id", event.run_id)
            if event.user_id:
                span.set_attribute("agent_app.user_id", event.user_id)
            if event.tenant_id:
                span.set_attribute("agent_app.tenant_id", event.tenant_id)
            if event.workflow_name:
                span.set_attribute("agent_app.workflow_name", event.workflow_name)
            if event.agent_name:
                span.set_attribute("agent_app.agent_name", event.agent_name)
            if event.tool_name:
                span.set_attribute("agent_app.tool_name", event.tool_name)
            if event.status:
                span.set_attribute("agent_app.status", event.status)
            if event.error:
                span.set_attribute("agent_app.error_type", event.error.get("type", ""))
                span.record_exception(Exception(event.error.get("message", "")))
            for k, v in event.data.items():
                if isinstance(v, (str, int, float, bool)):
                    span.set_attribute(f"agent_app.data.{k}", v)

    async def get_events(self, trace_id: str) -> list[RunEvent]:
        return await self._buffer.get_events(trace_id)

    async def list_traces(
        self,
        tenant_id: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> list[str]:
        return await self._buffer.list_traces(tenant_id=tenant_id, run_id=run_id, limit=limit)
