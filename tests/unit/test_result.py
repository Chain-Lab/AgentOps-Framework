"""Tests for AppRunResult model."""

import pytest

from agent_app.core.result import AppRunResult


class TestAppRunResult:
    def test_create_completed(self) -> None:
        res = AppRunResult(
            run_id="r1", status="completed", final_output="Hello!"
        )
        assert res.run_id == "r1"
        assert res.status == "completed"
        assert res.final_output == "Hello!"
        assert res.tool_calls == []
        assert res.latency_ms == 0
        assert res.error is None

    def test_create_failed(self) -> None:
        res = AppRunResult(
            run_id="r2",
            status="failed",
            error={"type": "APIError", "message": "timeout"},
        )
        assert res.status == "failed"
        assert res.error["type"] == "APIError"

    def test_create_interrupted(self) -> None:
        res = AppRunResult(
            run_id="r3",
            status="interrupted",
            interruptions=[{"approval_id": "apv_1"}],
        )
        assert len(res.interruptions) == 1
        assert res.interruptions[0]["approval_id"] == "apv_1"

    def test_defaults(self) -> None:
        res = AppRunResult(run_id="r4", status="completed")
        assert res.final_output is None
        assert res.handoffs == []
        assert res.usage == {}
        assert res.cost == {}
        assert res.trace_id is None

    def test_tool_calls(self) -> None:
        res = AppRunResult(
            run_id="r5",
            status="completed",
            tool_calls=[{"tool": "order.query", "status": "ok"}],
        )
        assert len(res.tool_calls) == 1
        assert res.tool_calls[0]["tool"] == "order.query"
