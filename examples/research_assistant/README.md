# Research Assistant Example

A multi-agent research assistant example demonstrating the Agent App Framework
with an **orchestrator (agents-as-tools) workflow** and **configurable routing policy**.

## What this example shows

- **Multi-agent orchestrator** — manager agent delegates to specialist agents
- **Routing policy** — declarative YAML rules for keyword-based task detection
- **Workflow trace** — structured execution observability via `AppRunResult.workflow_trace`
- **Eval regression testing** with 4 cases covering single and multi-agent delegation

## Prerequisites

```bash
pip install -e ".[dev]"
```

## Agents

| Agent | Role |
|-------|------|
| `manager` | Orchestrator — analyzes input and delegates to specialists |
| `researcher` | Research specialist — gathers information |
| `analyst` | Data analysis specialist |
| `writer` | Writing specialist — produces reports |

## Workflow

**Type:** `orchestrator`

The `manager` agent receives all input. Based on the routing policy,
it delegates to one or more specialist agents. Specialist invocations
are recorded in `AppRunResult.agent_calls`.

## Routing Rules

```yaml
routing:
  rules:
    - name: research_task
      target: researcher
      match_type: keyword
      keywords: [research, 调研, 研究, search]
      priority: 10
    - name: data_task
      target: analyst
      match_type: keyword
      keywords: [data, 数据, 分析, analyze]
      priority: 20
    - name: writing_task
      target: writer
      match_type: keyword
      keywords: [write, 写, 总结, 报告, report, summary]
      priority: 30
```

**Priority:** Lower number = higher priority. Multiple rules can match —
the orchestrator calls all matching specialists.

## Scenarios

### 1. Single specialist — research

```
Input:  "research the latest AI trends"
Result: completed — manager delegates to researcher
```

Matched by `research_task` routing rule.

### 2. Single specialist — data analysis

```
Input:  "analyze the sales data"
Result: completed — manager delegates to analyst
```

Matched by `data_task` routing rule.

### 3. Single specialist — writing

```
Input:  "write a summary report"
Result: completed — manager delegates to writer
```

Matched by `writing_task` routing rule.

### 4. Multiple specialists

```
Input:  "research AI trends, analyze the data, and write a report"
Result: completed — manager delegates to researcher, analyst, and writer
```

All three routing rules match. The orchestrator calls all specialists
in priority order.

## Files

| File | Description |
|------|-------------|
| `agentapp.yaml` | App configuration: agents, workflow, routing |
| `prompts/manager.md` | Manager agent instructions |
| `prompts/researcher.md` | Researcher specialist instructions |
| `prompts/analyst.md` | Analyst specialist instructions |
| `prompts/writer.md` | Writer specialist instructions |
| `main.py` | Direct Python entry point |
| `evals/research_assistant.yaml` | 4 eval cases |

## Run the Python example

```bash
python examples/research_assistant/main.py
```

## Run evals

```bash
agentapp eval run examples/research_assistant/evals/research_assistant.yaml \
  --config examples/research_assistant/agentapp.yaml
```

Expected output:

```
Eval suite: research_assistant_eval
Passed: 4/4
  ✓ research_delegates_to_researcher
  ✓ data_delegates_to_analyst
  ✓ report_delegates_to_writer
  ✓ mixed_delegates_multiple
```

## Current Limitations

- Uses `DryRunBackend` by default — no real LLM calls
- Specialists are called serially, not in parallel
- Routing is keyword-based heuristic, not semantic intent detection
- No real model reasoning — DryRunBackend simulates agent execution
