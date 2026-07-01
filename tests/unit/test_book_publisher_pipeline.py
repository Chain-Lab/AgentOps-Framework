"""Integration tests for book_publisher.pipeline — generation and governed publishing."""

from __future__ import annotations

import sys
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "book_publisher"
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

from book_publisher.build_app import build_app
from book_publisher.models import BookInput
from book_publisher.pipeline import complete_approved_publish, generate_content, publish_all


def _book() -> BookInput:
    return BookInput(
        title="Deep Echo",
        summary="A crew finds an ancient signal beneath the sea.",
        key_points=["found footage", "twist ending"],
        tags=["scifi", "mystery"],
    )


async def test_generate_content_produces_one_variant_per_persona(tmp_path):
    bp_app = build_app(log_path=tmp_path / "log.jsonl")
    book = _book()

    generated = await generate_content(bp_app.app, book, bp_app.personas)

    persona_names = {p.name for p in bp_app.personas.all()}
    assert set(generated.keys()) == persona_names
    for content in generated.values():
        assert content.book_title == "Deep Echo"
        assert content.status == "completed"
        assert content.text


async def test_publish_all_auto_publishes_low_risk_platform(tmp_path):
    bp_app = build_app(log_path=tmp_path / "log.jsonl")
    book = _book()
    generated = await generate_content(bp_app.app, book, bp_app.personas)

    report = await publish_all(
        bp_app.app, bp_app.tool_executor, book, bp_app.personas, bp_app.platforms, generated
    )

    csdn_receipts = [r for r in report.receipts if r.platform == "csdn"]
    assert csdn_receipts
    assert all(r.status == "published" for r in csdn_receipts)


async def test_publish_all_interrupts_high_risk_platform(tmp_path):
    bp_app = build_app(log_path=tmp_path / "log.jsonl")
    book = _book()
    generated = await generate_content(bp_app.app, book, bp_app.personas)

    report = await publish_all(
        bp_app.app, bp_app.tool_executor, book, bp_app.personas, bp_app.platforms, generated
    )

    wechat_receipts = [r for r in report.receipts if r.platform == "wechat_mp"]
    assert wechat_receipts
    assert all(r.status == "approval_required" for r in wechat_receipts)
    assert all(r.approval_id is not None for r in wechat_receipts)


async def test_children_persona_only_publishes_to_csdn(tmp_path):
    bp_app = build_app(log_path=tmp_path / "log.jsonl")
    book = _book()
    generated = await generate_content(bp_app.app, book, bp_app.personas)

    report = await publish_all(
        bp_app.app, bp_app.tool_executor, book, bp_app.personas, bp_app.platforms, generated
    )

    children_platforms = {r.platform for r in report.receipts if r.persona == "children"}
    assert children_platforms == {"csdn"}


async def test_approve_then_complete_approved_publish_marks_it_published(tmp_path):
    bp_app = build_app(log_path=tmp_path / "log.jsonl")
    book = _book()
    generated = await generate_content(bp_app.app, book, bp_app.personas)
    report = await publish_all(
        bp_app.app, bp_app.tool_executor, book, bp_app.personas, bp_app.platforms, generated
    )

    pending = next(r for r in report.receipts if r.status == "approval_required")

    await bp_app.app.approve(pending.approval_id, approved_by="demo-editor")
    completed = await complete_approved_publish(bp_app.app, book, generated, pending)

    assert completed.status == "published"
    assert completed.platform == pending.platform
    assert completed.persona == pending.persona
