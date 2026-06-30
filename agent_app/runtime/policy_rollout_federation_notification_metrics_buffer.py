"""Metrics ring buffer for daemon Phase 62 production hardening.

Buffers :class:`MetricsEvent` objects in a fixed-size ring buffer and
supports periodic flush to a :class:`PrometheusFileMetricsExporter`.
"""
from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class MetricsEvent(BaseModel):
    """Single metrics event buffered for later export."""

    name: str
    value: float | int
    labels: dict[str, str] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MetricsRingBuffer:
    """Fixed-size ring buffer for metrics events.

    When the buffer exceeds ``max_size`` the oldest events are discarded.
    Thread-safe for concurrent append / snapshot calls.
    """

    def __init__(self, max_size: int = 1000) -> None:
        self._max_size = max_size
        self._buffer: deque[MetricsEvent] = deque(maxlen=max_size)
        self._lock = threading.Lock()

    def append(self, event: MetricsEvent) -> None:
        """Append an event, discarding oldest if buffer is full."""
        with self._lock:
            self._buffer.append(event)

    def snapshot(self) -> list[MetricsEvent]:
        """Return a snapshot of current events (oldest → newest)."""
        with self._lock:
            return list(self._buffer)

    def clear(self) -> None:
        """Remove all buffered events."""
        with self._lock:
            self._buffer.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)

    def flush_to_exporter(
        self,
        exporter: Any,
    ) -> bool:
        """Flush buffered events to the given exporter.

        The exporter must expose an ``export(events: list[MetricsEvent])``
        method.  Returns ``True`` on success, ``False`` on failure.
        """
        events = self.snapshot()
        if not events:
            return True
        try:
            exporter.export(events)
            self.clear()
            return True
        except Exception:  # noqa: BLE001 — best-effort flush
            return False
