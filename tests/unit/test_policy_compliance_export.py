"""Tests for policy compliance export helpers."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

import pytest

from agent_app.runtime.policy_compliance_export import (
    export_federation_dlq_summary_csv,
    export_federation_dlq_summary_json,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dlq_item(**overrides):
    from agent_app.governance.policy_rollout_federation_notification import (
        FederationNotificationDeadLetter,
        FederationNotificationDLQReason,
        FederationNotificationDLQStatus,
    )

    now = datetime.now(timezone.utc)
    defaults = dict(
        dlq_id="fdlq_test123",
        notification_id="fn_test123",
        approval_id="fap_test123",
        federation_id="frp_test123",
        channel="webhook",
        reason=FederationNotificationDLQReason.MAX_RETRIES_EXCEEDED,
        status=FederationNotificationDLQStatus.PENDING,
        failure_count=3,
        last_error="Connection timeout",
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return FederationNotificationDeadLetter(**defaults)


# ---------------------------------------------------------------------------
# DLQ JSON Export Tests
# ---------------------------------------------------------------------------


class TestFederationDLQExport:
    """Tests for DLQ export helpers (Phase 50)."""

    def test_dlq_json_export_empty_list(self) -> None:
        """Empty list returns '[]'."""
        result = export_federation_dlq_summary_json([])
        assert json.loads(result) == []

    def test_dlq_json_export_single_item(self) -> None:
        """Single item produces valid JSON with expected fields."""
        item = _make_dlq_item()
        result = export_federation_dlq_summary_json([item])
        data = json.loads(result)
        assert len(data) == 1
        entry = data[0]
        assert entry["dlq_id"] == "fdlq_test123"
        assert entry["notification_id"] == "fn_test123"
        assert entry["approval_id"] == "fap_test123"
        assert entry["federation_id"] == "frp_test123"
        assert entry["channel"] == "webhook"
        assert entry["reason"] == "max_retries_exceeded"
        assert entry["status"] == "pending"
        assert entry["failure_count"] == 3
        assert entry["last_error"] == "Connection timeout"
        assert "created_at" in entry
        assert "updated_at" in entry

    def test_dlq_json_export_multiple_items(self) -> None:
        """Multiple items produce an array of objects."""
        item1 = _make_dlq_item(dlq_id="fdlq_a1")
        item2 = _make_dlq_item(dlq_id="fdlq_b2")
        result = export_federation_dlq_summary_json([item1, item2])
        data = json.loads(result)
        assert len(data) == 2
        assert data[0]["dlq_id"] == "fdlq_a1"
        assert data[1]["dlq_id"] == "fdlq_b2"

    def test_dlq_json_export_datetime_format(self) -> None:
        """Datetime fields are ISO format strings."""
        ts = datetime(2026, 6, 20, 12, 30, 0, tzinfo=timezone.utc)
        item = _make_dlq_item(created_at=ts, updated_at=ts)
        result = export_federation_dlq_summary_json([item])
        data = json.loads(result)
        assert data[0]["created_at"] == "2026-06-20T12:30:00+00:00"
        assert data[0]["updated_at"] == "2026-06-20T12:30:00+00:00"

    def test_dlq_csv_export_empty_list(self) -> None:
        """Empty list returns just the header row."""
        result = export_federation_dlq_summary_csv([])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0] == [
            "dlq_id",
            "notification_id",
            "approval_id",
            "federation_id",
            "channel",
            "reason",
            "status",
            "failure_count",
            "last_error",
            "created_at",
            "updated_at",
        ]

    def test_dlq_csv_export_single_item(self) -> None:
        """Single item produces header + one data row."""
        item = _make_dlq_item()
        result = export_federation_dlq_summary_csv([item])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 2
        assert rows[1][0] == "fdlq_test123"
        assert rows[1][1] == "fn_test123"
        assert rows[1][4] == "webhook"
        assert rows[1][5] == "max_retries_exceeded"
        assert rows[1][6] == "pending"
        assert rows[1][7] == "3"
        assert rows[1][8] == "Connection timeout"

    def test_dlq_csv_export_multiple_items(self) -> None:
        """Multiple items produce header + multiple data rows."""
        item1 = _make_dlq_item(dlq_id="fdlq_a1")
        item2 = _make_dlq_item(dlq_id="fdlq_b2")
        result = export_federation_dlq_summary_csv([item1, item2])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 3
        assert rows[1][0] == "fdlq_a1"
        assert rows[2][0] == "fdlq_b2"

    def test_dlq_csv_export_none_fields(self) -> None:
        """None fields become empty strings in CSV output."""
        item = _make_dlq_item(approval_id=None, federation_id=None, last_error=None)
        result = export_federation_dlq_summary_csv([item])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        # approval_id is column index 2, federation_id is 3, last_error is 8
        assert rows[1][2] == ""
        assert rows[1][3] == ""
        assert rows[1][8] == ""
