"""Streaming — event types, models, and async generators for run streaming."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, AsyncGenerator

from pydantic import BaseModel, Field


class StreamEventType(StrEnum):
    """Standard streaming event types."""

    RUN_STARTED = "run.started"
    TEXT_DELTA = "text.delta"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    APPROVAL_REQUIRED = "approval.required"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


class StreamEvent(BaseModel):
    """A single streaming event emitted during a run.

    Attributes:
        type: Event type from :class:`StreamEventType`.
        run_id: Unique run identifier.
        delta: Text delta (for TEXT_DELTA events).
        data: Arbitrary event-specific payload.
    """

    type: str = Field(..., description="Event type")
    run_id: str | None = Field(default=None, description="Run identifier")
    delta: str | None = Field(default=None, description="Text fragment")
    data: dict[str, Any] = Field(default_factory=dict, description="Extra payload")

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict (useful for SSE / JSON responses)."""
        d: dict[str, Any] = {"type": self.type}
        if self.run_id is not None:
            d["run_id"] = self.run_id
        if self.delta is not None:
            d["delta"] = self.delta
        if self.data:
            d["data"] = self.data
        return d


async def stream_events(
    events: list[StreamEvent],
) -> AsyncGenerator[StreamEvent, None]:
    """Yield *events* one at a time with a tiny delay to simulate streaming.

    Used by DryRunBackend to produce a realistic streaming experience
    without depending on a real LLM.
    """
    import asyncio

    for event in events:
        yield event
        await asyncio.sleep(0.01)
