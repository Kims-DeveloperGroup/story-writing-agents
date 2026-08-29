# Inspect and recover work

> **Availability:** full noninteractive and safe interactive inspection are
> implemented. Debate IDs are immutable; there is no same-ID resume, deadlock
> continuation, or stale-owner cleanup command.

Key terms: **blackboard**—the public persisted debate history; **terminal
result**—a completed consensus, deadlock, or failure record; **deadlock**—
unresolved conflict at the round limit; **interrupted**—best-effort cancellation state.

Inspect the explicit or default session's overrides and debate summaries:

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

With a session attached, no conversation focus is required:

| Command | Result |
| --- | --- |
| `/inspect` | Persisted debate summaries for the attached session. |
| `/inspect ID` | The exact current-session debate; a missing ID reports `not_found`. |
| `/inspect last` | This process's latest accepted started debate for that session; none is normal. |

`last` is process-local and per session. Accepted `STARTED` updates it, so it
covers any later consensus, deadlock, failure, or interruption. A trusted
`INTERRUPTED`-first cancellation has no `last` entry. No `last` pointer is
persisted, restored, or guessed; switching preserves each process-local value.
Bounded operational debug records are a separate persistence channel.

Shell inspection whitelists identity, lifecycle, input, revision, rounds,
artifact path, and context conversation/mode/counts. It never emits blackboard
messages, candidates, stored errors/actions, context event content, raw state,
or provider-private fields. Malformed collaborator data fails closed. These
reads are provider-free and preserve attachment, defaults, both focuses, transcript,
catalog, artifacts, and provider state. Shell `/inspect`, `story inspect`, and
the `StoryRuntime.inspect_*()` wrappers may append a bounded operational debug
record without changing their established JSON or presentation. Direct
`Workspace.inspect_session()`, `Workspace.inspect_debate()`, and
`Workspace.inspect_debate_context()` calls remain artifact-only reads and do not
submit a diagnostic record.

Use global `/status` for the current shell snapshot and focused `/history`
for one visible transcript snapshot plus canonical context accounting. Their
exact gates are in the [CLI reference](../reference/cli.md#interactive-streams).

## Recover from a deadlock

This copyable scenario deliberately allows no replacement after the initial
audit:

```bash
story run \
  --debate-id chapter_03_conflict \
  --output part-03/chapter-03-conflict.md \
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
  --output part-03/revisions/chapter-03-ruling.md \
  'User ruling: preserve the established rule. Rewrite the scene accordingly.'
```

The original debate ID is consumed. Although its unused output path may still
be available, a new path makes provenance clearer.

The old deadlock remains inspectable, but this release has no resume-in-place
or debate-extension command. Issue #140 owns that future behavior. Context
checkpoints reduce repeated provider input; they do not turn a terminal debate
into a resumable one.

## Handle other terminal states

A `failed` result describes a handled backend, reply-protocol, finalization, or
artifact problem. It may retain the latest candidate, but never resume it under
the same ID. Fix the cause and choose a new ID. Runtime cancellation is re-raised
after a best-effort `interrupted` transition and may leave no result JSON.

In the shell, Ctrl-C before consensus entry creates no debate. After entry, only
a trusted terminal `INTERRUPTED` proves recoverable cancellation; inspect that
terminal attempt and use a new ID. Missing proof is fatal
`operation_interrupted`, abandons known attachment state, and runs cleanup. See
the [cancellation recovery contract](../operations/recovery-and-limitations.md#controller-cancellation-proof).

`DebateExistsError` happens during preflight before the backend runs.
`ManuscriptExistsError` can occur after a new debate reaches finalization; that
attempt returns failed and consumes its ID. Preserve both records and retry
with a new ID and output.

After a process, operating-system, hardware, or power failure, inspect the
workspace before replacement work. Artifact publication rolls a valid journal
forward and exposes its catalog last, but core files, manuscripts, logs, debate
state, and results do not share one transaction. A divergent recovery target
blocks later artifact access instead of guessing.

Inspection omits the internal debate `owner_token`. It validates persisted JSON
and JSONL rather than repairing malformed data. Exact response shapes are in
[Runtime API](../reference/runtime-api.md); limits are in
[Recovery and limitations](../operations/recovery-and-limitations.md).
