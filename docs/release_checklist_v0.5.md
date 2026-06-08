# v0.5.0 Release Checklist

## Verification Steps

- [ ] `python -m pytest` — all tests pass
- [ ] `python -m pytest tests/unit/test_openai_backend.py -v` — 69 OpenAI backend tests pass
- [ ] `agentapp eval run examples/customer_support/evals/customer_support.yaml --config examples/customer_support/agentapp.yaml` — 4/4 pass
- [ ] `agentapp eval run examples/research_assistant/evals/research_assistant.yaml --config examples/research_assistant/agentapp.yaml` — 4/4 pass
- [ ] `import agent_app` works without `openai-agents` installed
- [ ] `OpenAIAgentsBackend()` raises clear RuntimeError when `openai-agents` not installed
- [ ] `DryRunBackend` behavior unchanged
- [ ] No regressions in existing 230+ tests

## Documentation

- [ ] `CHANGELOG.md` includes `## 0.5.0` section
- [ ] `README.md` OpenAI Backend section updated with governance wrapper details
- [ ] `docs/openai_backend.md` exists with design reference
- [ ] `examples/openai_basic/README.md` documents low-risk and high-risk tool behavior
- [ ] `examples/openai_basic/tools.py` includes both low-risk and high-risk tools
- [ ] Current limitations clearly documented

## Version

- [ ] `pyproject.toml` version = `0.5.0`

## Known Limitations (must be documented)

- [ ] Real OpenAI RunState pause/resume not implemented
- [ ] approval_required returned as tool output, not real RunState pause
- [ ] AppRunResult.interrupted detection depends on SDK result structure
- [ ] Multi-agent OpenAI backend integration deferred
- [ ] DryRunBackend recommended for eval/governance regression testing
