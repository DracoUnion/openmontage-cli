"""Approval-gate policy for single-command automation mode.

OpenMontage stages can declare ``human_approval_default: true`` in their
manifest. The min library enforces this: such a stage can only be written
``completed`` with ``human_approved=True`` (a GATE VIOLATION otherwise).

The CLI is non-interactive (single-command automation), so it can never pop a
prompt mid-run. This module turns that gate into a small, explicit policy:

* ``--yes``    -> auto-approve every gate (full autonomous run).
* ``--plan-only`` -> stop after the production plan is formed; never reach
                     an asset gate.
* ``(default)`` -> stop at the FIRST gated stage: print what is pending and
                   how to continue, then exit non-zero. The run resumes from
                   its checkpoint on a later invocation with ``--resume
                   --yes``.

A resolved decision is represented by ``GateDecision``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class Resolution(str):
    """How an encountered gate should be handled."""

    APPROVE = "approve"       # write human_approved=True and continue
    PLAN_ONLY = "plan_only"   # this run must not reach a gate
    PAUSE = "pause"           # cannot auto-approve; stop and tell the user


@dataclass
class GateDecision:
    resolution: Resolution
    reason: str = ""

    @property
    def approve(self) -> bool:
        return self.resolution == Resolution.APPROVE


class GatePolicy:
    """Holds the flags that decide how gates are resolved."""

    def __init__(self, *, yes: bool = False, plan_only: bool = False) -> None:
        self.yes = yes
        self.plan_only = plan_only

    def for_stage(self, stage: str, gated: bool) -> GateDecision:
        """Decide what to do when the orchestrator reaches ``stage``."""
        if self.plan_only:
            return GateDecision(
                Resolution.PLAN_ONLY,
                "plan-only mode: stopping before asset generation",
            )
        if gated and not self.yes:
            return GateDecision(
                Resolution.PAUSE,
                f"stage '{stage}' requires human approval; pass --yes to "
                f"auto-approve, or re-run with --resume --yes to continue "
                f"from this checkpoint",
            )
        return GateDecision(
            Resolution.APPROVE,
            "auto-approved" if gated else "stage is not gated",
        )

    def resolve(self, stage: str, gated: bool) -> GateDecision:
        return self.for_stage(stage, gated)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"GatePolicy(yes={self.yes}, plan_only={self.plan_only})"
