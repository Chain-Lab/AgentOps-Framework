"""Tool decorator — the @tool annotation for defining framework tools."""

from __future__ import annotations

import functools
from typing import Any, Callable

from agent_app.core.tool_spec import ToolSpec


def tool(
    name: str,
    description: str = "",
    namespace: str = "",
    risk_level: str = "low",
    requires_approval: bool = False,
    permissions: list[str] | None = None,
    timeout_seconds: int = 30,
    audit_enabled: bool = True,
    tags: list[str] | None = None,
) -> Callable:
    """Decorator that marks a function as a framework tool.

    The decorated function is registered into the default global
    :class:`ToolRegistry` automatically.

    Args:
        name: Fully-qualified tool name using dot-notation (e.g. "order.query").
        description: Human-readable description (defaults to the function docstring).
        namespace: Logical grouping.  Auto-derived from *name* if empty.
        risk_level: "low" | "medium" | "high" | "critical".
        requires_approval: If True, execution pauses for human approval.
        permissions: Required permission strings.
        timeout_seconds: Max execution time in seconds.
        audit_enabled: Whether to log this tool call.
        tags: Free-form tags.

    Returns:
        Decorator that wraps the function and registers it.

    Example::

        @tool(name="order.query", description="Query an order", risk_level="low")
        async def query_order(order_id: str) -> dict:
            return {"order_id": order_id, "status": "paid"}
    """

    def decorator(fn: Callable) -> Callable:
        spec = ToolSpec(
            name=name,
            description=description or (fn.__doc__ or "").strip(),
            namespace=namespace,
            risk_level=risk_level,
            requires_approval=requires_approval,
            permissions=permissions or [],
            timeout_seconds=timeout_seconds,
            audit_enabled=audit_enabled,
            tags=tags or [],
        )

        # Register into the global default registry lazily.
        _get_default_registry().register(name, spec, fn=fn)

        # Preserve the original function's metadata.
        wrapped = functools.wraps(fn)(fn)
        wrapped._tool_spec = spec  # type: ignore[attr-defined]
        return wrapped

    return decorator


# ---------------------------------------------------------------------------
# Lazy-initialised global default registry
# ---------------------------------------------------------------------------

_default_registry: Any = None


def _get_default_registry() -> Any:
    global _default_registry
    if _default_registry is None:
        from agent_app.registry.tool_registry import ToolRegistry

        _default_registry = ToolRegistry()
    return _default_registry


# Monkey-patch so the module-level call works correctly.
# The actual _default_registry reference inside tool() is resolved at
# decoration time, so we keep the original logic there.
# This re-export is for convenience.
def get_default_registry() -> Any:
    """Return the global default ToolRegistry."""
    return _get_default_registry()
