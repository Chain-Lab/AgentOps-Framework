"""Backend protocol and built-in backend implementations."""

from __future__ import annotations

from typing import AsyncGenerator

from agent_app.core.agent_spec import AgentSpec
from agent_app.core.context import RunContext
from agent_app.core.result import AppRunResult
from agent_app.runtime.streaming import StreamEvent, StreamEventType, stream_events

try:
    from typing import Protocol, runtime_checkable
except ImportError:
    from typing import Protocol
    def runtime_checkable(cls):  # type: ignore[misc]
        return cls


@runtime_checkable
class AgentBackend(Protocol):
    """Protocol that all execution backends must implement."""

    async def run(
        self,
        agent_spec: AgentSpec,
        input: str,
        context: RunContext,
        tools: list[object] | None = None,
        **kwargs: object,
    ) -> AppRunResult:
        ...

    async def stream(
        self,
        agent_spec: AgentSpec,
        input: str,
        context: RunContext,
        tools: list[object] | None = None,
        **kwargs: object,
    ) -> AsyncGenerator[StreamEvent, None]:
        ...

    async def resume(
        self,
        agent_spec: AgentSpec,
        context: RunContext,
        **kwargs: object,
    ) -> AppRunResult:
        """Resume a previously interrupted run.

        The default implementation returns a stub indicating that
        resume is not supported by this backend.
        """
        ...


class DryRunBackend:
    """No-op backend that echoes input without calling any SDK.

    Useful for local development, unit tests, and smoke-testing.
    """

    async def run(
        self,
        agent_spec: AgentSpec,
        input: str,
        context: RunContext,
        tools: list[object] | None = None,
        **kwargs: object,
    ) -> AppRunResult:
        from agent_app.core.result import AppRunResult

        tool_names = [t.name if hasattr(t, "name") else str(t) for t in (tools or [])]
        return AppRunResult(
            run_id=context.run_id,
            status="completed",
            final_output=f"[dry-run] Agent '{agent_spec.name}' received: {input}",
            tool_calls=[
                {
                    "tool": name,
                    "status": "dry_run",
                    "arguments": {},
                }
                for name in tool_names
            ],
            latency_ms=0,
            trace_id=None,
        )

    async def stream(
        self,
        agent_spec: AgentSpec,
        input: str,
        context: RunContext,
        tools: list[object] | None = None,
        **kwargs: object,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Yield streaming events simulating a real LLM response."""
        response = f"[dry-run] Agent '{agent_spec.name}' received: {input}"
        # Split into chunks to simulate token streaming.
        chunk_size = 8
        chunks = [response[i : i + chunk_size] for i in range(0, len(response), chunk_size)]

        yield StreamEvent(
            type=StreamEventType.RUN_STARTED,
            run_id=context.run_id,
        )

        for chunk in chunks:
            yield StreamEvent(
                type=StreamEventType.TEXT_DELTA,
                run_id=context.run_id,
                delta=chunk,
            )

        tool_names = [t.name if hasattr(t, "name") else str(t) for t in (tools or [])]
        for name in tool_names:
            yield StreamEvent(
                type=StreamEventType.TOOL_COMPLETED,
                run_id=context.run_id,
                data={"tool": name, "status": "dry_run"},
            )

        yield StreamEvent(
            type=StreamEventType.RUN_COMPLETED,
            run_id=context.run_id,
            data={"final_output": response},
        )

    async def resume(
        self,
        agent_spec: AgentSpec,
        context: RunContext,
        **kwargs: object,
    ) -> AppRunResult:
        """Resume a DryRunBackend run — returns a stub result."""
        from agent_app.core.result import AppRunResult
        return AppRunResult(
            run_id=context.run_id,
            status="completed",
            final_output=(
                f"Run '{context.run_id}' approved and resumed. "
                f"(DryRunBackend — framework-level resume stub.)"
            ),
            latency_ms=0,
        )
