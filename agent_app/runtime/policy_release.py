"""Policy release service — orchestrates bundle creation, gate evaluation, promote, and rollback.

Phase 29: provides the release safety gate workflow.
Phase 30: adds RBAC, promotion lifecycle, and audit logging.
Phase 34: adds change event emission and auto-refresh after state changes.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent_app.core.context import RunContext
from agent_app.governance.policy_change_event import PolicyChangeEvent, PolicyChangeEventType
from agent_app.governance.policy_rbac import PolicyReleasePermission, PolicyReleasePermissionChecker
from agent_app.governance.policy_promotion import PromotionRequest, PromotionRequestStatus
from agent_app.governance.audit import AuditEvent
from agent_app.governance.policy_release_gate import ReleaseGateRequirementStatus

logger = logging.getLogger(__name__)


class PolicyReleasePermissionError(PermissionError):
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
        activation_store: Any = None,
        policy_resolver: Any = None,
        environment_store: Any = None,
        ring_store: Any = None,
        ring_assignment_store: Any = None,
        ring_router: Any = None,
        event_store: Any = None,
        reload_manager: Any = None,
        strict: bool = False,
        release_gate_automation_service: Any = None,
        require_simulation_gate_for_promotion: bool = False,
        require_promotion_approval: bool = True,
        simulation_gate_max_age_seconds: int | None = None,
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
        self._activation_store = activation_store
        self._policy_resolver = policy_resolver
        self._environment_store = environment_store
        self._ring_store = ring_store
        self._ring_assignment_store = ring_assignment_store
        self._ring_router = ring_router
        self._event_store = event_store
        self._reload_manager = reload_manager
        self._strict = strict
        self._release_gate_automation_service = release_gate_automation_service
        self._require_simulation_gate_for_promotion = require_simulation_gate_for_promotion
        self._require_promotion_approval = require_promotion_approval
        self._simulation_gate_max_age_seconds = simulation_gate_max_age_seconds

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

    async def _emit_change_event(
        self,
        event_type,  # PolicyChangeEventType
        environment: str | None = None,
        ring_name: str | None = None,
        bundle_id: str | None = None,
        activation_id: str | None = None,
        assignment_id: str | None = None,
        actor_id: str | None = None,
        reason: str | None = None,
        data: dict | None = None,
    ) -> None:
        """Emit a policy change event if event_store is configured.

        If emission fails and strict mode is not enabled, log and continue.
        If strict mode is enabled, re-raise the exception.
        """
        if self._event_store is None:
            return

        event = PolicyChangeEvent(
            event_id=f"pce_{uuid.uuid4().hex[:12]}",
            event_type=event_type,
            environment=environment,
            ring_name=ring_name,
            bundle_id=bundle_id,
            activation_id=activation_id,
            assignment_id=assignment_id,
            actor_id=actor_id,
            reason=reason,
            data=data or {},
            created_at=datetime.now(timezone.utc),
        )

        try:
            await self._event_store.append(event)
        except Exception:
            if self._strict:
                raise
            # Non-strict: swallow the error, don't corrupt main state transition
            logger.debug("Change event emission failed for %s", event_type)

    async def _auto_refresh_resolver(
        self,
        environment: str | None = None,
        ring_name: str | None = None,
    ) -> None:
        """If reload_manager is configured, refresh resolver for target."""
        if self._reload_manager is None:
            return
        try:
            await self._reload_manager.refresh_resolver(environment, ring_name)
        except Exception:
            if self._strict:
                raise

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
        result = await self._bundle_store.create(bundle)
        await self._emit_change_event(
            PolicyChangeEventType.BUNDLE_CREATED,
            bundle_id=result.bundle_id,
            actor_id=created_by,
        )
        return result

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
        result = await self._gate_store.save(gate_result)
        await self._emit_change_event(
            PolicyChangeEventType.GATE_COMPLETED,
            bundle_id=bundle_id,
            actor_id=created_by,
        )
        return result

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
        result = await self._bundle_store.activate(bundle_id)
        await self._emit_change_event(
            PolicyChangeEventType.PROMOTION_EXECUTED,
            bundle_id=bundle_id,
            actor_id=promoted_by,
        )
        return result

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

        # Phase 42: Auto-create simulation gate requirement if configured
        if self._require_simulation_gate_for_promotion and self._release_gate_automation_service is not None:
            gate_req = await self._release_gate_automation_service.require_gate_for_promotion(
                request.promotion_id,
                max_age_seconds=self._simulation_gate_max_age_seconds,
            )
            # Store requirement_id on the request if possible
            try:
                request = request.model_copy(update={
                    "simulation_gate_required": True,
                    "simulation_gate_requirement_id": gate_req.requirement_id,
                })
                if self._promotion_store is not None:
                    request = await self._promotion_store.update(request)
            except Exception:
                pass  # If store doesn't support update, just proceed

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
        environment: str = "prod",
        reason: str | None = None,
    ) -> Any:
        """Execute a promotion request.

        Validates the request is APPROVED (unless require_promotion_approval
        was set to False at construction time, in which case any status is
        accepted), checks gate results, optionally allows gate bypass, then
        activates the bundle.

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
            ValueError: If request is not APPROVED and require_promotion_approval is True
                (the default), or if gate failed without bypass.
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

        if self._require_promotion_approval and request.status != PromotionRequestStatus.APPROVED:
            raise ValueError(
                f"Cannot execute promotion '{promotion_id}': "
                f"request status is '{request.status}', must be approved."
            )

        # Phase 42: Check simulation gate requirement
        if self._require_simulation_gate_for_promotion and self._release_gate_automation_service is not None:
            gate_req = await self._release_gate_automation_service.check_requirement(
                "promotion", promotion_id
            )
            if gate_req.status == ReleaseGateRequirementStatus.REQUIRED:
                await self._write_audit(
                    "policy.promotion.gate.execution_blocked",
                    user_id=context.user_id,
                    tenant_id=context.tenant_id,
                    data={
                        "promotion_id": promotion_id,
                        "reason": "simulation_gate_required",
                    },
                )
                raise ValueError(
                    f"Cannot execute promotion '{promotion_id}': "
                    f"simulation gate is required but no gate result has been attached."
                )
            elif gate_req.status == ReleaseGateRequirementStatus.FAILED:
                raise ValueError(
                    f"Cannot execute promotion '{promotion_id}': "
                    f"simulation gate failed."
                )
            elif gate_req.status == ReleaseGateRequirementStatus.EXPIRED:
                raise ValueError(
                    f"Cannot execute promotion '{promotion_id}': "
                    f"simulation gate result has expired."
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

        # Phase 31: Create activation record
        from agent_app.governance.policy_activation import PolicyActivation

        bundle = await self._bundle_store.get(request.bundle_id)

        if self._activation_store is not None:
            activation = PolicyActivation(
                activation_id=f"pa_{uuid.uuid4().hex[:12]}",
                environment=environment,
                bundle_id=request.bundle_id,
                config_hash=bundle.config_hash,
                promotion_id=promotion_id,
                activated_by=executed_by,
                reason=reason,
            )
            activation = await self._activation_store.activate(activation)
            await self._write_audit(
                "policy.activation.created",
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                data={
                    "activation_id": activation.activation_id,
                    "environment": environment,
                    "bundle_id": request.bundle_id,
                    "config_hash": bundle.config_hash,
                    "promotion_id": promotion_id,
                    "activated_by": executed_by,
                    "reason": reason,
                },
            )
            await self._emit_change_event(
                PolicyChangeEventType.ACTIVATION_CREATED,
                environment=environment,
                activation_id=activation.activation_id,
                bundle_id=request.bundle_id,
                actor_id=executed_by,
            )
        else:
            activated = await self._bundle_store.activate(request.bundle_id)
            activation = activated

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
                "environment": environment,
            },
        )

        return activation

    @property
    def promotion_store(self) -> Any:
        """Access the underlying promotion store (for console integration)."""
        return self._promotion_store

    @property
    def gate_store(self) -> Any:
        """Access the underlying gate store (for console integration)."""
        return self._gate_store

    async def get_active_policy(self, environment: str = "prod") -> Any | None:
        """Return the active policy bundle for the given environment."""
        if self._policy_resolver is None:
            return None
        return await self._policy_resolver.resolve_active_bundle(environment)

    async def require_active_policy(self, environment: str = "prod") -> Any:
        """Return the active policy bundle, raising if none is active."""
        if self._policy_resolver is None:
            raise RuntimeError("Policy resolver not configured.")
        return await self._policy_resolver.require_active_bundle(environment)

    async def list_activations(self, environment: str | None = None) -> list[Any]:
        """List policy activations, optionally filtered by environment."""
        if self._activation_store is None:
            return []
        return await self._activation_store.list(environment=environment)

    @property
    def activation_store(self) -> Any:
        """Access the underlying activation store (for console integration)."""
        return self._activation_store

    @property
    def policy_resolver(self) -> Any:
        """Access the underlying policy resolver (for console integration)."""
        return self._policy_resolver

    @property
    def environment_store(self) -> Any:
        """Access the underlying environment store."""
        return self._environment_store

    @property
    def ring_store(self) -> Any:
        """Access the underlying release ring store."""
        return self._ring_store

    @property
    def ring_assignment_store(self) -> Any:
        """Access the underlying ring assignment store."""
        return self._ring_assignment_store

    @property
    def event_store(self) -> Any:
        """Access the underlying event store."""
        return self._event_store

    @property
    def reload_manager(self) -> Any:
        """Access the underlying reload manager."""
        return self._reload_manager

    async def create_ring(
        self,
        environment: str,
        name: str,
        created_by: str,
        context: RunContext,
        description: str | None = None,
        is_default: bool = False,
    ) -> Any:
        """Create a release ring. Requires RING_CREATE permission."""
        await self._check_permission(PolicyReleasePermission.RING_CREATE, context)
        if self._ring_store is None:
            raise RuntimeError("No ring store configured.")

        from agent_app.governance.policy_ring import ReleaseRing

        ring = ReleaseRing(
            ring_id=f"ring_{uuid.uuid4().hex[:12]}",
            environment=environment,
            name=name,
            description=description,
            is_default=is_default,
        )
        ring = await self._ring_store.create(ring)
        await self._write_audit(
            "policy.ring.created",
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            data={
                "ring_id": ring.ring_id,
                "environment": environment,
                "name": name,
                "is_default": is_default,
            },
        )
        return ring

    async def assign_activation_to_ring(
        self,
        environment: str,
        ring_name: str,
        activation_id: str,
        assigned_by: str,
        context: RunContext,
        reason: str | None = None,
    ) -> Any:
        """Assign an activation to a ring. Requires RING_ASSIGN permission."""
        await self._check_permission(PolicyReleasePermission.RING_ASSIGN, context)
        if self._ring_assignment_store is None:
            raise RuntimeError("No ring assignment store configured.")

        # Validate activation exists and belongs to same environment
        activation = await self._activation_store.get(activation_id)
        if activation is None:
            raise KeyError(f"Activation '{activation_id}' not found.")
        if activation.environment != environment:
            raise ValueError(
                f"Activation '{activation_id}' belongs to environment "
                f"'{activation.environment}', not '{environment}'."
            )

        # Validate bundle exists
        bundle = await self._bundle_store.get(activation.bundle_id)
        if bundle is None:
            raise KeyError(f"Bundle '{activation.bundle_id}' not found.")

        # Create assignment
        from agent_app.governance.policy_ring_assignment import RingActivationAssignment

        assignment = RingActivationAssignment(
            assignment_id=f"ra_{uuid.uuid4().hex[:12]}",
            environment=environment,
            ring_name=ring_name,
            activation_id=activation_id,
            bundle_id=activation.bundle_id,
            config_hash=activation.config_hash,
            assigned_by=assigned_by,
            reason=reason,
        )
        assignment = await self._ring_assignment_store.assign(assignment)
        await self._write_audit(
            "policy.ring.assignment.created",
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            data={
                "assignment_id": assignment.assignment_id,
                "environment": environment,
                "ring_name": ring_name,
                "activation_id": activation_id,
                "bundle_id": activation.bundle_id,
            },
        )
        await self._emit_change_event(
            PolicyChangeEventType.RING_ASSIGNED,
            environment=environment,
            ring_name=ring_name,
            activation_id=activation_id,
            bundle_id=activation.bundle_id,
            assignment_id=assignment.assignment_id,
            actor_id=assigned_by,
            reason=reason,
        )
        await self._auto_refresh_resolver(environment, ring_name)
        return assignment

    async def promote_canary_to_stable(
        self,
        environment: str,
        canary_ring: str,
        stable_ring: str,
        promoted_by: str,
        context: RunContext,
        reason: str | None = None,
    ) -> Any:
        """Promote canary ring's activation to stable ring. Requires RING_PROMOTE permission."""
        await self._check_permission(PolicyReleasePermission.RING_PROMOTE, context)
        if self._ring_assignment_store is None:
            raise RuntimeError("No ring assignment store configured.")

        # Get canary's active assignment
        canary_assignment = await self._ring_assignment_store.get_active(
            environment, canary_ring
        )
        if canary_assignment is None:
            raise KeyError(
                f"No active assignment for ring '{canary_ring}' in environment '{environment}'."
            )

        # Assign the same activation to stable
        result = await self.assign_activation_to_ring(
            environment=environment,
            ring_name=stable_ring,
            activation_id=canary_assignment.activation_id,
            assigned_by=promoted_by,
            context=context,
            reason=reason or f"Promoted from {canary_ring} to {stable_ring}",
        )
        await self._emit_change_event(
            PolicyChangeEventType.RING_PROMOTED,
            environment=environment,
            ring_name=stable_ring,
            activation_id=canary_assignment.activation_id,
            actor_id=promoted_by,
            reason=reason,
        )
        return result

    async def disable_ring(
        self,
        environment: str,
        ring_name: str,
        disabled_by: str,
        context: RunContext,
        reason: str | None = None,
    ) -> Any:
        """Disable a ring. Requires RING_DISABLE permission."""
        await self._check_permission(PolicyReleasePermission.RING_DISABLE, context)
        if self._ring_store is None:
            raise RuntimeError("No ring store configured.")
        ring = await self._ring_store.disable(environment, ring_name)
        await self._write_audit(
            "policy.ring.disabled",
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            data={
                "environment": environment,
                "ring_name": ring_name,
                "disabled_by": disabled_by,
                "reason": reason,
            },
        )
        await self._emit_change_event(
            PolicyChangeEventType.RING_DISABLED,
            environment=environment,
            ring_name=ring_name,
            actor_id=disabled_by,
            reason=reason,
        )
        await self._auto_refresh_resolver(environment, ring_name)
        return ring

    async def enable_ring(
        self,
        environment: str,
        ring_name: str,
        enabled_by: str,
        context: RunContext,
        reason: str | None = None,
    ) -> Any:
        """Enable a ring. Requires RING_ENABLE permission."""
        await self._check_permission(PolicyReleasePermission.RING_ENABLE, context)
        if self._ring_store is None:
            raise RuntimeError("No ring store configured.")
        ring = await self._ring_store.enable(environment, ring_name)
        await self._write_audit(
            "policy.ring.enabled",
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            data={
                "environment": environment,
                "ring_name": ring_name,
                "enabled_by": enabled_by,
                "reason": reason,
            },
        )
        await self._emit_change_event(
            PolicyChangeEventType.RING_ENABLED,
            environment=environment,
            ring_name=ring_name,
            actor_id=enabled_by,
            reason=reason,
        )
        await self._auto_refresh_resolver(environment, ring_name)
        return ring

    async def rollback_environment(
        self,
        environment: str,
        rolled_back_by: str,
        context: RunContext,
        target_activation_id: str | None = None,
        reason: str | None = None,
    ) -> Any:
        """Roll back an environment to a previous activation.

        Phase 32: Creates a new activation pointing to the previous bundle.
        Requires ROLLBACK_EXECUTE permission.

        Args:
            environment: The environment to roll back.
            rolled_back_by: Who is performing the rollback.
            context: Run context for RBAC.
            target_activation_id: Optional specific activation to roll back to.
            reason: Optional rollback reason.

        Returns:
            The new rollback PolicyActivation.

        Raises:
            PolicyReleasePermissionError: If ROLLBACK_EXECUTE permission missing.
            ValueError: If no previous activation or target is wrong environment.
            KeyError: If target activation not found.
        """
        await self._check_permission(
            PolicyReleasePermission.ROLLBACK_EXECUTE, context
        )

        if self._activation_store is None:
            raise RuntimeError(
                "No activation store configured. "
                "Set activation_store to use environment rollback."
            )

        target_activation = None

        if target_activation_id is not None:
            target_activation = await self._activation_store.get(target_activation_id)
            if target_activation is None:
                raise KeyError(
                    f"Activation '{target_activation_id}' not found in activation store."
                )
            if target_activation.environment != environment:
                raise ValueError(
                    f"Target activation '{target_activation_id}' belongs to "
                    f"environment '{target_activation.environment}', not '{environment}'."
                )
        else:
            target_activation = await self._activation_store.get_previous_activation(
                environment
            )
            if target_activation is None:
                raise ValueError(
                    f"No previous activation found for environment '{environment}'."
                )

        # Validate the target bundle still exists in bundle_store
        bundle = await self._bundle_store.get(target_activation.bundle_id)
        if bundle is None:
            raise ValueError(
                f"Target bundle '{target_activation.bundle_id}' no longer exists "
                f"in bundle store. Cannot roll back to it."
            )

        result = await self._activation_store.rollback_to_activation(
            environment=environment,
            target_activation_id=target_activation.activation_id,
            rolled_back_by=rolled_back_by,
            reason=reason,
        )

        # Clear resolver cache for the environment
        if self._policy_resolver is not None:
            if hasattr(self._policy_resolver, "refresh"):
                self._policy_resolver.refresh(environment)
            elif hasattr(self._policy_resolver, "clear_cache"):
                self._policy_resolver.clear_cache()

        await self._write_audit(
            "policy.activation.rollback_completed",
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            data={
                "environment": environment,
                "target_activation_id": target_activation.activation_id,
                "target_bundle_id": target_activation.bundle_id,
                "new_activation_id": result.activation_id,
                "rolled_back_by": rolled_back_by,
                "reason": reason,
            },
        )

        await self._emit_change_event(
            PolicyChangeEventType.ACTIVATION_ROLLED_BACK,
            environment=environment,
            activation_id=result.activation_id,
            bundle_id=target_activation.bundle_id,
            actor_id=rolled_back_by,
            reason=reason,
        )
        await self._auto_refresh_resolver(environment)

        return result

    async def disable_policy_environment(
        self,
        environment: str,
        disabled_by: str,
        context: RunContext,
        reason: str,
    ) -> Any:
        """Disable a policy environment.

        Phase 32: Blocks active policy resolution for the environment.
        Requires ENVIRONMENT_DISABLE permission. Requires non-empty reason.
        """
        await self._check_permission(
            PolicyReleasePermission.ENVIRONMENT_DISABLE, context
        )

        if not reason or not reason.strip():
            raise ValueError(
                "A non-empty reason is required when disabling a policy environment."
            )

        if self._environment_store is None:
            raise RuntimeError(
                "No environment store configured. "
                "Set environment_store to use environment disable."
            )

        state = await self._environment_store.disable(
            environment=environment,
            disabled_by=disabled_by,
            reason=reason,
        )

        # Clear resolver cache for the environment
        if self._policy_resolver is not None:
            if hasattr(self._policy_resolver, "refresh"):
                self._policy_resolver.refresh(environment)
            elif hasattr(self._policy_resolver, "clear_cache"):
                self._policy_resolver.clear_cache()

        await self._write_audit(
            "policy.environment.disabled",
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            data={
                "environment": environment,
                "disabled_by": disabled_by,
                "reason": reason,
            },
        )

        await self._emit_change_event(
            PolicyChangeEventType.ENVIRONMENT_DISABLED,
            environment=environment,
            actor_id=disabled_by,
            reason=reason,
        )
        await self._auto_refresh_resolver(environment)

        return state

    async def enable_policy_environment(
        self,
        environment: str,
        enabled_by: str,
        context: RunContext,
        reason: str | None = None,
    ) -> Any:
        """Re-enable a disabled policy environment.

        Phase 32: Restores active policy resolution for the environment.
        Requires ENVIRONMENT_ENABLE permission.
        """
        await self._check_permission(
            PolicyReleasePermission.ENVIRONMENT_ENABLE, context
        )

        if self._environment_store is None:
            raise RuntimeError(
                "No environment store configured. "
                "Set environment_store to use environment enable."
            )

        state = await self._environment_store.enable(
            environment=environment,
            enabled_by=enabled_by,
            reason=reason,
        )

        # Clear resolver cache for the environment
        if self._policy_resolver is not None:
            if hasattr(self._policy_resolver, "refresh"):
                self._policy_resolver.refresh(environment)
            elif hasattr(self._policy_resolver, "clear_cache"):
                self._policy_resolver.clear_cache()

        await self._write_audit(
            "policy.environment.enabled",
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            data={
                "environment": environment,
                "enabled_by": enabled_by,
                "reason": reason,
            },
        )

        await self._emit_change_event(
            PolicyChangeEventType.ENVIRONMENT_ENABLED,
            environment=environment,
            actor_id=enabled_by,
            reason=reason,
        )
        await self._auto_refresh_resolver(environment)

        return state
