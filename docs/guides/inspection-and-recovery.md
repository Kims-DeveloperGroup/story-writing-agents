# Inspect and recover work

> **Availability:** full noninteractive and safe interactive inspection are
> implemented. Debate IDs are immutable; there is no same-ID resume, deadlock
> continuation, or stale-owner cleanup command.

Key terms: **blackboard**—the public persisted debate history; **terminal
result**—a completed consensus, deadlock, or failure record; **deadlock**—
unresolved conflict at the round limit; **interrupted**—best-effort cancellation state.

Inspect the selected session's overrides and debate summaries:

```bash
story inspect
```

Inspect one debate's lifecycle state, terminal result when present, and full
validated blackboard:

```bash
story inspect --debate opening_01
```

Use explicit `--workspace` and `--session` in automation. Session summaries are
sorted by debate ID. A debate inspection can report `not_found`, `active`,
`consensus`, `deadlock`, `failed`, or `interrupted`. `active` only proves the
ID was reserved; it does not prove the creating process is alive.

## Inspect safely in the interactive shell

After attachment, no conversation selection is required:

| Command | Result |
| --- | --- |
| `/inspect` | Persisted debate summaries for the attached session. |
| `/inspect ID` | The exact current-session debate; a missing ID reports `not_found`. |
| `/inspect last` | This process's latest accepted started debate for that session; none is normal. |

`last` is process-local and per session. It is updated from accepted `started`
progress and therefore covers eventual consensus, deadlock, failure, and
interruption. It is not persisted, restored, or guessed from filesystem order.
Switching sessions preserves each process-local value.

Shell inspection whitelists identity, lifecycle, input, revision, rounds,
artifact path, and context conversation/mode/counts. It never emits blackboard
messages, candidates, stored errors/actions, context event content, raw state,
or provider-private fields. Malformed collaborator data fails closed. These
reads are provider-free and preserve attachment, selection, target, transcript,
catalog, artifacts, and provider state. The established `story inspect` and
Python inspection JSON shapes remain unchanged.

Use global `/status` for the current shell snapshot and selected `/history`
for one visible transcript snapshot plus canonical context accounting. Their
exact gates are in the [CLI reference](../reference/cli.md#interactive-streams).

## Recover from a deadlock

This copyable scenario deliberately allows no replacement after the initial
audit:

```bash
story run \
  --debate-id chapter_03_conflict \
  --output chapter-03-conflict.md \
  --max-debate-rounds 0 \
  'Write a scene that contradicts an explicit core-document rule.'
story inspect --debate chapter_03_conflict
```

If a specialist reports the conflict, `run` exits with code `3`, returns
`deadlock`, provides `action_required`, and writes no manuscript. Read the
critiques, decide the story rule, update core documents if needed, then preserve
the old attempt and start a new one:

```bash
story run \
  --debate-id chapter_03_ruling \
  --output chapter-03-ruling.md \
  'User ruling: preserve the established rule. Rewrite the scene accordingly.'
```

The original debate ID is consumed. Although its unused output path may still
be available, a new path makes provenance clearer.

## Handle other terminal states

A `failed` result describes a handled backend, reply-protocol, finalization, or
artifact problem. It may retain the latest candidate, but never resume it under
the same ID. Fix the cause and choose a new ID. An external coroutine
cancellation is re-raised after a best-effort transition to `interrupted`; it
may leave state and blackboard data without result JSON.

`DebateExistsError` happens during preflight before the backend runs.
`ManuscriptExistsError` can occur after a new debate reaches finalization; that
attempt returns failed and consumes its ID. Preserve both records and retry
with a new ID and output.

After a process, operating-system, hardware, or power failure, inspect the
workspace before replacement work. Each file write is atomic, but core files,
manuscript, logs, state, and result do not share one transaction; best-effort
rollback can itself fail.

Inspection omits the internal debate `owner_token`. It validates persisted JSON
and JSONL rather than repairing malformed data. Exact response shapes are in
[Runtime API](../reference/runtime-api.md); limits are in
[Recovery and limitations](../operations/recovery-and-limitations.md).
