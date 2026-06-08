# OpenAI Multi-Agent Examples

Demonstrates using the **real OpenAI Agents SDK** backend with the framework's
multi-agent workflow types: **handoff** (triage) and **orchestrator** (agents-as-tools).

## Prerequisites

```bash
pip install -e ".[openai]"
```

Set your API key:

```bash
export OPENAI_API_KEY=sk-...
```

## Examples

### Handoff Workflow — `customer_support_handoff.yaml`

```yaml
workflows:
  customer_support:
    type: handoff
    entry: triage
    agents:
      - refund
      - billing
      - technical_support
```

The `triage` agent receives user input and hands off to a specialist agent
based on the conversation. The OpenAI SDK's native `Agent.handoffs` mechanism
is used when `backend: openai` is configured.

**Run:**

```python
from agent_app.config.loader import build_app

app = build_app("examples/openai_multi_agent/customer_support_handoff.yaml")

result = await app.run(
    workflow="customer_support",
    input="I want a refund for my order",
)
print(result.workflow_trace)
```

### Orchestrator Workflow — `research_assistant_orchestrator.yaml`

```yaml
workflows:
  research_assistant:
    type: orchestrator
    entry: manager
    agents_as_tools:
      - researcher
      - analyst
      - writer
```

The `manager` agent delegates to specialist agents (`researcher`, `analyst`,
`writer`) based on the task. Specialist agents are compiled as SDK tools
using `Agent.as_tool()` with a fallback wrapper.

**Run:**

```python
from agent_app.config.loader import build_app

app = build_app("examples/openai_multi_agent/research_assistant_orchestrator.yaml")

result = await app.run(
    workflow="research_assistant",
    input="Research AI trends and write a summary report",
)
print(result.agent_calls)
print(result.workflow_trace)
```

## HITL Modes

Both examples default to `hitl_mode: wrapper`. To use native HITL:

```yaml
runtime:
  backend: openai
  openai:
    hitl_mode: native
```

See `examples/openai_basic/agentapp.native.yaml` for a complete native HITL
configuration with SQLite persistence.

## Smoke Tests

Smoke tests that call the real OpenAI API are **opt-in only**:

```bash
OPENAI_API_KEY=sk-... python -m pytest -m openai_smoke
```

Default `pytest` does **not** call the OpenAI API.

## Current Limitations

- **Handoff target extraction** — The actual handoff target is recorded in
  `WorkflowTrace` as `handoff_candidates` but not extracted from SDK results.
- **Orchestrator agent_calls** — Extracted from SDK `tool_calls`; may be
  incomplete depending on SDK output.
- **Agent-as-tool governance** — Specialist agents-as-tools do not go through
  the framework's `ToolExecutor` governance pipeline.
- **No parallel execution** — Orchestrator specialists are conceptually
  parallel but executed serially through the SDK.
- **Fallback agent-as-tool** — When `Agent.as_tool()` is unavailable, the
  fallback wrapper returns a delegation placeholder rather than executing the
  specialist.
