"""Path helpers for the CLI driver."""

from __future__ import annotations

import sys
from pathlib import Path


def project_root() -> Path:
    """Return the OpenMontage working-copy root (the self-contained copy)."""
    return Path(__file__).resolve().parent.parent.parent


def ensure_root_in_path() -> None:
    """Put the project root on sys.path so top-level packages (tools, lib,
    schemas, backlot) import correctly."""
    root = str(project_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def projects_dir() -> Path:
    """Directory where production workspaces live (gitignored)."""
    return project_root() / "projects"


def skill_path(rel: str) -> Path:
    """Resolve a skill file path (e.g. 'pipelines/explainer/script-director')
    against the package skills/ tree."""
    return project_root() / "openmontage" / "skills" / f"{rel}.md"


def pipeline_path(name: str) -> Path:
    return project_root() / "openmontage" / "pipeline_defs" / f"{name}.yaml"


__all__ = [
    "project_root",
    "ensure_root_in_path",
    "projects_dir",
    "skill_path",
    "pipeline_path",
]
