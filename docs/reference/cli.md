# CLI reference

`story` and `story-writing-agents` are exact aliases. The installed CLI exposes
management, inspection, storage, local RAG, and provider-free smoke. It does
not expose a story-execution subcommand.

## Top-level grammar

```text
story [-h] {chat,init,directive,inspect,session,storage,rag,smoke} ...
```

Bare `story` is equivalent to interactive entry without an explicit session.

| Command | Syntax |
| --- | --- |
| Interactive | `story [chat [SESSION]] [--workspace PATH] [--backend AGENT=BACKEND]... [--codex-model MODEL] [--antigravity-executable EXECUTABLE] [--antigravity-model MODEL] [--antigravity-effort EFFORT] [--codex-timeout SECONDS]` |
| Initialize | `story init [WORKSPACE] [--overwrite-prompts]` |
| Directive | `story directive [--workspace PATH] [--session SESSION] TEXT` |
| Inspect | `story inspect [--workspace PATH] [--session SESSION] [--debate DEBATE]` |
| Session | `story session {create,reopen,pause} ...` |
| Storage | `story storage {use,transfer,verify} ...` |
| Local RAG | `story rag {status,enable,rebuild,search,disable,purge} ...` |
| Smoke | `story smoke [--workspace PATH]` |

There is no noninteractive direct-run grammar. Supplying an unknown top-level
command is an argparse usage error.

Unless a command documents otherwise, omitted `--workspace` discovers the
nearest initialized ancestor. Omitted `--session` uses the workspace default.
Identifiers and workspace paths are validated before the operation.

## Initialize

```text
story init [WORKSPACE] [--overwrite-prompts]
```

The default workspace is `.`. Initialization creates missing canonical
directories/configuration and preserves existing files. `--overwrite-prompts`
replaces only the four canonical role `AGENTS.md` files. Success prints the
resolved workspace path as JSON.

## Session lifecycle

```text
story session create SESSION [--workspace PATH]
story session reopen [SESSION] [--workspace PATH]
story session pause [SESSION] [--workspace PATH]
```

Create requires a new explicit ID. Reopen and pause may use the saved default.
Create/reopen temporarily acquire the session, validate lifecycle state, save
the default, then release access. Pause persists `paused` and releases access.

Success prints:

```json
{
  "access_state": "claimable",
  "durable_state": "ready",
  "session_id": "novel"
}
```

Pause reports `durable_state:"paused"`.

## Directives and inspection

```text
story directive [--workspace PATH] [--session SESSION] TEXT
story inspect [--workspace PATH] [--session SESSION] [--debate DEBATE]
```

Directive applies one exact supported session directive. Inspect without
`--debate` returns the persisted session summary; with it, inspect returns that
debate's safe lifecycle/result view. These calls do not start provider work.
Inspection can append bounded runtime diagnostics, but it does not mutate
session/debate state.

<a id="conversation-storage-transfer"></a>

## Conversation-storage selection

```text
story storage use BACKEND [--workspace PATH] [--install-missing --yes]
```

`BACKEND` must be `filesystem` or `sqlite`. Selection records the desired
backend for a later shell; it does not transfer data or change the current
process bundle. `--install-missing` and `--yes` must appear together and are
valid only for SQLite. The JSON report has a stable operation/outcome/failure
shape. Review the host command before authorizing installation.

Transfer and parity verification are separate:

```text
story storage transfer --from {filesystem,sqlite} --to {filesystem,sqlite} [--workspace PATH] [--dry-run]
story storage verify --from {filesystem,sqlite} --to {filesystem,sqlite} [--workspace PATH]
```

The endpoints must differ, both stores belong to the same workspace, and the
source must be active. Transfer copies canonical schema-v2 conversation data;
it never activates or deletes a backend. `--dry-run` plans without copying.
See [SQLite conversation storage](../guides/sqlite-conversation-storage.md).

<a id="conversation-rag-operations"></a>

## Local conversation RAG

```text
story rag status [--workspace PATH] [--session SESSION]
story rag enable --provider PROVIDER [--workspace PATH]
story rag rebuild [--yes] [--workspace PATH] [--session SESSION]
story rag search QUERY [--workspace PATH] [--session SESSION]
story rag disable [--workspace PATH]
story rag purge [--yes] [--workspace PATH] [--session SESSION]
```

These commands manage or query a local derived index. Status is provider-free;
enable changes configuration; rebuild and purge require explicit confirmation
for mutation; search emits bounded cited local results. The optional pinned
RAG dependency is required for provider-backed local indexing/search.

These commands do not call a story agent. The former interactive one-turn RAG
arming form is not part of the current parser. See
[Conversation RAG](../guides/conversation-rag.md).

<a id="smoke"></a>

## Smoke

```text
story smoke [--workspace PATH]
```

Without a path, smoke creates and removes a temporary workspace. With a path,
it initializes/uses and retains that workspace. The exact successful `checks`
array is:

```text
workspace_initialized
directive_persisted
conversation_catalog_persisted
collaboration_history_empty
collaboration_readable_context_empty
collaboration_search_empty
collaboration_page_empty
execution_artifacts_unchanged
```

The report also contains `command`, `status`, `provider_tested:false`, workspace
metadata, generated session/conversation IDs, and the note
`No LLM or external provider was called.` A named invariant failure adds
`failed_check` and returns code 1. See the [smoke guide](../guides/smoke-test.md).

<a id="interactive-streams"></a>
<a id="interactive-provider-authentication"></a>

## Interactive startup

The interactive adapter requires a usable TTY. It loads the workspace, resolves
and activates the selected conversation-storage bundle, checks native Codex and
Antigravity authentication, resolves effective agent provider profiles, then
constructs one controller. Provider authentication can run native sign-in and
may open the provider's browser flow.

This startup configuration gate does not make direct story input callable.
Accepted interactive operations are management and inspection operations.

## Interactive command registry

| Group | Grammar |
| --- | --- |
| Agents/configuration | `/agents`; `/auth`; `/settings`; `/override language LANG \| persona AGENT NAME \| reset` |
| Sessions | `/session list \| create NAME \| resume [NAME] \| pause` |
| Conversations | `/conversation new [SUBJECT] \| list [--all] [QUERY] \| resume [ID] \| subject SUBJECT \| archive [ID]` |
| Inspection | `/search QUERY`; `/transcript ID [COUNT] [--from SEQUENCE]`; `/inspect [DEBATE\|last]`; `/exchanges`; `/history [COUNT]`; `/status` |
| Help/exit | `/help [COMMAND]` (alias `/?`); `/quit` |

`/search` and `/transcript` require an attachment. Conversation mutations also
require attachment; history requires an active focus. Directives and debate
inspection are session-scoped but do not require conversation focus.

Ordinary text, leading agent routes, and every retired direct-execution command
produce this exact guidance sentence:

```text
Direct story execution is unavailable in this integration stage. Session, conversation, history, search, and inspection remain available.
```

Rejection occurs before provider, transcript, RAG, artifact-reference,
progress, or artifact effects. Removed commands are absent from help and static
completion. See the [parser reference](interactive-command-parser.md).

<a id="runtime-debug-logs"></a>

## Errors, diagnostics, and exits

Argparse owns help and syntax exits. Most ordinary command failures return 2
and print a sanitized exception/error. Domain report commands such as storage,
RAG, and smoke use their documented JSON report exit codes.

When an initialized explicit workspace is available, later failures can name
`debug_logs/runtime.jsonl`. The hint means only that a bounded diagnostic may
exist; logging failure never replaces the primary result. Provider credentials,
native authentication output, and raw provider stderr are not included in the
fixed CLI error text.
