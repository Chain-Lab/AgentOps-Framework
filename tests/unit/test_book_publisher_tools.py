"""Tests for book_publisher.tools.build_publish_tools."""

from __future__ import annotations

import sys
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "book_publisher"
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

from book_publisher.platforms import PlatformRegistry
from book_publisher.publishers.mock import MockPublisher
from book_publisher.tools import build_publish_tools


def test_builds_one_tool_per_platform(tmp_path):
    platforms = PlatformRegistry.load(_EXAMPLE_DIR / "platforms")
    publisher = MockPublisher(log_path=tmp_path / "log.jsonl")

    tools = build_publish_tools(platforms, publisher)

    names = {spec.name for spec, _fn in tools}
    assert names == {"publish_wechat_mp", "publish_zhihu", "publish_juejin", "publish_csdn"}


def test_tool_spec_risk_level_and_approval_match_platform(tmp_path):
    platforms = PlatformRegistry.load(_EXAMPLE_DIR / "platforms")
    publisher = MockPublisher(log_path=tmp_path / "log.jsonl")

    tools = {spec.name: spec for spec, _fn in build_publish_tools(platforms, publisher)}

    assert tools["publish_wechat_mp"].risk_level == "high"
    assert tools["publish_wechat_mp"].requires_approval is True
    assert tools["publish_csdn"].risk_level == "low"
    assert tools["publish_csdn"].requires_approval is False


async def test_tool_fn_calls_publisher_and_returns_receipt_dict(tmp_path):
    platforms = PlatformRegistry.load(_EXAMPLE_DIR / "platforms")
    publisher = MockPublisher(log_path=tmp_path / "log.jsonl")

    tools = {spec.name: fn for spec, fn in build_publish_tools(platforms, publisher)}
    fn = tools["publish_csdn"]

    result = await fn(content="A great book.", persona="adult", book_title="Deep Echo", tags=["scifi"])

    assert result["platform"] == "csdn"
    assert result["persona"] == "adult"
    assert result["status"] == "published"


async def test_each_tool_fn_targets_its_own_platform_not_the_last_one(tmp_path):
    """Regression test for the classic late-binding-closure-in-a-loop bug."""
    platforms = PlatformRegistry.load(_EXAMPLE_DIR / "platforms")
    publisher = MockPublisher(log_path=tmp_path / "log.jsonl")

    tools = {spec.name: fn for spec, fn in build_publish_tools(platforms, publisher)}

    csdn_result = await tools["publish_csdn"](content="c", persona="adult", book_title="t")
    zhihu_result = await tools["publish_zhihu"](content="c", persona="adult", book_title="t")

    assert csdn_result["platform"] == "csdn"
    assert zhihu_result["platform"] == "zhihu"
