"""Federation notification template service — safe template rendering with priority selection.

Phase 51: Template parsing, selection, and rendering with version snapshots.
"""
from __future__ import annotations

import json
import re
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from agent_app.governance.policy_rollout_federation_notification_template import (
    FederationNotificationRenderedContent,
    FederationNotificationTemplate,
    FederationNotificationTemplateFormat,
    TemplateDisabledError,
    TemplateForbiddenExpressionError,
    TemplateInvalidJsonError,
    TemplateMissingVariableError,
    TemplateNotFoundError,
    FederationNotificationTemplateError,
)

logger = logging.getLogger(__name__)

# Safe variable pattern: {{ var.path }} — only alphanumeric + dots + underscores
_VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\}\}")
# Forbidden patterns
_DUNDER_PATTERN = re.compile(r"__\w+__")
_FUNCTION_PATTERN = re.compile(r"\.\s*\w+\s*\(")

# Built-in fallback templates
_BUILTIN_TEMPLATES: dict[str, str] = {
    "approval.created": "Federation approval {{ approval.id }} created by {{ actor.display_name }} for federation {{ federation.id }}.",
    "approval.approved": "Federation approval {{ approval.id }} approved by {{ actor.display_name }}.",
    "approval.rejected": "Federation approval {{ approval.id }} rejected by {{ actor.display_name }}.",
    "approval.escalated": "Federation approval {{ approval.id }} escalated.",
    "approval.cancelled": "Federation approval {{ approval.id }} cancelled.",
    "approval.expired": "Federation approval {{ approval.id }} expired.",
}


class FederationNotificationTemplateService:
    """Service for selecting and rendering notification templates.

    Template selection priority (highest to lowest):
    1. Federation + event_type + channel explicit template
    2. Event_type + channel template
    3. Channel default template
    4. Global default template (no event_type, no channel)
    5. Built-in fallback template

    Template syntax: {{ variable.path }} — safe dot-notation access only.
    No function calls, no dunder access, no code execution.
    """

    def __init__(
        self,
        template_store: Any | None = None,
        *,
        strict_variables: bool = True,
        default_template_id: str | None = None,
    ) -> None:
        self._store = template_store
        self._strict_variables = strict_variables
        self._default_template_id = default_template_id

    async def render(
        self,
        *,
        event_type: str,
        channel: str,
        federation_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> FederationNotificationRenderedContent:
        """Select and render a template for the given parameters.

        Args:
            event_type: Notification event type.
            channel: Notification channel.
            federation_id: Optional federation scope.
            context: Rendering context variables.

        Returns:
            Rendered content with template ID and version snapshot.

        Raises:
            TemplateNotFoundError: No template found and no builtin.
            TemplateDisabledError: Matched template is disabled.
            TemplateSyntaxError: Template contains invalid syntax.
            TemplateMissingVariableError: Required variable missing (strict mode).
            TemplateInvalidJsonError: JSON template rendered invalid JSON.
            TemplateForbiddenExpressionError: Template contains forbidden expressions.
        """
        context = context or {}
        now = datetime.now(timezone.utc)

        template = await self._find_template(
            event_type=event_type,
            channel=channel,
            federation_id=federation_id,
        )

        if template is None:
            # Use builtin
            body_text = _BUILTIN_TEMPLATES.get(event_type, "")
            if not body_text:
                body_text = f"Notification: {event_type}"
            rendered_body = self._render_text(body_text, context)
            return FederationNotificationRenderedContent(
                template_id="builtin",
                template_version=0,
                subject=None,
                body=rendered_body,
                format=FederationNotificationTemplateFormat.TEXT,
                context_keys=list(context.keys()),
                rendered_at=now,
            )

        if not template.enabled:
            raise TemplateDisabledError(f"Template {template.template_id} is disabled")

        # Validate template syntax
        self._validate_template(template.body_template)
        if template.subject_template:
            self._validate_template(template.subject_template)

        # Render subject
        rendered_subject = None
        if template.subject_template:
            rendered_subject = self._render_text(template.subject_template, context)

        # Render body
        rendered_body = self._render_text(template.body_template, context)

        # Validate JSON format
        if template.format == FederationNotificationTemplateFormat.JSON:
            try:
                json.loads(rendered_body)
            except json.JSONDecodeError as exc:
                raise TemplateInvalidJsonError(f"Rendered template is not valid JSON: {exc}") from exc

        # Collect context keys used
        used_keys = self._extract_context_keys(template.body_template)
        if template.subject_template:
            used_keys.extend(self._extract_context_keys(template.subject_template))

        return FederationNotificationRenderedContent(
            template_id=template.template_id,
            template_version=template.version,
            subject=rendered_subject,
            body=rendered_body,
            format=template.format,
            context_keys=list(dict.fromkeys(used_keys)),  # deduplicate preserving order
            rendered_at=now,
        )

    def _validate_template(self, template_text: str) -> None:
        """Validate template for forbidden expressions.

        Checks both the raw template text and the variable placeholders
        for forbidden patterns like dunder access and function calls.
        """
        # Check for dunder access in raw text
        if _DUNDER_PATTERN.search(template_text):
            raise TemplateForbiddenExpressionError("Template contains dunder access (forbidden)")
        # Check for function calls in raw text (e.g. {{ obj.method() }})
        if _FUNCTION_PATTERN.search(template_text):
            raise TemplateForbiddenExpressionError("Template contains function calls (forbidden)")

    def _render_text(self, template_text: str, context: dict[str, Any]) -> str:
        """Render a template string with safe variable substitution."""
        def replacer(match: re.Match) -> str:
            var_path = match.group(1)
            value = self._resolve_path(var_path, context)
            if value is _MISSING:
                if self._strict_variables:
                    raise TemplateMissingVariableError(f"Missing variable: {var_path}")
                return ""
            return str(value)

        return _VARIABLE_PATTERN.sub(replacer, template_text)

    def _resolve_path(self, path: str, context: dict[str, Any]) -> Any:
        """Resolve a dot-notation path against the context safely."""
        parts = path.split(".")
        current: Any = context
        for part in parts:
            if not isinstance(current, dict):
                return _MISSING
            if part not in current:
                return _MISSING
            current = current[part]
        return current

    def _extract_context_keys(self, template_text: str) -> list[str]:
        """Extract top-level context keys referenced in a template."""
        keys = []
        for match in _VARIABLE_PATTERN.finditer(template_text):
            var_path = match.group(1)
            top_key = var_path.split(".")[0]
            if top_key not in keys:
                keys.append(top_key)
        return keys

    async def _find_template(
        self,
        event_type: str,
        channel: str,
        federation_id: str | None = None,
    ) -> FederationNotificationTemplate | None:
        """Find the most specific matching template using priority rules."""
        if self._store is None:
            return None

        # Priority 1: federation + event_type + channel
        if federation_id:
            tmpl = await self._store.find_effective_template(
                federation_id=federation_id,
                event_type=event_type,
                channel=channel,
            )
            if tmpl is not None:
                return tmpl

        # Priority 2: event_type + channel
        tmpl = await self._store.find_effective_template(
            event_type=event_type,
            channel=channel,
        )
        if tmpl is not None:
            return tmpl

        # Priority 3: channel default
        tmpl = await self._store.find_effective_template(channel=channel)
        if tmpl is not None:
            return tmpl

        # Priority 4: global default
        if self._default_template_id:
            try:
                tmpl = await self._store.get(self._default_template_id)
                if tmpl is not None and tmpl.enabled:
                    return tmpl
            except Exception:  # noqa: BLE001
                pass

        # Priority 5: builtin (returned by caller)
        return None


_MISSING = object()
