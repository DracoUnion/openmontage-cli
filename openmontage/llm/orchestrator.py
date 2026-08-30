"""Orchestrator: the tool-use loop that drives OpenMontage.

This is the replacement for "an AI coding assistant reading the skills in a
chat". It is an LLM tool-use loop:

    system prompt (AGENT_GUIDE contract) + user request
        -> model
        -> assistant tool calls
        -> bridge.call(name, args)  (the real OpenMontage functions)
        -> tool results back to the model
        -> repeat until the model calls finalize or we hit max turns

The loop is provider-agnostic: it only needs the small `turn()` shape from
the provider. Gate policy (yes / plan-only / pause) is enforced here, layered
on top of OpenMontage's own checkpoint gate rules.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .. import bridge
from ..gates import GatePolicy, Resolution
from .. import config
from . import openai as om_openai

MAX_TOOL_RESULT_CHARS = 12000


@dataclass
class RunSummary:
    finalized: bool = False
    finalized_message: str = ""
    turns: int = 0
    tool_calls: int = 0
    project_id: str = ""
    decisions: list[dict[str, Any]] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_user_prompt(request: str, helper_hint: str = "") -> str:
    """Assemble the orchestrator system prompt from the AGENT_GUIDE contract.

    This encodes the essential rules that used to live in the agent's head:
    Rule Zero (everything through a pipeline), read-the-director-skill-first,
    canonical artifacts per stage, checkpoint protocol with gate enforcement,
    decision-log discipline and quality review. A full copy of AGENT_GUIDE.md
    is too large to inline; we give the model the load-bearing rules plus a
    pointer to load_skill('meta/checkpoint-protocol') for detail.
    """
    return f"""You are the executive producer of OpenMontage, an agentic video
production system. You drive production through a documented pipeline using
the tools you are given. This is a NON-INTERACTIVE run: you cannot ask the user
mid-flight; make sound decisions yourself and record them in a decision log.

Follow these operating rules (the OpenMontage agent contract):

1. RULE ZERO: every video production goes through a pipeline. Do not improvise
   ad-hoc sequences. Select a pipeline (default animated-explainer), load its
   manifest, and work stage by stage.

2. MANDATORY PREFLIGHT: first call `preflight` to learn the machine's real
   capability envelope (which providers are configured). Plan around what is
   actually available.

3. STAGE DISCIPLINE: the pipeline has stages (e.g. research -> proposal ->
   script -> scene_plan -> assets -> edit -> compose -> publish). Before doing
   work in a stage, call `load_skill` with the stage's director skill path from
   the manifest (e.g. pipelines/explainer/script-director). The skill defines
   HOW to execute the stage and its quality bar. After the stage, write a
   checkpoint with the stage's canonical artifact(s).

4. GATES & CHECKPOINTS: after each stage, call `checkpoint_write` with the
   stage's canonical artifact. A stage whose manifest sets human_approval_default
   (you can check via `stage_gate_policy`) is GATED: write it as
   status='awaiting_human'. The CLI host resolves approval for you and feeds
   back whether you may complete it. Only write status='completed' when the
   host confirms approval (human_approved=true). Never skip a checkpoint.

5. DECISION LOG: every material creative/technical choice (provider, model,
   voice, music track, render runtime, any fallback) is a decision. Append it
   to a project-level `decision_log`, and include it in the proposal/checkpoint
   artifacts. If a locked choice changes mid-run, append a new entry reusing
   the same (category, subject) pair with the old one in rejected_because.

6. QUALITY GATES: after each stage, self-review against the manifest's
   review_focus. Do not present the final video unless it passes (exists,
   playable, matches the delivery promise, audio is sane).

7. WORKSPACE: initialise the project with `project_init`, then ALWAYS pass an
   explicit output_path under projects/<project_id>/ to every `run_tool` call.
   Assets written elsewhere violate the workspace contract.

8. COST & APPROVAL: respect budget. Announce paid generations (tool name,
   provider, model, reason) in your working text. Prefer the best available
   provider via selectors, applying fallbacks only when the primary is
   unavailable — and log any fallback as a decision.

9. RENDER: lock `render_runtime` at proposal (Remotion / HyperFrames / FFmpeg)
   and carry it through edit_decisions unchanged. Do not silently swap
   runtimes. For motion-required briefs, a still-image slideshow is forbidden.

Channel your working notes and decisions as plain text alongside tool calls.
At the end, call `finalize` with the project_id and the path to the rendered
deliverable. The request is:

USER REQUEST: {request}
{helper_hint}

Begin. Run preflight, select and load the pipeline, initialise the project, then execute stage by stage. Use finalize when the deliverable is ready.

Note that tool calls must be surrounded in "[tool]...[/tool]".
"""


class Orchestrator:
    def __init__(
        self,
        *,
        gate_policy: Optional[GatePolicy] = None,
        max_turns: Optional[int] = 50,
        model: Optional[str] = None,
        llm_call=None,
    ) -> None:
        self.gate_policy = gate_policy or GatePolicy(yes=True)
        self.max_turns = max_turns or config.max_turns()
        self.model = model
        self._llm_call = llm_call

    # -- gate override: the host may block a 'completed' write on a gated
    #    stage unless the CLI policy says approve. We enforce it at the bridge
    #    boundary *before* the LLM's own checkpoint_write call.

    def run(self, request: str, *, helper_hint: str = "") -> RunSummary:
        tool_defs = om_openai.build_openai_tools()
        tool_pmt = om_openai.TOOLCALL_PMT.replace('{tool_def}', json.dumps(tool_defs, ensure_ascii=False))
        msgs: list[dict[str, Any]] = [
            {"role": "system", 'content': tool_pmt},
            {"role": "user", "content": build_user_prompt(request, helper_hint)},
        ]
        summary = RunSummary()
        max_iter = self.max_turns
        for _ in range(max_iter):
            summary.turns += 1
            res = self._call_llm(msgs)
            tool_blocks = self._parse_toolcall(res)
            if not tool_blocks:
                # No tool call: the model stopped or is giving plain text. Treat
                # as a soft stop unless it already finalised.
                msgs.append({"role": "assistant", "content": res})
                msgs.append({
                    "role": "user",
                    "content": f"No tool calls found. Please surround tool calls in [tool]...[/tool]. And if you want to stop, call `finalize`.",
                })
                continue

            print(f'toolcall: {tool_blocks}')
            toolcall_res_list = []
            for tc in tool_blocks:
                summary.tool_calls += 1
                name = tc.get("tool", "")
                args = tc.get("parameters") or {}
                # finalize ends the run immediately.
                if name == "finalize":
                    summary.finalized = True
                    summary.finalized_message = args.get("message", "")
                    return summary
                result = self._dispatch(name, args, summary)
                # After a blocked gated stage, if the host paused (no --yes),
                # surface the pause and halt.
                if result.get("_gate_paused"):
                    summary.finalized_message = result.get("_gate_pause_msg", "")
                    return summary
                toolcall_res_list.append({
                    "id": tc.get("id", ""),
                    "result": _render_result(result),
                })
            
            print(f'toolcall res: {toolcall_res_list}')
            toolcall_res_str = json.dumps(toolcall_res_list, ensure_ascii=False)
            msgs.append({"role": "assistant", "content": res})
            msgs.append({
                "role": "user",
                "content": f"[tool-result]{toolcall_res_str}[/tool-result]",
            })
        if not summary.finalized:
            summary.finalized_message = (
                summary.finalized_message
                or f"Reached max turns ({max_iter}) without finalize."
            )
        return summary

    # -- LLM call: use llm/openai.py's call_llm (text protocol). Overridable
    #    seam so tests can script responses without hitting the network.

    def _call_llm(self, msgs: list[dict[str, Any]]) -> str:
        if self._llm_call is not None:
            return self._llm_call(msgs, self.model)
        return om_openai.call_llm(
            msgs, self.model,
            temp=0.3, top_p=0.95,
        )

    @staticmethod
    def _parse_toolcall(res: str) -> list[dict[str, Any]]:
        """Parse the first [tool]...[/tool] block in a response into a list of
        tool-call dicts (id / tool / parameters). Mirrors llm/openai.py."""
        import re
        m = re.search(r"\[tool\]([\s\S]+)\[/tool\]", res)
        if not m:
            return []
        try:
            blocks = json.loads(m.group(1))
        except json.JSONDecodeError:
            return []
        if not isinstance(blocks, list):
            return []
        return blocks

    # -- dispatch, with gate policy enforcement on checkpoint_write --------

    def _dispatch(self, name: str, args: dict[str, Any], summary: RunSummary) -> dict[str, Any]:
        if name == "checkpoint_write":
            return self._checkpoint_write_gated(args, summary)
        return bridge.call(name, args)

    def _checkpoint_write_gated(
        self, args: dict[str, Any], summary: RunSummary
    ) -> dict[str, Any]:
        """Enforce the CLI gate policy around bridge.checkpoint_write.

        OpenMontage itself enforces that a gated stage can't be 'completed'
        without human_approved=True. Here the host policy decides whether to
        grant that approval (--yes), pause, or — for plan-only — stop this
        run. We also relay the pause to the loop so it halts cleanly.
        """
        stage = args.get("stage", "")
        status = args.get("status", "")
        project_id = args.get("project_id", "")
        pipeline = args.get("pipeline")

        # Determine gated-ness from the manifest (authoritative).
        gated = False
        try:
            policy = bridge.stage_gate_policy(pipeline or "", stage)
            gated = bool(policy.get("human_approval_default"))
        except Exception:
            gated = args.get("human_approved") or False

        decision = self.gate_policy.resolve(stage, gated)

        if decision.resolution == Resolution.PLAN_ONLY:
            reflected = {
                "ok": True,
                "paused": True,
                "gate": stage,
                "note": "plan-only mode: not writing this gate. See planning output.",
            }
            # Complete nothing; halt the run.
            reflected["_gate_paused"] = True
            reflected["_gate_pause_msg"] = (
                "plan-only run: stopped at gated stage '%s'." % stage
            )
            summary.project_id = project_id or summary.project_id
            return reflected

        if decision.resolution == Resolution.PAUSE:
            reflected = {
                "ok": False,
                "paused": True,
                "gate": stage,
                "error": decision.reason,
            }
            reflected["_gate_paused"] = True
            reflected["_gate_pause_msg"] = decision.reason
            summary.project_id = project_id or summary.project_id
            return reflected

        # APPROVE: grant human_approved so a gated stage may complete.
        args["human_approved"] = True
        result = bridge.call("checkpoint_write", args)
        result["_gate_approve"] = True
        return result


def _render_result(result: dict[str, Any]) -> str:
    """Render a bridge result as a compact string for the model."""
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + "\n...[truncated]"
    return text
