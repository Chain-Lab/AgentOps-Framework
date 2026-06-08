"""Research assistant example — orchestrator workflow demo."""

import asyncio
import sys
from pathlib import Path

_examples_dir = Path(__file__).resolve().parent
if str(_examples_dir) not in sys.path:
    sys.path.insert(0, str(_examples_dir))

from agent_app.config.loader import build_app


async def main() -> None:
    app = build_app("examples/research_assistant/agentapp.yaml")

    print("=== Research Assistant (Orchestrator mode) ===\n")

    result = await app.run(
        workflow="research_assistant",
        input="research the latest AI trends and write a summary report",
        user_id="demo_user",
        tenant_id="demo_tenant",
    )

    print(f"Run ID   : {result.run_id}")
    print(f"Status   : {result.status}")
    print(f"Output   : {result.final_output}")
    if result.agent_calls:
        print(f"Agent calls: {result.agent_calls}")
    print(f"Latency  : {result.latency_ms} ms")


if __name__ == "__main__":
    asyncio.run(main())
