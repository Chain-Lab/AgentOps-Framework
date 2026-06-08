"""Function registry for DAG FUNCTION nodes (Phase 13.4).

Provides a registry for deterministic Python functions that can be
invoked as DAG nodes. Functions must be explicitly registered — no
arbitrary evaluation is allowed.

Supports sync and async functions. Provides a ``@workflow_function``
decorator for convenient registration.
"""

from __future__ import annotations

import asyncio
import functools
from enum import StrEnum
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class FunctionRegistryError(Exception):
    """Raised when a function registry operation fails."""


class FunctionNotFoundError(FunctionRegistryError):
    """Raised when a requested function is not in the registry."""


class DuplicateFunctionError(FunctionRegistryError):
    """Raised when registering a function with a name that already exists."""


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


F = TypeVar("F", bound=Callable[..., Any])


class WorkflowFunction(BaseModel):
    """Metadata wrapper for a registered workflow function.

    Attributes:
        name: Dot-separated registry key (e.g. ``"refund.calculate_amount"``).
        func: The callable (sync or async).
        description: Human-readable description.
        permissions: Permissions required to execute this function.
        risk_level: Risk classification ("low", "medium", "high").
        requires_approval: Whether this function requires human approval.
        timeout_seconds: Default timeout for this function (None = no timeout).
        metadata: Arbitrary extra metadata.
    """

    name: str = Field(..., description="Registry key")
    func: Callable[..., Any] = Field(..., description="The callable")
    description: str | None = Field(default=None, description="Human-readable description")
    permissions: list[str] = Field(
        default_factory=list, description="Required permissions"
    )
    risk_level: str = Field(default="low", description="Risk classification")
    requires_approval: bool = Field(
        default=False, description="Whether approval is required"
    )
    timeout_seconds: float | None = Field(
        default=None, ge=0.0, description="Default timeout (seconds)"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Extra metadata"
    )

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class FunctionRegistry:
    """Registry of deterministic Python functions for DAG FUNCTION nodes.

    Functions are looked up by a dot-separated name (e.g.
    ``"refund.calculate_amount"``) that is referenced from YAML config.

    Example::

        registry = FunctionRegistry()
        registry.register("hello.greet", lambda name: {"greeting": f"Hi {name}"})
        entry = registry.get("hello.greet")
    """

    def __init__(self) -> None:
        self._functions: dict[str, WorkflowFunction] = {}

    def register(
        self,
        name: str,
        func: Callable[..., Any],
        description: str | None = None,
        permissions: list[str] | None = None,
        risk_level: str = "low",
        requires_approval: bool = False,
        timeout_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowFunction:
        """Register a function.

        Args:
            name: Dot-separated registry key.
            func: The callable to register.
            description: Optional human-readable description.
            permissions: Optional list of required permissions.
            risk_level: Risk classification ("low", "medium", "high").
            requires_approval: Whether this function requires human approval.
            timeout_seconds: Optional default timeout in seconds.
            metadata: Optional extra metadata dict.

        Returns:
            The created WorkflowFunction entry.

        Raises:
            DuplicateFunctionError: If a function with this name already exists.
        """
        if name in self._functions:
            raise DuplicateFunctionError(
                f"Function '{name}' is already registered. "
                f"Use a different name or unregister first."
            )
        entry = WorkflowFunction(
            name=name,
            func=func,
            description=description,
            permissions=permissions or [],
            risk_level=risk_level,
            requires_approval=requires_approval,
            timeout_seconds=timeout_seconds,
            metadata=metadata or {},
        )
        self._functions[name] = entry
        return entry

    def unregister(self, name: str) -> None:
        """Remove a function from the registry.

        Silently ignored if the function is not registered.
        """
        self._functions.pop(name, None)

    def get(self, name: str) -> WorkflowFunction:
        """Get a registered function by name.

        Args:
            name: Dot-separated registry key.

        Returns:
            The WorkflowFunction entry.

        Raises:
            FunctionNotFoundError: If no function with this name is registered.
        """
        try:
            return self._functions[name]
        except KeyError:
            raise FunctionNotFoundError(
                f"Function '{name}' not found in function registry. "
                f"Register it with @workflow_function(name='{name}') or "
                f"registry.register('{name}', fn)."
            )

    def exists(self, name: str) -> bool:
        """Check if a function is registered."""
        return name in self._functions

    def list(self) -> list[str]:
        """List all registered function names."""
        return sorted(self._functions.keys())

    def clear(self) -> None:
        """Remove all registered functions. Primarily for testing."""
        self._functions.clear()


__all__ = [
    "DuplicateFunctionError",
    "FunctionNotFoundError",
    "FunctionRegistry",
    "FunctionRegistryError",
    "WorkflowFunction",
    "_call_function",
    "_normalize_output",
    "get_default_function_registry",
    "workflow_function",
]


# ---------------------------------------------------------------------------
# Default global registry
# ---------------------------------------------------------------------------

_default_registry: FunctionRegistry | None = None


def get_default_function_registry() -> FunctionRegistry:
    """Return the global default function registry (singleton)."""
    global _default_registry
    if _default_registry is None:
        _default_registry = FunctionRegistry()
    return _default_registry


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def workflow_function(
    name: str | Callable[..., Any] | None = None,
    description: str | None = None,
    permissions: list[str] | None = None,
    risk_level: str = "low",
    requires_approval: bool = False,
    timeout_seconds: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[..., Any] | Callable[[F], F]:
    """Decorator to register a Python function as a DAG workflow function.

    The decorated function is registered in the default global registry
    and the original function remains fully callable.

    Supports both sync and async functions. Supports both positional-name
    and keyword-argument calling conventions.

    Examples::

        # New style with governance metadata
        @workflow_function(
            name="refund.calculate_amount",
            description="Calculate refund amount",
            permissions=["refund:calculate"],
            risk_level="medium",
        )
        def calculate_refund(order_total: float) -> dict:
            return {"amount": order_total * 0.9}

        # Old style: positional name (backward compatible)
        @workflow_function("order.extract_order_id")
        def extract_order_id(text: str) -> dict:
            ...

        # Auto-name from function __name__
        @workflow_function
        def my_function(x: int) -> dict:
            return {"result": x}

    Args:
        name: Dot-separated registry key, or the function being decorated
            when called without parentheses (``@workflow_function``).
        description: Optional human-readable description.
        permissions: Optional list of required permissions.
        risk_level: Risk classification ("low", "medium", "high").
        requires_approval: Whether this function requires human approval.
        timeout_seconds: Optional default timeout in seconds.
        metadata: Optional extra metadata dict.
    """
    # Case 1: @workflow_function (no parens) — name is the function itself
    if callable(name):
        fn = name
        resolved_name = fn.__name__
        get_default_function_registry().register(
            name=resolved_name,
            func=fn,
            description=description,
            permissions=permissions,
            risk_level=risk_level,
            requires_approval=requires_approval,
            timeout_seconds=timeout_seconds,
            metadata=metadata,
        )
        return fn

    # Case 2 & 3: @workflow_function(name="...") or @workflow_function("...")
    resolved_name = name

    def decorator(fn: F) -> F:
        get_default_function_registry().register(
            name=resolved_name,
            func=fn,
            description=description,
            permissions=permissions,
            risk_level=risk_level,
            requires_approval=requires_approval,
            timeout_seconds=timeout_seconds,
            metadata=metadata,
        )
        # Preserve the original function — do NOT wrap it
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Async execution helper
# ---------------------------------------------------------------------------


async def _call_function(
    func: Callable[..., Any],
    kwargs: dict[str, Any],
) -> Any:
    """Call a function (sync or async) with the given kwargs.

    If the function is a coroutine function, await it.
    Otherwise, run it in a thread pool to avoid blocking the event loop.
    """
    if asyncio.iscoroutinefunction(func):
        return await func(**kwargs)
    else:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, functools.partial(func, **kwargs))


# ---------------------------------------------------------------------------
# Output normalization
# ---------------------------------------------------------------------------


def _normalize_output(result: Any) -> Any:
    """Normalize a function's return value for DAG node output.

    Rules:
    - dict → returned as-is
    - Pydantic BaseModel → converted to dict via model_dump()
    - Everything else → wrapped as {"value": result}
    """
    if isinstance(result, dict):
        return result
    # Check for Pydantic BaseModel (v2)
    if hasattr(result, "model_dump") and callable(result.model_dump):
        return result.model_dump()
    # Fallback: wrap scalar in {"value": result}
    return {"value": result}
