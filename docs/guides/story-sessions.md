# Story sessions and conversations

The TTY shell manages and inspects session/conversation state. After a ready
session is attached and an active conversation is focused, ordinary non-slash
input goes to the Lead; the runtime keeps provider calls and effects behind
the documented authority boundaries.

## State in user terms

| State | What it means to you |
| --- | --- |
| Session `ready` | The durable session may be attached. It does not mean story execution is currently exposed. |
| Session `paused` | Provider work is durably stopped until the session is reopened. |
| Access `held_here` | This shell owns the session claim. |
| Access `claimable` | No live process claim blocks a new attachment. |
| Default session | The saved session used when a later command omits an ID. |
| Current attachment | The session this shell is managing now. |
| Current conversation | The active conversation used by history and other scoped reads. |

These facts are separate. For example, a session can be durable `ready` and
`claimable` after `/quit`: the data remains ready, but the exited process no
longer owns access.

## Session workflow

Start with `story` or `story chat [SESSION]`. The interactive commands are:

```text
/session list
/session create NAME
/session resume [NAME]
/session pause
```

- Create verifies an absent lifecycle, validates configuration, provisions the
  exact session Git resources, creates a ready lifecycle, and assesses entry
  before attaching and saving the default.
- Resume attaches a ready session or reopens a paused session. Omitting the
  name opens a picker when choices exist. Entry is assessed after the
  destination's active persisted conversation default is loaded, if one
  exists.
- Switching assesses the acquired destination first, then assesses whether the
  current source may be left. The destination default changes only after both
  operations are allowed.
- Pause asks for confirmation, then assesses the attached session before it
  persists `paused`, releases access, and clears this shell's attachment.
- `/quit` and EOF release access without pausing. Defaults remain.

The noninteractive lifecycle surface uses `create`, `reopen`, and `pause`:

```bash
story session create novel --workspace .
story session pause novel --workspace .
story session reopen novel --workspace .
```

Each successful command prints JSON containing `session_id`, `durable_state`,
`access_state`, and a content-safe `assessment`. Create and reopen return after
releasing their temporary claim, so successful noninteractive reports are
ready and claimable. A denied transition prints JSON and exits 2 without
performing that transition.

Create now provisions and verifies the exact Git branch, sibling worktree, and
internal ref before lifecycle creation. Reopen does not create missing
resources: transition assessment requires the already-correlated triple. The
Git-resource states `absent`, `provisioning`, `ready`, `outcome_unknown`, and
`recovery_required` remain a separate state machine documented in
[Workspaces](workspaces.md#session-git-resources).

Create/Reopen do not roll back completed resource or paused-to-ready lifecycle
work when a later assessment or default step fails. They release a newly held
claim when possible and preserve the resulting durable facts. Inspect the
reported state before retrying.

### What transition assessment means

An eligible persisted conversation default makes assessment `active`; a
missing or archived default makes it `idle`. Active assessment preserves the
nested correlated recovery facts. Idle assessment instead requires two equal
observations of lifecycle, resources, history, external Git, and linked-
worktree status.

| Assessment shown to you | Allowed lifecycle operations | What to do next |
| --- | --- | --- |
| `idle_ready` / `begin_with_lead` | Enter, leave, pause | Continue through the lead. |
| Active `ready`, `proposed`, `reviewed`, `awaiting_application`, `committed`, or `rejected` / `follow_active_recovery` | Enter, leave, pause | For `ready`, begin the first lead turn. Otherwise follow the nested recovery result's exact next action. |
| Active `applied_not_committed` or `staged` / `follow_active_recovery` | Enter only | Attach to inspect; do not leave or pause the owner. |
| `outcome_unknown` / `inspect_owner_outcome`, owner `collaboration_history`, failure `owner_outcome_unknown` | Enter only | Attach to inspect an unmatched submitted Lead call; do not replay it, leave the owner, or pause the session. |
| Other `outcome_unknown` / `inspect_owner_outcome` | None | Preserve evidence and inspect the named owner without entering. |
| `recovery_required` / `operator_recovery` | None | Stop automated mutation and perform explicit operator recovery. |

For ordinary active progress the outer `owner` is null and `failure_code` is
`none`; the nested result names the workflow owner and its exact next action.
An exact zero-event conversation is active `ready`, owned by the Lead, with
`continue_with_lead`; it is not a missing predecessor. The runtime reaches that
state only after the same stable resource, history, external-Git, status, and
tip checks used for later active recovery.
For uncertainty or recovery, the outer result copies the nested owner and
failure code. It normally allows nothing. The only exception is the exact
`collaboration_history` / `owner_outcome_unknown` result for a proposal-free,
unmatched submitted Lead call: it allows `ENTER_DESTINATION` so the user can
inspect that conversation. It does not allow leaving or pausing the owner.
Assessment invokes no story provider and does not reveal or recreate
confirmation choices, content, or capability. An allowed entry is not replay,
resolution, Apply, write, or Commit authority; those effects retain their
existing runtime and confirmation boundaries.

See the [CLI JSON examples](../reference/cli.md#session-transition-json) and
[complete recovery matrix](../operations/recovery-and-limitations.md#session-transition-assessment).

## Conversation workflow

After attachment, these commands manage catalog metadata and local history:

```text
/conversation new [SUBJECT]
/conversation list [--all] [QUERY]
/conversation resume [ID]
/conversation subject SUBJECT
/conversation archive [ID]
/history [COUNT]
/search QUERY
/transcript ID [COUNT] [--from SEQUENCE]
```

New creates and focuses an active conversation. Resume accepts an exact ID or
an eligible unique prefix; omitting it opens a picker. Subject changes the
focused display subject. Archive removes a conversation from the active list
without deleting its stored identity/history. `--all` includes archives.
Archived history remains part of required Lead session memory and can be sent
to the configured provider. Archive is not a privacy opt-out or deletion.

`/history` reads the focused schema-v2 collaboration history and explains its
current state and next safe action. `/search` performs bounded literal search
over readable event fields in active conversations. Subjects are display-only
metadata and never create a hit. `/transcript` reads a fresh bounded structured
page for an active or archived conversation without changing focus. These
reads do not call a provider or mutate history.

Example:

```text
/session create novel
/conversation new Opening structure
/conversation list
/history 20
/search opening
/status
```

In a new workspace, the list contains the newly created catalog entry while
history reports `empty` and `/search opening` returns zero matching readable
fields. Catalog metadata is not a synthetic collaboration event.

When events exist, each history/transcript row identifies sequence, turn,
step, kind, direction, actors, and closed readable fields. Search citations
identify the exact conversation, event ID, sequence, field, and code-point
range. See [Collaboration history](../reference/collaboration-history.md) for
the state and next-action meanings.

<a id="edit-persistent-settings"></a>

## Directives and settings

Attached-session directives are:

```text
/override language LANG
/override persona AGENT NAME
/override reset
```

`/settings` edits persistent workspace choices such as language, persona,
debate-round policy, storage, local RAG policy, message details, and agent
provider profiles. Values remain pending until Apply Changes is confirmed.
Storage selection applies to the next shell. `/auth` refreshes native provider
status and can run the provider's sign-in flow; it does not send a story prompt.

These configuration controls remain available so the later provider-bound
lead/specialist workflow can use the same persisted choices.

## Inspection

```text
/agents
/status
/inspect [DEBATE|last]
/exchanges
/help [COMMAND]
```

`/agents` shows configured execution profiles. `/status` separates durable
state, process access, defaults, attachment, focus, storage, and provider
readiness. `/inspect` reads persisted debate metadata if such data exists.
`/exchanges` opens its scope and agent pickers and then the isolated read-only
JSONL viewer; it never invokes or replays a provider.

Inspection surfaces show persisted collaboration created by focused Lead turns,
confirmed steps, or direct lower-level integrations. Inspection itself never
invokes or replays a provider.

<a id="execution-identity-and-fresh-thread-continuity"></a>

## What happens to a story instruction

After attachment and active conversation focus, ordinary non-slash text is
sent literally to the Lead. Leading agent-like text is still Lead input; it is
not a specialist route. Retired slash execution forms remain unavailable and
are absent from help/completion. `/run` is unknown. Before a focused Lead turn,
the controller requires an attached `ready` session and one focused active
conversation. It then runs reference/context validation and provider work
through `RuntimeSession.lead_turn()`, records collaboration history, and shows
content-free progress. A Lead turn never implicitly mutates an artifact;
review, Apply, and Commit remain separately confirmed. In focused Lead input,
`$` selects a whole canonical artifact and `$$` selects an artifact before
opening the independent range viewer. The inserted durable forms are
`${path}` and `${path}#Lstart-Lend`.

The Lead shares compatible provider continuation across this session's
conversations, including its consultation and artifact-review syntheses.
Pause/reopen and restart retain that continuity when compatibility holds;
replacement reconstructs all retained active and archived history plus current
directives and core documents. Specialists and consensus keep separate scopes.
Memory does not carry current evidence or confirmation authority, and exact
delivery can fail its finite capacity limit. See
[Lead session memory](agent-context.md#lead-session-memory).

## Recovery

- Use `/status` after an interrupted lifecycle or settings operation.
- When a transition record shows `outcome_unknown`, preserve all evidence and
  follow `inspect_owner_outcome` for the displayed owner. If that owner is
  `collaboration_history` with failure `owner_outcome_unknown`, the shell may
  enter only to inspect the unmatched submitted Lead call. Ordinary input then
  returns the same preserved `State: outcome_unknown` and
  `Next action: inspect_and_resolve_outcome` without a provider replay or new
  durable user event. Other outcome-unknown owners remain blocked from entry.
  When the record shows `recovery_required`, stop automated mutation and
  follow `operator_recovery`.
- Entry for inspection does not authorize replay, Apply, artifact writes,
  Commit, leaving, pausing, or resolution of the uncertain call. Use
  `/conversation new SUBJECT` for intentionally distinct work.
- Do not use reset, abort, cleanup, claim deletion, or default removal to make
  an uncertain session appear idle or safe.
- Use `/session list` and `/conversation list --all` to rediscover IDs.
- Use `/inspect` only for persisted legacy debates; Lead collaboration is read
  through `/history` and `/transcript`.
- Use `/quit` to release access without changing durable readiness; use
  confirmed `/session pause` when durable pause is intended.

See the [CLI reference](../reference/cli.md),
[session lifecycle design](../design/session-lifecycle.md), and
[inspection guide](inspection-and-recovery.md).
