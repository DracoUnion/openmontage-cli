"""The `make` / `plan` / `run` / `resume` command handlers.

`make` is the core single-command automation entry. `plan` is shorthand for
`make --plan-only`. `run`/`resume` are aliases that continue from an existing
checkpoint (resume). All of them delegate to runner.make().
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from .runner import MakeResult, make


def _print_result(res: MakeResult) -> None:
    if res.project_id:
        print(f"\nPROJECT: {res.project_id}")
    if res.output_path:
        print(f"DELIVERABLE: {res.output_path}")
    print(res.message)
    if res.turns:
        print(f"\nturns: {res.turns}")


def cmd_make(args: argparse.Namespace) -> int:
    res = make(
        args.request,
        pipeline=args.pipeline,
        duration=args.duration,
        title=args.title,
        project=args.project,
        plan_only=args.plan_only,
        yes=args.yes,
        model=args.model,
        om_root=getattr(args, "om_root", None),
    )
    _print_result(res)
    return 0 if res.ok else 1


def cmd_plan(args: argparse.Namespace) -> int:
    res = make(
        args.request,
        pipeline=args.pipeline,
        duration=args.duration,
        title=args.title,
        project=args.project,
        plan_only=True,
        yes=True,
        model=args.model,
        om_root=getattr(args, "om_root", None),
    )
    _print_result(res)
    return 0 if res.ok else 1


def cmd_run(args: argparse.Namespace) -> int:
    # `run` is a full production; `resume` continues from a checkpoint (the
    # orchestrator picks up via get_next_stage) and auto-approves outstanding
    # gates when --yes is given.
    res = make(
        args.request,
        pipeline=args.pipeline,
        duration=args.duration,
        title=args.title,
        project=args.project,
        plan_only=False,
        yes=args.yes,
        model=args.model,
        om_root=getattr(args, "om_root", None),
    )
    _print_result(res)
    return 0 if res.ok else 1


def cmd_resume(args: argparse.Namespace) -> int:
    # Same execution path as run; keep the alias for clarity. A real resume
    # requires the project to already exist — the orchestrator's
    # checkpoint_next handles continuation.
    return cmd_run(args)
