# Recovery Admin Console

The Recovery Admin Console is an optional, server-rendered FastAPI UI for inspecting recovery status, reviewing candidates, viewing run-scoped recovery history, running dry-run scans, and explicitly confirming live recovery for one persisted DAG workflow run at a time.

## Installation

FastAPI is optional. Install the API extra before creating or mounting the UI router:

```bash
pip install 'agent-app-framework[api]'
```

The module is import-light by design:

- `import agent_app` does not require FastAPI.
- `import agent_app.adapters.recovery_ui` does not require FastAPI.
- FastAPI is imported only when `create_recovery_ui_router()` is called. If FastAPI is not installed at that point, router creation raises an `ImportError` explaining that the API extra is required.

## Mounting

Mount the UI router explicitly on your FastAPI application and provide an admin authorization dependency:

```python
from fastapi import FastAPI, HTTPException, Request

from agent_app import AgentApp
from agent_app.adapters.recovery_ui import create_recovery_ui_router

app = AgentApp()
api = FastAPI()

async def require_recovery_admin(request: Request) -> None:
    if request.headers.get("x-admin-token") != "expected-secret":
        raise HTTPException(status_code=403, detail="Forbidden")

api.include_router(
    create_recovery_ui_router(app, admin_dependency=require_recovery_admin)
)
```

`admin_dependency` is required for access control. If it is omitted, the router is deny-by-default and returns HTTP 403 for all UI routes. Authorization, authentication, session handling, CSRF policy, and audit identity mapping remain the responsibility of the supplied dependency and surrounding FastAPI application.

JSON admin endpoints and HTML UI endpoints are mounted explicitly and separately. Mount `agent_app.adapters.recovery_admin` when you need JSON admin API routes, and mount `agent_app.adapters.recovery_ui` when you need the server-rendered console.

## Routes

The UI router mounts under `/admin/recovery` and provides these routes:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/admin/recovery` | Recovery status dashboard. |
| GET | `/admin/recovery/candidates` | Dry-run candidate scan results. |
| GET | `/admin/recovery/candidates/{run_id}` | Inspect one recovery candidate. |
| GET | `/admin/recovery/history` | Run-scoped recovery history lookup and results. |
| POST | `/admin/recovery/scan` | Trigger a dry-run candidate scan. |
| POST | `/admin/recovery/candidates/{run_id}/confirm` | Render the live-recovery confirmation form and token. |
| POST | `/admin/recovery/candidates/{run_id}/recover` | Execute live recovery after explicit confirmation. |

GET routes are read-only. No GET route performs recovery or any other mutation.

## Safety defaults

The console preserves the recovery subsystem safety defaults:

- The recovery daemon remains default-off.
- Creating or mounting the UI router does not start the daemon.
- Dry-run remains the default mode.
- `POST /admin/recovery/scan` always performs a dry-run scan with `AutoRecoveryPolicy(dry_run=True)`.
- `POST /admin/recovery/scan` rejects attempts to submit `dry_run=false` with HTTP 400.
- Live recovery requires two POST requests:
  1. `POST /admin/recovery/candidates/{run_id}/confirm` inspects the candidate and renders a process-local HMAC confirmation token.
  2. `POST /admin/recovery/candidates/{run_id}/recover` requires both the confirmation token and `confirm_no_dry_run=true` before calling `app.recover_run(run_id=run_id, dry_run=False)`.

## Confirmation token details

The confirmation token is an HMAC over this exact message:

```text
recovery-confirm:{run_id}
```

The HMAC secret is generated when `create_recovery_ui_router()` is called. Tokens are valid only for the lifetime of that router instance in that process. Restarting the process or recreating the router invalidates previously rendered confirmation forms.

The token confirms that the operator loaded the live-recovery confirmation page for the same run in the same router process. It is not an authorization mechanism. Authorization remains the responsibility of `admin_dependency`.

## Error handling

The UI renders clean error pages for expected and unexpected failures. Internal exceptions are logged server-side and generic error messages are shown to the operator; raw exception strings and tracebacks are not rendered in response bodies.

## Limitations

- The UI stores no database state of its own.
- There is no React, Vite, Node, template engine, or frontend build pipeline.
- Recovery is best-effort. Leases coordinate ownership but do not provide exactly-once execution guarantees.
- History views are run-scoped; they depend on the configured audit logger's ability to list events for a run.
- The recovery daemon is not started by the UI router and must be configured and started explicitly if wanted.
- JSON admin routes and HTML UI routes are separate routers and must be mounted explicitly.
- Live recovery is intentionally one run at a time; bulk recovery remains outside the UI scope.
