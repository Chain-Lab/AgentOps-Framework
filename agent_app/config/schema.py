"""Configuration schema — Pydantic models for agentapp.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class ToolConfig(BaseModel):
    """Tool declaration in YAML config.

    Attributes:
        name: Fully-qualified tool name.
        type: Tool type (currently only "function" is supported).
        risk_level: Risk classification.
        requires_approval: Whether this tool needs human approval.
        permissions: Required permissions.
    """

    name: str = Field(..., description="Fully-qualified tool name")
    type: str = Field(default="function", description="Tool type")
    risk_level: str = Field(default="low")
    requires_approval: bool = Field(default=False)
    permissions: list[str] = Field(default_factory=list)


class AgentConfig(BaseModel):
    """Agent declaration in YAML config.

    Attributes:
        name: Agent identifier.
        description: Human-readable description.
        model: Model override.
        instructions: Prompt file path or inline text.
        tools: List of registered tool names.
        handoffs: List of handoff target agent names.
        guardrails: List of guardrail policy names.
    """

    name: str = Field(..., description="Agent identifier")
    description: str | None = Field(default=None)
    model: str | None = Field(default=None)
    instructions: str = Field(
        ..., description="Prompt file path or inline text"
    )
    tools: list[str] = Field(default_factory=list)
    handoffs: list[str] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)

    @field_validator("instructions")
    @classmethod
    def _validate_instructions(cls, v: str) -> str:
        return v


class RuntimeConfig(BaseModel):
    """Runtime configuration block.

    Supports both flat and nested session/run_state/workflow_state configs.
    """

    backend: str = Field(default="dry_run", description="Execution backend: dry_run | openai")
    session_type: str = Field(default="memory", description="Session backend")
    session_path: str | None = Field(default=None, description="SQLite path")
    run_state_type: str = Field(default="memory", description="Run state store: memory | sqlite")
    run_state_path: str | None = Field(default=None, description="Run state SQLite path")
    workflow_state_type: str = Field(default="memory", description="Workflow state store: memory | sqlite")
    workflow_state_path: str | None = Field(default=None, description="Workflow state SQLite path")
    lease_renewal_config: LeaseRenewalConfig | None = Field(
        default=None,
        description="Lease renewal configuration (Phase 15.2)",
    )
    dag_lease_config: DagLeaseConfig | None = Field(
        default=None,
        description="DAG lease backend configuration (Phase 16.2)",
    )
    dag_snapshot_config: DagSnapshotConfig | None = Field(
        default=None,
        description="DAG execution snapshot configuration (Phase 16.0)",
    )
    dag_compensation_config: DagCompensationConfig | None = Field(
        default=None,
        description="DAG compensation persistence configuration (Phase 16.1)",
    )
    recovery_config: dict[str, Any] | None = Field(
        default=None,
        description="Recovery scanner configuration (Phase 16.5)",
    )
    openai: dict[str, Any] | None = Field(default=None, description="OpenAI backend options")

    @model_validator(mode="before")
    @classmethod
    def _normalize_session(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        session = data.get("session")
        if isinstance(session, dict):
            result = dict(data)
            result["session_type"] = session.get("type", result.get("session_type", "memory"))
            result["session_path"] = session.get("path", result.get("session_path"))
            result.pop("session", None)
            return result
        return data

    @model_validator(mode="before")
    @classmethod
    def _normalize_run_state(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        run_state = data.get("run_state")
        if isinstance(run_state, dict):
            result = dict(data)
            result["run_state_type"] = run_state.get("type", result.get("run_state_type", "memory"))
            result["run_state_path"] = run_state.get("path", result.get("run_state_path"))
            result.pop("run_state", None)
            return result
        return data

    @model_validator(mode="before")
    @classmethod
    def _normalize_workflow_state(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        wf_state = data.get("workflow_state")
        if isinstance(wf_state, dict):
            result = dict(data)
            result["workflow_state_type"] = wf_state.get("type", result.get("workflow_state_type", "memory"))
            result["workflow_state_path"] = wf_state.get("path", result.get("workflow_state_path"))
            result.pop("workflow_state", None)
            return result
        # Also support flat string shorthand: workflow_state: memory
        if isinstance(wf_state, str):
            result = dict(data)
            result["workflow_state_type"] = wf_state
            result.pop("workflow_state", None)
            return result
        return data

    @model_validator(mode="before")
    @classmethod
    def _normalize_lease_renewal(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        lr = data.get("lease_renewal")
        if isinstance(lr, dict):
            result = dict(data)
            result["lease_renewal_config"] = lr
            result.pop("lease_renewal", None)
            return result
        return data

    @model_validator(mode="before")
    @classmethod
    def _normalize_dag_snapshot(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        ds = data.get("dag_snapshot")
        if isinstance(ds, dict):
            result = dict(data)
            result["dag_snapshot_config"] = ds
            result.pop("dag_snapshot", None)
            return result
        return data

    @model_validator(mode="before")
    @classmethod
    def _normalize_dag_compensation(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        dc = data.get("dag_compensation")
        if isinstance(dc, dict):
            result = dict(data)
            result["dag_compensation_config"] = dc
            result.pop("dag_compensation", None)
            return result
        return data

    @model_validator(mode="before")
    @classmethod
    def _normalize_dag_lease(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        dl = data.get("dag_lease")
        if isinstance(dl, dict):
            result = dict(data)
            result["dag_lease_config"] = dl
            result.pop("dag_lease", None)
            return result
        return data


class ApprovalConfig(BaseModel):
    """Approval store configuration."""

    type: str = Field(default="memory", description="Store type: memory | sqlite")
    path: str | None = Field(default=None, description="SQLite db path")
    default_ttl_seconds: int | None = Field(
        default=None,
        ge=0,
        description="Default TTL for new approvals in seconds (None = no expiry)",
    )


class RateLimitConfig(BaseModel):
    """Approval rate limiting configuration (Phase 21)."""

    max_requests: int = Field(default=10, ge=1, description="Max approval requests per window")
    window_seconds: int = Field(default=60, ge=1, description="Rate limit window in seconds")


class AuditConfig(BaseModel):
    """Audit logger configuration."""

    type: str = Field(default="memory", description="Logger type: memory | sqlite")
    path: str | None = Field(default=None, description="SQLite db path")


class PermissionConfig(BaseModel):
    """Permission checker configuration."""

    mode: str = Field(default="default", description="Permission mode")


class TracingConfig(BaseModel):
    """Observability tracing configuration."""

    type: str = Field(default="memory", description="Tracer type: noop | memory | jsonl")
    path: str | None = Field(default=None, description="Path for jsonl tracer")
    include_inputs: bool = Field(default=False, description="Include inputs in events")
    include_outputs: bool = Field(default=False, description="Include outputs in events")
    max_traces: int | None = Field(default=None, description="Max traces to retain in memory")
    max_events_per_trace: int | None = Field(default=None, description="Max events per trace in memory")


# ---------------------------------------------------------------------------
# Phase 23: Policy Engine config
# ---------------------------------------------------------------------------

from agent_app.governance.policy import _VALID_ACTIONS


class PolicyRuleConfig(BaseModel):
    """A single policy rule from YAML config."""

    name: str = Field(..., description="Unique rule name")
    when: dict[str, Any] = Field(
        ...,
        description="Conditions to match (tool_name, risk_level, etc.)",
    )
    then: dict[str, Any] = Field(
        ...,
        description="Actions to take when conditions match",
    )


class PolicyEngineConfig(BaseModel):
    """Governance policy engine configuration."""

    enabled: bool = Field(default=False, description="Enable policy engine")
    default_action: str = Field(default="allow", description="Fallback when no rule matches")
    rules: list[PolicyRuleConfig] = Field(
        default_factory=list,
        description="Ordered policy rules",
    )

    @field_validator("default_action")
    @classmethod
    def _validate_default_action(cls, v: str) -> str:
        valid = {"allow", "deny", "require_approval", "audit_only"}
        if v not in valid:
            raise ValueError(
                f"Invalid default_action '{v}'. Must be one of: {sorted(valid)}."
            )
        return v


class PolicyConsoleConfig(BaseModel):
    """Policy console UI configuration (Phase 26).

    Defaults to disabled.  When enabled, a read-only HTML console is
    mounted at ``base_path`` under the FastAPI app.
    """

    enabled: bool = Field(
        default=False,
        description="Enable policy ops console (default: disabled)",
    )
    base_path: str = Field(
        default="/policy-console",
        description="URL prefix for console routes",
    )
    title: str = Field(
        default="Agent App Policy Console",
        description="Page title shown in the console",
    )
    page_size: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Default page size for decision lists",
    )


class PolicyDecisionStoreConfig(BaseModel):
    """Policy decision persistence configuration (Phase 25)."""

    type: str = Field(
        default="memory",
        description="Store backend: memory | sqlite",
    )
    path: str | None = Field(
        default=None,
        description="SQLite database path (required when type=sqlite)",
    )


# ---------------------------------------------------------------------------
# Phase 29: Policy Release config
# ---------------------------------------------------------------------------

class PolicyGateRuleConfig(BaseModel):
    """A single release gate rule from YAML config."""

    name: str = Field(..., description="Unique rule name")
    description: str | None = Field(default=None, description="Rule description")
    max_changed_decisions: int | None = Field(
        default=None, description="Max changed decisions allowed"
    )
    max_changed_ratio: float | None = Field(
        default=None,
        description="Max changed ratio (0.0–1.0)",
    )
    max_failed_replays: int | None = Field(
        default=None, description="Max failed replays allowed"
    )
    max_new_denies: int | None = Field(
        default=None, description="Max new denies introduced"
    )
    max_new_approvals: int | None = Field(
        default=None, description="Max new approvals introduced"
    )
    fail_on_missing_required_context: bool = Field(
        default=False,
        description="Fail if required context is missing in replay",
    )


class PolicyReleaseStoreConfig(BaseModel):
    """Policy release store configuration."""

    type: str = Field(
        default="memory",
        description="Store backend: memory | sqlite",
    )
    path: str | None = Field(
        default=None,
        description="SQLite database path (required when type=sqlite)",
    )


class PolicyReleaseConfig(BaseModel):
    """Policy release gate configuration (Phase 29)."""

    bundles: PolicyReleaseStoreConfig = Field(
        default_factory=PolicyReleaseStoreConfig,
        description="Policy bundle store configuration",
    )
    gates: PolicyReleaseStoreConfig = Field(
        default_factory=PolicyReleaseStoreConfig,
        description="Policy gate result store configuration",
    )
    rules: list[PolicyGateRuleConfig] = Field(
        default_factory=list,
        description="Release gate rules",
    )


class LeaseRenewalConfig(BaseModel):
    """Configuration for best-effort background lease renewal (Phase 15.2).

    Lease renewal is NOT exactly-once execution, NOT a distributed
    worker backend, and does NOT survive process crashes.  It simply
    keeps the lease alive while the current process is running the DAG.

    Attributes:
        renew_enabled: Whether to enable automatic lease renewal during
            workflow execution.  Defaults to True when a state store is
            configured.
        renew_interval_seconds: How often to renew the lease, in seconds.
            Defaults to ttl_seconds / 3 if not specified.
        ttl_seconds: Lease TTL in seconds.  Used as the renewal period
            and as the default for interval calculation.  Defaults to 300.
    """

    renew_enabled: bool = Field(
        default=True,
        description="Enable automatic lease renewal during workflow execution",
    )
    renew_interval_seconds: float | None = Field(
        default=None,
        description="Renewal interval in seconds (default: ttl_seconds / 3)",
    )
    ttl_seconds: int = Field(
        default=300,
        ge=1,
        description="Lease TTL in seconds",
    )

    @field_validator("renew_interval_seconds")
    @classmethod
    def _validate_interval(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError("renew_interval_seconds must be positive.")
        return v


class DagLeaseMetricsConfig(BaseModel):
    """Configuration for lease backend metrics (Phase 16.3).

    Metrics are in-process counters for operator visibility.  They do
    NOT imply exactly-once execution and are NOT Prometheus/OpenTelemetry
    exporters.

    Attributes:
        enabled: Whether to enable lease backend metrics collection.
            Defaults to False (opt-in to avoid overhead).
    """

    enabled: bool = Field(
        default=False,
        description="Enable lease backend metrics collection",
    )


class DagLeaseHealthConfig(BaseModel):
    """Configuration for lease backend health checks (Phase 16.3).

    Health checks are lightweight, non-destructive diagnostics.  They do
    NOT imply distributed recovery or self-healing.

    Attributes:
        enabled: Whether to enable lease backend health checks.
            Defaults to True.
    """

    enabled: bool = Field(
        default=True,
        description="Enable lease backend health checks",
    )


class DagLeaseConfig(BaseModel):
    """Configuration for pluggable DAG lease backend (Phase 16.2/16.4).

    Lease coordination is best-effort and does NOT provide exactly-once
    execution.  The default backend is ``state_store`` which delegates to
    the configured workflow state store.  Standalone ``memory``, ``sqlite``,
    and ``redis`` backends are also available.

    Attributes:
        backend: Lease backend type — ``"state_store"`` (default),
            ``"memory"``, ``"sqlite"``, or ``"redis"``.
        db_path: SQLite database path (required when backend="sqlite").
        ttl_seconds: Lease TTL in seconds.  Defaults to 300.
        allow_steal_expired: Whether to allow stealing expired leases.
            Defaults to True.
        renew_before_seconds: Renew the lease this many seconds before
            expiry.  Defaults to 60.
        redis_url: Redis connection URL (required when backend="redis").
            Defaults to "redis://localhost:6379/0".
        key_prefix: Prefix for Redis lease keys (when backend="redis").
            Defaults to "agent_app:dag_lease".
        metrics: Metrics configuration (Phase 16.3).
        health: Health check configuration (Phase 16.3).
    """

    backend: str = Field(
        default="state_store",
        description="Lease backend: state_store | memory | sqlite | redis",
    )
    db_path: str | None = Field(
        default=None,
        description="SQLite db path (required when backend=sqlite)",
    )
    ttl_seconds: int = Field(
        default=300,
        ge=1,
        description="Lease TTL in seconds",
    )
    allow_steal_expired: bool = Field(
        default=True,
        description="Allow stealing expired leases",
    )
    renew_before_seconds: int = Field(
        default=60,
        ge=0,
        description="Renew lease this many seconds before expiry",
    )
    redis_url: str | None = Field(
        default=None,
        description="Redis URL (required when backend=redis)",
    )
    key_prefix: str | None = Field(
        default=None,
        description="Redis key prefix (when backend=redis)",
    )
    metrics: DagLeaseMetricsConfig | None = Field(
        default=None,
        description="Lease backend metrics configuration (Phase 16.3)",
    )
    health: DagLeaseHealthConfig | None = Field(
        default=None,
        description="Lease backend health check configuration (Phase 16.3)",
    )

    @field_validator("backend")
    @classmethod
    def _validate_backend(cls, v: str) -> str:
        if v not in ("state_store", "memory", "sqlite", "redis"):
            raise ValueError(
                f"Invalid lease backend '{v}'. "
                "Must be 'state_store', 'memory', 'sqlite', or 'redis'."
            )
        return v


class DagSnapshotConfig(BaseModel):
    """Configuration for DAG execution snapshots (Phase 16.0).

    Snapshots are recovery aids written at node-level state transitions.
    They do NOT guarantee exactly-once execution and do NOT replace
    lease renewal or business-level idempotency.

    Attributes:
        enabled: Whether snapshot persistence is enabled.  Defaults to True.
        store: Snapshot store type: "memory" or "sqlite".  Defaults to "memory".
        path: SQLite database path (required when store="sqlite").
        save_on_node_start: Save snapshot when a node starts executing.
        save_on_node_complete: Save snapshot when a node completes.
        save_on_interrupt: Save snapshot when execution is interrupted
            (e.g., approval wait).
        save_on_failure: Save snapshot when a node or workflow fails.
    """

    enabled: bool = Field(
        default=True,
        description="Enable DAG execution snapshot persistence",
    )
    store: str = Field(
        default="memory",
        description="Snapshot store type: memory | sqlite",
    )
    path: str | None = Field(
        default=None,
        description="SQLite database path (required when store=sqlite)",
    )
    save_on_node_start: bool = Field(
        default=True,
        description="Save snapshot on node start",
    )
    save_on_node_complete: bool = Field(
        default=True,
        description="Save snapshot on node completion",
    )
    save_on_interrupt: bool = Field(
        default=True,
        description="Save snapshot on interrupt (e.g., approval wait)",
    )
    save_on_failure: bool = Field(
        default=True,
        description="Save snapshot on node/workflow failure",
    )

    @field_validator("store")
    @classmethod
    def _validate_store(cls, v: str) -> str:
        if v not in ("memory", "sqlite"):
            raise ValueError(f"Invalid snapshot store '{v}'. Must be 'memory' or 'sqlite'.")
        return v


class DagCompensationConfig(BaseModel):
    """Configuration for DAG compensation state persistence (Phase 16.1).

    Compensation state persistence tracks the execution of compensation
    handlers for failed DAG runs.  It enables resume of incomplete
    compensation and provides an audit trail of compensation actions.

    Compensation state is a recovery aid — it does NOT guarantee
    exactly-once execution and does NOT replace lease renewal, snapshot,
    or business-level idempotency.

    Attributes:
        enabled: Whether compensation state persistence is enabled.
            Defaults to True.
        store: State store type: "memory" or "sqlite".  Defaults to "memory".
        path: SQLite database path (required when store="sqlite").
        max_attempts: Default maximum retry attempts for compensation actions.
            Defaults to 1.
        resume_incomplete: Whether resume() should automatically continue
            incomplete compensation.  Defaults to True.
    """

    enabled: bool = Field(
        default=True,
        description="Enable compensation state persistence",
    )
    store: str = Field(
        default="memory",
        description="Compensation state store type: memory | sqlite",
    )
    path: str | None = Field(
        default=None,
        description="SQLite database path (required when store=sqlite)",
    )
    max_attempts: int = Field(
        default=1,
        ge=1,
        description="Default max retry attempts for compensation actions",
    )
    resume_incomplete: bool = Field(
        default=True,
        description="Resume incomplete compensation on workflow resume",
    )

    @field_validator("store")
    @classmethod
    def _validate_store(cls, v: str) -> str:
        if v not in ("memory", "sqlite"):
            raise ValueError(
                f"Invalid compensation store '{v}'. Must be 'memory' or 'sqlite'."
            )
        return v


class ObservabilityConfig(BaseModel):
    """Observability configuration block."""

    tracing: TracingConfig = Field(default_factory=TracingConfig)


class GovernanceConfig(BaseModel):
    """Governance configuration block."""

    approvals: ApprovalConfig = Field(default_factory=ApprovalConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    permissions: PermissionConfig = Field(default_factory=PermissionConfig)
    rate_limit: RateLimitConfig | None = Field(default=None, description="Rate limiting config")
    policies: PolicyEngineConfig | None = Field(
        default=None,
        description="Policy engine configuration (Phase 23)",
    )
    policy_decisions: PolicyDecisionStoreConfig | None = Field(
        default=None,
        description="Policy decision store configuration (Phase 25)",
    )
    policy_console: PolicyConsoleConfig | None = Field(
        default=None,
        description="Policy ops console configuration (Phase 26)",
    )
    policy_release: PolicyReleaseConfig | None = Field(
        default=None,
        description="Policy release gate configuration (Phase 29)",
    )


class AppConfig(BaseModel):
    """Top-level application configuration.

    The YAML file uses a dict keyed by name for agents/tools/workflows,
    which Pydantic converts to a list of AgentConfig / ToolConfig objects.
    """

    app: dict[str, str] = Field(default_factory=dict)
    models: dict[str, str] = Field(default_factory=dict)
    agents: list[AgentConfig] = Field(default_factory=list)
    tools: list[ToolConfig] = Field(default_factory=list)
    workflows: dict[str, Any] = Field(default_factory=dict)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    governance: GovernanceConfig | None = Field(default=None)
    observability: ObservabilityConfig | None = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def _normalize_dicts_to_lists(cls, data: Any) -> Any:
        """Convert dict-keyed agents/tools into list format for Pydantic."""
        if not isinstance(data, dict):
            return data

        result = dict(data)

        # agents: {name: {config}} → [{name: name, ...config}]
        if "agents" in result and isinstance(result["agents"], dict):
            result["agents"] = [
                {"name": name, **cfg}
                for name, cfg in result["agents"].items()
            ]

        # tools: {name: {config}} → [{name: name, ...config}]
        if "tools" in result and isinstance(result["tools"], dict):
            result["tools"] = [
                {"name": name, **cfg}
                for name, cfg in result["tools"].items()
            ]

        # governance: {approvals: {type: ...}} — flatten nested keys
        gov = result.get("governance")
        if isinstance(gov, dict):
            normalized_gov = {}
            for section in ("approvals", "audit", "permissions", "policies",
                            "policy_decisions", "policy_console", "policy_release"):
                val = gov.get(section)
                if isinstance(val, dict):
                    normalized_gov[section] = val
                elif val is not None:
                    normalized_gov[section] = {"type": str(val)}
            result["governance"] = normalized_gov

        return result

    @model_validator(mode="after")
    def _check_duplicate_agent_names(self) -> AppConfig:
        names = [a.name for a in self.agents]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"Duplicate agent names in config: {sorted(dupes)}")
        return self
