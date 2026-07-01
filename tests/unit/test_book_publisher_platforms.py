"""Tests for book_publisher.platforms — YAML-driven platform registry."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "book_publisher"
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

from book_publisher.platforms import PlatformRegistry  # noqa: E402


def test_loads_all_default_platforms():
    registry = PlatformRegistry.load(_EXAMPLE_DIR / "platforms")
    names = {p.name for p in registry.all()}
    assert names == {"wechat_mp", "zhihu", "juejin", "csdn"}
    assert len(registry) == 4


def test_wechat_mp_is_high_risk_and_requires_approval():
    registry = PlatformRegistry.load(_EXAMPLE_DIR / "platforms")
    wechat = registry.get("wechat_mp")
    assert wechat.risk_level == "high"
    assert wechat.requires_approval is True


def test_csdn_is_low_risk_auto_publish():
    registry = PlatformRegistry.load(_EXAMPLE_DIR / "platforms")
    csdn = registry.get("csdn")
    assert csdn.risk_level == "low"
    assert csdn.requires_approval is False


def test_adding_a_new_platform_needs_zero_code_changes(tmp_path):
    (tmp_path / "toutiao.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "toutiao",
                "display_name": "Toutiao",
                "max_length": 2000,
                "risk_level": "medium",
            }
        ),
        encoding="utf-8",
    )
    registry = PlatformRegistry.load(tmp_path)
    assert len(registry) == 1
    assert registry.get("toutiao").max_length == 2000


def test_duplicate_platform_name_raises(tmp_path):
    for fname in ("a.yaml", "b.yaml"):
        (tmp_path / fname).write_text(
            yaml.safe_dump({"name": "dup", "display_name": "Dup"}),
            encoding="utf-8",
        )
    with pytest.raises(ValueError, match="Duplicate platform"):
        PlatformRegistry.load(tmp_path)
