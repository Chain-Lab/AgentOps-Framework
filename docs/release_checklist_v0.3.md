# v0.3.0 Release Checklist

## Verification

- [ ] `python -m pytest` — all tests pass (230+ passed, 0 failed)
- [ ] `agentapp eval run examples/customer_support/evals/customer_support.yaml --config examples/customer_support/agentapp.yaml` — 4/4 pass
- [ ] `agentapp eval run examples/research_assistant/evals/research_assistant.yaml --config examples/research_assistant/agentapp.yaml` — 4/4 pass
- [ ] `tests/unit/test_import_boundaries.py` — all import boundary tests pass
- [ ] Optional dependencies behavior verified (openai-agents not installed → clear error)

## Documentation

- [ ] `README.md` updated with Routing Policy section
- [ ] `README.md` updated with Workflow Trace section
- [ ] `README.md` current limitations reflect v0.3.0 state
- [ ] `README.md` roadmap updated
- [ ] `CHANGELOG.md` contains `0.3.0` section
- [ ] `docs/evals.md` exists and covers all eval assertion types
- [ ] `examples/customer_support/README.md` updated for multi-agent handoff
- [ ] `examples/research_assistant/README.md` created

## Code quality

- [ ] `pyproject.toml` version set to `0.3.0`
- [ ] No dead code or unused imports in new files
- [ ] All new public APIs have docstrings

## Known limitations documented

- [ ] DAG workflows not implemented
- [ ] Routing is keyword/regex/default, not semantic LLM routing
- [ ] OpenAI backend not deeply integrated with multi-agent / tool interception
- [ ] Orchestrator runs specialists serially
- [ ] Resume is not a real OpenAI RunState resume
- [ ] DryRunBackend uses heuristic matching, not real LLM reasoning
