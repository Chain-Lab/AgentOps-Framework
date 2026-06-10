"""Sanitization helpers for approval and audit payloads."""

from __future__ import annotations

from typing import Any

_SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "authorization",
    "credential",
)


def sanitize_payload(value: Any, *, max_string_length: int = 500) -> Any:
    """Return a copy of value with sensitive fields redacted."""
    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(part in key_text for part in _SENSITIVE_KEY_PARTS):
                sanitized[key] = "[redacted]"
            else:
                sanitized[key] = sanitize_payload(
                    item,
                    max_string_length=max_string_length,
                )
        return sanitized
    if isinstance(value, list):
        return [sanitize_payload(item, max_string_length=max_string_length) for item in value]
    if isinstance(value, tuple):
        return [sanitize_payload(item, max_string_length=max_string_length) for item in value]
    if isinstance(value, str):
        if len(value) > max_string_length:
            return value[:max_string_length] + "...(truncated)"
        return value
    return value


def sanitized_error(error_type: str, message: str) -> dict[str, str]:
    """Build a generic user-facing error detail."""
    return {"type": error_type, "message": message}
