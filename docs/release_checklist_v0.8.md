# v0.8.0 Release Checklist

## Pre-Release Verification

- [ ] `python -m pytest` — 394 tests pass, 5 skipped
- [ ] `pytest -W default` — only `StarletteDeprecationWarning` from `fastapi/testclient.py` (dependency, low risk)
- [ ] `agentapp eval run examples/customer_support/evals/customer_support.yaml --config examples/customer_support/agentapp.yaml` — 4/4 pass
- [ ] `agentapp eval run examples/research_assistant/evals/research_assistant.yaml --config examples/research_assistant/agentapp.yaml` — 4/4 pass
- [ ] `tests/unit/test_openai_multi_agent.py` — 23 Phase 11 tests pass
- [ ] `tests/unit/test_native_hitl.py` — 24 Phase 10 tests pass
- [ ] `tests/unit/test_import_boundaries.py` — core/registry/config do not import openai-agents
- [ ] Optional dependencies verified: `pip install -e ".[openai]"` works

## Documentation

- [ ] `README.md` — OpenAI Multi-Agent Workflows section present
- [ ] `README.md` — Roadmap updated (v0.8 entries)
- [ ] `README.md` — Current limitations updated
- [ ] `docs/openai_backend.md` — Multi-Agent Workflows section (handoff, orchestrator, delegation, governance, limitations)
- [ ] `docs/openai_backend.md` — HITL modes documented (Phase 10.5)
- [ ] `docs/openai_backend.md` — `backend_state` and RunState documented
- [ ] `examples/openai_multi_agent/README.md` — exists with handoff/orchestrator examples
- [ ] `examples/openai_multi_agent/customer_support_handoff.yaml` — exists
- [ ] `examples/openai_multi_agent/research_assistant_orchestrator.yaml` — exists

## Smoke Tests

- [ ] `tests/smoke/test_openai_native_hitl_smoke.py` — 3 tests, marked `openai_smoke`, skipped by default
- [ ] `tests/smoke/test_openai_multi_agent_smoke.py` — 2 tests, marked `openai_smoke`, skipped by default
- [ ] `pyproject.toml` — `openai_smoke` marker registered
- [ ] Default `pytest` does not run smoke tests
- [ ] Smoke test run documented: `OPENAI_API_KEY=... python -m pytest -m openai_smoke`

## Changelog

- [ ] `CHANGELOG.md` — `## 0.8.0` section with all additions and known limitations

## Package Metadata

- [ ] `pyproject.toml` — `version = "0.8.0"`
- [ ] `pyproject.toml` — pytest markers include `openai_smoke`

## Git

- [ ] `git add .`
- [ ] `git commit -m "release: v0.8.0 openai multi-agent backend"`
- [ ] `git tag v0.8.0`
