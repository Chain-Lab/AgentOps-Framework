"""Policy release RBAC — permission enum and checker for policy governance actions."""

from __future__ import annotations

from enum import StrEnum

from agent_app.core.context import RunContext


class PolicyReleasePermission(StrEnum):
    """Permissions governing policy release operations.

    Attributes:
        BUNDLE_CREATE: Create a new policy bundle.
        GATE_RUN: Execute a policy gate evaluation.
        PROMOTION_REQUEST: Request promotion of a policy bundle.
        PROMOTION_APPROVE: Approve a policy promotion request.
        PROMOTION_REJECT: Reject a policy promotion request.
        PROMOTION_EXECUTE: Execute a policy promotion.
        ROLLBACK_EXECUTE: Execute a policy rollback.
        BYPASS_GATE: Bypass a policy gate.
        ENVIRONMENT_DISABLE: Disable a policy environment.
        ENVIRONMENT_ENABLE: Enable a policy environment.
        ENVIRONMENT_VIEW: View policy environment state.
        RING_CREATE: Create a release ring.
        RING_ASSIGN: Assign a ring to an activation.
        RING_PROMOTE: Promote a policy bundle to a ring.
        RING_DISABLE: Disable a release ring.
        RING_ENABLE: Enable a release ring.
        RING_VIEW: View release ring state.
        RELOAD_REQUEST: Request a policy reload (requires explicit permission).
        RELOAD_VIEW: View policy reload status (default-allowed).
        EVENT_VIEW: View policy change events (default-allowed).
        ROUTING_SIMULATE: Simulate policy routing decisions (requires explicit permission).
        ROLLOUT_CREATE: Create a policy rollout.
        ROLLOUT_START: Start a policy rollout.
        ROLLOUT_EXECUTE: Execute a policy rollout.
        ROLLOUT_CANCEL: Cancel a policy rollout.
        ROLLOUT_VIEW: View policy rollout state (default-allowed).
        ROLLOUT_APPROVAL_REQUEST: Request approval for a rollout step (requires explicit permission).
        ROLLOUT_APPROVAL_APPROVE: Approve a rollout step (requires explicit permission).
        ROLLOUT_APPROVAL_REJECT: Reject a rollout step (requires explicit permission).
        ROLLOUT_APPROVAL_VIEW: View rollout approval state (default-allowed).
        FEDERATION_TARGET_CREATE: Create a federated rollout target.
        FEDERATION_TARGET_VIEW: View federated rollout targets (default-allowed).
        FEDERATION_TARGET_ENABLE: Enable a federated rollout target.
        FEDERATION_TARGET_DISABLE: Disable a federated rollout target.
        FEDERATION_PLAN_CREATE: Create a federated rollout plan.
        FEDERATION_PLAN_START: Start a federated rollout plan.
        FEDERATION_PLAN_EXECUTE: Execute a federated rollout plan.
        FEDERATION_PLAN_CANCEL: Cancel a federated rollout plan.
        FEDERATION_PLAN_VIEW: View federated rollout plans (default-allowed).
        FEDERATION_CONFLICT_VIEW: View federated rollout conflicts (default-allowed).
        FEDERATION_APPROVAL_LIST: List federation approvals (default-allowed).
        FEDERATION_APPROVAL_APPROVE: Approve a federation approval request.
        FEDERATION_APPROVAL_REJECT: Reject a federation approval request.
        FEDERATION_APPROVAL_ESCALATE: Escalate a federation approval request.
    """

    BUNDLE_CREATE = "policy.bundle.create"
    GATE_RUN = "policy.gate.run"
    PROMOTION_REQUEST = "policy.promotion.request"
    PROMOTION_APPROVE = "policy.promotion.approve"
    PROMOTION_REJECT = "policy.promotion.reject"
    PROMOTION_EXECUTE = "policy.promotion.execute"
    ROLLBACK_EXECUTE = "policy.rollback.execute"
    BYPASS_GATE = "policy.gate.bypass"
    ENVIRONMENT_DISABLE = "policy.environment.disable"
    ENVIRONMENT_ENABLE = "policy.environment.enable"
    ENVIRONMENT_VIEW = "policy.environment.view"
    RING_CREATE = "policy.ring.create"
    RING_ASSIGN = "policy.ring.assign"
    RING_PROMOTE = "policy.ring.promote"
    RING_DISABLE = "policy.ring.disable"
    RING_ENABLE = "policy.ring.enable"
    RING_VIEW = "policy.ring.view"
    RELOAD_REQUEST = "policy.reload.request"
    RELOAD_VIEW = "policy.reload.view"
    EVENT_VIEW = "policy.event.view"
    ROUTING_SIMULATE = "policy.routing.simulate"
    ROLLOUT_CREATE = "policy.rollout.create"
    ROLLOUT_START = "policy.rollout.start"
    ROLLOUT_EXECUTE = "policy.rollout.execute"
    ROLLOUT_CANCEL = "policy.rollout.cancel"
    ROLLOUT_VIEW = "policy.rollout.view"
    ROLLOUT_APPROVAL_REQUEST = "policy.rollout.approval.request"
    ROLLOUT_APPROVAL_APPROVE = "policy.rollout.approval.approve"
    ROLLOUT_APPROVAL_REJECT = "policy.rollout.approval.reject"
    ROLLOUT_APPROVAL_VIEW = "policy.rollout.approval.view"
    RUNTIME_POLICY_CREATE = "policy.runtime.create"
    RUNTIME_POLICY_VIEW = "policy.runtime.view"
    RUNTIME_POLICY_ENABLE = "policy.runtime.enable"
    RUNTIME_POLICY_DISABLE = "policy.runtime.disable"
    RUNTIME_POLICY_EVALUATE = "policy.runtime.evaluate"
    OBSERVABILITY_VIEW = "policy.observability.view"
    OBSERVABILITY_EXPORT = "policy.observability.export"
    SIMULATION_RUN = "policy.simulation.run"
    SIMULATION_VIEW = "policy.simulation.view"
    SIMULATION_EXPORT = "policy.simulation.export"
    SIMULATION_GATE_RUN = "policy.simulation.gate.run"
    SIMULATION_GATE_VIEW = "policy.simulation.gate.view"
    PROMOTION_GATE_REQUIRE = "policy.promotion.gate.require"
    PROMOTION_GATE_RUN = "policy.promotion.gate.run"
    PROMOTION_GATE_ATTACH = "policy.promotion.gate.attach"
    PROMOTION_GATE_VIEW = "policy.promotion.gate.view"
    ROLLOUT_GATE_ATTACH = "policy.rollout.gate.attach"
    ROLLOUT_GATE_VIEW = "policy.rollout.gate.view"
    ROLLOUT_GATE_RUN = "policy.rollout.gate.run"
    NOTIFICATION_VIEW = "policy.notification.view"
    NOTIFICATION_SEND = "policy.notification.send"
    NOTIFICATION_RULE_VIEW = "policy.notification.rule.view"
    NOTIFICATION_RULE_ENABLE = "policy.notification.rule.enable"
    NOTIFICATION_RULE_DISABLE = "policy.notification.rule.disable"
    EXPIRATION_SWEEP = "policy.expiration.sweep"
    EXPIRATION_VIEW = "policy.expiration.view"
    ROLLOUT_HISTORY_VIEW = "policy.rollout.history.view"
    ROLLOUT_ANALYTICS_VIEW = "policy.rollout.analytics.view"
    ROLLOUT_ANALYTICS_EXPORT = "policy.rollout.analytics.export"
    FEDERATION_TARGET_CREATE = "policy.federation.target.create"
    FEDERATION_TARGET_VIEW = "policy.federation.target.view"
    FEDERATION_TARGET_ENABLE = "policy.federation.target.enable"
    FEDERATION_TARGET_DISABLE = "policy.federation.target.disable"
    FEDERATION_PLAN_CREATE = "policy.federation.plan.create"
    FEDERATION_PLAN_START = "policy.federation.plan.start"
    FEDERATION_PLAN_EXECUTE = "policy.federation.plan.execute"
    FEDERATION_PLAN_CANCEL = "policy.federation.plan.cancel"
    FEDERATION_PLAN_VIEW = "policy.federation.plan.view"
    FEDERATION_CONFLICT_VIEW = "policy.federation.conflict.view"
    FEDERATION_HISTORY_VIEW = "policy.federation.history.view"
    FEDERATION_ANALYTICS_VIEW = "policy.federation.analytics.view"
    FEDERATION_ANALYTICS_EXPORT = "policy.federation.analytics.export"
    FEDERATION_APPROVAL_LIST = "policy.federation.approval.list"
    FEDERATION_APPROVAL_APPROVE = "policy.federation.approval.approve"
    FEDERATION_APPROVAL_REJECT = "policy.federation.approval.reject"
    FEDERATION_APPROVAL_ESCALATE = "policy.federation.approval.escalate"
    FEDERATION_NOTIFICATION_LIST = "policy.federation.notification.list"
    FEDERATION_NOTIFICATION_DISPATCH = "policy.federation.notification.dispatch"
    FEDERATION_ESCALATION_RUN = "policy.federation.escalation.run"
    FEDERATION_DLQ_LIST = "policy.federation.dlq.list"
    FEDERATION_DLQ_MANAGE = "policy.federation.dlq.manage"
    FEDERATION_WORKER_MANAGE = "policy.federation.worker.manage"
    FEDERATION_NOTIFICATION_TEMPLATE_LIST = "policy.federation.notification.template.list"
    FEDERATION_NOTIFICATION_TEMPLATE_MANAGE = "policy.federation.notification.template.manage"
    FEDERATION_NOTIFICATION_PREFERENCE_VIEW = "policy.federation.notification.preference.view"
    FEDERATION_NOTIFICATION_PREFERENCE_MANAGE = "policy.federation.notification.preference.manage"
    FEDERATION_WEBHOOK_REPLAY = "policy.federation.webhook.replay"
    FEDERATION_WEBHOOK_VERIFY = "policy.federation.webhook.verify"
    # Phase 59: multi-instance production readiness
    DLQ_REPLAY_IDEMPOTENCY_VIEW = "policy.federation.dlq.replay.idempotency.view"
    DLQ_REPLAY_IDEMPOTENCY_MANAGE = "policy.federation.dlq.replay.idempotency.manage"
    DLQ_REPLAY_RATE_LIMIT_VIEW = "policy.federation.dlq.replay.rate_limit.view"
    DLQ_REPLAY_RATE_LIMIT_MANAGE = "policy.federation.dlq.replay.rate_limit.manage"
    DLQ_REPLAY_RUN = "policy.federation.dlq.replay.run"
    PRIORITY_QUEUE_VIEW = "policy.federation.priority_queue.view"
    PRIORITY_QUEUE_MANAGE = "policy.federation.priority_queue.manage"
    DEAD_LETTER_POLICY_VIEW = "policy.federation.dead_letter.view"
    DEAD_LETTER_POLICY_MANAGE = "policy.federation.dead_letter.manage"
    DISTRIBUTED_LOCK_VIEW = "policy.federation.distributed_lock.view"
    DISTRIBUTED_LOCK_MANAGE = "policy.federation.distributed_lock.manage"
    WEBHOOK_KEY_ROTATION_VIEW = "policy.federation.webhook.key_rotation.view"
    WEBHOOK_KEY_ROTATION_MANAGE = "policy.federation.webhook.key_rotation.manage"
    METRICS_VIEW = "policy.federation.metrics.view"


_DEFAULT_ALLOWED: set[PolicyReleasePermission] = {
    PolicyReleasePermission.BUNDLE_CREATE,
    PolicyReleasePermission.GATE_RUN,
    PolicyReleasePermission.ENVIRONMENT_VIEW,
    PolicyReleasePermission.RING_VIEW,
    PolicyReleasePermission.RELOAD_VIEW,
    PolicyReleasePermission.EVENT_VIEW,
    PolicyReleasePermission.ROLLOUT_VIEW,
    PolicyReleasePermission.ROLLOUT_APPROVAL_VIEW,
    PolicyReleasePermission.RUNTIME_POLICY_VIEW,
    PolicyReleasePermission.RUNTIME_POLICY_EVALUATE,
    PolicyReleasePermission.OBSERVABILITY_VIEW,
    PolicyReleasePermission.SIMULATION_VIEW,
    PolicyReleasePermission.SIMULATION_GATE_VIEW,
    PolicyReleasePermission.PROMOTION_GATE_VIEW,
    PolicyReleasePermission.ROLLOUT_GATE_VIEW,
    PolicyReleasePermission.NOTIFICATION_VIEW,
    PolicyReleasePermission.EXPIRATION_VIEW,
    PolicyReleasePermission.ROLLOUT_HISTORY_VIEW,
    PolicyReleasePermission.ROLLOUT_ANALYTICS_VIEW,
    PolicyReleasePermission.FEDERATION_TARGET_VIEW,
    PolicyReleasePermission.FEDERATION_PLAN_VIEW,
    PolicyReleasePermission.FEDERATION_CONFLICT_VIEW,
    PolicyReleasePermission.FEDERATION_HISTORY_VIEW,
    PolicyReleasePermission.FEDERATION_ANALYTICS_VIEW,
    PolicyReleasePermission.FEDERATION_APPROVAL_LIST,
    PolicyReleasePermission.FEDERATION_NOTIFICATION_LIST,
    PolicyReleasePermission.FEDERATION_DLQ_LIST,
    PolicyReleasePermission.FEDERATION_NOTIFICATION_TEMPLATE_LIST,
    PolicyReleasePermission.FEDERATION_NOTIFICATION_PREFERENCE_VIEW,
    # Phase 59: default-allowed view permissions
    PolicyReleasePermission.DLQ_REPLAY_IDEMPOTENCY_VIEW,
    PolicyReleasePermission.DLQ_REPLAY_RATE_LIMIT_VIEW,
    PolicyReleasePermission.PRIORITY_QUEUE_VIEW,
    PolicyReleasePermission.DEAD_LETTER_POLICY_VIEW,
    PolicyReleasePermission.DISTRIBUTED_LOCK_VIEW,
    PolicyReleasePermission.WEBHOOK_KEY_ROTATION_VIEW,
    PolicyReleasePermission.METRICS_VIEW,
}


class PolicyReleasePermissionChecker:
    """RBAC checker for policy release permissions.

    Authorization rules:
    1. If the permission is in the default-allowed set, allow without
       requiring explicit permission in context.
    2. If the context's ``permissions`` list contains the required
       permission, allow.
    3. Otherwise deny.
    """

    async def check(
        self,
        required_permission: PolicyReleasePermission,
        context: RunContext,
    ) -> bool:
        """Check whether the context grants the required policy release permission.

        Args:
            required_permission: The policy release permission to check.
            context: Current run context (user, tenant, roles, permissions).

        Returns:
            True if authorized, False otherwise.
        """
        if required_permission in _DEFAULT_ALLOWED:
            return True
        return required_permission.value in context.permissions
