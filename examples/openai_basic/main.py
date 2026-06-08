"""OpenAI Backend Basic Example.

Demonstrates using the real OpenAI Agents SDK backend with the framework.

Prerequisites:
    pip install -e ".[openai]"
    export OPENAI_API_KEY=sk-...

Usage:
    python examples/openai_basic/main.py                          # uses agentapp.yaml
    python examples/openai_basic/main.py --config agentapp.native.yaml  # native HITL mode
"""

import asyncio
import sys

from agent_app.config.loader import build_app


DEFAULT_CONFIG = "examples/openai_basic/agentapp.yaml"


async def main() -> None:
    config_path = DEFAULT_CONFIG
    if "--config" in sys.argv:
        idx = sys.argv.index("--config")
        if idx + 1 < len(sys.argv):
            config_path = sys.argv[idx + 1]

    app = build_app(config_path)

    result = await app.run(
        agent="assistant",
        input="What is 42 + 17?",
    )
    print(f"Status: {result.status}")
    print(f"Output: {result.final_output}")
    print(f"Tool calls: {result.tool_calls}")


if __name__ == "__main__":
    asyncio.run(main())
