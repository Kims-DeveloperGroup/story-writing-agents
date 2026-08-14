# Filesystem safety

> **Availability:** containment checks, no-symlink handling, exclusive
> manuscript and identity creation, serialized transcript replacement, atomic
> per-file replacement, and best-effort artifact rollback are implemented.

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

The symlink rule protects logical boundaries as well as physical containment.
A link beneath `manuscript/` cannot redirect an output into `core_documents/`,
and a linked core document cannot redirect a consensus update into `config/`.
Workspace and template identifiers have additional conservative validation.

CLI discovery without `--workspace` selects the nearest fully initialized
ancestor of the current directory. Use an explicit path in automation or when
nested workspaces make that choice unclear. Discovery never initializes a
directory.

Orchestration core-document access is limited to the four fixed filenames.
Manuscript names may use safe relative subdirectories beneath `manuscript/`.
A direct client of generic `Workspace.write_text()` is trusted and can modify
any safely contained relative file; containment is not authorization.

The filesystem conversation adapters bind safe session and conversation IDs to
fixed metadata and transcript paths. They apply no-symlink and containment
checks to every directory and file, then require embedded IDs to match the
owning path. This preserves namespace integrity but is not access control: any
process with workspace access can still name another session.

The provider-session claim guard is opened without following symlinks where the
platform provides that flag, checked as a regular file, and verified so the
open descriptor and pathname keep the same file identity. Unsafe, replaced,
unverifiable, or unsupported guard state fails closed. POSIX uses exclusive
`flock`; Windows uses advisory one-byte locking. Closing the descriptor,
including at process exit, releases the operating-system lock. Replacing the
guard pathname could split cooperating processes across different locks, so do
not delete or replace it while a workspace may be used.

## Conversation transcript safety

Key terms: **in-process lock**—serialization inside one Python process;
**session lease**—the cross-process same-session write guard; **fail closed**—
rejecting suspicious persisted data instead of repairing or skipping it.

Transcript reads and appends for the same workspace, session, and conversation
share a weakly retained process-wide lock. Different conversations are not
serialized together. An append validates the complete old transcript, then
atomically replaces the complete file; readers observe the old or complete new
version, not a partial appended line.

The event-store adapter does not acquire or verify a session lease. A caller
that permits multiple processes must use the story-session lease to exclude
same-session writers. The lease is the cross-process guard; the in-process lock
alone is insufficient.

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
manuscripts, core documents, CLI selection, or agent state.

Invalid identifiers raise `SessionIdError`; unsafe paths or symlinks raise
`WorkspaceSecurityError`; malformed or path-mismatched persisted data raises
`WorkspaceFormatError`. Reads fail closed rather than repairing a record.

Exact validation patterns are in [Identifiers and errors](../reference/configuration/identifiers-and-errors.md).
