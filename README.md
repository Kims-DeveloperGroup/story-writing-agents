# story-writing-agents

A Python multi-agent runtime for writing and reviewing novels.

## Objective

Operate a team of specialized agents that cooperates with the user to produce
coherent long-form fiction. The lead orchestrator drafts and synthesizes while
plot, character, and execution specialists independently audit each revision.

The runtime turns a prompt or supplied draft into a versioned candidate,
requires all three specialists to approve the same revision, and commits the
manuscript only after consensus. It persists directives, debate state, the
public blackboard, owner-isolated agent outcomes, and Codex thread references
in a local workspace.

This implementation realizes the designs in
[Issue #1: Overall System Architecture Design](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1)
and
[Issue #2: Multi-Agent Open Debate Workflow](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/2).
The detailed documentation describes the implemented behavior where it narrows
those proposals.

## Quick Start

Python 3.11 or later and the Codex CLI are required for real story runs.
Install and authenticate Codex first:

```bash
npm install -g @openai/codex
rehash
codex login
```

Alternatively, provide `CODEX_API_KEY` in the process environment. The runtime
does not store provider credentials in its workspace.

From the repository root, choose one Python environment setup. Do not create a
standard-library virtual environment inside an active Conda environment.

Standard-library `venv`:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
rehash
```

Or Conda:

```bash
conda create --name story-writing-agents python=3.11
conda activate story-writing-agents
python -m pip install --editable .
rehash
```

The installation provides `story-writing-agents` and the shorter `story` as
exact aliases. Examples use `story`.

Run the built-in provider-free check:

```bash
story smoke
```

It exercises the complete deterministic revision workflow in a temporary
workspace without invoking Codex, using credentials, or making a network call.
A passing report contains `"provider_tested": false`.

Initialize a workspace, enter it, and select a persistent provider session:

```bash
story init ./quickstart-workspace
cd ./quickstart-workspace
story session start my_novel
```

Initialization creates one editable role prompt per agent at
`config/agents/<agent-id>/AGENTS.md`. It preserves existing files. To reset all
four role prompts to the packaged defaults:

```bash
story init . --overwrite-prompts
```

That flag changes only the four canonical prompt files.

Run a story-writing debate using the default Codex backend:

```bash
story run \
  'Write an opening scene using the workspace core documents.'
```

From an initialized workspace or any directory beneath it, the CLI discovers
the nearest workspace and uses the session selected by the last successful
`story session start`. Debate IDs and output names are generated when omitted.
Explicit `--workspace`, `--session`, `--debate-id`, and `--output` options
remain available for scripts and automation.

When work on the provider session is complete:

```bash
story session end
```

Ending a session prevents new provider calls but retains its per-agent Codex
thread IDs. Starting the same session later resumes compatible threads.

The [CLI User Guide](UserGuide.md) covers installation, prompt and draft
workflows, backend overrides, directives, inspection, generated files, and an
[end-to-end story-writing scenario](UserGuide.md#end-to-end-story-writing-scenario).
See [Configuration](docs/configuration.md) for complete schemas.

## Backends

`AgentBackend` and `BoundAgent` are concrete runtime classes. `AgentBackend`
resolves each agent's configured vendor, stages its definition, owns provider
session leases, and persists resumable threads. `BoundAgent` embeds the
vendor-specific invocation path for one immutable agent definition.

Codex is the default and currently the only implemented provider. Each entry
in `config/agent_configs.json` has a `backend` field. `gemini` is a recognized
configuration value reserved for future implementation; selecting it fails
before a debate begins.

A backend can be overridden for one run with a repeatable assignment:

```bash
story run ... --backend plot_architect=codex
```

The CLI does not dynamically import user-selected backend modules.

## Minimal Python API

```python
import asyncio

from story_writing_agents import AgentBackend, StoryRuntime, initialize_workspace


async def main() -> None:
    workspace = initialize_workspace("./python-workspace")
    backend = AgentBackend(workspace)
    backend.start_session("demo")
    try:
        runtime = StoryRuntime(workspace, backend)
        result = await runtime.run_prompt(
            "demo",
            "Write a restrained opening scene.",
            debate_id="opening_01",
            manuscript_name="opening.md",
        )
        print(result.status.value, result.manuscript_path)
    finally:
        backend.close()


asyncio.run(main())
```

`AgentBackend.bind()` receives an `AgentDefinition` containing verified local
file references and returns a concrete `BoundAgent`. Per-turn `AgentRequest`
values contain dynamic data only. See the [API reference](docs/api.md).

## Verification

```bash
python -m unittest discover -s tests -v
```

## Current Limitations

- Codex is the only implemented provider; Gemini is reserved but unsupported.
- Provider calls are not automatically retried or rate-limited.
- Session-wide owner-isolated agent history has no compaction or retention
  limit.
- Deadlocked debates cannot resume under the same debate ID.
- Workspace configuration, logs, provider metadata, agent state, and
  manuscripts are plaintext.
- Filesystem replacement is atomic per file with best-effort multi-file
  rollback, not a power-loss-safe transaction across all artifacts.

Read [Architecture](docs/architecture.md) for workflow and persistence details
and [Security, Privacy, and Limitations](docs/security.md) before using
sensitive story material.

## Contribution Guidelines

See [GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md) for the Issues, Projects,
milestones, logical iterations, branches, and pull-request workflow. Coding
agents use [AGENT_TASK_RUNBOOK.md](AGENT_TASK_RUNBOOK.md) for the parent/child
branch and worktree procedure. See [COMMIT_POLICY.md](COMMIT_POLICY.md) for
commit rules.

This repository consumes `repository-engineering@repository-engineering-kit`
version `0.2.0` from the canonical
[AI Engineering Kit](https://github.com/Kims-DeveloperGroup/AI-engineering-kit).
The [repository-engineering lock](.codex/repository-engineering-kit.lock.json)
records the immutable source commit and is the authoritative installed
baseline. The projected repository-development agents under `.codex/agents/`
are distinct from the four editable story-runtime roles under `config/agents/`;
only the latter participate in story-writing debates. Consult the canonical
[package documentation](https://github.com/Kims-DeveloperGroup/AI-engineering-kit/blob/c19213d182f91de06447bbf632c7dab0e5a898f6/plugins/repository-engineering/README.md)
for installation, ownership, upgrade, and MCP guidance.

## Project Structure

- `README.md` contains the project overview and contribution entry point.
- `PYTHON_MODULE_INDEX.md` records the current Python package and module map.
- `GITHUB_WORKFLOW.md` defines the GitHub planning and delivery workflow.
- `AGENT_TASK_RUNBOOK.md` defines the agent branch and worktree procedure.
- `UserGuide.md` is the complete CLI workflow and troubleshooting guide.
- `docs/` contains API, architecture, configuration, and security details.
- `.codex/agents/` contains the projected repository-development agents.
- `.codex/repository-engineering-kit.lock.json` records the immutable installed
  projection baseline and provenance.
- `src/story_writing_agents/` contains the Python runtime and concrete backend.
- `tests/` contains the standard-library test suite.
- `config/agents/` contains the four editable story-runtime role prompts used
  in novel-writing debates.
