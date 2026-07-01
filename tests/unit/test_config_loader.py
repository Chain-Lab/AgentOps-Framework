"""Tests for config loader and schema."""

import os
import tempfile

import pytest
import yaml

from agent_app.config.loader import build_app, load_config
from agent_app.config.schema import AppConfig


class TestConfigSchema:
    def test_minimal_config(self) -> None:
        raw = {"agents": [{"name": "bot", "instructions": "help"}]}
        cfg = AppConfig(**raw)
        assert len(cfg.agents) == 1
        assert cfg.agents[0].name == "bot"

    def test_duplicate_agent_names_raises(self) -> None:
        raw = {
            "agents": [
                {"name": "bot", "instructions": "help"},
                {"name": "bot", "instructions": "help2"},
            ]
        }
        with pytest.raises(ValueError, match="Duplicate"):
            AppConfig(**raw)

    def test_tool_config_defaults(self) -> None:
        from agent_app.config.schema import ToolConfig

        tc = ToolConfig(name="order.query")
        assert tc.type == "function"
        assert tc.risk_level == "low"
        assert tc.requires_approval is False


class TestConfigLoader:
    def test_load_yaml(self, tmp_path) -> None:
        yaml_content = """
agents:
  - name: support
    description: Support agent
    model: gpt-4o
    instructions: inline prompt
    tools:
      - order.query
"""
        p = tmp_path / "agentapp.yaml"
        p.write_text(yaml_content)
        cfg = load_config(str(p))
        assert len(cfg.agents) == 1
        assert cfg.agents[0].name == "support"
        assert cfg.agents[0].model == "gpt-4o"
        assert "order.query" in cfg.agents[0].tools

    def test_load_yaml_dict_keyed(self, tmp_path) -> None:
        """YAML may use dict keyed by agent name (plan document format)."""
        yaml_content = """
agents:
  support:
    description: Support agent
    model: gpt-4o
    instructions: inline prompt
    tools:
      - order.query
  billing:
    description: Billing agent
    instructions: help with bills
"""
        p = tmp_path / "agentapp.yaml"
        p.write_text(yaml_content)
        cfg = load_config(str(p))
        assert len(cfg.agents) == 2
        names = {a.name for a in cfg.agents}
        assert names == {"support", "billing"}

    def test_load_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/agentapp.yaml")

    def test_load_with_prompt_file(self, tmp_path) -> None:
        # Create a prompt file
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "support.md").write_text("You are a support agent.")

        yaml_content = f"""
agents:
  - name: support
    instructions: ./prompts/support.md
"""
        p = tmp_path / "agentapp.yaml"
        p.write_text(yaml_content)
        cfg = load_config(str(p))
        assert cfg.agents[0].instructions == "./prompts/support.md"
        # The loader's _load_prompt resolves the file; schema just stores the string.

    def test_load_workflows(self, tmp_path) -> None:
        yaml_content = """
agents:
  - name: triage
    instructions: Route users.

workflows:
  customer_support:
    type: handoff
    entry: triage
    agents:
      - billing
      - refund
"""
        p = tmp_path / "agentapp.yaml"
        p.write_text(yaml_content)
        cfg = load_config(str(p))
        assert "customer_support" in cfg.workflows
        assert cfg.workflows["customer_support"]["type"] == "handoff"


def test_build_app_wires_webhook_signature_service(tmp_path):
    """Regression test: webhook_signing.enabled=true must actually construct
    a working FederationWebhookSignatureService, not silently no-op.

    Prior to the Phase 65 fix, this raised ModuleNotFoundError (wrong import
    path) then TypeError (invalid constructor kwargs), both swallowed by a
    bare `except Exception: pass` in loader.py.
    """
    config_path = tmp_path / "agentapp.yaml"
    config_path.write_text("""
app:
  name: test-app
governance:
  policy_release:
    rollout_federation:
      enabled: true
      notifications:
        enabled: true
        webhook_signing:
          enabled: true
          active_key_id: test-key
          keys:
            test-key: test-secret-value
          timestamp_tolerance_seconds: 300
""")
    app = build_app(str(config_path))
    service = app.federation_webhook_signature_service
    assert service is not None
    headers = service.sign("test-body")
    assert headers["X-AgentApp-Signature"].startswith("v1=")
    assert headers["X-AgentApp-Key-ID"] == "test-key"


def _patch_rate_limiter_factory(monkeypatch):
    """Intercept create_approval_rate_limiter calls; returns the list of kwargs each call received."""
    calls = []

    def fake_create_approval_rate_limiter(**kwargs):
        calls.append(kwargs)
        from agent_app.runtime.approval_rate_limit import InMemoryApprovalRateLimiter
        return InMemoryApprovalRateLimiter(
            max_requests=kwargs["max_requests"], window_seconds=kwargs["window_seconds"]
        )

    import agent_app.runtime.approval_rate_limit as rl_module
    monkeypatch.setattr(rl_module, "create_approval_rate_limiter", fake_create_approval_rate_limiter)
    return calls


def test_build_app_wires_sqlite_rate_limiter(tmp_path, monkeypatch):
    calls = _patch_rate_limiter_factory(monkeypatch)

    config_path = tmp_path / "agentapp.yaml"
    db_path = str(tmp_path / "rl.db")
    config_path.write_text(f"""
app:
  name: test-app
governance:
  rate_limit:
    max_requests: 5
    window_seconds: 60
    backend: sqlite
    db_path: {db_path}
""")
    build_app(str(config_path))

    assert len(calls) == 1
    assert calls[0]["backend"] == "sqlite"
    assert calls[0]["max_requests"] == 5
    assert calls[0]["window_seconds"] == 60
    assert calls[0]["db_path"] == db_path


def test_build_app_wires_memory_rate_limiter_by_default(tmp_path, monkeypatch):
    calls = _patch_rate_limiter_factory(monkeypatch)

    config_path = tmp_path / "agentapp.yaml"
    config_path.write_text("""
app:
  name: test-app
governance:
  rate_limit:
    max_requests: 5
    window_seconds: 60
""")
    build_app(str(config_path))

    assert len(calls) == 1
    assert calls[0]["backend"] == "memory"


def test_build_app_wires_otel_trace_collector(tmp_path):
    from agent_app.observability.otel import OtelTraceCollector

    if not _otel_installed():
        pytest.skip("opentelemetry not installed")

    config_path = tmp_path / "agentapp.yaml"
    config_path.write_text("""
app:
  name: test-app
observability:
  tracing:
    type: otel
    otel_service_name: test-app
    otel_exporter: console
""")
    app = build_app(str(config_path))
    assert isinstance(app.trace_collector, OtelTraceCollector)


def test_build_app_raises_clear_error_when_otel_type_requested_without_package(tmp_path, monkeypatch):
    """Regression: misconfiguration must surface at build_app() time, not
    fail silently or crash deep inside a run."""
    import builtins
    import sys

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("opentelemetry"):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    for mod in list(sys.modules):
        if mod.startswith("opentelemetry"):
            monkeypatch.delitem(sys.modules, mod, raising=False)

    config_path = tmp_path / "agentapp.yaml"
    config_path.write_text("""
app:
  name: test-app
observability:
  tracing:
    type: otel
""")
    with pytest.raises(RuntimeError, match="OpenTelemetry"):
        build_app(str(config_path))


def _otel_installed() -> bool:
    try:
        import opentelemetry.sdk.trace  # noqa: F401
        return True
    except ImportError:
        return False
