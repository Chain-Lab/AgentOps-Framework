# Policy Console Lite — Phase 26

> **Status:** Implemented

## Overview

Phase 26 adds a lightweight, read-only HTML console for viewing policy
decision data. It sits on top of Phase 25's policy decision store and
reporting service — no duplicate query logic, no complex frontend build.

## Architecture

```
PolicyConsoleConfig (GovernanceConfig.policy_console)
  ├── enabled: bool (default: false)
  ├── base_path: str (default: "/policy-console")
  ├── title: str
  └── page_size: int (default: 50)

agent_app/console/
  ├── router.py         — FastAPI router with 4 HTML pages
  ├── templates/
  │   ├── base.html
  │   ├── policy_dashboard.html
  │   ├── policy_decisions.html
  │   ├── policy_decision_detail.html
  │   └── policy_report.html
  └── static/
      ├── console.css
      └── console.js
```

## Configuration

Add to `governance` in `agentapp.yaml`:

```yaml
governance:
  policy_decision_store:
    type: sqlite
    path: .agent_app/policy_decisions.db
  policy_console:
    enabled: true
    base_path: /policy-console
    title: Customer Support Policy Console
    page_size: 50
```

**Default: disabled.** The console is only registered when `enabled: true`.

## Pages

### Dashboard (`GET /policy-console/`)

- Total decisions count
- Action breakdown (allow, deny, require_approval, audit_only)
- Top rules, top tools
- Time range
- Recent decisions table

### Decisions List (`GET /policy-console/decisions`)

- Filterable table: action, rule, tool, agent, tenant, run ID
- Pagination (configurable page size)
- Click row → detail page
- Empty state when no matches

### Decision Detail (`GET /policy-console/decisions/{decision_id}`)

- Full decision info: timestamp, action, rule, tool, agent, workflow
- Reason, matched conditions (JSON), context summary (JSON)
- 404-style friendly message for missing decisions

### Report (`GET /policy-console/report`)

- Action breakdown table
- Rule breakdown table
- Tool breakdown table
- Time range

## FastAPI Integration

The console is wired through `create_fastapi_app()`:

```python
from agent_app.adapters.fastapi import create_fastapi_app

api = create_fastapi_app(app)  # reads console config from app._console_config
```

When `policy_console.enabled` is true, the router is automatically mounted
at `base_path`. Static files (CSS, JS) are served from the same prefix.

## Design Decisions

- **No frontend build step**: Pure Jinja2 + vanilla CSS/JS
- **Read-only**: No write or modify operations
- **Graceful degradation**: Pages render empty states when store is absent
- **Jinja2 optional**: Console gracefully returns error if jinja2 not installed
- **No auth**: Console is a debugging/ops tool — place behind reverse proxy
  or network isolation in production
- **HTML escaping**: All dynamic content is HTML-escaped by Jinja2

## Security

- Console defaults to disabled
- Not authenticated — do not expose publicly
- Does not display sensitive tool arguments (uses `context_summary` only)
- All output is Jinja2-escaped

## Relationship to Other Interfaces

| Interface | Purpose | Writes |
|-----------|---------|--------|
| CLI `agentapp policy decisions/report/export` | Terminal access | No |
| FastAPI JSON `/policy-decisions` | API access | No |
| Policy Console `/policy-console` | Browser access | No |
| ToolExecutor | Records decisions | Yes |

## Requirements

- `jinja2>=3.0` (install with `pip install 'agent-app-framework[all]'`)
