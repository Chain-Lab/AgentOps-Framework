"""Tests for book_publisher.publishers — Publisher protocol and MockPublisher."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "book_publisher"
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

from book_publisher.models import GeneratedContent, PlatformSpec
from book_publisher.publishers.base import Publisher
from book_publisher.publishers.mock import MockPublisher


def test_mock_publisher_satisfies_publisher_protocol():
    assert isinstance(MockPublisher(), Publisher)


async def test_publish_truncates_to_platform_max_length(tmp_path):
    publisher = MockPublisher(log_path=tmp_path / "log.jsonl")
    content = GeneratedContent(
        persona="adult", book_title="Deep Echo", text="x" * 100, run_id="r1", status="completed"
    )
    platform = PlatformSpec(name="csdn", display_name="CSDN", max_length=10)

    receipt = await publisher.publish(content=content, platform=platform)

    assert receipt.status == "published"
    assert receipt.platform == "csdn"
    assert receipt.persona == "adult"
    assert len(receipt.formatted_preview.split("\n\n")[0]) == 10
    assert receipt.published_at is not None


async def test_publish_appends_hashtags_from_tags(tmp_path):
    publisher = MockPublisher(log_path=tmp_path / "log.jsonl")
    content = GeneratedContent(
        persona="adult",
        book_title="Deep Echo",
        text="A great book.",
        run_id="r1",
        status="completed",
        tags=["scifi", "mystery"],
    )
    platform = PlatformSpec(name="wechat_mp", display_name="WeChat", hashtag_style="#{tag}")

    receipt = await publisher.publish(content=content, platform=platform)

    assert "#scifi" in receipt.formatted_preview
    assert "#mystery" in receipt.formatted_preview


async def test_publish_writes_one_jsonl_record(tmp_path):
    log_path = tmp_path / "log.jsonl"
    publisher = MockPublisher(log_path=log_path)
    content = GeneratedContent(
        persona="adult", book_title="Deep Echo", text="A great book.", run_id="r1", status="completed"
    )
    platform = PlatformSpec(name="csdn", display_name="CSDN")

    await publisher.publish(content=content, platform=platform)

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["platform"] == "csdn"
    assert record["persona"] == "adult"
    assert record["book_title"] == "Deep Echo"
