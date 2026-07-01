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
