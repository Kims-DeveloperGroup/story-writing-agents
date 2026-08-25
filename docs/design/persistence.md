# Persistence design

> **Availability:** filesystem persistence covers configuration, sessions,
> conversations, debates, optional debate-context sidecars, canonical writing
> artifacts, content-free explicit-reference authorization evidence, and
> bounded runtime debug-log infrastructure. A separate optional SQLite
> conversation store is available only through a direct Python module.
> `StoryRuntime` creates and finalizes reference evidence for inline referenced
> operations; Python callers can inspect it by operation ID.

Key terms: **blackboard**—public debate JSONL; **sidecar**—context stored apart
from established debate records; **create-only**—never replaces an existing
path; **context-omitted run**—a debate created without a context request.

The installed runtime uses separate inspectable files by default. An explicitly
provisioned SQLite database can instead serve direct Python conversation
catalog, event-store, and reference-search composition; it does not replace or
select the runtime files automatically.

| Channel | Owner |
| --- | --- |
| Debate log, lifecycle, and result | Runtime and normal inspection |
| Debate context sidecar | Runtime; separate context inspection |
| Per-agent history | Its owning agent |
| Provider manifest and access claim | `AgentBackend` |
| Session/conversation defaults | CLI context helpers |
| Conversation metadata and transcript | Filesystem catalog/event-store adapters by default; optional SQLite adapters by direct composition |
| Bounded runtime diagnostics | Internal `debug_log` writer |
| Retrieval actions, observations, progress, sources | Process/terminal only |
| Current conversation and agent focus | Controller memory only |
| Canonical writing artifacts and provenance | `WritingArtifactRepository` |
| Explicit-reference operation and invocation evidence | `WritingArtifactRepository`; content-free create-only records |
| Explicit-reference selected content | Transient repository/backend carriers and one immediate provider payload; no workspace evidence field |

<a id="optional-sqlite-conversation-persistence"></a>

## Optional SQLite conversation persistence

The optional adapter owns one fixed contained file:
`discussion_logs/conversation_storage.sqlite3`. Its database identity is schema
version `1`, SQLite application ID `0x53574143` (`SWAC`), UTF-8 encoding, and a
fixed schema cookie and schema-manifest fingerprint. The internal database
owner reads the canonical `Workspace.root` once at construction and pins both
its workspace key and database path. Later mutation of the supplied
`Workspace.root` cannot retarget database I/O. That capture is path arithmetic
only: the SQLite layer performs no database I/O and does not load `sqlite3`
during direct adapter/database construction. Resolving a string or path-like
argument still follows ordinary `Workspace` construction, and public
composition still performs its documented readiness preflight.

Strict preflight and composition readiness, plus new, existing, recovered, and
race-winner provisioning paths, verify the exact table, index, and trigger
manifest, run `PRAGMA integrity_check` and a global foreign-key check, reject
orphan events, and reconstruct every domain row. Routine adapter calls instead
verify bounded database/schema identity and validate only the rows involved in
the requested operation; they do not scan unrelated database contents.

Schema v1 has nine explicit application-owned objects: three authoritative
tables, three indexes, and three insert triggers. The tables hold one singleton
storage identity, catalog rows keyed by `(session_id, conversation_id)`, and
ordered event rows keyed by `(session_id, conversation_id, sequence)`. The two
catalog indexes serve active/all creation ordering; the unique turn-slot index
serves addressed turn lookup and uniqueness. Event ordering uses SQLite's
composite-primary-key autoindex, so there is no redundant explicit event-order
index. Catalog rows retain the exact public metadata and a private derived
`normalized_subject`. Event rows retain the exact public event and a private
`turn_slot` used with constraints and triggers to enforce contiguous history,
one user/outcome slot, addressed replies, and catalog ownership. These private
columns do not change the public catalog, transcript, context, search, or
citation contracts. There is no full-text or other persisted search index;
composition uses the existing provider-free `ReferenceConversationSearch`.

Every ordinary adapter read owns a read-only `mode=ro` connection and explicit
`BEGIN`/`COMMIT` transaction. Bounded identity verification and the requested
row validation observe one stable snapshot; the connection is then closed.
That boundary is one catalog or event-store operation. A search can perform
multiple operations and is not one database-global snapshot.

Every supported mutation acquires the workspace conversation-storage
coordinator before opening its `mode=rw` connection. It verifies the database
and active workspace/owner-thread lease, uses `BEGIN IMMEDIATE`, performs the
operation with operation-local row validation and database constraints,
reverifies schema identity plus the lease, commits, checks the named database
identity, and closes. The connection policy is DELETE journal mode,
`synchronous=FULL`, foreign keys enabled and enforced, a fixed five-second
SQLite busy timeout, and strict UTF-8 decoding for database `TEXT`. Malformed
stored text encountered by verification or the requested operation fails
closed through a content-free storage-format error; a
caller-originated `UnicodeDecodeError` is not silently reclassified as stored
corruption.

This serializes supported writers through the workspace guard but is not a
transaction with filesystem conversations, defaults, sessions, debates,
artifacts, provider state, or other workspace channels. Routine writes do not
run the exhaustive preflight scan over unrelated rows. In particular, append
uses the indexed maximum sequence and reads at most the addressed turn's one
user row and one outcome row; it does not reconstruct the full transcript.

Provisioning builds and verifies one private temporary database, installs it
create-only, and synchronizes the installed file and containing directory. It
does not install SQLite or ingest the filesystem store. The privileged archive
adapter can round-trip exact SQLite catalog/event rows under explicit leases,
but its payload and record types are private and are not a public format or
migration contract. Export globally reconstructs all metadata and event rows
and rejects every orphan event. Import validates the complete destination
before mutation and again after mutation in the same transaction, so an orphan,
malformed row, or divergent existing identity cannot be silently adopted or
dropped; failure rolls back the whole import.

Without POSIX `fcntl`, a valid existing database can still be preflighted and
read observationally. Supported coordinator acquisition, provisioning, and
mutations fail closed without changing the SQLite database or conversation
domain state. Full non-POSIX coordinator and activation readiness is deferred
to Issue #261.

`story init`, `/settings`, the installed shell, and `StoryRuntime` do not
provision or select this database. There is no automatic installation,
migration, activation, FTS, RAG, or MySQL behavior in this adapter. See the
[direct API](../reference/package-api.md#optional-sqlite-conversation-storage),
[filesystem boundary](../operations/filesystem-safety.md#sqlite-conversation-storage-safety),
and [recovery table](../operations/recovery-and-limitations.md#sqlite-conversation-storage-recovery).

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
at construction; a specifically missing root remains pending for one valid
`initialize()` attempt; any other unavailable identity never silently rebinds
on that instance. Invalid `overwrite_prompts` input raises `TypeError` before
I/O without consuming the pending attempt. The first valid attempt becomes
terminal before capability checks or mutation and preflights the full required
descriptor-relative directory and atomic-file capability set. It creates
missing canonical-root components beneath opened parent descriptors, verifies
the opened final root against its named identity, and retains that root
descriptor across every topology and default write.

Pending child directories and file parents are traversed relative to the
retained root without following names. File writes stage exclusive temporary
regular files and verify both the temporary and installed identities.
Create-only installation preserves existing non-symlink entries without
replacement; prompt overwrite remains limited to the four canonical role
prompts. After all writes, the canonical root is rewalked and matched to the
retained identity. Successful identity is recorded only after the retained
descriptor closes.

The first `OSError`, `KeyboardInterrupt`, or `SystemExit` propagates unchanged,
with an active body failure taking precedence over cleanup failure. Safely
created components and intended partial data on the retained root are not
rolled back. After any failure before complete writes, rewalk, and cleanup,
later `initialize()` calls reject with the established identity-unavailable
`OSError` before root or child I/O and never retry. Calls after success and roots
that existed at construction retain their established pathname behavior. A new
`Workspace` makes its own construction-time identity probe.

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
append a bounded operational debug record. The sidecar itself does not change
inspection JSON, debate logs, result JSON, or `DebateResult`; a separate
referenced run may add its optional content-free operation ID to the result.

## Durability boundaries

Explicit-reference authorization has a separate durability boundary under
`writing_artifacts/input_snapshots/`. For each nonempty operation, the
repository publishes a schema-v2 manifest and `requested` event before reading
artifact source content. Later operation and invocation events are
create-only, canonically encoded records with contiguous sequences and
contiguous invocation ordinals. A delivery event is durable before selected
bytes leave the repository boundary. The same repository lease serializes
these evidence writes with source reads and filesystem-identity revalidation;
it does not load the publication catalog or run transaction recovery.

Durable evidence contains schema and operation/invocation identity,
path/range, content-free filesystem source identity, hashes, counts,
correlation, states, checkpoints, and
fixed outcomes—not selected bytes, source/provider text, prompts, or results.
The private invocation manifest also binds a capability identifier; the
validated history returned to callers does not expose it. Captured and
delivered byte tuples are process-local, closeable values. The capability
object is never serialized or sent to a provider; its identifier remains
repository-private and is absent from provider payloads and validated history.
A backend-created read coordinator advances the existing invocation records:
the exact full provider action precedes `delivered`, a structurally valid
substantive result precedes `result_received`, and final source-identity
revalidation precedes `accepted`. Selected text, provider payloads, and provider
results never become evidence fields. Schema-v1 evidence cannot be reused or
migrated to authorize or resume work. A restart can validate schema-v2 history
but cannot reconstruct or resume transient capabilities. An empty reference batch creates
neither evidence nor artifact I/O. These records are not part of the canonical
writing-artifact catalog, completion, or picker namespace.

The blackboard retains input, candidates, audits, proposals, visible errors,
finalization metadata, and commit record. Result JSON is a projection and omits
its duplicated message list. Agent history remains owner-isolated and bounded,
though provider output in visible fields may still be sensitive.

Terminal result creation and lifecycle transition finish before terminal
progress. Approved artifact publication is a separate recoverable transaction:
one immutable schema-v2 journal records the exact catalog-before state,
catalog-after image, commit, and ordered manuscript/core targets. It is not
rewritten as publication advances. The repository publishes and verifies target
bytes, writes immutable commit history, publishes the catalog last, then
exclusively retires the unchanged completed journal to non-authoritative
staging. The next artifact access rolls forward only from exact catalog-before
or verifies an exact catalog-after state; any other catalog position or missing,
replaced, or divergent authority writes a persistent block marker and fails all
later artifact access closed. Retained `.tmp`, `.held`, `.retired`, and
`.completed` staging files are ignored by recovery and catalog listing and may
accumulate.

Immutable commits are also the permanent manuscript-reservation ledger, not
just history for current catalog rows. Each access globally audits exact commit
records against the catalog. Each immutable commit file must equal the
repository's canonical JSON bytes, catalog paths equal the union of every
artifact path in all non-exempt commits, and each entry exactly matches its
referenced commit. Reformatting is divergence, not a migration input.

Commit times are offset-aware instants. For a repeated core path, the catalog
must select the unique newest normalized instant; an equal-newest tie or older
projection fails closed. Generated timestamps are strictly later than every
ledger instant even across wall-clock rollback, and an unrepresentable next
instant fails before mutation. During exact catalog-before recovery, only the
pending journal's already published byte-identical canonical commit is exempt:
it contributes no paths or newest-core candidate, while all older established
authority remains required. Projected and exact catalog-after audit the full
commit set without an exemption. Catalog loss, subsets, malformed/noncanonical
or extra commits, mismatches, and duplicate manuscript reservations fail closed
instead of being rewritten, reconstructed, or migrated.

The permanent repository readiness triple has its own durability boundary:
primary lock, anchor, and `ready` are exact hard-link names for one regular inode
with link count three. Initialization first takes a transient bootstrap `flock`
on the open writing-artifact state directory and flushes its retained ancestry.
It exclusively creates and flushes the primary, flushes the parent, links and
flushes the anchor, verifies that `ready` is absent, then links and flushes
`ready`. It verifies the triple, acquires the primary `flock`, reverifies, and
releases bootstrap before work. Partial or old two-link state, aliases,
divergent or extra links, and any failure prevent protected work and remain
unchanged; no cleanup, reconstruction, or migration is attempted.

That artifact transaction does not combine sidecars, blackboards, defaults,
transcripts, debate lifecycle, results, provider state, and agent state into one
database transaction. Infrastructure interruption can therefore leave a
recoverable artifact journal alongside an incomplete non-artifact operation.
Exact artifact paths, eligibility, recovery positions, and user responses are in
[Canonical writing artifacts](../reference/writing-artifacts.md).

One-step and retrieval turns append exactly one user event before provider work.
Success appends one reply; ordinary failures attempt one sanitized runtime error,
and `asyncio.CancelledError` attempts its fixed runtime error. Provider
`KeyboardInterrupt` or `SystemExit` appends no transcript outcome: the runtime
instead attempts a separate `cancelled`/`interrupted` operational record and
re-raises the identical exception without automatic retry. The unmatched user
event remains visible to full transcript reads and is excluded from later context.
Retrieval actions and observations add no control, source, sidecar, catalog,
default, debate, state, or canonical-artifact publication; compatible
provider-thread metadata may update. A referenced top-level turn separately
adds the content-free operation and invocation evidence described above.
Consensus snapshots never append the transcript.

The canonical path tree is in [Session storage](../reference/configuration/session-storage.md).
Accessor signatures are in [Workspace API](../reference/workspace-api.md),
containment guarantees in [Filesystem safety](../operations/filesystem-safety.md),
and consequences in [Recovery](../operations/recovery-and-limitations.md).
