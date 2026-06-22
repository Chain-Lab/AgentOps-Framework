"""Tests for FederationNotificationTemplateService — safe template rendering with priority selection.

Phase 51 Task 2.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_rollout_federation_notification_template import (
    FederationNotificationTemplate,
    FederationNotificationTemplateFormat,
    TemplateDisabledError,
    TemplateForbiddenExpressionError,
    TemplateInvalidJsonError,
    TemplateMissingVariableError,
)
from agent_app.runtime.policy_rollout_federation_notification_template_service import (
    FederationNotificationTemplateService,
)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_template(
    template_id: str = "fnt_test",
    name: str = "Test Template",
    body_template: str = "Body {{ approval.id }}",
    subject_template: str | None = None,
    event_type: str | None = None,
    channel: str | None = None,
    federation_id: str | None = None,
    fmt: FederationNotificationTemplateFormat = FederationNotificationTemplateFormat.TEXT,
    enabled: bool = True,
    version: int = 1,
) -> FederationNotificationTemplate:
    now = datetime.now(timezone.utc)
    return FederationNotificationTemplate(
        template_id=template_id,
        name=name,
        body_template=body_template,
        subject_template=subject_template,
        event_type=event_type,
        channel=channel,
        federation_id=federation_id,
        format=fmt,
        enabled=enabled,
        version=version,
        created_at=now,
        updated_at=now,
    )


class MockTemplateStore:
    """Simple mock store for testing template selection priority."""

    def __init__(self, templates=None):
        self._templates = {t.template_id: t for t in (templates or [])}

    async def get(self, template_id):
        return self._templates.get(template_id)

    async def find_effective_template(self, **kwargs):
        # Simple implementation: return first matching template (including disabled)
        for t in self._templates.values():
            fed_match = kwargs.get("federation_id") is None or t.federation_id == kwargs["federation_id"]
            evt_match = kwargs.get("event_type") is None or t.event_type == kwargs["event_type"]
            ch_match = kwargs.get("channel") is None or t.channel == kwargs["channel"]
            if fed_match and evt_match and ch_match:
                return t
        return None


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_render_builtin_template():
    """Uses builtin when no store is configured."""
    svc = FederationNotificationTemplateService()
    result = _run_async(
        svc.render(
            event_type="approval.created",
            channel="email",
            context={"approval": {"id": "AP-1"}, "actor": {"display_name": "Alice"}, "federation": {"id": "F-1"}},
        )
    )
    assert result.template_id == "builtin"
    assert "AP-1" in result.body
    assert "Alice" in result.body
    assert "F-1" in result.body


def test_render_simple_variable():
    """{{ approval.id }} is replaced with the context value."""
    svc = FederationNotificationTemplateService()
    result = _run_async(
        svc.render(
            event_type="approval.created",
            channel="email",
            context={"approval": {"id": "AP-42"}, "actor": {"display_name": "Bob"}, "federation": {"id": "F-9"}},
        )
    )
    assert "AP-42" in result.body


def test_render_nested_variable():
    """{{ approval.requester.name }} resolved from nested dict."""
    tmpl = _make_template(
        body_template="Requester: {{ approval.requester.name }}",
        event_type="approval.created",
        channel="email",
    )
    store = MockTemplateStore([tmpl])
    svc = FederationNotificationTemplateService(template_store=store)
    result = _run_async(
        svc.render(
            event_type="approval.created",
            channel="email",
            context={"approval": {"requester": {"name": "Carol"}}},
        )
    )
    assert "Carol" in result.body


def test_render_missing_variable_strict():
    """Raises TemplateMissingVariableError when strict=True (default)."""
    tmpl = _make_template(
        body_template="Missing: {{ nonexistent.var }}",
        event_type="approval.created",
        channel="email",
    )
    store = MockTemplateStore([tmpl])
    svc = FederationNotificationTemplateService(template_store=store)
    with pytest.raises(TemplateMissingVariableError, match="nonexistent.var"):
        _run_async(
            svc.render(
                event_type="approval.created",
                channel="email",
                context={},
            )
        )


def test_render_missing_variable_lenient():
    """Empty string substituted when strict=False and variable is missing."""
    tmpl = _make_template(
        body_template="Hello {{ name }}!",
        event_type="approval.created",
        channel="email",
    )
    store = MockTemplateStore([tmpl])
    svc = FederationNotificationTemplateService(template_store=store, strict_variables=False)
    result = _run_async(
        svc.render(
            event_type="approval.created",
            channel="email",
            context={},
        )
    )
    assert result.body == "Hello !"


def test_render_dunder_access_forbidden():
    """Raises TemplateForbiddenExpressionError for dunder access in template."""
    tmpl = _make_template(
        body_template="{{ obj.__class__ }}",
        event_type="approval.created",
        channel="email",
    )
    store = MockTemplateStore([tmpl])
    svc = FederationNotificationTemplateService(template_store=store)
    with pytest.raises(TemplateForbiddenExpressionError, match="dunder"):
        _run_async(
            svc.render(
                event_type="approval.created",
                channel="email",
                context={"obj": {}},
            )
        )


def test_render_function_call_forbidden():
    """Raises TemplateForbiddenExpressionError for function call in template."""
    tmpl = _make_template(
        body_template="{{ obj.method() }}",
        event_type="approval.created",
        channel="email",
    )
    store = MockTemplateStore([tmpl])
    svc = FederationNotificationTemplateService(template_store=store)
    with pytest.raises(TemplateForbiddenExpressionError, match="function"):
        _run_async(
            svc.render(
                event_type="approval.created",
                channel="email",
                context={"obj": {"method": "not_callable_in_template"}},
            )
        )


def test_render_subject_and_body():
    """Both subject_template and body_template are rendered."""
    tmpl = _make_template(
        subject_template="Subject: {{ approval.id }}",
        body_template="Body: {{ approval.id }}",
        event_type="approval.created",
        channel="email",
    )
    store = MockTemplateStore([tmpl])
    svc = FederationNotificationTemplateService(template_store=store)
    result = _run_async(
        svc.render(
            event_type="approval.created",
            channel="email",
            context={"approval": {"id": "AP-99"}},
        )
    )
    assert result.subject == "Subject: AP-99"
    assert result.body == "Body: AP-99"


def test_render_json_template_valid():
    """Valid JSON template passes validation."""
    tmpl = _make_template(
        body_template='{"id": "{{ approval.id }}", "status": "created"}',
        event_type="approval.created",
        channel="webhook",
        fmt=FederationNotificationTemplateFormat.JSON,
    )
    store = MockTemplateStore([tmpl])
    svc = FederationNotificationTemplateService(template_store=store)
    result = _run_async(
        svc.render(
            event_type="approval.created",
            channel="webhook",
            context={"approval": {"id": "AP-7"}},
        )
    )
    import json
    parsed = json.loads(result.body)
    assert parsed["id"] == "AP-7"
    assert parsed["status"] == "created"


def test_render_json_template_invalid():
    """Raises TemplateInvalidJsonError when rendered JSON is invalid."""
    tmpl = _make_template(
        body_template='{{ approval.id }} is not json',
        event_type="approval.created",
        channel="webhook",
        fmt=FederationNotificationTemplateFormat.JSON,
    )
    store = MockTemplateStore([tmpl])
    svc = FederationNotificationTemplateService(template_store=store)
    with pytest.raises(TemplateInvalidJsonError):
        _run_async(
            svc.render(
                event_type="approval.created",
                channel="webhook",
                context={"approval": {"id": "AP-7"}},
            )
        )


def test_render_disabled_template_raises():
    """TemplateDisabledError raised when matched template is disabled."""
    tmpl = _make_template(
        body_template="Disabled template",
        event_type="approval.created",
        channel="email",
        enabled=False,
    )
    store = MockTemplateStore([tmpl])
    svc = FederationNotificationTemplateService(template_store=store)
    with pytest.raises(TemplateDisabledError):
        _run_async(
            svc.render(
                event_type="approval.created",
                channel="email",
                context={},
            )
        )


def test_render_template_version_snapshot():
    """Rendered content carries template_id and version at render time."""
    tmpl = _make_template(
        template_id="fnt_v3",
        body_template="Version test",
        event_type="approval.created",
        channel="email",
        version=3,
    )
    store = MockTemplateStore([tmpl])
    svc = FederationNotificationTemplateService(template_store=store)
    result = _run_async(
        svc.render(
            event_type="approval.created",
            channel="email",
            context={},
        )
    )
    assert result.template_id == "fnt_v3"
    assert result.template_version == 3


def test_render_context_keys_extracted():
    """context_keys populated with top-level keys referenced in template."""
    tmpl = _make_template(
        body_template="{{ approval.id }} by {{ actor.display_name }}",
        event_type="approval.created",
        channel="email",
    )
    store = MockTemplateStore([tmpl])
    svc = FederationNotificationTemplateService(template_store=store)
    result = _run_async(
        svc.render(
            event_type="approval.created",
            channel="email",
            context={"approval": {"id": "AP-1"}, "actor": {"display_name": "Dan"}},
        )
    )
    assert "approval" in result.context_keys
    assert "actor" in result.context_keys


def test_render_no_store_uses_builtin():
    """Falls back to builtin when no store is configured."""
    svc = FederationNotificationTemplateService()
    result = _run_async(
        svc.render(
            event_type="approval.approved",
            channel="email",
            context={"approval": {"id": "AP-5"}, "actor": {"display_name": "Eve"}},
        )
    )
    assert result.template_id == "builtin"
    assert "AP-5" in result.body


def test_render_unknown_event_uses_generic_builtin():
    """Fallback for unknown event type produces generic notification text."""
    svc = FederationNotificationTemplateService()
    result = _run_async(
        svc.render(
            event_type="custom.unknown_event",
            channel="email",
            context={},
        )
    )
    assert result.template_id == "builtin"
    assert "custom.unknown_event" in result.body


def test_render_multiple_variables():
    """Multiple {{ }} placeholders in a single template."""
    tmpl = _make_template(
        body_template="{{ a }} and {{ b }} and {{ c }}",
        event_type="test.multi",
        channel="email",
    )
    store = MockTemplateStore([tmpl])
    svc = FederationNotificationTemplateService(template_store=store)
    result = _run_async(
        svc.render(
            event_type="test.multi",
            channel="email",
            context={"a": "1", "b": "2", "c": "3"},
        )
    )
    assert result.body == "1 and 2 and 3"


def test_render_preserves_non_template_text():
    """Static text around variables is preserved exactly."""
    tmpl = _make_template(
        body_template="Hello, {{ name }}! Welcome aboard.",
        event_type="greeting",
        channel="email",
    )
    store = MockTemplateStore([tmpl])
    svc = FederationNotificationTemplateService(template_store=store)
    result = _run_async(
        svc.render(
            event_type="greeting",
            channel="email",
            context={"name": "Frank"},
        )
    )
    assert result.body == "Hello, Frank! Welcome aboard."


def test_render_empty_context_with_no_variables():
    """Template with no variables works with empty context."""
    tmpl = _make_template(
        body_template="No variables here.",
        event_type="static",
        channel="email",
    )
    store = MockTemplateStore([tmpl])
    svc = FederationNotificationTemplateService(template_store=store)
    result = _run_async(
        svc.render(
            event_type="static",
            channel="email",
            context={},
        )
    )
    assert result.body == "No variables here."


def test_render_non_dict_context_path_returns_missing():
    """Non-dict in path returns missing/raises in strict mode."""
    tmpl = _make_template(
        body_template="{{ approval.id.name }}",
        event_type="test.nondict",
        channel="email",
    )
    store = MockTemplateStore([tmpl])
    svc = FederationNotificationTemplateService(template_store=store)
    with pytest.raises(TemplateMissingVariableError):
        _run_async(
            svc.render(
                event_type="test.nondict",
                channel="email",
                context={"approval": {"id": 42}},
            )
        )


def test_validate_template_dunder_in_body():
    """Body template containing __dunder__ fails validation."""
    tmpl = _make_template(
        body_template="Secret: {{ data.__secret__ }}",
        event_type="test.dunder",
        channel="email",
    )
    store = MockTemplateStore([tmpl])
    svc = FederationNotificationTemplateService(template_store=store)
    with pytest.raises(TemplateForbiddenExpressionError, match="dunder"):
        _run_async(
            svc.render(
                event_type="test.dunder",
                channel="email",
                context={"data": {}},
            )
        )


def test_validate_template_function_call_in_body():
    """Body template containing .func() fails validation."""
    tmpl = _make_template(
        body_template="{{ data.upper() }}",
        event_type="test.func",
        channel="email",
    )
    store = MockTemplateStore([tmpl])
    svc = FederationNotificationTemplateService(template_store=store)
    with pytest.raises(TemplateForbiddenExpressionError, match="function"):
        _run_async(
            svc.render(
                event_type="test.func",
                channel="email",
                context={"data": "hello"},
            )
        )


def test_render_strict_mode_default():
    """strict_variables is True by default."""
    svc = FederationNotificationTemplateService()
    assert svc._strict_variables is True


def test_render_subject_none_when_no_subject_template():
    """No subject_template on the template means rendered subject is None."""
    tmpl = _make_template(
        body_template="Body only",
        subject_template=None,
        event_type="test.nosubject",
        channel="email",
    )
    store = MockTemplateStore([tmpl])
    svc = FederationNotificationTemplateService(template_store=store)
    result = _run_async(
        svc.render(
            event_type="test.nosubject",
            channel="email",
            context={},
        )
    )
    assert result.subject is None


def test_render_builtin_has_no_template_id():
    """Builtin fallback returns template_id='builtin'."""
    svc = FederationNotificationTemplateService()
    result = _run_async(
        svc.render(
            event_type="approval.expired",
            channel="email",
            context={"approval": {"id": "AP-X"}},
        )
    )
    assert result.template_id == "builtin"
    assert result.template_version == 0


def test_render_deterministic_output():
    """Same input produces same output."""
    svc = FederationNotificationTemplateService()
    ctx = {"approval": {"id": "AP-1"}, "actor": {"display_name": "Grace"}, "federation": {"id": "F-1"}}
    r1 = _run_async(svc.render(event_type="approval.created", channel="email", context=ctx))
    r2 = _run_async(svc.render(event_type="approval.created", channel="email", context=ctx))
    assert r1.body == r2.body
    assert r1.template_id == r2.template_id
