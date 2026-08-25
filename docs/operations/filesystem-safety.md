# Filesystem safety

> **Availability:** generic workspace containment, descriptor-relative
> writing-artifact publication and recovery, exclusive conversation creation,
> serialized transcript replacement, atomic per-file default/metadata
> replacement, guarded interactive settings persistence, and content-free
> explicit-reference authorization evidence and inline runtime/provider
> enforcement are implemented. Interactive `$`/`$$` discovery and viewer
> attachment use the catalog-independent reference-source boundary
> and add no artifact
> write path. Bounded fail-open debug logging uses a separate identity-bound
> storage path. Optional direct Python SQLite conversation storage has its own
> fixed database, transaction, sidecar, and coordinator boundary.

Key terms: **containment**—keeping paths beneath the workspace root; **symlink**—
a filesystem alias the runtime rejects; **exclusive creation**—failure instead
of overwrite; **atomic file**—a replacement observed as old or complete new data.

## Workspace boundary

`Workspace` resolves its root once and makes a nonmutating identity probe. An
existing canonical directory with meaningful positive device and inode values
binds that identity at construction. A specifically missing root remains
pending for one valid `initialize()` attempt. The method validates
`overwrite_prompts` before I/O; a non-boolean value raises `TypeError` without
consuming that attempt.

At the start of the first valid attempt, a pending instance becomes terminal
before capability checks or filesystem mutation. Safe initialization preflights
the required descriptor-relative directory and atomic-file operations,
no-follow flags and inspection, identity checks, synchronization, and cleanup.
If any required primitive is unavailable, `initialize()` raises `OSError` before
mutation rather than using a weaker fallback.

The attempt walks the canonical root from an opened filesystem anchor, creates
each missing component relative to its opened parent, and opens each component
without following its name. It verifies the opened final root against its final
named identity, then retains that root descriptor until initialization ends.
All topology creation and default-file parent traversal is relative to that
descriptor. Each new file is written and synchronized through an exclusively
created temporary regular file. Before installation, its meaningful identity is
captured; after installation, the target must have that same identity. The
create-only path leaves an existing non-symlink target unchanged and never
replaces a competing target. `overwrite_prompts=True` instead atomically
replaces only the four canonical role prompts. Both paths preserve the
established file bytes and create-versus-overwrite behavior.

After all topology and default writes, initialization reopens the filesystem
anchor, rewalks the complete canonical root without following component names,
and requires that descriptor to match the retained root identity. It then closes
the retained root descriptor. Only after that cleanup succeeds does the instance
record a successful identity. A canonical ancestor or root replacement after
the initial comparison cannot redirect writes: they remain on the retained
root, the final rewalk rejects the changed name, and later debug-log storage has
no successful identity to follow.

The originating `OSError`, `KeyboardInterrupt`, or `SystemExit` from root
creation, identity verification, child/default work, or file installation
propagates unchanged; cleanup does not replace an active body failure. If the
body succeeds and retained-root cleanup fails, that cleanup failure propagates
unchanged. Safely created components and intended partial data on the retained
root are not rolled back. Until complete success, every later `initialize()`
call on that instance raises the established identity-unavailable `OSError`
before root creation, stat or bind, child creation, or default writes. It neither
retries nor repeats the original failure. Calls after successful initialization
retain the established preserve-existing behavior and optional canonical-prompt
overwrite.

Any other unavailable or nonmeaningful construction-time identity remains
unavailable for that `Workspace` instance rather than silently rebinding later.
An existing directory in that state retains the established limitation: normal
pathname-based initialization may create its scaffold, but diagnostic storage
remains unavailable for that instance. A new `Workspace` performs its own
construction-time probe, so inspect and repair a failed root path before
constructing one.

This identity protects internal diagnostic containment only. Public and domain
operations retain pathname and no-symlink checks but do not compare the retained
root identity, so they may observe a real directory installed later at the same
root path. A `Workspace` is not an identity-pinned filesystem snapshot or a
transaction across operations. Every public path operation rejects:

- absolute paths;
- any `..` path component;
- a resolved target outside the workspace root; and
- every symbolic-link component, even when it points elsewhere inside the
  workspace.

The symlink rule protects logical as well as physical boundaries. A link cannot
redirect a manuscript, core document, default, catalog record, or transcript.
Workspace and template identifiers have additional validation.

CLI discovery chooses the nearest initialized ancestor and never initializes a
directory. Use an explicit path for automation or nested workspaces.

The writing-artifact storage adapter has a distinct construction contract. It
captures a supplied relative root immediately as a lexical absolute path using
the construction-time current directory. It does not use `Path.resolve()`,
dereference symlink spelling, inspect the filesystem, or probe secure primitives
at construction, and a later current-directory change cannot retarget it. The
first artifact operation performs the secure no-follow root open and identity
validation. An unsafe captured root therefore raises at that operation instead
of being silently resolved or causing constructor I/O.

Orchestration core-document access is limited to four exact filenames.
Manuscript outputs require `PART/PATH.md` and publish beneath
`manuscript/PART/`. Generic `Workspace.write_text()` and `write_json()` reject
those four core paths, every canonical manuscript path, and case-folded or
Unicode-normalized aliases of the reserved `core_documents/` and `manuscript/`
names before creating a parent or target. Canonical application writes must go
through the writing-artifact repository. Other safely contained workspace
paths retain the generic helpers.

Writing artifacts use a stronger domain adapter. It opens the workspace root
and every directory by descriptor without following links, retains and
revalidates the complete ancestor chain, requires each read target to be a
unique regular file with one link, opens reads nonblocking, and rechecks the
name and file after reading. Each descriptor-relative component must have its
exact stored spelling; case-folded and NFKC-normalized aliases are rejected
before the final component is inspected, opened, or staged instead of being
treated as the requested canonical identity. Symlinks,
hard-linked artifacts, nonregular files, root or ancestor replacement, and
detected concurrent change fail closed. Required
descriptor-relative, no-follow, listing, link, advisory-lock, and native atomic
no-replace rename primitives are checked on the first artifact I/O operation.
The rename is `renameatx_np(RENAME_EXCL)` on Darwin and
`renameat2(RENAME_NOREPLACE)` on Linux. Repository, runtime, and workspace
construction remain available on an unsupported host; the artifact operation
itself raises `WorkspaceSecurityError`.

Every child directory descriptor is registered for cleanup immediately after
it opens. A later `fstat`, type, identity, exact-name, or retained-chain failure
therefore closes that descriptor exactly once; repeated failed traversal does
not leak directory handles.

Artifact mutation additionally requires the workspace-local repository lease.
The permanent `.artifacts.lock`, `.artifacts.lock.anchor`, and
`.artifacts.lock.ready` names must be exact hard links to one regular inode with
link count three. Alias checks run before final inspection, creation, or
locking.

An exclusive bootstrap `flock` on the already-open writing-artifact state
directory serializes first initialization. While holding it, the repository
flushes the retained state-directory ancestry to the workspace root. If the
triple is wholly absent, it creates the primary with
`O_CREAT|O_EXCL|O_NOFOLLOW`, flushes the file and parent, links the anchor,
flushes the parent, verifies the intermediate two-link state with `ready`
absent, links `ready`, and flushes the parent again. It verifies the triple,
acquires the primary `flock`, reverifies the triple, then releases bootstrap and
enters protected work. Later use opens the primary without create flags and
requires the already complete triple.

Partial one- or two-link state, an old two-link development lock, a divergent
inode, an extra hard link, or any creation/link/flush failure never enters
protected work and is preserved without cleanup, reconstruction, or migration.
The triple, link count, open descriptor, directory ancestry, and workspace root
are reverified around mutations. These hard links belong only to lock identity;
canonical artifacts remain one-link regular files.

This serializes supported repository writers. Generic `Workspace` writes to
canonical paths are rejected. External/manual edits are supported only while
all workspace processes are stopped. The adapter detects many identity and
namespace changes, but does not claim safety against an actively hostile
same-user process mutating live names during the native filesystem call.

Conversation adapters and default helpers bind safe IDs to fixed paths. They
check every component and require embedded IDs to match the owning path. This
protects namespace integrity, not access control: a process with workspace
access can still name another session.

The provider-session claim guard is opened without following symlinks when the
platform supports it, verified as the same regular file, and locked with POSIX
`flock` or Windows one-byte advisory locking. Suspicious state fails closed.
Do not replace the guard pathname while a workspace may be used.

## Explicit-reference read safety

For one nonempty parsed reference batch, `WritingArtifactRepository` creates
the operation manifest and schema-v2 `requested` evidence under its repository
lease before source-content reads. It then uses the same descriptor-relative,
no-follow adapter as source discovery and prompt-context access without loading
the publication catalog or running recovery. Each unique
path is securely read once at initial capture; multiple ranges retain request
order while sharing that immutable source snapshot.

The complete source must be strict UTF-8 without NUL. Universal CRLF, LF, and
lone-CR records determine exact one-based range slices, including their line
terminators. Every source open is capped at 8 MiB; a larger explicit bound is
clamped to that cap and a stricter positive bound is honored. Each selected
reference may contain at most 128 KiB and the batch at most 256 KiB,
inclusive. Validation errors use fixed codes and do not echo paths or content.

A repository-minted capability is bound to one authorization, correlation, and
exact ordered batch and can be consumed only once. Before returning captured
bytes, accepting an invocation, recording an operation checkpoint, or finally
accepting the operation, the repository reopens each unique source and checks
its canonical path/kind, device, inode, size, modification/change timestamps,
and SHA-256 against capture. Captured content is never refreshed. A path/range
mismatch consumes the capability; deletion, replacement, unsafe topology,
oversize, or byte drift fails closed.

Interactive listing inspects metadata for the four exact core paths and every
safely representable lowercase-`.md` descendant anywhere under `manuscript/`.
That includes direct root files, arbitrary nesting, spaces, Unicode, `{`, `$`,
and `#`; publication output remains subject to the separate stricter
`manuscript/PART/PATH.md` grammar. Listing includes safe empty, manual,
pre-provenance, older, published, and hash-divergent files. It excludes
`.gitkeep`, extra core paths, unsupported/invalid or oversize paths, `}` and
unrepresentable line/control characters, normalized/case aliases, symlinks,
hardlinks, nonregular files, transcripts, discussion logs, provider/agent
state, configuration, diagnostics, outside paths, and repository metadata. The
result is sorted and content-free:
listing does not read, decode, hash, or line-split file bytes. A picker/viewer activation performs a
fresh bounded repository read before displaying text. The viewer receives text,
not a path or filesystem handle, and retains no repository authority. A stale
choice or listing-to-view race returns a fixed `workspace_invalid` failure; a
later submission independently captures and revalidates the then-current
artifact rather than reusing viewer bytes. A legitimately published new
current version may therefore be what agents read. No UI operation creates
provenance, adopts a manual source, repairs publication metadata, or changes
canonical bytes.

Operation and invocation manifests/events are schema-v2 canonical JSON,
published create-only with exact path identities and contiguous sequences.
Delivery evidence is written before the transient content is returned. A
partial, replaced, aliased, malformed, noncanonical, duplicated, gapped, or
invalidly transitioned evidence tree is corrupt and is neither repaired nor
rewritten. Schema-v1 evidence cannot authorize, resume, or be migrated into a
new operation. Never edit `writing_artifacts/input_snapshots/`; it is repository
state, not a canonical artifact directory.

<a id="debug-log-filesystem-safety"></a>

## Debug-log filesystem safety

The internal debug writer opens the identity-bound workspace root without
following its final name, verifies that its opened and named identities match
the identity retained by `Workspace`, and holds that root descriptor. It creates
a missing `debug_logs/` relative to the root descriptor, rechecks the root, then
opens the child directory without following its name. It verifies and holds both
root and child descriptors for the complete critical section. Every leaf stat,
open, create, unlink, and rename for `.runtime.lock`, the active log, and its
backups is relative to the child descriptor; leaf stats do not follow symlinks.
The writer rechecks both canonical identities before every forward leaf
mutation. Root, child, and final leaf opens request `O_NONBLOCK` where the
platform defines it, and final leaf opens are verified as regular, singly linked
files before use.

Before opening or lazily creating `.runtime.lock`, the writer validates the
active-log name and all five backup candidates through the held directory,
requiring every existing file to be regular, singly linked, and no larger than
1 MiB. It then applies the same no-follow regular and single-link checks to any
existing guard. If any prevalidation fails, no guard file is opened or created.
The 1 MiB limit applies only to active and backup files, not the guard. The
writer verifies the opened guard descriptor against its held-directory name
before attempting the advisory lock and again after acquisition. Under that
lock it revalidates active and backup names, then verifies the opened active
descriptor against its held-directory name before and during append.
Hard-linked or nonregular files, oversized active or backup files, and otherwise
unsafe storage are preserved rather than repaired, and that diagnostic record
is dropped without changing caller control flow.

Safe storage requires `O_DIRECTORY`, `O_NOFOLLOW`, descriptor-relative
mkdir/open/stat/unlink/rename operations, and no-follow stat support. An
unavailable root identity, a replaced root, or a platform without any required
primitive drops the record instead of using a weaker or less-contained fallback
destination. The diagnostic may therefore be absent, but the caller's result,
exception, output, and exit remain unchanged.

Explicit CLI smoke initializes one identity-bearing `Workspace` and passes that
same object as the smoke run's retained workspace and uses it for CLI diagnostic
attempts. Every fresh workspace-bound service inside smoke also borrows that
object. The CLI never resolves the pathname again for handoff or post-run
diagnostics. Replacing or making the bound root unavailable can therefore drop
later records, but cannot redirect them into the replacement or change the
compact report, established stderr lines, propagated process control, or exit
status. This does not identity-pin ordinary smoke artifacts; their ordinary
`Workspace` I/O is pathname-based and may observe the replacement. Implicit
smoke instead owns and removes its temporary workspace.

A guarded weak process-wide lock serializes writers for one resolved workspace.
After the safe-primitive and directory checks pass, an independent persistent
`.runtime.lock` attempts the available POSIX or Windows cross-process advisory
lock. A platform without either advisory mechanism drops the record. The process
and advisory waits share one 100-millisecond deadline. This guard is
intentionally separate from the provider-session access-claim guard: session
access uncertainty fails closed, while diagnostic contention or failure must
not affect caller control flow.

Under the guard, rotation happens before an append would exceed 1 MiB. The
writer snapshots each active/backup name as absent or as its validated
meaningful inode. Before each unlink or rename, it revalidates the involved
source and destination names against those expected absences or inodes, then
updates the expected state after a successful mutation. A mismatch stops later
forward rotation and drops the record; shifts already completed may remain. It
keeps five backups, moves the newest prior active file to `.1`, and unlinks only
the oldest backup when the set is full. Append writes are flushed and
synchronized. After a partial write or synchronization failure, rollback
truncates only when the open descriptor still identifies the exact inode
captured before append and that inode remains regular and singly linked.
Otherwise rollback is skipped.

Rotation and rollback do not form a transaction. Ordinary `Exception` failures
from locking, validation, append, synchronization, rotation, rollback, or
cleanup drop the record. Cleanup still attempts every independently acquired
active, guard, child-directory, and root-directory descriptor plus the advisory
and process locks. An original body `KeyboardInterrupt` or `SystemExit` wins;
otherwise the first cleanup process-control failure wins after later cleanup
attempts finish. That low-level failure preserves its identity, while each of
the five diagnostic consumers catches it privately so application results,
exceptions, cancellation, output, and exits remain unchanged.

Do not rename or replace the workspace root or `debug_logs/`, and do not edit,
replace, or create another hard link to `.runtime.lock` or the active/backup
files while the workspace may be used. Holding the root and child descriptors
prevents a replacement pathname from redirecting leaf operations. Revalidation
cannot close the interval after the last check and before the following system
call, however, so a concurrent external leaf change can still race a mutation.
These checks do not make filesystem changes transactional. Paths and retention
are listed in
[Session storage](../reference/configuration/session-storage.md#runtime-debug-log-storage).

## Conversation transcript safety

Transcript reads and appends for the same workspace, session, and conversation
share a weakly retained process-wide lock. Different conversations are not
serialized together. An append validates the complete old transcript, then
atomically replaces the complete file; readers observe the old or complete new
version, not a partial appended line.

The event-store adapter does not acquire or verify story-session access. A
caller that permits multiple processes must acquire the session's V2 access
claim before same-session writes. The persisted claim and claim guard provide
cross-process exclusion; the in-process transcript lock alone is insufficient.

After catalog identity succeeds, a missing transcript is valid empty history.
An existing empty file, malformed JSONL, invalid sequence or pairing, or
path-mismatched record raises `WorkspaceFormatError`. Symlinks raise
`WorkspaceSecurityError`. Exact event rules are in
[Conversation transcripts](../reference/conversation-transcripts.md).

<a id="sqlite-conversation-storage-safety"></a>

## SQLite conversation-storage safety

The optional adapter resolves only the fixed contained path
`discussion_logs/conversation_storage.sqlite3`. It requires the named database
and opened descriptor to identify the same regular, singly linked file and
rechecks that identity after each operation. Connections use non-creating SQLite
URI modes: `mode=ro` for reads and `mode=rw` for writes. Module import performs
no I/O. Adapter construction never opens or creates the database and does not
load `sqlite3`; resolving a string or path-like workspace still follows the
ordinary `Workspace` construction contract. `sqlite3` is loaded and probed only
when an operation is requested, and SQLite 3.7.16 or later is required.

Header verification requires the SQLite format header, DELETE rollback-journal
read/write versions, UTF-8 encoding, application ID `0x53574143` (`SWAC`), and
schema version `1`. Every connection against an existing database also verifies
the exact schema manifest and fingerprint and fixed schema cookie. Strict
preflight and composition readiness, along with both existing-target and new
temporary-database validation during provisioning, additionally run
`integrity_check`, a global foreign-key check, an orphan scan, and full
catalog/event reconstruction. Routine reads and writes validate the rows
involved in their requested operation; database constraints, triggers, and
enabled foreign-key enforcement protect supported mutations. They do not scan
unrelated database contents. Validation never repairs a value.

Sidecar handling deliberately differs by operation:

- strict preflight, composition readiness, provisioning, and the boundaries of
  supported writes require `-journal`, `-wal`, and `-shm` to be absent;
- an ordinary adapter read may tolerate only a regular, singly linked
  `-journal`, the shape needed while a concurrent supported DELETE-mode writer
  is live; and
- WAL and shared-memory sidecars are never accepted. No operation opens,
  rewrites, removes, or treats an authoritative-path sidecar as recovery input;
  provisioning cleanup is limited to sidecars of its own private temporary.

An ordinary read opens one read-only connection, verifies connection policy,
executes `BEGIN`, verifies bounded database/schema identity, validates the rows
requested by one adapter operation, commits, closes, then rechecks the sidecar
and database identity. This gives one stable snapshot for that operation. It
does not run the exhaustive preflight scan or combine the several
catalog/event-store reads made by `ReferenceConversationSearch` into a single
database-global snapshot.

Every supported mutation acquires an active workspace conversation-storage
lease before database work. The coordinator uses a weakly shared in-process
lock and the fixed `config/.conversation_storage.lock` POSIX advisory guard;
same-thread nesting reuses the same owner-thread/workspace-bound lease without
extending its expiry. After acquisition the adapter opens `mode=rw`, verifies
the database and lease, starts `BEGIN IMMEDIATE`, validates and mutates the
operation's affected rows under schema constraints and foreign-key
enforcement, reverifies schema identity and the lease, commits, and checks
named identity again. It does not globally scan unrelated rows. The connection
uses `journal_mode=DELETE`, `synchronous=FULL`, foreign keys on, UTF-8, and a
five-second SQLite busy timeout. The separately configurable mutation timeout
bounds coordinator acquisition.

An uncommitted failure attempts rollback and every opened connection is closed.
Rollback or close cleanup cannot suppress the primary body failure. A failure
reported during post-commit close or synchronization can nevertheless leave
one complete committed write or provisioned database; inspect before retrying.
Busy or locked resources map to the public unavailable failure and are not
retried automatically.

Provisioning requires an explicit active lease. It creates the schema in one
adapter-owned temporary file beside the target, commits and verifies it, writes
and synchronizes its application ID, then installs it create-only with a hard
link and synchronizes the installed file and parent directory. A competing
complete installation is verified rather than overwritten. Cleanup removes
only the provisioning call's own temporary path and its temporary sidecars;
the authoritative target and foreign sidecars are preserved.

The guard serializes supported writers but does not authenticate a caller,
acquire a story-session claim, or protect against an actively hostile same-user
process changing live filesystem names between checks and native calls. Stop
all workspace processes before external edits. See
[Recovery](recovery-and-limitations.md#sqlite-conversation-storage-recovery)
for fail-closed responses.

## Mutation guarantees

The runtime never silently overwrites a manuscript. Any existing target raises
`ManuscriptExistsError`; unsafe path state raises `WorkspaceSecurityError`.
Complete output grammar, core allowlist, strict-UTF-8 content, identity,
authorship, old hashes, and new hashes are validated or recorded before target
publication.

Writing-artifact publication is journaled, serialized by process and workspace
artifact locks, and catalog-last. Target replacement compares the current bytes
with the journal's expected prior hash and stages a unique regular temporary
file with `O_EXCL` outside the authoritative transaction directory. After
flushing it, the repository exclusively moves verified prior bytes to a unique
`.held` staging name when replacing, then uses native no-replace rename to move
the `.tmp` bytes into the absent target. Published bytes, retained path chain,
root, and lease are then reverified. Directory creation flushes each newly
created child's parent directory, and publication/retirement flushes the
directories whose entries changed.

Publication does not unlink or use a replacing rename. A detected failure after
target publication exclusively retires the new bytes to `.retired` and restores
the exact `.held` prior bytes only when identity and the original path remain
safe. Failed `.tmp` bytes and successful `.held` prior bytes remain retained;
name collisions are preserved while a different exclusive staging name is
tried.

The schema-v2 journal is one immutable prepared image: it contains the exact
catalog-before state, catalog-after image, commit, and ordered targets, and is
never replaced with a later phase. After all targets, the repository publishes
immutable commit history and the current catalog last. Completed journal
retirement compares its exact hash before an exclusive no-replace move to
`.completed` staging.

Outside recovery, every artifact read or commit audits the complete permanent
commit ledger against the catalog before trusting an entry or publishing new
bytes. The catalog path set must equal the union of every artifact path in every
non-exempt commit, every entry must exactly match its referenced commit, and
every immutable commit file must have the repository's exact canonical JSON
bytes. Semantic reformatting is not repaired. Every ledger commit permanently
reserves its one manuscript identity. For a repeated core path, the current
entry must reference the unique newest offset-aware commit instant; an equal
newest tie or older projection fails closed. Missing, subset, malformed, extra,
duplicate-reservation, noncanonical, or otherwise mismatched ledger/catalog
state also fails closed; catalog loss does not free paths and is never rewritten,
reconstructed, or migrated. See
[Canonical writing artifacts](../reference/writing-artifacts.md#provenance-and-work-history).

At exact catalog-before recovery, an on-disk pending commit is exempt only when
its canonical bytes exactly match the journal. It contributes no expected paths
or newest-core candidate; paths and core authority from all older established
commits remain required. Projected and stored catalog-after include that commit
in the full path, exact-entry, and unique-newest audit.

On the next catalog or publication access, an exact catalog-before position rolls forward;
an exact catalog-after position is verified without republishing. A catalog
matching neither image, target divergence, changed or missing journal bytes,
missing/divergent published commit, or invalid authoritative transaction state
writes a persistent marker and blocks all later catalog/publication access.
Safe filesystem-source discovery and reads remain independent. Every `.tmp`,
`.held`, `.retired`, and `.completed` file is retained under non-authoritative
staging, ignored by transaction recovery and catalog listing, and may accumulate.
Only JSON entries in `transactions/` are authoritative; any unexpected entry
there blocks recovery. This avoids partial eligibility but is not one
transaction with logs, debate state, results, or provider state. See
[Canonical writing artifacts](../reference/writing-artifacts.md).

Conversation identities still use exclusive creation, and their metadata and
transcripts use the separate atomic replacement guarantees below.

Conversation rename and status changes atomically replace one complete metadata
file. Failure before replacement preserves the old record. A failure reported
after successful replacement can leave the complete new record durable, but
not partial JSON. Catalog mutations cannot alter provider sessions, debates,
manuscripts, core documents, CLI defaults, or agent state.

Session and conversation default saves each atomically replace one complete
fixed JSON file. Failure before replacement preserves the prior bytes. A
failure reported after replacement can leave the complete new default durable,
but not partial JSON. These saves do not alter catalog metadata or in-memory
focus. Operations spanning files are not atomic, so inspect the persisted
default after any reported save failure. See
[Persistence](../design/persistence.md) for the cross-file consequence.

`/settings` coordinates `config/system_defaults.json` and
`config/agent_configs.json` more narrowly. Opening captures both files' exact
strict-UTF-8 bytes, their fully validated values, and the safe persona catalog.
Save validates the complete pending pair before rereading anything, then
requires both byte snapshots and the catalog to match the opening baseline.
Any mismatch or read failure reports a conflict and performs no write.

An unchanged candidate performs no write. Otherwise, only changed documents
are serialized as canonical sorted, indented JSON and atomically replaced,
system defaults before agent configs. If a write reports failure, every
attempted target—including the failing target, because replacement may already
have occurred—is restored from its exact opening bytes in reverse order. All
restorations are attempted even after one fails. Complete restoration reports a
write failure; any restoration failure reports rollback uncertainty and
requires inspection of both files.

This is guarded optimistic coordination, not one cross-file transaction. Each
replacement is atomic, but an abrupt process, operating-system, hardware,
filesystem, or power failure can still leave the pair partially durable.

Invalid identifiers raise `SessionIdError`; unsafe paths or symlinks raise
`WorkspaceSecurityError`; malformed or path-mismatched persisted data raises
`WorkspaceFormatError`. Reads fail closed rather than repairing a record.

Exact validation patterns are in [Identifiers and errors](../reference/configuration/identifiers-and-errors.md).
