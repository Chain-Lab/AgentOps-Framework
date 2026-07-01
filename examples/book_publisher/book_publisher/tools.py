"""Builds one governed ToolSpec + callable pair per registered platform.

Reuses the framework's existing ToolExecutor governance pipeline as-is: risk
level and approval requirement come straight from each platform's YAML, no
new mechanism needed.
"""

from __future__ import annotations

from typing import Any, Callable

from agent_app.core.tool_spec import ToolSpec

from book_publisher.models import GeneratedContent, PlatformSpec
from book_publisher.platforms import PlatformRegistry
from book_publisher.publishers.base import Publisher


def build_publish_tools(
    platform_registry: PlatformRegistry,
    publisher: Publisher,
) -> list[tuple[ToolSpec, Callable[..., Any]]]:
    """Return a (ToolSpec, async callable) pair for every platform in the registry."""
    tools: list[tuple[ToolSpec, Callable[..., Any]]] = []

    for platform in platform_registry.all():
        spec = ToolSpec(
            name=f"publish_{platform.name}",
            description=f"Publish content to {platform.display_name}",
            risk_level=platform.risk_level,
            requires_approval=platform.requires_approval,
        )

        # `_platform: PlatformSpec = platform` binds the CURRENT loop value as
        # a default argument, evaluated at function-definition time. Without
        # it, all closures would instead read the shared `platform` loop
        # variable at call time (Python's normal late-binding closure
        # behavior), so every tool would resolve to whatever platform the
        # loop last landed on — i.e. every publish_* tool would silently
        # publish to the final platform in the registry.
        async def _fn(
            content: str,
            persona: str,
            book_title: str,
            tags: list[str] | None = None,
            _platform: PlatformSpec = platform,
        ) -> dict:
            generated = GeneratedContent(
                persona=persona,
                book_title=book_title,
                text=content,
                run_id="",
                status="completed",
                tags=tags or [],
            )
            receipt = await publisher.publish(content=generated, platform=_platform)
            return receipt.model_dump()

        tools.append((spec, _fn))

    return tools
