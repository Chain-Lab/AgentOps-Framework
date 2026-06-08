"""Permission module — permission checking framework.

Phase 3: DefaultPermissionChecker with simple allowlist matching.
"""

from __future__ import annotations

from typing import Protocol

from agent_app.core.context import RunContext


class PermissionChecker(Protocol):
    """Protocol for permission checking.

    Implementations decide whether a tool call is authorized given
    the tool's required permissions and the run context.
    """

    async def check(
        self,
        required_permissions: list[str],
        context: RunContext,
    ) -> bool:
        """Check if the context grants all required permissions.

        Args:
            required_permissions: Permissions the tool requires.
            context: Current run context (user, tenant, roles, permissions).

        Returns:
            True if authorized, False otherwise.
        """
        ...


class DefaultPermissionChecker:
    """Simple allowlist-based permission checker.

    Authorization rules:
    1. If the tool requires no permissions, allow.
    2. If the context's ``permissions`` list contains ALL required
       permissions, allow.
    3. Otherwise deny.
    """

    async def check(
        self,
        required_permissions: list[str],
        context: RunContext,
    ) -> bool:
        if not required_permissions:
            return True
        return all(perm in context.permissions for perm in required_permissions)
