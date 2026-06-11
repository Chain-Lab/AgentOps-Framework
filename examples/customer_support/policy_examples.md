# Policy Examples for Customer Support

Phase 24: Demonstrates policy engine validation, simulation, and explanation.

## Policy Rules in agentapp.yaml

The customer_support example includes these policy rules:

### 1. Refund Approval (require_approval)

```yaml
- name: refund_requires_approval
  when:
    tool_name: refund.request
  then:
    action: require_approval
    reason: Refund requests require human approval
    ttl_seconds: 1800
```

**Effect:** Any call to `refund.request` requires human approval with a 30-minute TTL.

### 2. Billing Audit Only (audit_only)

```yaml
- name: billing_audit_only
  when:
    tool_name: billing.query
  then:
    action: audit_only
    reason: Billing queries are audit-only per compliance policy
```

**Effect:** Billing queries are logged for compliance but allowed to proceed.

### 3. Dangerous Tools Deny (deny)

```yaml
- name: deny_dangerous_tools
  when:
    tool_name_prefix: dangerous.
  then:
    action: deny
    reason: Dangerous tools are blocked by policy
```

**Effect:** Any tool with a `dangerous.` prefix is blocked by policy.

## Validate Policy Config

Check that policy configuration is valid before deployment:

```bash
agentapp policy validate --config examples/customer_support/agentapp.yaml
```

Output:
```
Policy config is valid. No issues found.
```

Exit codes:
- `0`: Valid config (or only warnings)
- `1`: Validation errors found

## Simulate Policy Decision

Test what the policy engine would decide without executing the tool:

```bash
agentapp policy simulate \
  --config examples/customer_support/agentapp.yaml \
  --tool refund.request \
  --risk high \
  --role refund_operator \
  --permission refund:create \
  --tenant eval_tenant
```

Output:
```
Policy simulation for tool 'refund.request':
  Action:     require_approval
  Allowed:    True
  Rule:       refund_requires_approval
  Reason:     Refund requests require human approval
  TTL:        1800s
  → Requires human approval
```

### Simulation Scenarios

**High-risk refund with proper role → requires approval:**
```bash
agentapp policy simulate \
  --config examples/customer_support/agentapp.yaml \
  --tool refund.request \
  --risk high \
  --role refund_operator \
  --permission refund:create
```

**Billing query → audit_only (allowed):**
```bash
agentapp policy simulate \
  --config examples/customer_support/agentapp.yaml \
  --tool billing.query
```

**Dangerous tool → denied:**
```bash
agentapp policy simulate \
  --config examples/customer_support/agentapp.yaml \
  --tool dangerous.delete \
  --risk critical
```

## Explain Policy Decision

Get detailed explanation of why a policy decision was made:

```bash
agentapp policy explain \
  --config examples/customer_support/agentapp.yaml \
  --tool refund.request \
  --risk high \
  --tenant eval_tenant
```

Output:
```
Policy explain for tool 'refund.request':
  Decision ID:  dec_unknown
  Action:       require_approval
  Rule:         refund_requires_approval
  Reason:       Refund requests require human approval
  Matched:      {'tool_name': 'refund.request'}
  Context:      {'tool_name': 'refund.request', 'risk_level': 'high', 'tenant_id': 'eval_tenant'}
```

### Explanation Fields

| Field | Description |
|-------|-------------|
| `decision_id` | Unique identifier for this decision |
| `action` | The policy action taken |
| `rule_name` | Name of the matched rule (or `default`) |
| `reason` | Human-readable explanation |
| `matched_conditions` | Conditions from the rule that matched |
| `context_summary` | Safe summary of evaluation context |

## Using in CI

Validate policy config as part of CI:

```bash
#!/bin/bash
# ci/validate-policy.sh
set -e
agentapp policy validate --config examples/customer_support/agentapp.yaml
echo "Policy validation passed"
```

## Policy Simulation in Tests

Use the policy simulator programmatically:

```python
from agent_app.config.loader import load_config
from agent_app.governance.policy import ConfigurablePolicyEngine
from agent_app.governance.policy_simulator import PolicySimulator, PolicySimulationInput

config = load_config("examples/customer_support/agentapp.yaml")
gov = config.governance
policy_cfg = gov.policies

rules = [r.model_dump() for r in policy_cfg.rules]
engine = ConfigurablePolicyEngine(rules=rules, default_action="allow")
sim = PolicySimulator(policy_engine=engine)

# Simulate a refund request
result = await sim.simulate(PolicySimulationInput(
    tool_name="refund.request",
    risk_level="high",
    tenant_id="eval_tenant",
))
assert result.decision.action.value == "require_approval"

# Get explain trace
explain_result = await sim.explain(PolicySimulationInput(
    tool_name="dangerous.delete",
))
assert explain_result.trace.action.value == "deny"
```

## Constraints

- **Policy does NOT execute tools**: Simulation and explain are read-only.
- **Policy does NOT create approvals**: Only runtime evaluation creates approvals.
- **No sensitive data in traces**: Context summary excludes raw arguments.
- **Policy can only tighten**: Default policy engine behavior is preserved when no rules match.
