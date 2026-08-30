"""Configuration and environment for the OpenMontage CLI driver.

The CLI is a self-contained copy of OpenMontage. This module centralises:

- resolving the project root (where tools/, lib/, openmontage/pipeline_defs/, skills/ live)
- reading orchestrator / LLM settings with sensible defaults
"""

from __future__ import annotations

import os
from typing import Optional

from .utils.paths import project_root, ensure_root_in_path

# Re-exported helpers so the rest of the package has a single import point.
ROOT = project_root()

# Default cost / iteration knobs for the orchestrator loop.
DEFAULT_MODEL = os.environ.get("OPENMONTAGE_MODEL", "gpt-4o")
DEFAULT_MAX_LLM_TURNS = int(os.environ.get("OPENMONTAGE_MAX_TURNS", "80"))
DEFAULT_BUDGET_USD = float(os.environ.get("OPENMONTAGE_BUDGET_USD", "10.0"))


def api_key() -> Optional[str]:
    """Return the orchestrator LLM API key, if configured."""
    return os.environ.get("OPENAI_API_KEY")


def has_api_key() -> bool:
    return bool(api_key())


def model() -> str:
    return os.environ.get("OPENMONTAGE_MODEL", DEFAULT_MODEL)


def max_turns() -> int:
    return int(os.environ.get("OPENMONTAGE_MAX_TURNS", str(DEFAULT_MAX_LLM_TURNS)))


def budget_usd() -> float:
    return float(os.environ.get("OPENMONTAGE_BUDGET_USD", str(DEFAULT_BUDGET_USD)))


__all__ = [
    "ROOT",
    "api_key",
    "has_api_key",
    "model",
    "max_turns",
    "budget_usd",
    "project_root",
    "ensure_root_in_path",
]
