# Story sessions and conversations

The TTY shell currently manages and inspects session/conversation state. It
does not execute story instructions.

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

- Create makes an absent session ready, attaches it, and saves it as default.
- Resume attaches a ready session or reopens a paused session. Omitting the
  name opens a picker when choices exist.
- Switching acquires the destination before releasing the source.
- Pause asks for confirmation, persists `paused`, releases access, and clears
  this shell's attachment.
- `/quit` and EOF release access without pausing. Defaults remain.

The noninteractive lifecycle surface uses `create`, `reopen`, and `pause`:

```bash
story session create novel --workspace .
story session pause novel --workspace .
story session reopen novel --workspace .
```

Each successful command prints JSON containing `session_id`, `durable_state`,
and `access_state`. Create and reopen return after releasing their temporary
claim, so successful noninteractive reports are ready and claimable.

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

`/history` reads the focused visible schema-v1 transcript snapshot retained by
the current selected service. `/search` performs bounded literal search in the
attached session. `/transcript` reads a fresh bounded transcript page for the
requested conversation without changing focus. These reads do not call a
provider. Schema-v2 history becomes the sole selected inspection service only
after #479.

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
history contains no visible events and search can match the subject. Catalog
metadata is not a synthetic conversation turn.

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

These configuration controls remain available so the active collaboration
cutover can use the same persisted choices.

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

Inspection surfaces can therefore show data created by lower-level tools or an
earlier integration even though this shell cannot create new story-execution
records.

<a id="execution-identity-and-fresh-thread-continuity"></a>

## What happens to a story instruction

Ordinary text, agent-routed text, and each retired execution command receive
the same guidance:

```text
Direct story execution is unavailable in this integration stage. Session, conversation, history, search, and inspection remain available.
```

This includes ordinary prose, a leading agent route, and the former direct-run,
draft, message, target, and one-turn RAG forms. They are absent from help and
completion. The parser/controller rejects them before:

- provider bind or call;
- transcript or schema-v2 append;
- RAG or artifact-reference read;
- progress callback; or
- core-document/manuscript mutation.

Typing `${core_documents/global_outline.md}` in an ordinary line does not
change this result. No currently public terminal span activates `$` or `$$`.

## Recovery

- Use `/status` after an interrupted lifecycle or settings operation.
- Use `/session list` and `/conversation list --all` to rediscover IDs.
- Use `/inspect` only for persisted debates; an absent result is expected in a
  fresh management-only workspace.
- Use `/quit` to release access without changing durable readiness; use
  confirmed `/session pause` when durable pause is intended.

See the [CLI reference](../reference/cli.md),
[session lifecycle design](../design/session-lifecycle.md), and
[inspection guide](inspection-and-recovery.md).
