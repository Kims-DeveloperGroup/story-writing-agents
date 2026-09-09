# Inspect and recover work

Inspection remains available for management state and persisted collaboration,
debate, and reference records. The Python runtime can create lead turns and
decisions; it cannot create a debate or specialist execution.

## Noninteractive inspection

Inspect the explicit or default session:

```bash
story inspect --workspace . --session novel
```

Inspect one persisted debate:

```bash
story inspect --workspace . --session novel --debate debate_id
```

Session inspection returns overrides plus debate summaries sorted by ID. A
debate inspection can report `not_found`, `active`, `consensus`, `deadlock`,
`failed`, or `interrupted`. `active` proves only that state was persisted; it
does not prove another process is alive.

## Interactive inspection

With a session attached:

| Command | Result |
| --- | --- |
| `/status` | Current defaults, attachment/focus, durable/access state, storage, and provider readiness. |
| `/inspect` | Persisted debate summaries for the attached session. |
| `/inspect ID` | Safe facts for one exact debate. |
| `/inspect last` | This process's most recent accepted legacy debate start, if any. Lead collaboration uses conversation history instead. |
| `/history [COUNT]` | Focused schema-v2 collaboration records, durable state, user meaning, and next-safe-action meaning. |
| `/transcript [ID [COUNT] [--from SEQUENCE]]` | Bounded structured page without changing focus; omit ID to pick an active or archived conversation in the attached session. |
| `/exchanges` | Read-only viewer over an immutable stored exchange snapshot. |

The transcript picker shows `Subject (conversation_id)` with full IDs in catalog
order and resolves the selected ID again before reading. An empty catalog,
cancellation, Ctrl-C, or EOF reads no transcript and leaves attachment, focus,
and conversation history unchanged.

Inspection is provider-free. Runtime inspection wrappers may best-effort append
a bounded operational diagnostic, but they do not change session/debate state,
conversation history, provider state, or artifacts.

`/search` covers readable event fields in active conversations only; the
displayed subject does not create a match. A hit's exact event/field/range
citation can be followed with `/transcript`. A continued direct-API page is
valid only for its unchanged snapshot; a stale cursor means to restart the
read, not join results across an append. See
[Conversation search](../reference/conversation-search.md).

## Existing terminal debates

A deadlocked, failed, or interrupted persisted debate remains inspectable. Its
ID is immutable. This revision has no public command that resumes, extends, or
copies it into a new run. Preserve the record and wait for the structured
lead/specialist workflow rather than reconstructing an earlier direct path.

For a persisted result:

- `deadlock` means the recorded conflict did not reach unanimous approval;
- `failed` means a handled provider/protocol/finalization/artifact failure was
  recorded; and
- `interrupted` means the lifecycle reached its trusted interrupted state.

An absent result can accompany active or interrupted state. Inspection
validates stored JSON/JSONL and does not repair malformed bytes.

## Recovery checklist

1. Run `/status` or `story inspect` to establish durable state before retrying
   a management action.
2. Use `/session list` and `/conversation list --all` to rediscover exact scope.
3. Use `story session reopen` for a paused default, or `/session resume` inside
   the shell.
4. Treat a missing debug log as normal; logging is fail-open.
5. Do not infer that an absent exchange record means no historical provider
   call occurred.
6. Do not alter a publication journal or persisted debate by hand. Filesystem
   recovery validates and rolls forward only its documented safe states.

See [Runtime and lead-orchestration API](../reference/runtime-api.md),
[Story sessions](story-sessions.md), and
[Recovery and limitations](../operations/recovery-and-limitations.md).
