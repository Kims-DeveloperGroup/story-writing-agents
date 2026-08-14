# Getting started

> **Availability:** this guide uses the implemented installed CLI. Real runs use
> Codex; `story smoke` is provider-free. There is no interactive chat shell.

Key terms: **editable install**—an installation that points at this checkout;
**workspace**—the local story project directory; **selected session**—the
saved CLI default session; **smoke test**—a provider-free deterministic check.

## 1. Prepare the tools

Use Python 3.11 or later. For real story work, install and authenticate the
Codex CLI:

```bash
npm install -g @openai/codex
rehash
codex login
```

You may set `CODEX_API_KEY` instead. Keep credentials outside workspaces,
prompts, drafts, and logs.

Choose one Python environment. Do not create a standard-library virtual
environment inside active Conda.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
rehash
story --help
```

For Conda, replace the first two lines with:

```bash
conda create --name story-writing-agents python=3.11
conda activate story-writing-agents
```

On PowerShell, activate a `venv` with `.venv\Scripts\Activate.ps1`. The
installed `story` and `story-writing-agents` commands are exact aliases.

## 2. Check the runtime without a provider

```bash
story smoke
```

The command creates a temporary workspace, exercises one deterministic
conflict-and-revision cycle, prints compact JSON, and removes the workspace.
Success includes `"status":"passed"` and `"provider_tested":false`; it does
not verify Codex, credentials, network access, story quality, or a model.

## 3. Complete a first real run

This copyable scenario keeps everything in a dedicated directory:

```bash
story init ./first-story
cd ./first-story
story session start first_novel
story directive '[SYS: LANG=EN]'
story run \
  --debate-id opening_01 \
  --output opening.md \
  'Write a restrained opening scene using the core documents.'
story inspect --debate opening_01
story session end
```

`init` creates missing scaffold files without replacing existing content.
`session start` activates `first_novel` and saves it as the workspace's default
CLI session. Commands from this directory or a child discover the workspace and
reuse that selection. `run` reserves the debate ID before calling a provider;
on consensus it creates `manuscript/opening.md`. `inspect` prints the persisted
lifecycle, result, and public blackboard.

The directive must be the exact whole argument. It persists for this session.
The CLI prints JSON to standard output and caught errors as
`ExceptionType: message` to standard error.

## 4. Add project context

Edit the four files under `core_documents/` and the four role prompts under
`config/agents/<agent-id>/AGENTS.md`. They are strict UTF-8 Markdown. The
runtime reads current content before each phase, so edit between runs rather
than while a run is binding files.

Continue with [Workspaces](guides/workspaces.md), [Consensus runs](guides/consensus-runs.md),
or the complete [Glasswater scenario](guides/glasswater-scenario.md). Exact
options and exit codes live only in the [CLI reference](reference/cli.md).
