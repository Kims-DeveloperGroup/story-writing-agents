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
| Initialize | `story init [WORKSPACE]` |
| Directive | `story directive [--workspace PATH] [--session SESSION] TEXT` |
| Inspect | `story inspect [--workspace PATH] [--session SESSION] [--debate DEBATE]` |
| Session | `story session {create,reopen,pause} ...` |
| Storage | `story storage {use,transfer,verify} ...` |
| Local RAG | `story rag {status,enable,rebuild,search,disable,purge} ...` |
| Smoke | `story smoke [--workspace PATH]` |

There is no noninteractive direct-run grammar. Supplying an unknown top-level
command is an argparse usage error.

Unless a command documents otherwise, omitted `--workspace` discovers and
validates the nearest recognizable workspace boundary. Discovery never skips
an unsupported or malformed inner boundary to select an outer workspace.
Omitted `--session` uses the workspace default. Identifiers and workspace paths
are validated before the operation.

## Initialize

```text
story init [WORKSPACE]
```

The default workspace is `.`. Initialization accepts an absent root, an empty
safe directory, or one exact journal-backed interrupted-v2 root. It creates the
strict schema-v2 marker, exact 25-file scaffold, four zero-byte core documents,
and one parentless `main` commit named `Initialize story workspace` using the
effective Git author and committer. It emits no post-baseline diagnostic or
untracked residue.

Success is pretty-printed JSON:

```json
{
  "workspace": "/absolute/path/to/workspace"
}
```

When stdin and stdout are terminals, successful initialization then
enters the normal interactive shell for the absolute initialized workspace,
equivalent to `story chat --workspace /absolute/path/to/workspace`. The usual
authentication, default-provider selection, and session lobby apply. The
command returns the shell's exit status. Initialization failures never enter
the shell. Redirecting stdin or stdout retains provider-free JSON output and exit
status zero on initialization success. The parent terminal's directory does
not change.

Rerunning init against the unchanged ready root validates only; it does not
rewrite, stage, or create another commit.
Successful terminal validation also enters the shell. There is no prompt-overwrite,
migration, compatibility, upgrade, or adoption option. A recognizable legacy
workspace reports:

```text
WorkspaceVersionUnsupportedError: workspace version is unsupported; migration and compatibility are unsupported, so create a new workspace
```

Missing effective identity reports:

```text
ConfigurationError: effective Git identity is unavailable or invalid
```

That failure can leave the exact journal/scaffold but creates no absent Git
repository. Configure a valid Git identity and run a fresh command to resume.
Malformed required state, markerless arbitrary content, a partial mismatch,
staged/disallowed tracked state, tracked unknown additions, nested Git, or an
unsafe root are preserved and rejected. On a journal-free ready root, ignored
and untracked entries are opaque and do not block validation; they are not
trusted or consumed. Initialization failures occur before a usable workspace
is available and therefore never add the debug-log hint.

## Session lifecycle

```text
story session create SESSION [--workspace PATH]
story session reopen [SESSION] [--workspace PATH]
story session pause [SESSION] [--workspace PATH]
```

Create requires a new explicit ID. Reopen and pause may use the saved default.
Create verifies absence, validates provider configuration, provisions and
verifies the exact session Git resources, creates/acquires the lifecycle,
correlates a post-read, assesses `enter_destination`, saves the default only
when allowed, then releases access. Reopen pre-reads an existing lifecycle,
validates configuration, acquires ready state or durably reopens paused state,
correlates a post-read, resolves active/default conversation evidence, assesses
`enter_destination`, saves the default only when allowed, then releases.

Pause pre-reads lifecycle state and acquires access only when needed. It
resolves the same active/default conversation evidence, assesses
`pause_session`, and persists `paused` plus release only when allowed. A denied
pause releases only a claim acquired by that command; a claim already held by
the caller is retained. Lifecycle assessment calls no story provider.

An eligible persisted active conversation default and the configured catalog's
same-provenance event store produce active evidence. A missing default, missing
catalog record, or archived default produces idle evidence without rewriting
the saved selection. Create deliberately assesses its newly created session as
idle. Active assessment includes the exact nested recovery result; idle
assessment requires stable clean resource/history/Git observations.

<a id="session-transition-json"></a>

### Session-transition JSON

Successful Create with stable idle evidence prints:

```json
{
  "access_state": "claimable",
  "assessment": {
    "allowed_operations": [
      "enter_destination",
      "leave_source",
      "pause_session"
    ],
    "conversation_id": null,
    "failure_code": "none",
    "next_action": "begin_with_lead",
    "owner": null,
    "recovery_result": null,
    "scope": "idle",
    "session_id": "novel",
    "state": "idle_ready"
  },
  "durable_state": "ready",
  "session_id": "novel"
}
```

Pause success has the same top-level shape and reports
`durable_state: "paused"`. Reopen of an active awaiting-application workflow
retains request `R9` and every fixed nested field; it does not expose revisions,
digests, confirmation choices, story content, or capability:

```json
{
  "access_state": "claimable",
  "assessment": {
    "allowed_operations": [
      "enter_destination",
      "leave_source",
      "pause_session"
    ],
    "conversation_id": "review_1",
    "failure_code": "none",
    "next_action": "follow_active_recovery",
    "owner": null,
    "recovery_result": {
      "application_id": null,
      "checkpoint_operation_id": null,
      "collaboration_tail_sequence": 9,
      "commit_id": null,
      "confirmation_request_id": "R9",
      "conversation_id": "review_1",
      "correlation_id": "correlation-9",
      "failure_code": "none",
      "next_action": "submit_application_decision",
      "owner": "lead",
      "proposal_id": "proposal-9",
      "reviewed_candidate_id": null,
      "session_id": "story_review",
      "state": "awaiting_application",
      "story_commit_candidate_id": null,
      "story_commit_operation_id": null
    },
    "scope": "active",
    "session_id": "story_review",
    "state": "active"
  },
  "durable_state": "ready",
  "session_id": "story_review"
}
```

An assessment denial also prints JSON and exits 2. Its `assessment` contains
the state, next action, owner, failure, allowed operations, and any nested
active recovery facts; `transition_denial` is null. For example, active
`staged` permits only `enter_destination`, so Pause is denied while leaving the
durable/default/conversation state unchanged.

Create/Reopen assessment occurs after their lifecycle/resource work. That work
is preserved rather than rolled back: a denied Create can leave verified
resources and a ready lifecycle, and a denied Reopen of a paused session can
leave it ready after release, although the new default is not saved. Read the
reported durable/access fields and inspect recovery evidence before retrying.

A lifecycle-only denial happens before transition assessment. For example,
Reopen against a ready session held by another live local owner exits 2 with no
stderr and prints:

```json
{
  "access_state": "blocked_by_live_local_owner",
  "assessment": null,
  "durable_state": "ready",
  "session_id": "novel",
  "transition_denial": "lifecycle_not_ready_held_here"
}
```

The same fixed `transition_denial` covers another lifecycle state that cannot
reach the required local `ready + held_here` precondition; the exact
`access_state` and `durable_state` explain which fact blocked it. No default is
changed, no assessment is fabricated, and no provider runs.

These JSON fields report lifecycle and transition evidence; they do not grant
Apply, stage, commit, reset, abort, cleanup, or provider authority. Create now
provisions the exact Git branch, sibling worktree, and internal ref. Reopen and
Pause assess the existing correlated resources and never create missing ones.
Runtime content writes still occur only through the existing lead/user
confirmation path. See the [complete operation matrix and recovery behavior](../operations/recovery-and-limitations.md#session-transition-assessment).

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

Without a path, smoke creates and removes a temporary workspace through the
real initializer. Its exact successful `checks` array is:

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
`No LLM or external provider was called.` The temporary path can truthfully
fail for missing Git identity and is removed even on failure.

With `--workspace`, smoke requires an already ready, clean v2 root. It performs
no initialization or diagnostic submission and returns only:

```json
{
  "checks": [
    "workspace_initialized",
    "execution_artifacts_unchanged"
  ],
  "command": "smoke",
  "conversation_id": null,
  "note": "No LLM or external provider was called.",
  "provider_tested": false,
  "session_id": null,
  "status": "passed",
  "workspace": {
    "mode": "persistent",
    "path": "/absolute/path/to/workspace",
    "retained": true
  }
}
```

Actual output is compact one-line JSON with sorted keys. The explicit form
creates no directive, conversation, history, agent state, debug log, or other
residue. A named temporary-mode invariant failure adds `failed_check` and
returns code 1. See the [smoke guide](../guides/smoke-test.md).

<a id="interactive-streams"></a>
<a id="interactive-provider-authentication"></a>

## Interactive startup

The interactive adapter requires a usable TTY. It loads the workspace, resolves
and activates the selected conversation-storage bundle, checks native Codex and
Antigravity authentication, resolves effective agent provider profiles, then
constructs one controller. Provider authentication can run native sign-in and
may open the provider's browser flow.

After attachment and conversation focus, ordinary non-slash input calls the
Lead workflow. Startup authentication/configuration does not itself authorize
specialist, artifact, or Git effects.

`story chat --codex-timeout SECONDS` retains its legacy name and sets the
positive response timeout for both Codex and Antigravity. The default is 900
seconds, including bare `story` startup. This is a per-call limit; a Lead turn
can make multiple calls. Antigravity uses native timeout `N` and an outer
watchdog of `N + 30` seconds (900/930 by default). Codex uses only its watchdog
at `N` (900 by default). The override is a `chat` option.

When an eligible read-only Lead attempt fails or times out, follow
`retry_lead_turn`: send a new message, revise the wording, or resubmit the
request in the same conversation, including after restart. Runtime preserves
the original request and failure and starts a fresh turn only after explicit
input. See [Lead recovery](../guides/lead-agent.md#submitted-lead-outcome-unavailable-after-restart).

## Interactive command registry

| Group | Grammar |
| --- | --- |
| Agents/configuration | `/agents`; `/auth`; `/settings`; `/override language LANG \| persona AGENT NAME \| reset` |
| Sessions | `/session list \| create NAME \| resume [NAME] \| pause` |
| Conversations | `/conversation new [SUBJECT] \| list [--all] [QUERY] \| resume [ID] \| subject SUBJECT \| archive [ID]` |
| Inspection | `/search QUERY`; `/transcript [ID [COUNT] [--from SEQUENCE]]`; `/inspect [DEBATE\|last]`; `/exchanges`; `/history [COUNT]`; `/status` |
| Display | `/progress [on\|off\|toggle]` (alias `/toggle`) |
| Help/exit | `/help [COMMAND]` (alias `/?`); `/quit` |

`/search` and `/transcript` require an attachment. Conversation mutations also
require attachment; history requires an active focus. Directives and debate
inspection are session-scoped but do not require conversation focus.

Bare `/transcript` opens the attached session's active and archived conversation
picker, in catalog order. Choices show `Subject (conversation_id)` with full IDs.
Selection resolves the ID again before reading one fresh validated snapshot.
An empty catalog, picker cancellation, Ctrl-C, or EOF reads no transcript and
preserves attachment, focus, and history. An explicit ID skips the picker.
Without `COUNT`, browse the complete conversation from sequence 1 or `--from`;
with a count, browse the existing bounded page. Close with `q`, Escape, Ctrl-C,
or EOF. Like `/exchanges`, closing clears the viewer and restores the preceding
shell screen without transcript rows, headings, results, or hints in scrollback.
Unsupported terminals receive only a content-free notice.

Ordinary non-slash text is literal Lead input. It requires an attached session
and focused active conversation; no agent selector is accepted. The retired
slash commands `/agent`, `/cancel`, `/compose`, `/draft-file`, `/rag`, `/say`,
`/send`, and `/to` still produce this guidance sentence:

```text
Direct story execution is unavailable in this integration stage. Session, conversation, history, search, and inspection remain available.
```

`/run` is an ordinary unknown command, and there is no `story run` subcommand.
Removed commands are absent from help and static completion. See the
[parser reference](interactive-command-parser.md). Lead proposals use exact
Confirm/Reject/Revise controls; artifact Apply and local Commit have separate
controls and authority.

### Premise choices

`/premise` takes no arguments. With an attached session and focused conversation, it shows
the exact current provisional analyst report and opens structured choose/reject/defer
controls. The shell supplies the report pins; users choose whole option text and the
disclosed targets/task transition. A minor delegation requires an exact option allowlist.
Opening the control and reject/defer remain provider-free. A valid choice may invoke the
Lead for a fresh authoring proposal, whose specialist execution still needs Confirm.
Pending Apply/Commit, running work, and uncertain outcomes retain priority.

See [premise states and costs](../guides/lead-agent.md#choosing-an-unresolved-premise).

Target paths are collected one at a time; submit an empty entry to finish. An empty first entry requests targetless work. Cancel and EOF leave the collection unsubmitted. The shell offers minor delegation only when the chosen whole option is the report recommendation. A retained selection from an earlier report does not restrict Reject or Defer for the current report.

### Explicit exploration controls

With an attached session and focused active conversation, `/explore TEXT`
selects exploration for that one turn. Its report is labeled
`Exploration (provisional)`; the next ordinary line returns to normal Lead
orchestration.

`/adopt` asks for a source conversation, a provisional idea, whole or partial
extent, Authoring or Review only, and canonical target paths. A partial extent
uses an explicit `START:END` range of zero-based Unicode code points, with an
exclusive end. The shell shows the exact selected text, range, source result,
source and selected-text digests, owner conversation, use and targets before
submission. Cancel, EOF or an invalid selection starts no task or provider work.

The source can be another conversation of the same session. Adoption starts a
fresh owner task with new confirmation. Authoring requires drafting and complete
final candidate review; Review only evaluates the exact supplied text without
rewriting. Confirm, Apply and Commit remain separate controls. Pending decisions
and recovery boundaries across the session take priority and identify the owner
whose control must be resolved.

See [exploration and adoption](../guides/lead-agent.md#exploration-and-adoption).

<a id="runtime-debug-logs"></a>

## Errors, diagnostics, and exits

Argparse owns help and syntax exits. Most ordinary command failures return 2
and print a sanitized exception/error. Domain report commands such as storage,
RAG, and smoke use their documented JSON report exit codes.

When a ready explicit workspace has already been resolved, later failures can name
`debug_logs/runtime.jsonl`. The hint means only that a bounded diagnostic may
exist; logging failure never replaces the primary result. Provider credentials,
native authentication output, and raw provider stderr are not included in the
fixed CLI error text. Initialization/discovery failures, explicit read-only
smoke, and implicit temporary smoke never add this hint.
