"""Assembles the book_publisher example's AgentApp and governance components.

Does not go through agent_app.config.loader.build_app() — that loader always
defaults to DryRunBackend with no supported hook to swap it post-construction
without reaching into AgentApp._runner internals. Instead this constructs
AgentApp directly, with every registry and governance store explicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_app import AgentApp, AgentSpec, Workflow
from agent_app.governance.approval import InMemoryApprovalStore
from agent_app.governance.audit import InMemoryAuditLogger
from agent_app.governance.permission import DefaultPermissionChecker
from agent_app.observability.collector import InMemoryTraceCollector
from agent_app.registry.agent_registry import AgentRegistry
from agent_app.registry.tool_registry import ToolRegistry
from agent_app.registry.workflow_registry import WorkflowRegistry
from agent_app.runtime.tool_executor import ToolExecutor

from book_publisher.mock_backend import MockPersonaBackend
from book_publisher.personas import PersonaRegistry
from book_publisher.platforms import PlatformRegistry
from book_publisher.publishers.mock import MockPublisher
from book_publisher.tools import build_publish_tools

_EXAMPLE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class _RegistryBundle:
    """Duck-types as AgentApp's registry= argument (agent_registry/tool_registry/
    workflow_registry attributes), isolated per build_app() call — NOT the
    process-global default ToolRegistry that AgentApp() falls back to when
    registry= is omitted.
    """

    agent_registry: AgentRegistry
    tool_registry: ToolRegistry
    workflow_registry: WorkflowRegistry


class BookPublisherApp:
    """Bundle returned by build_app(): the AgentApp plus everything main.py needs."""

    def __init__(
        self,
        app: AgentApp,
        tool_executor: ToolExecutor,
        personas: PersonaRegistry,
        platforms: PlatformRegistry,
    ) -> None:
        self.app = app
        self.tool_executor = tool_executor
        self.personas = personas
        self.platforms = platforms


def build_app(
    personas_dir: str | Path | None = None,
    platforms_dir: str | Path | None = None,
    prompt_path: str | Path | None = None,
    log_path: str | Path | None = None,
) -> BookPublisherApp:
    """Load personas/platforms from YAML and assemble the AgentApp + ToolExecutor bundle.

    All directory/path arguments default to the paths shipped alongside this
    example (examples/book_publisher/{personas,platforms,prompts}/); override
    them for tests or alternate persona/platform sets.
    """
    personas_dir = Path(personas_dir) if personas_dir else _EXAMPLE_DIR / "personas"
    platforms_dir = Path(platforms_dir) if platforms_dir else _EXAMPLE_DIR / "platforms"
    prompt_path = (
        Path(prompt_path) if prompt_path else _EXAMPLE_DIR / "prompts" / "book_writer.md"
    )

    personas = PersonaRegistry.load(personas_dir)
    platforms = PlatformRegistry.load(platforms_dir)

    registry = _RegistryBundle(
        agent_registry=AgentRegistry(),
        tool_registry=ToolRegistry(),
        workflow_registry=WorkflowRegistry(),
    )

    approval_store = InMemoryApprovalStore()
    audit_logger = InMemoryAuditLogger()
    trace_collector = InMemoryTraceCollector()

    app = AgentApp(
        registry=registry,
        backend=MockPersonaBackend(),
        approval_store=approval_store,
        audit_logger=audit_logger,
        trace_collector=trace_collector,
    )

    template = prompt_path.read_text(encoding="utf-8")
    dag_nodes: list[dict[str, str]] = []
    for persona in personas.all():
        agent_name = f"book_writer__{persona.name}"
        instructions = template.format(
            tone=persona.tone,
            reading_level=persona.reading_level,
            max_length=persona.max_length,
            extra_instructions=persona.extra_instructions,
        )
        app.register_agent(
            AgentSpec(
                name=agent_name,
                description=f"Writes book descriptions for the {persona.display_name} audience",
                instructions=instructions,
                metadata={
                    "persona_name": persona.name,
                    "tone": persona.tone,
                    "reading_level": persona.reading_level,
                    "max_length": persona.max_length,
                    "extra_instructions": persona.extra_instructions,
                },
            )
        )
        dag_nodes.append({"id": f"write_{persona.name}", "type": "agent", "ref": agent_name})

    # Safe to run in parallel: each persona's write_{name} node is fully
    # independent — no shared mutable state, no cross-persona ordering.
    wf = Workflow.dag(name="book_generation", nodes=dag_nodes, execution_mode="parallel")
    app.register_workflow(wf)

    publisher = MockPublisher(log_path=log_path) if log_path is not None else MockPublisher()
    for spec, fn in build_publish_tools(platforms, publisher):
        app.register_tool(spec, fn=fn)

    tool_executor = ToolExecutor(
        tool_registry=app.tool_registry,
        approval_store=approval_store,
        permission_checker=DefaultPermissionChecker(),
        audit_logger=audit_logger,
        trace_collector=trace_collector,
    )

    return BookPublisherApp(
        app=app, tool_executor=tool_executor, personas=personas, platforms=platforms
    )
