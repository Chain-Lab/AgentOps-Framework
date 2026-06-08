"""WorkflowRegistry — stores Workflow instances."""

from __future__ import annotations

from agent_app.core.workflow import Workflow
from agent_app.registry.base import Registry


class WorkflowRegistry(Registry[Workflow]):
    """Registry for :class:`Workflow` objects."""

    def register(self, name: str, item: Workflow) -> None:  # type: ignore[override]
        """Register a Workflow.

        Args:
            name: Workflow name (must match ``item.name``).
            item: The Workflow to store.
        """
        if item.name != name:
            raise ValueError(
                f"Workflow.name ('{item.name}') does not match "
                f"the registration key ('{name}')."
            )
        super().register(name, item)
