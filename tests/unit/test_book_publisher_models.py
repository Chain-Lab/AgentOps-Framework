"""Tests for book_publisher.models — data models for the example."""

from __future__ import annotations

import sys
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "book_publisher"
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

from book_publisher.models import (  # noqa: E402
    BookInput,
    GeneratedContent,
    PersonaSpec,
    PlatformSpec,
    PublishingReport,
    PublishReceipt,
)


def test_book_input_from_yaml_loads_sample_book():
    book = BookInput.from_yaml(_EXAMPLE_DIR / "data" / "sample_book.yaml")
    assert book.title
    assert book.summary
    assert len(book.key_points) >= 1


def test_book_input_to_prompt_text_includes_title_and_summary():
    book = BookInput(
        title="Deep Echo",
        summary="A crew finds an ancient signal beneath the sea.",
        key_points=["found footage", "twist ending"],
        tags=["scifi", "mystery"],
    )
    text = book.to_prompt_text()
    assert "Deep Echo" in text
    assert "A crew finds an ancient signal beneath the sea." in text
    assert "found footage" in text
    assert "scifi" in text


def test_persona_spec_defaults_target_platforms_to_none():
    persona = PersonaSpec(
        name="adult",
        display_name="Adult",
        tone="measured",
        reading_level="high school+",
        max_length=800,
    )
    assert persona.target_platforms is None
    assert persona.extra_instructions == ""


def test_platform_spec_defaults():
    platform = PlatformSpec(name="csdn", display_name="CSDN")
    assert platform.risk_level == "medium"
    assert platform.requires_approval is False
    assert platform.format == "plain"


def test_publishing_report_summary_lists_receipts():
    book = BookInput(title="Deep Echo", summary="...", key_points=[], tags=[])
    report = PublishingReport(
        book=book,
        generated=[
            GeneratedContent(
                persona="adult", book_title="Deep Echo", text="...", run_id="r1", status="completed"
            )
        ],
        receipts=[
            PublishReceipt(platform="csdn", persona="adult", status="published"),
            PublishReceipt(platform="wechat_mp", persona="adult", status="approval_required", approval_id="apv_1"),
        ],
    )
    text = report.summary()
    assert "Deep Echo" in text
    assert "csdn" in text
    assert "approval_required" in text
    assert "apv_1" in text
