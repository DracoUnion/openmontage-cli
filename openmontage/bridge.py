"""Bridge: turn OpenMontage's capabilities into callable operations.

The LLM orchestrator (see llm/) is a tool-use loop. This module is the
"server" it talks to. Each public function here is one thing the orchestrator
can ask the CLI to do: run preflight, load a pipeline manifest or director
skill, execute a registered OpenMontage tool, write a checkpoint, read/write
artifacts, and finalise a project.

Everything here is deliberately thin: it wraps the existing OpenMontage
functions (lib.checkpoint, lib.pipeline_loader, tools.tool_registry) rather
than reimplementing them. Results are returned as JSON-safe dicts so they can
be rendered into an LLM tool result.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from openmontage.utils.paths import (
    project_root,
    ensure_root_in_path,
    projects_dir,
    skill_path,
)

_ROOT = project_root()
ensure_root_in_path()

# These imports require the project root already on sys.path (done above).
from lib.checkpoint import (  # noqa: E402
    init_project,
    write_checkpoint,
    read_checkpoint,
    get_completed_stages,
    get_next_stage,
)
from lib.pipeline_loader import (  # noqa: E402
    load_pipeline,
    list_pipelines,
    get_stage_human_approval_default,
)
from tools.tool_registry import registry  # noqa: E402


def _json_safe(value: Any, depth: int = 0) -> Any:
    """Recursively coerce values to JSON-serialisable primitives.

    Path -> str, and cap string lengths so a giant artifact never blows the
    LLM context window.
    """
    if depth > 12:
        return "<nesting-too-deep>"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v, depth + 1) for v in value]
    if isinstance(value, bytes):
        return "<bytes %d>" % len(value)
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


# ---------------------------------------------------------------------------
# Discovery / capability
# ---------------------------------------------------------------------------

def preflight() -> dict[str, Any]:
    """Return the human-ready capability menu (N of M configured per family)."""
    registry.discover()
    return registry.provider_menu_summary()


def list_all_pipelines() -> dict[str, Any]:
    return {"pipelines": sorted(list_pipelines())}


def load_pipeline_manifest(name: str) -> dict[str, Any]:
    try:
        manifest = load_pipeline(name)
    except FileNotFoundError:
        return {"ok": False, "error": f"unknown pipeline {name!r}",
                "known": sorted(list_pipelines())}
    return {"ok": True, "manifest": manifest}


def stage_gate_policy(pipeline: str, stage: str) -> dict[str, Any]:
    """Return whether a stage gates on human approval per the manifest."""
    try:
        manifest = load_pipeline(pipeline)
    except FileNotFoundError:
        return {"ok": False, "error": f"unknown pipeline {pipeline!r}"}
    return {
        "ok": True,
        "pipeline": pipeline,
        "stage": stage,
        "human_approval_default": bool(
            get_stage_human_approval_default(manifest, stage)
        ),
    }


def load_director_skill(rel: str) -> dict[str, Any]:
    """Load a skills/ file by its relative path without the .md suffix,
    e.g. 'pipelines/explainer/script-director'."""
    path = skill_path(rel)
    if not path.is_file():
        return {"ok": False, "error": f"no skill at skills/{rel}.md"}
    return {"ok": True, "path": str(path), "content": path.read_text(encoding="utf-8")}


def load_playbook(name: str) -> dict[str, Any]:
    import yaml
    path = _ROOT / "styles" / f"{name}.yaml"
    if not path.is_file():
        return {"ok": False, "error": f"no playbook styles/{name}.yaml"}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"could not parse playbook: {exc}"}
    return {"ok": True, "name": name, "playbook": data}


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def list_tools(capability: Optional[str] = None, include_unavailable: bool = False) -> dict[str, Any]:
    registry.ensure_discovered()
    tools = list(registry._tools.values())
    if capability:
        tools = [t for t in tools if t.capability == capability]
    if not include_unavailable:
        tools = [t for t in tools if t.get_status().value == "available"]
    tools.sort(key=lambda t: (t.capability, t.provider, t.name))
    return {
        "tools": [
            {
                "name": t.name,
                "provider": t.provider,
                "capability": t.capability,
                "status": t.get_status().value,
                "best_for": (t.best_for or [])[:4],
                "runtime": t.runtime.value,
            }
            for t in tools
        ]
    }


def run_tool(name: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Execute a registered OpenMontage tool (.execute) and return its result.

    This is the only way the orchestrator produces assets. ToolResult fields
    (success, data, artifacts, cost_usd, error) are flattened into a compact,
    JSON-safe dict.
    """
    registry.ensure_discovered()
    tool = registry.get(name)
    if tool is None:
        known = sorted(registry.list_all())[:120]
        return {"ok": False, "error": f"unknown tool {name!r}", "hint": known}
    try:
        result = tool.execute(inputs)
    except Exception as exc:  # surface any tool-level exception to the LLM
        return {
            "ok": False,
            "tool": name,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "ok": bool(result.success),
        "tool": name,
        "provider": tool.provider,
        "success": bool(result.success),
        "data": _json_safe(result.data),
        "artifacts": [str(a) for a in (result.artifacts or [])],
        "cost_usd": result.cost_usd,
        "model": result.model,
        "error": result.error,
    }


# ---------------------------------------------------------------------------
# Project lifecycle / checkpoints
# ---------------------------------------------------------------------------

def project_init(
    project_id: str,
    title: str,
    pipeline: str,
    style_playbook: Optional[str] = None,
) -> dict[str, Any]:
    pdir = projects_dir() / project_id
    try:
        created = init_project(
            project_id,
            title=title,
            pipeline_type=pipeline,
            style_playbook=style_playbook,
        )
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "project_id": project_id, "project_dir": str(created)}


def checkpoint_write(
    project_id: str,
    stage: str,
    status: str,
    artifacts: Optional[dict[str, Any]] = None,
    *,
    pipeline: Optional[str] = None,
    human_approved: bool = False,
    review: Optional[dict[str, Any]] = None,
    cost_snapshot: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
) -> dict[str, Any]:
    """Write a stage checkpoint for a project.

    Respects OpenMontage gate enforcement: a stage whose manifest declares
    human_approval_default=true can only be written with status='completed'
    when human_approved is true. The orchestrator should first write
    status='awaiting_human' and surface approval decisions to the CLI, which
    passes human_approved through per its gate policy (see gates.py).
    """
    pdir = projects_dir()
    try:
        path = write_checkpoint(
            pdir,
            project_id,
            stage,
            status,
            artifacts or {},
            pipeline_type=pipeline,
            human_approved=human_approved,
            review=review,
            cost_snapshot=cost_snapshot,
            metadata=metadata,
            error=error,
        )
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "stage": stage}
    return {"ok": True, "project_id": project_id, "stage": stage, "status": status,
            "checkpoint_path": str(path)}


def checkpoint_next(project_id: str, pipeline: Optional[str] = None) -> dict[str, Any]:
    pdir = projects_dir()
    try:
        stage = get_next_stage(pdir, project_id, pipeline)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "next_stage": stage}


def checkpoint_completed(project_id: str, pipeline: Optional[str] = None) -> dict[str, Any]:
    pdir = projects_dir()
    try:
        done = get_completed_stages(pdir, project_id, pipeline)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "completed": done}


def checkpoint_read(project_id: str, stage: str) -> dict[str, Any]:
    pdir = projects_dir()
    cp = read_checkpoint(pdir, project_id, stage)
    if cp is None:
        return {"ok": False, "error": f"no checkpoint for stage {stage!r}"}
    return {"ok": True, "checkpoint": _json_safe(cp)}


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

def _artifact_path(project_id: str, rel: str) -> Path:
    return projects_dir() / project_id / "artifacts" / rel


def write_artifact(project_id: str, rel: str, data: dict[str, Any]) -> dict[str, Any]:
    path = _artifact_path(project_id, rel)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "path": str(path)}


def read_artifact(project_id: str, rel: str) -> dict[str, Any]:
    path = _artifact_path(project_id, rel)
    if not path.is_file():
        return {"ok": False, "error": f"no artifact projects/{project_id}/artifacts/{rel}"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"could not parse artifact: {exc}"}
    return {"ok": True, "artifact": _json_safe(data)}


def finalize(project_id: str, message: str) -> dict[str, Any]:
    """Orchestrators call this to signal the run is complete."""
    return {"ok": True, "finalized": True, "project_id": project_id, "message": message}


# ---------------------------------------------------------------------------
# Public dispatch table: name -> (function, argument schema). The LLM driver
# builds its tool-use schema from this.
# ---------------------------------------------------------------------------

def _str_opt(*, required: bool = False, desc: str = ""):
    return {"type": "string", "required": required, "description": desc}


TOOL_DEFS: dict[str, dict[str, Any]] = {
    "preflight": {"fn": preflight, "args": {}},
    "list_pipelines": {"fn": list_all_pipelines, "args": {}},
    "load_pipeline": {"fn": load_pipeline_manifest, "args": {"name": _str_opt(required=True, desc="pipeline name, e.g. animated-explainer")}},
    "stage_gate_policy": {"fn": stage_gate_policy, "args": {
        "pipeline": _str_opt(required=True), "stage": _str_opt(required=True)}},
    "load_skill": {"fn": load_director_skill, "args": {"rel": _str_opt(required=True, desc="skills path without .md, e.g. pipelines/explainer/script-director")}},
    "load_playbook": {"fn": load_playbook, "args": {"name": _str_opt(required=True)}},
    "list_tools": {"fn": list_tools, "args": {
        "capability": _str_opt(desc="capability family, e.g. video_generation"),
        "include_unavailable": {"type": "boolean", "required": False, "description": "include unavailable tools"}}},
    "run_tool": {"fn": run_tool, "args": {
        "name": _str_opt(required=True, desc="registered tool name"),
        "inputs": {"type": "object", "required": True, "description": "JSON inputs for the tool (must include output_path under projects/<id>/)"}}},
    "project_init": {"fn": project_init, "args": {
        "project_id": _str_opt(required=True), "title": _str_opt(required=True),
        "pipeline": _str_opt(required=True),
        "style_playbook": _str_opt(desc="optional playbook name")}},
    "checkpoint_write": {"fn": checkpoint_write, "args": {
        "project_id": _str_opt(required=True), "stage": _str_opt(required=True),
        "status": _str_opt(required=True, desc="one of completed|awaiting_human|in_progress|failed"),
        "artifacts": {"type": "object", "required": False, "description": "canonical artifact(s) for the stage"},
        "pipeline": _str_opt(desc="pipeline type"),
        "human_approved": {"type": "boolean", "required": False, "description": "must be true to complete a gated stage"},
        "review": {"type": "object", "required": False},
        "cost_snapshot": {"type": "object", "required": False}}},
    "checkpoint_next": {"fn": checkpoint_next, "args": {"project_id": _str_opt(required=True), "pipeline": _str_opt()}},
    "checkpoint_completed": {"fn": checkpoint_completed, "args": {"project_id": _str_opt(required=True), "pipeline": _str_opt()}},
    "checkpoint_read": {"fn": checkpoint_read, "args": {"project_id": _str_opt(required=True), "stage": _str_opt(required=True)}},
    "write_artifact": {"fn": write_artifact, "args": {"project_id": _str_opt(required=True), "rel": _str_opt(required=True), "data": {"type": "object", "required": True}}},
    "read_artifact": {"fn": read_artifact, "args": {"project_id": _str_opt(required=True), "rel": _str_opt(required=True)}},
    "finalize": {"fn": finalize, "args": {"project_id": _str_opt(required=True), "message": _str_opt()}},
}


def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a tool call by name. Raises KeyError for unknown tools."""
    return TOOL_DEFS[name]["fn"](**args)


def tool_names() -> list[str]:
    return sorted(TOOL_DEFS.keys())
