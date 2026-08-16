# Filesystem safety

> **Availability:** containment and no-symlink checks, exclusive manuscript and
> conversation creation, serialized transcript replacement, atomic per-file
> selection/metadata replacement, and best-effort artifact rollback are
> implemented.

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
redirect a manuscript, core document, selection, catalog record, or transcript.
Workspace and template identifiers have additional validation.

CLI discovery chooses the nearest initialized ancestor and never initializes a
directory. Use an explicit path for automation or nested workspaces.

Orchestration core-document access is limited to the four fixed filenames.
Manuscript names may use safe relative subdirectories beneath `manuscript/`.
A direct client of generic `Workspace.write_text()` is trusted and can modify
any safely contained relative file; containment is not authorization.

Conversation adapters and selection helpers bind safe IDs to fixed paths. They
check every component and require embedded IDs to match the owning path. This
protects namespace integrity, not access control: a process with workspace
access can still name another session.

The provider-session claim guard is opened without following symlinks when the
platform supports it, verified as the same regular file, and locked with POSIX
`flock` or Windows one-byte advisory locking. Suspicious state fails closed.
Do not replace the guard pathname while a workspace may be used.

## Conversation transcript safety

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

Session and conversation selection saves also replace one complete fixed JSON
file. A conversation-selection failure preserves its prior bytes and cannot
alter catalog metadata; operations spanning both files are not atomic. See
[Persistence](../design/persistence.md) for the cross-file consequence.

Invalid identifiers raise `SessionIdError`; unsafe paths or symlinks raise
`WorkspaceSecurityError`; malformed or path-mismatched persisted data raises
`WorkspaceFormatError`. Reads fail closed rather than repairing a record.

Exact validation patterns are in [Identifiers and errors](../reference/configuration/identifiers-and-errors.md).
