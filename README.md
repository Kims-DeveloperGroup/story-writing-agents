# story-writing-agents

A Python 3.11+ runtime for building a lead-led, multi-agent novel-writing
workflow. The repository currently exposes the workspace, configuration,
session, conversation, collaboration-history, storage, search, and inspection
foundations. Active lead/specialist story collaboration is the next integration
step; it is not callable in this revision.

## Current integration stage

Public direct-execution paths are absent. The selected runtime now has one
authoritative schema-v2 collaboration-history service for storage and
provider-free inspection.

| You can do now | You cannot do in this stage |
| --- | --- |
| Initialize a writing workspace. | Send an ordinary shell line to an agent. |
| Create, resume, list, and pause story sessions. | Route a line to an agent or select a sticky target. |
| Create, list, focus, rename, archive, search, and read conversations. | Start a debate or publish a manuscript from the installed CLI. |
| Apply directives and edit workspace settings. | Attach canonical artifact references to an agent call. |
| Inspect session, debate, history, transcript, status, and recorded exchanges. | Call a direct-execution method on `StoryRuntime` or `RuntimeSession`. |
| Use schema-v2 collaboration contracts, stores, archive/transfer, readable projection, search, and paging APIs. | Treat the active inspection path as an active lead/provider workflow. |
| Run a provider-free management/storage smoke check. | Use smoke as evidence of provider behavior or story quality. |

When ordinary text, routed text, or a retired execution command is submitted in
the shell, Story reports:

```text
Direct story execution is unavailable in this integration stage. Session, conversation, history, search, and inspection remain available.
```

The rejection happens before provider binding, transcript append, RAG,
artifact-reference reads, progress reporting, or artifact publication. #466
delivered the pure schema-v2 values; #474-#479 added readable projections,
storage, archive/transfer, and the sole active storage/inspection service.
Open issue #452 owns the later lead proposal/confirmation workflow, #453 owns
specialist execution/results, and #454 owns whole-workspace rejection of
unsupported history. None is implied by the current inspection service.

See [Lead agent and collaboration status](docs/guides/lead-agent.md) for the
lead's responsibilities, confirmation boundary, and current versus target
examples.

## Install

Choose one Python environment. Do not create a standard-library virtual
environment inside an active Conda environment.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
rehash
story --help
```

`story` and `story-writing-agents` are exact command aliases. The normal
installation includes the isolated read-only JSONL viewer used by
`/exchanges`. Optional local conversation RAG requires the reviewed `rag`
extra before it can be enabled; installation alone never enables it.

The interactive shell still performs its configured native provider
authentication gate and exposes `/auth` and provider settings. This prepares
configuration for the forthcoming collaboration cutover. No currently
accepted shell content invokes a story provider.

## Quick start

```bash
story init ./my-story
cd ./my-story
story smoke
story
```

At `story[lobby]>`, manage and inspect local state:

```text
/session create novel
/conversation new Opening structure
/status
/conversation list
/history
/search opening
/quit
```

A newly created conversation has no collaboration events. `/history` reports
state `empty`, its user meaning, and `none` as the next safe action. `/search`
does not match the conversation subject: it searches readable event fields
only, while the subject is display metadata on a hit.

An attempted story instruction is rejected without side effects:

```text
story[novel]> Please review the opening.
Direct story execution is unavailable in this integration stage. Session, conversation, history, search, and inspection remain available.
```

The semantic renderer adds an error heading and may add decoration depending on
terminal capabilities; the sentence and behavior are stable.

## Provider-free smoke

```bash
story smoke
```

Smoke creates a temporary workspace and verifies exactly these checks:

```json
{
  "checks": [
    "workspace_initialized",
    "directive_persisted",
    "conversation_catalog_persisted",
    "collaboration_history_empty",
    "collaboration_readable_context_empty",
    "collaboration_search_empty",
    "collaboration_page_empty",
    "execution_artifacts_unchanged"
  ],
  "provider_tested": false,
  "status": "passed"
}
```

The actual report is compact one-line JSON and also includes generated session
and conversation IDs plus workspace metadata. It proves management, catalog,
and empty schema-v2 projections work together. It creates no transcript,
debate, specialist state, provider exchange, or manuscript.

Use `story smoke --workspace PATH` to retain the initialized workspace,
directive, catalog metadata, and bounded diagnostics. See the
[smoke guide](docs/guides/smoke-test.md).

## Supported commands

Top-level commands are:

```text
story [chat [SESSION]]
story init [WORKSPACE]
story directive [--workspace PATH] [--session ID] TEXT
story inspect [--workspace PATH] [--session ID] [--debate ID]
story session {create|reopen|pause} ...
story storage {use|transfer|verify} ...
story rag {status|enable|rebuild|search|disable|purge} ...
story smoke [--workspace PATH]
```

There is no noninteractive story-execution command. In the shell, use `/help`
for the retained command registry. The management and inspection commands are
`/agents`, `/auth`, `/settings`, `/override`, `/session`, `/conversation`,
`/search`, `/transcript`, `/inspect`, `/exchanges`, `/history`, `/status`,
`/help`, and `/quit`.

The exact grammar is in the [CLI reference](docs/reference/cli.md) and
[interactive parser reference](docs/reference/interactive-command-parser.md).

## Python integration

`StoryRuntime` is now a management, configuration, directive, and inspection
facade. It can bind a `RuntimeSession`, but neither class has a public method
for a provider turn or debate. The package root no longer exports the broad
`Runtime` or `Session` aliases.

```python
from story_writing_agents import StoryRuntime, Workspace

workspace = Workspace("/path/to/initialized-story")
runtime = StoryRuntime(workspace)
runtime.apply_directive("novel", "[SYS: LANG=EN]")
session = runtime.session("novel")
print(session.inspect())
```

Direct schema-v2 collaboration-history values and pure projections are
available through `story_writing_agents.conversations`; direct search, paging,
and citation values are in `story_writing_agents.conversation_search`.
Filesystem/SQLite stores, archives, transfer, and activation remain separate
infrastructure boundaries. Start with the [runtime API](docs/reference/runtime-api.md),
[collaboration-history reference](docs/reference/collaboration-history.md), and
[package API](docs/reference/package-api.md).

## Canonical writing artifacts

The canonical reference-source paths remain `core_documents/...` and
lowercase-`.md` files below `manuscript/`. `${path}` and
`${path}#Lx-Ly` remain the content-free reference indicators that future
collaboration requests will carry. This revision has no public provider-bound
input span, so `$`, `$$`, and those indicators do not trigger a picker, read,
or agent requirement. See [artifact-reference status](docs/guides/artifact-references.md).

## Important limits

- No public story generation, advisory turn, debate, or artifact modification
  is active in this integration stage.
- Smoke is intentionally provider-free and story-free.
- Workspace configuration, histories, diagnostics, and artifacts are local
  plaintext unless the surrounding environment protects them.
- `/exchanges` can reveal bounded provider-call diagnostics from earlier or
  externally produced records; the viewer is not a redaction boundary.
- Conversation storage selection applies to the next shell and is separate
  from transfer. Transfer accepts schema-v2 collaboration history only.
- Optional RAG lifecycle and cited local search remain available, but the
  interactive one-turn provider preflight is not.

## Documentation

- [Documentation index](docs/INDEX.md)
- [Getting started](docs/getting-started.md)
- [Story sessions and conversations](docs/guides/story-sessions.md)
- [Lead agent and collaboration status](docs/guides/lead-agent.md)
- [Provider-free smoke](docs/guides/smoke-test.md)
- [CLI reference](docs/reference/cli.md)
- [Runtime API](docs/reference/runtime-api.md)
- [System design](docs/design/overview.md)

## Development

The Python package is under `src/story_writing_agents/`; tests are under
`tests/`; role prompts are under `config/agents/`; and
[PYTHON_MODULE_INDEX.md](PYTHON_MODULE_INDEX.md) is the production-module map.

Run the complete standard-library suite with:

```bash
python -m unittest discover -s tests -v
```

Contributors must follow [GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md),
[AGENT_TASK_RUNBOOK.md](AGENT_TASK_RUNBOOK.md), and
[COMMIT_POLICY.md](COMMIT_POLICY.md).
