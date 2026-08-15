# story-writing-agents

<a id="objective"></a>

A Python 3.11+ multi-agent runtime for drafting and reviewing long-form fiction.
Four fixed roles work from editable project documents, audit the same revision,
and write a manuscript only after all three voting specialists agree.

> **Available now:** workspace setup, exact directives, prompt or draft consensus
> runs, inspection, persistent Codex sessions, and a provider-free smoke test.
> There is no interactive chat shell, transcript command, or search command yet.

## Quick start

Install and authenticate the Codex CLI for real story runs:

```bash
npm install -g @openai/codex
rehash
codex login
```

Alternatively, set `CODEX_API_KEY` in the process environment. Credentials are
not stored in the story workspace.

Install this repository in either a standard-library virtual environment or a
Conda environment. Do not nest a `venv` inside active Conda.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
rehash
story smoke
```

`story` and `story-writing-agents` are exact aliases. The smoke test uses no
provider, credentials, or network; a pass includes `"provider_tested":false`.

Create a workspace and run the first real debate:

```bash
story init ./my-story
cd ./my-story
story session start novel
story run 'Write an opening scene using the workspace core documents.'
story session end
```

Commands run inside the workspace, or a directory beneath it, discover the
nearest initialized workspace. They use the session selected by the last
successful `session start`. Scripts can pass `--workspace` and `--session`.

## Choose the next page

- [Getting started](docs/getting-started.md) explains installation and the first
  run.
- [Documentation index](docs/INDEX.md) shows what is implemented and routes
  each task, exact contract, design topic, and operational concern.
- [Workspace guide](docs/guides/workspaces.md) explains the editable project
  files.
- [Consensus-run guide](docs/guides/consensus-runs.md) covers prompts, drafts,
  outputs, and round limits.
- [CLI reference](docs/reference/cli.md) is the exact installed command contract.
- [Package API](docs/reference/package-api.md) starts the Python reference.
- [Security and provider privacy](docs/operations/privacy-and-provider.md) should
  be read before using sensitive material.

<a id="backends"></a>

`AgentBackend` is the concrete provider coordinator; Codex is the only
implemented provider. `gemini` is a reserved configuration value and fails
preflight. `StoryRuntime` owns the consensus workflow and persistence. See the
[system design](docs/design/overview.md) for their responsibilities.

<a id="minimal-python-api"></a>

For Python integrations, start with the [runtime API](docs/reference/runtime-api.md)
and [agent contracts](docs/reference/agent-contracts.md).

<a id="current-limitations"></a>

## Important limits

- Provider calls have a timeout but no automatic retry or rate limiter.
- Deadlocked and interrupted debates cannot resume under the same debate ID.
- Workspace content, logs, provider metadata, agent state, and manuscripts are
  plaintext.
- Artifact replacement is atomic per file with best-effort multi-file rollback,
  not a power-loss-safe transaction.
- Conversation metadata, transcript persistence, [bounded search and cited
  transcript pages](docs/reference/conversation-search.md), and interactive-line
  parsing exist as Python APIs. Selection, dispatch, and
  conversation/search/transcript CLI commands are not implemented.

## Development

<a id="verification"></a>

Run the standard-library tests with:

```bash
python -m unittest discover -s tests -v
```

<a id="contribution-guidelines"></a>

Contributors should read [GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md),
[AGENT_TASK_RUNBOOK.md](AGENT_TASK_RUNBOOK.md), and
[COMMIT_POLICY.md](COMMIT_POLICY.md).

<a id="project-structure"></a>

The Python package is under
`src/story_writing_agents/`; tests are under `tests/`; editable role prompts are
under `config/agents/`; and [PYTHON_MODULE_INDEX.md](PYTHON_MODULE_INDEX.md) is
the current module navigation map.
