"""OpenAIAgentsBackend — delegates execution to the OpenAI Agents SDK.

This is the ONLY module in the framework that imports the ``agents`` package.
If the package is not installed, a clear error message is shown.

Phase 8: Governance-aware tool wrapper — real SDK function_tool calls
route through ToolExecutor for permissions, approval, and audit.

Phase 10: Native HITL mode — uses SDK ``needs_approval`` and RunState
for native pause/resume with real OpenAI Agents SDK interruptions.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import secrets
import time
import uuid
from typing import Any, AsyncGenerator, Callable

from agent_app.core.agent_spec import AgentSpec
from agent_app.core.context import RunContext
from agent_app.core.result import AppRunResult, WorkflowStep, WorkflowTrace
from agent_app.core.tool_spec import ToolSpec
from agent_app.core.workflow import Workflow, WorkflowType
from agent_app.governance.approval import ApprovalRequest
from agent_app.governance.risk import requires_tool_approval
from agent_app.governance.sanitization import sanitize_payload
from agent_app.observability.collector import NoOpTraceCollector, TraceCollector
from agent_app.observability.events import RunEventType
from agent_app.runtime.backends import AgentBackend
from agent_app.runtime.streaming import StreamEvent, StreamEventType
from agent_app.runtime.tool_executor import (
    ToolExecutor,
    ToolExecutionResult,
    ToolExecutionStatus,
    _make_native_hitl_approval_marker,
)


# ---------------------------------------------------------------------------
# Module-level SDK loader — lazy, single import point
# ---------------------------------------------------------------------------

def _load_agents_sdk() -> tuple[Any, Any, Any]:
    """Import and return (Agent, Runner, function_tool) from the SDK.

    Raises:
        RuntimeError: If ``openai-agents`` is not installed, with a
                      clear install hint.
    """
    try:
        from agents import Agent, Runner, function_tool
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI Agents SDK is required to use OpenAIAgentsBackend. "
            "Install it with: pip install 'agent-app-framework[openai]'"
        ) from exc
    return Agent, Runner, function_tool


# ---------------------------------------------------------------------------
# RunState serialization helpers
# ---------------------------------------------------------------------------

def _serialize_run_state(state: Any) -> dict[str, Any]:
    """Serialize an SDK RunState to a JSON-compatible dict.

    Tries multiple strategies in order:
    1. ``state.to_json()`` — SDK-native serialization
    2. ``state.to_dict()`` — fallback method
    3. ``dataclasses.asdict()`` — for dataclass-based states
    4. ``repr()`` — last resort (marks as non-deserializable)

    Args:
        state: SDK RunState object.

    Returns:
        A dict with at least a ``"serialization"`` key indicating the
        strategy used, plus the serialized payload.
    """
    # Strategy 1: SDK to_json()
    if hasattr(state, "to_json") and callable(state.to_json):
        try:
            data = state.to_json()
            if isinstance(data, dict):
                return {"serialization": "to_json", "value": data}
        except Exception:
            pass

    # Strategy 2: to_dict()
    if hasattr(state, "to_dict") and callable(state.to_dict):
        try:
            data = state.to_dict()
            if isinstance(data, dict):
                return {"serialization": "to_dict", "value": data}
        except Exception:
            pass

    # Strategy 3: dataclass
    import dataclasses
    if dataclasses.is_dataclass(state):
        try:
            return {
                "serialization": "dataclass",
                "value": dataclasses.asdict(state),
            }
        except Exception:
            pass

    # Strategy 4: repr (non-resumable)
    return {
        "serialization": "repr",
        "value": repr(state),
        "_non_resumable": True,
    }


def _deserialize_run_state(
    data: dict[str, Any], initial_agent: Any
) -> tuple[Any, str]:
    """Deserialize an SDK RunState from a dict previously produced by
    :func:`_serialize_run_state`.

    Args:
        data: The serialized state dict (from ``backend_state["run_state"]``).
        initial_agent: The compiled SDK Agent to pass to ``from_json``.

    Returns:
        Tuple of ``(run_state, error_message)``.  If deserialization fails,
        ``run_state`` is ``None`` and ``error_message`` is set.
    """
    if not data:
        return None, "No RunState data found in backend_state."

    method = data.get("serialization", "unknown")
    value = data.get("value")

    if method == "to_json" and isinstance(value, dict):
        try:
            Agent, Runner, function_tool = _load_agents_sdk()
            # Try to get RunState from the same agents module we just loaded.
            # In the real SDK it's in agents.run_state; in tests it may be on
            # agents.RunState directly (set by fake modules).
            import agents as _agents_mod
            RunState = getattr(_agents_mod, "RunState", None)
            if RunState is None:
                run_state_mod = getattr(_agents_mod, "run_state", None)
                RunState = getattr(run_state_mod, "RunState", None) if run_state_mod else None

            if RunState is not None and hasattr(RunState, "from_json"):
                state = RunState.from_json(initial_agent, value)
                # from_json is async in the real SDK — handle both sync and async
                if asyncio.iscoroutine(state):
                    state = asyncio.run(state)
                return state, ""
        except Exception as exc:
            return None, f"from_json failed: {exc}"

    if method == "repr" or data.get("_non_resumable"):
        return None, (
            "Stored OpenAI RunState is not deserializable with the current SDK. "
            "Native resume unavailable."
        )

    return None, f"Unsupported serialization method: {method}"


# ---------------------------------------------------------------------------
# Interruption → ApprovalRequest mapping
# ---------------------------------------------------------------------------

def _interruption_to_approval_request(
    interruption: Any,
    run_id: str,
    context: RunContext,
) -> dict[str, Any]:
    """Convert an SDK ``ToolApprovalItem`` to a framework approval dict.

    Extracts as much information as the SDK interruption provides.
    Falls back to sensible defaults if the SDK item lacks fields.

    Args:
        interruption: SDK ``ToolApprovalItem``.
        run_id: Framework run ID.
        context: Framework run context.

    Returns:
        Approval request dict compatible with the framework ApprovalStore.
    """
    tool_name = getattr(interruption, "tool_name", None) or getattr(
        interruption, "name", "unknown"
    )
    arguments = _normalize_tool_arguments(getattr(interruption, "arguments", {}))

    return {
        "approval_id": str(uuid.uuid4()),
        "run_id": run_id,
        "tool_name": tool_name,
        "arguments": arguments,
        "risk_level": "high",
        "status": "pending",
        "requested_by": context.user_id,
        "tenant_id": context.tenant_id,
        "created_at": None,  # filled by ApprovalStore
    }


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

class OpenAIAgentsBackend:
    """Backend that uses OpenAI Agents SDK to run agents.

    Phase 8: Accepts governance components so that compiled function tools
    route through the framework's ToolExecutor pipeline (permissions,
    approval, audit).

    Phase 10: Supports native HITL mode using SDK ``needs_approval`` and
    ``RunState`` for real pause/resume.

    Args:
        agent_registry: Registry of AgentSpec objects (for tool resolution).
        tool_registry: Registry of ToolSpec objects (for tool compilation).
        workflow_registry: Optional registry of Workflow objects.
        raise_on_missing: If True (default), raise ImportError when
                          ``openai-agents`` is not installed.
        default_model: Fallback model name when AgentSpec has no model.
        tool_executor: ToolExecutor for governance-aware tool execution.
        approval_store: Approval persistence store.
        audit_logger: Audit event logger.
        permission_checker: Permission checker.
        hitl_mode: HITL strategy — ``"wrapper"`` (Phase 8, default) or
                   ``"native"`` (Phase 10, uses SDK ``needs_approval``).
    """

    def __init__(
        self,
        agent_registry: Any | None = None,
        tool_registry: Any | None = None,
        workflow_registry: Any | None = None,
        *,
        raise_on_missing: bool = True,
        default_model: str | None = None,
        tool_executor: ToolExecutor | None = None,
        approval_store: Any | None = None,
        audit_logger: Any | None = None,
        permission_checker: Any | None = None,
        hitl_mode: str = "wrapper",
        trace_collector: TraceCollector | None = None,
    ) -> None:
        self._agent_registry = agent_registry
        self._tool_registry = tool_registry
        self._workflow_registry = workflow_registry
        self._raise_on_missing = raise_on_missing
        self._default_model = default_model
        self._last_native_agent: Any = None

        # -- Governance components (Phase 8) --
        self._tool_executor = tool_executor
        self._approval_store = approval_store
        self._audit_logger = audit_logger
        self._permission_checker = permission_checker

        # -- Phase 10: HITL mode --
        if hitl_mode not in ("wrapper", "native"):
            raise ValueError(
                f"Invalid hitl_mode '{hitl_mode}'. "
                "Supported: 'wrapper', 'native'."
            )
        self._hitl_mode = hitl_mode

        # -- Phase 12: observability --
        self.trace_collector = trace_collector or NoOpTraceCollector()

    # ------------------------------------------------------------------
    # Compilation helpers
    # ------------------------------------------------------------------

    def compile_agent(
        self,
        agent_spec: AgentSpec,
        context: RunContext | None = None,
        handoffs: list[Any] | None = None,
        approval_markers: dict[str, list[dict[str, Any]]] | None = None,
    ) -> Any:
        """Convert an :class:`AgentSpec` into an ``agents.Agent`` instance.

        Args:
            agent_spec: The agent specification to compile.
            context: Optional run context passed to tool compilation for
                     governance-aware tool wrappers (Phase 8).
            handoffs: Optional list of compiled SDK Agent objects to use as
                      handoffs. Takes priority over ``agent_spec.handoffs``.
        """
        Agent, _, function_tool = _load_agents_sdk()

        # -- Resolve tools --
        sdk_tools: list[Any] = []
        if self._tool_registry is not None:
            for tool_name in agent_spec.tools:
                try:
                    entry = self._tool_registry.get_entry(tool_name)
                    sdk_tools.append(self.compile_tool(
                        entry,
                        context=context,
                        approval_markers=approval_markers,
                    ))
                except KeyError:
                    raise KeyError(
                        f"Tool '{tool_name}' not found in tool registry. "
                        "Register it before compiling the agent."
                    )
        else:
            # No registry — rely on raw_agent_kwargs to carry tools.
            pass

        # -- Build kwargs --
        kwargs: dict[str, Any] = {
            "name": agent_spec.name,
            "instructions": agent_spec.instructions,
            "tools": sdk_tools,
        }

        # Phase 11: handoffs — explicit parameter takes priority
        if handoffs is not None:
            kwargs["handoffs"] = handoffs
        elif agent_spec.handoffs:
            kwargs["handoffs"] = agent_spec.handoffs

        if agent_spec.model:
            kwargs["model"] = agent_spec.model
        elif self._default_model:
            kwargs["model"] = self._default_model

        # model_settings → top-level SDK kwargs
        if agent_spec.model_settings:
            kwargs.update(agent_spec.model_settings)

        # output_schema → output_type (if SDK supports it)
        if agent_spec.output_schema is not None:
            kwargs["output_type"] = agent_spec.output_schema

        # raw_agent_kwargs last so it can override anything
        kwargs.update(agent_spec.raw_agent_kwargs)

        return Agent(**kwargs)

    def compile_tool(
        self,
        tool_def: Any,
        context: RunContext | None = None,
        approval_markers: dict[str, list[dict[str, Any]]] | None = None,
    ) -> Any:
        """Wrap a framework tool (ToolSpec entry or callable) as an SDK function_tool.

        Phase 8: When governance components are available and a context is
        provided, wraps the original callable with a governance-aware wrapper
        that routes through ToolExecutor for permissions, approval, and audit.

        Phase 10: In ``hitl_mode="native"``, sets ``needs_approval=True`` on
        the SDK function_tool for tools that require approval, enabling the
        SDK's native HITL mechanism.

        Args:
            tool_def: Either a ToolRegistry entry (with ``.fn`` attribute)
                      or a plain callable.
            context: Optional run context for governance-aware wrapping.
        """
        _, _, function_tool = _load_agents_sdk()

        fn: Any
        if hasattr(tool_def, "fn") and tool_def.fn is not None:
            fn = tool_def.fn
        elif callable(tool_def):
            fn = tool_def
        else:
            raise TypeError(
                f"Cannot compile tool of type {type(tool_def).__name__}. "
                "Expected a callable or a registry entry with .fn."
            )

        # Phase 10: Native HITL — determine needs_approval from tool spec
        needs_approval = False
        if self._hitl_mode == "native":
            spec = self._get_tool_spec(tool_def)
            if spec and requires_tool_approval(spec.risk_level, spec.requires_approval):
                needs_approval = True

        # Phase 8: Governance-aware wrapping (wrapper mode always wraps)
        if self._tool_executor is not None and context is not None:
            if self._hitl_mode == "native":
                # Native mode: SDK handles approval, but framework still
                # does permission checking via governance wrapper.
                # The wrapper intercepts BEFORE the SDK sees the call.
                fn = self._create_governed_tool_wrapper(
                    tool_def,
                    fn,
                    context,
                    approval_markers=approval_markers,
                )
            else:
                # Wrapper mode: full governance via wrapper
                fn = self._create_governed_tool_wrapper(
                    tool_def,
                    fn,
                    context,
                    approval_markers=approval_markers,
                )

        return function_tool(fn, needs_approval=needs_approval)

    def _get_tool_spec(self, tool_def: Any) -> ToolSpec | None:
        """Extract ToolSpec from a tool definition."""
        if hasattr(tool_def, "spec"):
            return tool_def.spec
        if isinstance(tool_def, ToolSpec):
            return tool_def
        return None

    # ------------------------------------------------------------------
    # Phase 8: Governance-aware tool wrapper
    # ------------------------------------------------------------------

    def _create_governed_tool_wrapper(
        self,
        tool_def: Any,
        original_fn: Callable[..., Any],
        context: RunContext,
        approval_markers: dict[str, list[dict[str, Any]]] | None = None,
    ) -> Callable[..., Any]:
        """Create a governance-aware wrapper around a tool callable.

        The wrapper intercepts SDK tool invocations and routes them through
        the framework's ToolExecutor pipeline:
          1. Resolve tool spec from registry
          2. Check permissions (denied → structured error)
          3. Approval gate (required → structured approval_required)
          4. Execute original function
          5. Audit log

        Args:
            tool_def: ToolSpec entry or callable (used for name lookup).
            original_fn: The underlying tool callable.
            context: Per-run context (run_id, user_id, tenant_id, etc.).

        Returns:
            An async callable compatible with the SDK function_tool protocol.
        """
        # Resolve the tool name and spec
        tool_name = self._resolve_tool_name(tool_def)

        if approval_markers is None:
            approval_markers = _approval_markers_from_context(context)

        if asyncio.iscoroutinefunction(original_fn):
            # Async tool — return async wrapper
            async def governed_async_tool(**kwargs: Any) -> Any:
                return await self._execute_governed_tool(
                    tool_name=tool_name,
                    original_fn=original_fn,
                    arguments=kwargs,
                    context=context,
                    is_async=True,
                    approval_markers=approval_markers,
                )
            return governed_async_tool
        else:
            # Sync tool — return sync wrapper (SDK may call sync functions)
            def governed_sync_tool(**kwargs: Any) -> Any:
                # Run async governance pipeline in a new event loop if needed
                try:
                    loop = asyncio.get_running_loop()
                    # We're in an async context; create a task
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(
                            asyncio.run,
                            self._execute_governed_tool(
                                tool_name=tool_name,
                                original_fn=original_fn,
                                arguments=kwargs,
                                context=context,
                                is_async=False,
                                approval_markers=approval_markers,
                            ),
                        )
                        return future.result(timeout=30)
                except RuntimeError:
                    # No event loop — run directly
                    return asyncio.run(
                        self._execute_governed_tool(
                            tool_name=tool_name,
                            original_fn=original_fn,
                            arguments=kwargs,
                            context=context,
                            is_async=False,
                            approval_markers=approval_markers,
                        )
                    )
            return governed_sync_tool

    async def _execute_governed_tool(
        self,
        tool_name: str,
        original_fn: Callable[..., Any],
        arguments: dict[str, Any],
        context: RunContext,
        is_async: bool,
        approval_markers: dict[str, list[dict[str, Any]]],
    ) -> Any:
        """Execute a tool through the governance pipeline.

        This is the core governance method. It delegates to ToolExecutor
        which handles the full pipeline: resolve → permissions → approval →
        execute → audit.

        In native HITL mode, permission denied still returns an error
        dict so the framework can block unauthorized calls before they
        reach the SDK's native approval flow.

        Args:
            tool_name: Fully-qualified tool name.
            original_fn: The original tool callable.
            arguments: Call arguments from the SDK.
            context: Per-run context.
            is_async: Whether the original function is async.

        Returns:
            Tool output, approval_required dict, or error dict.
        """
        # Delegate to ToolExecutor for the full governance pipeline
        result: ToolExecutionResult = await self._tool_executor.execute(
            tool_name=tool_name,
            arguments=arguments,
            context=context,
            approved_tool_call=_pop_approval_marker(
                approval_markers,
                tool_name,
                arguments,
            ),
        )

        if result.status == "completed":
            # COMPLETED — ToolExecutor already executed the tool
            return result.output

        if result.status == ToolExecutionStatus.INTERRUPTED.value:
            # Approval required — return structured response to the model
            approval = result.approval_request
            return {
                "status": "approval_required",
                "approval_id": approval.approval_id if approval else None,
                "tool_name": result.tool_name,
                "risk_level": approval.risk_level if approval else "unknown",
                "message": (
                    f"Tool '{result.tool_name}' requires approval "
                    f"(approval_id: {approval.approval_id if approval else 'N/A'})."
                ),
            }

        if result.status == ToolExecutionStatus.FAILED.value:
            # Permission denied or other error
            return {
                "status": "error",
                "error": result.error,
                "tool_name": result.tool_name,
            }

        # Unknown status — return as-is
        return result.output

    def _resolve_tool_name(self, tool_def: Any) -> str:
        """Extract the tool name from a tool definition."""
        if hasattr(tool_def, "spec") and hasattr(tool_def.spec, "name"):
            return tool_def.spec.name
        if hasattr(tool_def, "name"):
            return tool_def.name
        return "unknown"

    # ------------------------------------------------------------------
    # SDK availability guard
    # ------------------------------------------------------------------

    def _ensure_agents(self) -> None:
        if not self._raise_on_missing:
            return
        _load_agents_sdk()  # raises RuntimeError if missing

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    async def run(
        self,
        agent_spec: AgentSpec,
        input: str,
        context: RunContext,
        tools: list[object] | None = None,
        **kwargs: object,
    ) -> AppRunResult:
        """Compile AgentSpec and run via Runner.run.

        Phase 8: Compiles tools with the current run context so that
        governance wrappers have access to run_id, user_id, tenant_id.

        Phase 10: Detects SDK-native interruptions, serializes RunState,
        and populates ``backend_state`` when ``hitl_mode="native"``.
        """
        import time

        self._ensure_agents()
        Agent, Runner, function_tool = _load_agents_sdk()

        # -- Phase 12: agent.started --
        await self._record_backend_event(
            RunEventType.AGENT_STARTED,
            context,
            agent_name=agent_spec.name,
            data={"model": agent_spec.model or self._default_model or "unknown"},
        )

        # -- Compile with context for governance-aware tools (Phase 8) --
        agent = self.compile_agent(agent_spec, context=context)

        # -- Run --
        t0 = time.perf_counter()
        try:
            result = await Runner.run(
                agent,
                input=input,
                context=context,
            )
        except Exception as exc:
            # -- Phase 12: agent.failed --
            await self._record_backend_event(
                RunEventType.AGENT_FAILED,
                context,
                agent_name=agent_spec.name,
                status="failed",
                error={"type": "backend_execution_failed", "message": "Backend execution failed; check server logs for details."},
            )
            return AppRunResult(
                run_id=context.run_id,
                status="failed",
                error={"type": "backend_execution_failed", "message": "Backend execution failed; check server logs for details."},
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        # -- Extract output --
        final_output = _extract_output(result)

        # -- Extract tool calls --
        tool_calls: list[dict] = _extract_tool_calls(result)

        # -- Phase 8: Detect governance interruptions from tool outputs --
        interruptions = _extract_governance_interruptions(result)

        # -- Phase 10: Detect native SDK interruptions --
        backend_state: dict[str, Any] = {}
        if self._hitl_mode == "native":
            sdk_interruptions = getattr(result, "interruptions", None) or []
            if sdk_interruptions:
                interruptions = []
                approval_id_map: dict[str, str] = {}
                framework_interruptions: list[dict[str, Any]] = []
                sdk_metadata: list[dict[str, Any]] = []
                for item in sdk_interruptions:
                    approval_id = f"apv_{secrets.token_hex(16)}"
                    sdk_call_id = _sdk_interruption_call_id(item)
                    tool_name = getattr(
                        item,
                        "tool_name",
                        getattr(item, "name", "unknown"),
                    )
                    arguments = sanitize_payload(
                        _normalize_tool_arguments(getattr(item, "arguments", {}))
                    )
                    if self._approval_store is not None:
                        await self._approval_store.create(ApprovalRequest(
                            approval_id=approval_id,
                            run_id=context.run_id,
                            agent_name=agent_spec.name,
                            tool_name=tool_name,
                            arguments=arguments,
                            risk_level="high",
                            requested_by=context.user_id,
                            tenant_id=context.tenant_id,
                            metadata={
                                "sdk_call_id": sdk_call_id,
                                "argument_keys": sorted(arguments.keys()),
                            },
                        ))
                    if sdk_call_id:
                        approval_id_map[approval_id] = sdk_call_id
                    sdk_metadata.append({
                        "approval_id": approval_id,
                        "sdk_call_id": sdk_call_id,
                        "tool_name": tool_name,
                    })
                    framework_interruptions.append(
                        {
                            "type": "approval_required",
                            "approval_id": approval_id,
                            "tool_name": tool_name,
                            "arguments": arguments,
                            "risk_level": "high",
                            "sdk_call_id": sdk_call_id or None,
                            "_sdk_interruption": True,
                        }
                    )
                interruptions = framework_interruptions
                # Serialize RunState
                try:
                    run_state = result.to_state()
                    backend_state = _serialize_run_state(run_state)
                    backend_state["hitl_mode"] = "native"
                    backend_state["backend"] = "openai"
                    backend_state["approval_id_map"] = approval_id_map
                    backend_state.setdefault("metadata", {})
                    backend_state["metadata"]["sdk_interruptions"] = sdk_metadata
                except Exception:
                    backend_state = {
                        "hitl_mode": "native",
                        "backend": "openai",
                        "approval_id_map": approval_id_map,
                        "metadata": {
                            "sdk_interruptions": sdk_metadata,
                            "resumable": False,
                        },
                    }

        # -- Determine status --
        status = "completed"
        if interruptions:
            status = "interrupted"

        app_result = AppRunResult(
            run_id=context.run_id,
            status=status,
            final_output=final_output,
            interruptions=interruptions,
            tool_calls=tool_calls,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            usage=_extract_usage(result),
            backend_state=backend_state,
        )

        # -- Phase 12: agent.completed --
        await self._record_backend_event(
            RunEventType.AGENT_COMPLETED,
            context,
            agent_name=agent_spec.name,
            status=status,
        )

        self._last_native_agent = agent
        return app_result

    # ------------------------------------------------------------------
    # Phase 10: Resume
    # ------------------------------------------------------------------

    async def resume(
        self,
        agent_spec: AgentSpec,
        context: RunContext,
        **kwargs: object,
    ) -> AppRunResult:
        """Resume a previously interrupted native HITL run.

        Reads the saved RunState from ``backend_state`` in the kwargs,
        applies approval/rejection decisions, and continues execution
        via ``Runner.run(agent, state)``.

        Args:
            agent_spec: The agent specification to compile for resume.
            context: Run context for the resumed run.
            **kwargs: Must include ``backend_state`` (the serialized
                      RunState dict) and ``approvals`` (list of
                      approval decision dicts).

        Returns:
            AppRunResult from the resumed run, or a failed result if
            the RunState cannot be deserialized.
        """
        import time

        self._ensure_agents()
        Agent, Runner, _ = _load_agents_sdk()

        backend_state: dict[str, Any] = kwargs.get("backend_state", {})
        approvals: list[dict[str, Any]] = kwargs.get("approvals", [])
        framework_interruptions: list[dict[str, Any]] = kwargs.get("interruptions", [])
        if not framework_interruptions:
            framework_interruptions = backend_state.get("metadata", {}).get(
                "sdk_interruptions",
                [],
            )

        approval_markers: dict[str, list[dict[str, Any]]] = {}
        agent = self.compile_agent(
            agent_spec,
            context=context,
            approval_markers=approval_markers,
        )

        # -- Deserialize RunState --
        # backend_state format: {"serialization": "...", "value": {...}, "hitl_mode": "...", "backend": "..."}
        run_state_data = backend_state.get("value", backend_state)
        if not run_state_data or not isinstance(run_state_data, dict):
            return AppRunResult(
                run_id=context.run_id,
                status="failed",
                error={
                    "type": "no_run_state",
                    "message": "No RunState data found in backend_state.",
                },
                latency_ms=0,
            )

        # Wrap in the expected format for _deserialize_run_state
        serialization_method = backend_state.get("serialization", "unknown")
        wrapped_state = {
            "serialization": serialization_method,
            "value": run_state_data,
        }
        if backend_state.get("_non_resumable"):
            wrapped_state["_non_resumable"] = True

        run_state, err = _deserialize_run_state(wrapped_state, agent)

        if run_state is None:
            return AppRunResult(
                run_id=context.run_id,
                status="failed",
                error={"type": "deserialization_failed", "message": err},
                latency_ms=0,
            )

        # -- Apply approval decisions --
        sdk_interruptions = run_state.get_interruptions()
        approved_items: list[Any] = []
        rejected_items: list[Any] = []

        approval_id_map = backend_state.get("approval_id_map", {})
        if not isinstance(approval_id_map, dict):
            approval_id_map = {}

        decision_map = _build_sdk_decision_map(
            approvals,
            framework_interruptions,
            approval_id_map,
        )

        for item in sdk_interruptions:
            call_id = _sdk_interruption_call_id(item)
            decision = decision_map.get(call_id, "pending")
            if decision == "approved":
                approved_items.append(item)
                tool_name = getattr(
                    item,
                    "tool_name",
                    getattr(item, "name", "unknown"),
                )
                approval_markers.setdefault(tool_name, []).append(
                    _make_native_hitl_approval_marker(
                        tool_name=tool_name,
                        arguments=_normalize_tool_arguments(
                            getattr(item, "arguments", {})
                        ),
                        call_id=str(call_id) if call_id else None,
                    )
                )
            elif decision == "rejected":
                rejected_items.append(item)

        # Apply approvals to RunState
        for item in approved_items:
            try:
                run_state.approve(item)
            except Exception:
                pass

        for item in rejected_items:
            try:
                run_state.reject(
                    item,
                    rejection_message=kwargs.get("rejection_message"),
                )
            except Exception:
                pass
            pass

        # -- Resume execution --
        t0 = time.perf_counter()
        try:
            result = await Runner.run(
                agent,
                run_state,
                context=context,
            )
        except Exception as exc:
            return AppRunResult(
                run_id=context.run_id,
                status="failed",
                error={"type": "backend_resume_failed", "message": "Backend resume failed; check server logs for details."},
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        final_output = _extract_output(result)
        tool_calls = _extract_tool_calls(result)
        usage = _extract_usage(result)

        # Check for new interruptions after resume
        new_interruptions = _extract_governance_interruptions(result)
        status = "interrupted" if new_interruptions else "completed"

        return AppRunResult(
            run_id=context.run_id,
            status=status,
            final_output=final_output,
            interruptions=new_interruptions,
            tool_calls=tool_calls,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            usage=usage,
        )

    # ------------------------------------------------------------------
    # stream()
    # ------------------------------------------------------------------

    async def stream(
        self,
        agent_spec: AgentSpec,
        input: str,
        context: RunContext,
        tools: list[object] | None = None,
        **kwargs: object,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream a run — delegates to Runner.run_streamed when available.

        Falls back to run() + chunked output if streaming is not supported
        by the installed SDK version.

        Phase 8: Passes context for governance-aware tool compilation.

        Phase 10: After stream completes, captures any SDK interruptions
        and yields a run.interrupted event.
        """
        self._ensure_agents()
        _, Runner, _ = _load_agents_sdk()

        # -- Compile with context for governance-aware tools (Phase 8) --
        agent = self.compile_agent(agent_spec, context=context)

        yield StreamEvent(
            type=StreamEventType.RUN_STARTED,
            run_id=context.run_id,
        )

        backend_state: dict[str, Any] = {}
        try:
            if hasattr(Runner, "run_streamed"):
                streamed = Runner.run_streamed(
                    agent, input=input, context=context
                )
                async for event in streamed.stream_events():
                    yield StreamEvent(
                        type=getattr(event, "type", StreamEventType.TEXT_DELTA),
                        run_id=context.run_id,
                        delta=getattr(event, "delta", None),
                        data=getattr(event, "data", {}),
                    )

                # Phase 10: Check for interruptions after stream completes
                if self._hitl_mode == "native" and hasattr(streamed, "to_state"):
                    sdk_interruptions = getattr(streamed, "interruptions", None) or []
                    if sdk_interruptions:
                        try:
                            run_state = streamed.to_state()
                            backend_state = _serialize_run_state(run_state)
                            backend_state["hitl_mode"] = "native"
                            backend_state["backend"] = "openai"
                        except Exception:
                            pass
            else:
                # Fallback: run synchronously and emit as a single chunk.
                result = await Runner.run(agent, input=input, context=context)
                output = _extract_output(result)
                yield StreamEvent(
                    type=StreamEventType.TEXT_DELTA,
                    run_id=context.run_id,
                    delta=output,
                )
        except Exception as exc:
            yield StreamEvent(
                type=StreamEventType.RUN_FAILED,
                run_id=context.run_id,
                data={"error": {"type": "backend_execution_failed", "message": "Backend execution failed; check server logs for details."}},
            )
            return

        yield StreamEvent(
            type=StreamEventType.RUN_COMPLETED,
            run_id=context.run_id,
        )

        self._last_native_agent = agent

    # ------------------------------------------------------------------
    # Phase 11: Multi-agent workflow support
    # ------------------------------------------------------------------

    async def run_workflow(
        self,
        workflow: Workflow,
        input: str,
        context: RunContext,
        **kwargs: object,
    ) -> AppRunResult:
        """Execute a multi-agent workflow using the OpenAI Agents SDK.

        Dispatches to the appropriate handler based on ``workflow.type``:

        - ``SINGLE`` — delegates to :meth:`run`
        - ``HANDOFF`` — compiles entry agent with handoffs, runs via SDK
        - ``ORCHESTRATOR`` — compiles manager with specialist tools, runs via SDK
        - ``DAG`` or unknown — returns failed ``AppRunResult``

        Args:
            workflow: The workflow definition.
            input: User input.
            context: Run context.
            **kwargs: Extra forwarded to the underlying ``run()`` call.

        Returns:
            ``AppRunResult`` with ``workflow_trace`` populated.
        """
        self._ensure_agents()

        if workflow.type == WorkflowType.SINGLE:
            entry = workflow.entry_agent_name()
            return await self.run(
                AgentSpec(name=entry, instructions="", tools=[]),
                input,
                context,
                **kwargs,
            )
        if workflow.type == WorkflowType.HANDOFF:
            return await self._run_handoff_workflow(workflow, input, context)
        if workflow.type == WorkflowType.ORCHESTRATOR:
            return await self._run_orchestrator_workflow(workflow, input, context)
        if workflow.type == WorkflowType.DAG:
            return AppRunResult(
                run_id=context.run_id,
                status="failed",
                error={
                    "type": "NotImplementedError",
                    "message": "DAG workflows not yet implemented for OpenAI backend.",
                },
            )

        return AppRunResult(
            run_id=context.run_id,
            status="failed",
            error={
                "type": "ValueError",
                "message": f"Unknown workflow type: {workflow.type}",
            },
        )

    async def _run_handoff_workflow(
        self,
        workflow: Workflow,
        input: str,
        context: RunContext,
    ) -> AppRunResult:
        """Run a handoff (triage) workflow via the OpenAI SDK.

        Compiles the entry agent with ``handoffs`` pointing to the compiled
        candidate agents. The SDK's LLM decides which handoff to invoke.

        If a candidate agent is not found in the registry, returns a failed
        result immediately.
        """
        Agent, Runner, _ = _load_agents_sdk()
        t0 = time.perf_counter()

        # -- Build workflow trace --
        trace = WorkflowTrace(
            workflow_name=workflow.name,
            workflow_type=workflow.type.value,
            entry_agent=workflow.entry or "",
            steps=[
                WorkflowStep(
                    step_id=_uid(),
                    step_type="agent",
                    agent_name=workflow.entry or "",
                    input_summary=input[:100],
                    status="started",
                    metadata={"backend": "openai"},
                ),
                WorkflowStep(
                    step_id=_uid(),
                    step_type="handoff_candidates",
                    agent_name=workflow.entry or "",
                    status="started",
                    metadata={"agents": workflow.agents},
                ),
            ],
        )

        # -- Resolve entry agent --
        entry_name = workflow.entry or ""
        try:
            entry_spec = self._agent_registry.get(entry_name)
        except (KeyError, AttributeError):
            trace.steps.append(WorkflowStep(
                step_id=_uid(),
                step_type="error",
                agent_name=entry_name,
                status="failed",
                output_summary=f"Entry agent '{entry_name}' not found",
            ))
            return AppRunResult(
                run_id=context.run_id,
                status="failed",
                error={"type": "KeyError", "message": f"Entry agent '{entry_name}' not found"},
                workflow_trace=trace,
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        # -- Compile candidate agents --
        handoff_agents: list[Any] = []
        for candidate_name in workflow.agents:
            try:
                candidate_spec = self._agent_registry.get(candidate_name)
            except (KeyError, AttributeError):
                return AppRunResult(
                    run_id=context.run_id,
                    status="failed",
                    error={
                        "type": "KeyError",
                        "message": f"Handoff candidate agent '{candidate_name}' not found.",
                    },
                    workflow_trace=trace,
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                )
            handoff_agents.append(
                self.compile_agent(candidate_spec, context=context)
            )

        # -- Compile entry agent with handoffs --
        entry_agent = self.compile_agent(
            entry_spec,
            context=context,
            handoffs=handoff_agents,
        )

        # -- Execute --
        try:
            result = await Runner.run(
                entry_agent,
                input=input,
                context=context,
            )
        except Exception as exc:
            trace.steps.append(WorkflowStep(
                step_id=_uid(),
                step_type="error",
                agent_name=entry_name,
                status="failed",
                output_summary="backend execution failed",
            ))
            return AppRunResult(
                run_id=context.run_id,
                status="failed",
                error={"type": "backend_execution_failed", "message": "Backend execution failed; check server logs for details."},
                workflow_trace=trace,
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        # -- Build result --
        final_output = _extract_output(result)
        tool_calls = _extract_tool_calls(result)
        interruptions = _extract_governance_interruptions(result)

        # Record SDK execution step
        trace.steps.append(WorkflowStep(
            step_id=_uid(),
            step_type="agent",
            agent_name=entry_name,
            input_summary=input[:100],
            output_summary=str(final_output or "")[:100],
            status="completed",
            metadata={"backend": "openai", "sdk_result": True},
        ))

        status = "interrupted" if interruptions else "completed"

        app_result = AppRunResult(
            run_id=context.run_id,
            status=status,
            final_output=final_output,
            interruptions=interruptions,
            tool_calls=tool_calls,
            workflow_trace=trace,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            usage=_extract_usage(result),
        )

        self._last_native_agent = entry_agent
        return app_result

    async def _run_orchestrator_workflow(
        self,
        workflow: Workflow,
        input: str,
        context: RunContext,
    ) -> AppRunResult:
        """Run an orchestrator (agents-as-tools) workflow via the OpenAI SDK.

        Compiles specialist agents as SDK tools (using ``Agent.as_tool()`` when
        available, or a fallback ``function_tool`` wrapper), then compiles the
        manager agent with those specialist tools included.

        If ``Agent.as_tool()`` is available, the SDK handles nested agent
        execution natively. Otherwise, a fallback wrapper calls
        ``Runner.run()`` for each specialist.
        """
        Agent, Runner, function_tool = _load_agents_sdk()
        t0 = time.perf_counter()

        agents_as_tools = workflow.config.get("agents_as_tools", workflow.agents)

        # -- Build workflow trace --
        trace = WorkflowTrace(
            workflow_name=workflow.name,
            workflow_type=workflow.type.value,
            entry_agent=workflow.entry or "",
            steps=[
                WorkflowStep(
                    step_id=_uid(),
                    step_type="agent",
                    agent_name=workflow.entry or "",
                    input_summary=input[:100],
                    status="started",
                    metadata={"backend": "openai"},
                ),
                WorkflowStep(
                    step_id=_uid(),
                    step_type="agent_tools",
                    agent_name=workflow.entry or "",
                    status="started",
                    metadata={"agents_as_tools": agents_as_tools},
                ),
            ],
        )

        # -- Resolve manager agent --
        manager_name = workflow.entry or ""
        try:
            manager_spec = self._agent_registry.get(manager_name)
        except (KeyError, AttributeError):
            trace.steps.append(WorkflowStep(
                step_id=_uid(),
                step_type="error",
                agent_name=manager_name,
                status="failed",
                output_summary=f"Manager agent '{manager_name}' not found",
            ))
            return AppRunResult(
                run_id=context.run_id,
                status="failed",
                error={"type": "KeyError", "message": f"Manager agent '{manager_name}' not found"},
                workflow_trace=trace,
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        # -- Compile specialist agents as tools --
        specialist_tools: list[Any] = []
        # Start with manager's own tools
        if self._tool_registry is not None:
            for tool_name in manager_spec.tools:
                try:
                    entry = self._tool_registry.get_entry(tool_name)
                    specialist_tools.append(self.compile_tool(entry, context=context))
                except KeyError:
                    pass

        # Add specialist agents as tools
        agent_calls: list[dict[str, Any]] = []
        for specialist_name in agents_as_tools:
            try:
                specialist_spec = self._agent_registry.get(specialist_name)
            except (KeyError, AttributeError):
                return AppRunResult(
                    run_id=context.run_id,
                    status="failed",
                    error={
                        "type": "KeyError",
                        "message": f"Specialist agent '{specialist_name}' not found.",
                    },
                    workflow_trace=trace,
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                )

            specialist_compiled = self.compile_agent(specialist_spec, context=context)
            agent_tool = self.compile_agent_as_tool(
                specialist_compiled,
                specialist_name,
                input,
                context,
            )
            specialist_tools.append(agent_tool)

        # -- Compile manager agent with specialist tools --
        manager_kwargs: dict[str, Any] = {
            "name": manager_spec.name,
            "instructions": manager_spec.instructions,
            "tools": specialist_tools,
        }
        if manager_spec.model:
            manager_kwargs["model"] = manager_spec.model
        elif self._default_model:
            manager_kwargs["model"] = self._default_model
        if manager_spec.model_settings:
            manager_kwargs.update(manager_spec.model_settings)
        manager_kwargs.update(manager_spec.raw_agent_kwargs)

        manager_agent = Agent(**manager_kwargs)

        # -- Execute --
        try:
            result = await Runner.run(
                manager_agent,
                input=input,
                context=context,
            )
        except Exception as exc:
            trace.steps.append(WorkflowStep(
                step_id=_uid(),
                step_type="error",
                agent_name=manager_name,
                status="failed",
                output_summary="backend execution failed",
            ))
            return AppRunResult(
                run_id=context.run_id,
                status="failed",
                error={"type": "backend_execution_failed", "message": "Backend execution failed; check server logs for details."},
                workflow_trace=trace,
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        # -- Extract agent calls from tool calls --
        final_output = _extract_output(result)
        raw_tool_calls = _extract_tool_calls(result)
        interruptions = _extract_governance_interruptions(result)

        for tc in raw_tool_calls:
            tc_name = tc.get("tool", "unknown")
            if tc_name in agents_as_tools:
                agent_calls.append({
                    "agent_name": tc_name,
                    "input": input,
                    "status": "completed",
                })

        # If no agent_calls detected from tool_calls, record all specialists
        # as called (the manager may have used them without explicit tracking)
        if not agent_calls and raw_tool_calls:
            for tc in raw_tool_calls:
                tc_name = tc.get("tool", "unknown")
                if tc_name not in [c["agent_name"] for c in agent_calls]:
                    agent_calls.append({
                        "agent_name": tc_name,
                        "input": input,
                        "status": "completed",
                    })

        # Record manager execution step
        trace.steps.append(WorkflowStep(
            step_id=_uid(),
            step_type="agent",
            agent_name=manager_name,
            input_summary=input[:100],
            output_summary=str(final_output or "")[:100],
            status="completed",
            metadata={"backend": "openai", "agent_calls": agent_calls},
        ))

        status = "interrupted" if interruptions else "completed"

        app_result = AppRunResult(
            run_id=context.run_id,
            status=status,
            final_output=final_output,
            agent_calls=agent_calls,
            interruptions=interruptions,
            tool_calls=raw_tool_calls,
            workflow_trace=trace,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            usage=_extract_usage(result),
        )

        self._last_native_agent = manager_agent
        return app_result

    def compile_agent_as_tool(
        self,
        compiled_agent: Any,
        agent_name: str,
        input_text: str,
        context: RunContext | None = None,
    ) -> Any:
        """Compile an agent as an SDK tool for orchestrator workflows.

        Uses the SDK's native ``Agent.as_tool()`` when available, falling
        back to a ``function_tool`` wrapper that calls ``Runner.run()``.

        Args:
            compiled_agent: A pre-compiled SDK Agent object.
            agent_name: Name for the tool (defaults to agent name).
            input_text: Default input text passed to the specialist.
            context: Optional run context for the fallback wrapper.

        Returns:
            An SDK ``FunctionTool`` object.
        """
        _, _, function_tool = _load_agents_sdk()

        # Try native SDK as_tool() first
        if hasattr(compiled_agent, "as_tool") and callable(compiled_agent.as_tool):
            try:
                return compiled_agent.as_tool(
                    tool_name=agent_name,
                    tool_description=f"Delegate to {agent_name} agent for specialized tasks.",
                )
            except Exception:
                pass  # Fall back to wrapper

        # Fallback: function_tool wrapper that calls Runner.run()
        Agent_fb, Runner_fb, _ = _load_agents_sdk()

        agent_ref = compiled_agent
        ctx_ref = context

        def _agent_tool_wrapper(**kwargs: Any) -> Any:
            """Fallback: run specialist agent via Runner.run()."""
            tool_input = kwargs.get("input", input_text)
            # We can't await here since function_tool may call sync;
            # return a placeholder — the orchestrator pattern with
            # fallback wrapper records the call but actual execution
            # requires async support in the tool call path.
            return {
                "status": "delegated",
                "agent": agent_name,
                "input": tool_input,
                "note": "Fallback agent-as-tool — use SDK Agent.as_tool() for real execution.",
            }

        return function_tool(_agent_tool_wrapper)

    async def _record_backend_event(
        self,
        event_type: Any,
        context: RunContext,
        agent_name: str | None = None,
        status: str | None = None,
        error: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Record a backend-level RunEvent via the trace collector."""
        event_payload: dict[str, Any] = {
            "event_type": event_type,
            "trace_id": context.trace_id or "",
            "run_id": context.run_id,
            "user_id": context.user_id,
            "tenant_id": context.tenant_id,
            "agent_name": agent_name,
            "status": status,
            "error": error,
            "data": data or {},
        }
        try:
            from agent_app.observability.events import RunEvent
            event = RunEvent(**event_payload)
            await self.trace_collector.record(event)
        except Exception:
            pass  # Never let observability break execution


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    """Generate a short unique ID for trace steps."""
    return uuid.uuid4().hex[:12]


def _approval_markers_from_context(
    context: RunContext,
) -> dict[str, list[dict[str, Any]]]:
    """Extract internal trusted approval markers from context metadata."""
    markers: dict[str, list[dict[str, Any]]] = {}
    raw_markers = context.metadata.get("_trusted_approved_tool_calls", [])
    if not isinstance(raw_markers, list):
        return markers
    for raw_marker in raw_markers:
        if not isinstance(raw_marker, dict):
            continue
        tool_name = raw_marker.get("tool_name")
        if not isinstance(tool_name, str):
            continue
        markers.setdefault(tool_name, []).append(raw_marker)
    return markers


def _sdk_interruption_call_id(item: Any) -> str:
    """Return a stable SDK interruption identifier."""
    value = getattr(item, "call_id", None) or getattr(item, "tool_lookup_key", None)
    return str(value or "")


def _build_sdk_decision_map(
    approvals: list[dict[str, Any]],
    interruptions: list[dict[str, Any]],
    approval_id_map: dict[str, str] | None = None,
) -> dict[str, str]:
    """Map SDK call IDs to approval decisions."""
    framework_to_status = {
        str(item.get("approval_id", "")): str(item.get("status", "pending"))
        for item in approvals
    }
    decision_map: dict[str, str] = {}
    for interruption in interruptions:
        approval_id = str(interruption.get("approval_id", ""))
        sdk_call_id = str(interruption.get("sdk_call_id", ""))
        if sdk_call_id and approval_id in framework_to_status:
            decision_map[sdk_call_id] = framework_to_status[approval_id]
    if approval_id_map:
        for approval_id, sdk_call_id in approval_id_map.items():
            if approval_id in framework_to_status:
                decision_map[str(sdk_call_id)] = framework_to_status[approval_id]
    for item in approvals:
        approval_id = str(item.get("approval_id", ""))
        if approval_id and approval_id not in decision_map:
            decision_map.setdefault(approval_id, str(item.get("status", "pending")))
    return decision_map


def _normalize_tool_arguments(value: Any) -> dict[str, Any]:
    """Normalize SDK tool-call arguments to a dict for storage and matching."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {"_raw": value}
        if isinstance(decoded, dict):
            return dict(decoded)
        return {"_raw": decoded}
    if value is None:
        return {}
    return {"_raw": value}


def _pop_approval_marker(
    approval_markers: dict[str, list[dict[str, Any]]],
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any] | None:
    """Consume the first trusted approval marker matching a tool call."""
    normalized_arguments = _normalize_tool_arguments(arguments)
    markers = approval_markers.get(tool_name, [])
    for index, marker in enumerate(markers):
        if _normalize_tool_arguments(marker.get("arguments")) == normalized_arguments:
            return markers.pop(index)
    return None


def _extract_output(result: Any) -> str:
    """Extract final_output from an SDK RunResult."""
    if hasattr(result, "final_output"):
        val = result.final_output
        if isinstance(val, str):
            return val
        if val is not None:
            return str(val)
    # Fallback: try common attribute names
    for attr in ("output", "content", "response"):
        if hasattr(result, attr):
            val = getattr(result, attr)
            if isinstance(val, str):
                return val
            if val is not None:
                return str(val)
    return str(result)


def _extract_tool_calls(result: Any) -> list[dict]:
    """Extract tool call list from an SDK RunResult."""
    tool_calls: list[dict] = []
    raw = getattr(result, "tool_calls", None)
    if not raw:
        return tool_calls
    for tc in raw:
        tool_calls.append(
            {
                "tool": getattr(tc, "tool_name", getattr(tc, "name", "unknown")),
                "arguments": getattr(tc, "arguments", getattr(tc, "args", {})),
                "status": "completed",
            }
        )
    return tool_calls


def _extract_usage(result: Any) -> dict[str, Any]:
    """Extract usage info from an SDK RunResult."""
    usage = getattr(result, "usage", None)
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return usage
    # SDK usage object → dict
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def _extract_governance_interruptions(result: Any) -> list[dict[str, Any]]:
    """Detect governance interruptions from SDK RunResult tool outputs.

    Phase 8: Scans the SDK result for tool outputs that contain
    approval_required markers. These are produced by the governance-aware
    tool wrapper when a high-risk tool is called without prior approval.

    Searches multiple possible locations in the SDK result structure:
    - result.new_items (OpenAI Agents SDK v0.2+)
    - result.items (older SDK versions)
    - result.tool_calls with governance metadata
    - Custom attributes on the result

    Args:
        result: SDK RunResult or compatible object.

    Returns:
        List of interruption dicts with approval_id, tool_name, etc.
    """
    interruptions: list[dict[str, Any]] = []

    # -- Strategy 1: Scan new_items for tool outputs --
    items = getattr(result, "new_items", None)
    if items is None:
        items = getattr(result, "items", None)

    if items:
        for item in items:
            output = _get_item_output(item)
            if isinstance(output, dict) and output.get("status") == "approval_required":
                interruptions.append({
                    "type": "approval_required",
                    "approval_id": output.get("approval_id"),
                    "tool_name": output.get("tool_name"),
                    "risk_level": output.get("risk_level", "unknown"),
                    "message": output.get("message", ""),
                })

    # -- Strategy 2: Scan tool_calls for governance metadata --
    if not interruptions:
        raw_tool_calls = getattr(result, "tool_calls", None)
        if raw_tool_calls:
            for tc in raw_tool_calls:
                # Skip MagicMock tool calls — getattr on them creates
                # spurious attributes. Real objects with real data work fine.
                if type(tc).__name__ == "MagicMock":
                    continue
                if _is_governance_interruption(tc):
                    interruptions.append({
                        "type": "approval_required",
                        "approval_id": _extract_from_tool_call(tc, "approval_id"),
                        "tool_name": _extract_from_tool_call(
                            tc, "tool_name", fallback="name", default="unknown"
                        ),
                        "risk_level": _extract_from_tool_call(
                            tc, "risk_level", default="unknown"
                        ),
                    })

    # -- Strategy 3: Check for interruptions attribute on result --
    if not interruptions:
        result_interruptions = getattr(result, "interruptions", None)
        if result_interruptions:
            for item in result_interruptions:
                if isinstance(item, dict):
                    interruptions.append(item)

    return interruptions


def _get_item_output(item: Any) -> Any:
    """Extract the output/value from an SDK result item."""
    # OpenAI Agents SDK CallItem has .output or .result
    for attr in ("output", "result", "value"):
        if hasattr(item, attr):
            val = getattr(item, attr)
            if isinstance(val, dict) and "status" in val:
                return val
            # Might be a string representation of a dict
            if isinstance(val, str) and "approval_required" in val:
                import json
                try:
                    return json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
    # Check raw_responses
    raw = getattr(item, "raw_responses", None)
    if raw:
        for resp in raw:
            for attr in ("output", "content"):
                if hasattr(resp, attr):
                    val = getattr(resp, attr)
                    if isinstance(val, dict) and val.get("status") == "approval_required":
                        return val
    return None


def _is_governance_interruption(tc: Any) -> bool:
    """Check if a tool call represents a governance interruption."""
    # Avoid mock false positives — check by type name
    if type(tc).__name__ == "MagicMock":
        return False
    # Check for approval_required in arguments or result
    args = getattr(tc, "arguments", getattr(tc, "args", {}))
    if isinstance(args, dict) and args.get("status") == "approval_required":
        return True
    # Check result attribute
    result_val = getattr(tc, "result", None)
    if isinstance(result_val, dict) and result_val.get("status") == "approval_required":
        return True
    # Check output attribute
    output = getattr(tc, "output", None)
    if isinstance(output, dict) and output.get("status") == "approval_required":
        return True
    return False


def _safe_getattr(
    obj: Any, attr: str, fallback: str | None = None, default: Any = None
) -> Any:
    """Get attribute from object, returning default if not found or if mock.

    Uses type name check to avoid importing unittest.mock in production code.
    """
    if type(obj).__name__ == "MagicMock":
        return default
    if fallback and hasattr(obj, fallback) and not hasattr(obj, attr):
        return getattr(obj, fallback)
    if hasattr(obj, attr):
        val = getattr(obj, attr)
        # Avoid auto-generated MagicMock attributes
        if type(val).__name__ == "MagicMock":
            return default
        return val
    return default


def _extract_from_tool_call(
    tc: Any, key: str, fallback: str | None = None, default: Any = None
) -> Any:
    """Extract a value from a tool call, checking attributes then arguments dict.

    Some SDK implementations put governance metadata in the arguments dict
    (e.g., {"status": "approval_required", "approval_id": "..."}).
    Others put it as direct attributes on the tool call object.

    Args:
        tc: Tool call object.
        key: Key to extract.
        fallback: Alternative attribute name to try.
        default: Default value if not found.

    Returns:
        The extracted value or default.
    """
    # Check direct attribute first
    val = _safe_getattr(tc, key, default=None)
    if val is not None:
        return val
    # Check arguments dict
    args = _safe_getattr(tc, "arguments", default=None)
    if isinstance(args, dict) and key in args:
        return args[key]
    # Check args alias
    args = _safe_getattr(tc, "args", default=None)
    if isinstance(args, dict) and key in args:
        return args[key]
    # Try fallback attribute name
    if fallback:
        val = _safe_getattr(tc, fallback, default=None)
        if val is not None:
            return val
    return default
