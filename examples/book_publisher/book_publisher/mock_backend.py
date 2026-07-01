"""Deterministic mock LLM backend — no network I/O, fully offline.

Implements the framework's AgentBackend Protocol (agent_app/runtime/backends.py)
directly. A real backend (e.g. an LMStudio OpenAI-compatible server) would read
`agent_spec.instructions` as its system prompt instead; this mock reads the same
persona traits from `agent_spec.metadata` so output stays deterministic for
tests and evals. Swapping to a real backend is a one-line change in
build_app.py's `backend=` argument — the rest of the pipeline is backend-agnostic.
"""

from __future__ import annotations

from typing import AsyncGenerator

from agent_app.core.agent_spec import AgentSpec
from agent_app.core.context import RunContext
from agent_app.core.result import AppRunResult
from agent_app.runtime.streaming import StreamEvent, StreamEventType


class MockPersonaBackend:
    """Renders persona-flavored book descriptions with zero network calls."""

    def _render(self, agent_spec: AgentSpec, input: str) -> str:
        """Stable output shape `[reading_level | tone] book_text (extra)`, sliced to max_length.

        Tests assert against this exact shape — changing it is a breaking change.
        """
        meta = agent_spec.metadata
        tone = meta.get("tone", "")
        reading_level = meta.get("reading_level", "")
        max_length = meta.get("max_length", 280)
        extra = meta.get("extra_instructions", "")

        book_text = " ".join(input.strip().split())
        rendered = f"[{reading_level} | {tone}] {book_text}"
        if extra:
            rendered = f"{rendered} ({extra})"
        return rendered[:max_length]

    async def run(
        self,
        agent_spec: AgentSpec,
        input: str,
        context: RunContext,
        tools: list[object] | None = None,
        **kwargs: object,
    ) -> AppRunResult:
        text = self._render(agent_spec, input)
        return AppRunResult(
            run_id=context.run_id,
            status="completed",
            final_output=text,
            latency_ms=0,
        )

    async def stream(
        self,
        agent_spec: AgentSpec,
        input: str,
        context: RunContext,
        tools: list[object] | None = None,
        **kwargs: object,
    ) -> AsyncGenerator[StreamEvent, None]:
        text = self._render(agent_spec, input)
        yield StreamEvent(type=StreamEventType.RUN_STARTED, run_id=context.run_id)

        chunk_size = 16
        for i in range(0, len(text), chunk_size):
            yield StreamEvent(
                type=StreamEventType.TEXT_DELTA,
                run_id=context.run_id,
                delta=text[i : i + chunk_size],
            )

        yield StreamEvent(
            type=StreamEventType.RUN_COMPLETED,
            run_id=context.run_id,
            data={"final_output": text},
        )

    async def resume(
        self,
        agent_spec: AgentSpec,
        context: RunContext,
        **kwargs: object,
    ) -> AppRunResult:
        return AppRunResult(
            run_id=context.run_id,
            status="completed",
            final_output=(
                f"Run '{context.run_id}' resumed. "
                "(MockPersonaBackend has no interruptible state to resume.)"
            ),
            latency_ms=0,
        )
