# v0.1.0 Release Checklist

Use this checklist to verify the release before tagging `v0.1.0`.

## Automated checks

- [ ] `python -m pytest` — all tests pass (target: 0 failures)
- [ ] `ruff check agent_app/` — no lint errors
- [ ] `mypy agent_app/` — no type errors (strict mode)

## Manual checks

- [ ] `agentapp --help` — help text renders correctly
- [ ] `agentapp eval --help` — eval subcommand help renders
- [ ] `agentapp eval run --help` — run subcommand help renders
- [ ] `agentapp eval run <nonexistent> --config <config>` — exits non-zero with error
- [ ] `agentapp eval run evals/customer_support.yaml --config examples/customer_support/agentapp.yaml` — exits 0 (4/4 pass)

## Import boundary checks

- [ ] `python -c "import agent_app"` works without `openai-agents` installed
- [ ] `python -c "import agent_app"` works without `fastapi` installed
- [ ] `python -c "from agent_app.adapters.openai_agents import OpenAIAgentsBackend"` gives clear error when `openai-agents` is not installed
- [ ] `python -c "from agent_app.adapters.fastapi import create_fastapi_app"` gives clear error when `fastapi` is not installed
- [ ] `pytest tests/unit/test_import_boundaries.py` — all pass

## Example checks

- [ ] `python examples/customer_support/main.py` — runs without error
- [ ] `agentapp eval run examples/customer_support/evals/customer_support.yaml --config examples/customer_support/agentapp.yaml` — 4/4 pass

## Documentation checks

- [ ] README.md is up to date
- [ ] CHANGELOG.md is complete
- [ ] `examples/customer_support/README.md` exists and is accurate
- [ ] Known limitations are documented in README

## Package checks

- [ ] `pyproject.toml` version is `0.1.0`
- [ ] `pyproject.toml` `[project]` section has: name, version, description, readme, requires-python, authors
- [ ] `pyproject.toml` `[project.dependencies]` has: `pydantic>=2`, `pyyaml>=6`, `typing-extensions>=4`
- [ ] `pyproject.toml` `[project.optional-dependencies]` has: `openai`, `api`, `dev`, `all`
- [ ] `pyproject.toml` entry point `agentapp = "agent_app.cli:main"` works
- [ ] `pip install -e ".[dev]"` succeeds
- [ ] `pip install -e ".[api]"` succeeds (if fastapi/uvicorn available)
- [ ] `pip install -e ".[openai]"` succeeds (if openai-agents available)

## Release steps

1. Run all checks above and fix any failures
2. Update CHANGELOG.md if any last-minute changes were made
3. Commit: `git commit -am "chore: release v0.1.0"`
4. Tag: `git tag v0.1.0`
5. Push: `git push && git push --tags`
