"""Customer support example — main entry point.

Run with:
    cd examples/customer_support
    python3 main.py

Or from project root:
    python3 examples/customer_support/main.py
"""

import asyncio
import sys
from pathlib import Path

# Ensure tools are imported so @tool decorators register them.
# Support both `python3 main.py` and `python3 examples/customer_support/main.py`.
_examples_dir = Path(__file__).resolve().parent
if str(_examples_dir) not in sys.path:
    sys.path.insert(0, str(_examples_dir))
from tools import (  # noqa: E402  # noqa: F401
    query_order,
    request_refund,
)

from agent_app.config.loader import build_app


async def main() -> None:
    app = build_app("examples/customer_support/agentapp.yaml")

    print("=== Customer Support Agent (DryRun mode) ===\n")

    result = await app.run(
        workflow="customer_support",
        input="Hi, I'd like to check the status of my order 123.",
        user_id="demo_user",
        tenant_id="demo_tenant",
        session_id="session_001",
    )

    print(f"Run ID   : {result.run_id}")
    print(f"Status   : {result.status}")
    print(f"Output   : {result.final_output}")
    print(f"Latency  : {result.latency_ms} ms")
    if result.tool_calls:
        print(f"Tool calls: {result.tool_calls}")


if __name__ == "__main__":
    asyncio.run(main())
