#!/usr/bin/env python3
"""Check that repository metadata, versions, and required documentation agree.

This script is deliberately standard-library only so it can run in a fresh
checkout, in CI, and inside a restricted AI-agent sandbox. ``make verify``
runs it, ``make check`` (and therefore CI) depends on it, and
``tests/test_repo_metadata.py`` asserts it stays green.

It fails when:

* the package version in ``app/__init__.py`` differs from ``pyproject.toml``;
* the newest ``CHANGELOG.md`` release heading differs from that version;
* a required project, GitHub, or assistant-guidance file is missing.

It intentionally does not check formatting or content quality; Ruff, pytest,
and review own that.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_FILES = (
    # Project and community surface
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    "Makefile",
    "pyproject.toml",
    ".env.example",
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    ".pre-commit-config.yaml",
    ".devcontainer/devcontainer.json",
    ".vscode/extensions.json",
    ".vscode/settings.json",
    # Assistant / vibe-coding guidance
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    ".cursor/rules/website-health-monitor.mdc",
    ".windsurf/rules/website-health-monitor.md",
    ".github/copilot-instructions.md",
    # GitHub configuration
    ".github/CODEOWNERS",
    ".github/dependabot.yml",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/workflows/ci.yml",
    ".github/workflows/dependency-review.yml",
    # Product documentation and deployment
    "deploy/website-health-monitor.service",
    "docs/architecture.md",
    "docs/ai-assisted-development.md",
    "docs/project-plan.md",
    "docs/release-checklist.md",
    "docs/runbook.md",
)

VERSION_LINE = re.compile(r'^__version__\s*=\s*"(?P<version>[^"]+)"', re.MULTILINE)
CHANGELOG_HEADING = re.compile(r"^##\s+\[(?P<version>\d+\.\d+\.\d+)\]", re.MULTILINE)


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def package_version() -> str:
    match = VERSION_LINE.search(_read("app/__init__.py"))
    if match is None:
        raise ValueError("app/__init__.py does not define __version__")
    return match.group("version")


def project_version() -> str:
    metadata = tomllib.loads(_read("pyproject.toml"))
    version = metadata.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("pyproject.toml does not define [project].version")
    return version


def changelog_version() -> str:
    match = CHANGELOG_HEADING.search(_read("CHANGELOG.md"))
    if match is None:
        raise ValueError("CHANGELOG.md has no '## [x.y.z]' release heading")
    return match.group("version")


def missing_required_files() -> list[str]:
    return sorted(path for path in REQUIRED_FILES if not (ROOT / path).exists())


def check() -> list[str]:
    """Return every consistency problem; an empty list means the repository is consistent."""
    problems = [f"missing required file: {path}" for path in missing_required_files()]
    versions = {
        "app/__init__.py": package_version(),
        "pyproject.toml": project_version(),
        "CHANGELOG.md": changelog_version(),
    }
    reference = versions["app/__init__.py"]
    for source, version in versions.items():
        if version != reference:
            problems.append(f"version mismatch: {source} declares {version}, expected {reference}")
    return problems


def main() -> int:
    try:
        problems = check()
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"repository consistency check failed to run: {exc}", file=sys.stderr)
        return 1
    if problems:
        print("repository consistency check FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(
        f"repository consistency check passed: version {package_version()}, "
        f"{len(REQUIRED_FILES)} required files present"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
