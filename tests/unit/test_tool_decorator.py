"""Tests for the @tool decorator."""

import pytest

from agent_app.core.tool_spec import ToolSpec
from agent_app.registry.tool_registry import ToolRegistry
from agent_app.tools.decorator import tool as tool_decorator


class TestToolDecorator:
    def test_decorator_registers_globally(self) -> None:
        """@tool should register into the global default registry."""
        from agent_app.tools.decorator import get_default_registry

        reg = get_default_registry()
        reg.clear()

        @tool_decorator(name="test.tool", description="A test tool")
        async def my_tool(x: str) -> dict:
            return {"result": x}

        assert reg.exists("test.tool")
        spec = reg.get_spec("test.tool")
        assert spec.name == "test.tool"
        assert spec.description == "A test tool"
        assert spec.namespace == "test"
        assert spec.risk_level == "low"

        # Cleanup
        reg.clear()

    def test_decorator_preserves_function(self) -> None:
        """The decorated function should be callable."""
        from agent_app.tools.decorator import get_default_registry

        reg = get_default_registry()
        reg.clear()

        @tool_decorator(name="test.tool2", description="Test")
        async def my_tool2(x: str) -> dict:
            """Original docstring."""
            return {"result": x}

        assert callable(my_tool2)
        assert my_tool2.__name__ == "my_tool2"

        # _tool_spec attribute should be set
        assert hasattr(my_tool2, "_tool_spec")
        assert isinstance(my_tool2._tool_spec, ToolSpec)

        reg.clear()

    def test_decorator_default_docstring(self) -> None:
        """If no description given, use function docstring."""
        from agent_app.tools.decorator import get_default_registry

        reg = get_default_registry()
        reg.clear()

        @tool_decorator(name="test.tool3")
        async def my_tool3(x: str) -> dict:
            """My docstring."""
            return {}

        assert reg.get_spec("test.tool3").description == "My docstring."
        reg.clear()

    def test_decorator_risk_levels(self) -> None:
        """Test different risk levels."""
        from agent_app.tools.decorator import get_default_registry

        reg = get_default_registry()
        reg.clear()

        @tool_decorator(name="low.tool", description="Low", risk_level="low")
        async def low_tool() -> dict:
            return {}

        @tool_decorator(name="high.tool", description="High", risk_level="high", requires_approval=True)
        async def high_tool() -> dict:
            return {}

        assert reg.get_spec("low.tool").risk_level == "low"
        assert reg.get_spec("high.tool").risk_level == "high"
        assert reg.get_spec("high.tool").requires_approval is True

        reg.clear()

    def test_decorator_permissions(self) -> None:
        """Test permissions are stored correctly."""
        from agent_app.tools.decorator import get_default_registry

        reg = get_default_registry()
        reg.clear()

        @tool_decorator(
            name="perm.tool",
            description="Tool with perms",
            permissions=["order:read", "order:write"],
        )
        async def perm_tool() -> dict:
            return {}

        assert reg.get_spec("perm.tool").permissions == ["order:read", "order:write"]
        reg.clear()
