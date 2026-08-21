# Persistence design

> **Availability:** filesystem persistence covers configuration, sessions,
> conversations, debates, optional debate-context sidecars, bounded runtime
> debug-log infrastructure, and manuscripts.

Key terms: **blackboard**—public debate JSONL; **sidecar**—context stored apart
from established debate records; **create-only**—never replaces an existing
path; **context-omitted run**—a debate created without a context request.

The workspace uses separate inspectable files rather than a database:

| Channel | Owner |
| --- | --- |
| Debate log, lifecycle, and result | Runtime and normal inspection |
| Debate context sidecar | Runtime; separate context inspection |
| Per-agent history | Its owning agent |
| Provider manifest and access claim | `AgentBackend` |
| Session/conversation defaults | CLI context helpers |
| Conversation metadata and transcript | Catalog and event-store adapters |
| Bounded runtime diagnostics | Internal `debug_log` writer |
| Retrieval actions, observations, progress, sources | Process/terminal only |
| Current conversation and agent focus | Controller memory only |
| Approved manuscript | Workspace artifact commit |

<a id="workspace-runtime-debug-log"></a>

## Workspace runtime debug log

The internal diagnostics writer appends compact, ASCII-safe schema-v1 JSONL.
Every record has `schema_version`, a writer-owned UTC `timestamp`, and fixed
`level`, `component`, `operation`, and `phase` values. It may add an `outcome`
from its fixed taxonomy and exact validated `session_id`, `conversation_id`,
`debate_id`, or canonical `agent_id`; absent optional fields are omitted rather
than written as `null`.

An error record additionally has an `exception` object with only `type`,
`frames`, and `frames_truncated`. A frame has only a safe module name, function
name, and positive line number. The writer keeps the nearest 32 frames and uses
fixed replacements for unsafe type, module, or function names. It does not
accept a caller timestamp, message, path, traceback text, or arbitrary detail
or payload mapping.

`Workspace` owns canonical-root resolution, containment, no-symlink checks, its
per-instance meaningful root identity, package-internal root open/reverification
helpers, and the `debug_logs/` scaffold declaration. An existing directory binds
at construction; a specifically missing root receives one binding attempt after
initialization creates it; any other unavailable identity never silently
rebinds on that instance. If the post-creation bind cannot produce a meaningful
directory identity—including when a symlink appears at the missing root—
`initialize()` raises `OSError` before creating workspace child directories or
default files. That failed bind is terminal for the instance: every later
`initialize()` call raises the same identity-unavailable `OSError` before child
or default writes and never retries or rebinds. Parent or root directories
already created by `mkdir` are not rolled back. A new `Workspace` makes its own
construction-time identity probe.

An existing directory whose construction-time probe is unavailable or
nonmeaningful follows the established, separate limitation: normal
pathname-based initialization may still create its scaffold, but that instance
never gains a diagnostic root identity and diagnostic storage fails closed.
Those helpers are not project-root exports.

For each supported storage attempt, the internal writer owns the lifetimes of
the root, debug-directory, guard, and active-log descriptors. It creates or opens
the debug directory relative to the verified root, performs leaf operations
relative to the verified child, tracks expected rotation absences/inodes, and
owns this schema plus lazy leaf creation, single-link regular-file validation,
active/backup size validation, append, best-effort exact-inode rollback,
rotation, and cleanup. An unavailable or replaced root, or a platform without
the required safe descriptor-relative primitives, drops the record instead of
using a weaker fallback. Constructing the internal writer remains
filesystem-I/O-free.
`StoryRuntime` construction only retains that I/O-free handle and emits nothing.
`AgentBackend` begins emitting only after the complete configuration snapshot
loads successfully; CLI, controller, and smoke emission starts only after
workspace resolution or initialization.

Only package-internal diagnostic storage consults the retained root identity.
Ordinary `Workspace` domain I/O remains pathname-based and may observe a real
directory installed later at the same root path. It is not an identity-pinned
filesystem snapshot or a transaction across operations.

Explicit CLI smoke initializes and retains one identity-bearing `Workspace`,
then passes that exact instance to `run_smoke`. The workflow treats a supplied
workspace as caller-owned and borrows it. Every fresh workspace-bound catalog,
event store, runtime, and memory service inside the workflow also borrows that
instance; service freshness never rebinds workspace identity.
Implicit smoke creates, initializes, and removes its own temporary `Workspace`.
The smoke contract accepts a `Workspace` or `None`, so the CLI does not
reconstruct the explicit workspace from a path for handoff or post-run
diagnostics. CLI and smoke diagnostic-handle acquisition and every submission
have separate `BaseException` guards. After CLI initialization or discovery
succeeds, hint eligibility is recorded before optional handle acquisition; that
eligibility, not handle presence or record success, controls a later fixed
failure hint. A replaced or unavailable bound root can therefore omit a record,
but cannot redirect diagnostics or change the smoke report, stderr, exception,
or exit.

The low-level writer attempts cleanup for every independently acquired
descriptor and lock and drops ordinary `Exception` failures. An original body
`KeyboardInterrupt` or `SystemExit` takes precedence; otherwise the first
cleanup process-control failure propagates after later cleanup attempts. Each of
the five consumers adds a private `BaseException` guard around submissions, so
diagnostic failure or an absent record cannot replace established results,
exceptions, cancellation, persistence, cleanup, output, or exits.
Explicit smoke retains best-effort records; implicit smoke removes them with its
temporary workspace.

For the five synchronous provider-free `StoryRuntime` methods—directive
application, runtime-override saving, and session, debate, or context
inspection—a successful directive or configuration operation attempts
`succeeded`/`completed`, while successful inspection attempts
`observed`/`completed`. An ordinary underlying `Exception` attempts
`failed`/`failed`. `KeyboardInterrupt` or `SystemExit` instead attempts
`cancelled`/`interrupted`, then is bare-reraised unchanged; diagnostic failure
cannot replace that control.

Storage paths and retention are in [Session storage](../reference/configuration/session-storage.md#runtime-debug-log-storage),
failure guarantees in [Filesystem safety](../operations/filesystem-safety.md#debug-log-filesystem-safety),
and excluded content in [Privacy](../operations/privacy-and-provider.md#workspace-debug-log-privacy).

<a id="debate-context-sidecar"></a>

## Debate-context sidecar

The exact lazy path is
`discussion_logs/<session_id>/<debate_id>.context.json`. Its version-1 included
shape is:

```json
{
  "schema_version": 1,
  "session_id": "novel",
  "debate_id": "opening_01",
  "conversation_id": "c_opening",
  "mode": "included",
  "snapshot": {
    "events": [{
      "schema_version": 1,
      "session_id": "novel",
      "conversation_id": "c_opening",
      "turn_id": "t_01",
      "sequence": 1,
      "timestamp": "2026-01-02T00:00:00+00:00",
      "sender": "user",
      "recipient": "plot_architect",
      "kind": "user_message",
      "content": "Question"
    }, {
      "schema_version": 1,
      "session_id": "novel",
      "conversation_id": "c_opening",
      "turn_id": "t_01",
      "sequence": 2,
      "timestamp": "2026-01-02T00:00:01+00:00",
      "sender": "plot_architect",
      "recipient": "user",
      "kind": "agent_reply",
      "content": "Answer"
    }],
    "total_complete_turns": 1,
    "retained_turns": 1,
    "omitted_turns": 0,
    "retained_utf8_bytes": 14,
    "truncated": false
  }
}
```

The paired events are one complete turn. Top-level objects have exactly the six
shown keys, event objects exactly the ten shown keys, and snapshot objects
exactly the six shown keys. Snapshot metadata must match ordered,
identity-bound events and fixed context caps.

Included mode always stores an exact snapshot, including the empty form
`events: []` with zero counts and `truncated: false`. Fresh mode keeps the same
six top-level fields, sets `mode` to `fresh`, and sets `snapshot` to `null`.
A context-omitted run has no sidecar; absence is read as `None`.

Creation occurs after debate reservation and before initiation or provider
work. The file is strict UTF-8 JSON, identity-bound to its path, rejects
missing, extra, or duplicate keys, and is never overwritten. A collision raises
`DebateExistsError`. `Workspace.inspect_debate_context()` and the matching
runtime methods are read-only for debate/session state; runtime wrappers may
append a bounded operational debug record. Existing inspection JSON, debate
logs, result JSON, and `DebateResult` remain unchanged.

## Durability boundaries

The blackboard retains input, candidates, audits, proposals, visible errors,
finalization metadata, and commit record. Result JSON is a projection and omits
its duplicated message list. Agent history remains owner-isolated and bounded,
though provider output in visible fields may still be sensitive.

Terminal result creation and lifecycle transition finish before terminal
progress. Approved artifact commit validates the whole set, replaces core files
in fixed order, then exclusively creates the manuscript; cross-file rollback is
best effort. No sidecar, log, default, transcript, result, state, and artifact
set forms one database transaction.

One-step and retrieval turns append exactly one user event before provider work.
Success appends one reply; ordinary failures attempt one sanitized runtime error,
and `asyncio.CancelledError` attempts its fixed runtime error. Provider
`KeyboardInterrupt` or `SystemExit` appends no transcript outcome: the runtime
instead attempts a separate `cancelled`/`interrupted` operational record and
re-raises the identical exception without automatic retry. The unmatched user
event remains visible to full transcript reads and is excluded from later context.
Retrieval adds no control, source, sidecar, catalog, default, debate, state, or
artifact record; compatible provider-thread metadata may update. Consensus
snapshots never append the transcript.

The canonical path tree is in [Session storage](../reference/configuration/session-storage.md).
Accessor signatures are in [Workspace API](../reference/workspace-api.md),
containment guarantees in [Filesystem safety](../operations/filesystem-safety.md),
and consequences in [Recovery](../operations/recovery-and-limitations.md).
