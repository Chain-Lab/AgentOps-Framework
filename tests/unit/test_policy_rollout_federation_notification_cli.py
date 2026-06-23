"""Tests for Phase 51 template, preference, and webhook CLI commands."""
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from agent_app.governance.policy_rollout_federation_notification import (
    FederationNotificationChannel,
    FederationNotificationDLQReason,
    FederationNotificationDLQStatus,
    FederationNotificationDeadLetter,
)
from agent_app.governance.policy_rollout_federation_notification_template import (
    FederationNotificationRenderedContent,
    FederationNotificationTemplate,
    FederationNotificationTemplateFormat,
)
from agent_app.governance.policy_rollout_federation_notification_preference import (
    FederationNotificationPreference,
    FederationNotificationPreferenceDecision,
    FederationNotificationPreferenceExplanation,
    FederationNotificationPreferenceSubjectType,
)
from agent_app.governance.policy_rollout_federation_webhook import (
    FederationWebhookReplayResult,
    FederationWebhookSignatureResult,
)


def _run(coro):
    return asyncio.run(coro)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _template(
    template_id: str = "fnt_test001",
    name: str = "Test Template",
    event_type: str | None = "approval.created",
    channel: str | None = "email",
    fmt: FederationNotificationTemplateFormat = FederationNotificationTemplateFormat.TEXT,
    enabled: bool = True,
    version: int = 1,
    body_template: str = "Hello {{ actor.name }}",
) -> FederationNotificationTemplate:
    now = _now()
    return FederationNotificationTemplate(
        template_id=template_id,
        name=name,
        body_template=body_template,
        subject_template="Subject for {{ actor.name }}",
        event_type=event_type,
        channel=channel,
        format=fmt,
        enabled=enabled,
        version=version,
        created_at=now,
        updated_at=now,
    )


def _preference(
    preference_id: str = "fnp_test001",
    subject_type: str = "user",
    subject_id: str = "user_001",
    decision: FederationNotificationPreferenceDecision = FederationNotificationPreferenceDecision.OPT_OUT,
    channel: str | None = "email",
    event_type: str | None = None,
    federation_id: str | None = None,
    reason: str | None = None,
) -> FederationNotificationPreference:
    now = _now()
    return FederationNotificationPreference(
        preference_id=preference_id,
        subject_type=FederationNotificationPreferenceSubjectType(subject_type),
        subject_id=subject_id,
        decision=decision,
        channel=channel,
        event_type=event_type,
        federation_id=federation_id,
        reason=reason,
        created_at=now,
        updated_at=now,
    )


def _dlq_entry(
    dlq_id: str = "fdlq_test001",
    notification_id: str = "fn_test001",
    channel: str = "webhook",
) -> FederationNotificationDeadLetter:
    now = _now()
    return FederationNotificationDeadLetter(
        dlq_id=dlq_id,
        notification_id=notification_id,
        approval_id="fap_test001",
        federation_id="frp_test001",
        channel=channel,
        reason=FederationNotificationDLQReason.MAX_RETRIES_EXCEEDED,
        status=FederationNotificationDLQStatus.PENDING,
        failure_count=3,
        last_error="Connection timeout",
        payload={"subject": "Test"},
        metadata={"adapter": "webhook", "event_type": "approval.created"},
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# TestFederationTemplateCLI
# ---------------------------------------------------------------------------


class TestFederationTemplateCLI:
    """Tests for template CLI commands (Phase 51)."""

    def test_template_list_empty(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_template_list

        store = MagicMock()
        store.list = AsyncMock(return_value=[])
        args = argparse.Namespace(config="agentapp.yaml", event_type=None, channel=None, limit=100)
        app = MagicMock()
        app.federation_notification_template_store = store
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_template_list(args))
        assert rc == 0
        assert "No templates found" in capsys.readouterr().out

    def test_template_list_with_templates(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_template_list

        store = MagicMock()
        store.list = AsyncMock(
            return_value=[
                _template(template_id="fnt_001", name="Approval Email", event_type="approval.created", channel="email"),
                _template(template_id="fnt_002", name="Slack Notify", event_type="approval.approved", channel="slack"),
            ]
        )
        args = argparse.Namespace(config="agentapp.yaml", event_type=None, channel=None, limit=100)
        app = MagicMock()
        app.federation_notification_template_store = store
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_template_list(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "fnt_001" in output
        assert "fnt_002" in output
        assert "Approval Email" in output

    def test_template_show_existing(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_template_show

        tmpl = _template(template_id="fnt_001", name="Test Template")
        store = MagicMock()
        store.get = AsyncMock(return_value=tmpl)
        args = argparse.Namespace(config="agentapp.yaml", template_id="fnt_001")
        app = MagicMock()
        app.federation_notification_template_store = store
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_template_show(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "fnt_001" in output
        assert "Test Template" in output
        assert "approval.created" in output

    def test_template_create(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_template_create

        store = MagicMock()
        store.create = AsyncMock(
            return_value=_template(template_id="fnt_new001", name="New Template")
        )
        # Write a temp body file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Hello {{ actor.name }}")
            body_path = f.name
        try:
            args = argparse.Namespace(
                config="agentapp.yaml",
                name="New Template",
                body_file=body_path,
                subject=None,
                event_type="approval.created",
                channel="email",
                format="text",
                federation_id=None,
            )
            app = MagicMock()
            app.federation_notification_template_store = store
            with patch("agent_app.config.loader.build_app", return_value=app):
                rc = _run(_cmd_policy_federation_notification_template_create(args))
            assert rc == 0
            assert "Template created" in capsys.readouterr().out
        finally:
            Path(body_path).unlink(missing_ok=True)

    def test_template_disable(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_template_disable

        store = MagicMock()
        store.delete = AsyncMock(return_value=None)
        args = argparse.Namespace(config="agentapp.yaml", template_id="fnt_001")
        app = MagicMock()
        app.federation_notification_template_store = store
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_template_disable(args))
        assert rc == 0
        assert "disabled" in capsys.readouterr().out
        store.delete.assert_called_once_with("fnt_001")

    def test_template_render_preview(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_template_render

        tmpl = _template(template_id="fnt_001", name="Render Test")
        store = MagicMock()
        store.get = AsyncMock(return_value=tmpl)

        rendered = FederationNotificationRenderedContent(
            template_id="fnt_001",
            template_version=1,
            subject="Subject for Alice",
            body="Hello Alice",
            format=FederationNotificationTemplateFormat.TEXT,
            context_keys=["actor"],
            rendered_at=_now(),
        )
        service = MagicMock()
        service.render = AsyncMock(return_value=rendered)

        # Write a temp context file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"actor": {"name": "Alice"}}, f)
            ctx_path = f.name
        try:
            args = argparse.Namespace(
                config="agentapp.yaml",
                template_id="fnt_001",
                context_file=ctx_path,
            )
            app = MagicMock()
            app.federation_notification_template_store = store
            app.federation_notification_template_service = service
            with patch("agent_app.config.loader.build_app", return_value=app):
                rc = _run(_cmd_policy_federation_notification_template_render(args))
            assert rc == 0
            output = capsys.readouterr().out
            assert "Hello Alice" in output
            assert "Subject for Alice" in output
        finally:
            Path(ctx_path).unlink(missing_ok=True)

    def test_template_create_with_body_file(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_template_create

        store = MagicMock()
        created = _template(template_id="fnt_file001", name="File Template")
        store.create = AsyncMock(return_value=created)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write("<h1>{{ title }}</h1><p>{{ body }}</p>")
            body_path = f.name
        try:
            args = argparse.Namespace(
                config="agentapp.yaml",
                name="File Template",
                body_file=body_path,
                subject=None,
                event_type=None,
                channel=None,
                format="html",
                federation_id=None,
            )
            app = MagicMock()
            app.federation_notification_template_store = store
            with patch("agent_app.config.loader.build_app", return_value=app):
                rc = _run(_cmd_policy_federation_notification_template_create(args))
            assert rc == 0
            assert "Template created" in capsys.readouterr().out
            # Verify the body was read from the file
            call_args = store.create.call_args[0][0]
            assert "<h1>{{ title }}</h1>" in call_args.body_template
        finally:
            Path(body_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# TestFederationPreferenceCLI
# ---------------------------------------------------------------------------


class TestFederationPreferenceCLI:
    """Tests for preference CLI commands (Phase 51)."""

    def test_preference_list_empty(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_preference_list

        store = MagicMock()
        store.list_preferences = AsyncMock(return_value=[])
        args = argparse.Namespace(config="agentapp.yaml", subject_type=None, subject_id=None, channel=None, limit=100)
        app = MagicMock()
        app.federation_notification_preference_store = store
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_preference_list(args))
        assert rc == 0
        assert "No preferences found" in capsys.readouterr().out

    def test_preference_set_opt_out(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_preference_set

        store = MagicMock()
        pref = _preference(
            preference_id="fnp_new001",
            subject_type="user",
            subject_id="user_001",
            decision=FederationNotificationPreferenceDecision.OPT_OUT,
            channel="email",
        )
        store.set_preference = AsyncMock(return_value=pref)
        args = argparse.Namespace(
            config="agentapp.yaml",
            subject_type="user",
            subject_id="user_001",
            channel="email",
            event_type=None,
            decision="opt_out",
            federation_id=None,
            reason=None,
        )
        app = MagicMock()
        app.federation_notification_preference_store = store
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_preference_set(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "Preference set" in output
        assert "opt_out" in output

    def test_preference_show_existing(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_preference_show

        pref = _preference(
            preference_id="fnp_001",
            subject_type="user",
            subject_id="user_001",
            decision=FederationNotificationPreferenceDecision.OPT_OUT,
            channel="email",
            reason="Too many emails",
        )
        store = MagicMock()
        store.get_preference = AsyncMock(return_value=pref)
        args = argparse.Namespace(config="agentapp.yaml", preference_id="fnp_001")
        app = MagicMock()
        app.federation_notification_preference_store = store
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_preference_show(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "fnp_001" in output
        assert "opt_out" in output
        assert "Too many emails" in output

    def test_preference_delete(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_preference_delete

        store = MagicMock()
        store.delete_preference = AsyncMock(return_value=None)
        args = argparse.Namespace(config="agentapp.yaml", preference_id="fnp_001")
        app = MagicMock()
        app.federation_notification_preference_store = store
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_preference_delete(args))
        assert rc == 0
        assert "deleted" in capsys.readouterr().out
        store.delete_preference.assert_called_once_with("fnp_001")

    def test_preference_explain(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_preference_explain

        explanation = FederationNotificationPreferenceExplanation(
            decision=FederationNotificationPreferenceDecision.OPT_OUT,
            matched_preference_id="fnp_001",
            specificity=2,
            is_mandatory=False,
            system_default=False,
            reason="Matched preference fnp_001: opt_out",
            reason_code="preference_opt_out",
        )
        service = MagicMock()
        service.explain_preference = AsyncMock(return_value=explanation)
        args = argparse.Namespace(
            config="agentapp.yaml",
            subject_type="user",
            subject_id="user_001",
            channel="email",
            event_type="approval.created",
            federation_id=None,
        )
        app = MagicMock()
        app.federation_notification_preference_service = service
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_preference_explain(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "opt_out" in output
        assert "fnp_001" in output
        assert "2" in output

    def test_preference_explain_mandatory(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_preference_explain

        explanation = FederationNotificationPreferenceExplanation(
            decision=FederationNotificationPreferenceDecision.OPT_IN,
            matched_preference_id=None,
            specificity=0,
            is_mandatory=True,
            system_default=False,
            reason="Mandatory notification — overrides user preference",
            reason_code="mandatory_override",
        )
        service = MagicMock()
        service.explain_preference = AsyncMock(return_value=explanation)
        args = argparse.Namespace(
            config="agentapp.yaml",
            subject_type="user",
            subject_id="user_001",
            channel="email",
            event_type="approval.escalated",
            federation_id=None,
        )
        app = MagicMock()
        app.federation_notification_preference_service = service
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_preference_explain(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "opt_in" in output
        assert "True" in output  # is_mandatory=True

    def test_preference_explain_system_default(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_preference_explain

        explanation = FederationNotificationPreferenceExplanation(
            decision=FederationNotificationPreferenceDecision.OPT_IN,
            matched_preference_id=None,
            specificity=0,
            is_mandatory=False,
            system_default=True,
            reason="No preference found — using system default (opt_in)",
            reason_code="system_default",
        )
        service = MagicMock()
        service.explain_preference = AsyncMock(return_value=explanation)
        args = argparse.Namespace(
            config="agentapp.yaml",
            subject_type="user",
            subject_id="user_999",
            channel="email",
            event_type="approval.created",
            federation_id=None,
        )
        app = MagicMock()
        app.federation_notification_preference_service = service
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_preference_explain(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "opt_in" in output
        assert "True" in output  # system_default=True


# ---------------------------------------------------------------------------
# TestFederationWebhookCLI
# ---------------------------------------------------------------------------


class TestFederationWebhookCLI:
    """Tests for webhook CLI commands (Phase 51)."""

    def test_dlq_replay_original_dry_run(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_dlq_replay_original

        result = FederationWebhookReplayResult(
            replay_id="fwrp_001",
            dlq_id="fdlq_001",
            notification_id="fn_001",
            success=True,
            replay_count=0,
        )
        service = MagicMock()
        service.replay_original = AsyncMock(return_value=result)
        dlq_store = MagicMock()
        args = argparse.Namespace(
            config="agentapp.yaml",
            dlq_id="fdlq_001",
            dry_run=True,
            target_url=None,
            key_id=None,
        )
        app = MagicMock()
        app.federation_notification_service = service
        app.federation_dlq_store = dlq_store
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_dlq_replay_original(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "fwrp_001" in output
        assert "fdlq_001" in output
        assert "True" in output
        service.replay_original.assert_called_once_with(
            dlq_id="fdlq_001",
            dlq_store=dlq_store,
            dry_run=True,
            target_url=None,
        )

    def test_dlq_replay_original_executed(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_dlq_replay_original

        now = _now()
        result = FederationWebhookReplayResult(
            replay_id="fwrp_002",
            dlq_id="fdlq_001",
            notification_id="fn_001",
            success=True,
            replay_count=1,
            last_replay_at=now,
        )
        service = MagicMock()
        service.replay_original = AsyncMock(return_value=result)
        dlq_store = MagicMock()
        args = argparse.Namespace(
            config="agentapp.yaml",
            dlq_id="fdlq_001",
            dry_run=False,
            target_url=None,
            key_id=None,
        )
        app = MagicMock()
        app.federation_notification_service = service
        app.federation_dlq_store = dlq_store
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_dlq_replay_original(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "fwrp_002" in output
        assert "1" in output  # replay_count

    def test_webhook_verify_valid(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_webhook_verify

        sig_result = FederationWebhookSignatureResult(
            valid=True,
            matched_key_id="key_001",
            signature_version="v1",
            timestamp_valid=True,
            nonce_valid=True,
        )
        service = MagicMock()
        service.verify = MagicMock(return_value=sig_result)
        # Write a temp body file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"test": "payload"}')
            body_path = f.name
        try:
            args = argparse.Namespace(
                config="agentapp.yaml",
                body_file=body_path,
                signature="v1=abc123",
                timestamp="2026-01-01T00:00:00Z",
                nonce="nonce123",
            )
            app = MagicMock()
            app.federation_webhook_signature_service = service
            with patch("agent_app.config.loader.build_app", return_value=app):
                rc = _run(_cmd_policy_federation_webhook_verify(args))
            assert rc == 0
            output = capsys.readouterr().out
            assert "True" in output
            assert "key_001" in output
        finally:
            Path(body_path).unlink(missing_ok=True)

    def test_webhook_verify_invalid(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_webhook_verify

        sig_result = FederationWebhookSignatureResult(
            valid=False,
            reason="signature_mismatch",
            matched_key_id=None,
            signature_version="v1",
            timestamp_valid=True,
        )
        service = MagicMock()
        service.verify = MagicMock(return_value=sig_result)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"test": "payload"}')
            body_path = f.name
        try:
            args = argparse.Namespace(
                config="agentapp.yaml",
                body_file=body_path,
                signature="v1=invalid",
                timestamp="2026-01-01T00:00:00Z",
                nonce="nonce123",
            )
            app = MagicMock()
            app.federation_webhook_signature_service = service
            with patch("agent_app.config.loader.build_app", return_value=app):
                rc = _run(_cmd_policy_federation_webhook_verify(args))
            assert rc == 0
            output = capsys.readouterr().out
            assert "False" in output
            assert "signature_mismatch" in output
        finally:
            Path(body_path).unlink(missing_ok=True)

    def test_webhook_verify_no_keys_in_output(self, capsys) -> None:
        """Verify that secret key values never appear in CLI output."""
        from agent_app.cli import _cmd_policy_federation_webhook_verify

        sig_result = FederationWebhookSignatureResult(
            valid=True,
            matched_key_id="key_prod_001",
            signature_version="v1",
            timestamp_valid=True,
            nonce_valid=True,
        )
        service = MagicMock()
        service.verify = MagicMock(return_value=sig_result)
        # The service has actual secret keys internally
        service._keys = {"key_prod_001": "super-secret-key-value-never-output"}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"test": "payload"}')
            body_path = f.name
        try:
            args = argparse.Namespace(
                config="agentapp.yaml",
                body_file=body_path,
                signature="v1=abc123",
                timestamp="2026-01-01T00:00:00Z",
                nonce="nonce123",
            )
            app = MagicMock()
            app.federation_webhook_signature_service = service
            with patch("agent_app.config.loader.build_app", return_value=app):
                rc = _run(_cmd_policy_federation_webhook_verify(args))
            assert rc == 0
            output = capsys.readouterr().out
            # Key ID is shown, but the actual secret key value must NEVER appear
            assert "key_prod_001" in output
            assert "super-secret-key-value-never-output" not in output
        finally:
            Path(body_path).unlink(missing_ok=True)

    def test_dlq_replay_original_non_webhook_rejected(self, capsys) -> None:
        """Replaying a non-webhook DLQ entry should show an error."""
        from agent_app.cli import _cmd_policy_federation_notification_dlq_replay_original

        result = FederationWebhookReplayResult(
            replay_id="fwrp_fail",
            dlq_id="fdlq_email001",
            notification_id="fn_001",
            success=False,
            replay_count=0,
            error="Cannot replay non-webhook channel: email",
        )
        service = MagicMock()
        service.replay_original = AsyncMock(return_value=result)
        dlq_store = MagicMock()
        args = argparse.Namespace(
            config="agentapp.yaml",
            dlq_id="fdlq_email001",
            dry_run=False,
            target_url=None,
            key_id=None,
        )
        app = MagicMock()
        app.federation_notification_service = service
        app.federation_dlq_store = dlq_store
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_dlq_replay_original(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "False" in output
        assert "Cannot replay non-webhook channel" in output


class TestDlqBatchReplayCLI:
    """Tests for Phase 56 DLQ batch-replay CLI command."""

    def test_batch_replay_dry_run(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_dlq_batch_replay

        attempts = [
            MagicMock(
                attempt_id="nda_t1_a1_1", target_id="t1", alert_id="a1",
                created_at=datetime.now(timezone.utc),
                status=AlertDeliveryStatus.DLQ,
            ),
            MagicMock(
                attempt_id="nda_t1_a2_1", target_id="t1", alert_id="a2",
                created_at=datetime.now(timezone.utc),
                status=AlertDeliveryStatus.DLQ,
            ),
        ]
        dlq_store = MagicMock()
        dlq_store.list_attempts = AsyncMock(return_value=attempts)
        service = MagicMock()
        service.replay_dlq_attempt = AsyncMock(return_value=MagicMock(status=AlertDeliveryStatus.DELIVERED))
        args = argparse.Namespace(
            config="agentapp.yaml",
            target_id=None, alert_id=None,
            since=None, until=None,
            limit=100, dry_run=True,
        )
        app = MagicMock()
        app._federation_notification_alert_delivery_service = service
        app.federation_dlq_store = dlq_store
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_dlq_batch_replay(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "DRY RUN" in output
        assert "Replayed: 2" in output

    def test_batch_replay_with_filters(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_dlq_batch_replay

        now = datetime.now(timezone.utc)
        attempts = [
            MagicMock(
                attempt_id="nda_t1_a1_1", target_id="t1", alert_id="a1",
                created_at=now, status=AlertDeliveryStatus.DLQ,
            ),
            MagicMock(
                attempt_id="nda_t2_a1_1", target_id="t2", alert_id="a1",
                created_at=now, status=AlertDeliveryStatus.DLQ,
            ),
        ]
        dlq_store = MagicMock()
        dlq_store.list_attempts = AsyncMock(return_value=attempts)
        service = MagicMock()
        service.replay_dlq_attempt = AsyncMock(return_value=MagicMock(status=AlertDeliveryStatus.DELIVERED))
        args = argparse.Namespace(
            config="agentapp.yaml",
            target_id="t1", alert_id=None,
            since=None, until=None,
            limit=100, dry_run=False,
        )
        app = MagicMock()
        app._federation_notification_alert_delivery_service = service
        app.federation_dlq_store = dlq_store
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_dlq_batch_replay(args))
        assert rc == 0
        output = capsys.readouterr().out
        assert "LIVE" in output
        assert "Replayed: 1" in output  # Only t1 matches

    def test_batch_replay_service_not_configured(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_dlq_batch_replay

        args = argparse.Namespace(
            config="agentapp.yaml",
            target_id=None, alert_id=None,
            since=None, until=None,
            limit=100, dry_run=False,
        )
        app = MagicMock()
        app._federation_notification_alert_delivery_service = None
        app.federation_dlq_store = MagicMock()
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_dlq_batch_replay(args))
        assert rc == 1
        output = capsys.readouterr().out
        assert "not configured" in output

    def test_batch_replay_dlq_store_not_configured(self, capsys) -> None:
        from agent_app.cli import _cmd_policy_federation_notification_dlq_batch_replay

        args = argparse.Namespace(
            config="agentapp.yaml",
            target_id=None, alert_id=None,
            since=None, until=None,
            limit=100, dry_run=False,
        )
        app = MagicMock()
        app._federation_notification_alert_delivery_service = MagicMock()
        app.federation_dlq_store = None
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run(_cmd_policy_federation_notification_dlq_batch_replay(args))
        assert rc == 1
        output = capsys.readouterr().out
        assert "DLQ store not configured" in output
