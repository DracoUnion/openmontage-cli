"""Single-command automation runner.

Implements ``openmontage-cli make "<request>"``: spins up the LLM orchestrator
and drives a production to completion (or as far as the gate policy allows).
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Optional

from . import config, bridge
from .gates import GatePolicy


def parse_duration(raw: Optional[str]) -> Optional[int]:
    """Parse '45s'/'60s'/plain int into seconds. Returns None if absent/invalid."""
    if not raw:
        return None
    m = re.match(r"^\s*(\d+)\s*(?:s|sec|seconds)?\s*$", raw, re.I)
    if not m:
        return None
    return int(m.group(1))


@dataclass
class MakeResult:
    ok: bool
    message: str
    project_id: str = ""
    output_path: str = ""
    turns: int = 0

    def __bool__(self) -> bool:
        return self.ok


def _make_project_id(request: str, overrides: Optional[str]) -> str:
    """Derive a kebab-case project id from the request, unless overridden."""
    if overrides:
        return overrides
    slug = re.sub(r"[^a-z0-9]+", "-", request.lower()).strip("-")[:60]
    return slug or "production"


def make(
    request: str,
    *,
    pipeline: str = "animated-explainer",
    duration: Optional[int] = None,
    title: Optional[str] = None,
    project: Optional[str] = None,
    plan_only: bool = False,
    yes: bool = False,
    model: Optional[str] = None,
    om_root: Optional[str] = None,
) -> MakeResult:
    """Run a single-command production. Returns a rich result object.

    Args:
        request: natural-language video brief.
        pipeline: pipeline manifest name.
        duration: target duration in seconds (optional).
        plan_only: stop after planning; never reach an asset/approval gate.
        yes: auto-approve every gate (full autonomous run).
        model: orchestrator model id (defaults to config).
        om_root: retained for CLI compatibility; paths use the installed package.
    """

    if om_root:
        # Retained for CLI compatibility; package paths are resolved internally.
        pass

    # API keys are read from the process environment (see config.api_key()).
    if not config.has_api_key():
        return MakeResult(
            False,
            "OPENAI_API_KEY is not set. Set it in .env or the environment to "
            "run the LLM orchestrator.",
        )

    plan_only = plan_only or False
    policy = GatePolicy(yes=yes, plan_only=plan_only)
    proj = _make_project_id(request, project)
    effective_title = title or (request.capitalize()[:80])

    helper = _build_helper(pipeline, duration, plan_only, yes)

    from .llm.orchestrator import Orchestrator

    orch = Orchestrator(gate_policy=policy, model=model or config.model())

    try:
        summary = orch.run(request, helper_hint=helper)
    except Exception as exc:
        # Surface API / network errors cleanly instead of a raw traceback.
        import openai as _openai
        if isinstance(exc, _openai.APIConnectionError):
            return MakeResult(
                False,
                "Could not reach the OpenAI API (network or proxy error). "
                "Check your connection / OPENAI_BASE_URL and retry.",
                project_id=proj,
            )
        if isinstance(exc, _openai.AuthenticationError):
            return MakeResult(
                False,
                "OPENAI_API_KEY was rejected by the API. Check that the key "
                "is valid and retry.",
                project_id=proj,
            )
        if isinstance(exc, _openai.APIError):
            return MakeResult(
                False,
                f"OpenAI API error: {exc}",
                project_id=proj,
            )
        return MakeResult(False, f"Orchestrator error: {type(exc).__name__}: {exc}",
                          project_id=proj)

    # Plan-only: surface the plan and stop (nothing rendered).
    if plan_only and not summary.finalized:
        return MakeResult(
            True,
            "Plan phase complete (plan-only). Run without --plan-only to "
            "produce the video." + (f" {summary.finalized_message}" if summary.finalized_message else ""),
            project_id=proj,
            turns=summary.turns,
        )

    if not summary.finalized:
        return MakeResult(
            False,
            summary.finalized_message or "Production did not complete.",
            project_id=proj,
            turns=summary.turns,
        )

    return MakeResult(
        True,
        "Production complete." + (f" {summary.finalized_message}" if summary.finalized_message else ""),
        project_id=proj,
        turns=summary.turns,
    )


def _build_helper(pipeline: str, duration: Optional[int], plan_only: bool, yes: bool) -> str:
    parts = [f"TARGET PIPELINE: {pipeline}"]
    if duration:
        parts.append(f"TARGET DURATION: {duration} seconds")
    if plan_only:
        parts.append(
            "MODE: plan-only — research and produce the production plan "
            "(concepts, tool path, itemized cost, per-stage plan) and STOP. "
            "Do not generate assets or render."
        )
    else:
        parts.append(
            "MODE: full production" + (" (approval gates auto-approved)" if yes else "")
        )
    return "\n".join(parts)
