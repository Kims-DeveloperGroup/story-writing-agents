# story-writing-agents

<a id="objective"></a>
A Python 3.11+ multi-agent runtime for drafting and reviewing long-form fiction.
Four fixed roles work from editable project documents, audit the same revision,
and write a manuscript only after all three voting specialists agree.

> **Available now:** workspace setup, directives, consensus, inspection,
> persistent Codex and Antigravity sessions, a provider-free smoke test, and a TTY shell. The
> shell provides lifecycle status, conversation work, retrieval, and consensus.
> Inline canonical artifact references are available after a session is
> attached and a conversation is focused. In a capable terminal's
> provider-bound prompt span, `$` opens
> `Writing artifact selection [open]` for whole-file insertion, `$${path}`
> opens that file in the ranged viewer, and `$$` opens a picker before viewing.
> There is no standalone artifact command or noninteractive picker UI.

## Quick start

Codex is the default provider. Install and authenticate its CLI for real story
runs:

```bash
npm install -g @openai/codex
rehash
codex login
```

Alternatively, set `CODEX_API_KEY` in the process environment; credentials are
not stored in the story workspace. To route one or more agents through
Antigravity instead, install and authenticate the `agy` executable as described
in [Use Antigravity agents](docs/guides/antigravity.md).

Install in a standard-library virtual environment or Conda; do not nest a `venv` inside active Conda.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
rehash
```

`story` and `story-writing-agents` are exact aliases. Smoke uses synthetic data
without Codex or Antigravity, credential reads, a terminal, subprocesses, apps,
web, or network.
It validates conversation persistence/routing, exact search, cited retrieval, fresh-runtime continuity, and included-context consensus; a pass includes `"provider_tested":false`.

Create a workspace, run the provider-free check, and enter the primary human workflow:

```bash
story init ./my-story
cd ./my-story
story smoke
story
```

At `story[lobby]>`, create a session and focus a subject conversation:

```text
/session create novel
/conversation new Opening scene
Which core-document constraint should shape the opening?
/run --output part-01/opening.md Draft the opening from the current conversation focus.
/inspect last
/quit
```

Every consensus run publishes to a required create-only `PART/PATH.md` beneath
`manuscript/`; omitting it in the shell opens a separate output prompt. Advisory
input is one line; `/run --output part-01/opening.md` with no prompt owns
multiline composition. `/quit`
releases process access without pausing the ready session. The default session
and default conversation remain saved, so later `story` can resume the session
and restore its conversation.
Inside the shell, `/session resume [NAME]` and
`/conversation resume [ID]` are the only resume grammar. On a capable terminal,
typing either omitted form shows choices before Enter; highlighted Enter submits
an explicit identity and bypasses the picker. If the omitted form itself is
submitted, the controller reads the choices again. A successful read with at
least one eligible choice opens a generic picker: live and transient on a
capable terminal, or numbered in ordinary-line fallback. An empty or failed
read reports its result or error without opening the picker. See the user-visible
state and input behavior in [Story sessions](docs/guides/story-sessions.md).
See the complete [Glasswater journey](docs/guides/glasswater-scenario.md).

For scripts and non-TTY automation, use `story run`; it never uses conversation
focus. See the [single automation example](docs/guides/glasswater-scenario.md#use-one-explicit-automation-call-then-pause)
and [CLI contract](docs/reference/cli.md#workspace-noninteractive-behavior-and-exits).
For example:

```bash
story run --output part-01/opening.md --prompt 'Draft the opening.'
```

## Choose the next page

- [Getting started](docs/getting-started.md) explains installation and the first run.
- [Documentation index](docs/INDEX.md) routes each task, contract, design topic, and operational concern.
- [Workspace guide](docs/guides/workspaces.md) explains the editable project files.
- [Writing artifacts](docs/reference/writing-artifacts.md) defines the exact produced-output paths, provenance states, and recovery behavior.
- [Antigravity guide](docs/guides/antigravity.md) covers `agy` setup, routing, safety, sessions, and provider-free verification.
- [Story sessions](docs/guides/story-sessions.md) shows shell management, conversation reads, retrieval, and consensus.
- [Session lifecycle](docs/design/session-lifecycle.md) maps internal state to commands, output, errors, and next actions.
- [Consensus-run guide](docs/guides/consensus-runs.md) covers prompts, drafts, outputs, and round limits.
- [CLI reference](docs/reference/cli.md) is the exact installed command contract.
- [Package API](docs/reference/package-api.md) starts the Python reference.
- [Security and provider privacy](docs/operations/privacy-and-provider.md) should be read before using sensitive material.

<a id="backends"></a>

`AgentBackend` coordinates Codex and Antigravity; Codex remains the default.
The only supported backend names are `codex` and `antigravity`; legacy
`gemini` configuration is rejected with migration guidance.
`StoryRuntime` owns consensus, advisory sequencing, and retrieval authorization. See the [system design](docs/design/overview.md).

<a id="minimal-python-api"></a>

For Python integrations, start with the [runtime API](docs/reference/runtime-api.md), [agent contracts](docs/reference/agent-contracts.md), and [bounded retrieval](docs/reference/conversation-retrieval.md).

<a id="current-limitations"></a>

## Important limits

- Provider calls have a timeout but no automatic retry or rate limiter.
- Deadlocked and interrupted debates cannot resume under the same debate ID.
- Workspace content, logs, provider metadata, agent state, and manuscripts are plaintext.
- Writing-artifact publication is journaled and catalog-last, and rolls forward
  on the next artifact access after an interruption. It is not one transaction
  with debate logs, lifecycle state, or result files.
- Smoke's explicit workspace is not read-only and retains bounded plaintext
  diagnostics when they can be written; abrupt termination can interrupt
  cleanup. A pass does not validate provider/model, terminal behavior,
  integration, or story quality. See the
  [smoke guide](docs/guides/smoke-test.md).
- The interactive shell accepts one advisory line; `/run --output PART/PATH.md`
  without prompt text owns multiline composition. Bare `/run` first prompts for
  that required output. Process-level PTY tests verify TTY gating and VINTR/VEOF delivery
  on supported POSIX. Retrieval is active-only and bounded. Inline `${path}`
  and `${path}#Lx-Ly` indicators make every addressed agent read the ordered
  canonical batch before its result is accepted. In provider-bound shell spans,
  `$` opens a labeled artifact-selection panel and completes a whole-file
  indicator; `$${path}` or `$$` opens the ranged viewer. These editing actions
  do not submit the prompt. Lobby/no-focus input and non-provider fields remain
  inert. Standalone artifact commands and automation UI remain unavailable.
  See the [artifact-reference guide](docs/guides/artifact-references.md) and
  [conversation retrieval](docs/reference/conversation-retrieval.md).

## Development

<a id="verification"></a>

Run the full standard-library suite or the focused POSIX PTY boundary with:

```bash
python -m unittest discover -s tests -v
PYTHONPATH=src python -m unittest tests.test_interactive_pty -v
```

Without a usable POSIX PTY, the focused suite skips with an explicit reason; it does not emulate Windows consoles.

<a id="contribution-guidelines"></a>

Contributors should read [GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md), [AGENT_TASK_RUNBOOK.md](AGENT_TASK_RUNBOOK.md), and [COMMIT_POLICY.md](COMMIT_POLICY.md).

<a id="project-structure"></a>

The Python package is under `src/story_writing_agents/`; tests are under `tests/`; role prompts are under `config/agents/`; and [PYTHON_MODULE_INDEX.md](PYTHON_MODULE_INDEX.md) is the module navigation map.
