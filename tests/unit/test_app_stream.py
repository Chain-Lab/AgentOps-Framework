"""Tests for AgentApp.run() with session support and AgentApp.stream()."""

import pytest

from agent_app import AgentApp, AgentSpec, ToolSpec, Workflow, tool
from agent_app.core.context import RunContext
from agent_app.runtime.session import InMemorySessionStore


@pytest.fixture
def app_with_session() -> AgentApp:
    store = InMemorySessionStore()
    app = AgentApp(session_store=store)
    app.register_agent(AgentSpec(name="support", instructions="Helpful"))
    app.register_workflow(Workflow.single(agent="support", name="cs"))
    return app


class TestAppRunWithSession:
    @pytest.mark.asyncio
    async def test_run_without_session(self, app_with_session) -> None:
        result = await app_with_session.run(
            agent="support", input="hello", user_id="u1", tenant_id="t1"
        )
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_run_with_session_appends(self, app_with_session) -> None:
        store = app_with_session.session_store
        assert store is not None

        await app_with_session.run(
            agent="support",
            input="first message",
            user_id="u1",
            tenant_id="t1",
            session_id="s1",
        )

        items = await store.get_items("s1")
        assert len(items) == 2
        assert items[0]["role"] == "user"
        assert items[0]["content"] == "first message"
        assert items[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_multiple_runs_same_session(self, app_with_session) -> None:
        store = app_with_session.session_store
        assert store is not None

        await app_with_session.run(
            agent="support", input="msg1",
            user_id="u1", tenant_id="t1", session_id="s1",
        )
        await app_with_session.run(
            agent="support", input="msg2",
            user_id="u1", tenant_id="t1", session_id="s1",
        )

        items = await store.get_items("s1")
        assert len(items) == 4  # 2 exchanges
        assert items[0]["content"] == "msg1"
        assert items[2]["content"] == "msg2"

    @pytest.mark.asyncio
    async def test_different_sessions_isolated(self, app_with_session) -> None:
        store = app_with_session.session_store
        assert store is not None

        await app_with_session.run(
            agent="support", input="a",
            user_id="u1", tenant_id="t1", session_id="s1",
        )
        await app_with_session.run(
            agent="support", input="b",
            user_id="u2", tenant_id="t1", session_id="s2",
        )

        assert len(await store.get_items("s1")) == 2
        assert len(await store.get_items("s2")) == 2


class TestAppStream:
    @pytest.mark.asyncio
    async def test_stream_returns_events(self, app_with_session) -> None:
        events = []
        async for ev in app_with_session.stream(
            agent="support", input="hi", user_id="u1", tenant_id="t1"
        ):
            events.append(ev)

        assert len(events) >= 3
        types = [e.type for e in events]
        assert "run.started" in types
        assert "text.delta" in types
        assert "run.completed" in types

    @pytest.mark.asyncio
    async def test_stream_appends_to_session(self, app_with_session) -> None:
        store = app_with_session.session_store
        assert store is not None

        async for _ev in app_with_session.stream(
            agent="support",
            input="streamed message",
            user_id="u1",
            tenant_id="t1",
            session_id="s_stream",
        ):
            pass

        items = await store.get_items("s_stream")
        assert len(items) == 2
        assert items[0]["content"] == "streamed message"
