"""Tests for book_publisher.personas — YAML-driven persona registry."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "book_publisher"
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

from book_publisher.personas import PersonaRegistry  # noqa: E402


def test_loads_all_default_personas():
    registry = PersonaRegistry.load(_EXAMPLE_DIR / "personas")
    names = {p.name for p in registry.all()}
    assert names == {"children", "adult", "student", "teacher"}
    assert len(registry) == 4


def test_children_persona_restricts_target_platforms():
    registry = PersonaRegistry.load(_EXAMPLE_DIR / "personas")
    children = registry.get("children")
    assert children.target_platforms == ["csdn"]


def test_adult_persona_targets_all_platforms_by_default():
    registry = PersonaRegistry.load(_EXAMPLE_DIR / "personas")
    adult = registry.get("adult")
    assert adult.target_platforms is None


def test_adding_a_new_persona_needs_zero_code_changes(tmp_path):
    (tmp_path / "librarian.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "librarian",
                "display_name": "Librarian",
                "tone": "curatorial, precise",
                "reading_level": "professional",
                "max_length": 400,
            }
        ),
        encoding="utf-8",
    )
    registry = PersonaRegistry.load(tmp_path)
    assert len(registry) == 1
    assert registry.get("librarian").tone == "curatorial, precise"


def test_duplicate_persona_name_raises(tmp_path):
    for fname in ("a.yaml", "b.yaml"):
        (tmp_path / fname).write_text(
            yaml.safe_dump(
                {
                    "name": "dup",
                    "display_name": "Dup",
                    "tone": "x",
                    "reading_level": "x",
                    "max_length": 100,
                }
            ),
            encoding="utf-8",
        )
    with pytest.raises(ValueError, match="Duplicate persona"):
        PersonaRegistry.load(tmp_path)
