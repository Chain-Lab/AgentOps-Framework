# OpenAI Backend Basic Example

Demonstrates using the **real OpenAI Agents SDK** as the execution backend for the Agent App Framework.

## Prerequisites

```bash
pip install -e ".[openai]"
```

Set your API key:

```bash
export OPENAI_API_KEY=sk-...
```

## What this example shows

- **Real OpenAI Agent execution** — uses `OpenAIAgentsBackend` instead of `DryRunBackend`
- **Function tool compilation** — framework tools compiled into SDK `function_tool`
- **Config-driven backend selection** — `runtime.backend: openai` in YAML
- **Governance-aware tools** — tools route through ToolExecutor for permissions and audit

## Risk Levels

This example includes two tools demonstrating different risk levels:

### `math.add` (low-risk)

```python
@tool(
    name="math.add",
    description="Add two numbers.",
    risk_level="low",
    permissions=[],
)
async def add_numbers(a: float, b: float) -> dict:
    return {"result": a + b}
```

Low-risk tools execute directly through the governance pipeline without requiring approval.

### `account.delete` (high-risk)

```python
@tool(
    name="account.delete",
    description="Delete an account by ID.",
    risk_level="high",
    requires_approval=True,
    permissions=["account:delete"],
)
async def delete_account(account_id: str) -> dict:
    return {"deleted": True, "account_id": account_id}
```

High-risk tools behave as follows:

| Condition | Result |
|-----------|--------|
| Caller has `account:delete` permission AND approval granted | Tool executes, returns `{"deleted": true, "account_id": "..."}` |
| Caller lacks `account:delete` permission | Returns `{"status": "error", "error": {"type": "permission_denied", ...}, "tool_name": "account.delete"}` |
| Approval not yet granted | Returns `{"status": "approval_required", "approval_id": "...", ...}` |

**Important:** The `approval_required` response is returned to the model as a tool
output. The OpenAI SDK run is **not** actually paused. The model will see the
response and continue generating. True RunState pause/resume is a future enhancement.

## HITL Modes

This example supports two HITL modes for the OpenAI backend:

### Wrapper Mode (default)

Uses the framework's governance wrapper. High-risk tools return
`approval_required` as tool output to the model — the SDK run is not paused.

```bash
python examples/openai_basic/main.py
```

Uses `agentapp.yaml` with default `hitl_mode: wrapper`.

### Native Mode (Phase 10)

Uses the SDK's native `needs_approval` and `RunState` for real pause/resume:

```bash
python examples/openai_basic/main.py --config examples/openai_basic/agentapp.native.yaml
```

Or modify `main.py` to load the native config:

```python
app = build_app("examples/openai_basic/agentapp.native.yaml")
```

Native mode requires `openai-agents >= 0.2.0` with `RunState` support.
State is persisted to SQLite for resume.

## Governance Configuration

To enable full governance (permissions, approval, audit) with the OpenAI backend:

```yaml
runtime:
  backend: openai

governance:
  approvals:
    type: sqlite
    path: .agent_app/approvals.db
  audit:
    type: sqlite
    path: .agent_app/audit.db
  permissions:
    mode: default
```

With governance configured:
- Tool calls are checked for required permissions
- High-risk tools (risk_level="high", requires_approval=true) generate approval requests
- All tool executions are audit-logged with run_id and tenant_id
- Permission denied returns a structured error to the model
- Approval required returns a structured response to the model (wrapper mode)
  or natively interrupts the SDK run (native mode)

## Files

| File | Description |
|------|-------------|
| `agentapp.yaml` | App config with `runtime.backend: openai` |
| `prompts/assistant.md` | Assistant agent instructions |
| `tools.py` | `math.add` tool definition |
| `main.py` | Entry point |

## Run

### Wrapper mode (default)

```bash
python examples/openai_basic/main.py
```

### Native HITL mode

```bash
python examples/openai_basic/main.py --config examples/openai_basic/agentapp.native.yaml
```

The `--config` flag loads the specified YAML config. Defaults to `agentapp.yaml`.

## Current Limitations

- Requires a valid `OPENAI_API_KEY`
- **Wrapper mode** (default): `approval_required` is returned to the model as tool output; SDK run is not paused
- **Native mode**: requires `openai-agents >= 0.2.0` with `RunState` support; SDK run is natively paused for approvals
- No multi-agent handoff/orchestrator support yet with OpenAI backend
