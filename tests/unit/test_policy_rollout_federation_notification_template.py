"""Tests for federation notification template domain models."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_app.governance.policy_rollout_federation_notification_template import (
    FederationNotificationRenderedContent,
    FederationNotificationTemplate,
    FederationNotificationTemplateFormat,
    TemplateDisabledError,
    TemplateForbiddenExpressionError,
    TemplateInvalidJsonError,
    TemplateMissingVariableError,
    TemplateNotFoundError,
    TemplateSyntaxError,
)


def _ts() -> datetime:
    return datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)


# --- FederationNotificationTemplateFormat enum ---


class TestTemplateFormatEnum:
    def test_enum_values(self):
        assert FederationNotificationTemplateFormat.TEXT == "text"
        assert FederationNotificationTemplateFormat.HTML == "html"
        assert FederationNotificationTemplateFormat.JSON == "json"
        assert len(FederationNotificationTemplateFormat) == 3


# --- FederationNotificationTemplate model ---


class TestTemplateModel:
    def test_template_model_valid(self):
        t = FederationNotificationTemplate(
            template_id="fnt_approval_created",
            name="Approval Created",
            body_template="Approval {{ approval_id }} created",
            created_at=_ts(),
            updated_at=_ts(),
        )
        assert t.template_id == "fnt_approval_created"
        assert t.name == "Approval Created"
        assert t.format == FederationNotificationTemplateFormat.TEXT
        assert t.enabled is True
        assert t.version == 1

    def test_template_id_prefix_valid(self):
        t = FederationNotificationTemplate(
            template_id="fnt_valid",
            name="Valid",
            body_template="body",
            created_at=_ts(),
            updated_at=_ts(),
        )
        assert t.template_id == "fnt_valid"

    def test_template_id_prefix_invalid(self):
        with pytest.raises(ValidationError, match="fnt_"):
            FederationNotificationTemplate(
                template_id="bad_id",
                name="Bad",
                body_template="body",
                created_at=_ts(),
                updated_at=_ts(),
            )

    def test_template_name_empty_rejected(self):
        with pytest.raises(ValidationError, match="name must not be empty"):
            FederationNotificationTemplate(
                template_id="fnt_test",
                name="   ",
                body_template="body",
                created_at=_ts(),
                updated_at=_ts(),
            )

    def test_template_version_positive(self):
        with pytest.raises(ValidationError, match="version must be positive"):
            FederationNotificationTemplate(
                template_id="fnt_test",
                name="Test",
                body_template="body",
                version=0,
                created_at=_ts(),
                updated_at=_ts(),
            )

    def test_template_tz_aware_required(self):
        naive = datetime(2026, 6, 20, 12, 0, 0)
        with pytest.raises(ValidationError, match="timezone-aware"):
            FederationNotificationTemplate(
                template_id="fnt_test",
                name="Test",
                body_template="body",
                created_at=naive,
                updated_at=_ts(),
            )


# --- FederationNotificationRenderedContent model ---


class TestRenderedContent:
    def test_rendered_content_model(self):
        rc = FederationNotificationRenderedContent(
            template_id="fnt_test",
            template_version=2,
            body="Rendered body",
            format=FederationNotificationTemplateFormat.HTML,
            context_keys=["approval_id", "federation_id"],
            rendered_at=_ts(),
        )
        assert rc.template_id == "fnt_test"
        assert rc.template_version == 2
        assert rc.subject is None
        assert rc.body == "Rendered body"
        assert rc.format == FederationNotificationTemplateFormat.HTML
        assert rc.context_keys == ["approval_id", "federation_id"]


# --- Exception hierarchy ---


class TestTemplateExceptions:
    def test_exception_hierarchy(self):
        assert issubclass(TemplateNotFoundError, Exception)
        assert issubclass(TemplateDisabledError, Exception)
        assert issubclass(TemplateSyntaxError, Exception)
        assert issubclass(TemplateMissingVariableError, Exception)
        assert issubclass(TemplateInvalidJsonError, Exception)
        assert issubclass(TemplateForbiddenExpressionError, Exception)
