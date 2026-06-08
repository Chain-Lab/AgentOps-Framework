"""Generic Registry base class for type-safe registration."""

from __future__ import annotations

from typing import Generic, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
    """Thread-unsafe, in-memory registry with name-based lookup.

    Type parameter ``T`` is the stored item type.

    Subclass and override ``_validate`` if you need custom storage logic.
    """

    def __init__(self) -> None:
        self._items: dict[str, T] = {}

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def register(self, name: str, item: T) -> None:
        """Register *item* under *name*.

        Args:
            name: Unique key.
            item: The item to store.

        Raises:
            ValueError: If *name* is already registered.
            TypeError: If *name* is not a non-empty string.
        """
        self._validate_name(name)
        if name in self._items:
            raise ValueError(
                f"'{name}' is already registered. "
                "Use a different name or unregister first."
            )
        self._items[name] = item

    def get(self, name: str) -> T:
        """Return the item registered under *name*.

        Raises:
            KeyError: If *name* is not found.
        """
        self._validate_name(name)
        if name not in self._items:
            raise KeyError(
                f"'{name}' is not registered. "
                f"Registered items: {sorted(self._items)}"
            )
        return self._items[name]

    def exists(self, name: str) -> bool:
        """Return True if *name* is registered."""
        return name in self._items

    def list(self) -> list[str]:
        """Return all registered names, sorted."""
        return sorted(self._items)

    def unregister(self, name: str) -> None:
        """Remove *name* from the registry.

        Raises:
            KeyError: If *name* is not registered.
        """
        self._validate_name(name)
        if name not in self._items:
            raise KeyError(f"'{name}' is not registered.")
        del self._items[name]

    def clear(self) -> None:
        """Remove all registered items."""
        self._items.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _validate_name(self, name: str) -> None:
        if not isinstance(name, str) or not name:
            raise TypeError("Registry name must be a non-empty string.")
