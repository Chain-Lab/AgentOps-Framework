"""Publisher protocol — pluggable downstream platform adapter."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from book_publisher.models import GeneratedContent, PlatformSpec, PublishReceipt


@runtime_checkable
class Publisher(Protocol):
    """Adapter that pushes generated content to one downstream platform."""

    async def publish(
        self, *, content: GeneratedContent, platform: PlatformSpec
    ) -> PublishReceipt: ...
