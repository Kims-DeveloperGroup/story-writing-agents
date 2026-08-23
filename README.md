# story-writing-agents

<a id="objective"></a>
A Python 3.11+ multi-agent runtime for drafting and reviewing long-form fiction.
Four fixed roles work from editable project documents, audit the same revision,
and write a manuscript only after all three voting specialists agree.

> **Available now:** workspace setup, directives, consensus, inspection,
> persistent Codex and Antigravity sessions, a provider-free smoke test, and a TTY shell. The
> shell provides lifecycle status, conversation work, retrieval, and consensus.
> Standalone artifact commands remain unavailable.

## Quick start

Install the Codex CLI for the final fallback route used by real story runs:

```bash
npm install -g @openai/codex
rehash
```

To route through Antigravity, install the `agy` executable as described in
[Use Antigravity agents](docs/guides/antigravity.md). On each interactive
`story` launch, the runtime checks both native CLIs and opens the selected CLI's
own sign-in flow when you choose an unauthenticated provider. Its exact native
commands are:

| Provider | Status check | Sign-in |
| --- | --- | --- |
| Codex | `codex login status` | `codex login` |
| Antigravity | `agy models` | `agy` |

The sign-in process inherits the real terminal and environment at the resolved
workspace root and may open a provider-controlled browser. The runtime does not
read provider output, account details, OAuth URLs, tokens, credentials, or the
provider's native credential storage.

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

After the welcome, authentication is the first interactive gate. At least one
provider must pass a fresh native status check, and every distinct provider
selected by the effective agent routes must pass again before the runtime
constructs a story backend or session controller. Press Ctrl-C or send EOF to
leave without starting the shell; cancelling the picker keeps the gate open.

At `story[lobby]>`, create a session and focus a subject conversation:

```text
/session create novel
/conversation new Opening scene
Which core-document constraint should shape the opening?
/run Draft the opening from the current conversation focus.
/inspect last
/quit
```

Advisory input is one line; bare `/run` owns multiline composition. `/quit`
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

## Choose the next page

- [Getting started](docs/getting-started.md) explains installation and the first run.
- [Documentation index](docs/INDEX.md) routes each task, contract, design topic, and operational concern.
- [Workspace guide](docs/guides/workspaces.md) explains the editable project files.
- [Antigravity guide](docs/guides/antigravity.md) covers `agy` setup, authentication, routing, safety, sessions, and provider-free verification.
- [Story sessions](docs/guides/story-sessions.md) shows shell management, conversation reads, retrieval, and consensus.
- [Session lifecycle](docs/design/session-lifecycle.md) maps internal state to commands, output, errors, and next actions.
- [Consensus-run guide](docs/guides/consensus-runs.md) covers prompts, drafts, outputs, and round limits.
- [CLI reference](docs/reference/cli.md) is the exact installed command contract.
- [Package API](docs/reference/package-api.md) starts the Python reference.
- [Security and provider privacy](docs/operations/privacy-and-provider.md) should be read before using sensitive material.

<a id="backends"></a>

`AgentBackend` coordinates Codex and Antigravity. Route precedence is a CLI
override, explicit agent route, persisted workspace default, then Codex.
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
- Artifact replacement is atomic per file with best-effort multi-file rollback, not a power-loss-safe transaction.
- Smoke's explicit workspace is not read-only and retains any plaintext
  diagnostic records that were successfully written; abrupt termination can
  interrupt cleanup. A pass does not validate provider/model, terminal
  behavior, integration, or story quality. See the
  [smoke guide](docs/guides/smoke-test.md).
- The interactive shell accepts one advisory line; bare `/run` owns multiline
  composition. Process-level PTY tests verify TTY gating and VINTR/VEOF delivery
  on supported POSIX. Retrieval is active-only and bounded; standalone artifact
  commands remain unavailable. See [conversation retrieval](docs/reference/conversation-retrieval.md).

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
