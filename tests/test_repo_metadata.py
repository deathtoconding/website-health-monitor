"""Keep repository metadata, documentation inventory, and version strings aligned.

These tests protect the human/AI contributor workflow: a version bump or a moved
document must not silently leave the repository inconsistent.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

TOOL_PATH = Path(__file__).resolve().parent.parent / "tools" / "check_repo_consistency.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("check_repo_consistency", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repository_metadata_is_consistent() -> None:
    tool = load_tool()
    assert tool.check() == []
    assert tool.main() == 0


def test_package_and_project_versions_match() -> None:
    tool = load_tool()
    assert tool.package_version() == tool.project_version() == tool.changelog_version()


def test_required_documentation_and_agent_guidance_exist() -> None:
    tool = load_tool()
    root = tool.ROOT
    assert tool.missing_required_files() == []
    for relative_path in (
        "README.md",
        "CONTRIBUTING.md",
        "AGENTS.md",
        ".github/copilot-instructions.md",
        "docs/ai-assisted-development.md",
    ):
        assert (root / relative_path).is_file()
