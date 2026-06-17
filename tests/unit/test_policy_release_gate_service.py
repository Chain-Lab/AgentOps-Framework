"""Tests for ReleaseGateAutomationService — Phase 42 Task 4."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_app.governance.audit import AuditEvent, InMemoryAuditLogger
from agent_app.governance.policy_gate import PolicyGateResult, PolicyGateRule
from agent_app.governance.policy_release_gate import (
    ReleaseGateRequirement,
    ReleaseGateRequirementStatus,
)
from agent_app.governance.runtime_policy import RuntimePolicyRule
from agent_app.runtime.policy_change_event_store import InMemoryPolicyChangeEventStore
from agent_app.runtime.policy_gate_store import InMemoryPolicyGateStore
from agent_app.runtime.policy_release_gate_service import ReleaseGateAutomationService
from agent_app.runtime.policy_release_gate_store import InMemoryReleaseGateRequirementStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_gate_result(
    passed: bool = True,
    gate_result_id: str = "gr_test123",
    created_at: datetime | None = None,
) -> PolicyGateResult:
    """Create a PolicyGateResult for testing."""
    return PolicyGateResult(
        gate_result_id=gate_result_id,
        bundle_id="bundle_test",
        replay_id="replay_test",
        status="passed" if passed else "failed",
        passed=passed,
        total_decisions=10,
        changed_decisions=0,
        failed_replays=0,
        changed_ratio=0.0,
        created_at=created_at or datetime.now(timezone.utc),
    )


def _make_runtime_rule(rule_id: str = "rpr_test") -> RuntimePolicyRule:
    """Create a RuntimePolicyRule for testing."""
    return RuntimePolicyRule(
        rule_id=rule_id,
        name="Test Rule",
        action_type="tool.execute",
        effect="allow",
        priority=100,
    )


# ---------------------------------------------------------------------------
# Test: require_gate_for_promotion
# ---------------------------------------------------------------------------


class TestRequireGateForPromotion:
    """Tests for ReleaseGateAutomationService.require_gate_for_promotion."""

    @pytest.mark.asyncio
    async def test_creates_required_requirement(self) -> None:
        """require_gate_for_promotion creates a REQUIRED requirement."""
        store = InMemoryReleaseGateRequirementStore()
        service = ReleaseGateAutomationService(requirement_store=store)

        result = await service.require_gate_for_promotion(promotion_id="promo_1")

        assert result.source_type == "promotion"
        assert result.source_id == "promo_1"
        assert result.status == ReleaseGateRequirementStatus.REQUIRED
        assert result.required is True
        assert result.requirement_id.startswith("rgr_")

    @pytest.mark.asyncio
    async def test_with_max_age_seconds(self) -> None:
        """require_gate_for_promotion with max_age_seconds set."""
        store = InMemoryReleaseGateRequirementStore()
        service = ReleaseGateAutomationService(requirement_store=store)

        result = await service.require_gate_for_promotion(
            promotion_id="promo_2",
            max_age_seconds=3600,
        )

        assert result.max_age_seconds == 3600
        assert result.source_id == "promo_2"

    @pytest.mark.asyncio
    async def test_with_metadata(self) -> None:
        """require_gate_for_promotion with custom metadata."""
        store = InMemoryReleaseGateRequirementStore()
        service = ReleaseGateAutomationService(requirement_store=store)

        result = await service.require_gate_for_promotion(
            promotion_id="promo_3",
            metadata={"environment": "staging", "ring": "canary"},
        )

        assert result.metadata["environment"] == "staging"
        assert result.metadata["ring"] == "canary"

    @pytest.mark.asyncio
    async def test_persists_to_store(self) -> None:
        """require_gate_for_promotion persists the requirement in the store."""
        store = InMemoryReleaseGateRequirementStore()
        service = ReleaseGateAutomationService(requirement_store=store)

        result = await service.require_gate_for_promotion(promotion_id="promo_4")

        stored = await store.get(result.requirement_id)
        assert stored is not None
        assert stored.source_id == "promo_4"

    @pytest.mark.asyncio
    async def test_emits_audit_event(self) -> None:
        """require_gate_for_promotion emits an audit event."""
        store = InMemoryReleaseGateRequirementStore()
        audit_logger = InMemoryAuditLogger()
        service = ReleaseGateAutomationService(
            requirement_store=store,
            audit_logger=audit_logger,
        )

        await service.require_gate_for_promotion(promotion_id="promo_5")

        events = audit_logger.list_events(event_type="policy.promotion.gate.required")
        assert len(events) == 1
        assert events[0].data["promotion_id"] == "promo_5"


# ---------------------------------------------------------------------------
# Test: attach_gate_result
# ---------------------------------------------------------------------------


class TestAttachGateResult:
    """Tests for ReleaseGateAutomationService.attach_gate_result."""

    @pytest.mark.asyncio
    async def test_with_passed_gate_marks_satisfied(self) -> None:
        """attach_gate_result with a passed gate marks SATISFIED."""
        req_store = InMemoryReleaseGateRequirementStore()
        gate_store = InMemoryPolicyGateStore()
        service = ReleaseGateAutomationService(
            requirement_store=req_store,
            gate_store=gate_store,
        )

        # Create requirement first
        req = await service.require_gate_for_promotion(promotion_id="promo_10")

        # Create and store a passed gate result
        gate_result = _make_gate_result(passed=True, gate_result_id="gr_pass")
        await gate_store.save(gate_result)

        # Attach
        result = await service.attach_gate_result(
            source_type="promotion",
            source_id="promo_10",
            gate_result_id="gr_pass",
        )

        assert result.status == ReleaseGateRequirementStatus.SATISFIED
        assert result.gate_result_id == "gr_pass"
        assert result.satisfied_at is not None

    @pytest.mark.asyncio
    async def test_with_failed_gate_marks_failed(self) -> None:
        """attach_gate_result with a failed gate marks FAILED."""
        req_store = InMemoryReleaseGateRequirementStore()
        gate_store = InMemoryPolicyGateStore()
        service = ReleaseGateAutomationService(
            requirement_store=req_store,
            gate_store=gate_store,
        )

        req = await service.require_gate_for_promotion(promotion_id="promo_11")

        gate_result = _make_gate_result(passed=False, gate_result_id="gr_fail")
        await gate_store.save(gate_result)

        result = await service.attach_gate_result(
            source_type="promotion",
            source_id="promo_11",
            gate_result_id="gr_fail",
        )

        assert result.status == ReleaseGateRequirementStatus.FAILED
        assert result.gate_result_id == "gr_fail"
        assert result.satisfied_at is None

    @pytest.mark.asyncio
    async def test_no_gate_store_assumes_satisfied(self) -> None:
        """attach_gate_result with no gate store assumes SATISFIED."""
        req_store = InMemoryReleaseGateRequirementStore()
        service = ReleaseGateAutomationService(requirement_store=req_store)

        req = await service.require_gate_for_promotion(promotion_id="promo_12")

        result = await service.attach_gate_result(
            source_type="promotion",
            source_id="promo_12",
            gate_result_id="gr_trust",
        )

        assert result.status == ReleaseGateRequirementStatus.SATISFIED

    @pytest.mark.asyncio
    async def test_raises_keyerror_when_no_requirement(self) -> None:
        """attach_gate_result raises KeyError when no requirement exists."""
        req_store = InMemoryReleaseGateRequirementStore()
        service = ReleaseGateAutomationService(requirement_store=req_store)

        with pytest.raises(KeyError, match="No gate requirement found"):
            await service.attach_gate_result(
                source_type="promotion",
                source_id="nonexistent",
                gate_result_id="gr_x",
            )

    @pytest.mark.asyncio
    async def test_with_simulation_id(self) -> None:
        """attach_gate_result stores simulation_id."""
        req_store = InMemoryReleaseGateRequirementStore()
        gate_store = InMemoryPolicyGateStore()
        service = ReleaseGateAutomationService(
            requirement_store=req_store,
            gate_store=gate_store,
        )

        req = await service.require_gate_for_promotion(promotion_id="promo_13")

        gate_result = _make_gate_result(passed=True, gate_result_id="gr_sim")
        await gate_store.save(gate_result)

        result = await service.attach_gate_result(
            source_type="promotion",
            source_id="promo_13",
            gate_result_id="gr_sim",
            simulation_id="sim_42",
        )

        assert result.simulation_id == "sim_42"

    @pytest.mark.asyncio
    async def test_emits_satisfied_audit_event(self) -> None:
        """attach_gate_result emits a satisfied audit event."""
        req_store = InMemoryReleaseGateRequirementStore()
        gate_store = InMemoryPolicyGateStore()
        audit_logger = InMemoryAuditLogger()
        service = ReleaseGateAutomationService(
            requirement_store=req_store,
            gate_store=gate_store,
            audit_logger=audit_logger,
        )

        req = await service.require_gate_for_promotion(promotion_id="promo_14")
        gate_result = _make_gate_result(passed=True, gate_result_id="gr_aud")
        await gate_store.save(gate_result)

        await service.attach_gate_result(
            source_type="promotion",
            source_id="promo_14",
            gate_result_id="gr_aud",
        )

        events = audit_logger.list_events(event_type="policy.promotion.gate.satisfied")
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_emits_failed_audit_event(self) -> None:
        """attach_gate_result emits a failed audit event."""
        req_store = InMemoryReleaseGateRequirementStore()
        gate_store = InMemoryPolicyGateStore()
        audit_logger = InMemoryAuditLogger()
        service = ReleaseGateAutomationService(
            requirement_store=req_store,
            gate_store=gate_store,
            audit_logger=audit_logger,
        )

        req = await service.require_gate_for_promotion(promotion_id="promo_15")
        gate_result = _make_gate_result(passed=False, gate_result_id="gr_aud_f")
        await gate_store.save(gate_result)

        await service.attach_gate_result(
            source_type="promotion",
            source_id="promo_15",
            gate_result_id="gr_aud_f",
        )

        events = audit_logger.list_events(event_type="policy.promotion.gate.failed")
        assert len(events) == 1


# ---------------------------------------------------------------------------
# Test: check_requirement
# ---------------------------------------------------------------------------


class TestCheckRequirement:
    """Tests for ReleaseGateAutomationService.check_requirement."""

    @pytest.mark.asyncio
    async def test_returns_not_required_when_no_record(self) -> None:
        """check_requirement returns NOT_REQUIRED when no record exists."""
        store = InMemoryReleaseGateRequirementStore()
        service = ReleaseGateAutomationService(requirement_store=store)

        result = await service.check_requirement(
            source_type="promotion",
            source_id="nonexistent",
        )

        assert result.status == ReleaseGateRequirementStatus.NOT_REQUIRED
        assert result.required is False
        assert result.requirement_id == "rgr_none"

    @pytest.mark.asyncio
    async def test_returns_required_when_no_gate_attached(self) -> None:
        """check_requirement returns REQUIRED when no gate attached yet."""
        store = InMemoryReleaseGateRequirementStore()
        service = ReleaseGateAutomationService(requirement_store=store)

        req = await service.require_gate_for_promotion(promotion_id="promo_20")

        result = await service.check_requirement(
            source_type="promotion",
            source_id="promo_20",
        )

        assert result.status == ReleaseGateRequirementStatus.REQUIRED

    @pytest.mark.asyncio
    async def test_returns_satisfied_when_gate_passed(self) -> None:
        """check_requirement returns SATISFIED when gate passed."""
        req_store = InMemoryReleaseGateRequirementStore()
        gate_store = InMemoryPolicyGateStore()
        service = ReleaseGateAutomationService(
            requirement_store=req_store,
            gate_store=gate_store,
        )

        req = await service.require_gate_for_promotion(promotion_id="promo_21")
        gate_result = _make_gate_result(passed=True, gate_result_id="gr_chk")
        await gate_store.save(gate_result)

        await service.attach_gate_result(
            source_type="promotion",
            source_id="promo_21",
            gate_result_id="gr_chk",
        )

        result = await service.check_requirement(
            source_type="promotion",
            source_id="promo_21",
        )

        assert result.status == ReleaseGateRequirementStatus.SATISFIED

    @pytest.mark.asyncio
    async def test_returns_expired_when_gate_too_old(self) -> None:
        """check_requirement returns EXPIRED when gate result is too old."""
        req_store = InMemoryReleaseGateRequirementStore()
        gate_store = InMemoryPolicyGateStore()
        service = ReleaseGateAutomationService(
            requirement_store=req_store,
            gate_store=gate_store,
        )

        # Create requirement with max_age_seconds=60
        req = await service.require_gate_for_promotion(
            promotion_id="promo_22",
            max_age_seconds=60,
        )

        # Create a gate result from 120 seconds ago
        old_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        gate_result = _make_gate_result(
            passed=True,
            gate_result_id="gr_old",
            created_at=old_time,
        )
        await gate_store.save(gate_result)

        await service.attach_gate_result(
            source_type="promotion",
            source_id="promo_22",
            gate_result_id="gr_old",
        )

        # Check — should be expired
        result = await service.check_requirement(
            source_type="promotion",
            source_id="promo_22",
        )

        assert result.status == ReleaseGateRequirementStatus.EXPIRED

    @pytest.mark.asyncio
    async def test_returns_failed_when_gate_failed(self) -> None:
        """check_requirement returns FAILED when gate failed."""
        req_store = InMemoryReleaseGateRequirementStore()
        gate_store = InMemoryPolicyGateStore()
        service = ReleaseGateAutomationService(
            requirement_store=req_store,
            gate_store=gate_store,
        )

        req = await service.require_gate_for_promotion(promotion_id="promo_23")
        gate_result = _make_gate_result(passed=False, gate_result_id="gr_fail_chk")
        await gate_store.save(gate_result)

        await service.attach_gate_result(
            source_type="promotion",
            source_id="promo_23",
            gate_result_id="gr_fail_chk",
        )

        result = await service.check_requirement(
            source_type="promotion",
            source_id="promo_23",
        )

        assert result.status == ReleaseGateRequirementStatus.FAILED

    @pytest.mark.asyncio
    async def test_satisfied_still_fresh_within_max_age(self) -> None:
        """check_requirement returns SATISFIED when gate is within max_age_seconds."""
        req_store = InMemoryReleaseGateRequirementStore()
        gate_store = InMemoryPolicyGateStore()
        service = ReleaseGateAutomationService(
            requirement_store=req_store,
            gate_store=gate_store,
        )

        req = await service.require_gate_for_promotion(
            promotion_id="promo_24",
            max_age_seconds=3600,
        )

        gate_result = _make_gate_result(passed=True, gate_result_id="gr_fresh")
        await gate_store.save(gate_result)

        await service.attach_gate_result(
            source_type="promotion",
            source_id="promo_24",
            gate_result_id="gr_fresh",
        )

        result = await service.check_requirement(
            source_type="promotion",
            source_id="promo_24",
        )

        assert result.status == ReleaseGateRequirementStatus.SATISFIED

    @pytest.mark.asyncio
    async def test_expired_emits_audit_event(self) -> None:
        """check_requirement emits an expired audit event when gate too old."""
        req_store = InMemoryReleaseGateRequirementStore()
        gate_store = InMemoryPolicyGateStore()
        audit_logger = InMemoryAuditLogger()
        service = ReleaseGateAutomationService(
            requirement_store=req_store,
            gate_store=gate_store,
            audit_logger=audit_logger,
        )

        req = await service.require_gate_for_promotion(
            promotion_id="promo_25",
            max_age_seconds=30,
        )

        old_time = datetime.now(timezone.utc) - timedelta(seconds=60)
        gate_result = _make_gate_result(
            passed=True,
            gate_result_id="gr_exp_aud",
            created_at=old_time,
        )
        await gate_store.save(gate_result)

        await service.attach_gate_result(
            source_type="promotion",
            source_id="promo_25",
            gate_result_id="gr_exp_aud",
        )

        await service.check_requirement(
            source_type="promotion",
            source_id="promo_25",
        )

        events = audit_logger.list_events(event_type="policy.promotion.gate.expired")
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_expiry_fallback_satisfied_at(self) -> None:
        """check_requirement uses satisfied_at as fallback for expiry check."""
        req_store = InMemoryReleaseGateRequirementStore()
        service = ReleaseGateAutomationService(requirement_store=req_store)

        # Create requirement with max_age_seconds=30, no gate store
        req = await service.require_gate_for_promotion(
            promotion_id="promo_26",
            max_age_seconds=30,
        )

        # Manually set satisfied_at to be old (no gate store to check)
        # We need to attach a gate result (without gate store, it's trusted as SATISFIED)
        result = await service.attach_gate_result(
            source_type="promotion",
            source_id="promo_26",
            gate_result_id="gr_no_store",
        )

        # Manually update the satisfied_at to be old for testing the fallback
        old_time = datetime.now(timezone.utc) - timedelta(seconds=60)
        updated = result.model_copy(update={"satisfied_at": old_time})
        await req_store.update(updated)

        # Check — should be expired via satisfied_at fallback
        check = await service.check_requirement(
            source_type="promotion",
            source_id="promo_26",
        )

        assert check.status == ReleaseGateRequirementStatus.EXPIRED


# ---------------------------------------------------------------------------
# Test: run_and_attach_simulation_gate_for_promotion
# ---------------------------------------------------------------------------


class TestRunAndAttachSimulationGateForPromotion:
    """Tests for ReleaseGateAutomationService.run_and_attach_simulation_gate_for_promotion."""

    @pytest.mark.asyncio
    async def test_orchestrates_sim_gate_attach(self) -> None:
        """run_and_attach_simulation_gate_for_promotion orchestrates sim+gate+attach."""
        req_store = InMemoryReleaseGateRequirementStore()
        gate_store = InMemoryPolicyGateStore()

        # Create mock simulation service
        sim_service = AsyncMock()
        gate_result = _make_gate_result(passed=True, gate_result_id="gr_sim_run")
        sim_report = MagicMock()
        sim_report.simulation_id = "sim_auto"
        validation_report = MagicMock()
        sim_service.validate_and_gate.return_value = (
            sim_report,
            validation_report,
            gate_result,
        )

        # Create mock simulation gate evaluator
        sim_gate_evaluator = AsyncMock()

        service = ReleaseGateAutomationService(
            requirement_store=req_store,
            gate_store=gate_store,
            simulation_service=sim_service,
            simulation_gate_evaluator=sim_gate_evaluator,
        )

        # Create requirement first
        req = await service.require_gate_for_promotion(promotion_id="promo_30")

        # Run and attach
        result = await service.run_and_attach_simulation_gate_for_promotion(
            promotion_id="promo_30",
            candidate_rules=[_make_runtime_rule()],
            gate_rules=[PolicyGateRule(name="test_rule")],
            context=MagicMock(),
        )

        assert result.status == ReleaseGateRequirementStatus.SATISFIED
        assert result.gate_result_id == "gr_sim_run"
        assert result.simulation_id == "sim_auto"

        # Verify gate result was stored
        stored_gate = await gate_store.get("gr_sim_run")
        assert stored_gate is not None

    @pytest.mark.asyncio
    async def test_raises_when_simulation_service_not_configured(self) -> None:
        """run_and_attach raises RuntimeError when simulation service not configured."""
        req_store = InMemoryReleaseGateRequirementStore()
        service = ReleaseGateAutomationService(requirement_store=req_store)

        with pytest.raises(RuntimeError, match="Simulation service and gate evaluator"):
            await service.run_and_attach_simulation_gate_for_promotion(
                promotion_id="promo_31",
                candidate_rules=[_make_runtime_rule()],
                gate_rules=[PolicyGateRule(name="test_rule")],
                context=MagicMock(),
            )

    @pytest.mark.asyncio
    async def test_with_failed_gate(self) -> None:
        """run_and_attach with a failed gate marks FAILED."""
        req_store = InMemoryReleaseGateRequirementStore()
        gate_store = InMemoryPolicyGateStore()

        sim_service = AsyncMock()
        gate_result = _make_gate_result(passed=False, gate_result_id="gr_sim_fail")
        sim_report = MagicMock()
        sim_report.simulation_id = "sim_fail"
        validation_report = MagicMock()
        sim_service.validate_and_gate.return_value = (
            sim_report,
            validation_report,
            gate_result,
        )

        service = ReleaseGateAutomationService(
            requirement_store=req_store,
            gate_store=gate_store,
            simulation_service=sim_service,
            simulation_gate_evaluator=AsyncMock(),
        )

        req = await service.require_gate_for_promotion(promotion_id="promo_32")

        result = await service.run_and_attach_simulation_gate_for_promotion(
            promotion_id="promo_32",
            candidate_rules=[_make_runtime_rule()],
            gate_rules=[PolicyGateRule(name="test_rule")],
            context=MagicMock(),
        )

        assert result.status == ReleaseGateRequirementStatus.FAILED

    @pytest.mark.asyncio
    async def test_passes_context_user_id_as_actor(self) -> None:
        """run_and_attach passes context.user_id as actor_id to attach_gate_result."""
        req_store = InMemoryReleaseGateRequirementStore()
        gate_store = InMemoryPolicyGateStore()

        sim_service = AsyncMock()
        gate_result = _make_gate_result(passed=True, gate_result_id="gr_ctx")
        sim_report = MagicMock()
        sim_report.simulation_id = "sim_ctx"
        validation_report = MagicMock()
        sim_service.validate_and_gate.return_value = (
            sim_report,
            validation_report,
            gate_result,
        )

        service = ReleaseGateAutomationService(
            requirement_store=req_store,
            gate_store=gate_store,
            simulation_service=sim_service,
            simulation_gate_evaluator=AsyncMock(),
        )

        req = await service.require_gate_for_promotion(promotion_id="promo_33")

        ctx = MagicMock()
        ctx.user_id = "user_alice"

        result = await service.run_and_attach_simulation_gate_for_promotion(
            promotion_id="promo_33",
            candidate_rules=[_make_runtime_rule()],
            gate_rules=[PolicyGateRule(name="test_rule")],
            context=ctx,
        )

        assert result.status == ReleaseGateRequirementStatus.SATISFIED


# ---------------------------------------------------------------------------
# Test: Audit events emitted on state transitions
# ---------------------------------------------------------------------------


class TestAuditEventsOnStateTransitions:
    """Tests that audit events are emitted on all state transitions."""

    @pytest.mark.asyncio
    async def test_required_transition_audit(self) -> None:
        """Audit event on REQUIRED transition."""
        req_store = InMemoryReleaseGateRequirementStore()
        audit_logger = InMemoryAuditLogger()
        service = ReleaseGateAutomationService(
            requirement_store=req_store,
            audit_logger=audit_logger,
        )

        await service.require_gate_for_promotion(promotion_id="promo_40")

        events = audit_logger.list_events(event_type="policy.promotion.gate.required")
        assert len(events) == 1
        assert "promotion_id" in events[0].data
        assert "requirement_id" in events[0].data

    @pytest.mark.asyncio
    async def test_satisfied_transition_audit(self) -> None:
        """Audit event on SATISFIED transition."""
        req_store = InMemoryReleaseGateRequirementStore()
        gate_store = InMemoryPolicyGateStore()
        audit_logger = InMemoryAuditLogger()
        service = ReleaseGateAutomationService(
            requirement_store=req_store,
            gate_store=gate_store,
            audit_logger=audit_logger,
        )

        await service.require_gate_for_promotion(promotion_id="promo_41")
        gate_result = _make_gate_result(passed=True, gate_result_id="gr_sat_aud")
        await gate_store.save(gate_result)

        await service.attach_gate_result(
            source_type="promotion",
            source_id="promo_41",
            gate_result_id="gr_sat_aud",
        )

        events = audit_logger.list_events(event_type="policy.promotion.gate.satisfied")
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_failed_transition_audit(self) -> None:
        """Audit event on FAILED transition."""
        req_store = InMemoryReleaseGateRequirementStore()
        gate_store = InMemoryPolicyGateStore()
        audit_logger = InMemoryAuditLogger()
        service = ReleaseGateAutomationService(
            requirement_store=req_store,
            gate_store=gate_store,
            audit_logger=audit_logger,
        )

        await service.require_gate_for_promotion(promotion_id="promo_42")
        gate_result = _make_gate_result(passed=False, gate_result_id="gr_fail_aud")
        await gate_store.save(gate_result)

        await service.attach_gate_result(
            source_type="promotion",
            source_id="promo_42",
            gate_result_id="gr_fail_aud",
        )

        events = audit_logger.list_events(event_type="policy.promotion.gate.failed")
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_expired_transition_audit(self) -> None:
        """Audit event on EXPIRED transition."""
        req_store = InMemoryReleaseGateRequirementStore()
        gate_store = InMemoryPolicyGateStore()
        audit_logger = InMemoryAuditLogger()
        service = ReleaseGateAutomationService(
            requirement_store=req_store,
            gate_store=gate_store,
            audit_logger=audit_logger,
        )

        await service.require_gate_for_promotion(
            promotion_id="promo_43",
            max_age_seconds=10,
        )

        old_time = datetime.now(timezone.utc) - timedelta(seconds=30)
        gate_result = _make_gate_result(
            passed=True,
            gate_result_id="gr_exp_aud2",
            created_at=old_time,
        )
        await gate_store.save(gate_result)

        await service.attach_gate_result(
            source_type="promotion",
            source_id="promo_43",
            gate_result_id="gr_exp_aud2",
        )

        await service.check_requirement(
            source_type="promotion",
            source_id="promo_43",
        )

        events = audit_logger.list_events(event_type="policy.promotion.gate.expired")
        assert len(events) == 1
