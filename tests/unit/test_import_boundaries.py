"""Tests for import boundaries — ensure optional deps are truly optional."""

from __future__ import annotations

import pathlib
import sys


class TestImportBoundaries:
    """Verify that the package can be imported without optional dependencies."""

    def test_import_agent_app_core(self):
        """import agent_app works without openai-agents or fastapi."""
        # Should not raise — no optional deps needed.
        import agent_app  # noqa: F401

    def test_public_api_accessible(self):
        """All public names are accessible from agent_app top-level."""
        from agent_app import (  # noqa: F401
            AgentApp,
            AgentSpec,
            AppRunResult,
            RunContext,
            ToolSpec,
            Workflow,
            tool,
        )

    def test_core_no_adapter_imports(self):
        """Core modules must not import adapters (openai-agents or fastapi)."""
        import ast
        import pathlib

        core_dirs = [
            pathlib.Path(__file__).parent.parent.parent / "agent_app" / "core",
            pathlib.Path(__file__).parent.parent.parent / "agent_app" / "registry",
            pathlib.Path(__file__).parent.parent.parent / "agent_app" / "config",
            pathlib.Path(__file__).parent.parent.parent / "agent_app" / "governance",
            pathlib.Path(__file__).parent.parent.parent / "agent_app" / "runtime",
            pathlib.Path(__file__).parent.parent.parent / "agent_app" / "tools",
            pathlib.Path(__file__).parent.parent.parent / "agent_app" / "evals",
        ]

        forbidden = {"openai.agents", "fastapi", "uvicorn"}

        for core_dir in core_dirs:
            for py_file in core_dir.rglob("*.py"):
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if any(alias.name.startswith(f) for f in forbidden):
                                raise AssertionError(
                                    f"{py_file} imports forbidden module '{alias.name}'"
                                )
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and any(
                            node.module.startswith(f) for f in forbidden
                        ):
                            raise AssertionError(
                                f"{py_file} imports from forbidden module '{node.module}'"
                            )

    def test_openai_agents_not_required(self):
        """openai-agents must not be in base dependencies."""
        import tomllib

        pyproject = pathlib.Path(
            pathlib.Path(__file__).parent.parent.parent / "pyproject.toml"
        )
        data = tomllib.loads(pyproject.read_text())
        deps = data["project"]["dependencies"]
        assert not any("openai" in d.lower() for d in deps), (
            "openai-agents must not be in base dependencies"
        )

    def test_fastapi_not_required(self):
        """fastapi must not be in base dependencies."""
        import tomllib

        pyproject = pathlib.Path(
            pathlib.Path(__file__).parent.parent.parent / "pyproject.toml"
        )
        data = tomllib.loads(pyproject.read_text())
        deps = data["project"]["dependencies"]
        assert not any("fastapi" in d.lower() for d in deps), (
            "fastapi must not be in base dependencies"
        )

    def test_openai_in_optional_deps(self):
        """openai-agents should be listed under [project.optional-dependencies]."""
        import tomllib

        pyproject = pathlib.Path(
            pathlib.Path(__file__).parent.parent.parent / "pyproject.toml"
        )
        data = tomllib.loads(pyproject.read_text())
        opt = data["project"].get("optional-dependencies", {})
        assert "openai" in opt or "openai-agents" in str(opt), (
            "openai-agents should be in optional-dependencies"
        )

    def test_fastapi_in_optional_deps(self):
        """fastapi should be listed under [project.optional-dependencies]."""
        import tomllib

        pyproject = pathlib.Path(
            pathlib.Path(__file__).parent.parent.parent / "pyproject.toml"
        )
        data = tomllib.loads(pyproject.read_text())
        opt = data["project"].get("optional-dependencies", {})
        assert "api" in opt or "fastapi" in str(opt), (
            "fastapi should be in optional-dependencies"
        )

    def test_typing_extensions_in_deps(self):
        """typing-extensions should be in base dependencies for Protocol support."""
        import tomllib

        pyproject = pathlib.Path(
            pathlib.Path(__file__).parent.parent.parent / "pyproject.toml"
        )
        data = tomllib.loads(pyproject.read_text())
        deps = data["project"]["dependencies"]
        assert any("typing-extensions" in d for d in deps), (
            "typing-extensions should be in base dependencies"
        )
