"""Headless tests for the openmontage driver (no API keys, no network).

These prove the wiring works: the bridge executes real OpenMontage tools, and
the orchestrator loop drives a provider through preflight -> tool calls ->
checkpoint (with gate policy) -> finalize.
"""

from __future__ import annotations

import json
import os

import pytest

from openmontage import bridge
from openmontage.gates import GatePolicy, Resolution
from openmontage.llm.orchestrator import Orchestrator, build_user_prompt


def test_bridge_preflight_returns_menu():
    env = bridge.preflight()
    assert "capabilities" in env
    assert "composition_runtimes" in env
    caps = {c["capability"] for c in env["capabilities"]}
    assert "tts" in caps or "video_generation" in caps or len(caps) > 0


def test_bridge_list_pipelines():
    res = bridge.list_all_pipelines()
    pipes = res["pipelines"]
    assert "animated-explainer" in pipes


def test_bridge_tool_names():
    names = bridge.tool_names()
    for expected in ("preflight", "run_tool", "checkpoint_write", "project_init", "finalize"):
        assert expected in names


def test_tool_defs_have_armatures():
    # every tool callable by the bridge must have a schema usable by a provider
    from openmontage.llm.providers.openai import build_openai_tools
    tools = build_openai_tools()
    names = {t["function"]["name"] for t in tools}
    assert names == set(bridge.tool_names())


class _FakeLLM:
    """A scripted text-protocol LLM that avoids network calls."""

    def __init__(self, script):
        self.script = list(script)
        self.turns_served = 0

    def __call__(self, _msgs, _model):
        self.turns_served += 1
        call = self.script.pop(0) if self.script else ("finalize", {})
        if isinstance(call, str):
            name, args = call, {}
        else:
            name, args = call
        return "[tool]" + json.dumps([{
            "id": f"call_{name}",
            "tool": name,
            "parameters": args,
        }]) + "[/tool]"


def _run_orchestrator(script, gate_policy=None):
    fake_llm = _FakeLLM(script)
    orch = Orchestrator(
        gate_policy=gate_policy or GatePolicy(yes=True),
        max_turns=20,
        llm_call=fake_llm,
    )
    return orch.run("test request")


def test_orchestrator_preflight_then_finalize(tmp_path, monkeypatch):
    """A minimal happy path: preflight -> (tool) -> finalize."""
    # Point projects dir into a temp dir so nothing random is created.
    monkeypatch.setattr(bridge, "projects_dir", lambda: tmp_path)
    summary = _run_orchestrator(
        [
            ("preflight", {}),
            ("project_init", {"project_id": "t", "title": "T", "pipeline": "animated-explainer"}),
            ("finalize", {"project_id": "t", "message": "done"}),
        ]
    )
    assert summary.finalized is True
    assert summary.finalized_message == "done"


def test_orchestrator_gate_pause_without_yes(tmp_path, monkeypatch):
    """Without --yes, a gated checkpoint write pauses the run."""
    monkeypatch.setattr(bridge, "projects_dir", lambda: tmp_path)
    summary = _run_orchestrator(
        [
            ("checkpoint_write", {
                "project_id": "t",
                "stage": "proposal",
                "status": "completed",
                "pipeline": "animated-explainer",
            }),
        ],
        gate_policy=GatePolicy(yes=False),
    )
    # loop should halt with a pause message, not finalize
    assert summary.finalized is False
    assert summary.finalized_message and "approval" in summary.finalized_message.lower()


def test_orchestrator_plan_only_halts_at_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "projects_dir", lambda: tmp_path)
    summary = _run_orchestrator(
        [("checkpoint_write", {
            "project_id": "t",
            "stage": "proposal",
            "status": "completed",
            "pipeline": "animated-explainer",
        })],
        gate_policy=GatePolicy(yes=False, plan_only=True),
    )
    assert summary.finalized is False
    assert "plan-only" in summary.finalized_message.lower()


def test_gate_policy_matrix():
    assert GatePolicy(yes=True).resolve("x", True).resolution == Resolution.APPROVE
    assert GatePolicy(yes=False).resolve("x", True).resolution == Resolution.PAUSE
    assert GatePolicy(yes=False, plan_only=True).resolve("x", True).resolution == Resolution.PLAN_ONLY


def test_build_system_prompt_includes_contract():
    p = build_user_prompt("make a 45s explainer about black holes")
    assert "RULE ZERO" in p
    assert "MANDATORY PREFLIGHT" in p
    assert "checkpoint_write" in p or "CHECKPOINTS" in p
    assert "black holes" in p


def test_runner_requires_api_key_to_be_set(monkeypatch):
    # Simulate no key configured -> runner must fail fast with a clear message.
    from openmontage import runner, config
    monkeypatch.setattr(config, "has_api_key", lambda: False)
    res = runner.make("some request")
    assert res.ok is False
    assert "OPENAI_API_KEY" in res.message


def test_runner_parse_duration():
    from openmontage.runner import parse_duration
    assert parse_duration("45s") == 45
    assert parse_duration("60") == 60
    assert parse_duration("90 seconds") == 90
    assert parse_duration(None) is None
    assert parse_duration("fast") is None


def test_bridge_run_tool_local_ffmpeg(tmp_path):
    """Run a real local (FFmpeg) tool through the bridge with a known-good
    path. This proves the tool-execution ledger works with zero API keys."""
    import shutil, subprocess
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not available")
    inp = tmp_path / "in.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1",
         "-pix_fmt", "yuv420p", str(inp)],
        capture_output=True, check=True,
    )
    out = tmp_path / "out.mp4"
    res = bridge.run_tool("video_trimmer", {
        "operation": "cut",
        "input_path": str(inp),
        "output_path": str(out),
        "start_seconds": 0.0,
        "end_seconds": 0.5,
    })
    assert res["ok"] is True
    assert out.exists()
    assert str(out) in res["artifacts"]
