# examples/book_publisher/main.py
"""Book publisher example — multi-persona, multi-platform publishing demo.

Generates audience-tailored book descriptions via a parallel DAG of mock-LLM
agent nodes, then publishes each variant to a set of mock platforms through
the framework's real governance pipeline: low-risk platforms auto-publish,
high-risk platforms pause for human approval via app.approve().
"""

import asyncio
import sys
from pathlib import Path

_EXAMPLES_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _EXAMPLES_DIR.parents[1]
for _path in (_EXAMPLES_DIR, _REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from book_publisher.build_app import build_app
from book_publisher.models import BookInput
from book_publisher.pipeline import complete_approved_publish, generate_content, publish_all


async def main() -> None:
    book = BookInput.from_yaml(_EXAMPLES_DIR / "data" / "sample_book.yaml")
    bp_app = build_app()

    print(f"=== Book Publisher: {book.title} ===\n")

    print("-- Generating persona variants --")
    generated = await generate_content(bp_app.app, book, bp_app.personas)
    for persona_name, content in generated.items():
        print(f"[{persona_name}] {content.text}\n")

    print("-- Publishing --")
    report = await publish_all(
        bp_app.app, bp_app.tool_executor, book, bp_app.personas, bp_app.platforms, generated
    )
    print(report.summary())

    pending = [r for r in report.receipts if r.status == "approval_required"]
    if pending:
        print(f"\n-- Approving {len(pending)} pending publish(es) --")
        for receipt in pending:
            print(f"Approving publish to '{receipt.platform}' for persona '{receipt.persona}'...")
            await bp_app.app.approve(receipt.approval_id, approved_by="demo-editor")
            completed = await complete_approved_publish(bp_app.app, book, generated, receipt)
            receipt.status = completed.status
            receipt.published_at = completed.published_at
            receipt.formatted_preview = completed.formatted_preview

    print("\n=== Final report ===")
    print(report.summary())


if __name__ == "__main__":
    asyncio.run(main())
