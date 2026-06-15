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

    # -- Governance: rate limiter (Phase 21) --
    rate_limiter: Any = None
    if gov and getattr(gov, "rate_limit", None):
        from agent_app.runtime.approval_rate_limit import InMemoryApprovalRateLimiter
        rl_cfg = gov.rate_limit
        rate_limiter = InMemoryApprovalRateLimiter(
            max_requests=getattr(rl_cfg, "max_requests", 10),
            window_seconds=getattr(rl_cfg, "window_seconds", 60),
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

    # -- Observability: trace collector (Phase 12) --
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
