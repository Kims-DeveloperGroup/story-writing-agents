# Filesystem safety

> **Availability:** containment and no-symlink checks, exclusive manuscript and
> conversation creation, serialized transcript replacement, atomic per-file
> default/metadata replacement, guarded interactive settings persistence,
> bounded fail-open debug logging, and best-effort rollback are implemented.

Key terms: **containment**—keeping paths beneath the workspace root; **symlink**—
a filesystem alias the runtime rejects; **exclusive creation**—failure instead
of overwrite; **atomic file**—a replacement observed as old or complete new data.

## Workspace boundary

`Workspace` resolves its root once. Every public path operation rejects:

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

Orchestration core-document access is limited to the four fixed filenames.
Manuscript names may use safe relative subdirectories beneath `manuscript/`.
A direct client of generic `Workspace.write_text()` is trusted and can modify
any safely contained relative file; containment is not authorization.

Conversation adapters and default helpers bind safe IDs to fixed paths. They
check every component and require embedded IDs to match the owning path. This
protects namespace integrity, not access control: a process with workspace
access can still name another session.

The provider-session claim guard is opened without following symlinks when the
platform supports it, verified as the same regular file, and locked with POSIX
`flock` or Windows one-byte advisory locking. Suspicious state fails closed.
Do not replace the guard pathname while a workspace may be used.

<a id="debug-log-filesystem-safety"></a>

## Debug-log filesystem safety

The internal debug writer begins with `Workspace` containment and no-symlink
resolution for `debug_logs/`. It then opens that canonical directory without
following its final name, verifies the opened and named directory identities,
and holds the directory descriptor for the complete critical section. Every
leaf stat, open, create, unlink, and rename for `.runtime.lock`, the active log,
and its backups is relative to that descriptor; leaf stats do not follow
symlinks. The writer rechecks the canonical directory identity before every
forward leaf mutation. Directory and final leaf opens request `O_NONBLOCK` where
the platform defines it, and final leaf opens are verified as regular, singly
linked files before use.

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
open/stat/unlink/rename operations, and no-follow stat support. If the platform
lacks any required primitive, the writer drops the record without opening a
diagnostic leaf or changing the caller's result, exception, output, or exit.
There is no less-contained fallback destination.

A guarded weak process-wide lock serializes writers for one resolved workspace.
After the safe-primitive and directory checks pass, an independent persistent
`.runtime.lock` attempts the available POSIX or Windows cross-process advisory
lock. A platform without either advisory mechanism drops the record. The process
and advisory waits share one 100-millisecond deadline. This guard is
intentionally separate from the provider-session access-claim guard: session
access uncertainty fails closed, while diagnostic contention or failure must
not affect caller control flow.

Under the guard, rotation happens before an append would exceed 1 MiB. It keeps
five backups, shifts the newest prior active file to `.1`, and discards only the
oldest backup when the set is full. Append writes are flushed and synchronized.
After a partial write or synchronization failure, rollback truncates only when
the open descriptor still identifies the exact inode captured before append and
that inode remains regular and singly linked. Otherwise rollback is skipped.
Rotation and rollback do not form a transaction. Lock, validation, append,
flush, synchronization, rollback, rotation, and cleanup failures are suppressed;
a record may be dropped, and a failed rotation can leave fewer retained files.

Do not rename or replace `debug_logs/`, and do not edit, replace, or create
another hard link to `.runtime.lock` or the active/backup files while the
workspace may be used. Holding the directory descriptor prevents a renamed or
replaced pathname from redirecting leaf operations, but validation still covers
only the identities observed at each check. It cannot make concurrent external
filesystem changes transactional. Paths and retention are listed in
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

## Mutation guarantees

The runtime never silently overwrites a manuscript. A regular existing target
raises `ManuscriptExistsError`; a symlink target or component is rejected with
`WorkspaceSecurityError`. Complete core-update and manuscript preconditions are
validated before content mutation.

Individual replacements are staged in the destination directory, flushed, and
atomically replaced. New manuscripts and conversation identities use exclusive
creation. Approved artifact commit writes allowlisted core documents first and
the manuscript last. If a later step fails, the runtime makes a best-effort
attempt to remove the new manuscript and restore each earlier core file;
rollback failures are attached to the original exception.

These are per-file guarantees, not a transactional filesystem or database.
There is no single atomic boundary across core documents, manuscript, logs,
state, and result. Rollback may fail, and abrupt process, operating-system,
hardware, filesystem, or power failure may leave a partially durable
cross-file operation. Inspect after infrastructure interruption.

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
