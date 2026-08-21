# OpenMontage CLI — standalone driver

This copy of OpenMontage is packaged as a **standalone Python CLI program**. It
keeps the full OpenMontage runtime (tools, lib, skills, pipeline_defs, schemas,
styles, backlot, remotion-composer) but adds a command-line driver that replaces
"an AI coding assistant in a chat" with a **tool-use LLM orchestrator**: you give
one command, the CLI spins up an LLM, feeds it the OpenMontage agent contract and
the pipeline skills, and it drives the production stage by stage.

## Install

```bash
python -m pip install -e .
# or
pip install -r requirements.txt   # runtime deps (OpenMontage + openai)
```

Requires Python 3.10+, FFmpeg, and (for LLM orchestration) an `OPENAI_API_KEY`
in `.env` or the environment.

## Commands

```
openmontage --version
openmontage doctor            # environment self-check
openmontage preflight         # capability menu  (alias: capabilities)
openmontage pipelines         # list pipeline manifests
openmontage pipeline <name>   # show a manifest
openmontage tools             # list discovered production tools
openmontage init <id> --title <t> --pipeline <p> [--open]
openmontage backlot [<id>]    # open the Backlot live board
openmontage plan  "<request>" [flags]   # research + production plan only
openmontage make  "<request>" [flags]   # full production (single command)
openmontage run  "<request>" --yes      # full autonomous run (auto-approve gates)
openmontage resume "<request>" --yes    # continue from checkpoint
```

`make` / `plan` / `run` share flags:

```
--pipeline <name>    pipeline manifest (default: animated-explainer)
--duration <s>       target duration, e.g. 45s or 60
--title <t>          production title
--project <id>       project id override
--plan-only          stop after planning; do not generate assets or render
--yes                auto-approve every approval gate (full autonomous run)
--model <id>         orchestrator model (default: gpt-4o)
```

## Approval gates

Pipelines declare `human_approval_default` per stage. OpenMontage enforces that
a gated stage cannot be marked complete without approval. The CLI is
non-interactive, so it applies a policy:

| Flag | Behaviour |
|------|-----------|
| `--yes` | Auto-approve every gate — full autonomous run |
| `--plan-only` | Stop at the plan, before any asset gate |
| (default) | Stop at the first gated stage and explain how to continue |

## How it works

`openmontage_cli/` adds:
- `bridge.py` — exposes OpenMontage capabilities (preflight, load pipeline /
  skill / playbook, `run_tool` for any registered `BaseTool`, checkpoints,
  artifacts, finalize) as callable operations.
- `llm/orchestrator.py` + `llm/providers/openai.py` — the tool-use loop. The
  system prompt encodes the AGENT_GUIDE contract (Rule Zero, preflight,
  stage discipline, checkpoint/gate protocol, decision log, quality gates) and
  the model drives the bridge until it calls `finalize`.
- `gates.py` — the approval-gate policy above.
- `runner.py` / `commands/make_cmd.py` — the single-command `make` automation.

Every production writes to `projects/<project-id>/` (renerateable, gitignored)
exactly as upstream OpenMontage does; the Backlot board reads the same files.
