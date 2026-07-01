# examples/book_publisher/book_publisher/pipeline.py
"""Orchestrates content generation and governed publishing.

Content generation runs through the framework's DAG workflow engine
(tool-free agent nodes only). Publishing deliberately does NOT use DAG tool
nodes — DagExecutor's internal approval store can't be resolved via
app.approve() — and instead drives a directly-constructed, real ToolExecutor
(built once in build_app.py, shared with the app's real approval_store).
"""

from __future__ import annotations

import uuid

from agent_app.core.context import RunContext
from agent_app.governance.risk import ApprovalStatus

from book_publisher.models import (
    BookInput,
    GeneratedContent,
    PersonaSpec,
    PlatformSpec,
    PublishingReport,
    PublishReceipt,
)
from book_publisher.personas import PersonaRegistry
from book_publisher.platforms import PlatformRegistry


async def generate_content(
    app, book: BookInput, personas: PersonaRegistry
) -> dict[str, GeneratedContent]:
    """Runs the book_generation DAG and collects one GeneratedContent per persona.

    Raises RuntimeError naming any persona whose write_{persona} node did not
    complete, rather than silently omitting it — a caller with a partial
    `generated` dict has no way to tell "persona wasn't configured" apart
    from "persona's generation failed" without this.
    """
    result = await app.run(workflow="book_generation", input=book.to_prompt_text())

    generated: dict[str, GeneratedContent] = {}
    failed: dict[str, str] = {}
    for node_result in result.node_results:
        node_id = node_result["node_id"]
        if not node_id.startswith("write_"):
            continue
        persona_name = node_id.removeprefix("write_")
        if node_result["status"] != "completed":
            failed[persona_name] = node_result["status"]
            continue
        generated[persona_name] = GeneratedContent(
            persona=persona_name,
            book_title=book.title,
            text=node_result["output"],
            run_id=result.run_id,
            status=node_result["status"],
            tags=book.tags,
        )

    if failed:
        details = ", ".join(f"{name} ({status})" for name, status in sorted(failed.items()))
        raise RuntimeError(f"Content generation failed for persona(s): {details}")

    return generated


def _target_platforms(
    persona: PersonaSpec, platforms: PlatformRegistry
) -> list[PlatformSpec]:
    if persona.target_platforms is None:
        return platforms.all()
    return [platforms.get(name) for name in persona.target_platforms]


async def publish_all(
    app,
    tool_executor,
    book: BookInput,
    personas: PersonaRegistry,
    platforms: PlatformRegistry,
    generated: dict[str, GeneratedContent],
) -> PublishingReport:
    """Drives a real, governed ToolExecutor.execute() per persona x platform pair."""
    receipts: list[PublishReceipt] = []

    for persona in personas.all():
        content = generated.get(persona.name)
        if content is None:
            continue
        for platform in _target_platforms(persona, platforms):
            context = RunContext(
                run_id=str(uuid.uuid4()), user_id="demo-editor", tenant_id="default"
            )
            result = await tool_executor.execute(
                tool_name=f"publish_{platform.name}",
                arguments={
                    "content": content.text,
                    "persona": content.persona,
                    "book_title": content.book_title,
                    "tags": content.tags,
                },
                context=context,
            )

            if result.status == "completed":
                receipts.append(PublishReceipt(**result.output))
            elif result.status == "interrupted":
                receipts.append(
                    PublishReceipt(
                        platform=platform.name,
                        persona=persona.name,
                        status="approval_required",
                        approval_id=result.approval_request.approval_id,
                    )
                )
            else:
                receipts.append(
                    PublishReceipt(
                        platform=platform.name,
                        persona=persona.name,
                        status="failed",
                    )
                )

    return PublishingReport(book=book, generated=list(generated.values()), receipts=receipts)


async def complete_approved_publish(
    app,
    book: BookInput,
    generated: dict[str, GeneratedContent],
    receipt: PublishReceipt,
) -> PublishReceipt:
    """Completes a publish call after app.approve() has granted its approval.

    The framework has no public "resume this exact governed tool call" API
    outside of the OpenAI-native-SDK HITL marker path (reserved for that
    integration, not usable here). tool_registry.get_fn() is the legitimate,
    publicly-exposed escape hatch: it returns the exact same callable
    ToolExecutor would have invoked had the approval gate not fired.

    Because this bypasses ToolExecutor.execute() (and the permission/audit
    logging it would normally perform), the approval itself must be checked
    here explicitly — ToolExecutor's approval gate is the only thing that
    would otherwise have enforced it.
    """
    if receipt.approval_id is None:
        raise ValueError(
            f"Receipt for persona '{receipt.persona}' / platform '{receipt.platform}' "
            "has no approval_id — it was never gated on approval."
        )
    approval = await app.approval_store.get(receipt.approval_id)
    if approval.status != ApprovalStatus.APPROVED:
        raise ValueError(
            f"Cannot complete publish: approval '{receipt.approval_id}' is "
            f"'{approval.status}', not '{ApprovalStatus.APPROVED}'."
        )

    content = generated[receipt.persona]
    fn = app.tool_registry.get_fn(f"publish_{receipt.platform}")
    result_dict = await fn(
        content=content.text,
        persona=content.persona,
        book_title=content.book_title,
        tags=content.tags,
    )
    return PublishReceipt(**result_dict)
