"""Tools for the openai_basic example.

Demonstrates both low-risk and high-risk tools with the OpenAI backend:

- ``math.add`` — low-risk, executes directly
- ``account.delete`` — high-risk, requires approval and specific permission
"""

from agent_app import tool


# -- Low-risk tool: executes directly --

@tool(
    name="math.add",
    description="Add two numbers together.",
    risk_level="low",
    permissions=[],
)
async def add_numbers(a: float, b: float) -> dict:
    """Add two numbers and return the result."""
    return {"result": a + b}


# -- High-risk tool: requires approval + permission --

@tool(
    name="account.delete",
    description="Delete an account by ID.",
    risk_level="high",
    requires_approval=True,
    permissions=["account:delete"],
)
async def delete_account(account_id: str) -> dict:
    """Delete an account (requires approval and account:delete permission)."""
    # NOTE: This function body is only reached when:
    # 1. The caller has the "account:delete" permission
    # 2. An approval has been granted for this specific call
    return {"deleted": True, "account_id": account_id}
