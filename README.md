# story-writing-agents

<a id="objective"></a>
A Python 3.11+ multi-agent runtime for drafting and reviewing long-form fiction.
Four fixed roles work from editable project documents, audit the same revision,
and write a manuscript only after all three voting specialists agree.

> **Available now:** workspace setup, directives, consensus, inspection,
> persistent Codex and Antigravity sessions, a provider-free smoke test, and a TTY shell. The
> shell provides lifecycle status, conversation work, retrieval, and consensus.
> Inline writing-artifact references are available after a session is attached
> and a conversation is focused. In a capable terminal's provider-bound prompt
> span, `$` opens `Writing artifact selection [open]` for whole-file insertion.
> The list contains the four supported core documents and every eligible
> lowercase-`.md` file anywhere below `manuscript/`. Typing the second `$`
> immediately changes that panel into ranged-source selection; Up/Down chooses
> a source and Enter/Tab opens the independent viewer. A completed
> `$${path}` marker also opens that source directly on Enter/Tab. There is no
> standalone artifact command or noninteractive picker UI.

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
| Antigravity | `EXECUTABLE models` | `EXECUTABLE` with no arguments |

For Antigravity, `EXECUTABLE` is `agy` by default or the resolved
`--antigravity-executable PATH` override supported by `story chat`.

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

That is the RAG-free base installation. Approved internal, non-redistributing
evaluation can use the optional direct Python boundary, deterministic local
provider, disposable generation store, and programmatic runtime/agent
integration. Explicit composition requires the pinned `rag` extra plus an
application-owned runtime state port; commands and settings remain unavailable.
See [Optional conversation RAG](docs/guides/conversation-rag.md) for setup,
trust/persistence limits, current agent use, and the planned user flow.

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

After workspace and configuration loading, startup first resolves the desired
conversation-storage backend into one ready active service bundle. The welcome
then precedes the first interactive gate: at least one provider must pass a
fresh native status check, and every distinct provider selected by the
effective agent routes must pass again before the runtime constructs a story
backend or session controller. Press Ctrl-C or send EOF to leave without
starting the shell; cancelling the picker keeps the gate open.

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
- [Writing artifacts](docs/reference/writing-artifacts.md) separates current reference sources from publication identity, provenance, and recovery.
- [Conversation archives](docs/reference/conversation-archives.md) documents canonical conversation-only bytes and privileged filesystem transfer.
- [Optional conversation RAG](docs/guides/conversation-rag.md) covers the direct local provider/store, programmatic runtime integration, and planned user commands/settings.
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

The optional conversation RAG contracts, deterministic local provider,
identity-bound generation store, exact generic adapter, composition, and
provider/store-neutral runtime service are direct-module APIs and are not
package-root exports. An application can inject the runtime service into
`StoryRuntime`; RAG commands and `/settings` selection remain deferred. See
the [RAG package boundary](docs/reference/package-api.md#optional-conversation-rag-boundary).

Canonical conversation archive values and lease-bound filesystem export/import
are available from the `story_writing_agents.conversation_storage` subpackage.
They are application-controlled transport infrastructure, not a shell command,
complete workspace backup, or arbitrary restore workflow. See
[Conversation archives](docs/reference/conversation-archives.md).

Conversation catalog, transcript, and reference-search ports also have an
optional SQLite implementation for direct Python composition. The installed
shell can select the complete filesystem or SQLite bundle for its next launch;
direct `StoryRuntime` construction still defaults to filesystem unless the
caller supplies a composed bundle. Start with the
[package API](docs/reference/package-api.md#optional-sqlite-conversation-storage).
`SQLiteConversationSearch` keeps the public literal-search contract exact; a
private disposable FTS5 companion can supply hints but never determines a
result or readiness.

Select desired storage without a provider:

```bash
story storage use sqlite
```

The command checks SQLite through the active Python interpreter and may
explicitly provision an absent empty database. If it reports
`installation_required`, rerun it only after reviewing the supported host
command and authorizing both automation flags:

```bash
story storage use sqlite --install-missing --yes
```

If the active filesystem store is nonempty, selection can instead report
`migration_required`. The ready SQLite destination can then be copied and
verified before selection is retried:

```bash
story storage migrate --from filesystem --to sqlite --dry-run
story storage migrate --from filesystem --to sqlite
story storage verify --from filesystem --to sqlite
story storage use sqlite
story
```

Selection changes only the desired backend. It never migrates, deletes, or
activates data; a successful choice applies when the next shell freshly checks
readiness and exact canonical parity. The current shell keeps its original
bundle. See the
[storage selection contract](docs/reference/cli.md#conversation-storage-selection).

Ready filesystem and SQLite stores in the same workspace can be
planned, copied, and compared without a provider:

```bash
story storage migrate --from filesystem --to sqlite --dry-run
story storage migrate --from filesystem --to sqlite
story storage verify --from filesystem --to sqlite
```

`--from` must name the workspace's active backend, and the two backend names
must differ. Migration preserves exact active and archived conversation
metadata and every transcript event, but it never provisions SQLite, changes
the active backend, deletes the source, or copies the derived FTS companion.
See the [CLI contract](docs/reference/cli.md#conversation-storage-migration).

<a id="current-limitations"></a>

## Important limits

- Provider calls have a timeout but no automatic retry or rate limiter.
- Deadlocked and interrupted debates cannot resume under the same debate ID.
- Workspace content, logs, provider metadata, agent state, and manuscripts are plaintext.
- Writing-artifact publication is journaled and catalog-last, and rolls forward
  on the next catalog or publication access after an interruption. It is not
  one transaction with debate logs, lifecycle state, or result files.
- Conversation storage defaults to filesystem. `/settings` and provider-free
  `story storage use` can save filesystem or SQLite as the desired backend for
  the next shell. SQLite host installation requires explicit confirmation, and
  startup never installs or provisions it. Migration remains a separate
  provider-free copy/parity workflow over two ready same-workspace stores and
  never activates or deletes one. There is no live switch, dual write, MySQL
  adapter, or shell RAG integration. The optional direct Python RAG boundary
  now has a deterministic local provider, separate disposable generation store,
  and authorized programmatic scheduling/reread/citation path, but no commands
  or settings yet. It consumes a complete filesystem or SQLite authority bundle
  without coupling derived state to either backend. The derived FTS5 companion
  does not change readiness, exact literal results, migration parity, or
  selection.
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
  `$` opens a labeled artifact-selection panel populated from the current safe
  reference sources and completes a whole-file indicator. Exact `$$`
  immediately shows ranged-source choices; Enter/Tab opens the highlighted
  source in the independent viewer. A completed `$${path}` marker opens that
  source directly on Enter/Tab. These editing actions do not submit the prompt.
  Lobby/no-focus input and non-provider fields remain inert. Standalone artifact
  commands and automation UI remain unavailable.
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
