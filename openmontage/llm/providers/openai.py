"""OpenAI backend for the LLM orchestrator driver.

Wraps the Chat Completions tool-use API. Kept deliberately thin so the
orchestrator loop (llm/orchestrator.py) only depends on a small, stable
shape: build the message list, call the model, get back assistant content and
tool calls, then submit tool results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from openai import OpenAI

from ...bridge import TOOL_DEFS


@dataclass
class Turn:
    """One model turn: assistant text plus the tool calls it requested."""
    content: Optional[str] = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = ""
    usage: Optional[dict[str, Any]] = None


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


class OpenAIOrchestrator:
    """Drives one OpenAI model through a tool-use session."""

    def __init__(self, api_key: str, model: str = "gpt-4o") -> None:
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.tools = build_openai_tools()
        self.history: list[dict[str, Any]] = []

    def reset(self) -> None:
        self.history = []

    def add_system(self, text: str) -> None:
        self.history.insert(0, {"role": "system", "content": text})

    def add_user(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})

    def add_assistant(self, turn: Turn) -> None:
        msg: dict[str, Any] = {"role": "assistant", "content": turn.content}
        if turn.tool_calls:
            msg["tool_calls"] = turn.tool_calls
        self.history.append(msg)

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        self.history.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        })

    def turn(self) -> Turn:
        """One model call. Returns the assistant turn (content + tool calls)."""
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=self.history,
            tools=self.tools if self.tools else None,
            tool_choice="auto",
        )
        choice = resp.choices[0]
        tcalls = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tcalls.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                })
        usage = None
        if resp.usage is not None:
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            }
        return Turn(
            content=choice.message.content,
            tool_calls=tcalls,
            finish_reason=(choice.finish_reason or ""),
            usage=usage,
        )
