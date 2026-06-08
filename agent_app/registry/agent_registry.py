"""AgentRegistry — stores AgentSpec instances."""

from __future__ import annotations

from agent_app.core.agent_spec import AgentSpec
from agent_app.registry.base import Registry


class AgentRegistry(Registry[AgentSpec]):
    """Registry for :class:`AgentSpec` objects."""

    def register(self, name: str, item: AgentSpec) -> None:  # type: ignore[override]
        """Register an AgentSpec.

        Args:
            name: Agent name (must match ``item.name``).
            item: The AgentSpec to store.
        """
        super()._validate_name(name)
        if item.name != name:
            raise ValueError(
                f"AgentSpec.name ('{item.name}') does not match "
                f"the registration key ('{name}')."
            )
        super().register(name, item)
