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
