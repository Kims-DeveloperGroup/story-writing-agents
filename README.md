# story-writing-agents
multi agents runtime to support story writing

The project is implemented in Python.

## Objective

Operate a multi-agent team for writing novels that interactively communicates
and cooperates with the user. Each agent is a specialized professional that
works within its own domain, while the team coordinates its expertise to
deliver coherent results.

## Runtime Overview

The implemented runtime turns a prompt or supplied draft into a versioned
candidate, has three domain specialists audit it concurrently, and permits a
filesystem artifact commit only after all three specialists approve the same
revision. It persists session overrides, debate lifecycle state, the full
public blackboard, and owner-isolated agent outcomes in a local workspace.

The runtime requires Python 3.11 or later and uses only the Python standard
library. No LLM, model SDK, or provider adapter is bundled. To run a debate,
supply a trusted asynchronous backend plugin that implements the public
`AgentBackend` protocol.

This implementation realizes the designs in
[Issue #1: Overall System Architecture Design](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1)
and
[Issue #2: Multi-Agent Open Debate Workflow](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/2).
The detailed documentation describes the implemented behavior where it differs
from or narrows those design proposals.

## Quick Start

From the repository root, choose one environment setup. Do not create a
standard-library virtual environment inside an active Conda environment.

Standard-library `venv`:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
```

Or Conda:

```bash
conda create --name story-writing-agents python=3.11
conda activate story-writing-agents
python -m pip install --editable .
```

After either setup, run the built-in provider-free check:

```bash
story-writing-agents smoke
```

The command exercises the full deterministic revision workflow in a temporary
workspace and removes it afterward. It never loads an external backend or
calls a network service. A passing result verifies this runtime, not an LLM,
provider, model, credentials, or real story quality; its compact JSON reports
`"provider_tested": false`.

Then initialize a normal workspace:

```bash
story-writing-agents init ./quickstart-workspace
```

No provider is bundled. To write real story content, supply a trusted provider
adapter to `run`. The
[CLI User Guide](UserGuide.md) covers prompt and draft workflows, every
directive, inspection, generated files, retry rules, and troubleshooting. It
also includes an original
[end-to-end story-writing scenario](UserGuide.md#end-to-end-story-writing-scenario).
See
[Configuration](docs/configuration.md) for the complete setting schemas.

## Minimal Python API

```python
import asyncio

from story_writing_agents import AgentBackend, StoryRuntime


async def run(backend: AgentBackend) -> None:
    runtime = StoryRuntime(".", backend)
    result = await runtime.run_draft(
        "demo",
        "A neutral draft for domain review.",
        debate_id="debate_01",
        manuscript_name="demo.md",
    )
    print(result.status.value, result.manuscript_path)


# asyncio.run(run(your_backend))
```

Backends receive structured `AgentRequest` values and must return phase-valid
`AgentReply` values. See the [API reference](docs/api.md) for the protocol,
signatures, result fields, CLI exit codes, and inspection shapes.

## Verification

With the editable installation active, run the standard-library test suite:

```bash
python -m unittest discover -s tests -v
```

## Current Limitations

- No LLM/provider implementation, network client, interactive chat UI, or
  automatic provider retry is included.
- Session-wide owner-isolated agent history is rehydrated in full; it currently
  has no compaction or retention limit.
- Deadlocked debates stop and report `action_required`; they cannot be resumed
  under the same debate ID. Start a new debate after the user resolves the
  conflict.
- Local configuration, logs, agent state, and manuscripts are plaintext.
- Filesystem replacements are atomic per file with best-effort multi-file
  rollback, not a power-loss-safe transaction across all artifacts.

Read [Architecture](docs/architecture.md) for the exact workflow and persistence
topology, and [Security, Privacy, and Limitations](docs/security.md) before
using sensitive story material or third-party backend plugins.

## Contribution Guidelines

See [COMMIT_POLICY.md](COMMIT_POLICY.md) for commit rules.

## Project Structure

- `README.md` contains the project description and contribution guidelines.
- `UserGuide.md` is the complete CLI workflow and troubleshooting guide.
- `docs/` contains project-related and product documentation.
- Root-level documents contain project-management, work-rule, and policy
  documents such as `COMMIT_POLICY.md` and `DEPENDENCY_POLICY.md`.
- `src/story_writing_agents/` contains the provider-neutral Python runtime.
- `tests/` contains the standard-library unit and integration test suite.
- `config/`, `core_documents/`, `agent_states/`, `discussion_logs/`, and
  `manuscript/` form the local runtime workspace.
