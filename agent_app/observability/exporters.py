"""JSONL trace collector — appends events to a JSONL file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_app.observability.collector import TraceCollector
from agent_app.observability.events import RunEvent, RunEventType


class JSONLTraceCollector:
    """Appends run events to a JSONL file, one JSON object per line.

    Also supports reading events back for querying via get_events()
    and list_traces(). Suitable for local debugging and log-based
    observability pipelines.

    Args:
        path: File path for the JSONL output. Parent directories
              are created automatically.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Write empty file if it doesn't exist yet.
        if not self._path.exists():
            self._path.touch()

    async def record(self, event: RunEvent) -> None:
        """Append a single event as a JSON line."""
        line = json.dumps(event.model_dump(mode="json"), default=str)
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    async def get_events(self, trace_id: str) -> list[RunEvent]:
        """Read all events for a trace from the JSONL file.

        Returns events ordered by timestamp ascending.
        Only loads events matching the given trace_id.
        """
        events: list[RunEvent] = []
        if not self._path.exists():
            return events
        with open(self._path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if raw.get("trace_id") == trace_id:
                    events.append(self._deserialize(raw))
        return sorted(events, key=lambda e: e.timestamp)

    async def list_traces(
        self,
        tenant_id: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> list[str]:
        """List trace IDs from the JSONL file, optionally filtered.

        Reads the entire file each time — acceptable for local
        debugging with modest file sizes.
        """
        seen: dict[str, bool] = {}
        if not self._path.exists():
            return []
        with open(self._path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if tenant_id is not None and raw.get("tenant_id") != tenant_id:
                    continue
                if run_id is not None and raw.get("run_id") != run_id:
                    continue
                tid = raw.get("trace_id")
                if tid and tid not in seen:
                    seen[tid] = True
                    if len(seen) >= limit:
                        break
        return list(seen.keys())

    async def count_events(self) -> int:
        """Count total events in the JSONL file (valid lines only)."""
        if not self._path.exists():
            return 0
        count = 0
        with open(self._path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    json.loads(line)
                    count += 1
                except json.JSONDecodeError:
                    continue
        return count

    async def count_traces(self) -> int:
        """Count distinct trace_ids in the JSONL file."""
        if not self._path.exists():
            return 0
        seen: set[str] = set()
        with open(self._path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tid = raw.get("trace_id")
                if tid:
                    seen.add(tid)
        return len(seen)

    async def compact(
        self,
        output_path: str | Path | None = None,
        max_events_per_trace: int | None = None,
    ) -> Path:
        """Write a compacted copy of the JSONL file.

        When ``max_events_per_trace`` is set, only the most recent N events
        per trace_id are kept (sorted by timestamp).

        Args:
            output_path: Where to write the compacted file. If None, writes
                         to a sibling file with ``.compact`` suffix and then
                         atomically replaces the original.
            max_events_per_trace: Max events to keep per trace. None = keep all.

        Returns:
            Path to the written compacted file.
        """
        target = Path(output_path) if output_path is not None else self._path

        # Read all events grouped by trace_id
        events_by_trace: dict[str, list[dict]] = {}
        if self._path.exists():
            with open(self._path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    tid = raw.get("trace_id", "")
                    events_by_trace.setdefault(tid, []).append(raw)

        # Trim per trace if limit is set
        if max_events_per_trace is not None:
            for tid in events_by_trace:
                events = events_by_trace[tid]
                events.sort(key=lambda r: r.get("timestamp", ""))
                if len(events) > max_events_per_trace:
                    events_by_trace[tid] = events[-max_events_per_trace:]

        # Write output
        write_path = Path(target)
        if output_path is None:
            # Write to temp sibling file then atomic rename
            tmp_path = write_path.with_suffix(".tmp")
        else:
            tmp_path = write_path

        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as fh:
            for tid in sorted(events_by_trace):
                for raw in events_by_trace[tid]:
                    fh.write(json.dumps(raw, default=str) + "\n")

        if output_path is None:
            tmp_path.replace(write_path)

        return write_path

    @staticmethod
    def _deserialize(raw: dict[str, Any]) -> RunEvent:
        """Reconstruct a RunEvent from a JSON-decoded dict."""
        raw["event_type"] = RunEventType(raw["event_type"])
        if raw.get("timestamp"):
            from datetime import datetime, timezone
            ts = raw["timestamp"]
            if isinstance(ts, str):
                raw["timestamp"] = datetime.fromisoformat(ts)
        return RunEvent.model_validate(raw)
