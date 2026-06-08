# Customer Support Example

A multi-agent customer support example demonstrating the Agent App Framework
with a **handoff (triage) workflow** and **configurable routing policy**.

## What this example shows

- **Multi-agent handoff** — triage agent routes users to specialist agents
- **Routing policy** — declarative YAML rules for keyword-based intent detection
- **Tool governance** — low-risk tools execute directly, high-risk tools require approval
- **Permission-based access control** — users without the right permissions get `permission_denied`
- **Human-in-the-loop approval** — approve/reject/resume flow
- **Workflow trace** — structured execution observability via `AppRunResult.workflow_trace`
- **Eval regression testing** with 4 cases covering routing and governance

## Prerequisites

```bash
pip install -e ".[dev]"
```

## Agents

| Agent | Role |
|-------|------|
| `triage` | Entry point — routes users to the right specialist |
| `refund` | Handles refund requests |
| `billing` | Handles billing and invoice queries |
| `technical_support` | Handles technical issues and errors |

## Tools

| Tool | Risk Level | Permissions |
|------|-----------|-------------|
| `order.query` | `low` | `order:read` |
| `refund.request` | `high` | `refund:create` (requires approval) |

## Workflow

**Type:** `handoff`

The `triage` agent receives all input. Based on the routing policy,
the framework routes the request to the appropriate specialist.
The handoff chain is recorded in `AppRunResult.handoffs`.

## Routing Rules

```yaml
routing:
  rules:
    - name: refund_intent
      target: refund
      match_type: keyword
      keywords: [refund, 退款, 退货, 退钱]
      priority: 10
    - name: billing_intent
      target: billing
      match_type: keyword
      keywords: [invoice, 发票, billing, 账单, 付款]
      priority: 20
    - name: technical_intent
      target: technical_support
      match_type: keyword
      keywords: [error, 报错, 技术, bug, 故障, 问题]
      priority: 30
    - name: default_triage
      target: triage
      match_type: default
      priority: 999
```

**Priority:** Lower number = higher priority. The first matching rule wins.

## Scenarios

### 1. Order query (low risk)

```
Input:  "query order 123"
Result: completed — returns order details
```

No approval required. Any user with `order:read` permission can execute.

### 2. Refund request — approval required (high risk)

```
Input:  "refund order 123"
Result: interrupted — approval required for refund.request
```

The tool has `risk_level: "high"` and `requires_approval: true`.
The run is paused and an approval request is created.

### 3. Invoice / billing query

```
Input:  "I need my invoice"
Result: completed — routed to billing agent
```

Matched by `billing_intent` routing rule.

### 4. Technical issue

```
Input:  "system error bug"
Result: completed — routed to technical_support agent
```

Matched by `technical_intent` routing rule.

## Files

| File | Description |
|------|-------------|
| `agentapp.yaml` | App configuration: agents, workflows, routing, session, governance |
| `tools.py` | Tool definitions: `order.query` and `refund.request` |
| `prompts/triage.md` | Triage agent instructions |
| `prompts/refund.md` | Refund specialist instructions |
| `prompts/billing.md` | Billing specialist instructions |
| `prompts/technical_support.md` | Technical support specialist instructions |
| `main.py` | Direct Python entry point |
| `api.py` | FastAPI entry point |
| `evals/customer_support.yaml` | 4 eval cases |

## Run the Python example

```bash
python examples/customer_support/main.py
```

## Run the FastAPI server

```bash
uvicorn examples.customer_support.api:api --reload
```

Then try:

```bash
# Health check
curl http://localhost:8000/health

# List agents
curl http://localhost:8000/agents

# Run an agent
curl -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{"agent": "triage", "input": "I want a refund", "user_id": "u1", "tenant_id": "t1"}'
```

## Run evals

```bash
agentapp eval run examples/customer_support/evals/customer_support.yaml \
  --config examples/customer_support/agentapp.yaml
```

Expected output:

```
Eval suite: customer_support_eval
Passed: 4/4
  ✓ order_query_success
  ✓ refund_requires_approval
  ✓ billing_query_success
  ✓ technical_support_routed
```

## Current Limitations

- Uses `DryRunBackend` by default — no real LLM calls
- Routing is keyword-based heuristic, not semantic intent detection
- Resume is not a real OpenAI RunState resume
