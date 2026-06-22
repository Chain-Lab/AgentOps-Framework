"""Federation notification template models — configurable notification content templates.

Phase 51: Template models, rendering results, and template errors.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class FederationNotificationTemplateFormat(StrEnum):
    """Format of a notification template."""
    TEXT = "text"
    HTML = "html"
    JSON = "json"


class FederationNotificationTemplate(BaseModel):
    """A configurable notification template with safe variable substitution."""
    template_id: str = Field(..., description="Unique template identifier (fnt_ prefix)")
    name: str = Field(..., description="Template name, non-empty")
    description: str = Field(default="", description="Template description")
    event_type: str | None = Field(default=None, description="Event type this template applies to")
    channel: str | None = Field(default=None, description="Channel this template applies to")
    federation_id: str | None = Field(default=None, description="Federation scope, None = global")
    subject_template: str | None = Field(default=None, description="Subject template string")
    body_template: str = Field(..., description="Body template string (required)")
    format: FederationNotificationTemplateFormat = Field(default=FederationNotificationTemplateFormat.TEXT, description="Template format")
    enabled: bool = Field(default=True, description="Whether this template is active")
    version: int = Field(default=1, description="Template version, must be positive")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Template metadata")
    created_at: datetime = Field(..., description="Timezone-aware creation timestamp")
    updated_at: datetime = Field(..., description="Timezone-aware last update timestamp")

    @field_validator("template_id")
    @classmethod
    def _validate_template_id(cls, v: str) -> str:
        if not v.startswith("fnt_"):
            raise ValueError(f"ID must start with 'fnt_', got '{v}'")
        return v

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be empty")
        return v

    @field_validator("version")
    @classmethod
    def _validate_version(cls, v: int) -> int:
        if v < 1:
            raise ValueError("version must be positive")
        return v

    @field_validator("created_at", "updated_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("datetime must be timezone-aware")
        return v


class FederationNotificationRenderedContent(BaseModel):
    """Result of rendering a notification template."""
    template_id: str = Field(..., description="Template that was used")
    template_version: int = Field(..., description="Template version at render time")
    subject: str | None = Field(default=None, description="Rendered subject")
    body: str = Field(..., description="Rendered body")
    format: FederationNotificationTemplateFormat = Field(..., description="Content format")
    context_keys: list[str] = Field(default_factory=list, description="Context keys used in rendering")
    rendered_at: datetime = Field(..., description="Timezone-aware render timestamp")

    @field_validator("rendered_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("datetime must be timezone-aware")
        return v


class FederationNotificationTemplateError(Exception):
    """Base exception for template errors."""
    pass


class TemplateNotFoundError(FederationNotificationTemplateError):
    """Requested template does not exist."""
    pass


class TemplateDisabledError(FederationNotificationTemplateError):
    """Template is disabled."""
    pass


class TemplateSyntaxError(FederationNotificationTemplateError):
    """Template contains invalid syntax."""
    pass


class TemplateMissingVariableError(FederationNotificationTemplateError):
    """Required variable is missing from context."""
    pass


class TemplateInvalidJsonError(FederationNotificationTemplateError):
    """Rendered JSON template is not valid JSON."""
    pass


class TemplateForbiddenExpressionError(FederationNotificationTemplateError):
    """Template contains forbidden expressions (function calls, dunder access, etc.)."""
    pass
