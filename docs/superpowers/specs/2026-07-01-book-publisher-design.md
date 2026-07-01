# Book Publisher — Multi-Persona, Multi-Platform Content Pipeline

**Status:** Approved
**Location:** `examples/book_publisher/`

## Goal

Apply the existing Agent App Framework to a concrete scenario: given structured
book content, generate audience-tailored content variants (children, adult,
student, teacher, ... — extensible) and push each variant to a set of
downstream publishing platforms (WeChat official account, Zhihu, Juejin,
CSDN, ... — extensible), in the spirit of the
[ArtiPub](https://github.com/artipub/artipub) "one article, many platforms"
workflow. This is a new example app, not a framework core change.

## Non-goals

- No PDF/EPUB parsing — book input is structured (YAML/dict), not raw files.
- No real platform API/credential integration — publishing is mocked.
- No real LLM calls — content generation is a deterministic template backend.
  Swapping in a real backend (e.g. a local LMStudio OpenAI-compatible server)
  is a one-line `backend=` change, documented but not implemented here.
- No changes to `agent_app/` core. Everything lives in `examples/book_publisher/`.
- No Policy Engine integration — per-platform risk level is a static field in
  that platform's YAML, which is sufficient to demonstrate the approval gate.

## Directory layout

Follows the existing `examples/research_assistant` / `examples/customer_support`
convention (`agentapp.yaml`, `main.py`, `prompts/`, `evals/`, `README.md`).

```
examples/book_publisher/
├── README.md
├── agentapp.yaml
├── main.py
├── data/
│   └── sample_book.yaml
├── personas/
│   ├── children.yaml
│   ├── adult.yaml
│   ├── student.yaml
│   └── teacher.yaml
├── platforms/
│   ├── wechat_mp.yaml
│   ├── zhihu.yaml
│   ├── juejin.yaml
│   └── csdn.yaml
├── prompts/
│   └── book_writer.md
└── book_publisher/                 # importable package
    ├── __init__.py
    ├── models.py
    ├── personas.py
    ├── platforms.py
    ├── mock_backend.py
    ├── publishers/
    │   ├── __init__.py
    │   ├── base.py
    │   └── mock.py
    ├── tools.py
    ├── pipeline.py
    └── build_app.py

evals/book_publisher.yaml               # under examples/book_publisher/evals/
tests/unit/test_book_publisher_*.py     # under repo root tests/, matching convention
```

## Data models (`book_publisher/models.py`)

Pydantic models, mirroring the style of `agent_app/core/*`:

- `BookInput`: `title: str`, `summary: str`, `key_points: list[str]`,
  `tags: list[str] = []`. Loaded from `data/sample_book.yaml`.
  `to_prompt_text() -> str` renders a plain-text brief used as agent input.
- `PersonaSpec`: `name: str`, `display_name: str`, `tone: str`,
  `reading_level: str`, `max_length: int`, `extra_instructions: str = ""`,
  `target_platforms: list[str] | None = None` (`None` = all registered
  platforms).
- `PlatformSpec`: `name: str`, `display_name: str`, `max_length: int | None`,
  `format: str` ("markdown" | "plain"), `hashtag_style: str = ""`,
  `risk_level: str = "medium"` (`"low" | "medium" | "high"`),
  `requires_approval: bool = False`.
- `GeneratedContent`: `persona: str`, `book_title: str`, `text: str`,
  `run_id: str`, `status: str`.
- `PublishReceipt`: `platform: str`, `persona: str`, `status: str`
  (`"published" | "approval_required" | "failed"`), `approval_id: str | None`,
  `published_at: str | None`, `formatted_preview: str | None`.
- `PublishingReport`: `book: BookInput`, `generated: list[GeneratedContent]`,
  `receipts: list[PublishReceipt]`. Has a `summary() -> str` for
  human-readable console output.

## Persona registry (`book_publisher/personas.py`)

`PersonaRegistry.load(dir_path)` scans `personas/*.yaml`, parses each into a
`PersonaSpec`, keyed by `name`. Adding a new audience = adding one YAML file;
no code changes. Example (`personas/children.yaml`):

```yaml
name: children
display_name: "儿童"
tone: "简单、有趣、多用比喻和提问"
reading_level: "小学中低年级"
max_length: 300
extra_instructions: "避免任何暴力或恐怖描写，多鼓励式语言"
target_platforms: [csdn]   # example of a persona restricting its own platforms
```

## Platform registry (`book_publisher/platforms.py`)

`PlatformRegistry.load(dir_path)` scans `platforms/*.yaml` into `PlatformSpec`.
Example (`platforms/wechat_mp.yaml`):

```yaml
name: wechat_mp
display_name: "微信公众号"
max_length: 3000
format: markdown
hashtag_style: "#{tag}"
risk_level: high
requires_approval: true    # public, irreversible — demonstrates HITL gate
```

`platforms/csdn.yaml` sets `risk_level: low`, `requires_approval: false` to
demonstrate the auto-publish path.

## Content generation

### Mock LLM backend (`book_publisher/mock_backend.py`)

`MockPersonaBackend` implements the framework's `AgentBackend` Protocol
(`agent_app/runtime/backends.py`) directly — `run()`, `stream()`, `resume()`.
`run()` renders a deterministic template combining `agent_spec.instructions`
(the persona-specific brief) with the book input text, producing
persona-flavored but non-LLM output (e.g. varying sentence complexity /
length / tone markers by persona). This is intentionally not a call to any
real model — no network I/O, fully offline and deterministic for tests/evals.

Doc note in the README: to use a real local LLM (e.g. LMStudio's
OpenAI-compatible `/v1` server), swap the backend passed to `AgentApp` for
`OpenAIAgentsBackend` pointed at the LMStudio endpoint — the pipeline code
is backend-agnostic because it only depends on the `AgentBackend` Protocol.
This is documented, not implemented, per the user's explicit "mock is enough
for now" decision.

### Per-persona agents

`build_app()` registers one `AgentSpec` per loaded persona:
`book_writer__<persona.name>`, `instructions` = `prompts/book_writer.md`
template with `{tone}`, `{reading_level}`, `{max_length}`,
`{extra_instructions}` interpolated. No tools attached (pure generation, so
the framework's built-in tool-matching heuristic in `AppRunner._simulate_tool_call`
never fires for these agents).

## Publishing

### Publisher protocol (`book_publisher/publishers/base.py`)

```python
class Publisher(Protocol):
    async def publish(self, *, content: GeneratedContent, platform: PlatformSpec) -> PublishReceipt: ...
```

### Mock publisher (`book_publisher/publishers/mock.py`)

`MockPublisher.publish()` formats the content per `PlatformSpec` (truncate to
`max_length`, apply `hashtag_style` to `book.tags`), then appends a JSON line
to `.agent_app/book_publisher_log.jsonl` (created under the example's own
`.agent_app/` working dir) simulating a "posted" record, and returns a
`PublishReceipt(status="published", published_at=...)`.

### Governed publish tool (`book_publisher/tools.py`)

`build_publish_tools(platform_registry) -> list[tuple[ToolSpec, Callable]]`
builds one `ToolSpec` per platform: `name=f"publish_{platform.name}"`,
`risk_level=platform.risk_level`, `requires_approval=platform.requires_approval`.
The tool function calls `MockPublisher.publish(...)` with the arguments it's
given (`content`, `persona`, `book_title`).

This reuses the framework's existing governance pipeline as-is — no new
mechanism needed. Risk level and approval requirement come straight from the
platform's YAML.

## Orchestration

### Content generation — DAG workflow, built programmatically

Personas are dynamic-length (extensible via YAML directory), so the DAG is
**constructed in Python at `build_app()` time**, not hand-written in
`agentapp.yaml`. This uses `Workflow.dag(name=..., nodes=[...],
execution_mode="parallel", max_concurrency=...)` (`agent_app/config/loader.py`
builds DAG workflows the same way from parsed YAML — constructing the same
object in Python directly is an equally supported path, not a workaround).

For each persona `p`:
- one `agent` node `write_{p.name}` — `type=agent, ref=book_writer__{p.name}`,
  no `input` override and no `depends_on` (all generate in parallel).

The DAG contains **only agent nodes**. `app.register_workflow(wf)`; the demo
runs `result = await app.run(workflow="book_generation",
input=book.to_prompt_text())`. Every node receives the same book-text input
(the framework resolves each node's input by starting from the top-level
`app.run(input=...)` string — confirmed via `DagExecutor._build_node_input`,
`agent_app/workflows/dag.py`); the persona identity comes from which
`AgentSpec` (`book_writer__<persona.name>`) the node's `ref` points to, so
`MockPersonaBackend.run()` picks tone/reading-level/etc. off `agent_spec.name`.
Generated text per persona is read back from
`result.node_results` (`list[dict]`, one entry per node, each with
`node_id`/`status`/`output`):

```python
generated = {
    nr["node_id"].removeprefix("write_"): nr["output"]
    for nr in result.node_results
    if nr["node_id"].startswith("write_") and nr["status"] == "completed"
}
```

**Why not put publishing in the same DAG as `tool` nodes:** the framework's
`DagExecutor._execute_tool_node` (`agent_app/workflows/dag.py`) lazily builds
its own internal `ToolExecutor` wired to a throwaway `_NoOpApprovalStore` —
never the `approval_store` passed into `AgentApp`. Approvals created this way
can never be resolved through the public `app.approve()` API (the no-op
store's `.get()` unconditionally raises `KeyError`), and
`AppRunResult.interruptions` is never populated for DAG runs either
(`WorkflowExecutor._run_dag`, `agent_app/runtime/workflow_executor.py`). This
is a framework gap, not a design choice, and per this example's non-goal of
zero `agent_app/` core changes, the example works around it rather than
patching the framework: publishing uses a directly-constructed `ToolExecutor`
instead of DAG tool nodes (below).

### Publishing — direct governed tool calls, no DAG

`book_publisher/pipeline.py` provides `async def publish_all(app, tool_executor,
book, personas, platforms, generated) -> PublishingReport`. For each persona
`p` and each platform `pl` in `p.target_platforms or all_platforms`, it calls:

```python
result = await tool_executor.execute(
    tool_name=f"publish_{pl.name}",
    arguments={"content": generated[p.name], "persona": p.name, "book_title": book.title},
    context=RunContext(run_id=..., user_id="demo", tenant_id="default"),
)
```

`tool_executor` is the framework's real, unmodified `ToolExecutor`
(`agent_app/runtime/tool_executor.py`), constructed once in `build_app.py` and
wired to the app's real `approval_store`/`audit_logger`/`trace_collector`/
`tool_registry` — this reuses the framework's existing governance pipeline
exactly as designed; no new mechanism, no bypass. Low-risk platforms
(`requires_approval=False`) execute immediately and return
`ToolExecutionResult(status="completed", output=<PublishReceipt-shaped dict>)`.
High-risk platforms return `status="interrupted"` with a real, persisted
`ApprovalRequest` (`result.approval_request.approval_id`) — because this
`ToolExecutor` is the same one used by the app's real `approval_store`, that
approval **can** be resolved via `app.approve()`.

`publish_all` maps each `ToolExecutionResult` to a `PublishReceipt`
(`status="published"` / `"approval_required"` / `"failed"`) and returns the
assembled `PublishingReport`.

### Handling the approval interruption

`main.py`'s demo:

1. Runs `book_generation`, builds `generated`, calls `publish_all(...)`,
   prints the resulting `PublishingReport` (some receipts
   `approval_required`).
2. For each `approval_required` receipt, calls
   `app.approve(receipt.approval_id, approved_by="demo-editor")` — this is
   the framework's real, public, working approval API (`AgentApp.approve`,
   `agent_app/core/app.py`), backed by the same `approval_store` the
   `ToolExecutor` used.
3. After approval, since the framework has no public "resume this exact
   governed tool call" API outside of the OpenAI-native-SDK HITL marker path
   (which is reserved for that integration and not usable here), the
   orchestration code completes the publish itself by invoking the tool's
   underlying callable directly through the registry's public accessor:
   `fn = app.tool_registry.get_fn(f"publish_{pl.name}"); receipt_dict =
   await fn(content=..., persona=..., book_title=...)`. This is a legitimate,
   publicly-exposed `ToolRegistry` method (`get_fn`, `agent_app/registry/
   tool_registry.py`), not a private/internal reach-around, and the tool
   function itself is identical to the one `ToolExecutor` would have called
   had the gate not fired.
4. Re-prints the updated `PublishingReport` with the now-`published` receipts.

## `agentapp.yaml`

```yaml
app:
  name: book-publisher
  environment: dev

governance:
  approvals:
    type: memory
  audit:
    type: memory

observability:
  tracing:
    type: memory
```

This file documents intent (governance/tracing store types) for a human
reader, but `book_publisher/build_app.py` does **not** call
`agent_app.config.loader.build_app()` — that loader always defaults to
`DryRunBackend` with no supported hook to swap it post-construction without
reaching into `AgentApp._runner` internals. Instead, `build_app()` constructs
`AgentApp` directly:

```python
approval_store = InMemoryApprovalStore()
audit_logger = InMemoryAuditLogger()
trace_collector = InMemoryTraceCollector()

app = AgentApp(
    backend=MockPersonaBackend(),
    approval_store=approval_store,
    audit_logger=audit_logger,
    trace_collector=trace_collector,
)

tool_executor = ToolExecutor(
    tool_registry=app.tool_registry,
    approval_store=approval_store,
    permission_checker=DefaultPermissionChecker(),
    audit_logger=audit_logger,
    trace_collector=trace_collector,
)
```

then registers the dynamically-built persona agents, platform tools, and
content-generation DAG workflow onto `app` via the existing public
`register_agent` / `register_tool` / `register_workflow` methods, and returns
both `app` and `tool_executor` (as a small `BookPublisherApp` namedtuple/
dataclass) so `main.py` can pass `tool_executor` into `publish_all(...)`. This
keeps every wiring decision explicit and avoids touching any private
attribute — `ToolExecutor` and `DefaultPermissionChecker` are the framework's
own public classes (`agent_app/runtime/tool_executor.py`,
`agent_app/governance/permission.py`), constructed the same way `AppRunner`
constructs its internal one. Swapping to a real backend later (e.g. LMStudio)
is exactly one line: replace the `backend=MockPersonaBackend()` argument.

## Evals (`examples/book_publisher/evals/book_publisher.yaml`)

Following `docs/evals.md` conventions:
- Case: run `book_generation` with all default personas → assert `status:
  completed` and one `write_*` node per persona in `result.node_results`.
- Case: run `publish_all` for the low-risk `csdn` platform → assert the
  returned `PublishReceipt.status == "published"`.
- Case: run `publish_all` for the high-risk `wechat_mp` platform → assert
  `PublishReceipt.status == "approval_required"`, then call
  `app.approve(receipt.approval_id, approved_by=...)` followed by
  `tool_registry.get_fn("publish_wechat_mp")(...)` and assert the follow-up
  call succeeds.
- Case: add a throwaway extra persona YAML fixture to prove a new persona
  needs zero pipeline code changes (loaded via a test-local personas dir).

## Testing (`tests/unit/test_book_publisher_*.py`)

- `test_book_publisher_personas.py` — `PersonaRegistry` loads/validates YAML,
  rejects duplicate names.
- `test_book_publisher_platforms.py` — `PlatformRegistry` loads/validates YAML.
- `test_book_publisher_mock_backend.py` — `MockPersonaBackend.run()` produces
  different, deterministic output per persona traits; satisfies `AgentBackend`
  Protocol (`isinstance(backend, AgentBackend)` via `runtime_checkable`).
- `test_book_publisher_publishers.py` — `MockPublisher` truncates to
  `max_length`, writes a JSONL record, returns a well-formed `PublishReceipt`.
- `test_book_publisher_pipeline.py` — integration: `build_app()` +
  `app.run(workflow="book_generation", ...)` produces one `write_*` node per
  persona; `publish_all(app, tool_executor, ...)` produces one
  `PublishReceipt` per persona×platform pair, low-risk platform completes
  immediately, high-risk platform returns `approval_required` and is
  completed after `app.approve()` + `tool_registry.get_fn(...)`.

## README (`examples/book_publisher/README.md`)

Documents: how to run `python examples/book_publisher/main.py`, how to add a
persona (drop a YAML file), how to add a platform (drop a YAML file + it
auto-registers as a governed tool), how to run the eval suite, and the LMStudio
backend-swap note from the "Content generation" section above.

## Risks / open questions resolved during brainstorming

- **LLM backend**: mock only for this version (user decision); LMStudio swap
  is documented, not implemented.
- **Publishing**: mock adapter + pluggable `Publisher` protocol (user
  decision); no real platform credentials.
- **Persona extensibility**: YAML-declarative, directory-scanned (user
  decision), matching the framework's existing declarative style.
- **Book input**: structured text/summary only, no file parsing (user
  decision).
- **Framework core**: zero changes — `Workflow.dag()` (content generation),
  the real `ToolExecutor` governance pipeline (publishing), and the
  `AgentBackend` Protocol already provide every primitive this example needs.
- **DAG tool nodes for publishing (rejected during plan verification)**:
  initially designed, but `DagExecutor._execute_tool_node`
  (`agent_app/workflows/dag.py`) always executes against an internal,
  ephemeral `_NoOpApprovalStore` rather than the app's configured
  `approval_store`, so approvals it creates can never be resolved via
  `app.approve()`, and DAG runs never populate `AppRunResult.interruptions`.
  This is a framework gap (confirmed by reading `DagExecutor`,
  `WorkflowExecutor._run_dag`, and `ToolExecutor`'s `approved_tool_call`
  marker, which is reserved for OpenAI-native-SDK HITL only). Per the
  no-core-changes non-goal, the example avoids DAG tool nodes for publishing
  entirely and instead drives the real `ToolExecutor` directly from
  orchestration code (`book_publisher/pipeline.py`) — the DAG is used only
  for the parallel, tool-free content-generation step.
