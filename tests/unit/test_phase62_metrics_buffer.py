"""Phase 62 Task 3: Metrics ring buffer tests."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from agent_app.runtime.policy_rollout_federation_notification_metrics_buffer import (
    MetricsEvent,
    MetricsRingBuffer,
)


class TestMetricsRingBuffer:
    """Tests for MetricsRingBuffer."""

    def test_append_and_len(self):
        """append increases len, clear resets to 0."""
        buf = MetricsRingBuffer(max_size=5)
        assert len(buf) == 0
        buf.append(MetricsEvent(name="m1", value=1.0))
        assert len(buf) == 1
        buf.append(MetricsEvent(name="m2", value=2.0))
        assert len(buf) == 2
        buf.clear()
        assert len(buf) == 0

    def test_max_size_discards_oldest(self):
        """When buffer exceeds max_size, oldest events are discarded."""
        buf = MetricsRingBuffer(max_size=3)
        for i in range(5):
            buf.append(MetricsEvent(name=f"m{i}", value=float(i)))
        assert len(buf) == 3
        names = [e.name for e in buf.snapshot()]
        assert names == ["m2", "m3", "m4"]

    def test_snapshot_returns_list(self):
        """snapshot returns a list of MetricsEvent objects."""
        buf = MetricsRingBuffer(max_size=10)
        buf.append(MetricsEvent(name="m1", value=1.0))
        buf.append(MetricsEvent(name="m2", value=2.0))
        snap = buf.snapshot()
        assert isinstance(snap, list)
        assert len(snap) == 2
        assert snap[0].name == "m1"
        assert snap[1].name == "m2"

    def test_snapshot_is_copy(self):
        """snapshot returns a copy, not the internal buffer."""
        buf = MetricsRingBuffer(max_size=10)
        buf.append(MetricsEvent(name="m1", value=1.0))
        snap = buf.snapshot()
        snap.append(MetricsEvent(name="m2", value=2.0))
        assert len(buf) == 1

    def test_empty_snapshot(self):
        """snapshot on empty buffer returns empty list."""
        buf = MetricsRingBuffer(max_size=10)
        assert buf.snapshot() == []

    def test_thread_safety_append(self):
        """Concurrent appends do not corrupt buffer."""
        buf = MetricsRingBuffer(max_size=1000)
        errors = []

        def _append(n):
            try:
                for i in range(100):
                    buf.append(MetricsEvent(name=f"t{n}_m{i}", value=float(i)))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_append, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(buf) == 1000

    def test_thread_safety_snapshot(self):
        """Concurrent snapshots do not corrupt buffer."""
        buf = MetricsRingBuffer(max_size=100)
        for i in range(100):
            buf.append(MetricsEvent(name=f"m{i}", value=float(i)))

        errors = []

        def _snapshot():
            try:
                for _ in range(50):
                    snap = buf.snapshot()
                    assert len(snap) <= 100
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_snapshot) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

    def test_flush_to_exporter_empty(self):
        """flush_to_exporter returns True for empty buffer."""
        buf = MetricsRingBuffer(max_size=10)
        exporter = MagicMock()
        assert buf.flush_to_exporter(exporter) is True
        exporter.export.assert_not_called()

    def test_flush_to_exporter_success(self):
        """flush_to_exporter calls exporter.export and clears buffer."""
        buf = MetricsRingBuffer(max_size=10)
        buf.append(MetricsEvent(name="m1", value=1.0))
        buf.append(MetricsEvent(name="m2", value=2.0))
        exporter = MagicMock()
        result = buf.flush_to_exporter(exporter)
        assert result is True
        exporter.export.assert_called_once()
        events = exporter.export.call_args[0][0]
        assert len(events) == 2
        assert len(buf) == 0

    def test_flush_to_exporter_failure(self):
        """flush_to_exporter returns False on exception, does not clear."""
        buf = MetricsRingBuffer(max_size=10)
        buf.append(MetricsEvent(name="m1", value=1.0))
        exporter = MagicMock()
        exporter.export = MagicMock(side_effect=RuntimeError("export failed"))
        result = buf.flush_to_exporter(exporter)
        assert result is False
        assert len(buf) == 1  # not cleared on failure

    def test_event_timestamp_default(self):
        """MetricsEvent timestamp defaults to now (UTC)."""
        before = datetime.now(timezone.utc)
        event = MetricsEvent(name="m1", value=1.0)
        after = datetime.now(timezone.utc)
        assert before <= event.timestamp <= after
        assert event.timestamp.tzinfo is not None

    def test_event_labels_default(self):
        """MetricsEvent labels default to empty dict."""
        event = MetricsEvent(name="m1", value=1.0)
        assert event.labels == {}

    def test_event_with_labels(self):
        """MetricsEvent stores custom labels."""
        event = MetricsEvent(name="m1", value=1.0, labels={"env": "prod"})
        assert event.labels == {"env": "prod"}
