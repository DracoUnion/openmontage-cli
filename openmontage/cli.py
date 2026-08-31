"""OpenMontage command-line interface.

Usage:
    openmontage --version
    openmontage tools [--json] [--all]
    openmontage capabilities [--json]
    openmontage pipelines
    openmontage pipeline <name>
    openmontage init <project-id> --title <title> --pipeline <pipeline> [--style <style>] [--open]
    openmontage backlot [project-id]
    openmontage info
    openmontage doctor
    openmontage make "<request>" [--pipeline <p>] [--duration <s>] [--title <t>]
                              [--project <id>] [--plan-only] [--yes] [--model <m>]
    openmontage plan "<request>" [same flags]
    openmontage run "<request>" [--yes] [same flags]
    openmontage resume "<request>" [--yes] [same flags]

Examples:
    openmontage init hidden-math-of-nature --title "Hidden Math of Nature" --pipeline animated-explainer --open
    openmontage capabilities --json > capabilities.json
    openmontage plan  "make a 45s explainer about why the sky is blue"
    openmontage make  "make a 45s explainer about why the sky is blue" --plan-only
    openmontage make  "make a 60s explainer about CRISPR" --yes
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any, Optional

from . import __version__


def _project_root() -> Path:
    """Return the openmontage package directory (the working copy's Python package)."""
    return Path(__file__).resolve().parent


def _ensure_root_in_path() -> None:
    """Put the project root on sys.path so the openmontage package resolves."""
    root = str(_project_root().parent)
    if root not in sys.path:
        sys.path.insert(0, root)


def _pipelines_dir() -> Path:
    return _project_root() / "pipeline_defs"


def _list_pipelines() -> list[str]:
    """Return sorted pipeline names (stem of each YAML manifest)."""
    pipes: list[str] = []
    pipes_dir = _pipelines_dir()
    if not pipes_dir.exists():
        return pipes
    for p in sorted(pipes_dir.glob("*.yaml")):
        pipes.append(p.stem)
    return pipes


def _print_table(rows: list[list[str]]) -> None:
    """Print a simple text table from rows of strings."""
    if not rows:
        return
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    for row in rows:
        print("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))


def cmd_version(_args: argparse.Namespace) -> int:
    print(f"openmontage {__version__}")
    print(f"project root: {_project_root()}")
    return 0


def cmd_info(_args: argparse.Namespace) -> int:
    root = _project_root()
    print("OpenMontage CLI")
    print(f"  version:       {__version__}")
    print(f"  project root:  {root}")
    print(f"  python:        {sys.version.split()[0]}")
    print(f"  platform:      {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"  pipelines:     {len(_list_pipelines())}")
    env_file = root.parent / ".env"
    print(f"  .env present:  {env_file.exists()}")
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    root = _project_root()
    issues: list[str] = []

    if sys.version_info < (3, 10):
        issues.append(f"Python {sys.version_info.major}.{sys.version_info.minor} is below 3.10")

    required_dirs = ["tools", "lib", "schemas", "styles", "skills"]
    for name in required_dirs:
        if not (root / name).is_dir():
            issues.append(f"Missing required directory: {name}")
    if not _pipelines_dir().is_dir():
        issues.append("Missing required directory: pipeline_defs")

    env_file = root.parent / ".env"
    if not env_file.exists():
        issues.append("No .env file found; many providers will be unavailable")

    # Try to import the registry as a sanity check.
    try:
        _ensure_root_in_path()
        from .tools.tool_registry import registry  # type: ignore[import]

        registry.ensure_discovered()
        available = len(registry.get_available())
        unavailable = len(registry.get_unavailable())
        print(f"Tool registry imported successfully ({available} available, {unavailable} unavailable)")
    except Exception as exc:
        issues.append(f"Could not import or discover tool registry: {exc}")

    if issues:
        print("Issues found:")
        for item in issues:
            print(f"  - {item}")
        return 1

    print("Environment looks healthy.")
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    _ensure_root_in_path()
    from .tools.tool_registry import registry  # type: ignore[import]

    registry.ensure_discovered()
    tools = registry.get_available()
    if args.all:
        tools = list(registry._tools.values())

    if args.json:
        payload = [
            {"name": t.name, "provider": t.provider, "capability": t.capability, "status": t.get_status().value}
            for t in tools
        ]
        json.dump(payload, sys.stdout, indent=2)
        print()
        return 0

    print(f"{'name':<30} {'provider':<18} {'capability':<22} {'status'}")
    print("-" * 80)
    for t in tools:
        print(f"{t.name:<30} {t.provider:<18} {t.capability:<22} {t.get_status().value}")
    return 0


def cmd_capabilities(args: argparse.Namespace) -> int:
    _ensure_root_in_path()
    from .tools.tool_registry import registry  # type: ignore[import]

    registry.ensure_discovered()
    summary = registry.provider_menu_summary()

    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        print()
        return 0

    runtimes = summary.get("composition_runtimes") or {}
    print("Composition runtimes")
    for engine, ok in sorted(runtimes.items()):
        print(f"  {engine:<12} {'available' if ok else 'unavailable'}")

    print("\nCapabilities")
    headers = ["capability", "configured", "total", "available providers"]
    rows = [headers]
    for cap in summary.get("capabilities", []):
        rows.append([
            cap.get("capability", ""),
            str(cap.get("configured", 0)),
            str(cap.get("total", 0)),
            ", ".join(cap.get("available_providers", [])),
        ])
    _print_table(rows)

    setup_offers = summary.get("setup_offers", [])
    if setup_offers:
        print("\nQuick setup offers")
        for offer in setup_offers[:10]:
            print(f"  - {offer.get('tool')} ({offer.get('capability')}): {offer.get('install_instructions', '')}")

    warnings = summary.get("runtime_warnings", [])
    if warnings:
        print("\nRuntime warnings")
        for w in warnings:
            print(f"  - {w}")
    return 0


def cmd_pipelines(_args: argparse.Namespace) -> int:
    pipes = _list_pipelines()
    if not pipes:
        print("No pipeline manifests found.")
        return 1
    print("Available pipelines:")
    for name in pipes:
        print(f"  - {name}")
    return 0


def cmd_pipeline_show(args: argparse.Namespace) -> int:
    root = _project_root()
    path = root / "pipeline_defs" / f"{args.name}.yaml"
    if not path.is_file():
        print(f"Unknown pipeline: {args.name!r}")
        print("Choose from:")
        for p in _list_pipelines():
            print(f"  - {p}")
        return 2
    print(path.read_text(encoding="utf-8"))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    _ensure_root_in_path()
    from .lib.checkpoint import init_project  # type: ignore[import]

    project_id = args.project_id
    title = args.title
    pipeline = args.pipeline
    style = args.style

    pipes = _list_pipelines()
    if pipes and pipeline not in pipes:
        print(f"Unknown pipeline: {pipeline!r}")
        print("Choose from:")
        for p in pipes:
            print(f"  - {p}")
        return 2

    project_dir = init_project(
        project_id,
        title=title,
        pipeline_type=pipeline,
        style_playbook=style,
    )
    print(f"Initialized project '{project_id}' at {project_dir}")

    if args.open:
        return _backlot_open(project_id)
    return 0


def _backlot_open(project_id: Optional[str]) -> int:
    try:
        _ensure_root_in_path()
        from .backlot.__main__ import main as backlot_main  # type: ignore[import]

        argv = ["open", project_id] if project_id else ["open"]
        return backlot_main(argv)
    except Exception as exc:
        print(f"backlot: could not open board ({exc})")
        return 1


def cmd_backlot(args: argparse.Namespace) -> int:
    return _backlot_open(args.project_id)


def _build_parser() -> argparse.ArgumentParser:
    openai_key = os.environ.get('OPENAI_API_KEY')
    openai_url = os.environ.get('OPENAI_BASE_URL')
    openai_model = os.environ.get('OPENAI_CHAT_MODEL', 'gpt-3.5-turbo')
    openai_vmodel = os.environ.get('OPENAI_VIS_MODEL', '')
    openai_tti_model = os.environ.get('OPENAI_TTI_MODEL', '')

    parser = argparse.ArgumentParser(
        prog="openmontage",
        description="OpenMontage — AI-Orchestrated Video Production CLI",
        epilog="Tip: run 'openmontage doctor' to check the environment.",
    )
    parser.add_argument("--version", action="version", version=f"openmontage {__version__}")
    parser.add_argument("-m", "--model", default=openai_model, help="model name")
    parser.add_argument("-k", "--key", default=openai_key, help="OpenAI API key")
    parser.add_argument("-r", "--retry", type=int, default=1_000_000, help="times of retry")
    parser.add_argument("-tm", "--temp", type=float, default=1, help="temperature")
    parser.add_argument("-tp", "--top-p", type=float, help="top p")
    parser.add_argument("-fp", "--frequency-penalty", type=float, help="frequency penalty")
    parser.add_argument("-pp", "--presence-penalty", type=float, help="presence penalty")
    parser.add_argument("-mt", "--max-tokens", type=int, default=None, help="max tokens")
    parser.add_argument("-H", "--host", default=openai_url, help="api host")
    parser.add_argument("--emb", default=os.environ.get('EMB_MODEL_PATH', 'moka-ai/m3e-base'), help="emb model path")
    parser.add_argument("-vm", "--vmodel", default=openai_vmodel, help="vision model name")
    parser.add_argument("-im", "--tti-model", default=openai_tti_model, help="vision model name")
    parser.add_argument("-ua", "--user-agent", default='claude-cli/2.1.41 (external, cli)', help="HTTP User-Agent Header")
    parser.add_argument("-st", "--stream", action='store_true' , help="stream mode")
    parser.add_argument("-eb", "--extra-body", help="extra body")
    parser.add_argument("-ct", "--conn-timeout", type=int, default=60, help="")
    parser.add_argument("-rt", "--read-timeout", type=int, default=120, help="")
    parser.add_argument("-rr", "--repetition-regex", default='', help="re for repetition detection")
    parser.set_defaults(func=lambda x: parser.print_help())
    sub = parser.add_subparsers(dest="command", help="Available commands", metavar="COMMAND")

    sub.add_parser("version", help="show version and project root")
    sub.add_parser("info", help="show environment and project info")
    sub.add_parser("doctor", help="check the local environment")
    sub.add_parser("pipelines", help="list available pipeline manifests")

    p_pipe_show = sub.add_parser("pipeline", help="show a pipeline manifest")
    p_pipe_show.add_argument("name", help="pipeline name (see 'openmontage pipelines')")

    p_tools = sub.add_parser("tools", help="list discovered production tools")
    p_tools.add_argument("--json", action="store_true", help="output JSON")
    p_tools.add_argument("--all", action="store_true", help="include unavailable tools")

    p_caps = sub.add_parser("capabilities", help="show capability/provider menu")
    p_caps.add_argument("--json", action="store_true", help="output JSON")

    p_init = sub.add_parser("init", help="initialize a new production project")
    p_init.add_argument("project_id", help="kebab-case project identifier")
    p_init.add_argument("--title", required=True, help="human-readable production title")
    p_init.add_argument("--pipeline", required=True, help="pipeline type (see 'openmontage pipelines')")
    p_init.add_argument("--style", default=None, help="optional style playbook")
    p_init.add_argument("--open", action="store_true", help="open the Backlot board after init")

    p_backlot = sub.add_parser("backlot", help="open the Backlot board in a browser")
    p_backlot.add_argument("project_id", nargs="?", default=None, help="project to focus the board on")

    for name, plan_only in (("make", None), ("plan", True), ("run", False), ("resume", False)):
        p = sub.add_parser(name, help=f"run a production from a natural-language request")
        p.add_argument("request", help="natural-language video brief")
        p.add_argument("--pipeline", default="animated-explainer", help="pipeline manifest (default: animated-explainer)")
        p.add_argument("--duration", default=None, help="target duration (e.g. 45s or 60)")
        p.add_argument("--title", default=None, help="production title")
        p.add_argument("--project", default=None, help="project id override (kebab-case)")
        if plan_only is not None and plan_only:
            p.add_argument("--plan-only", action="store_true", default=True, help=argparse.SUPPRESS)
        else:
            p.add_argument("--plan-only", action="store_true", default=False, help="stop after planning; do not generate assets or render")
        p.add_argument("--yes", action="store_true", default=False, help="auto-approve every approval gate")
        p.add_argument("--om-root", default=None, help="project root override")
        p.add_argument("--max-turns", type=int, default=80, help="max turns")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    from .utils.utf8 import configure_utf8
    configure_utf8()

    parser = _build_parser()
    args = parser.parse_args(argv)

    command_map: dict[str, Any] = {
        "version": cmd_version,
        "info": cmd_info,
        "doctor": cmd_doctor,
        "tools": cmd_tools,
        "capabilities": cmd_capabilities,
        "pipelines": cmd_pipelines,
        "pipeline": cmd_pipeline_show,
        "init": cmd_init,
        "backlot": cmd_backlot,
    }
    # Import lazily so the read-only commands still work even if an LLM
    # dependency (openai) is missing.
    if args.command in ("make", "plan", "run", "resume"):
        from .commands.make_cmd import (
            cmd_make, cmd_plan, cmd_run, cmd_resume,
        )
        command_map.update({
            "make": cmd_make,
            "plan": cmd_plan,
            "run": cmd_run,
            "resume": cmd_resume,
        })

    handler = command_map.get(args.command)
    if handler is None:
        parser.print_help()
        return 2

    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
