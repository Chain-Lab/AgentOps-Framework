"""Mock publisher — simulates posting via a local JSONL log, no real API calls."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from book_publisher.models import GeneratedContent, PlatformSpec, PublishReceipt

_DEFAULT_LOG_PATH = ".agent_app/book_publisher_log.jsonl"


class MockPublisher:
    """Formats content per-platform and appends a JSON record simulating a post."""

    def __init__(self, log_path: str | Path = _DEFAULT_LOG_PATH) -> None:
        self._log_path = Path(log_path)

    async def publish(
        self, *, content: GeneratedContent, platform: PlatformSpec
    ) -> PublishReceipt:
        text = content.text
        if platform.max_length is not None:
            text = text[: platform.max_length]

        preview = text
        if platform.hashtag_style and content.tags:
            hashtags = " ".join(
                platform.hashtag_style.format(tag=tag) for tag in content.tags
            )
            preview = f"{text}\n\n{hashtags}"

        published_at = datetime.now(timezone.utc).isoformat()

        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "platform": platform.name,
            "persona": content.persona,
            "book_title": content.book_title,
            "text": preview,
            "published_at": published_at,
        }
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        return PublishReceipt(
            platform=platform.name,
            persona=content.persona,
            status="published",
            published_at=published_at,
            formatted_preview=preview,
        )
