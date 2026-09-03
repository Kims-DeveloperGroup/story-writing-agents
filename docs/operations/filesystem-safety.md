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
> storage path. Bounded agent-exchange storage uses another independent
> descriptor-safe subtree and guard. Privileged filesystem conversation archive transfer is
> available only through a direct Python adapter under a caller-held mutation
> lease. Explicit next-shell filesystem/SQLite selection and optional direct
> Python SQLite conversation storage use one workspace coordinator; SQLite has
> its own fixed database, derived search companion, transaction, and sidecar
> boundary. Conversation RAG also has a separate identity-bound, disposable
> per-session generation store. An explicitly injected runtime service or the
> noninteractive local lifecycle/search commands can authorize and schedule its
> use; interactive `/settings` persists next-shell policy, and startup composes
> Local only beside a complete authoritative storage bundle. Exact session
> branch/ref/sibling-worktree provisioning is also implemented as
> package-internal #504 infrastructure. Package-internal #508 adds the shared
> repository lease, durable content-free session-history records, and a bounded
> alternate checkpoint index. Neither is yet connected to installed story
> commands.

Key terms: **containment**—keeping paths beneath the workspace root; **symlink**—
a filesystem alias the runtime rejects; **exclusive creation**—failure instead
of overwrite; **atomic file**—a replacement observed as old or complete new data.

## Workspace boundary

`Workspace` classifies the exact lexical absolute root without mutation. An
existing root must be a non-symlinked directory with a meaningful device/inode
identity. An absent root remains pending; an empty root is eligible; an exact
initialization journal is resumable; an exact clean v2 root is ready. Any other
nonempty, malformed, drifted, or unsafe state fails closed.

Before creating a missing root, initialization rejects any repository ancestor.
Before mutating an empty root, it requires Git topology `absent`. Journal-backed
recovery accepts only `absent` or an exact standalone repository. Parent
repositories, nested repositories, linked worktrees, submodules, sparse or bare
repositories, root mismatches, and unsafe Git administration are never adopted.

Initialization preflights required descriptor-relative, no-follow,
exclusive-create, synchronization, identity, and cleanup operations. It opens
the filesystem anchor, creates/walks each component through retained directory
descriptors, and repeatedly compares the opened and named root identities. If a
required primitive is unavailable, it raises `OSError` before root mutation.

For a missing or empty root, the first scaffold mutation is an exclusively
created and synchronized `.story-writing-agents-initialize-v2.json`. Its strict
canonical JSON binds schema version `2`, state `initializing`, and the SHA-256
identity of the exact 22-file manifest. Existing entries during recovery must
be byte-identical members of that manifest; unknown files, undeclared
directories, symlinks, nonregular entries, mismatched bytes, or a different
journal are preserved and rejected. There is no overwrite, adoption, cleanup,
upgrade, or weaker fallback path.

Missing exact scaffold files are installed create-only through synchronized
temporary regular files and descriptor-relative hard-link publication. The
initializer then asks the closed Git adapter for one fixed parentless `main`
commit. The adapter verifies the effective author and committer and never uses
an agent-invented identity. An identity failure leaves the exact journal and
scaffold available to a fresh retry but creates no absent Git repository.

After baseline creation, the initializer verifies the exact marker, scaffold,
manifest, repository topology, one commit, branch, index/tree, and a status
containing only the untracked journal. It removes and synchronizes only that
journal, then repeats ready verification and root-identity comparison. Success
is reported only after the journal is absent and status is clean. `story init`
itself creates no debug-log event or other post-baseline residue.

An `OSError`, `KeyboardInterrupt`, `SystemExit`, typed workspace failure, or Git
failure terminalizes that `Workspace` object. It does not roll back durable
exact partial progress. Every later `initialize()` call on the same object
raises before another attempt; a fresh object/process must reclassify the
journal and all bytes. A clean ready-root `initialize()` is read-only and
creates no second commit. Dirty or drifted ready state is preserved and reported
as recovery-required.

`Workspace.require_ready()` checks the authorization captured by that object;
it is not a live disk scan. Fresh CLI resolution, explicit smoke, and fresh
construction perform strict current inspection. A long-lived `Workspace` is
not an identity-pinned filesystem snapshot, watcher, or transaction across
operations. Every public path operation rejects:

- absolute paths;
- any `..` path component;
- a resolved target outside the workspace root; and
- every symbolic-link component, even when it points elsewhere inside the
  workspace.

The symlink rule protects logical as well as physical boundaries. A link cannot
redirect a manuscript, core document, default, catalog record, or transcript.
Workspace and template identifiers have additional validation.

CLI discovery selects the nearest recognizable boundary and validates it. A
marker, initialization journal, or known legacy sentinel stops the search, so
an unsupported or malformed inner boundary is never skipped for an outer one.
Discovery and explicit paths never initialize. Use the exact root for
automation; nested workspace/repository initialization is unsupported.

<a id="session-git-resource-safety"></a>

## Session Git-resource safety

The package-internal owner freshly binds the initialization root and its exact
non-symlink `.git` directory for every operation. One repository-wide advisory
lease on the opened common-Git directory now serializes provisioning and
session-history work across both same-session and different-session calls.
Provisioning additionally retains its persistent owner-only lock:

```text
.git/story-writing-agents/runtime/session-provisioning/.lock
```

Read-only inspect/list/require-ready operations never create that namespace.
Provision may create the private directories and lock, then writes one
owner-only `<session_id>.json` journal by descriptor-relative, no-follow,
exclusive creation. The immutable payload is canonical UTF-8 JSON with a
trailing newline and a 64-KiB maximum. The journal file and its containing
directory are synchronized before any seed, ref, branch, or worktree mutation.
Malformed names, types, links, modes, duplicate or unknown fields, noncanonical
bytes, and identity substitution fail closed.

Under the lock, provisioning performs this fixed order:

```text
verify main baseline and exhaustive inventory
-> reject sibling-path and identity collisions
-> capture effective identity, UTC timestamp, baseline, provisioning ID
-> publish and synchronize the immutable journal
-> create and fully decode/verify the deterministic empty-tree seed object
-> compare-and-swap internal ref from zero to the seed
-> compare-and-swap session branch from zero to the baseline
-> create the exact sibling linked worktree for that branch
-> verify root invariance and the correlated triple
-> remove and synchronize only the journal
-> verify the complete journal-free result again
```

The main root must keep symbolic `HEAD` on `main`, the same baseline/tree/index,
the exact initializer files, and clean status. Inventory accounts for every
branch, session internal ref, registered worktree, and provisioning journal;
unknown or uncorrelated facts are not ignored. An accepted session worktree
must be at its schema-derived sibling path, have exact common-Git indirection
and registration, use its expected symbolic session branch, and retain the
immutable baseline and seed roots. Later tips may advance, but #504 does not
interpret that #505 history.

Every Git child uses fixed arguments, `shell=False`, bounded streams and time,
disabled prompting, hooks, signing, filters, pagers, and inherited Git control
variables, followed by root-identity revalidation and complete process/pipe
cleanup. Only a temporary file still proved to belong to the in-process
operation can be removed automatically. Named refs, branches, worktrees,
administration entries, Git objects, journals that carry recovery evidence,
and user bytes are never cleaned up automatically. See
[Session Git-resource recovery](recovery-and-limitations.md#session-git-resource-recovery).

For a ready session, the same common-Git lease guards
`story-writing-agents/runtime/session-history/<session_id>/`. The history owner
walks from the already-open common-Git descriptor, creates private directories,
and accepts only exact names and no-follow single-link regular leaves. Candidate
records use exclusive immutable publication. Operation records use synchronized
temporary publication and identity-checked atomic replacement, with only an
exact sequence increment and legal state successor accepted. All JSON is
canonical, at most 1 MiB, and synchronized before a later owner can rely on it
to authorize Git mutation.

The checkpoint-input leaf is derived exactly as
`checkpoint-inputs/<operation_id>/index`, bounded to 64 MiB when inspected, and
accepted by only the internal-checkpoint process profile. The process owner
strips arbitrary inherited `GIT_*` variables, disables hooks/signing/external
diff/fsmonitor/pagers/editors/prompts, and revalidates the root and every index
ancestor before and after the child. An ambiguous or recovery-required index is
preserved; only the exact durably committed checkpoint permits removal. See the
[internal session-history contract](../reference/git-adapter.md#package-internal-session-history).

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
protected work and is preserved without cleanup, reconstruction, or conversion.
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
rewritten. Schema-v1 evidence cannot authorize, resume, or be converted into a
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

Explicit CLI smoke resolves an already ready root, then constructs one fresh
`Workspace` to reverify the exact clean v2 marker, scaffold, baseline, and
status. It performs no initialization, directive/catalog/history work, or
debug-log handle/submission, and creates no filesystem residue. Its report has
null session/conversation IDs and only the initialization and unchanged-
execution checks.

Implicit smoke instead owns a disposable directory, uses the real initializer,
exercises provider-free management/storage checks, and removes the complete
directory. Effective Git identity failures propagate truthfully; the temporary
directory is still removed. Neither form can redirect work through a replaced
root or use diagnostics to change its primary result.

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

<a id="agent-exchange-filesystem-safety"></a>

## Agent-exchange filesystem safety

The exchange store borrows only `Workspace`'s identity-bound root open and
reverification helpers. It opens and retains descriptors for that root, the
exact `debug_logs` child, the exact private `agent_exchanges` child, and the
selected private session directory. It never joins caller input into an
absolute path or follows a final component. Session, scope, agent, and exchange
identities are validated before they can influence a managed name; only the
session and canonical agent identify directories or files.

Safe operation requires `O_DIRECTORY`, `O_NOFOLLOW`, descriptor-relative
mkdir/open/stat/unlink/rename, no-follow stat, descriptor-based directory
listing, meaningful positive device/inode identity, and either the POSIX or
Windows advisory-lock mechanism. The host must provide all of them. If one is
missing, the operation returns `unsupported_platform`; there is no pathname,
temporary-directory, unlocked, or other weaker fallback.

The store and each session directory must be exact directories with mode
`0700`. Every guard, state, pending, active, and backup leaf must be an exact
regular file with mode `0600`, link count one, a bounded size, and the same
device/inode identity through its no-follow name and open descriptor. Opens
request nonblocking and noninheritable behavior where available. The owner
rejects symlinks, hard links, FIFOs and other special files, wrong modes,
case-folded or Unicode-normalized aliases, root/debug/store/session replacement,
unexpected owned names, and a name or identity changed during an operation.
It does not chmod, relink, reinterpret, or follow unsafe state.

Every operation uses a weak per-workspace process lock plus the independent
persistent `debug_logs/agent_exchanges/.agent_exchanges.lock`. Both acquisition
attempts are bounded to 0.1 seconds; cross-process advisory locking serializes
append, rotation, pruning, inventory, and snapshot with other processes.
Exchange locking is separate from `.runtime.lock`, the provider-error `.lock`,
provider-session access claims, conversation-storage coordination, and
writing-artifact locks.

Under the guard, the owner bounds each directory listing to 4,096 entries and
validates the complete exchange namespace before trusting any stream. State is
strict canonical schema-v1 JSON, at most 512 bytes. Segments are complete
strict-UTF-8 canonical schema-v1 JSONL, at most 2 MiB each, ending at a complete
newline. The scanner rejects duplicate JSON members, extra/missing fields,
noncanonical bytes, invalid enums or identities, inconsistent byte/truncation
facts, inconsistent Unicode-code-point counts, invalid timestamps, missing or
lower high-water state, duplicate global sequences, and duplicate or decreasing
per-stream/global order. The retained character count must equal Python
`len(content)` for the decoded prefix; it cannot exceed the full character
count. Untruncated records require both count pairs to be equal, while truncated
records require both retained counts to be lower. Valid sequence gaps remain
permitted because a durable reservation can outlive a failed append.

`append()` alone recognizes and removes exact verified `.state.pending`,
`.<agent>.sequence.pending`, and `.<agent>.segment.pending` remnants before its
full scan. A reader never performs that cleanup: a visible pending state is
corrupt, and inventory/snapshot fail without mutation. Pending cleanup never
uses a wildcard and never removes an unrecognized, aliased, linked, oversized,
or wrong-mode leaf.

An append writes all bytes, synchronizes the open segment, and then reverifies
identity and expected size. If that body fails, it truncates only the still-open
same inode to the captured start size and synchronizes the rollback. A newly
created failed segment is discarded only when its identity still matches.
Rotation renames only captured managed inodes into verified absent managed
names. Pruning rewrites a split segment through one exclusive `0600` pending
leaf, synchronizes it, atomically renames it, synchronizes the directory, and
rereads the exact published payload. These steps reduce partial data but do not
form one transaction; durable high-water gaps and earlier completed rotation
shifts can remain after a reported failure.

Primary body failure has precedence over lock-release or descriptor-close
failure. If only cleanup fails after the append body completed, the API reports
`io_failed` even though the complete record remains durable. A process
control exception such as `KeyboardInterrupt` retains its original identity
over cleanup failure. Cleanup still attempts all held descriptors and locks, so
a later operation can proceed when the underlying namespace remains safe.

The public `AgentExchangeStorageError` reveals no path, provider content, raw
exception, or OS message. Use its fixed class as follows:

| Failure | Safe next action |
| --- | --- |
| `unsupported_platform` | Use a host with the required descriptor/no-follow and advisory-lock primitives. Do not redirect storage to a weaker path. |
| `contention` | Let the current exchange operation finish, then retry; do not remove the guard. |
| `unsafe_storage` | Stop writers and preserve the workspace. Inspect ownership, type, link count, mode, exact names, and root identity with trusted local tools; do not follow or repair an alias in place. |
| `corrupt_storage` | Stop writers and restore the complete exchange subtree from a trusted consistent backup. Do not edit one JSON line, state file, sequence, or pending leaf to force acceptance. |
| `capacity_exceeded` | Check free space/quota and whether a managed leaf or namespace exceeds its fixed bound. A later append can prune a safe interrupted over-cap JSONL state, but it cannot repair oversized or unrecognized state. |
| `io_failed` | Treat append completion as indeterminate. Inspect content-free inventory and the exact immutable snapshot before retrying because cleanup-only failure can follow a durable append. |
| `not_found` | Refresh inventory; the selection may be wrong, absent, rotated, or globally pruned. Readers do not create missing state. |

There is no supported in-place repair or delete API. If diagnostics may be
discarded and a trusted backup is unavailable, stop all Story processes and
quarantine the entire `debug_logs/agent_exchanges` subtree as one unit before
allowing a future append to create a fresh store. That intentionally loses all
exchange history and sequence continuity; moving individual leaves can convert
one failure into another. Quarantine or ordinary deletion is not secure erase.

The owner lists `debug_logs` names only to verify the exact child and aliases.
It never opens, parses, validates, imports, double-writes, rotates, prunes,
modifies, or counts
the files or bytes in `debug_logs/runtime.jsonl`,
`debug_logs/provider_error_responses/`, or malformed entries beneath those
sibling owners toward exchange retention. Their sizes do not consume the
16-MiB exchange cap. Conversely, those owners have no authority over
`agent_exchanges`. See [persistence ordering](../design/persistence.md#agent-exchange-persistence),
the [exact topology](../reference/configuration/session-storage.md#agent-exchange-storage),
and [privacy](privacy-and-provider.md#agent-exchange-privacy).

## Unsupported transcript sentinel

The active filesystem schema-v2 owner treats any `transcript.jsonl` below an
addressed catalog conversation as an unsupported sentinel. It detects that
file before append/read mutation and preserves it unchanged. A symlink or
unsafe owning path also fails closed. #454 separately owns a future
whole-workspace rejection; this boundary checks only the conversation being
opened.

<a id="schema-v2-collaboration-history-safety"></a>

## Schema-v2 collaboration-history safety

`FilesystemCollaborationEventStore` is a direct-module-only concrete adapter
placed behind the selected filesystem service bundle. It is not exported
through a package facade. It owns these siblings below an existing catalog
conversation:

```text
collaboration-history.jsonl
.collaboration-history.lock
```

`transcript.jsonl` is rejected as described above. Construction and an
absent-history read create no v2 storage. Present reads and appends verify
catalog identity, use the workspace's contained no-symlink path checks, and
hold both a weak per-history process lock and a blocking POSIX exclusive
advisory lock. A present guard must remain the same regular non-symlink file
after open. Unsupported safe-open, advisory-lock, or directory-fsync primitives
fail closed rather than weakening the operation.

The history must be strict canonical schema-v2 JSONL: one exact encoded event
per line and exactly one final LF. The adapter validates every record's path
identity, complete batch metadata, contiguous sequence, unique IDs, digest,
state transition, correlation, budget, and co-batch closure before returning a
read or considering an append. A present empty file, malformed or noncanonical
JSON, invalid UTF-8, schema-v1 record, partial batch, or invalid complete
history raises `WorkspaceFormatError`. `append_batch()` maps that stored
corruption to `CollaborationAppendError(CORRUPT_HISTORY)`; neither path mutates
the rejected bytes.

Append publication is one complete-file replacement:

1. validate the locked current history and candidate;
2. assign contiguous sequence/batch metadata and canonical UTC timestamps;
3. write the complete canonical image to a same-directory mode-`0600`
   temporary file;
4. flush and file-fsync it;
5. atomically replace `collaboration-history.jsonl`; and
6. mandatorily fsync the parent directory before returning `APPENDED`.

Failure before replace leaves the old history or absence authoritative and
cleans the temporary file when cleanup remains possible. Failure after replace,
including directory-fsync, advisory unlock, or descriptor close, is uncertain:
the call reports an operational error while a complete new history may be
visible. Retry only the exact same `batch_id` and exact canonical drafts. If
the replacement is present, validation returns the original sequence range and
timestamps as `ALREADY_COMMITTED`; do not create a fresh batch merely because
the first call lacked a receipt.

The history contains plaintext user-visible proposal, message, assignment,
reply, decision, result, notice, interruption, and state facts. A newly staged
history file and newly created guard request mode `0600`; existing workspace
directory ownership and access policy remain the operator's responsibility.
The adapter is not encryption, authorization, backup, retention, secure erase,
or permission repair.

Use failures as follows:

| Observation | Safe response |
| --- | --- |
| `APPENDED` | Continue from the returned exact receipt and freshly read history. |
| `ALREADY_COMMITTED` | Treat the exact retry as success; retain its original sequences and timestamps. |
| Fixed semantic conflict | Preserve history; reread for stale CAS, or repair caller-generated batch/event identity. Never force the rejected candidate. |
| Operational error before commit is known | Preserve the workspace. If replace might have happened, retry only the exact batch. |
| `WorkspaceFormatError` or `corrupt_history` | Stop normal appends, preserve the exact bytes, and restore only a trusted complete schema-v2 history through an owning future recovery/transfer workflow. |
| Unsafe lock/history path | Stop writers and inspect the containing workspace with trusted local tools. Do not follow, replace, or repair an alias in place. |

Do not hand-edit, pretty-print, truncate, combine, or copy unsupported
transcript bytes into this file. Durable confirmation and projected
next action remain evidence only; reading them never recreates a provider,
workspace lease, Git capability, credential, or permission to repeat work. See
[Structured collaboration history](../reference/collaboration-history.md#direct-filesystem-storage-and-retry)
and the [persistence boundary](../design/persistence.md#structured-collaboration-history-persistence).

<a id="conversation-archive-filesystem-safety"></a>

## Conversation archive filesystem safety

`compose_filesystem_conversation_archive()` captures one canonical absolute
root and workspace key in a private immutable topology. It retains no caller
`Workspace` or public mutable catalog/event-store collaborator and performs no
archive scan or mutation. After pure import-payload canonicalization where
applicable, every export and import validates an active, unexpired,
owner-thread-bound lease for that key before constructing its one private
operation-local `Workspace`, catalog, and event-store set. A wrong-root lease
therefore fails before collaborator materialization or filesystem I/O, and
later caller-root mutation cannot redirect root A transfer into root B.

This archive-owned topology is independent of composed-service provenance and
SQLite's pinned database owner. The caller must stop or otherwise coordinate
ordinary catalog and history writers for the complete operation: those
ordinary adapters do not acquire the archive lease, and the lease is not an
authorization decision.

The archive scans only canonical `metadata.json` and, when nonempty,
`collaboration-history.jsonl`. It validates but excludes the safe
`.collaboration-history.lock`. Any `transcript.jsonl` anywhere in the canonical
conversation namespace rejects export or import before destination mutation;
legacy bytes are never converted. Import performs a complete preflight, then
publishes create-only metadata and, for nonempty history, one complete history
file. An interruption may leave exact metadata-only residue, which the same
canonical payload can complete; divergent or unsafe residue remains and fails
closed.

Export strictly enumerates only the authoritative
`discussion_logs/<session_id>/interactive/conversations/<conversation_id>/`
namespace. `metadata.json` is required. `collaboration-history.jsonl` is absent
for an empty history and must contain nonempty canonical JSONL when present.
IDs, metadata, complete collaboration history, pairing, and path correlation
are revalidated through addressed `FilesystemConversationCatalog.get()` and
`FilesystemCollaborationEventStore.read()` calls. No catalog-wide list is used.

Every scanned directory must remain a real directory. Every included file must
be regular and singly linked. Symlinks, hard links, nonregular files, malformed
or empty present collaboration histories, orphan/unknown conversation
artifacts, invalid identifiers, and unexpected interactive layout fail closed.
Every legacy `transcript.jsonl` is rejected before destination mutation; it is
never interpreted as collaboration history. Safe regular non-session root files
and safe regular noninteractive session siblings are excluded without reading
their content, but each consumes the one-million-entry inventory budget; a safe
session without `interactive` is skipped. An unsafe excluded entry fails rather
than being followed or silently ignored. The normal
`discussion_logs/.gitkeep` sentinel must be a singly linked regular file whose
zero size is verified from metadata; its bytes are not read.

Export performs two complete scans and compares their canonical archive bytes
under the same lease. A difference or second-scan archive failure becomes
`ConversationArchiveSourceChangedError`. This is change detection, not a lock
or filesystem snapshot: do not copy a live, ordinarily mutating source and
rely on the second scan for consistency. A valid excluded-only change does not
alter the compared bytes, although it remains bounded and validated in its
scan.

Import first canonicalizes and fully validates the bounded archive in memory.
It then scans the complete destination namespace, classifies the full
destination-plus-payload union, builds exact planned metadata and canonical
collaboration-history bytes, projects paths, descendant entries, included
bytes, logical text, and canonical wire, and checks every planned target and
ancestor path before writing anything. A late divergent identity or projected
bound failure therefore prevents an earlier absent identity from being
installed. Missing state is published as complete create-only files:
`metadata.json` first, then `collaboration-history.jsonl` only when the history
is nonempty. An empty history is represented by metadata alone; a present empty
history file is invalid. Exact domain-value collisions are accepted; divergent
or unsafe collisions are preserved and rejected.

The lease and containing path are revalidated around each installation. A
complete destination rescan must then match the preflight inventory plus the
requested archive. Import never overwrites, replaces, or deletes conversation
or excluded state. It can add to a nonempty destination and preserves unrelated
complete records byte-for-byte. A post-preflight exact target collision is
accepted only after an addressed domain reread; a divergent collision or
unrelated post-write mutation is preserved and reported without rollback.

Operational failure may leave canonical directories, exact metadata without
its nonempty history, or complete exact earlier conversations. The same
canonical payload can resume that recognized inactive metadata-only residue
and publish its one complete history file under a fresh correct lease after the
destination is quiescent. Unknown artifacts, divergence, unsafe topology, or a
post-preflight change instead fail closed; the adapter performs no cleanup or
automatic retry. After each addressed conversation has been published, import
rereads its exact metadata and complete collaboration history before invoking
the durable-progress callback used for a transfer checkpoint. Callback failure
stops the import but does not roll back complete create-only files. Neither the
archive import nor its callback activates a backend. Path revalidation narrows
but cannot eliminate a hostile same-user race between the last check and a
native filesystem call.

Archive errors are content-free and never expose the rejected path or bytes.
Exact values, bounds, exclusions, statuses, and error types are in
[Canonical conversation archives](../reference/conversation-archives.md),
including the exact
[scan and projected-union limits](../reference/conversation-archives.md#filesystem-archive-bounds).

<a id="composed-conversation-storage-provenance"></a>

## Composed conversation-storage provenance

Direct standalone filesystem catalog and event-store adapters retain their
established borrowed-`Workspace` behavior. The stronger provenance guarantee
begins only at `compose_filesystem_conversation_storage`: the returned
catalog, event store, reference search, and mutation coordinator all derive
from one construction-pinned canonical absolute root. Changing the caller's
`Workspace.root` or rebinding the composed instances' inherited collaborators
cannot split root A provenance from root B I/O. This does not make the general
`Workspace` type immutable.

The composed filesystem catalog's create, rename, archive, and unarchive plus
the composed event store's append borrow this same coordinator. Their reads
remain observational. Direct standalone filesystem adapters retain their
uncoordinated compatibility behavior and therefore do not participate in
selection, startup, or transfer safety unless their caller stops or otherwise
coordinates them.

A separately constructed
`WorkspaceConversationStorageMutationCoordinator` also pins its root at
construction without opening a file. Its in-process coordination key refers to
that root; each outer `borrow` opens its fixed
`config/.conversation_storage.lock` guard and issues a lease for the same root.
Later caller mutation cannot move the guard to another root; nested borrowing,
expiry, timeout, and cleanup semantics are unchanged.

Activation-state operations pin at call entry instead. Compare-and-set rejects
a lease for another root with `ConversationStorageCoordinationError` before
creating an operation workspace or performing filesystem I/O; a matching call
reads, revalidates its lease, and publishes only within the captured root.
Caller-root reassignment during a load or compare-and-set therefore cannot
redirect its read or write. This path provenance is not an inode-pinned
snapshot or a transaction with catalog, event, transfer, or SQLite work.
SQLite keeps its separate already-pinned database owner. See the
[composition API](../reference/package-api.md#conversation-storage-composition).

<a id="conversation-storage-transfer-safety"></a>

## Conversation storage transfer safety

The transfer coordinator captures one canonical workspace root and acquires
one outer `WorkspaceConversationStorageMutationCoordinator` lease for the
complete operation. That lease covers activation/readiness checks, fresh
canonical exports, planning, all destination work, checkpoint publication, and
fresh final parity. Root and lease correlation are revalidated throughout, so
later mutation of the caller's `Workspace.root` cannot redirect any phase.
Acquiring the lease may create the empty, owner-only
`config/.conversation_storage.lock` guard even for dry-run or verify; those
workflows create no conversation rows/files or checkpoint.

The coordinator serializes every supported filesystem/SQLite writer that uses
the same guard. Direct low-level filesystem catalog/event-store writers do not
acquire it automatically, and external database/file editors do not participate
at all. Stop or otherwise coordinate those writers before transfer. The guard
is advisory coordination, not caller authorization, a hostile-process defense,
or a transaction spanning both stores.

Transfer reads each per-direction checkpoint through a bounded no-follow file
descriptor and requires the fixed name and descriptor to remain one regular,
singly linked file with stable identity and size. Strict UTF-8 JSON parsing
rejects duplicate, missing, extra, noncanonical, oversized, wrong-direction,
unsupported-version, invalid-count, or unsafe-identity state. Symlinks, hard
links, nonregular files, replacement races, and ambiguous state are preserved
and fail closed. Publication uses the workspace's contained atomic JSON write,
compares the expected prior checkpoint before and after writing, revalidates the
lease, and rereads the exact result. Do not edit, relink, or replace a checkpoint
to manufacture progress.

Filesystem-to-SQLite destination work occurs in one `BEGIN IMMEDIATE`
transaction, so an uncommitted failure rolls back the complete import. It
imports exact canonical BLOBs into `collaboration_events`; any legacy
`conversation_events` row rejects the archive before mutation. FTS is excluded
from parity, and a best-effort refresh after commit cannot change canonical
success or failure.
SQLite-to-filesystem work first preflights the complete destination union, then
uses create-only archive publication. A progress callback runs only after one
conversation's metadata and optional history are present as one complete
canonical value and have been reread exactly. If checkpoint publication fails,
those complete files remain; the next run accepts them only after fresh source
and destination exports revalidate the fingerprint, counts, completed
identities, and exact records.

Conflict, unsupported SQLite values, or destination-only data blocks transfer
before checkpoint or destination mutation. Final parity still uses fresh
canonical exports. A source change can therefore be discovered after a complete
destination transaction, and a final mismatch can follow create-only
filesystem publication; neither condition triggers destructive rollback. See
[transfer recovery](recovery-and-limitations.md#conversation-storage-transfer-recovery)
before retrying.

<a id="conversation-storage-selection-and-startup-safety"></a>

## Conversation storage selection and startup safety

Desired selection captures one canonical workspace root and first borrows the
workspace coordinator only long enough to prove safe mutation is available. It
releases that proof lease before interactive consent or a host installer, so a
long-running native package manager does not retain the conversation-storage
guard. The installer receives only one allowlisted argument tuple, runs without
a shell at the captured workspace root, and inherits stdin/environment; its
stdout and stderr use the caller's diagnostic stream. No timeout, retry,
privilege escalation, automatic flag, or arbitrary command is introduced.

After installation, selection reacquires the coordinator and reprobes SQLite
through the same active Python interpreter. That lease then spans create-only
empty-database provisioning when needed, requested-backend composition, active-
state read, fresh canonical source/target parity, and final configuration
publication. Immediately before publication it validates the lease, invokes an
optional caller guard, validates the lease again, and runs the config-owned
exact-byte complete-document CAS. Settings supplies its fresh persona-catalog
guard at this point. Guard, byte, validation, or write failure preserves prior
configuration whenever the config rollback contract completes; an installed
host package or empty database is deliberately outside that rollback.

Selection never writes `config/conversation_storage_state.json`. Interactive
startup owns that marker and holds one outer coordinator lease while it reloads
configuration, composes current active services, freshly checks target
readiness/parity, composes the complete target services, validates the lease,
and compare-and-sets the expected generation. Failure before CAS preserves the
marker and aborts without fallback. A completed CAS is the durable activation
boundary and is not compensated if later terminal or provider startup fails.
The current process never replaces its bundle live.

Neither workflow is a transaction spanning host packages, both conversation
stores, configuration, activation, sessions, providers, or other workspace
channels. The advisory guard serializes only supported participants; direct
standalone filesystem adapters and external file/database editors remain
outside it. See the exact
[CLI contract](../reference/cli.md#conversation-storage-selection) and
[selection recovery](recovery-and-limitations.md#conversation-storage-selection-and-activation-recovery).

<a id="conversation-rag-generation-store-safety"></a>

## Conversation RAG generation-store safety

The direct `ConversationRagGenerationStore` is the sole owner of
`agent_states/orchestrator_state/conversation_rag`. Construction retains the
supplied `Workspace` and its established identity without touching the
filesystem. Operations require POSIX `fcntl`, descriptor-relative directory
and file calls, no-follow flags, meaningful device/inode identities, and
directory synchronization. Missing capability reports `unsupported`; there is
no pathname or lock-free fallback.

The owner creates/verifies every directory beneath its fixed subtree as mode
`0700` and every regular file as mode `0600`. It rejects symlinks, hard-linked
files, nonregular names, wrong modes, unexpected namespace entries, changed
descriptor/name identity, root replacement, and unsafe generation contents.
Traversal, listing, open, read, write, rename, unlink, and removal are relative
to retained verified directory descriptors. A weakly shared process lock and
bounded advisory `flock` serialize the addressed session; a short global lock
serializes topology creation before the session lock becomes the operation
owner. Busy state fails without automatic retry.

Canonical strict-UTF-8 JSON/JSONL has fixed schemas, sorted compact fields,
exact LF termination, duplicate-field rejection, and finite floats serialized
through canonical `float.hex` strings. Per-file/per-record/aggregate limits are
checked before allocation or mutation where applicable. Inspection streams and
digests bounded records, reconstructs every fragment sequence/source digest,
and proves provider/model/schema/chunking, manifest, counts, generation digest,
and active-pointer identity. The fixed store schema identifies these envelopes;
the caller-owned projection schema is an independently verified
`projection_schema_id` and checkpoint identity. The exact nonblank strict-UTF-8
inspection expectation is validated before filesystem I/O. Deep JSON recursion,
`NaN`/infinity, decimal or hex-float overflow, malformed Unicode, noncanonical
encoding, and inconsistent records map to content-free `corrupt` rather than
leaking persisted values or decoder details.

Generation mutation stays under one retained session lock and descriptor
scope. An inactive build is synchronized and verified before it is renamed to
its content-derived generation name, the generations directory is synchronized,
and the installed generation is reopened/reverified. Advisory `status.json` is
prepared next. `active.json` alone commits the generation. Its complete
temporary file and the session directory are synchronized before the atomic
replacement rename, which is deliberately the final callable publish action.
The store does not claim a post-rename directory sync: an immediate crash can
leave either the old or complete new valid pointer. Any failure reported before
that rename leaves the old pointer byte-identical; a complete but inactive
generation can remain as an orphan.

Inspection is observational and never cleans an orphan or temporary pointer.
After complete structural verification, a provider, model, or expected
projection-schema mismatch reports `incompatible` while retaining the verified
checkpoint plus safe provider/generation/count fields. This differs from an
incompatible envelope or unverifiable identity, which exposes no trusted
checkpoint. Advisory ready/stale/provider-failed status cannot hide either
incompatibility result.

The next generation mutation validates the complete namespace before removing
recognized build/inactive-generation residue. Unknown, unsafe, or divergent
state is preserved and rejected. Rebuild begins again from authoritative input;
it never resumes partial build bytes or relabels an existing generation. A
schema-changing rebuild writes a separate inactive generation and preserves the
old active pointer if any precommit step fails. Active readers reopen and
completely verify the exact snapshot, including its checkpoint projection
schema, under the same session lock and close descriptors exactly once at
context exit.

Purge requires exact `confirmed=True` and prevalidates the entire addressed
session namespace before removal. It removes the active pointer first and then
only status, pointer temporaries, and recognized derived generations; session
locks/directories remain. It neither follows/removes an unsafe name nor touches
authoritative filesystem/SQLite conversations, configuration, manuscripts,
artifacts, provider state, or another session. Advisory locks coordinate only
supported store participants; they are not authorization or protection against
an actively hostile same-user process.

The installed command adapter preserves those boundaries. `story rag status`
uses a no-write optimistic active-storage composition and repeated authority,
derived-state, and configuration reads; it creates neither the coordinator lock
nor this subtree. Confirmed rebuild holds the authoritative storage mutation
lease through target construction, derived publication, and final source
revalidation. Search rereads every retained range from the freshly activated
configured authority but writes no generation. Confirmed purge calls only this
session-contained derived owner and does not activate or inspect authoritative
storage. Enable/disable write only canonical configuration through its separate
compare-and-set owner.

Status/snapshot/error values and diagnostic records are content-free and do not
include source or query text. The verified source-map file does contain derived
plaintext fragments, so filesystem permissions do not replace workspace-level
data protection. Derived vectors and fragments are never authority; the
injected runtime service reauthorizes stable references and rereads conversation
bytes before use. See the
[persistence design](../design/persistence.md#optional-conversation-rag-derived-persistence),
[direct API](../reference/package-api.md#optional-conversation-rag-boundary),
and [current commands plus interactive workflow](../guides/conversation-rag.md).

<a id="sqlite-conversation-storage-safety"></a>

## SQLite conversation-storage safety

The optional adapter resolves only the fixed contained path
`discussion_logs/conversation_storage.sqlite3`. The named and opened database
must have the same meaningful regular-file identity with the exact link count
required by the operation. A symlink at the database name raises
`WorkspaceSecurityError`; a stable named entry that is nonregular but not a
symlink raises `WorkspaceFormatError`. A nonmeaningful or mismatched identity,
an unsafe opened type, replacement, or unexpected link count fails closed as
`WorkspaceSecurityError`. The adapter rechecks identity after each operation.
Its database owner reads the canonical workspace root once at construction and
retains the resulting workspace key and database path; later mutation of the
supplied `Workspace.root` cannot retarget the database. Connections use
non-creating SQLite URI modes: `mode=ro` for reads and `mode=rw` for writes.
Module import performs no I/O. Adapter construction never opens or creates the
database and does not load `sqlite3`; resolving a string or path-like workspace
still follows the ordinary `Workspace` construction contract. `sqlite3` is
loaded and probed only when an operation is requested, and SQLite 3.7.16 or
later is required.

Header verification requires the SQLite format header, DELETE rollback-journal
read/write versions, UTF-8 encoding, application ID `0x53574143` (`SWAC`), and
schema version `1`. Every connection against an existing database also verifies
the exact schema manifest and fingerprint and fixed schema cookie. Strict
preflight and composition readiness, along with new-temporary, existing-target,
race-winner, and recovered-target provisioning validation, additionally run
`integrity_check`, a global foreign-key check, an orphan scan, and full
catalog/event reconstruction. Routine reads and writes validate the rows
involved in their requested operation; database constraints, triggers, and
enabled foreign-key enforcement protect supported mutations. They do not scan
unrelated database contents. Preflight and ordinary reads are observational;
validation never repairs a value.

Database `TEXT` is decoded as strict UTF-8 on every connection. Malformed
stored text encountered by verification or the requested operation fails
closed as content-free `WorkspaceFormatError`, without
leaking the undecodable value or a cause. A `UnicodeDecodeError` raised by
caller-owned operation code remains that caller exception instead of being
misclassified as stored corruption.

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
does not run the exhaustive preflight scan. `SQLiteCollaborationHistorySearch` uses one
such main-database transaction for its complete bounded search rather than
composing several catalog and event-store operations.

### Derived FTS companion

The SQLite collaboration-history search adapter may consult the fixed private
`discussion_logs/.conversation_storage_fts5.sqlite3` companion while its main
snapshot is open. The companion is a derived candidate source, not authority:
every candidate is checked by exact normalized literal comparison against a
closed readable event field in the main database. Subjects never create hits.
False, missing, or reordered hints
therefore cannot change hits, citations, snippets, counts, truncation, or
result order.

The companion owner derives its path from the main database's already-pinned
workspace key. A usable companion is an owner-only, singly linked regular file
with no journal, WAL, or shared-memory sidecar and exact schema, source
generation, normalization, tokenizer, and SQLite-runtime identities. It is
opened with a noncreating URI. Missing, stale, busy, malformed, unsafe,
capability-mismatched, replaced, or otherwise unusable state becomes exact
main-database fallback. Search never creates, removes, rewrites, repairs, or
follows that state, and companion failure does not make the authoritative
database unready.

An effective supported mutation updates required searchable content and the
main lexical generation inside its authoritative transaction. Failure there
rolls back the mutation. Only after that commit, while the same workspace lease
remains valid, may the owner create an absent companion or atomically refresh a
recognized safe one. A companion publication or refresh failure leaves the
main commit intact and exact fallback available. No-op archive/unarchive calls
leave the generation and companion stable. Owner cleanup is limited to its own
unchanged temporary resources and one exactly recognized interrupted
create-only publication alias; unsafe or ambiguous names are preserved.

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

Append is specifically history-bounded: it obtains one plus the indexed maximum
sequence for the addressed conversation and reads at most the addressed turn's
user/outcome pair through the unique turn-slot index. It validates that pair and
the proposed event under the transaction and database triggers; it does not
read or reconstruct the complete transcript.

Context selection is also history-bounded. It obtains the exact total of
complete outcomes, then uses the recent-outcome and turn-slot indexes to
materialize at most the newest 25 complete pairs—50 events. That is sufficient
to retain the fixed newest 24 pairs and detect the turn boundary without a
full-history read. The complete `read()` operation remains a complete
transcript validation.

The privileged archive boundary is deliberately global. Export reconstructs
and validates every metadata/event row and rejects any orphan event. Import
validates the complete destination before mutation and again after mutation in
the same transaction. Orphan or malformed destination rows therefore fail
before insertion, and post-mutation invalidity rolls back every import rather
than silently adopting or dropping rows.

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
only the provisioning call's own temporary path and its temporary sidecars in
ordinary failure paths; the authoritative target and foreign sidecars are
preserved.

Explicit leased provisioning has one additional, narrow interrupted-publication
cleanup. If and only if the target and exactly one provisioning-pattern sibling
are regular names for the same two-link inode, with no sidecars or other link
ambiguity, provisioning exhaustively validates the intact two-link target
before any unlink. It then rechecks the lease, exact candidate set, sidecar
absence, and target/alias identity. Only then does it remove that sibling alias
and synchronize the target and directory; the recovered single-link target is
exhaustively validated again before success. Preflight and ordinary reads never
perform this cleanup. Symlinks, wrong or multiple candidates, a different
inode, an extra link, any sidecar, and corrupt or unsupported target content
remain fail-closed conditions; suspicious names and target content are not
rewritten. This recognizes residue from the adapter's interrupted hard-link
publication, not a general database-repair mechanism.

On a host without POSIX `fcntl`, existing valid storage remains available to
observational preflight and reads. The supported coordinator cannot acquire a
mutation lease, so provisioning and supported writes fail with
`ConversationStorageCoordinationError` before any database or conversation
domain change. Explicit installation through selection, configuration save,
and activation also fail closed because coordination is required. The lazy
guard file retains its separately documented lifecycle.

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
reconstructed, or converted. See
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
For a mixed candidate, Apply Changes first completes its provider gate in
memory, then delegates desired storage readiness, optional explicit
installation, provisioning, and fresh parity to the selection owner. Under that
workflow's active lease, the settings-owned precommit guard requires the
persona catalog to match the opening baseline; the config owner then revalidates
the complete pending pair and requires both exact byte snapshots to match. Any
mismatch or read failure reports a conflict and performs no configuration or
activation write. A candidate that changes only `show_message_details` skips
that provider/storage/runtime path: it prepares, verifies, and commits the exact
configuration candidate, then changes the active display flag only after the
commit succeeds. A candidate that changes only Conversation RAG also skips the
provider/storage/runtime path. It prepares, verifies, and commits only the
system configuration, performs no composition or optional-package probe, and
leaves the current RAG service and automatic policy unchanged until the next
shell.

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
filesystem, or power failure can still leave the pair partially durable. Host
installation and an empty SQLite database prepared before the config boundary
are not rolled back. A successful Apply Changes changes desired storage only; startup
owns later active-marker CAS.

Invalid identifiers raise `SessionIdError`; unsafe paths or symlinks raise
`WorkspaceSecurityError`; malformed or path-mismatched persisted data raises
`WorkspaceFormatError`. Reads fail closed rather than repairing a record.

Exact validation patterns are in [Identifiers and errors](../reference/configuration/identifiers-and-errors.md).
