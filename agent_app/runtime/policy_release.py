"""Policy release service — orchestrates bundle creation, gate evaluation, promote, and rollback.

Phase 29: provides the release safety gate workflow.
Phase 30: adds RBAC, promotion lifecycle, and audit logging.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent_app.core.context import RunContext
from agent_app.governance.policy_rbac import PolicyReleasePermission, PolicyReleasePermissionChecker
from agent_app.governance.policy_promotion import PromotionRequest, PromotionRequestStatus
from agent_app.governance.audit import AuditEvent


class PolicyReleasePermissionError(Exception):
    """Raised when a policy release permission check fails."""

    pass


class PolicyReleaseService:
    """Orchestrates the policy release lifecycle.

    Coordinates policy bundle stores, replay runners, gate evaluators,
    and gate stores to provide:
    - Bundle creation with config hashing
    - Gate evaluation against replay results
    - Promote (requires passing gate by default)
    - Rollback to a previous bundle

    Args:
        bundle_store: Store for policy bundles.
        replay_runner: Runner for policy replays.
        replay_store: Optional store for persisting replay results.
        gate_evaluator: Evaluator for release gate rules.
        gate_store: Store for persisting gate results.
    """

    def __init__(
        self,
        bundle_store: Any,
        replay_runner: Any,
        replay_store: Any,
        gate_evaluator: Any,
        gate_store: Any,
        promotion_store: Any = None,
        permission_checker: Any = None,
        audit_logger: Any = None,
        allow_gate_bypass: bool = False,
    ) -> None:
        self._bundle_store = bundle_store
        self._replay_runner = replay_runner
        self._replay_store = replay_store
        self._gate_evaluator = gate_evaluator
        self._gate_store = gate_store
        self._promotion_store = promotion_store
        self._permission_checker = (
            permission_checker
            if permission_checker is not None
            else PolicyReleasePermissionChecker()
        )
        self._audit_logger = audit_logger
        self._allow_gate_bypass = allow_gate_bypass

    async def _check_permission(
        self, permission: PolicyReleasePermission, context: RunContext
    ) -> None:
        """Raise PolicyReleasePermissionError if the context lacks the permission."""
        if not await self._permission_checker.check(permission, context):
            await self._write_audit(
                "policy.promotion.permission_denied",
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                data={"required_permission": permission.value},
            )
            raise PolicyReleasePermissionError(
                f"Permission denied: '{permission.value}' required."
            )

    async def _write_audit(
        self,
        event_type: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        data: dict | None = None,
    ) -> None:
        """Write an audit event if an audit logger is configured."""
        if self._audit_logger is None:
            return
        await self._audit_logger.log(
            AuditEvent(
                event_id=f"ae_{uuid.uuid4().hex[:12]}",
                event_type=event_type,
                user_id=user_id,
                tenant_id=tenant_id,
                data=data or {},
            )
        )

    async def create_bundle(
        self,
        name: str,
        version: str,
        config_path: str,
        description: str | None = None,
        created_by: str | None = None,
    ) -> Any:
        """Create a new policy bundle.

        Reads the config content, computes a hash, and stores the bundle
        as a DRAFT.

        Args:
            name: Human-readable bundle name.
            version: Semantic version string.
            config_path: Path to the config file.
            description: Optional description of changes.
            created_by: Identity of who created the bundle.

        Returns:
            The created PolicyBundle.
        """
        from agent_app.governance.policy_bundle import (
            PolicyBundle,
            PolicyBundleStatus,
            compute_config_hash,
        )

        # Read config content for hashing
        config_content = ""
        try:
            config_content = Path(config_path).read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            config_content = config_path

        config_hash = compute_config_hash(config_content)

        bundle = PolicyBundle(
            bundle_id=f"pb_{uuid.uuid4().hex[:12]}",
            name=name,
            version=version,
            status=PolicyBundleStatus.DRAFT,
            config_path=config_path,
            config_hash=config_hash,
            description=description,
            created_by=created_by,
            created_at=datetime.now(timezone.utc),
        )
        return await self._bundle_store.create(bundle)

    async def run_gate(
        self,
        bundle_id: str,
        limit: int | None = None,
        tenant_id: str | None = None,
        tool_name: str | None = None,
        rule_id: str | None = None,
        created_by: str | None = None,
    ) -> Any:
        """Run a release gate evaluation for a bundle.

        Executes a replay with the given filters, evaluates the result
        against gate rules, and stores the gate result.

        Args:
            bundle_id: The bundle to evaluate.
            limit: Max decisions to replay.
            tenant_id: Filter by tenant.
            tool_name: Filter by tool name.
            rule_id: Filter by original rule name.
            created_by: Identity of who triggered the evaluation.

        Returns:
            PolicyGateResult with evaluation outcome.

        Raises:
            KeyError: If bundle_id not found.
        """
        from agent_app.governance.policy_bundle import PolicyBundle

        bundle = await self._bundle_store.get(bundle_id)
        if bundle is None:
            raise KeyError(
                f"Bundle '{bundle_id}' not found in policy bundle store."
            )

        # Run replay with filters
        replay_result = await self._replay_runner.run_replay(
            limit=limit,
            tenant_id=tenant_id,
            tool_name=tool_name,
            rule_id=rule_id,
        )

        # Persist replay result if store available
        if self._replay_store is not None:
            await self._replay_store.save(replay_result)

        # Evaluate gate
        gate_result = await self._gate_evaluator.evaluate(
            bundle=bundle,
            replay_result=replay_result,
            created_by=created_by,
        )

        # Store gate result
        return await self._gate_store.save(gate_result)

    async def promote(
        self,
        bundle_id: str,
        promoted_by: str | None = None,
        require_passing_gate: bool = True,
    ) -> Any:
        """Promote a bundle to ACTIVE.

        By default, requires that the bundle has at least one latest
        PASSED gate result.

        Args:
            bundle_id: The bundle to promote.
            promoted_by: Identity of who is promoting.
            require_passing_gate: If True, block promotion if latest gate failed.

        Returns:
            The promoted PolicyBundle (status: ACTIVE).

        Raises:
            KeyError: If bundle_id not found.
            ValueError: If latest gate failed and require_passing_gate is True.
        """
        from agent_app.governance.policy_bundle import PolicyBundleStatus

        bundle = await self._bundle_store.get(bundle_id)
        if bundle is None:
            raise KeyError(
                f"Bundle '{bundle_id}' not found in policy bundle store."
            )

        if require_passing_gate:
            # Check latest gate result for this bundle
            gate_results = await self._gate_store.list(bundle_id=bundle_id, limit=1)
            if gate_results:
                latest = gate_results[0]
                if not latest.passed:
                    raise ValueError(
                        f"Cannot promote bundle '{bundle_id}': "
                        f"latest gate result is {latest.status}. "
                        f"Gate result ID: {latest.gate_result_id}"
                    )

        # Promote: activate this bundle (archives previous active)
        return await self._bundle_store.activate(bundle_id)

    async def rollback(
        self,
        target_bundle_id: str,
        rolled_back_by: str | None = None,
    ) -> Any:
        """Rollback to a previous bundle.

        Re-activates the target bundle, archiving the current ACTIVE bundle.

        Args:
            target_bundle_id: The bundle to roll back to.
            rolled_back_by: Identity of who is rolling back.

        Returns:
            The re-activated PolicyBundle (status: ACTIVE).

        Raises:
            KeyError: If target_bundle_id not found.
        """
        bundle = await self._bundle_store.get(target_bundle_id)
        if bundle is None:
            raise KeyError(
                f"Bundle '{target_bundle_id}' not found in policy bundle store."
            )

        # Rollback = activate the target bundle
        return await self._bundle_store.activate(target_bundle_id)

    async def request_promotion(
        self,
        bundle_id: str,
        requested_by: str,
        context: RunContext,
        reason: str | None = None,
        gate_result_id: str | None = None,
    ) -> PromotionRequest:
        """Request promotion of a policy bundle.

        Args:
            bundle_id: The bundle to promote.
            requested_by: Identity of who is requesting promotion.
            context: Current run context for RBAC.
            reason: Optional reason for the promotion request.
            gate_result_id: Optional gate evaluation result reference.

        Returns:
            The created PromotionRequest.

        Raises:
            PolicyReleasePermissionError: If the context lacks PROMOTION_REQUEST permission.
            KeyError: If bundle_id not found.
        """
        await self._check_permission(
            PolicyReleasePermission.PROMOTION_REQUEST, context
        )

        bundle = await self._bundle_store.get(bundle_id)
        if bundle is None:
            raise KeyError(
                f"Bundle '{bundle_id}' not found in policy bundle store."
            )

        request = PromotionRequest(
            promotion_id=f"pr_{uuid.uuid4().hex[:12]}",
            bundle_id=bundle_id,
            gate_result_id=gate_result_id,
            requested_by=requested_by,
            tenant_id=context.tenant_id,
            reason=reason,
        )

        if self._promotion_store is not None:
            request = await self._promotion_store.create(request)

        await self._write_audit(
            "policy.promotion.requested",
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            data={
                "promotion_id": request.promotion_id,
                "bundle_id": bundle_id,
                "reason": reason,
            },
        )

        return request

    async def approve_promotion(
        self,
        promotion_id: str,
        approved_by: str,
        context: RunContext,
        reason: str | None = None,
    ) -> PromotionRequest:
        """Approve a pending promotion request.

        Args:
            promotion_id: The promotion request to approve.
            approved_by: Identity of who is approving.
            context: Current run context for RBAC.
            reason: Optional approval reason.

        Returns:
            The updated PromotionRequest.

        Raises:
            PolicyReleasePermissionError: If the context lacks PROMOTION_APPROVE permission.
            KeyError: If promotion_id not found.
        """
        await self._check_permission(
            PolicyReleasePermission.PROMOTION_APPROVE, context
        )

        if self._promotion_store is None:
            raise RuntimeError(
                "No promotion store configured. "
                "Set promotion_store to use promotion approval."
            )

        updated = await self._promotion_store.approve(
            promotion_id=promotion_id,
            approved_by=approved_by,
            reason=reason,
        )

        await self._write_audit(
            "policy.promotion.approved",
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            data={
                "promotion_id": promotion_id,
                "bundle_id": updated.bundle_id,
                "approved_by": approved_by,
                "reason": reason,
            },
        )

        return updated

    async def reject_promotion(
        self,
        promotion_id: str,
        rejected_by: str,
        context: RunContext,
        reason: str | None = None,
    ) -> PromotionRequest:
        """Reject a pending promotion request.

        Args:
            promotion_id: The promotion request to reject.
            rejected_by: Identity of who is rejecting.
            context: Current run context for RBAC.
            reason: Optional rejection reason.

        Returns:
            The updated PromotionRequest.

        Raises:
            PolicyReleasePermissionError: If the context lacks PROMOTION_REJECT permission.
            KeyError: If promotion_id not found.
        """
        await self._check_permission(
            PolicyReleasePermission.PROMOTION_REJECT, context
        )

        if self._promotion_store is None:
            raise RuntimeError(
                "No promotion store configured. "
                "Set promotion_store to use promotion rejection."
            )

        updated = await self._promotion_store.reject(
            promotion_id=promotion_id,
            rejected_by=rejected_by,
            reason=reason,
        )

        await self._write_audit(
            "policy.promotion.rejected",
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            data={
                "promotion_id": promotion_id,
                "bundle_id": updated.bundle_id,
                "rejected_by": rejected_by,
                "reason": reason,
            },
        )

        return updated

    async def execute_promotion(
        self,
        promotion_id: str,
        executed_by: str,
        context: RunContext,
        bypass_gate: bool = False,
        bypass_reason: str | None = None,
    ) -> Any:
        """Execute an approved promotion request.

        Validates the request is APPROVED, checks gate results,
        optionally allows gate bypass, then activates the bundle.

        Args:
            promotion_id: The promotion request to execute.
            executed_by: Identity of who is executing.
            context: Current run context for RBAC.
            bypass_gate: If True, allow execution even if gate failed.
            bypass_reason: Required reason when bypassing a failed gate.

        Returns:
            The activated PolicyBundle.

        Raises:
            PolicyReleasePermissionError: If the context lacks PROMOTION_EXECUTE or BYPASS_GATE.
            ValueError: If request is not APPROVED, or if gate failed without bypass.
            KeyError: If promotion_id or bundle_id not found.
        """
        await self._check_permission(
            PolicyReleasePermission.PROMOTION_EXECUTE, context
        )

        if self._promotion_store is None:
            raise RuntimeError(
                "No promotion store configured. "
                "Set promotion_store to use promotion execution."
            )

        request = await self._promotion_store.get(promotion_id)
        if request is None:
            raise KeyError(
                f"Promotion request '{promotion_id}' not found in promotion store."
            )

        if request.status != PromotionRequestStatus.APPROVED:
            raise ValueError(
                f"Cannot execute promotion '{promotion_id}': "
                f"request status is '{request.status}', must be approved."
            )

        # Check latest gate result for the bundle
        gate_results = await self._gate_store.list(bundle_id=request.bundle_id, limit=1)
        gate_failed = gate_results and not gate_results[0].passed

        if gate_failed:
            if bypass_gate and self._allow_gate_bypass:
                if not bypass_reason:
                    raise ValueError(
                        "bypass_reason is required when bypassing a failed gate."
                    )
                await self._check_permission(
                    PolicyReleasePermission.BYPASS_GATE, context
                )
                await self._write_audit(
                    "policy.gate.bypass_used",
                    user_id=context.user_id,
                    tenant_id=context.tenant_id,
                    data={
                        "promotion_id": promotion_id,
                        "bundle_id": request.bundle_id,
                        "gate_result_id": gate_results[0].gate_result_id,
                        "bypass_reason": bypass_reason,
                    },
                )
            else:
                await self._write_audit(
                    "policy.promotion.execute_blocked",
                    user_id=context.user_id,
                    tenant_id=context.tenant_id,
                    data={
                        "promotion_id": promotion_id,
                        "bundle_id": request.bundle_id,
                        "gate_result_id": (
                            gate_results[0].gate_result_id if gate_results else None
                        ),
                    },
                )
                raise ValueError(
                    f"Cannot execute promotion '{promotion_id}': "
                    f"latest gate for bundle '{request.bundle_id}' failed. "
                    f"Gate bypass is not enabled in config (allow_gate_bypass=false)."
                )

        # Activate the bundle
        activated = await self._bundle_store.activate(request.bundle_id)

        # Mark promotion as executed
        await self._promotion_store.mark_executed(
            promotion_id=promotion_id,
            executed_by=executed_by,
        )

        await self._write_audit(
            "policy.promotion.executed",
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            data={
                "promotion_id": promotion_id,
                "bundle_id": request.bundle_id,
                "executed_by": executed_by,
                "bypass_gate": bypass_gate,
            },
        )

        return activated

    @property
    def promotion_store(self) -> Any:
        """Access the underlying promotion store (for console integration)."""
        return self._promotion_store

    @property
    def gate_store(self) -> Any:
        """Access the underlying gate store (for console integration)."""
        return self._gate_store
