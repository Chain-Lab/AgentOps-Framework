"""Pydantic data models for the book publisher example."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class BookInput(BaseModel):
    """Structured book brief — no file parsing, just title/summary/points/tags."""

    title: str
    summary: str
    key_points: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str | Path) -> BookInput:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)

    def to_prompt_text(self) -> str:
        lines = [f"Title: {self.title}", f"Summary: {self.summary}"]
        if self.key_points:
            lines.append("Key points:")
            lines.extend(f"- {point}" for point in self.key_points)
        if self.tags:
            lines.append(f"Tags: {', '.join(self.tags)}")
        return "\n".join(lines)


class PersonaSpec(BaseModel):
    """One target audience, loaded from personas/*.yaml."""

    name: str
    display_name: str
    tone: str
    reading_level: str
    max_length: int
    extra_instructions: str = ""
    target_platforms: list[str] | None = None


class PlatformSpec(BaseModel):
    """One downstream publishing platform, loaded from platforms/*.yaml."""

    name: str
    display_name: str
    max_length: int | None = None
    format: str = "plain"
    hashtag_style: str = ""
    risk_level: str = "medium"
    requires_approval: bool = False


class GeneratedContent(BaseModel):
    """One persona's generated description for the book."""

    persona: str
    book_title: str
    text: str
    run_id: str
    status: str
    tags: list[str] = Field(default_factory=list)


class PublishReceipt(BaseModel):
    """Result of attempting to publish one persona's content to one platform."""

    platform: str
    persona: str
    status: str  # "published" | "approval_required" | "failed"
    approval_id: str | None = None
    published_at: str | None = None
    formatted_preview: str | None = None


class PublishingReport(BaseModel):
    """Full run summary: the book, what was generated, and every publish receipt."""

    book: BookInput
    generated: list[GeneratedContent] = Field(default_factory=list)
    receipts: list[PublishReceipt] = Field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Book: {self.book.title}", f"Generated variants: {len(self.generated)}"]
        for r in self.receipts:
            line = f"  [{r.platform}] persona={r.persona} status={r.status}"
            if r.approval_id:
                line += f" approval_id={r.approval_id}"
            lines.append(line)
        return "\n".join(lines)
