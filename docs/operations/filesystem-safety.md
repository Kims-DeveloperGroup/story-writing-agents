# Filesystem safety

> **Availability:** generic workspace containment, descriptor-relative
> writing-artifact publication and recovery, exclusive conversation creation,
> serialized transcript replacement, atomic per-file default/metadata
> replacement, guarded interactive settings persistence, and content-free
> explicit-reference authorization evidence and inline runtime/provider
> enforcement are implemented. Interactive `$`/`$$` discovery and viewer
> attachment use the same verified repository boundary; they add no artifact
> write path.

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
the operation manifest and `requested` evidence under its repository lease
before catalog or content reads. It then uses the same descriptor-relative,
no-follow adapter and eligibility rules as other artifact access. Each unique
path is securely read once at initial capture; multiple ranges retain request
order while sharing that immutable source snapshot.

The complete source must be strict UTF-8 without NUL. Universal CRLF, LF, and
lone-CR records determine exact one-based range slices, including their line
terminators. Each selected reference may contain at most 128 KiB and the batch
at most 256 KiB, inclusive. Validation errors use fixed codes and do not echo
paths or content.

A repository-minted capability is bound to one authorization, correlation, and
exact ordered batch and can be consumed only once. Before returning captured
bytes, accepting an invocation, recording an operation checkpoint, or finally
accepting the operation, the repository reopens each unique source and checks
its full catalog provenance and SHA-256 against capture. Captured content is
never refreshed. A path/range mismatch consumes the capability; provenance or
byte drift fails closed.

Interactive listing uses only the repository's current eligible catalog and
excludes transcripts, discussion logs, provider/agent state, configuration,
diagnostics, and repository metadata. A picker/viewer activation performs a
fresh bounded repository read before displaying text. The viewer receives text,
not a path or filesystem handle, and retains no repository authority. A stale
choice or listing-to-view race returns a fixed `workspace_invalid` failure; a
later submission independently captures and revalidates the then-current
artifact rather than reusing viewer bytes. A legitimately published new
current version may therefore be what agents read. No UI operation creates
provenance, repairs a stale artifact, or changes canonical bytes.

Operation and invocation manifests/events are schema-v1 canonical JSON,
published create-only with exact path identities and contiguous sequences.
Delivery evidence is written before the transient content is returned. A
partial, replaced, aliased, malformed, noncanonical, duplicated, gapped, or
invalidly transitioned evidence tree is corrupt and is neither repaired nor
rewritten. Never edit `writing_artifacts/input_snapshots/`; it is repository
state, not a canonical artifact directory.

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

On the next artifact access, an exact catalog-before position rolls forward;
an exact catalog-after position is verified without republishing. A catalog
matching neither image, target divergence, changed or missing journal bytes,
missing/divergent published commit, or invalid authoritative transaction state
writes a persistent marker and blocks all later artifact access. Every `.tmp`,
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
