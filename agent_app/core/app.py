"""AgentApp — the primary user-facing entry point."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, AsyncIterator

from agent_app.core.agent_spec import AgentSpec
from agent_app.core.tool_spec import ToolSpec
from agent_app.core.workflow import Workflow, WorkflowType
from agent_app.governance.approval import ApprovalRequest, ApprovalStatus

if TYPE_CHECKING:
    from agent_app.registry.agent_registry import AgentRegistry
    from agent_app.registry.tool_registry import ToolRegistry
    from agent_app.registry.workflow_registry import WorkflowRegistry
    from agent_app.runtime.app_runner import AppRunner
    from agent_app.runtime.approval_store import ApprovalStore
    from agent_app.runtime.run_state import RunStateStore
    from agent_app.runtime.session import SessionStore
    from agent_app.runtime.streaming import StreamEvent


def _approval_status_value(status: Any, default: str = "") -> str:
    """Return an approval status as a plain string across store implementations."""
    value = getattr(status, "value", status)
    if value is None:
        return default
    return str(value)


class AgentApp:
    """Top-level application object that composes registries, config, and runner.

    Args:
        registry: Optional shared registry bundle. When omitted, a fresh
                  default set of registries is created.
        session_store: Optional session history store.
        approval_store: Optional approval persistence store.
        dag_state_store: Optional DAG workflow execution state store (Phase 14.0).
        lease_renewal_config: Optional lease renewal config (Phase 15.2).
        dag_lease_config: Optional DAG lease backend config (Phase 16.2).
    """

    def __init__(
        self,
        registry: Any = None,
        session_store: Any = None,
        approval_store: Any = None,
        backend: Any = None,
        run_state_store: Any = None,
        trace_collector: Any = None,
        dag_state_store: Any = None,
        lease_renewal_config: Any = None,
        dag_snapshot_config: Any = None,
        dag_compensation_config: Any = None,
        dag_lease_config: Any = None,
        dag_lease_backend: Any = None,
        audit_logger: Any = None,
        policy_engine: Any = None,
        policy_decision_store: Any = None,
        policy_resolver: Any = None,  # Phase 31
        ring_router: Any = None,  # Phase 34
    ) -> None:
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry

        if registry is not None:
            self.agent_registry = registry.agent_registry  # type: ignore[assignment]
            self.tool_registry = registry.tool_registry  # type: ignore[assignment]
            self.workflow_registry = registry.workflow_registry  # type: ignore[assignment]
        else:
            from agent_app.tools.decorator import get_default_registry

            self.agent_registry = AgentRegistry()
            self.tool_registry = get_default_registry()
            self.workflow_registry = WorkflowRegistry()

        self.session_store = session_store
        self.approval_store = approval_store
        self._backend = backend
        self._run_state_store = run_state_store
        self._dag_state_store = dag_state_store
        # Phase 15.2: Lease renewal config (best-effort background renewal)
        self._lease_renewal_config = lease_renewal_config
        # Phase 16.0: Snapshot config (DAG execution recovery points)
        self._dag_snapshot_config = dag_snapshot_config
        # Phase 16.1: Compensation persistence config
        self._dag_compensation_config = dag_compensation_config
        # Phase 16.2: DAG lease backend config
        self._dag_lease_config = dag_lease_config
        # Phase 16.5: DAG lease backend instance for recovery
        self._dag_lease_backend = dag_lease_backend
        # Phase 16.5: Audit logger for recovery events
        self._audit_logger = audit_logger
        # Phase 23: Policy engine for governance
        self.policy_engine = policy_engine
        # Phase 25: Policy decision store for persistence
        self.policy_decision_store = policy_decision_store
        # Phase 31: Policy resolver for runtime activation
        self._policy_resolver = policy_resolver
        # Phase 34: Ring router for ring-aware policy resolution
        self._ring_router = ring_router
        # Phase 35: Rollout store and service
        self._rollout_store: Any = None
        self._rollout_service: Any = None
        # Phase 36: Rollout approval store
        self._rollout_approval_store: Any = None
        # Phase 17: Recovery config for auto-recovery policy
        self._recovery_config: dict[str, Any] | None = None
        self._runner: AppRunner | None = None
        self._native_agents: dict[str, Any] = {}
        self.trace_collector = trace_collector

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def ring_router(self) -> Any:
        """Phase 34: Return the ring router, if configured."""
        return getattr(self, "_ring_router", None)

    @property
    def rollout_store(self) -> Any:
        """Phase 35: Return the rollout plan store, if configured."""
        return self._rollout_store

    @property
    def rollout_service(self) -> Any:
        """Phase 35: Return the rollout service, if configured."""
        return self._rollout_service

    @property
    def rollout_approval_store(self) -> Any:
        """Phase 36: Return the rollout approval store, if configured."""
        return self._rollout_approval_store

    @property
    def rollout_gate_automation_service(self) -> Any:
        """Phase 43: Return the rollout gate automation service, if configured."""
        return getattr(self, "_rollout_gate_automation_service", None)

    @rollout_gate_automation_service.setter
    def rollout_gate_automation_service(self, value: Any) -> None:
        """Phase 43: Set the rollout gate automation service."""
        self._rollout_gate_automation_service = value

    @property
    def notification_service(self) -> Any:
        """Phase 44: Return the notification service, if configured."""
        return getattr(self, "_notification_service", None)

    @notification_service.setter
    def notification_service(self, value: Any) -> None:
        """Phase 44: Set the notification service."""
        self._notification_service = value

    @property
    def expiration_service(self) -> Any:
        """Phase 44: Return the expiration service, if configured."""
        return getattr(self, "_expiration_service", None)

    @expiration_service.setter
    def expiration_service(self, value: Any) -> None:
        """Phase 44: Set the expiration service."""
        self._expiration_service = value

    @property
    def expiration_worker(self) -> Any:
        """Phase 44: Return the expiration worker, if configured."""
        return getattr(self, "_expiration_worker", None)

    @expiration_worker.setter
    def expiration_worker(self, value: Any) -> None:
        """Phase 44: Set the expiration worker."""
        self._expiration_worker = value

    @property
    def rollout_history_store(self) -> Any:
        """Phase 45: Return the rollout history store, if configured."""
        return getattr(self, "_rollout_history_store", None)

    @rollout_history_store.setter
    def rollout_history_store(self, value: Any) -> None:
        """Phase 45: Set the rollout history store."""
        self._rollout_history_store = value

    @property
    def rollout_history_recorder(self) -> Any:
        """Phase 45: Return the rollout history recorder, if configured."""
        return getattr(self, "_rollout_history_recorder", None)

    @rollout_history_recorder.setter
    def rollout_history_recorder(self, value: Any) -> None:
        """Phase 45: Set the rollout history recorder."""
        self._rollout_history_recorder = value

    @property
    def rollout_history_service(self) -> Any:
        """Phase 45: Return the rollout history service, if configured."""
        return getattr(self, "_rollout_history_service", None)

    @rollout_history_service.setter
    def rollout_history_service(self, value: Any) -> None:
        """Phase 45: Set the rollout history service."""
        self._rollout_history_service = value

    @property
    def federated_rollout_target_store(self) -> Any:
        """Phase 46: Return the federated rollout target store, if configured."""
        return getattr(self, "_federated_rollout_target_store", None)

    @federated_rollout_target_store.setter
    def federated_rollout_target_store(self, value: Any) -> None:
        self._federated_rollout_target_store = value

    @property
    def federated_rollout_plan_store(self) -> Any:
        """Phase 46: Return the federated rollout plan store, if configured."""
        return getattr(self, "_federated_rollout_plan_store", None)

    @federated_rollout_plan_store.setter
    def federated_rollout_plan_store(self, value: Any) -> None:
        self._federated_rollout_plan_store = value

    @property
    def rollout_federation_service(self) -> Any:
        """Phase 46: Return the rollout federation service, if configured."""
        return getattr(self, "_rollout_federation_service", None)

    @rollout_federation_service.setter
    def rollout_federation_service(self, value: Any) -> None:
        self._rollout_federation_service = value

    @property
    def federation_history_store(self) -> Any:
        """Phase 47: Return the federation history store, if configured."""
        return getattr(self, "_federation_history_store", None)

    @federation_history_store.setter
    def federation_history_store(self, value: Any) -> None:
        self._federation_history_store = value

    @property
    def federation_history_recorder(self) -> Any:
        """Phase 47: Return the federation history recorder, if configured."""
        return getattr(self, "_federation_history_recorder", None)

    @federation_history_recorder.setter
    def federation_history_recorder(self, value: Any) -> None:
        self._federation_history_recorder = value

    @property
    def federation_observability_service(self) -> Any:
        """Phase 47: Return the federation observability service, if configured."""
        return getattr(self, "_federation_observability_service", None)

    @federation_observability_service.setter
    def federation_observability_service(self, value: Any) -> None:
        self._federation_observability_service = value

    @property
    def federation_approval_store(self) -> Any:
        """Phase 48: Return the federation approval store, if configured."""
        return getattr(self, "_federation_approval_store", None)

    @federation_approval_store.setter
    def federation_approval_store(self, value: Any) -> None:
        self._federation_approval_store = value

    @property
    def federation_approval_policy(self) -> Any:
        """Phase 48: Return the federation approval policy, if configured."""
        return getattr(self, "_federation_approval_policy", None)

    @federation_approval_policy.setter
    def federation_approval_policy(self, value: Any) -> None:
        self._federation_approval_policy = value

    @property
    def federation_approval_service(self) -> Any:
        """Phase 48: Return the federation approval service, if configured."""
        return getattr(self, "_federation_approval_service", None)

    @federation_approval_service.setter
    def federation_approval_service(self, value: Any) -> None:
        self._federation_approval_service = value

    @property
    def federation_notification_store(self) -> Any:
        return getattr(self, "_federation_notification_store", None)

    @property
    def federation_notification_service(self) -> Any:
        return getattr(self, "_federation_notification_service", None)

    @property
    def federation_escalation_worker(self) -> Any:
        return getattr(self, "_federation_escalation_worker", None)

    @property
    def distributed_lock(self) -> Any:
        return getattr(self, "_distributed_lock", None)

    # ------------------------------------------------------------------
    # Registration helpers
    # ------------------------------------------------------------------

    def register_agent(self, spec: AgentSpec) -> None:
        """Register an AgentSpec."""
        self.agent_registry.register(spec.name, spec)

    def register_tool(self, spec: ToolSpec, fn: Any = None) -> None:
        """Register a ToolSpec with an optional callable."""
        self.tool_registry.register(spec.name, spec, fn=fn)

    def register_workflow(self, wf: Workflow) -> None:
        """Register a Workflow."""
        self.workflow_registry.register(wf.name, wf)

    # ------------------------------------------------------------------
    # Run / Stream
    # ------------------------------------------------------------------

    async def run(
        self,
        workflow: str | None = None,
        agent: str | None = None,
        input: str = "",
        user_id: str = "anonymous",
        tenant_id: str = "default",
        session_id: str | None = None,
        permissions: list[str] | None = None,
        worker: Any = None,
        idempotency_key: str | None = None,
        metadata: dict[str, object] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Execute a run against the given workflow or agent.

        Args:
            workflow: Workflow name to execute.
            agent: Agent name (shortcut for single-agent).
            input: User input.
            user_id: End-user ID.
            tenant_id: Tenant ID.
            session_id: Session / conversation ID.
            permissions: Granted permissions.
            worker: Optional worker identity for lease management (Phase 15).
            idempotency_key: Optional idempotency key for duplicate prevention (Phase 15.1).
            metadata: Optional metadata dict propagated through multi-agent runs (Phase 22).
            **kwargs: Extra forwarded to the backend.
        """
        self._ensure_runner()

        # -- Workflow dispatch (handoff / orchestrator) --
        if workflow is not None:
            try:
                wf = self.workflow_registry.get(workflow)
            except KeyError:
                from agent_app.core.result import AppRunResult
                return AppRunResult(
                    run_id=str(uuid.uuid4()),
                    status="failed",
                    error={"type": "KeyError", "message": f"Workflow '{workflow}' not found."},
                )
            if wf.type != WorkflowType.SINGLE:
                return await self._run_workflow(
                    wf, input, user_id, tenant_id, session_id, permissions, worker, idempotency_key, metadata
                )

        return await self._runner.run(
            workflow=workflow,
            agent=agent,
            input=input,
            user_id=user_id,
            tenant_id=tenant_id,
            session_id=session_id,
            app=self,
            permissions=permissions,
            worker=worker,
            idempotency_key=idempotency_key,
            metadata=metadata,
            **kwargs,
        )

    async def _run_workflow(
        self,
        workflow: Workflow,
        input: str,
        user_id: str,
        tenant_id: str,
        session_id: str | None,
        permissions: list[str] | None,
        worker: Any = None,
        idempotency_key: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Any:
        """Dispatch non-SINGLE workflows.

        Phase 11: If the backend supports ``run_workflow()`` (e.g.
        ``OpenAIAgentsBackend``), delegate to it. Otherwise use the
        framework's ``WorkflowExecutor`` with DryRun heuristics.

        Phase 15: Accepts optional worker identity for lease management.

        Phase 15.1: Accepts optional idempotency_key for duplicate prevention.

        Phase 22: Accepts optional metadata dict for multi-agent propagation.
        """
        from agent_app.core.context import RunContext

        merged_meta = dict(metadata) if metadata else {}
        context = RunContext(
            run_id=str(uuid.uuid4()),
            user_id=user_id,
            tenant_id=tenant_id,
            session_id=session_id,
            permissions=permissions or [],
            metadata=merged_meta,
        )

        # Phase 11: delegate to backend if it supports multi-agent workflows
        backend = getattr(self, "_backend", None)
        if (
            backend is not None
            and hasattr(backend, "run_workflow")
            and type(backend).__name__ != "DryRunBackend"
        ):
            return await backend.run_workflow(
                workflow=workflow,
                input=input,
                context=context,
            )

        # Fallback: framework WorkflowExecutor (DryRun path)
        return await self._runner._workflow_executor.run_workflow(
            workflow=workflow,
            input=input,
            context=context,
            app_runner=self._runner,
            permissions=permissions,
            worker=worker,
            idempotency_key=idempotency_key,
        )

    async def resume_workflow_run(
        self,
        workflow: str,
        run_id: str,
        input: str = "",
        permissions: list[str] | None = None,
        resume_policy: Any = None,
        worker: Any = None,
        idempotency_key: str | None = None,
    ) -> Any:
        """Resume a persisted DAG workflow run.

        Phase 14.1: Looks up the DAG workflow by name and delegates to
        ``AppRunner.resume_workflow_run()``.

        Phase 15: Accepts optional worker identity for lease management.

        Phase 15.1: Accepts optional idempotency_key for duplicate prevention.

        Args:
            workflow: Name of the DAG workflow to resume.
            run_id: The persisted workflow run ID.
            input: Original user input.
            permissions: Granted permissions.
            resume_policy: Optional ResumePolicy controlling retry/skip behavior.
            worker: Optional worker identity for lease management (Phase 15).

        Returns:
            AppRunResult with the resumed execution outcome.
        """
        self._ensure_runner()
        return await self._runner.resume_workflow_run(
            workflow=workflow,
            run_id=run_id,
            input=input,
            permissions=permissions,
            resume_policy=resume_policy,
            worker=worker,
            idempotency_key=idempotency_key,
        )

    # ------------------------------------------------------------------
    # Recovery scanning & manual recovery (Phase 16.5)
    # ------------------------------------------------------------------

    async def scan_recovery_candidates(
        self,
        config: Any = None,
    ) -> Any:
        """Scan persisted workflow runs for recovery candidates.

        Phase 16.5: Read-only scan.  Does not modify state or acquire leases.

        Args:
            config: Optional RecoveryScanConfig. Uses defaults if not provided.

        Returns:
            RecoveryScanResult with candidates.

        Raises:
            RuntimeError: If no dag_state_store is configured.
        """
        if self._dag_state_store is None:
            raise RuntimeError(
                "Recovery scanning requires a workflow state store. "
                "Configure workflow_state in your agentapp.yaml."
            )
        from agent_app.runtime.recovery_service import RecoveryScanner
        from agent_app.runtime.recovery_models import RecoveryScanConfig

        cfg = config or RecoveryScanConfig()
        lease_backend = getattr(self, "_dag_lease_backend", None)
        scanner = RecoveryScanner(self._dag_state_store, lease_backend)
        return await scanner.scan(cfg)

    async def inspect_recovery_candidate(self, run_id: str) -> Any:
        """Inspect a single workflow run as a recovery candidate.

        Args:
            run_id: The run to inspect.

        Returns:
            RecoveryCandidate for the run.

        Raises:
            RuntimeError: If no dag_state_store is configured.
            KeyError: If the run_id is not found.
        """
        if self._dag_state_store is None:
            raise RuntimeError(
                "Recovery inspection requires a workflow state store. "
                "Configure workflow_state in your agentapp.yaml."
            )
        from agent_app.runtime.recovery_service import RecoveryScanner

        lease_backend = getattr(self, "_dag_lease_backend", None)
        scanner = RecoveryScanner(self._dag_state_store, lease_backend)
        return await scanner.inspect_run(run_id)

    async def recover_workflow_run(
        self,
        workflow: str,
        run_id: str,
        recovered_by: str,
        resume_policy: Any = None,
    ) -> Any:
        """Manually recover a persisted workflow run.

        Acquires a lease before resuming and releases it afterwards.
        Recovery is only attempted if the run passes inspection.

        Args:
            workflow: Name of the workflow to resume.
            run_id: The run ID to recover.
            recovered_by: Identity of the operator performing recovery.
            resume_policy: Optional ResumePolicy controlling retry/skip behavior.

        Returns:
            ManualRecoveryResult with outcome details.

        Raises:
            RuntimeError: If no dag_state_store or dag_lease_backend is configured.
        """
        if self._dag_state_store is None:
            raise RuntimeError(
                "Manual recovery requires a workflow state store. "
                "Configure workflow_state in your agentapp.yaml."
            )
        if getattr(self, "_dag_lease_backend", None) is None:
            raise RuntimeError(
                "Manual recovery requires a lease backend. "
                "Configure dag_lease in your agentapp.yaml."
            )
        from agent_app.runtime.recovery_service import RecoveryService

        service = RecoveryService(
            app=self,
            state_store=self._dag_state_store,
            lease_backend=self._dag_lease_backend,
            audit_logger=getattr(self, "_audit_logger", None),
        )
        return await service.recover_run(
            workflow=workflow,
            run_id=run_id,
            recovered_by=recovered_by,
            resume_policy=resume_policy,
        )

    def create_recovery_daemon(
        self,
        policy: Any = None,
    ) -> Any:
        """Create an automatic recovery daemon.

        The daemon is **not** started automatically.  Call
        ``await daemon.run_once()`` or ``await daemon.run_forever()``
        explicitly.

        Args:
            policy: Optional AutoRecoveryPolicy.  If not provided, the
                policy from config (if any) is used, or the default
                conservative policy (disabled, dry-run).

        Returns:
            RecoveryDaemon instance.

        Raises:
            RuntimeError: If required dependencies are not configured.
        """
        if self._dag_state_store is None:
            raise RuntimeError(
                "Recovery daemon requires a workflow state store. "
                "Configure workflow_state in your agentapp.yaml."
            )
        if getattr(self, "_dag_lease_backend", None) is None:
            raise RuntimeError(
                "Recovery daemon requires a lease backend. "
                "Configure dag_lease in your agentapp.yaml."
            )
        from agent_app.runtime.recovery_daemon import RecoveryDaemon
        from agent_app.runtime.recovery_models import AutoRecoveryPolicy
        from agent_app.runtime.recovery_scanner import RecoveryScanner
        from agent_app.runtime.recovery_service import RecoveryService

        if policy is None:
            # Try to load from config
            cfg = getattr(self, "_dag_lease_config", None)
            recovery_cfg = getattr(self, "_recovery_config", None)
            if recovery_cfg and "auto" in recovery_cfg:
                policy = AutoRecoveryPolicy(**recovery_cfg["auto"])
            else:
                policy = AutoRecoveryPolicy()  # default: disabled, dry-run

        scanner = RecoveryScanner(
            self._dag_state_store,
            getattr(self, "_dag_lease_backend", None),
        )
        service = RecoveryService(
            app=self,
            state_store=self._dag_state_store,
            lease_backend=self._dag_lease_backend,
            audit_logger=getattr(self, "_audit_logger", None),
        )
        return RecoveryDaemon(
            scanner=scanner,
            recovery_service=service,
            policy=policy,
            audit_logger=getattr(self, "_audit_logger", None),
        )

    # ------------------------------------------------------------------
    # Recovery observability & admin APIs (Phase 18)
    # ------------------------------------------------------------------

    def get_recovery_system_status(self) -> Any:
        """Return a snapshot of the recovery subsystem's configuration.

        Phase 18: Read-only status for admin dashboards and CLI.

        Returns:
            RecoverySystemStatus describing availability and configuration.
        """
        from agent_app.runtime.recovery_models import (
            AutoRecoveryPolicy,
            RecoveryDaemonTickResult,
            RecoverySystemStatus,
        )

        has_store = self._dag_state_store is not None
        has_lease = getattr(self, "_dag_lease_backend", None) is not None

        # Load policy from config or use default
        recovery_cfg = getattr(self, "_recovery_config", None)
        policy: AutoRecoveryPolicy | None = None
        if recovery_cfg and "auto" in recovery_cfg:
            try:
                policy = AutoRecoveryPolicy(**recovery_cfg["auto"])
            except Exception:
                policy = AutoRecoveryPolicy()
        else:
            policy = AutoRecoveryPolicy()

        return RecoverySystemStatus(
            enabled=policy.enabled,
            dry_run=policy.dry_run,
            daemon_configured=has_store and has_lease,
            scanner_available=has_store,
            recovery_service_available=has_store and has_lease,
            policy=policy,
        )

    async def run_recovery_scan_once(
        self,
        policy: Any = None,
    ) -> Any:
        """Execute a single recovery scan cycle (dry-run by default).

        Phase 18: Public API for triggering scan-once without managing
        a RecoveryDaemon directly.  Always dry-run unless overridden
        via the policy argument.

        Args:
            policy: Optional AutoRecoveryPolicy.  If not provided,
                uses the config policy or a default dry-run policy.

        Returns:
            RecoveryDaemonTickResult with the scan outcome.

        Raises:
            RuntimeError: If required dependencies are not configured.
        """
        from agent_app.runtime.recovery_models import AutoRecoveryPolicy
        from agent_app.runtime.recovery_scanner import RecoveryScanner

        if self._dag_state_store is None:
            raise RuntimeError(
                "Recovery scan requires a workflow state store. "
                "Configure workflow_state in your agentapp.yaml."
            )

        # Build policy: default to dry-run
        if policy is None:
            recovery_cfg = getattr(self, "_recovery_config", None)
            if recovery_cfg and "auto" in recovery_cfg:
                try:
                    policy = AutoRecoveryPolicy(**recovery_cfg["auto"])
                except Exception:
                    policy = AutoRecoveryPolicy(dry_run=True)
            else:
                policy = AutoRecoveryPolicy(dry_run=True)
        else:
            # Ensure dry-run unless explicitly set
            if not hasattr(policy, "dry_run"):
                policy = AutoRecoveryPolicy(**policy.model_dump(), dry_run=True)

        scanner = RecoveryScanner(
            self._dag_state_store,
            getattr(self, "_dag_lease_backend", None),
        )
        from agent_app.runtime.recovery_models import RecoveryDaemonTickResult

        scan_config = self._build_scan_config_from_policy(policy)
        try:
            scan_result = await scanner.scan(scan_config)
        except Exception as exc:
            return RecoveryDaemonTickResult(
                scanned_count=0,
                dry_run=True,
                failures=[{"error": f"Scan failed: {exc}"}],
            )

        candidates = scan_result.candidates[: policy.max_candidates_per_scan]

        selected_run_ids: list[str] = []
        skipped: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []

        for candidate in candidates:
            skip_reason = self._should_skip_candidate(candidate, policy)
            if skip_reason is not None:
                skipped.append({
                    "run_id": candidate.run_id,
                    "reason": skip_reason,
                    "recommendation": candidate.recommendation.value,
                })
            else:
                selected_run_ids.append(candidate.run_id)

        return RecoveryDaemonTickResult(
            scanned_count=scan_result.total_scanned,
            selected_count=len(selected_run_ids),
            recovered_count=0,  # dry-run: no actual recovery
            skipped_count=len(skipped),
            failed_count=len(failures),
            dry_run=True,
            selected_run_ids=selected_run_ids,
            recovered_run_ids=[],
            skipped=skipped,
            failures=failures,
        )

    async def recover_run(
        self,
        run_id: str,
        workflow: str = "",
        dry_run: bool = True,
        recovered_by: str = "admin-api",
    ) -> Any:
        """Manually trigger recovery for a specific run.

        Phase 18: Thin wrapper around ``recover_workflow_run`` that
        defaults to dry-run.  Actual recovery requires ``dry_run=False``.

        Args:
            run_id: The run ID to recover.
            workflow: Name of the workflow the run belongs to.
            dry_run: If True (default), only inspect — do not recover.
            recovered_by: Identity of the operator / caller.

        Returns:
            ManualRecoveryResult with outcome details.

        Raises:
            RuntimeError: If required dependencies are not configured.
        """
        if dry_run:
            # Dry-run: just inspect the candidate
            try:
                candidate = await self.inspect_recovery_candidate(run_id)
            except KeyError:
                from agent_app.runtime.recovery_models import ManualRecoveryResult
                return ManualRecoveryResult(
                    run_id=run_id,
                    attempted=False,
                    error={"type": "not_found", "message": f"Run '{run_id}' not found."},
                )
            from agent_app.runtime.recovery_models import ManualRecoveryResult
            return ManualRecoveryResult(
                run_id=run_id,
                attempted=False,
                status="dry_run",
                error={
                    "type": "dry_run",
                    "message": "Dry-run: no recovery attempted.",
                    "candidate": candidate.model_dump(mode="json"),
                },
            )

        # Live recovery: delegate to recover_workflow_run
        return await self.recover_workflow_run(
            workflow=workflow,
            run_id=run_id,
            recovered_by=recovered_by,
        )

    async def get_recovery_history(
        self,
        run_id: str,
        limit: int = 50,
    ) -> list[Any]:
        """Query audit events related to a specific run.

        Phase 18: Provides recovery history for admin dashboards.

        Args:
            run_id: The run ID to query history for.
            limit: Maximum number of events to return.

        Returns:
            List of AuditEvent instances, sorted by timestamp.
            Returns empty list if no audit logger is configured.
        """
        audit_logger = getattr(self, "_audit_logger", None)
        if audit_logger is None:
            return []
        try:
            events = audit_logger.list_events(run_id=run_id)
            return events[-limit:]
        except Exception:
            return []

    def _build_scan_config_from_policy(self, policy: Any) -> Any:
        """Build a RecoveryScanConfig from an AutoRecoveryPolicy.

        Args:
            policy: The AutoRecoveryPolicy to convert.

        Returns:
            RecoveryScanConfig for use with RecoveryScanner.
        """
        from agent_app.runtime.recovery_models import RecoveryScanConfig

        include_failed = False
        include_running = False
        include_compensating = False
        include_completed = False

        _STATUS_TO_SCAN_FLAGS: dict[str, tuple[str, ...]] = {
            "failed": ("include_failed",),
            "running": ("include_running",),
            "pending": ("include_running",),
            "started": ("include_running",),
            "compensating": ("include_compensating",),
            "compensation_started": ("include_compensating",),
            "completed": ("include_completed",),
        }

        for status in policy.statuses:
            flags = _STATUS_TO_SCAN_FLAGS.get(status.lower(), ())
            for flag in flags:
                if flag == "include_failed":
                    include_failed = True
                elif flag == "include_running":
                    include_running = True
                elif flag == "include_compensating":
                    include_compensating = True
                elif flag == "include_completed":
                    include_completed = True

        if policy.include_completed:
            include_completed = True

        return RecoveryScanConfig(
            stale_after_seconds=int(policy.stale_after_seconds),
            include_failed=include_failed,
            include_running=include_running,
            include_compensating=include_compensating,
            include_completed=include_completed,
            limit=policy.max_candidates_per_scan,
            workflow_name=policy.workflow_name,
            tenant_id=policy.tenant_id,
        )

    @staticmethod
    def _should_skip_candidate(candidate: Any, policy: Any) -> str | None:
        """Determine if a candidate should be skipped.

        Args:
            candidate: The RecoveryCandidate to evaluate.
            policy: The AutoRecoveryPolicy to apply.

        Returns:
            Skip reason string, or None if the candidate should be selected.
        """
        from agent_app.runtime.recovery_models import RecoveryCandidateReason, RecoveryRecommendation

        if candidate.recommendation != RecoveryRecommendation.RESUME:
            return f"recommendation={candidate.recommendation.value}"

        is_failed = RecoveryCandidateReason.NODE_FAILED in candidate.reasons
        is_stale_running = (
            RecoveryCandidateReason.RUN_STALE in candidate.reasons
            or RecoveryCandidateReason.RUNNING_TOO_LONG in candidate.reasons
        )
        is_compensating = (
            RecoveryCandidateReason.COMPENSATION_INCOMPLETE in candidate.reasons
        )

        if is_failed and not policy.recover_failed:
            return "recover_failed disabled"
        if is_stale_running and not policy.recover_stale_running:
            return "recover_stale_running disabled"
        if is_compensating and not policy.recover_compensating:
            return "recover_compensating disabled"

        if candidate.recommendation == RecoveryRecommendation.WAIT_FOR_ACTIVE_LEASE:
            return "active lease"
        if candidate.recommendation == RecoveryRecommendation.DO_NOT_RESUME:
            return "not resumable"

        if RecoveryCandidateReason.LEASE_MISSING in candidate.reasons:
            if not (is_failed or is_stale_running or is_compensating):
                return "lease missing without recoverable condition"

        return None

    async def stream(
        self,
        workflow: str | None = None,
        agent: str | None = None,
        input: str = "",
        user_id: str = "anonymous",
        tenant_id: str = "default",
        session_id: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Stream events for a workflow or single agent run."""
        self._ensure_runner()
        async for event in self._runner.stream(
            workflow=workflow,
            agent=agent,
            input=input,
            user_id=user_id,
            tenant_id=tenant_id,
            session_id=session_id,
            app=self,
            **kwargs,
        ):
            yield event

    # ------------------------------------------------------------------
    # Approval lifecycle
    # ------------------------------------------------------------------

    async def approve(
        self,
        approval_id: str,
        approved_by: str,
        reason: str | None = None,
    ) -> ApprovalRequest:
        """Approve a pending approval request.

        Args:
            approval_id: The approval to approve.
            approved_by: Identity of the approver.
            reason: Optional reason.

        Returns:
            Updated ApprovalRequest with status APPROVED.

        Raises:
            KeyError: If approval_id not found.
            ValueError: If approval is not pending.
        """
        if self.approval_store is None:
            raise RuntimeError(
                "No approval_store configured on this AgentApp. "
                "Pass approval_store=... when creating the app."
            )
        req = await self.approval_store.approve(approval_id, approved_by, reason)
        # -- Phase 12: approval.approved --
        await self._record_trace_event(
            event_type="approval.approved",
            approval_id=approval_id,
            data={
                "approval_id": approval_id,
                "tool_name": getattr(req, "tool_name", None),
                "status": _approval_status_value(req.status, "approved"),
            },
        )
        return req

    async def reject(
        self,
        approval_id: str,
        rejected_by: str,
        reason: str | None = None,
    ) -> ApprovalRequest:
        """Reject a pending approval request.

        Args:
            approval_id: The approval to reject.
            rejected_by: Identity of the rejector.
            reason: Optional reason.

        Returns:
            Updated ApprovalRequest with status REJECTED.
        """
        if self.approval_store is None:
            raise RuntimeError(
                "No approval_store configured on this AgentApp. "
                "Pass approval_store=... when creating the app."
            )
        req = await self.approval_store.reject(approval_id, rejected_by, reason)
        # -- Phase 12: approval.rejected --
        await self._record_trace_event(
            event_type="approval.rejected",
            approval_id=approval_id,
            data={
                "approval_id": approval_id,
                "tool_name": getattr(req, "tool_name", None),
                "status": _approval_status_value(req.status, "rejected"),
            },
        )
        return req

    async def list_pending_approvals(
        self, tenant_id: str | None = None
    ) -> list[ApprovalRequest]:
        """List pending approval requests.

        Args:
            tenant_id: Optional tenant filter.

        Returns:
            List of pending ApprovalRequest instances.
        """
        if self.approval_store is None:
            return []
        return await self.approval_store.list_pending(tenant_id=tenant_id)

    async def approve_and_resume(
        self,
        approval_id: str,
        decided_by: str,
        decision_note: str | None = None,
        tenant_id: str | None = None,
    ) -> Any:
        """Approve a pending approval and resume its interrupted run."""
        if self.approval_store is None:
            raise RuntimeError(
                "No approval_store configured on this AgentApp. "
                "Pass approval_store=... when creating the app."
            )
        if self._run_state_store is None:
            from agent_app.core.result import AppRunResult
            return AppRunResult(
                run_id="",
                status="failed",
                error={
                    "type": "no_run_state_store",
                    "message": "Run state is missing or no longer resumable.",
                },
            )
        self._ensure_runner()
        from agent_app.runtime.approval_resume import ApprovalResumeService

        service = ApprovalResumeService(
            app=self,
            approval_store=self.approval_store,
            run_state_store=self._run_state_store,
            backend=self._runner.backend,
            agent_registry=self.agent_registry,
            audit_logger=getattr(self, "_audit_logger", None),
        )
        return await service.approve_and_resume(
            approval_id=approval_id,
            decided_by=decided_by,
            decision_note=decision_note,
            tenant_id=tenant_id,
        )

    async def reject_approval(
        self,
        approval_id: str,
        decided_by: str,
        reason: str | None = None,
        tenant_id: str | None = None,
    ) -> Any:
        """Reject a pending approval without resuming backend execution."""
        if self.approval_store is None:
            raise RuntimeError(
                "No approval_store configured on this AgentApp. "
                "Pass approval_store=... when creating the app."
            )
        if self._run_state_store is None:
            from agent_app.core.result import AppRunResult
            return AppRunResult(
                run_id="",
                status="failed",
                error={
                    "type": "no_run_state_store",
                    "message": "Run state is missing or no longer resumable.",
                },
            )
        self._ensure_runner()
        from agent_app.runtime.approval_resume import ApprovalResumeService

        service = ApprovalResumeService(
            app=self,
            approval_store=self.approval_store,
            run_state_store=self._run_state_store,
            backend=self._runner.backend,
            agent_registry=self.agent_registry,
            audit_logger=getattr(self, "_audit_logger", None),
        )
        return await service.reject(
            approval_id=approval_id,
            decided_by=decided_by,
            reason=reason,
            tenant_id=tenant_id,
        )

    async def resume(
        self,
        run_id: str,
        approval_id: str | None = None,
    ) -> Any:
        """Resume a run that was interrupted for approval.

        Phase 9: Framework-level resume backed by RunStateStore.
        Reads the InterruptedRun from the store, checks approval status,
        and returns an appropriate AppRunResult.

        For DryRunBackend: returns a completed stub result.
        For OpenAI backend: returns a completed stub with a note that
        native OpenAI RunState resume is not yet implemented.

        Args:
            run_id: Original run ID.
            approval_id: The approval that was resolved (optional).

        Returns:
            AppRunResult reflecting the resumed outcome.
        """
        from agent_app.core.result import AppRunResult

        # -- Phase 12: run_state.resumed --
        await self._record_trace_event(
            event_type="run_state.resumed",
            run_id=run_id,
            data={"approval_id": approval_id},
        )

        # -- Phase 9: Load from RunStateStore --
        if self._run_state_store is not None:
            try:
                interrupted = await self._run_state_store.get(run_id)
            except KeyError:
                await self._record_trace_event(
                    event_type="run.failed",
                    run_id=run_id,
                    status="failed",
                    error={"type": "run_not_found", "message": f"Run '{run_id}' not found."},
                )
                return AppRunResult(
                    run_id=run_id,
                    status="failed",
                    error={
                        "type": "run_not_found",
                        "message": f"Run '{run_id}' not found in run state store.",
                    },
                )

            # Check if all approvals are resolved
            pending_approvals = await self._check_pending_approvals(
                interrupted.approval_ids
            )

            if pending_approvals:
                # Still pending — return interrupted
                await self._record_trace_event(
                    event_type="run.interrupted",
                    run_id=run_id,
                    status="interrupted",
                )
                return AppRunResult(
                    run_id=run_id,
                    status="interrupted",
                    interruptions=interrupted.interruptions,
                    latency_ms=0,
                )

            # All approvals resolved — check for rejections
            has_rejection = await self._check_rejected_approvals(
                interrupted.approval_ids
            )
            if has_rejection:
                # Mark as completed with rejection message
                await self._run_state_store.mark_completed(run_id)
                rejected_by = await self._get_rejection_info(
                    interrupted.approval_ids
                )
                await self._record_trace_event(
                    event_type="run.completed",
                    run_id=run_id,
                    status="completed",
                    data={"reason": rejected_by.get("reason", "No reason provided.")},
                )
                return AppRunResult(
                    run_id=run_id,
                    status="completed",
                    final_output=(
                        f"Run '{run_id}' was rejected. "
                        f"Reason: {rejected_by.get('reason', 'No reason provided.')}"
                    ),
                    latency_ms=0,
                )

            # All approved — resume
            await self._run_state_store.mark_resumed(run_id)

            # -- Phase 10: Dispatch to backend resume for OpenAI --
            backend = getattr(self._runner, "backend", None) if self._runner else None
            if (
                backend is not None
                and hasattr(backend, "resume")
                and getattr(backend, "_hitl_mode", None) == "native"
                and interrupted.backend_state
            ):
                # Resolve agent_spec for resume
                try:
                    agent_spec = self.agent_registry.get(interrupted.agent_name)
                except KeyError:
                    agent_spec = None

                if agent_spec is not None:
                    # Build approval decisions from ApprovalStore
                    approvals = []
                    if self.approval_store is not None:
                        for apv_id in interrupted.approval_ids:
                            try:
                                req = await self.approval_store.get(apv_id)
                                approvals.append({
                                    "approval_id": apv_id,
                                    "status": _approval_status_value(req.status),
                                })
                            except KeyError:
                                pass

                    context = interrupted.context
                    resume_result = await backend.resume(
                        agent_spec=agent_spec,
                        context=context,
                        backend_state=interrupted.backend_state,
                        approvals=approvals,
                    )
                    await self._record_trace_event(
                        event_type="run.completed",
                        run_id=run_id,
                        status=resume_result.status,
                    )
                    return resume_result

            result = AppRunResult(
                run_id=run_id,
                status="completed",
                final_output=(
                    f"Run '{run_id}' approved and resumed. "
                    f"(Framework-level resume — native backend resume not implemented.)"
                ),
                latency_ms=0,
            )
            await self._record_trace_event(
                event_type="run.completed",
                run_id=run_id,
                status="completed",
            )
            return result

        # -- Fallback: legacy approval store path --
        if self.approval_store is None:
            return AppRunResult(
                run_id=run_id,
                status="failed",
                error={
                    "type": "no_run_state_store",
                    "message": "No run_state_store or approval_store configured.",
                },
            )

        try:
            req = await self.approval_store.get(approval_id)  # type: ignore[union-attr]
        except KeyError:
            return AppRunResult(
                run_id=run_id,
                status="failed",
                error={
                    "type": "approval_not_found",
                    "message": f"Approval '{approval_id}' not found.",
                },
            )

        if _approval_status_value(req.status) == "approved":
            return AppRunResult(
                run_id=run_id,
                status="completed",
                final_output=(
                    f"Tool '{req.tool_name}' was approved by "
                    f"{req.resolved_by}. Execution simulated (Phase 3 stub)."
                ),
            )
        if _approval_status_value(req.status) == "rejected":
            return AppRunResult(
                run_id=run_id,
                status="completed",
                final_output=(
                    f"Tool '{req.tool_name}' was rejected. "
                    f"Reason: {req.reason or 'No reason provided.'}"
                ),
            )
        return AppRunResult(
            run_id=run_id,
            status="interrupted",
            interruptions=[{
                "type": "approval_required",
                "approval_id": approval_id,
                "tool_name": req.tool_name,
                "arguments": req.arguments,
                "risk_level": req.risk_level,
            }],
        )

    async def _check_pending_approvals(self, approval_ids: list[str]) -> bool:
        """Check if any approval IDs are still pending."""
        if not approval_ids:
            return False
        if self.approval_store is None:
            return False
        for apv_id in approval_ids:
            try:
                req = await self.approval_store.get(apv_id)
                from agent_app.governance.approval import ApprovalStatus
                if _approval_status_value(req.status) == "pending":
                    return True
            except KeyError:
                continue
        return False

    async def _check_rejected_approvals(self, approval_ids: list[str]) -> bool:
        """Check if any approval IDs were rejected."""
        if not approval_ids:
            return False
        if self.approval_store is None:
            return False
        from agent_app.governance.approval import ApprovalStatus
        for apv_id in approval_ids:
            try:
                req = await self.approval_store.get(apv_id)
                if _approval_status_value(req.status) == "rejected":
                    return True
            except KeyError:
                continue
        return False

    async def _get_rejection_info(self, approval_ids: list[str]) -> dict[str, Any]:
        """Get info about the first rejected approval."""
        if self.approval_store is None:
            return {"reason": "Unknown"}
        from agent_app.governance.approval import ApprovalStatus
        for apv_id in approval_ids:
            try:
                req = await self.approval_store.get(apv_id)
                if _approval_status_value(req.status) == "rejected":
                    return {"reason": req.reason or "No reason provided."}
            except KeyError:
                continue
        return {"reason": "Unknown"}

    # ------------------------------------------------------------------
    # Escape hatch
    # ------------------------------------------------------------------

    async def _record_trace_event(
        self,
        event_type: str,
        run_id: str | None = None,
        approval_id: str | None = None,
        status: str | None = None,
        error: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Record a trace event if a trace_collector is configured."""
        collector = getattr(self, "trace_collector", None)
        if collector is None:
            return
        try:
            from agent_app.observability.events import RunEvent
            event = RunEvent(
                event_type=event_type,
                trace_id="",
                run_id=run_id,
                approval_id=approval_id,
                status=status,
                error=error,
                data=data or {},
            )
            await collector.record(event)
        except Exception:
            pass  # Never let observability break the main flow

    def get_native_agent(self, name: str) -> Any:
        """Return the underlying OpenAI Agents SDK Agent object (if compiled)."""
        if name not in self._native_agents:
            raise KeyError(
                f"Agent '{name}' has not been compiled yet. "
                "Call app.run() first or compile manually."
            )
        return self._native_agents[name]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_runner(self) -> None:
        if self._runner is None:
            from agent_app.runtime.app_runner import AppRunner
            self._runner = AppRunner(
                agent_registry=self.agent_registry,
                tool_registry=self.tool_registry,
                workflow_registry=self.workflow_registry,
                session_store=self.session_store,
                approval_store=self.approval_store,
                backend=self._backend,
                run_state_store=self._run_state_store,
                trace_collector=getattr(self, "trace_collector", None),
                dag_state_store=getattr(self, "_dag_state_store", None),
                lease_renewal_config=getattr(self, "_lease_renewal_config", None),
                dag_snapshot_config=getattr(self, "_dag_snapshot_config", None),
                dag_compensation_config=getattr(self, "_dag_compensation_config", None),
                dag_lease_config=getattr(self, "_dag_lease_config", None),
                policy_engine=getattr(self, "policy_engine", None),
                policy_decision_store=getattr(self, "policy_decision_store", None),
                policy_resolver=getattr(self, "_policy_resolver", None),
                ring_router=getattr(self, "_ring_router", None),
            )
