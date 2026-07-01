"""Tests for book_publisher.mock_backend.MockPersonaBackend."""

from __future__ import annotations

import sys
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "book_publisher"
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

from agent_app.core.agent_spec import AgentSpec
from agent_app.core.context import RunContext
from agent_app.runtime.backends import AgentBackend

from book_publisher.mock_backend import MockPersonaBackend


def _agent_spec(name: str, **metadata) -> AgentSpec:
    return AgentSpec(
        name=name,
        instructions="write a book description",
        metadata={
            "tone": "playful",
            "reading_level": "early elementary",
            "max_length": 60,
            "extra_instructions": "",
            **metadata,
        },
    )


def test_mock_backend_satisfies_agent_backend_protocol():
    backend = MockPersonaBackend()
    assert isinstance(backend, AgentBackend)


async def test_run_produces_deterministic_output():
    backend = MockPersonaBackend()
    spec = _agent_spec("book_writer__children")
    context = RunContext(run_id="r1", user_id="u1", tenant_id="t1")

    result1 = await backend.run(spec, "Title: Deep Echo\nSummary: ...", context)
    result2 = await backend.run(spec, "Title: Deep Echo\nSummary: ...", context)

    assert result1.status == "completed"
    assert result1.final_output == result2.final_output


async def test_run_output_differs_by_persona_traits():
    backend = MockPersonaBackend()
    context = RunContext(run_id="r1", user_id="u1", tenant_id="t1")
    input_text = "Title: Deep Echo\nSummary: A crew finds an ancient signal."

    children_spec = _agent_spec("book_writer__children", tone="playful", max_length=60)
    adult_spec = _agent_spec("book_writer__adult", tone="measured", max_length=200)

    children_result = await backend.run(children_spec, input_text, context)
    adult_result = await backend.run(adult_spec, input_text, context)

    assert children_result.final_output != adult_result.final_output
    assert len(children_result.final_output) <= 60
    assert len(adult_result.final_output) <= 200


async def test_run_respects_max_length_truncation():
    backend = MockPersonaBackend()
    spec = _agent_spec("book_writer__children", max_length=20)
    context = RunContext(run_id="r1", user_id="u1", tenant_id="t1")

    result = await backend.run(spec, "Title: Deep Echo\nSummary: " + ("x" * 500), context)
    assert len(result.final_output) <= 20


async def test_stream_yields_run_started_and_completed_events():
    from agent_app.runtime.streaming import StreamEventType

    backend = MockPersonaBackend()
    spec = _agent_spec("book_writer__adult")
    context = RunContext(run_id="r1", user_id="u1", tenant_id="t1")

    events = [event async for event in backend.stream(spec, "Title: Deep Echo", context)]
    assert events[0].type == StreamEventType.RUN_STARTED
    assert events[-1].type == StreamEventType.RUN_COMPLETED


async def test_resume_returns_completed_result():
    backend = MockPersonaBackend()
    spec = _agent_spec("book_writer__adult")
    context = RunContext(run_id="r1", user_id="u1", tenant_id="t1")

    result = await backend.resume(spec, context)
    assert result.status == "completed"
