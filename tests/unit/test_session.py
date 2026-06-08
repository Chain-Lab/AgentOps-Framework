"""Tests for session store implementations."""

import os
import tempfile

import pytest

from agent_app.runtime.session import InMemorySessionStore, SQLiteSessionStore
from agent_app.runtime.session_manager import create_session_store


class TestInMemorySessionStore:
    @pytest.mark.asyncio
    async def test_empty_session_returns_empty_list(self) -> None:
        store = InMemorySessionStore()
        assert await store.get_items("s1") == []

    @pytest.mark.asyncio
    async def test_add_and_get_items(self) -> None:
        store = InMemorySessionStore()
        await store.add_items("s1", [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ])
        items = await store.get_items("s1")
        assert len(items) == 2
        assert items[0]["role"] == "user"
        assert items[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_clear_session(self) -> None:
        store = InMemorySessionStore()
        await store.add_items("s1", [{"role": "user", "content": "hi"}])
        await store.clear_session("s1")
        assert await store.get_items("s1") == []

    @pytest.mark.asyncio
    async def test_clear_nonexistent_is_noop(self) -> None:
        store = InMemorySessionStore()
        await store.clear_session("nonexistent")  # should not raise

    @pytest.mark.asyncio
    async def test_multiple_sessions_isolated(self) -> None:
        store = InMemorySessionStore()
        await store.add_items("s1", [{"role": "user", "content": "a"}])
        await store.add_items("s2", [{"role": "user", "content": "b"}])
        assert len(await store.get_items("s1")) == 1
        assert len(await store.get_items("s2")) == 1

    @pytest.mark.asyncio
    async def test_append_preserves_order(self) -> None:
        store = InMemorySessionStore()
        await store.add_items("s1", [{"role": "user", "content": "first"}])
        await store.add_items("s1", [{"role": "assistant", "content": "second"}])
        items = await store.get_items("s1")
        assert items[0]["content"] == "first"
        assert items[1]["content"] == "second"


class TestSQLiteSessionStore:
    @pytest.fixture
    def db_path(self, tmp_path) -> str:
        return str(tmp_path / "test_sessions.db")

    @pytest.mark.asyncio
    async def test_creates_table(self, db_path) -> None:
        store = SQLiteSessionStore(db_path=db_path)
        assert os.path.exists(db_path)
        store.close()

    @pytest.mark.asyncio
    async def test_add_and_get_items(self, db_path) -> None:
        store = SQLiteSessionStore(db_path=db_path)
        await store.add_items("s1", [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ])
        items = await store.get_items("s1")
        assert len(items) == 2
        assert items[0]["role"] == "user"
        store.close()

    @pytest.mark.asyncio
    async def test_clear_session(self, db_path) -> None:
        store = SQLiteSessionStore(db_path=db_path)
        await store.add_items("s1", [{"role": "user", "content": "hi"}])
        await store.clear_session("s1")
        assert await store.get_items("s1") == []
        store.close()

    @pytest.mark.asyncio
    async def test_persistence_across_instances(self, db_path) -> None:
        store1 = SQLiteSessionStore(db_path=db_path)
        await store1.add_items("s1", [{"role": "user", "content": "persistent"}])
        store1.close()

        store2 = SQLiteSessionStore(db_path=db_path)
        items = await store2.get_items("s1")
        assert len(items) == 1
        assert items[0]["content"] == "persistent"
        store2.close()


class TestCreateSessionStore:
    def test_memory_default(self) -> None:
        store = create_session_store()
        assert isinstance(store, InMemorySessionStore)

    def test_memory_explicit(self) -> None:
        store = create_session_store("memory")
        assert isinstance(store, InMemorySessionStore)

    def test_sqlite(self, tmp_path) -> None:
        db = str(tmp_path / "s.db")
        store = create_session_store("sqlite", db_path=db)
        assert isinstance(store, SQLiteSessionStore)
        store.close()

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown"):
            create_session_store("redis")
