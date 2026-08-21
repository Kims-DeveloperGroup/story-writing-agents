# Getting started

> **Availability:** this guide uses the installed CLI. Real runs and routed
> interactive turns use Codex; `story smoke` is provider-free. See [Story sessions](guides/story-sessions.md) for the shell.

Key terms: **editable install**—an installation that points at this checkout;
**workspace**—the local story project directory; **default session**—the saved
session used when a command omits its name; **current focus**—the conversation
used for turns in this shell; **smoke test**—a provider-free deterministic check.

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

The command creates and removes a temporary workspace while exercising local
conversation, retrieval, and consensus persistence. Success includes
`"status":"passed"` and `"provider_tested":false`; it does not validate Codex,
credentials, network access, terminal behavior, story quality, or a model.

## 3. Prepare a story workspace

Initialize a dedicated directory, then work from inside it:

```bash
story init ./first-story
cd ./first-story
```

`init` creates missing scaffold files without replacing existing content. Edit
the four files under `core_documents/` and the four role prompts under
`config/agents/<agent-id>/AGENTS.md`; all are strict UTF-8 Markdown. The runtime
reads current content before each provider phase.

## 4. Begin the human workflow

Bare `story` is the primary interactive entry:

```bash
story
```

At the lobby prompt, create a durable session and subject conversation:

```text
/session create first_novel
/conversation new Opening scene
Which established fact should anchor the opening?
/run Draft a restrained opening using the current conversation focus.
/inspect last
/status
/quit
```

The ordinary question is one advisory line. `/run PROMPT` starts consensus with
one bounded snapshot of the current focus. `/inspect last` shows safe metadata
for this process's latest accepted run. `/status` names the durable state,
process access, defaults, current attachment and focus, provider readiness, and
next action. `/quit` releases access without pausing. Run `story` again to resume
the ready default and restore its active default conversation. Use confirmed
`/session pause` when provider work should stop until the session is resumed again.
Resume it in the shell with `/session resume [NAME]`; omit `NAME` to choose from
the live pre-Enter rows on a capable terminal. Submitting the omitted command
rereads the available sessions. A successful nonempty read opens a generic
picker, which is live on a capable terminal and numbered in ordinary-line
fallback; an empty or failed read reports its result or error without a picker.

## 5. Automate without a TTY

Scripts, pipelines, and other non-TTY callers must use noninteractive
`story run`; it never receives conversation focus. Use explicit workspace and
session inputs in the [single automation example](guides/glasswater-scenario.md#use-one-explicit-automation-call-then-pause),
then consult the exact [CLI contract](reference/cli.md#workspace-noninteractive-behavior-and-exits).

Continue with [Workspaces](guides/workspaces.md), [Consensus runs](guides/consensus-runs.md),
or the complete interactive [Glasswater journey](guides/glasswater-scenario.md).
Exact grammar and exits live in the [CLI reference](reference/cli.md).
