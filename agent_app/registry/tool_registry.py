"""ToolRegistry — stores ToolSpec instances plus optional callables."""

from __future__ import annotations

from typing import Any

from agent_app.core.tool_spec import ToolSpec
from agent_app.registry.base import Registry


class _ToolEntry:
    """Internal container pairing a ToolSpec with its callable."""

    __slots__ = ("spec", "fn")

    def __init__(self, spec: ToolSpec, fn: Any = None) -> None:
        self.spec = spec
        self.fn = fn


class ToolRegistry(Registry[_ToolEntry]):
    """Registry for :class:`ToolSpec` objects.

    The ``get()`` method returns the ``_ToolEntry`` wrapper.  Use
    :meth:`get_spec` and :meth:`get_fn` for convenient access.
    """

    def register(  # type: ignore[override]
        self,
        name: str,
        item: ToolSpec,
        fn: Any = None,
    ) -> None:
        """Register a ToolSpec with an optional callable.

        Args:
            name: Tool name (must match ``item.name``).
            item: The ToolSpec to store.
            fn: The Python callable implementing this tool.
        """
        if item.name != name:
            raise ValueError(
                f"ToolSpec.name ('{item.name}') does not match "
                f"the registration key ('{name}')."
            )
        super().register(name, _ToolEntry(spec=item, fn=fn))

    def get_spec(self, name: str) -> ToolSpec:
        """Return the :class:`ToolSpec` for *name*."""
        entry = self.get(name)
        return entry.spec

    def get_fn(self, name: str) -> Any:
        """Return the callable registered for *name*, or None."""
        entry = self.get(name)
        return entry.fn

    def get_entry(self, name: str) -> _ToolEntry:
        """Return the full :class:`_ToolEntry` for *name*."""
        return self.get(name)
