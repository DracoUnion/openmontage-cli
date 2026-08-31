"""Single-command automation runner.

Implements ``openmontage-cli make "<request>"``: spins up the LLM orchestrator
and drives a production to completion (or as far as the gate policy allows).
"""

from __future__ import annotations

import json
import re
import sys
import traceback
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


def make(args) -> MakeResult:
    """Run a single-command production. Accepts the CLI args Namespace directly
    (request, pipeline, duration, title, project, plan_only, yes, model, om_root)
    so the command handlers don't have to unpack individual attributes.

    Returns a rich result object.
    """
    request = args.request
    pipeline = getattr(args, "pipeline", "animated-explainer")
    duration = getattr(args, "duration", None)
    title = getattr(args, "title", None)
    project = getattr(args, "project", None)
    plan_only = bool(getattr(args, "plan_only", False))
    yes = bool(getattr(args, "yes", False))
    model = getattr(args, "model", None)
    om_root = getattr(args, "om_root", None)

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

    policy = GatePolicy(yes=yes, plan_only=plan_only)
    proj = _make_project_id(request, project)
    effective_title = title or (request.capitalize()[:80])

    helper = _build_helper(pipeline, duration, plan_only, yes)

    from .llm.orchestrator import Orchestrator

    orch = Orchestrator(args, gate_policy=policy)

    try:
        summary = orch.run(request, helper_hint=helper)
    except Exception as exc:
        # Surface API / network errors cleanly instead of a raw traceback.
        return MakeResult(False, f"Orchestrator error: {traceback.format_exc()}",
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
