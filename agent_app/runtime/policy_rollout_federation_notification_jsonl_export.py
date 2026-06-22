"""JSONL structured export for notification events, alerts, and attempts.

Phase 53 Task 6: JSONL structured export.
"""
from __future__ import annotations

import json
from typing import Any

from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryAttempt,
    AlertDeliveryChannelType,
    AlertDeliveryStatus,
    AlertDeliveryTarget,
)
from agent_app.governance.policy_rollout_federation_notification_observability import (
    NotificationAlertEvent,
    NotificationDeliveryEvent,
    NotificationDeliveryEventType,
    _redact_sensitive_values,
)


def _sanitize_value(value: Any) -> Any:
    """Sanitize a value for safe export."""
    if isinstance(value, str):
        return _redact_sensitive_values(value)
    if isinstance(value, dict):
        return _sanitize_metadata(value)
    return value


def _sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Sanitize metadata dict for safe export."""
    if not metadata:
        return {}
    _SENSITIVE_KEYS = {"authorization", "token", "secret", "password", "api_key",
                       "x-signature", "x-signature-key", "x-api-key", "x-secret",
                       "x-auth-token", "x-webhook-secret", "cookie", "set-cookie",
                       "proxy-authorization", "www-authenticate", "signature",
                       "key", "private_key", "access_key"}
    result: dict[str, Any] = {}
    for k, v in metadata.items():
        if k.lower() in _SENSITIVE_KEYS:
            result[k] = "[REDACTED]"
        elif isinstance(v, str):
            result[k] = _redact_sensitive_values(v)
        else:
            result[k] = v
    return result


def _model_to_safe_dict(model: Any) -> dict[str, Any]:
    """Convert a Pydantic model to a dict with sensitive fields sanitized."""
    data = model.model_dump(mode="json")
    # Sanitize string fields that might contain sensitive values
    if "error_message" in data and data["error_message"]:
        data["error_message"] = _redact_sensitive_values(data["error_message"])
    if "message" in data and data["message"]:
        data["message"] = _redact_sensitive_values(data["message"])
    if "metadata" in data and data["metadata"]:
        data["metadata"] = _sanitize_metadata(data["metadata"])
    if "payload_preview" in data and data["payload_preview"]:
        data["payload_preview"] = _sanitize_metadata(data["payload_preview"])
    if "headers" in data and data["headers"]:
        data["headers"] = _sanitize_metadata(data["headers"])
    return data


def export_delivery_events_jsonl(events: list[NotificationDeliveryEvent]) -> str:
    """Export delivery events as JSONL (one JSON object per line)."""
    if not events:
        return ""
    lines = []
    for event in events:
        data = _model_to_safe_dict(event)
        lines.append(json.dumps(data, default=str))
    return "\n".join(lines) + "\n"


def export_alert_events_jsonl(alerts: list[NotificationAlertEvent]) -> str:
    """Export alert events as JSONL (one JSON object per line)."""
    if not alerts:
        return ""
    lines = []
    for alert in alerts:
        data = _model_to_safe_dict(alert)
        lines.append(json.dumps(data, default=str))
    return "\n".join(lines) + "\n"


def export_delivery_attempts_jsonl(attempts: list[AlertDeliveryAttempt]) -> str:
    """Export delivery attempts as JSONL (one JSON object per line)."""
    if not attempts:
        return ""
    lines = []
    for attempt in attempts:
        data = _model_to_safe_dict(attempt)
        # Ensure channel_type and status are string values
        if "channel_type" in data and hasattr(data["channel_type"], "value"):
            data["channel_type"] = data["channel_type"].value
        if "status" in data and hasattr(data["status"], "value"):
            data["status"] = data["status"].value
        lines.append(json.dumps(data, default=str))
    return "\n".join(lines) + "\n"
