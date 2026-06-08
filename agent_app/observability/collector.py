"""Trace collector implementations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from agent_app.observability.events import RunEvent


class TraceCollector(Protocol):
    """Protocol for trace collection backends.

    Implementations store and retrieve structured run events
    for debugging, testing, and export.
    """

    async def record(self, event: RunEvent) -> None:
        """Record a single event."""
        ...

    async def get_events(self, trace_id: str) -> list[RunEvent]:
        """Retrieve all events for a given trace, ordered by timestamp ascending."""
        ...

    async def list_traces(
        self,
        tenant_id: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> list[str]:
        """List trace IDs, optionally filtered by tenant or run."""
        ...


class NoOpTraceCollector:
    """No-op collector — discards all events.

    Used when tracing is disabled to avoid conditional checks
    throughout the codebase.
    """

    async def record(self, event: RunEvent) -> None:
        """Discard event."""
        return None

    async def get_events(self, trace_id: str) -> list[RunEvent]:
        """Always returns empty list."""
        return []

    async def list_traces(
        self,
        tenant_id: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> list[str]:
        """Always returns empty list."""
        return []


class InMemoryTraceCollector:
    """In-memory trace collector for testing and development.

    Stores events in memory organized by trace_id.
    Events are returned ordered by timestamp ascending.

    Args:
        max_traces: Maximum number of traces to retain. When exceeded,
                    the oldest trace (by first event timestamp) is dropped.
                    None (default) means unlimited.
        max_events_per_trace: Maximum events per trace. When exceeded,
                              the oldest events within that trace are dropped.
                              None (default) means unlimited.
    """

    def __init__(
        self,
        max_traces: int | None = None,
        max_events_per_trace: int | None = None,
    ) -> None:
        self._events: dict[str, list[RunEvent]] = {}
        self._max_traces = max_traces
        self._max_events_per_trace = max_events_per_trace

    async def record(self, event: RunEvent) -> None:
        """Record an event into its trace bucket.

        Applies retention limits after insertion.
        """
        tid = event.trace_id
        if tid not in self._events:
            self._events[tid] = []
        self._events[tid].append(event)

        # Enforce per-trace event limit (drop oldest events first)
        if self._max_events_per_trace is not None:
            events = self._events[tid]
            if len(events) > self._max_events_per_trace:
                # Sort by timestamp, keep the newest N
                events.sort(key=lambda e: e.timestamp)
                self._events[tid] = events[-self._max_events_per_trace:]

        # Enforce global trace limit (drop oldest trace by first event timestamp)
        if self._max_traces is not None and len(self._events) > self._max_traces:
            self._evict_oldest_trace()

    def _evict_oldest_trace(self) -> None:
        """Remove the trace whose first event has the oldest timestamp."""
        if not self._events:
            return
        oldest_tid = min(
            self._events,
            key=lambda tid: self._events[tid][0].timestamp if self._events[tid] else datetime.max.replace(tzinfo=timezone.utc),
        )
        del self._events[oldest_tid]

    async def get_events(self, trace_id: str) -> list[RunEvent]:
        """Get all events for a trace, ordered by timestamp ascending."""
        events = self._events.get(trace_id, [])
        return sorted(events, key=lambda e: e.timestamp)

    async def list_traces(
        self,
        tenant_id: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> list[str]:
        """List trace IDs, optionally filtered.

        When tenant_id or run_id is provided, only traces that have
        at least one matching event are returned.
        """
        result: list[str] = []
        for tid, events in self._events.items():
            if tenant_id is not None:
                if not any(e.tenant_id == tenant_id for e in events):
                    continue
            if run_id is not None:
                if not any(e.run_id == run_id for e in events):
                    continue
            result.append(tid)
            if len(result) >= limit:
                break
        return result
