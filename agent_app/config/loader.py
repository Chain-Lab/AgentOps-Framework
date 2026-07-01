"""Config loader — loads agentapp.yaml into AppConfig and wires up AgentApp."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from agent_app.config.schema import AgentConfig, AppConfig, ToolConfig
from agent_app.core.agent_spec import AgentSpec
from agent_app.core.routing import RoutingMatchType, RoutingPolicy, RoutingRule
from agent_app.core.tool_spec import ToolSpec
from agent_app.core.workflow import Workflow
from agent_app.registry.agent_registry import AgentRegistry
from agent_app.registry.tool_registry import ToolRegistry
from agent_app.registry.workflow_registry import WorkflowRegistry

if TYPE_CHECKING:
    from agent_app.core.app import AgentApp


def _import_tools_module(base_dir: Path) -> None:
    """Try to import a ``tools`` module from *base_dir* so that any
    ``@tool``-decorated functions are registered in the global default registry.

    Silently ignored if no such module exists or it was already imported.
    """
    import importlib.util
    import sys

    candidate = base_dir / "tools.py"
    if not candidate.exists():
        return
    # Skip if already imported (e.g. the caller already imported it).
    module_name = candidate.stem
    if module_name in sys.modules:
        return
    # Ensure the directory is on sys.path so the import works reliably.
    dir_str = str(base_dir)
    if dir_str not in sys.path:
        sys.path.insert(0, dir_str)
    spec = importlib.util.spec_from_file_location(module_name, candidate)
    if spec is not None and spec.loader is not None:
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)


def _load_prompt(instructions: str, base_dir: Path) -> str:
    """Return instructions text, loading from file if it looks like a path."""
    candidate = (base_dir / instructions).resolve()
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    # Not a file path — treat as inline text.
    return instructions


def _parse_routing_policy(wf_body: dict[str, Any]) -> RoutingPolicy | None:
    """Parse a routing policy from a workflow YAML body.

    Returns ``None`` if no ``routing`` key is present.
    """
    routing_cfg = wf_body.get("routing")
    if not routing_cfg:
        return None
    rules: list[RoutingRule] = []
    for rule_cfg in routing_cfg.get("rules", []):
        match_type_str = rule_cfg.get("match_type", "keyword")
        try:
            match_type = RoutingMatchType(match_type_str)
        except ValueError:
            raise ValueError(
                f"Invalid match_type '{match_type_str}' in routing rule '{rule_cfg.get('name', '?')}'"
            )
        rules.append(RoutingRule(
            name=rule_cfg["name"],
            target=rule_cfg["target"],
            match_type=match_type,
            keywords=rule_cfg.get("keywords", []),
            pattern=rule_cfg.get("pattern"),
            priority=rule_cfg.get("priority", 100),
            reason=rule_cfg.get("reason"),
            metadata=rule_cfg.get("metadata", {}),
        ))
    return RoutingPolicy(name=f"{wf_body.get('name', 'default')}_policy", rules=rules)


def load_config(path: str | Path) -> AppConfig:
    """Load and validate an ``agentapp.yaml`` file.

    Args:
        path: Path to the YAML configuration file.

    Returns:
        A validated :class:`AppConfig` instance.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the YAML is malformed or fails Pydantic validation.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    try:
        return AppConfig(**raw)
    except Exception as exc:
        raise ValueError(f"Invalid config '{path}': {exc}") from exc


def build_app(
    config_path: str | Path,
    extra_tools: list[tuple[ToolSpec, Any]] | None = None,
) -> AgentApp:
    """Build an :class:`AgentApp` from a YAML config file.

    Reads agents, tools, and workflows from the config, resolves
    instruction file paths relative to the config file's directory,
    and registers everything.

    Tools registered via the ``@tool`` decorator (global default registry)
    are automatically included.

    Session configuration (``runtime.session`` in YAML) is applied when
    present; otherwise the app uses no session store.

    Args:
        config_path: Path to ``agentapp.yaml``.
        extra_tools: Additional (ToolSpec, callable) pairs to register
                     before processing the config's tool list.

    Returns:
        A fully configured :class:`AgentApp`.
    """
    from agent_app.core.app import AgentApp
    from agent_app.governance.audit import InMemoryAuditLogger, SQLiteAuditLogger
    from agent_app.runtime.approval_store import InMemoryApprovalStore, SQLiteApprovalStore
    from agent_app.governance.permission import DefaultPermissionChecker
    from agent_app.runtime.session import SessionStore
    from agent_app.runtime.session_manager import create_session_store
    from agent_app.tools.decorator import get_default_registry

    config_path = Path(config_path)
    base_dir = config_path.resolve().parent
    config = load_config(config_path)

    # Auto-import tools module from config directory so @tool decorators
    # register themselves in the global default registry.
    _import_tools_module(base_dir)

    agent_registry = AgentRegistry()
    tool_registry = ToolRegistry()
    workflow_registry = WorkflowRegistry()

    # -- Session store --
    session_store: SessionStore | None = None
    runtime_cfg = getattr(config, "runtime", None)
    if runtime_cfg and runtime_cfg.session_type != "memory":
        session_store = create_session_store(
            store_type=runtime_cfg.session_type,
            db_path=runtime_cfg.session_path,
        )

    # -- Run state store (Phase 9) --
    run_state_store: Any = None
    if runtime_cfg:
        from agent_app.runtime.run_state_store import create_run_state_store
        run_state_store = create_run_state_store(
            store_type=runtime_cfg.run_state_type,
            db_path=runtime_cfg.run_state_path,
        )

    # -- Workflow state store (Phase 14.0) --
    dag_state_store: Any = None
    if runtime_cfg and getattr(runtime_cfg, "workflow_state_type", None):
        from agent_app.runtime.dag_state_store import create_workflow_state_store
        dag_state_store = create_workflow_state_store(
            store_type=runtime_cfg.workflow_state_type,
            db_path=runtime_cfg.workflow_state_path,
        )

    # -- DAG lease backend (Phase 15/16) --
    dag_lease_backend: Any = None
    if runtime_cfg and getattr(runtime_cfg, "dag_lease_config", None):
        from agent_app.runtime.lease_backend import create_lease_backend
        lease_cfg = runtime_cfg.dag_lease_config
        dag_lease_backend = create_lease_backend(
            backend_type=lease_cfg.backend,
            state_store=dag_state_store,
            db_path=getattr(lease_cfg, "db_path", None),
            redis_url=getattr(lease_cfg, "redis_url", None),
            key_prefix=getattr(lease_cfg, "key_prefix", None),
            ttl_seconds=getattr(lease_cfg, "ttl_seconds", 300),
        )

    # -- Governance: approval store --
    gov = getattr(config, "governance", None)
    approval_cfg = gov.approvals if gov else None
    if approval_cfg and approval_cfg.type == "sqlite":
        approval_store: Any = SQLiteApprovalStore(db_path=approval_cfg.path or ".agent_app/approvals.db")
    else:
        approval_store = InMemoryApprovalStore()

    # -- Governance: audit logger --
    audit_cfg = gov.audit if gov else None
    if audit_cfg and audit_cfg.type == "sqlite":
        audit_logger: Any = SQLiteAuditLogger(db_path=audit_cfg.path or ".agent_app/audit.db")
    else:
        audit_logger = InMemoryAuditLogger()

    # -- Governance: permission checker --
    permission_checker: Any = DefaultPermissionChecker() if gov else None

    # -- Governance: rate limiter (Phase 21, backend selection Phase 65) --
    rate_limiter: Any = None
    if gov and getattr(gov, "rate_limit", None):
        from agent_app.runtime.approval_rate_limit import create_approval_rate_limiter
        rl_cfg = gov.rate_limit
        rate_limiter = create_approval_rate_limiter(
            backend=getattr(rl_cfg, "backend", "memory"),
            max_requests=getattr(rl_cfg, "max_requests", 10),
            window_seconds=getattr(rl_cfg, "window_seconds", 60),
            db_path=getattr(rl_cfg, "db_path", None),
            audit_logger=audit_logger,
        )

    # -- Governance: policy engine (Phase 23) --
    policy_engine: Any = None
    if gov and getattr(gov, "policies", None) and gov.policies.enabled:
        from agent_app.governance.policy import ConfigurablePolicyEngine
        rule_dicts = [r.model_dump() for r in gov.policies.rules]
        policy_engine = ConfigurablePolicyEngine(
            rules=rule_dicts,
            default_action=gov.policies.default_action,
        )

    # -- Phase 25: Policy decision store --
    policy_decision_store: Any = None
    if gov and getattr(gov, "policy_decisions", None):
        from agent_app.governance.policy_decision_store import (
            InMemoryPolicyDecisionStore,
            SQLitePolicyDecisionStore,
        )
        store_cfg = gov.policy_decisions
        if store_cfg.type == "sqlite":
            db_path = store_cfg.path or ".agent_app/policy_decisions.db"
            policy_decision_store = SQLitePolicyDecisionStore(db_path)
        else:
            policy_decision_store = InMemoryPolicyDecisionStore()

    # -- Phase 26: Policy console config --
    console_config: Any = None
    if gov and getattr(gov, "policy_console", None):
        console_config = gov.policy_console

    # -- Phase 29: Policy release config --
    release_config: Any = None
    if gov and getattr(gov, "policy_release", None):
        release_config = gov.policy_release

    # -- Merge tools from global default registry (registered via @tool) --
    default_tr = get_default_registry()
    for name in default_tr.list():
        entry = default_tr.get_entry(name)
        tool_registry.register(name, entry.spec, fn=entry.fn)

    # -- Extra tools --
    if extra_tools:
        for spec, fn in extra_tools:
            tool_registry.register(spec.name, spec, fn=fn)

    # -- Tools declared in config (may override or supplement) --
    for tool_cfg in config.tools:
        spec = ToolSpec(
            name=tool_cfg.name,
            description="",
            risk_level=tool_cfg.risk_level,
            requires_approval=tool_cfg.requires_approval,
            permissions=tool_cfg.permissions,
        )
        if not tool_registry.exists(spec.name):
            tool_registry.register(spec.name, spec)

    # -- Agents --
    for agent_cfg in config.agents:
        spec = AgentSpec(
            name=agent_cfg.name,
            description=agent_cfg.description,
            model=agent_cfg.model or config.models.get("default"),
            instructions=_load_prompt(agent_cfg.instructions, base_dir),
            tools=agent_cfg.tools,
            handoffs=agent_cfg.handoffs,
            guardrails=agent_cfg.guardrails,
        )
        agent_registry.register(spec.name, spec)

    # -- Workflows --
    wf_defs = config.workflows
    for wf_name, wf_body in wf_defs.items():
        wf_type = wf_body.get("type", "single")
        routing_policy = _parse_routing_policy(wf_body)
        if wf_type == "single":
            wf = Workflow.single(agent=wf_body.get("agent", wf_name), name=wf_name)
        elif wf_type == "handoff":
            wf = Workflow.handoff(
                entry=wf_body["entry"],
                agents=wf_body.get("agents", []),
                name=wf_name,
                max_handoffs=max(0, int(wf_body.get("max_handoffs", 3))),
            )
        elif wf_type == "orchestrator":
            wf = Workflow.orchestrator(
                manager=wf_body.get("entry", wf_name),
                agents_as_tools=wf_body.get("agents_as_tools", []),
                name=wf_name,
                max_agent_calls=max(0, int(wf_body.get("max_agent_calls", 5))),
            )
        elif wf_type == "dag":
            wf_retry_cfg = wf_body.get("retry")
            wf = Workflow.dag(
                name=wf_name,
                nodes=wf_body.get("nodes", []),
                execution_mode=wf_body.get("execution_mode", "sequential"),
                max_concurrency=wf_body.get("max_concurrency"),
                retry=wf_retry_cfg,
                timeout_seconds=wf_body.get("timeout_seconds"),
                deadline_seconds=wf_body.get("deadline_seconds"),
            )
        else:
            wf = Workflow(name=wf_name, type=wf_type, entry=wf_body.get("entry"))
        if routing_policy is not None:
            wf.routing_policy = routing_policy
        workflow_registry.register(wf.name, wf)

    # -- Backend --
    backend = _create_backend(
        runtime_cfg,
        agent_registry,
        tool_registry,
        approval_store=approval_store,
        audit_logger=audit_logger,
        permission_checker=permission_checker,
        rate_limiter=rate_limiter,
    )

    # -- Observability: trace collector (Phase 12, otel added Phase 65) --
    trace_collector: Any = None
    obs_cfg = getattr(config, "observability", None)
    if obs_cfg and obs_cfg.tracing and obs_cfg.tracing.type != "noop":
        tracing_type = obs_cfg.tracing.type
        if tracing_type == "memory":
            from agent_app.observability.collector import InMemoryTraceCollector
            trace_collector = InMemoryTraceCollector(
                max_traces=obs_cfg.tracing.max_traces,
                max_events_per_trace=obs_cfg.tracing.max_events_per_trace,
            )
        elif tracing_type == "jsonl":
            from agent_app.observability.exporters import JSONLTraceCollector
            path = obs_cfg.tracing.path or ".agent_app/traces.jsonl"
            trace_collector = JSONLTraceCollector(path=path)
        elif tracing_type == "otel":
            from agent_app.observability.otel import OtelTraceCollector, OpenTelemetryNotInstalledError
            try:
                trace_collector = OtelTraceCollector(
                    service_name=obs_cfg.tracing.otel_service_name,
                    exporter=obs_cfg.tracing.otel_exporter,
                    otlp_endpoint=obs_cfg.tracing.otel_otlp_endpoint,
                    max_traces=obs_cfg.tracing.max_traces,
                    max_events_per_trace=obs_cfg.tracing.max_events_per_trace,
                )
            except OpenTelemetryNotInstalledError as exc:
                raise RuntimeError(
                    f"tracing.type is 'otel' but OpenTelemetry is not installed: {exc}"
                ) from exc

    app = AgentApp(
        registry=_bundle(agent_registry, tool_registry, workflow_registry),
        session_store=session_store,
        approval_store=approval_store,
        backend=backend,
        run_state_store=run_state_store,
        dag_state_store=dag_state_store,
        trace_collector=trace_collector,
        lease_renewal_config=getattr(runtime_cfg, "lease_renewal_config", None),
        dag_snapshot_config=getattr(runtime_cfg, "dag_snapshot_config", None),
        dag_compensation_config=getattr(runtime_cfg, "dag_compensation_config", None),
        dag_lease_config=getattr(runtime_cfg, "dag_lease_config", None),
        dag_lease_backend=dag_lease_backend,
        audit_logger=audit_logger,
        policy_engine=policy_engine,
        policy_decision_store=policy_decision_store,
    )
    # Phase 17: Store recovery config for auto-recovery policy
    app._recovery_config = getattr(runtime_cfg, "recovery_config", None)
    # Phase 26: Store console config for FastAPI adapter
    app._console_config = console_config

    # -- Phase 29-31: Policy release service --
    release_service: Any = None
    if release_config:
        from agent_app.runtime.policy_release import PolicyReleaseService
        from agent_app.runtime.policy_gate_store import create_gate_store
        from agent_app.runtime.promotion_store import create_promotion_store
        from agent_app.governance.policy_bundle import create_bundle_store
        from agent_app.governance.policy_gate import PolicyGateEvaluator, PolicyGateRule
        from agent_app.governance.policy_replay import PolicyReplayRunner
        from agent_app.governance.policy_replay_context import PolicyReplayContextBuilder

        bundle_store = create_bundle_store(
            store_type=release_config.bundles.type,
            db_path=release_config.bundles.path,
        )
        gate_store = create_gate_store(
            store_type=release_config.gates.type,
            db_path=release_config.gates.path,
        )
        promotion_store = None
        if release_config.promotions:
            promotion_store = create_promotion_store(
                store_type=release_config.promotions.type,
                db_path=release_config.promotions.path,
            )

        # Build gate evaluator from rules
        rules = []
        for rule_cfg in getattr(release_config, "rules", []):
            rules.append(PolicyGateRule(
                name=rule_cfg.name,
                description=getattr(rule_cfg, "description", None),
                max_changed_decisions=getattr(rule_cfg, "max_changed_decisions", None),
                max_changed_ratio=getattr(rule_cfg, "max_changed_ratio", None),
                max_failed_replays=getattr(rule_cfg, "max_failed_replays", None),
                max_new_denies=getattr(rule_cfg, "max_new_denies", None),
                max_new_approvals=getattr(rule_cfg, "max_new_approvals", None),
                fail_on_missing_required_context=getattr(rule_cfg, "fail_on_missing_required_context", False),
            ))
        gate_evaluator = PolicyGateEvaluator(rules=rules)

        # Build replay runner from app components
        replay_runner = PolicyReplayRunner(
            decision_store=policy_decision_store,
            policy_engine=policy_engine,
            replay_store=None,
            context_builder=PolicyReplayContextBuilder(),
        )

        # Phase 31: activation store
        activation_store = None
        if getattr(release_config, "activations", None):
            act_cfg = release_config.activations
            if act_cfg.type == "sqlite":
                from agent_app.runtime.policy_activation_store import SQLitePolicyActivationStore
                activation_store = SQLitePolicyActivationStore(
                    db_path=act_cfg.path or ".agent_app/policy_activations.db"
                )
            else:
                from agent_app.runtime.policy_activation_store import InMemoryPolicyActivationStore
                activation_store = InMemoryPolicyActivationStore()

        # Phase 32: environment store
        environment_store = None
        if getattr(release_config, "environments", None):
            env_cfg = release_config.environments
            if env_cfg.type == "sqlite":
                from agent_app.runtime.policy_environment_store import SQLitePolicyEnvironmentStore
                environment_store = SQLitePolicyEnvironmentStore(
                    db_path=env_cfg.path or ".agent_app/policy_environments.db"
                )
            else:
                from agent_app.runtime.policy_environment_store import InMemoryPolicyEnvironmentStore
                environment_store = InMemoryPolicyEnvironmentStore()

        # Phase 33: ring store
        ring_store = None
        if getattr(release_config, "rings", None):
            ring_cfg = release_config.rings
            if ring_cfg.type == "sqlite":
                from agent_app.runtime.policy_ring_store import SQLiteReleaseRingStore
                ring_store = SQLiteReleaseRingStore(db_path=ring_cfg.path or ".agent_app/policy_rings.db")
            else:
                from agent_app.runtime.policy_ring_store import InMemoryReleaseRingStore
                ring_store = InMemoryReleaseRingStore()

        # Phase 33: ring assignment store
        ring_assignment_store = None
        if getattr(release_config, "ring_assignments", None):
            ra_cfg = release_config.ring_assignments
            if ra_cfg.type == "sqlite":
                from agent_app.runtime.policy_ring_assignment_store import SQLiteRingActivationAssignmentStore
                ring_assignment_store = SQLiteRingActivationAssignmentStore(db_path=ra_cfg.path or ".agent_app/policy_ring_assignments.db")
            else:
                from agent_app.runtime.policy_ring_assignment_store import InMemoryRingActivationAssignmentStore
                ring_assignment_store = InMemoryRingActivationAssignmentStore()

        # Phase 34: Ring routing config
        routing_config = None
        runtime_cfg = getattr(release_config, "runtime", None)
        if runtime_cfg is not None and hasattr(runtime_cfg, "routing") and runtime_cfg.routing is not None:
            from agent_app.runtime.policy_ring_router import RingRoutingConfig
            if isinstance(runtime_cfg.routing, dict):
                routing_config = RingRoutingConfig(**runtime_cfg.routing)
            elif isinstance(runtime_cfg.routing, RingRoutingConfig):
                routing_config = runtime_cfg.routing

        # Phase 33: ring router (extended Phase 34 with routing_config)
        ring_router = None
        if ring_store is not None:
            from agent_app.runtime.policy_ring_router import PolicyRingRouter
            default_ring = getattr(runtime_cfg, "ring", None) or "stable"
            ring_router = PolicyRingRouter(
                ring_store=ring_store,
                default_ring=default_ring,
                routing_config=routing_config,
            )

        # Phase 31: policy resolver
        policy_resolver = None
        if activation_store is not None:
            from agent_app.runtime.policy_resolver import ActivePolicyResolver
            cache_ttl = getattr(runtime_cfg, "cache_ttl_seconds", 5) if runtime_cfg else 5
            policy_resolver = ActivePolicyResolver(
                bundle_store=bundle_store,
                activation_store=activation_store,
                cache_ttl_seconds=cache_ttl,
            )

        # Update resolver with environment store
        if policy_resolver is not None and environment_store is not None:
            policy_resolver._environment_store = environment_store

        # Update resolver with ring stores
        if policy_resolver is not None:
            if ring_assignment_store is not None:
                policy_resolver._ring_assignment_store = ring_assignment_store
            if ring_store is not None:
                policy_resolver._ring_store = ring_store

        # Phase 34: Policy change event store
        event_store = None
        if release_config is not None and getattr(release_config, "change_events", None) is not None:
            from agent_app.runtime.policy_change_event_store import create_policy_change_event_store
            ce_cfg = release_config.change_events
            event_store = create_policy_change_event_store(
                store_type=ce_cfg.type,
                db_path=ce_cfg.path,
            )

        # Phase 34: Policy reload manager
        reload_manager = None
        if event_store is not None and policy_resolver is not None:
            from agent_app.runtime.policy_reload import PolicyReloadManager
            reload_manager = PolicyReloadManager(
                resolver=policy_resolver,
                event_store=event_store,
            )

        release_service = PolicyReleaseService(
            bundle_store=bundle_store,
            replay_runner=replay_runner,
            replay_store=None,
            gate_evaluator=gate_evaluator,
            gate_store=gate_store,
            promotion_store=promotion_store,
            allow_gate_bypass=getattr(release_config, "allow_gate_bypass", False),
            require_promotion_approval=getattr(release_config, "require_promotion_approval", True),
            activation_store=activation_store,
            policy_resolver=policy_resolver,
            environment_store=environment_store,
            ring_store=ring_store,
            ring_assignment_store=ring_assignment_store,
            ring_router=ring_router,
            event_store=event_store,
            reload_manager=reload_manager,
            strict=(release_config.change_events.strict if release_config.change_events else False),
        )
        app._release_service = release_service
        if environment_store is not None:
            app._environment_store = environment_store
        if ring_store is not None:
            app._ring_store = ring_store
        if ring_assignment_store is not None:
            app._ring_assignment_store = ring_assignment_store
        if ring_router is not None:
            app._ring_router = ring_router
        # Phase 34: Attach event_store and reload_manager for console/CLI access
        if event_store is not None:
            app._event_store = event_store
        if reload_manager is not None:
            app._reload_manager = reload_manager

        # Phase 35: Rollout store and service
        rollout_store = None
        rollout_service = None
        rollout_approval_store = None
        if release_config.rollouts is not None:
            from agent_app.runtime.policy_rollout_store import create_rollout_plan_store
            from agent_app.runtime.policy_rollout_service import RolloutService
            rollout_store = create_rollout_plan_store(
                store_type=release_config.rollouts.type,
                db_path=release_config.rollouts.path,
            )
            # Phase 36: Approval store for rollout step approvals
            approval_store = None
            approval_require_reason = False
            approval_policy = None
            if release_config.rollouts.approvals is not None:
                from agent_app.runtime.policy_rollout_approval_store import create_rollout_step_approval_store
                apv_cfg = release_config.rollouts.approvals
                approval_store = create_rollout_step_approval_store(
                    store_type=apv_cfg.type,
                    db_path=apv_cfg.path,
                )
                approval_require_reason = apv_cfg.require_reason
                rollout_approval_store = approval_store
                # Phase 37: Build approval policy from config
                if apv_cfg.policy is not None:
                    from agent_app.governance.policy_rollout_approval import (
                        RolloutApprovalPolicy,
                        RolloutApprovalPolicyType,
                    )
                    policy_cfg = apv_cfg.policy
                    # Map require_reason from parent config if not explicitly set in policy
                    require_reason = policy_cfg.require_reason
                    if not require_reason and apv_cfg.require_reason:
                        require_reason = True
                    approval_policy = RolloutApprovalPolicy(
                        policy_type=RolloutApprovalPolicyType(policy_cfg.policy_type),
                        required_approvals=policy_cfg.required_approvals,
                        allowed_approver_roles=policy_cfg.allowed_approver_roles,
                        allowed_approver_permissions=policy_cfg.allowed_approver_permissions,
                        prohibit_requester_approval=policy_cfg.prohibit_requester_approval,
                        prohibit_creator_approval=policy_cfg.prohibit_creator_approval,
                        expires_after_seconds=policy_cfg.expires_after_seconds,
                        require_reason=require_reason,
                    )
            rollout_service = RolloutService(
                rollout_store=rollout_store,
                release_service=release_service,
                audit_logger=audit_logger,
                event_store=event_store,
                permission_checker=permission_checker,
                approval_store=approval_store,
                approval_require_reason=approval_require_reason,
                approval_policy=approval_policy,
            )
        app._rollout_store = rollout_store
        app._rollout_service = rollout_service
        app._rollout_approval_store = rollout_approval_store

        # Phase 38: Runtime policy enforcement
        runtime_policy_store = None
        runtime_policy_evaluator = None
        policy_enforcement_service = None
        if hasattr(gov, 'runtime_policies') and gov.runtime_policies is not None:
            import uuid as _uuid
            from agent_app.runtime.runtime_policy_store import create_runtime_policy_store
            from agent_app.runtime.runtime_policy_evaluator import RuntimePolicyEvaluator
            from agent_app.runtime.policy_enforcement_service import PolicyEnforcementService

            rp_cfg = gov.runtime_policies
            runtime_policy_store = create_runtime_policy_store(
                store_type=rp_cfg.type,
                db_path=rp_cfg.path,
            )

            # Load inline rules from config
            from agent_app.governance.runtime_policy import (
                RuntimePolicyEffect,
                RuntimePolicyRule,
                RuntimePolicyRuleStatus,
            )
            from agent_app.governance.policy_enforcement import PolicyActionType
            from agent_app.governance.policy_rollout_approval import (
                RolloutApprovalPolicy,
                RolloutApprovalPolicyType,
            )
            import asyncio

            for rule_cfg in rp_cfg.rules:
                ap = None
                if rule_cfg.approval_policy:
                    ap_cfg = rule_cfg.approval_policy
                    ap = RolloutApprovalPolicy(
                        policy_type=RolloutApprovalPolicyType(ap_cfg.policy_type),
                        required_approvals=ap_cfg.required_approvals,
                        allowed_approver_roles=ap_cfg.allowed_approver_roles,
                        allowed_approver_permissions=ap_cfg.allowed_approver_permissions,
                        prohibit_requester_approval=ap_cfg.prohibit_requester_approval,
                        prohibit_creator_approval=ap_cfg.prohibit_creator_approval,
                        expires_after_seconds=ap_cfg.expires_after_seconds,
                        require_reason=ap_cfg.require_reason,
                    )

                rule = RuntimePolicyRule(
                    rule_id=f"rpr_{_uuid.uuid4().hex[:12]}",
                    name=rule_cfg.name,
                    action_type=PolicyActionType(rule_cfg.action_type),
                    effect=RuntimePolicyEffect(rule_cfg.effect),
                    status=RuntimePolicyRuleStatus.ENABLED,
                    tool_name=rule_cfg.tool_name,
                    risk_level=rule_cfg.risk_level,
                    required_permissions=rule_cfg.required_permissions,
                    required_roles=rule_cfg.required_roles,
                    approval_policy=ap,
                    reason=rule_cfg.reason,
                )
                try:
                    asyncio.get_event_loop().run_until_complete(runtime_policy_store.create(rule))
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    try:
                        loop.run_until_complete(runtime_policy_store.create(rule))
                    finally:
                        loop.close()

            runtime_policy_evaluator = RuntimePolicyEvaluator(policy_store=runtime_policy_store)
            policy_enforcement_service = PolicyEnforcementService(
                evaluator=runtime_policy_evaluator,
                audit_logger=audit_logger,
            )

        app._runtime_policy_store = runtime_policy_store
        app._runtime_policy_evaluator = runtime_policy_evaluator
        app._policy_enforcement_service = policy_enforcement_service

        # Phase 39: Policy observability
        policy_observability_service = None
        obs_cfg = getattr(gov, 'policy_observability', None)
        if obs_cfg is None or getattr(obs_cfg, 'enabled', True):
            from agent_app.runtime.policy_observability_service import PolicyObservabilityService
            policy_observability_service = PolicyObservabilityService(
                audit_logger=audit_logger,
                event_store=event_store,
                rollout_store=rollout_store,
                rollout_approval_store=rollout_approval_store,
                runtime_policy_store=runtime_policy_store,
            )
        app._policy_observability_service = policy_observability_service

        # Phase 40: Policy simulation service
        simulation_config = getattr(gov, 'policy_simulation', None)
        if simulation_config and simulation_config.enabled:
            from agent_app.runtime.policy_simulation_service import PolicySimulationService
            app.policy_simulation_service = PolicySimulationService(
                audit_logger=audit_logger,
                runtime_policy_store=runtime_policy_store,
            )

            # Phase 41: Simulation gate evaluator
            if simulation_config.gates:
                from agent_app.governance.policy_gate import PolicyGateRule
                from agent_app.runtime.policy_simulation_gate_evaluator import SimulationGateEvaluator

                gate_rules = [
                    PolicyGateRule(
                        name=gc.name,
                        description=gc.description,
                        max_changed_decisions=gc.max_changed_decisions,
                        max_changed_ratio=gc.max_changed_ratio,
                        max_failed_replays=gc.max_failed_replays,
                        max_new_denies=gc.max_new_denies,
                        max_new_approvals=gc.max_new_approvals,
                        fail_on_missing_required_context=gc.fail_on_missing_required_context,
                    )
                    for gc in simulation_config.gates
                ]
                app.simulation_gate_evaluator = SimulationGateEvaluator(rules=gate_rules)

        # Phase 42: Simulation gate enforcement
        release_gate_requirement_store = None
        release_gate_automation_service = None
        enforcement_config = getattr(release_config, 'simulation_gate_enforcement', None) if release_config else None
        if enforcement_config is not None:
            from agent_app.runtime.policy_release_gate_store import create_release_gate_requirement_store
            from agent_app.runtime.policy_release_gate_service import ReleaseGateAutomationService

            # Create requirement store
            if enforcement_config.requirement_store is not None:
                req_store_cfg = enforcement_config.requirement_store
                release_gate_requirement_store = create_release_gate_requirement_store(
                    store_type=req_store_cfg.type,
                    path=req_store_cfg.path,
                )
            else:
                release_gate_requirement_store = create_release_gate_requirement_store()

            # Create automation service
            simulation_service = getattr(app, 'policy_simulation_service', None)
            simulation_gate_evaluator = getattr(app, 'simulation_gate_evaluator', None)
            release_gate_automation_service = ReleaseGateAutomationService(
                requirement_store=release_gate_requirement_store,
                gate_store=gate_store if release_config else None,
                simulation_service=simulation_service,
                simulation_gate_evaluator=simulation_gate_evaluator,
                audit_logger=audit_logger,
                event_store=event_store,
            )

            # When enforcement is enabled, pass to PolicyReleaseService and RolloutService
            if enforcement_config.require_for_promotion and release_service is not None:
                release_service._release_gate_automation_service = release_gate_automation_service
                release_service._require_simulation_gate_for_promotion = True
                release_service._simulation_gate_max_age_seconds = enforcement_config.max_age_seconds
                if rollout_service is not None:
                    rollout_service._release_gate_automation_service = release_gate_automation_service

        app._release_gate_requirement_store = release_gate_requirement_store
        app._release_gate_automation_service = release_gate_automation_service

        # Phase 43: Rollout gate automation
        rollout_gate_config = getattr(release_config, 'rollout_gate_automation', None) if release_config else None
        if rollout_gate_config is not None and rollout_gate_config.enabled:
            from agent_app.runtime.policy_rollout_gate_service import RolloutGateAutomationService
            from agent_app.governance.policy_gate import PolicyGateRule

            # Convert config gate rules to PolicyGateRule objects
            default_gate_rules = []
            for rule_cfg in rollout_gate_config.default_gate_rules:
                rule_kwargs = {"name": rule_cfg.name}
                if "changed_ratio" in rule_cfg.metric:
                    rule_kwargs["max_changed_ratio"] = float(rule_cfg.threshold)
                elif "errors" in rule_cfg.metric:
                    rule_kwargs["max_failed_replays"] = int(rule_cfg.threshold)
                elif "deny" in rule_cfg.metric:
                    rule_kwargs["max_new_denies"] = int(rule_cfg.threshold)
                elif "approval" in rule_cfg.metric or "require_approval" in rule_cfg.metric:
                    rule_kwargs["max_new_approvals"] = int(rule_cfg.threshold)
                else:
                    rule_kwargs["max_changed_ratio"] = float(rule_cfg.threshold)
                default_gate_rules.append(PolicyGateRule(**rule_kwargs))

            rollout_gate_automation_service = RolloutGateAutomationService(
                release_gate_automation_service=release_gate_automation_service,
                simulation_service=getattr(app, 'policy_simulation_service', None),
                simulation_gate_evaluator=getattr(app, 'simulation_gate_evaluator', None),
                audit_logger=audit_logger,
                event_store=event_store,
                default_gate_rules=default_gate_rules,
                default_max_age_seconds=rollout_gate_config.default_max_age_seconds,
            )

            # Set on RolloutService if available
            if rollout_service is not None:
                rollout_service._rollout_gate_automation_service = rollout_gate_automation_service

            app._rollout_gate_automation_service = rollout_gate_automation_service

        # Phase 44: Notification and expiration wiring
        try:
            notification_config = getattr(release_config, 'notifications', None) if release_config else None
            if notification_config is not None and notification_config.enabled:
                from agent_app.runtime.policy_notification_store import (
                    InMemoryPolicyNotificationStore,
                    SQLitePolicyNotificationStore,
                )
                from agent_app.runtime.policy_notification_rule_store import (
                    InMemoryPolicyNotificationRuleStore,
                    SQLitePolicyNotificationRuleStore,
                )
                from agent_app.runtime.policy_notification_service import PolicyNotificationService
                from agent_app.runtime.policy_notification_channel import LogNotificationChannel

                # Create notification store
                if notification_config.store is not None and notification_config.store.type == "sqlite":
                    notification_store = SQLitePolicyNotificationStore(
                        db_path=notification_config.store.path or ".agent_app/policy_notifications.db"
                    )
                else:
                    notification_store = InMemoryPolicyNotificationStore()

                # Create rule store
                if notification_config.store is not None and notification_config.store.type == "sqlite":
                    rule_store = SQLitePolicyNotificationRuleStore(
                        db_path=notification_config.store.path or ".agent_app/policy_notification_rules.db"
                    )
                else:
                    rule_store = InMemoryPolicyNotificationRuleStore()

                # Create channels
                channels = {"log": LogNotificationChannel()}

                # Create notification service
                notification_service = PolicyNotificationService(
                    store=notification_store,
                    rule_store=rule_store,
                    channels=channels,
                    audit_logger=audit_logger,
                )

                # Load inline rules from config into rule store
                import asyncio as _asyncio
                for rule_cfg in notification_config.rules:
                    from agent_app.governance.policy_notification import PolicyNotificationRule
                    rule = PolicyNotificationRule(
                        name=rule_cfg.name,
                        event_types=rule_cfg.event_types,
                        severity=rule_cfg.severity,
                        channels=rule_cfg.channels,
                        title_template=rule_cfg.title_template,
                        body_template=rule_cfg.body_template,
                    )
                    try:
                        loop = _asyncio.get_event_loop()
                        if loop.is_running():
                            import concurrent.futures
                            with concurrent.futures.ThreadPoolExecutor() as pool:
                                pool.submit(_asyncio.run, rule_store.create(rule)).result()
                        else:
                            loop.run_until_complete(rule_store.create(rule))
                    except RuntimeError:
                        _asyncio.run(rule_store.create(rule))

                app.notification_service = notification_service

            expiration_config = getattr(release_config, 'expiration', None) if release_config else None
            if expiration_config is not None and expiration_config.enabled:
                from agent_app.runtime.policy_expiration_service import PolicyExpirationService
                from agent_app.runtime.policy_expiration_worker import PolicyExpirationWorker

                # Gather required stores from the app
                _rollout_approval_store = getattr(app, "rollout_approval_store", None)
                _release_gate_req_store = getattr(app, "_release_gate_requirement_store", None)
                _notification_service = getattr(app, "notification_service", None)
                _event_store = getattr(app, "_event_store", None)

                expiration_service = PolicyExpirationService(
                    rollout_approval_store=_rollout_approval_store,
                    release_gate_requirement_store=_release_gate_req_store,
                    notification_service=_notification_service,
                    audit_logger=audit_logger,
                    event_store=_event_store,
                )
                app.expiration_service = expiration_service

                # Optionally create expiration worker
                expiration_worker = PolicyExpirationWorker(
                    expiration_service=expiration_service,
                    interval_seconds=expiration_config.sweep_interval_seconds,
                )
                app.expiration_worker = expiration_worker
        except Exception:
            pass  # Phase 44 imports are optional — don't crash the loader

        # -- Phase 45: Rollout history --
        try:
            if release_config and getattr(release_config, "rollout_history", None) and release_config.rollout_history.enabled:
                from agent_app.runtime.policy_rollout_history_store import create_rollout_history_store
                from agent_app.runtime.policy_rollout_history_recorder import RolloutHistoryRecorder
                from agent_app.runtime.policy_rollout_history_service import RolloutHistoryService

                rh_cfg = release_config.rollout_history
                rh_store_cfg = rh_cfg.store
                rh_store = create_rollout_history_store(
                    store_type=rh_store_cfg.type if rh_store_cfg else "memory",
                    db_path=rh_store_cfg.path if rh_store_cfg else None,
                )
                rh_recorder = RolloutHistoryRecorder(
                    history_store=rh_store,
                    audit_logger=audit_logger,
                )
                rh_service = RolloutHistoryService(
                    history_store=rh_store,
                    rollout_store=app.rollout_store,
                    rollout_approval_store=app.rollout_approval_store,
                    release_gate_requirement_store=getattr(app, "_release_gate_requirement_store", None),
                    notification_store=app.notification_service._store if app.notification_service else None,
                    audit_logger=audit_logger,
                )
                app.rollout_history_store = rh_store
                app.rollout_history_recorder = rh_recorder
                app.rollout_history_service = rh_service

                # Inject recorder into existing services
                if app.rollout_service:
                    app.rollout_service._history_recorder = rh_recorder
                if app.rollout_gate_automation_service:
                    app.rollout_gate_automation_service._history_recorder = rh_recorder
                if app.expiration_service:
                    app.expiration_service._history_recorder = rh_recorder
                if app.notification_service:
                    app.notification_service._history_recorder = rh_recorder
        except Exception:
            pass  # Phase 45 wiring failure should not break existing behavior

        # -- Phase 46: Rollout federation --
        try:
            fed_cfg = getattr(release_config, "rollout_federation", None) if release_config else None
            if fed_cfg is not None and fed_cfg.enabled:
                from agent_app.runtime.policy_rollout_conflict_detector import RolloutConflictDetector
                from agent_app.runtime.policy_rollout_federation_service import RolloutFederationService
                from agent_app.runtime.policy_rollout_federation_store import (
                    create_federated_rollout_plan_store,
                    create_federated_rollout_target_store,
                )

                target_store_cfg = fed_cfg.target_store
                plan_store_cfg = fed_cfg.plan_store
                federated_target_store = create_federated_rollout_target_store(
                    store_type=target_store_cfg.type if target_store_cfg else "memory",
                    db_path=target_store_cfg.path if target_store_cfg else None,
                )
                federated_plan_store = create_federated_rollout_plan_store(
                    store_type=plan_store_cfg.type if plan_store_cfg else "memory",
                    db_path=plan_store_cfg.path if plan_store_cfg else None,
                )
                conflict_detector = RolloutConflictDetector(
                    target_store=federated_target_store,
                    federation_store=federated_plan_store,
                    rollout_store=app.rollout_store,
                )
                conflict_policy = getattr(fed_cfg, "conflict_policy", None)
                federation_service = RolloutFederationService(
                    target_store=federated_target_store,
                    federation_store=federated_plan_store,
                    rollout_store=app.rollout_store,
                    rollout_service=app.rollout_service,
                    conflict_detector=conflict_detector,
                    history_recorder=getattr(app, "rollout_history_recorder", None),
                    notification_service=app.notification_service,
                    audit_logger=audit_logger,
                    event_store=event_store,
                    fail_on_error_conflicts=getattr(conflict_policy, "fail_on_error", True),
                    warn_on_bundle_conflict=getattr(conflict_policy, "warn_on_bundle_conflict", True),
                )
                app.federated_rollout_target_store = federated_target_store
                app.federated_rollout_plan_store = federated_plan_store
                app.rollout_federation_service = federation_service
        except Exception:
            pass  # Phase 46 wiring failure should not break existing behavior

        # -- Phase 47: Rollout Federation History --
        try:
            fed_hist_cfg = getattr(release_cfg, "rollout_federation_history", None) if release_config else None
            if fed_hist_cfg and fed_hist_cfg.enabled:
                from agent_app.runtime.policy_rollout_federation_history_store import create_federation_history_store
                from agent_app.runtime.policy_rollout_federation_history_recorder import FederationHistoryRecorder
                from agent_app.runtime.policy_rollout_federation_observability_service import FederationObservabilityService

                fed_hist_store = create_federation_history_store(
                    type=fed_hist_cfg.store.type if fed_hist_cfg.store else "memory",
                    path=fed_hist_cfg.store.path if fed_hist_cfg.store else None,
                )
                fed_hist_recorder = FederationHistoryRecorder(
                    history_store=fed_hist_store,
                    audit_logger=audit_logger,
                )
                fed_obs_service = FederationObservabilityService(
                    history_store=fed_hist_store,
                    federation_plan_store=app.federated_rollout_plan_store,
                    federation_target_store=app.federated_rollout_target_store,
                    audit_logger=audit_logger,
                )
                app.federation_history_store = fed_hist_store
                app.federation_history_recorder = fed_hist_recorder
                app.federation_observability_service = fed_obs_service

                # Inject recorder into federation service
                if app.rollout_federation_service is not None:
                    app.rollout_federation_service._federation_recorder = fed_hist_recorder
                # Inject recorder into notification service
                if app.notification_service is not None:
                    app.notification_service._federation_recorder = fed_hist_recorder
        except Exception:
            pass  # Phase 47 wiring failure should not break existing behavior

        # -- Phase 48: Federation Approval --
        try:
            _fed_approval_cfg = getattr(fed_cfg, 'approvals', None) if fed_cfg else None
            if _fed_approval_cfg and _fed_approval_cfg.enabled:
                from agent_app.runtime.policy_rollout_federation_approval_store import create_federation_approval_store
                from agent_app.runtime.policy_rollout_federation_approval_service import FederationApprovalService
                from agent_app.governance.policy_rollout_federation_approval import FederationApprovalPolicy

                federation_approval_store = create_federation_approval_store(
                    type=_fed_approval_cfg.type,
                    path=_fed_approval_cfg.path,
                )
                federation_approval_policy = FederationApprovalPolicy(
                    enabled=_fed_approval_cfg.enabled,
                    require_approval_for=_fed_approval_cfg.require_approval_for,
                    default_required_approvers=_fed_approval_cfg.default_required_approvers,
                    delegation_enabled=_fed_approval_cfg.delegation_enabled,
                    escalation_enabled=_fed_approval_cfg.escalation_enabled,
                    escalation_after_minutes=_fed_approval_cfg.escalation_after_minutes,
                    escalate_to=_fed_approval_cfg.escalate_to,
                )
                federation_approval_service = FederationApprovalService(
                    approval_store=federation_approval_store,
                    approval_policy=federation_approval_policy,
                    audit_logger=audit_logger,
                    change_event_store=event_store,
                    federation_history_recorder=getattr(app, 'federation_history_recorder', None),
                )
                app.federation_approval_store = federation_approval_store
                app.federation_approval_policy = federation_approval_policy
                app.federation_approval_service = federation_approval_service
        except Exception:
            pass  # Phase 48 wiring failure should not break existing behavior

        # -- Phase 49: Federation Notification & Escalation Worker --
        try:
            if fed_cfg is not None:

                # Notification store and service
                if hasattr(fed_cfg, "notifications") and fed_cfg.notifications is not None and fed_cfg.notifications.enabled:
                    from agent_app.runtime.policy_rollout_federation_notification_store import create_federation_notification_store
                    from agent_app.runtime.policy_rollout_federation_notification_service import FederationNotificationService
                    from agent_app.runtime.policy_rollout_federation_notification_adapters import (
                        NoopFederationNotificationAdapter,
                        ConsoleFederationNotificationAdapter,
                    )
                    from agent_app.governance.policy_rollout_federation_notification import (
                        FederationNotificationChannel,
                        FederationNotificationPolicy,
                    )

                    fed_notif_store = create_federation_notification_store(
                        store_type=fed_cfg.notifications.type,
                        db_path=fed_cfg.notifications.path,
                    )

                    # Build adapters
                    adapters: dict[FederationNotificationChannel, Any] = {}
                    for ch in fed_cfg.notifications.default_channels:
                        channel = FederationNotificationChannel(ch)
                        if channel == FederationNotificationChannel.CONSOLE:
                            adapters[channel] = ConsoleFederationNotificationAdapter()
                        elif channel == FederationNotificationChannel.NOOP:
                            adapters[channel] = NoopFederationNotificationAdapter()
                        elif channel == FederationNotificationChannel.WEBHOOK:
                            from agent_app.runtime.policy_rollout_federation_notification_adapters import WebhookFederationNotificationAdapter
                            webhook_url = fed_cfg.notifications.channels.get("webhook", {}).get("url", "")
                            timeout = fed_cfg.notifications.channels.get("webhook", {}).get("timeout_seconds", 5)
                            adapters[channel] = WebhookFederationNotificationAdapter(url=webhook_url, timeout_seconds=timeout)

                    fed_notif_policy = FederationNotificationPolicy(
                        enabled=True,
                        default_channels=[FederationNotificationChannel(ch) for ch in fed_cfg.notifications.default_channels],
                        max_attempts=fed_cfg.notifications.retry_max_attempts,
                        backoff_seconds=fed_cfg.notifications.retry_backoff_seconds,
                    )

                    fed_notif_service = FederationNotificationService(
                        notification_store=fed_notif_store,
                        adapters=adapters,
                        notification_policy=fed_notif_policy,
                    )

                    app._federation_notification_store = fed_notif_store
                    app._federation_notification_service = fed_notif_service

                # Distributed lock and escalation worker
                if hasattr(fed_cfg, "worker") and fed_cfg.worker is not None and fed_cfg.worker.enabled:
                    from agent_app.runtime.distributed_lock import create_distributed_lock
                    from agent_app.runtime.policy_rollout_federation_escalation_worker import FederationApprovalEscalationWorker

                    dlock = create_distributed_lock(
                        store_type=fed_cfg.worker.lock_type,
                        db_path=fed_cfg.worker.lock_path,
                    )
                    app._distributed_lock = dlock

                    # Build worker if approval service and store are available
                    fed_approval_store = getattr(app, "_federation_approval_store", None)
                    fed_approval_service = getattr(app, "_federation_approval_service", None)
                    if fed_approval_store is not None and fed_approval_service is not None:
                        escalation_minutes = 60
                        if hasattr(fed_cfg, "approvals") and fed_cfg.approvals is not None:
                            escalation_minutes = fed_cfg.approvals.escalation_after_minutes

                        fed_notif_service = getattr(app, "_federation_notification_service", None)
                        worker = FederationApprovalEscalationWorker(
                            approval_store=fed_approval_store,
                            approval_service=fed_approval_service,
                            notification_service=fed_notif_service,
                            distributed_lock=dlock,
                            escalation_after_minutes=escalation_minutes,
                        )
                        app._federation_escalation_worker = worker
        except Exception:
            pass  # Phase 49 wiring failure should not break existing behavior

        # Phase 50: DLQ, retry policy, scheduled worker
        try:
            if fed_cfg is not None:

                # DLQ store
                if hasattr(fed_cfg, "notifications") and fed_cfg.notifications is not None:
                    dlq_cfg = getattr(fed_cfg.notifications, "dlq", None)
                    if dlq_cfg is not None and dlq_cfg.enabled:
                        from agent_app.runtime.policy_rollout_federation_notification_dlq_store import create_federation_notification_dlq_store
                        dlq_store = create_federation_notification_dlq_store(
                            store_type=dlq_cfg.type,
                            db_path=dlq_cfg.path,
                        )
                        app._federation_dlq_store = dlq_store

                        # Attach DLQ store to notification service if it exists
                        ns = getattr(app, "_federation_notification_service", None)
                        if ns is not None:
                            ns._dlq_store = dlq_store

                    # Retry policy
                    retry_cfg = getattr(fed_cfg.notifications, "retry", None)
                    if retry_cfg is not None:
                        from agent_app.governance.policy_rollout_federation_notification import FederationNotificationRetryPolicy
                        retry_policy = FederationNotificationRetryPolicy(
                            max_attempts=retry_cfg.max_attempts,
                            backoff_seconds=retry_cfg.backoff_seconds,
                            send_to_dlq=retry_cfg.send_to_dlq,
                        )
                        ns = getattr(app, "_federation_notification_service", None)
                        if ns is not None:
                            ns._retry_policy = retry_policy

                            # Per-channel retry policies
                            by_channel = getattr(fed_cfg.notifications, "by_channel_retry", None)
                            if by_channel:
                                ns._by_channel_retry_policy = {
                                    ch: FederationNotificationRetryPolicy(**cfg.model_dump())
                                    for ch, cfg in by_channel.items()
                                }

                # Scheduled worker
                sw_cfg = getattr(fed_cfg, "scheduled_worker", None)
                if sw_cfg is not None and sw_cfg.enabled:
                    from agent_app.runtime.policy_rollout_federation_scheduled_worker import FederationScheduledWorker
                    from agent_app.runtime.distributed_lock import create_distributed_lock

                    sw_lock = None
                    if sw_cfg.lock_type:
                        sw_lock = create_distributed_lock(
                            store_type=sw_cfg.lock_type,
                            db_path=sw_cfg.lock_path,
                        )

                    scheduled_worker = FederationScheduledWorker(
                        escalation_worker=getattr(app, "_federation_escalation_worker", None),
                        notification_service=getattr(app, "_federation_notification_service", None),
                        distributed_lock=sw_lock,
                        interval_seconds=sw_cfg.interval_seconds,
                    )
                    app._federation_scheduled_worker = scheduled_worker
        except Exception:  # noqa: BLE001 — graceful failure
            pass

        # -- Phase 51: Federation notification templates, preferences, webhook signing --
        try:
            if fed_cfg is not None and hasattr(fed_cfg, "notifications") and fed_cfg.notifications is not None and fed_cfg.notifications.enabled:
                notif_cfg = fed_cfg.notifications

                # Template store and service
                tmpl_cfg = getattr(notif_cfg, "templates", None)
                if tmpl_cfg is not None and tmpl_cfg.enabled:
                    from agent_app.runtime.policy_rollout_federation_notification_template_store import create_federation_notification_template_store
                    from agent_app.runtime.policy_rollout_federation_notification_template_service import FederationNotificationTemplateService

                    tmpl_store = create_federation_notification_template_store(
                        store_type=tmpl_cfg.store_backend,
                        db_path=tmpl_cfg.store_path,
                    )
                    tmpl_service = FederationNotificationTemplateService(
                        store=tmpl_store,
                        strict_variables=tmpl_cfg.strict_variables,
                        default_template_id=tmpl_cfg.default_template_id,
                    )
                    app._federation_notification_template_store = tmpl_store
                    app._federation_notification_template_service = tmpl_service

                    # Attach template service to notification service if it exists
                    ns = getattr(app, "_federation_notification_service", None)
                    if ns is not None:
                        ns._template_service = tmpl_service

                # Preference store and service
                pref_cfg = getattr(notif_cfg, "preferences", None)
                if pref_cfg is not None and pref_cfg.enabled:
                    from agent_app.runtime.policy_rollout_federation_notification_preference_store import create_federation_notification_preference_store
                    from agent_app.runtime.policy_rollout_federation_notification_preference_service import FederationNotificationPreferenceService

                    pref_store = create_federation_notification_preference_store(
                        store_type=pref_cfg.store_backend,
                        db_path=pref_cfg.store_path,
                    )
                    pref_service = FederationNotificationPreferenceService(
                        store=pref_store,
                        default_delivery=pref_cfg.default_delivery,
                        failure_mode=pref_cfg.failure_mode,
                        mandatory_event_types=pref_cfg.mandatory_event_types,
                    )
                    app._federation_notification_preference_store = pref_store
                    app._federation_notification_preference_service = pref_service

                    # Attach preference service to notification service if it exists
                    ns = getattr(app, "_federation_notification_service", None)
                    if ns is not None:
                        ns._preference_service = pref_service

                # Webhook signature service and nonce store
                signing_cfg = getattr(notif_cfg, "webhook_signing", None)
                if signing_cfg is not None and signing_cfg.enabled:
                    try:
                        from agent_app.runtime.policy_rollout_federation_webhook_signature import FederationWebhookSignatureService
                        from agent_app.runtime.policy_rollout_federation_webhook_nonce_store import create_federation_webhook_nonce_store

                        nonce_store = create_federation_webhook_nonce_store(
                            store_type=signing_cfg.nonce_store_backend,
                            db_path=signing_cfg.nonce_store_path,
                        )
                        signature_service = FederationWebhookSignatureService(
                            active_key_id=signing_cfg.active_key_id,
                            keys=signing_cfg.keys,
                            signature_version=signing_cfg.signature_version,
                            timestamp_tolerance_seconds=signing_cfg.timestamp_tolerance_seconds,
                        )
                        app._federation_webhook_signature_service = signature_service
                        app._federation_webhook_nonce_store = nonce_store
                        app._federation_webhook_nonce_replay_protection = signing_cfg.nonce_replay_protection

                        # Attach signature service to notification service if it exists
                        ns = getattr(app, "_federation_notification_service", None)
                        if ns is not None:
                            ns._signature_service = signature_service
                    except Exception as exc:  # noqa: BLE001 — best-effort, but visible
                        import logging
                        logging.getLogger(__name__).warning(
                            "Failed to initialize federation webhook signature service: %s", exc
                        )
        except Exception:  # noqa: BLE001 — graceful failure
            pass

        # -- Phase 52: Federation notification observability, SLA, alerts --
        try:
            if (
                fed_cfg is not None
                and hasattr(fed_cfg, "notifications")
                and fed_cfg.notifications is not None
                and fed_cfg.notifications.enabled
            ):
                notif_cfg = fed_cfg.notifications

                # Observability config
                obs_cfg = getattr(notif_cfg, "observability", None)
                if obs_cfg is not None:
                    app._federation_notification_observability_config = obs_cfg

                # SLA config
                sla_cfg = getattr(notif_cfg, "sla", None)
                if sla_cfg is not None:
                    app._federation_notification_sla_config = sla_cfg

                # Alerts config
                alerts_cfg = getattr(notif_cfg, "alerts", None)
                if alerts_cfg is not None:
                    app._federation_notification_alert_config = alerts_cfg

                # Phase 53: Alert delivery config
                alert_delivery_cfg = getattr(notif_cfg, "alert_delivery", None)
                if alert_delivery_cfg is not None:
                    app._federation_notification_alert_delivery_config = alert_delivery_cfg

                # Phase 53: Retention config
                retention_cfg = getattr(notif_cfg, "retention", None)
                if retention_cfg is not None:
                    app._federation_notification_retention_config = retention_cfg

                # Phase 53: Rollup config
                rollup_cfg = getattr(notif_cfg, "rollup", None)
                if rollup_cfg is not None:
                    app._federation_notification_rollup_config = rollup_cfg

                # -- Phase 55: Alert delivery extensions (retry daemon, write actions, archive cleanup) --
                if alert_delivery_cfg is not None:
                    # Wire retry_daemon config from alert_delivery
                    retry_daemon_cfg = getattr(alert_delivery_cfg, "retry_daemon", None)
                    if retry_daemon_cfg is not None:
                        app._federation_notification_retry_daemon_config = retry_daemon_cfg
                    # Wire write_actions config from alert_delivery
                    write_actions_cfg = getattr(alert_delivery_cfg, "write_actions", None)
                    if write_actions_cfg is not None:
                        app._federation_notification_write_actions_config = write_actions_cfg
                    # Phase 56: Wire priority_queue_store config from alert_delivery
                    priority_queue_store_cfg = getattr(alert_delivery_cfg, "priority_queue_store", None)
                    if priority_queue_store_cfg is not None:
                        app._federation_notification_priority_queue_store_config = priority_queue_store_cfg
                    # Phase 57: Wire daemon_id from alert_delivery
                    daemon_id_cfg = getattr(alert_delivery_cfg, "daemon_id", None)
                    if daemon_id_cfg is not None:
                        app._federation_notification_retry_daemon_id = daemon_id_cfg
                    # Phase 57: Wire state_store config from alert_delivery
                    state_store_cfg = getattr(alert_delivery_cfg, "state_store", None)
                    if state_store_cfg is not None:
                        app._federation_notification_retry_daemon_state_store_config = state_store_cfg
                    # Phase 57: Wire claim_lease_seconds from alert_delivery
                    claim_lease_cfg = getattr(alert_delivery_cfg, "claim_lease_seconds", None)
                    if claim_lease_cfg is not None:
                        app._federation_notification_claim_lease_seconds = claim_lease_cfg
                    # Phase 57: Wire batch_replay_enqueue_default from alert_delivery
                    batch_replay_enqueue_cfg = getattr(alert_delivery_cfg, "batch_replay_enqueue_default", None)
                    if batch_replay_enqueue_cfg is not None:
                        app._federation_notification_batch_replay_enqueue_default = batch_replay_enqueue_cfg

                # Wire archive_cleanup config from notification top-level
                archive_cleanup_cfg = getattr(notif_cfg, "archive_cleanup", None)
                if archive_cleanup_cfg is not None:
                    app._federation_notification_archive_cleanup_config = archive_cleanup_cfg

                # Phase 59: Multi-instance production readiness wiring
                try:
                    # Priority queue Redis config
                    pq_redis_cfg = getattr(notif_cfg, "priority_queue_redis", None)
                    if pq_redis_cfg is not None:
                        app._federation_notification_priority_queue_redis_config = pq_redis_cfg

                    # Distributed lock config
                    dist_lock_cfg = getattr(notif_cfg, "distributed_lock", None)
                    if dist_lock_cfg is not None:
                        app._federation_notification_distributed_lock_config = dist_lock_cfg

                    # Replay idempotency config and store
                    replay_idem_cfg = getattr(notif_cfg, "replay_idempotency", None)
                    if replay_idem_cfg is not None and replay_idem_cfg.enabled:
                        from agent_app.runtime.policy_rollout_federation_notification_replay_idempotency import (
                            InMemoryReplayIdempotencyStore,
                            SQLiteReplayIdempotencyStore,
                        )
                        if replay_idem_cfg.type == "sqlite":
                            replay_idem_store = SQLiteReplayIdempotencyStore(
                                db_path=replay_idem_cfg.path or ".agent_app/federation_replay_idempotency.db"
                            )
                        else:
                            replay_idem_store = InMemoryReplayIdempotencyStore()
                        app.replay_idempotency_store = replay_idem_store
                        app._federation_notification_replay_idempotency_config = replay_idem_cfg

                    # Replay rate limiter config and store
                    rate_limit_cfg = getattr(notif_cfg, "replay_rate_limiter", None)
                    if rate_limit_cfg is not None and rate_limit_cfg.enabled:
                        from agent_app.runtime.policy_rollout_federation_notification_replay_rate_limiter import (
                            InMemoryReplayRateLimiterStore,
                            SQLiteReplayRateLimiterStore,
                        )
                        if rate_limit_cfg.type == "sqlite":
                            rate_limit_store = SQLiteReplayRateLimiterStore(
                                db_path=rate_limit_cfg.path or ".agent_app/federation_replay_rate_limiter.db"
                            )
                        else:
                            rate_limit_store = InMemoryReplayRateLimiterStore()
                        app.replay_rate_limiter_store = rate_limit_store
                        app._federation_notification_replay_rate_limiter_config = rate_limit_cfg

                    # Dead letter policy config and store
                    dl_policy_cfg = getattr(notif_cfg, "dead_letter_policy", None)
                    if dl_policy_cfg is not None and dl_policy_cfg.enabled:
                        from agent_app.runtime.policy_rollout_federation_notification_dead_letter_policy import (
                            InMemoryDeadLetterPolicyStore,
                            SQLiteDeadLetterPolicyStore,
                        )
                        if dl_policy_cfg.type == "sqlite":
                            dl_policy_store = SQLiteDeadLetterPolicyStore(
                                db_path=dl_policy_cfg.path or ".agent_app/federation_dead_letter_policy.db"
                            )
                        else:
                            dl_policy_store = InMemoryDeadLetterPolicyStore()
                        app.dead_letter_policy_store = dl_policy_store
                        app._federation_notification_dead_letter_policy_config = dl_policy_cfg

                    # Enhanced metrics
                    enhanced_metrics_cfg = getattr(notif_cfg, "enhanced_metrics", None)
                    if enhanced_metrics_cfg is not None and enhanced_metrics_cfg.enabled:
                        from agent_app.runtime.policy_rollout_federation_notification_metrics_enhanced import EnhancedMetrics
                        app.enhanced_metrics = EnhancedMetrics()
                        app._federation_notification_enhanced_metrics_config = enhanced_metrics_cfg

                    # Webhook key rotation config and service
                    key_rotation_cfg = getattr(notif_cfg, "webhook_key_rotation", None)
                    if key_rotation_cfg is not None and key_rotation_cfg.enabled:
                        from agent_app.runtime.policy_rollout_federation_notification_webhook_key_rotation import (
                            WebhookKeyRotationService,
                            InMemoryWebhookKeyRotationStore,
                            SQLiteWebhookKeyRotationStore,
                        )
                        if key_rotation_cfg.type == "sqlite":
                            key_rotation_store = SQLiteWebhookKeyRotationStore(
                                db_path=key_rotation_cfg.path or ".agent_app/federation_webhook_key_rotation.db"
                            )
                        else:
                            key_rotation_store = InMemoryWebhookKeyRotationStore()
                        key_rotation_service = WebhookKeyRotationService(
                            store=key_rotation_store,
                            rotation_interval_hours=key_rotation_cfg.rotation_interval_hours,
                            keep_previous_count=key_rotation_cfg.keep_previous_count,
                            key_bits=key_rotation_cfg.key_bits,
                        )
                        app.webhook_key_rotation_service = key_rotation_service
                        app._federation_notification_webhook_key_rotation_config = key_rotation_cfg
                except Exception:  # noqa: BLE001 — graceful failure
                    pass
        except Exception:  # noqa: BLE001 — graceful failure
            pass

    app._release_config = release_config
    return app


def _create_backend(
    runtime_cfg: Any,
    agent_registry: Any,
    tool_registry: Any,
    approval_store: Any = None,
    audit_logger: Any = None,
    permission_checker: Any = None,
    rate_limiter: Any = None,
) -> Any:
    """Create the execution backend based on runtime config.

    Args:
        runtime_cfg: RuntimeConfig instance (or None).
        agent_registry: AgentRegistry for OpenAIAgentsBackend.
        tool_registry: ToolRegistry for OpenAIAgentsBackend.
        approval_store: Approval store for governance (Phase 8).
        audit_logger: Audit logger for governance (Phase 8).
        permission_checker: Permission checker for governance (Phase 8).
        rate_limiter: Approval rate limiter (Phase 21).

    Returns:
        A backend instance implementing AgentBackend.

    Raises:
        ValueError: If backend type is unknown.
        RuntimeError: If openai backend requested but SDK not installed.
    """
    from agent_app.runtime.backends import DryRunBackend
    from agent_app.runtime.tool_executor import ToolExecutor

    if runtime_cfg is None or runtime_cfg.backend == "dry_run":
        return DryRunBackend()

    if runtime_cfg.backend == "openai":
        # Eagerly validate that the SDK is available.
        from agent_app.adapters.openai_agents import _load_agents_sdk
        _load_agents_sdk()  # raises RuntimeError if missing
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        # Phase 8: Build governance components if not provided
        tool_executor: ToolExecutor | None = None
        if approval_store is not None and audit_logger is not None:
            from agent_app.governance.permission import DefaultPermissionChecker
            tool_executor = ToolExecutor(
                tool_registry=tool_registry,
                approval_store=approval_store,
                permission_checker=permission_checker or DefaultPermissionChecker(),
                audit_logger=audit_logger,
                rate_limiter=rate_limiter,
            )

        # Phase 10: Extract hitl_mode from openai config
        hitl_mode = "wrapper"  # default
        openai_cfg = getattr(runtime_cfg, "openai", None)
        if isinstance(openai_cfg, dict):
            hitl_mode = openai_cfg.get("hitl_mode", "wrapper")

        return OpenAIAgentsBackend(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
            raise_on_missing=True,
            tool_executor=tool_executor,
            approval_store=approval_store,
            audit_logger=audit_logger,
            permission_checker=permission_checker,
            hitl_mode=hitl_mode,
            trace_collector=trace_collector,
        )

    raise ValueError(
        f"Unknown backend '{runtime_cfg.backend}'. "
        "Supported: 'dry_run', 'openai'."
    )


class _RegistryBundle:
    """Lightweight container so AgentApp can share a single registry set."""

    def __init__(
        self,
        agent_registry: AgentRegistry,
        tool_registry: ToolRegistry,
        workflow_registry: WorkflowRegistry,
    ) -> None:
        self.agent_registry = agent_registry
        self.tool_registry = tool_registry
        self.workflow_registry = workflow_registry


def _bundle(
    ar: AgentRegistry,
    tr: ToolRegistry,
    wr: WorkflowRegistry,
) -> _RegistryBundle:
    return _RegistryBundle(ar, tr, wr)
