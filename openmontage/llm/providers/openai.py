"""OpenAI helper: build the Chat Completions tools array from the bridge.

Kept thin: the orchestrator now drives the loop directly through
``llm/openai.py`` (text ``[tool]`` protocol). This module only provides the
schema armature used to validate that every bridge tool is expressible as an
OpenAI function, plus plain-language tool descriptions.
"""

from __future__ import annotations

from typing import Any

from ...bridge import TOOL_DEFS


def build_openai_tools() -> list[dict[str, Any]]:
    """Build the OpenAI tools array from the bridge's TOOL_DEFS."""
    tools = []
    for name, spec in TOOL_DEFS.items():
        props = {}
        required = []
        for argname, arg_spec in spec["args"].items():
            props[argname] = {
                "type": arg_spec["type"],
                "description": arg_spec.get("description", ""),
            }
            if arg_spec.get("required"):
                required.append(argname)
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": _TOOL_DESCRIPTIONS.get(name, ""),
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    return tools


# Short plain-language descriptions surfaced to the model alongside each tool.
_TOOL_DESCRIPTIONS = {
    "preflight": "Return the machine's production capability menu (N of M providers configured per family). Call once at the start.",
    "list_pipelines": "List available pipeline manifest names.",
    "load_pipeline": "Load a pipeline manifest (YAML) with its stages, gates, required tools and review focus.",
    "stage_gate_policy": "Return whether a given stage gates on human approval per the pipeline manifest.",
    "load_skill": "Load a stage director / meta skill markdown by relative path (no .md suffix). Read the director skill BEFORE doing a stage.",
    "load_playbook": "Load a visual style playbook (openmontage/styles/*.yaml).",
    "list_tools": "List registered production tools, optionally filtered by capability.",
    "run_tool": "Execute a registered OpenMontage tool with JSON inputs. Always put output_path under projects/<project_id>/. This is how you produce assets / narration / renders.",
    "project_init": "Initialise a project workspace under projects/<project_id>/. Call once before working on a project.",
    "checkpoint_write": "Write a stage checkpoint with its canonical artifact. A gated stage needs status='awaiting_human' first; only write status='completed' when approval is granted (human_approved=true).",
    "checkpoint_next": "Return the next stage to run based on completed checkpoints.",
    "checkpoint_completed": "List completed stages for a project.",
    "checkpoint_read": "Read a project's stage checkpoint.",
    "write_artifact": "Write a canonical JSON artifact under projects/<project_id>/artifacts/.",
    "read_artifact": "Read a JSON artifact from a project.",
    "finalize": "Signal that production is complete and the deliverable is ready. Call this last.",
}
