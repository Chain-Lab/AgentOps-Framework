"""Federation webhook models — request snapshots, signature results, replay tracking.

Phase 51: Webhook request snapshot, signature verification result, replay result.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class FederationWebhookRequestSnapshot(BaseModel):
    """Immutable snapshot of a webhook request for audit and replay."""
    request_id: str = Field(..., description="Unique request identifier (fwr_ prefix)")
    notification_id: str = Field(..., description="Related notification ID")
    url: str = Field(..., description="Target URL (sensitive params redacted)")
    method: str = Field(default="POST", description="HTTP method, POST only by default")
    headers: dict[str, str] = Field(default_factory=dict, description="Request headers (auth headers redacted)")
    body: str = Field(..., description="Request body as stable string")
    content_type: str = Field(default="application/json", description="Content-Type header")
    timestamp: datetime = Field(..., description="Request timestamp")
    nonce: str = Field(..., description="Unique nonce for replay protection")
    signature_algorithm: str = Field(default="hmac-sha256", description="Signature algorithm")
    signature_version: str = Field(default="v1", description="Signature version")
    payload_digest: str = Field(..., description="SHA-256 digest of original body")
    created_at: datetime = Field(..., description="Timezone-aware creation timestamp")

    @field_validator("request_id")
    @classmethod
    def _validate_request_id(cls, v: str) -> str:
        if not v.startswith("fwr_"):
            raise ValueError(f"ID must start with 'fwr_', got '{v}'")
        return v

    @field_validator("method")
    @classmethod
    def _validate_method(cls, v: str) -> str:
        if v.upper() != "POST":
            raise ValueError("Only POST method is allowed")
        return v.upper()

    @field_validator("timestamp", "created_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("datetime must be timezone-aware")
        return v


class FederationWebhookSignatureResult(BaseModel):
    """Result of webhook signature verification."""
    valid: bool = Field(..., description="Whether the signature is valid")
    reason: str | None = Field(default=None, description="Failure reason")
    matched_key_id: str | None = Field(default=None, description="Key ID that matched (not the key itself)")
    signature_version: str | None = Field(default=None, description="Signature version used")
    timestamp_valid: bool = Field(default=True, description="Whether timestamp is within tolerance")
    nonce_valid: bool | None = Field(default=None, description="Whether nonce is unique (None if not checked)")


class FederationWebhookReplayResult(BaseModel):
    """Result of a webhook original-payload replay."""
    replay_id: str = Field(..., description="Unique replay identifier (fwrp_ prefix)")
    dlq_id: str = Field(..., description="DLQ entry that was replayed")
    notification_id: str = Field(..., description="Original notification ID")
    success: bool = Field(..., description="Whether replay succeeded")
    replay_count: int = Field(default=0, description="Total replay count for this entry")
    last_replay_at: datetime | None = Field(default=None, description="Timezone-aware last replay time")
    error: str | None = Field(default=None, description="Error message if replay failed")

    @field_validator("replay_id")
    @classmethod
    def _validate_replay_id(cls, v: str) -> str:
        if not v.startswith("fwrp_"):
            raise ValueError(f"ID must start with 'fwrp_', got '{v}'")
        return v

    @field_validator("last_replay_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and (v.tzinfo is None or v.tzinfo.utcoffset(v) is None):
            raise ValueError("datetime must be timezone-aware")
        return v
