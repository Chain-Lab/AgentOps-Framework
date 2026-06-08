"""Customer support example — FastAPI entry point.

Run with:
    pip install -e ".[api]"
    uvicorn examples.customer_support.api:api --reload

Then:
    curl http://localhost:8000/health
    curl http://localhost:8000/agents
    curl -X POST http://localhost:8000/runs \
      -H "Content-Type: application/json" \
      -d '{"agent": "support", "input": "查询订单 123", "user_id": "u1", "tenant_id": "t1"}'
"""

import sys
from pathlib import Path

# Ensure tools are imported so @tool decorators register them.
_examples_dir = Path(__file__).resolve().parent
if str(_examples_dir) not in sys.path:
    sys.path.insert(0, str(_examples_dir))
from tools import (  # noqa: E402  # noqa: F401
    query_order,
    request_refund,
)

from agent_app.adapters.fastapi import create_fastapi_app
from agent_app.config.loader import build_app

# Build the agent app from YAML config.
app = build_app("examples/customer_support/agentapp.yaml")

# Create the FastAPI wrapper.
api = create_fastapi_app(app)
