# Eval Runner Reference

The eval runner executes YAML-defined regression suites against an `AgentApp`
instance and checks the results against expected outcomes.

## Running evals

```bash
agentapp eval run <suite.yaml> --config <agentapp.yaml>
```

## Suite structure

```yaml
name: my_eval_suite                    # Required: suite identifier
description: Optional description       # Optional: human-readable description
defaults:                              # Optional: defaults applied to all cases
  agent: support                       # Default agent name
  workflow: customer_support           # Default workflow name
  user_id: eval_user                   # Default user ID
  tenant_id: eval_tenant               # Default tenant ID
  permissions:                         # Default permissions list
    - order:read

cases:                                 # Required: list of eval cases
  - id: case_id                        # Required: unique case identifier
    input: "user input text"           # Required: input to test
    agent: override_agent              # Optional: override default agent
    workflow: override_workflow        # Optional: override default workflow
    user_id: override_user             # Optional: override default user_id
    tenant_id: override_tenant         # Optional: override default tenant_id
    permissions:                       # Optional: override default permissions
      - refund:create
    expect:                            # Required: expected outcomes
      status: completed                # Expected result status
      output_contains:                 # List of strings that must appear in output
        - "order"
        - "123"
```

## Expect fields

### `status`

Expected run status. One of:

- `"completed"` — run finished successfully
- `"interrupted"` — run paused (e.g. awaiting approval)
- `"failed"` — run encountered an error

```yaml
expect:
  status: completed
```

### `output_contains`

List of strings that must appear in `result.final_output`.

```yaml
expect:
  output_contains:
    - "order"
    - "123"
```

### `tools_called`

List of tool names that must have been called during the run.

```yaml
expect:
  tools_called:
    - order.query
```

### `approvals_required`

List of tool names that must have triggered approval requests
(checked against `result.interruptions`).

```yaml
expect:
  approvals_required:
    - refund.request
```

### `error_type`

Expected error type when `status` is `"failed"`.

```yaml
expect:
  status: failed
  error_type: KeyError
```

### `handoffs`

List of expected handoff records (for handoff workflows).
Each entry is a dict with `from_agent` and `to_agent`.

```yaml
expect:
  status: completed
  handoffs:
    - from_agent: triage
      to_agent: refund
```

### `agent_calls`

List of agent names that must have been called (for orchestrator workflows).

```yaml
expect:
  status: completed
  agent_calls:
    - researcher
    - writer
```

### `routing_decisions`

List of routing rule names that must appear in the workflow trace.
Checks `workflow_trace.steps[].metadata["rule"]` for matching entries.

```yaml
expect:
  routing_decisions:
    - refund_intent
    - billing_intent
```

### `workflow_steps`

List of step types that must appear in the workflow trace.
Checks `workflow_trace.steps[].step_type`.

```yaml
expect:
  workflow_steps:
    - routing
    - agent
```

### `approve_and_resume`

When `true`, the eval runner will:

1. Run the case and expect `status: "interrupted"` with approvals
2. Automatically approve all pending approvals
3. Resume the run
4. Check `resumed_status` if specified

```yaml
expect:
  status: interrupted
  approvals_required:
    - refund.request
  approve_and_resume: true
  resumed_status: completed
```

## Complete example

```yaml
name: customer_support_eval
description: Customer support agent regression tests

defaults:
  workflow: customer_support
  user_id: eval_user
  tenant_id: eval_tenant
  permissions:
    - order:read
    - refund:create

cases:
  - id: order_query_success
    input: "query order 123"
    expect:
      status: completed
      output_contains:
        - "order"
        - "123"

  - id: refund_requires_approval
    input: "refund order 123"
    expect:
      status: interrupted
      handoffs:
        - from_agent: triage
          to_agent: refund
      routing_decisions:
        - refund_intent
      approvals_required:
        - refund.request

  - id: billing_query_success
    input: "invoice order 123"
    expect:
      status: completed
      handoffs:
        - from_agent: triage
          to_agent: billing
      routing_decisions:
        - billing_intent

  - id: technical_support_routed
    input: "system error bug"
    expect:
      status: completed
      handoffs:
        - from_agent: triage
          to_agent: technical_support
      routing_decisions:
        - technical_intent
```
