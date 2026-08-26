# Canonical conversation archives

> **Availability:** canonical conversation archive values, deterministic bytes,
> and privileged filesystem/SQLite export/import are direct Python APIs. There
> is no raw archive shell command. Provider-free `story storage migrate` and
> `story storage verify` compose these adapters for two already-ready stores in
> one workspace; `StoryRuntime` does not run archive work automatically.

Key terms: **canonical**—one logical value has exactly one accepted byte image;
**authoritative conversation data**—catalog metadata and its complete
transcript; **additive import**—create absent identities without deleting or
replacing existing ones; **quiescent**—ordinary conversation writers cannot
change the source or destination during the operation.

<a id="public-archive-api"></a>

## Public API

The archive implementation is intentionally absent from the 120-name
`story_writing_agents` root facade. Its 22 archive-owned names and two
filesystem-adapter names are supported from
`story_writing_agents.conversation_storage`:

```python
from story_writing_agents.conversation_storage import (
    CONVERSATION_ARCHIVE_SCHEMA_VERSION,
    CONVERSATION_ARCHIVE_MAX_RAW_BYTES,
    CONVERSATION_ARCHIVE_MAX_SESSIONS,
    CONVERSATION_ARCHIVE_MAX_CONVERSATIONS,
    CONVERSATION_ARCHIVE_MAX_EVENTS,
    CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES,
    ConversationArchive,
    ConversationArchiveSession,
    ConversationArchiveConversation,
    ConversationArchiveEvent,
    ConversationArchiveError,
    ConversationArchiveFormatError,
    ConversationArchiveVersionError,
    ConversationArchiveBoundsError,
    ConversationArchiveDigestError,
    ConversationArchiveSourceError,
    ConversationArchiveSourceChangedError,
    ConversationArchiveDestinationError,
    ConversationArchiveConflictError,
    ConversationArchiveDestinationChangedError,
    FilesystemConversationArchiveAdapter,
    compose_filesystem_conversation_archive,
    serialize_conversation_archive,
    deserialize_conversation_archive,
)
```

The exact callable signatures are:

```text
serialize_conversation_archive(archive: ConversationArchive, /) -> bytes
deserialize_conversation_archive(data: bytes, /) -> ConversationArchive

FilesystemConversationArchiveAdapter(
    workspace: Workspace | str | os.PathLike[str],
)
compose_filesystem_conversation_archive(
    workspace: Workspace | str | os.PathLike[str],
    /,
) -> FilesystemConversationArchiveAdapter

adapter.export_archive(
    lease: ConversationStorageMutationLease,
) -> ConversationArchive
adapter.import_archive(
    lease: ConversationStorageMutationLease,
    payload: ConversationArchive,
    *,
    on_conversation_completed: Callable[[str, str], None] | None = None,
) -> ConversationArchiveImportReceipt
```

The supporting generic names used by those signatures are also public from the
conversation-storage subpackage:

```python
from story_writing_agents.conversation_storage import (
    ConversationArchiveImportReceipt,
    ConversationArchiveImportStatus,
    ConversationArchiveSink,
    ConversationArchiveSource,
    ConversationStorageCoordinationError,
    ConversationStorageMutationLease,
    WorkspaceConversationStorageMutationCoordinator,
)
```

`ConversationArchiveImportReceipt.status` is
`ConversationArchiveImportStatus.IMPORTED` when the call created at least one
file and `UNCHANGED` when the complete requested state already existed or the
payload was empty. The receipt, status, mutation-lease protocol, and workspace
coordinator are the existing generic contracts in the same subpackage. The
filesystem adapter implements
`ConversationArchiveSource[ConversationArchive]` and
`ConversationArchiveSink[ConversationArchive]`.
Adapter construction captures one canonical absolute root and workspace key in
a private immutable topology. It retains neither the caller's `Workspace` nor
public mutable catalog or event-store collaborators, and it performs no archive
scan or mutation. A path-like argument still follows ordinary `Workspace`
construction before the root is captured.

Each export, and each import after pure payload canonicalization, validates its
lease against the captured key before constructing one private operation-local
`Workspace`, `FilesystemConversationCatalog`, and
`FilesystemConversationEventStore` set. The set is used for the complete call
and is not exposed on the adapter. A wrong-root lease therefore fails before
collaborator materialization or filesystem I/O, while later mutation of the
original workspace cannot redirect root A transfer into root B. This topology
belongs to the archive adapter; it is separate from composed conversation
service pinning and SQLite's independent database owner.

The optional callback is invoked after each addressed filesystem conversation
has complete exact metadata and, when nonempty, an exact transcript, and after
that complete value has been reread. It runs under the caller's active lease;
an exception stops the import without deleting already completed create-only
records. The migration coordinator uses this hook to publish progress only
after a resumable unit is complete.

The optional SQLite module exposes another implementation of the same canonical
archive source/sink contract:

```python
from story_writing_agents.conversation_storage.sqlite import (
    SQLiteConversationArchiveAdapter,
    compose_sqlite_conversation_archive,
)
```

Its database must already be provisioned and ready. Export returns a canonical
`ConversationArchive`; import accepts that same value, preserves every exact
metadata/event field, and commits the complete archive import in one SQLite
transaction. The adapter and composer remain direct-module exports rather than
subpackage or package-root facade names.

<a id="archive-logical-values"></a>

## Immutable logical values

All four value types are frozen, slotted dataclasses. Their exact constructor
fields are:

```text
ConversationArchive(
    schema_version: int,
    sessions: tuple[ConversationArchiveSession, ...],
)
ConversationArchiveSession(
    session_id: str,
    conversations: tuple[ConversationArchiveConversation, ...],
)
ConversationArchiveConversation(
    schema_version: int,
    session_id: str,
    conversation_id: str,
    subject: str,
    subject_revision: int,
    status: ConversationStatus,
    created_at: str,
    updated_at: str,
    events: tuple[ConversationArchiveEvent, ...],
)
ConversationArchiveEvent(
    schema_version: int,
    session_id: str,
    conversation_id: str,
    turn_id: str,
    sequence: int,
    timestamp: str,
    sender: str,
    recipient: str,
    kind: ConversationEventKind,
    content: str,
)
```

The values, validators, canonical serializer, digest verification, and
deserializer own no mutable state or external resource and perform no I/O.

Schema version is the exact integer `1`; booleans and other versions fail.
Conversation and event fields preserve the exact public catalog and transcript
values, including archived status, timestamps, runtime-error events, and empty
history. An archive may contain no sessions, but a present session group must
contain at least one conversation.

Construction revalidates the existing catalog and event domain contracts,
identity correlation, unique session/conversation identities, contiguous event
sequence, and valid turn pairing. It canonicalizes sessions by `session_id`,
conversations by `(created_at, conversation_id)`, and events by `sequence`.
The logical `ConversationArchive` has no digest field; the digest belongs only
to its wire representation.

Validation short-circuits before deep traversal. Logical construction and
serialization require exact tuple containers, enforce each container's count
bound, and reject wrong shallow archive value types before inspecting their
descendants. Archive-wide conversation/event counts are also checked before
later values or text. Deserialization similarly preflights exact JSON container
shapes and global counts before constructing values. Subject and event-content
UTF-8 bytes consume one incremental 32 MiB budget at each applicable scope;
exhaustion fails immediately without inspecting later text.

<a id="archive-wire-format"></a>

## Canonical wire format and bounds

`serialize_conversation_archive()` emits one compact strict-UTF-8 JSON object
with exact field order and one final LF. It emits no BOM, insignificant
whitespace, or second newline. The top-level fields are exactly
`schema_version`, `sessions`, and `digest`. The digest object is exactly
`{"algorithm":"sha256","value":"LOWERCASE_HEX"}`.
Nested session, conversation, and event field order follows the constructor
field order above; `status` and `kind` use their exact enum value strings.

SHA-256 covers the canonical JSON image containing only `schema_version` and
`sessions`, including that image's final LF. Deserialization verifies the
digest and then requires byte-for-byte equality with a fresh canonical
serialization. It rejects malformed UTF-8/JSON, duplicate, missing, or extra
fields, non-finite constants, noncanonical field or collection order,
alternate escaping/spacing/newlines, invalid domain values, and unsupported
versions. The digest detects accidental or untrusted-byte modification; it is
not a signature, encryption, or proof of who produced the archive.

The fixed version-1 bounds are:

| Constant | Exact value | Applied to |
| --- | ---: | --- |
| `CONVERSATION_ARCHIVE_MAX_RAW_BYTES` | 67,108,864 | Complete serialized or input byte image |
| `CONVERSATION_ARCHIVE_MAX_SESSIONS` | 1,024 | One archive |
| `CONVERSATION_ARCHIVE_MAX_CONVERSATIONS` | 65,536 | One session and the archive-wide total |
| `CONVERSATION_ARCHIVE_MAX_EVENTS` | 1,000,000 | One conversation, one session, and the archive-wide total |
| `CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES` | 33,554,432 | One event's content and sums of subjects/event content at conversation, session, and archive scopes |

Bounds are checked during logical construction, serialization, and
deserialization as applicable, before an archive payload is imported. All
limits are inclusive. A `RecursionError` while deserializing deeply nested JSON
becomes a content-free `ConversationArchiveFormatError` without retaining its
cause.

<a id="filesystem-archive-utilization"></a>

## Application-controlled filesystem transfer

The caller must keep ordinary conversation writers quiescent for the whole
export or import. The mutation lease is mandatory coordination evidence for
the addressed workspace, owner thread, and bounded lifetime. It is not transfer
authorization or a filesystem transaction, and ordinary catalog/event-store
calls do not acquire it automatically.

```python
from story_writing_agents import Workspace
from story_writing_agents.conversation_storage import (
    WorkspaceConversationStorageMutationCoordinator,
    compose_filesystem_conversation_archive,
    deserialize_conversation_archive,
    serialize_conversation_archive,
)

# The application first stops or coordinates ordinary source conversation writes.
source = Workspace("/path/to/source-story")
source_adapter = compose_filesystem_conversation_archive(source)
source_coordinator = WorkspaceConversationStorageMutationCoordinator(source)
with source_coordinator.borrow(timeout_seconds=5.0) as source_lease:
    payload = source_adapter.export_archive(source_lease)

wire = serialize_conversation_archive(payload)
# The application owns any storage, transmission, access control, and retention.
received = deserialize_conversation_archive(wire)

# The application separately authorizes this exact destination and keeps it quiescent.
destination = Workspace("/path/to/destination-story")
destination_adapter = compose_filesystem_conversation_archive(destination)
destination_coordinator = WorkspaceConversationStorageMutationCoordinator(
    destination
)
with destination_coordinator.borrow(timeout_seconds=5.0) as destination_lease:
    receipt = destination_adapter.import_archive(destination_lease, received)
```

Each adapter validates its own lease. This API does not decide whether the
caller may move data between workspaces, and it does not persist `wire` itself.
Import performs its complete payload and destination preflight inside
`import_archive()`; there is no separate filesystem-archive preflight function.

<a id="filesystem-archive-export-scope"></a>

## Export scope and exclusions

Filesystem export reads only authoritative conversation metadata and transcript
files beneath:

```text
discussion_logs/<session_id>/interactive/conversations/<conversation_id>/
├── metadata.json
└── transcript.jsonl    # present only for nonempty history
```

It includes active and archived conversations, every exact event kind, and
metadata-only conversations with empty history. A normally initialized empty
workspace exports `ConversationArchive(1, ())`; a missing source
`discussion_logs/` root is invalid rather than another spelling of empty.

Safe regular non-session files under `discussion_logs/`, safe regular siblings
of `interactive` in session directories, and valid sessions without an
`interactive` directory are excluded. They still consume bounded inventory
work, but ordinary excluded-file bytes are neither read nor compared. The
initialized `.gitkeep` sentinel is special: its single-link regular-file shape
and zero size are checked from metadata without reading its bytes.

The archive excludes debate logs, runtime logs, debate state/results/context,
credentials, provider/session and agent state, prompts and configuration,
defaults/focus, storage activation state, RAG state, manuscripts, canonical
writing artifacts, and every other workspace channel. Export preserves none of
those channels, even when they coexist under the same workspace.

Each scan enumerates the complete bounded namespace, then reads only addressed
records through `FilesystemConversationCatalog.get()` and
`FilesystemConversationEventStore.read()`; it never uses a catalog-wide list.
Before returning, export performs two such scans with one operation-local
collaborator set under the same active lease and requires identical canonical
archive bytes. Excluded-only changes do not affect those bytes, although every
excluded entry is still validated and charged in its scan. The comparison
detects an ordinary conversation change but does not lock or make writers
linearizable. The source must already be quiescent; a detected change fails
with `ConversationArchiveSourceChangedError` and yields no archive.

<a id="filesystem-archive-bounds"></a>

## Filesystem scan and projected-union bounds

The filesystem adapter adds fixed inclusive work bounds to the canonical value
bounds:

| Bound | Exact maximum | Scope |
| --- | ---: | --- |
| Descendant inventory entries | 1,000,000 | Every authoritative, structural, or excluded child enumerated below `discussion_logs/` in one scan; import also projects missing planned descendants |
| Contributing sessions | 1,024 | Sessions containing at least one authoritative conversation in one scan or projected union |
| Conversations | 65,536 | Complete scan or projected destination-plus-payload union |
| Events | 1,000,000 | Complete scan or projected destination-plus-payload union |
| One included metadata or transcript file | 67,108,864 bytes (64 MiB) | Existing file size or exact planned encoded bytes |
| Total included metadata and transcript bytes | 134,217,728 bytes (128 MiB) | Complete scan or projected destination-plus-payload union; excluded bytes never contribute |
| Logical subject and event-content UTF-8 | 33,554,432 bytes (32 MiB) | Complete scan or projected destination-plus-payload union |
| Canonical archive wire | 67,108,864 bytes (64 MiB) | Exported archive or projected destination-plus-payload union |

Export applies these bounds independently to both complete scans. Import
rejects an overflowing payload, destination, or projected union before its
first archive write.

<a id="filesystem-archive-import-semantics"></a>

## Import and resumability

Import first round-trips the payload through the canonical serializer and
deserializer, validates its lease, and scans the complete destination
conversation namespace. Before creating a file, it classifies the complete
destination-plus-payload union, builds the exact canonical metadata and
transcript bytes for every missing file, projects paths, descendant entries,
included bytes, logical values, and canonical wire against every bound, and
checks every planned target and ancestor path. Unsafe or malformed layout, a
late addressed conflict, or any projected overflow therefore fails before an
earlier absent identity is installed. Import then:

- creates absent conversation directories and exact metadata create-only;
- creates a canonical JSONL transcript only when events are present;
- accepts exact metadata/transcript domain-value collisions without rewriting
  them;
- adds absent archive identities while preserving unrelated complete
  destination conversations and excluded state;
- rejects a matching identity whose metadata or transcript differs;
- validates the lease and target path around each file installation; and
- rescans the complete destination and requires the expected post-import
  snapshot before returning.

An absent destination `discussion_logs/` root is valid. A successful empty
import leaves it absent and returns `UNCHANGED`; a nonempty import creates only
the addressed canonical conversation paths.

Import never overwrites or deletes. An empty archive is therefore unchanged,
not a request to empty the destination. The privileged path installs exact IDs,
timestamps, revisions, status, sequence, and event values from the archive;
ordinary `catalog.create()` and `event_store.append()` retain their existing
allocation behavior for later application work.

A create-only collision that appears after preflight is accepted only when an
addressed catalog/event reread proves the exact domain value. A divergent or
unsafe collision is preserved and raises
`ConversationArchiveDestinationChangedError`. A post-write unrelated change is
also preserved; the final complete rescan reports the change without removing
already created exact archive state.

An operational interruption may leave only canonical addressed directory
prefixes, one or more complete exact conversations, or exact metadata awaiting
its nonempty transcript. Reapplying the identical canonical payload under a
fresh valid lease can complete those exact recognized prefixes. A divergent
file, unknown artifact, or changed post-preflight destination is never treated
as resumable and is never overwritten.

<a id="conversation-storage-migration"></a>

## Same-workspace migration and verification

The direct `story_writing_agents.conversation_storage.migration` module and
`story storage` commands turn the two archive adapters into one bounded
filesystem↔SQLite workflow. They always operate inside one initialized
workspace. The source must be the active backend, the destination must be the
other backend, and SQLite must already be provisioned and ready.

Migration copies the complete canonical archive: active and archived
conversations, empty histories, exact timestamps/revisions, and every event
kind including runtime errors. It excludes all non-conversation workspace
channels and the derived FTS companion. A deterministic plan must have no
conflict, SQLite-unrepresentable record, or destination-only identity before
any transfer. The coordinator then performs destination work and requires fresh
source and destination exports to equal the original canonical source bytes.

Use dry-run before transfer and verify afterward:

```bash
story storage migrate --from filesystem --to sqlite --dry-run
story storage migrate --from filesystem --to sqlite
story storage verify --from filesystem --to sqlite
```

Neither operation changes the active backend. Migration retains a strict,
privacy-limited per-direction checkpoint for safe replay of exact complete
filesystem conversations and terminal audit. Dry-run never writes a checkpoint;
verify ignores it. Exact JSON fields, exit codes, failure codes, and recovery
steps are in the [CLI reference](cli.md#conversation-storage-migration) and
[migration recovery](../operations/recovery-and-limitations.md#conversation-storage-migration-recovery).

<a id="archive-failures"></a>

## Content-free failures

Archive exceptions have fixed messages that do not include subjects, content,
paths, or malformed bytes:

| Exception | Exact message |
| --- | --- |
| `ConversationArchiveError` | `conversation archive error` |
| `ConversationArchiveFormatError` | `conversation archive format is invalid` |
| `ConversationArchiveVersionError` | `conversation archive version is unsupported` |
| `ConversationArchiveBoundsError` | `conversation archive bounds exceeded` |
| `ConversationArchiveDigestError` | `conversation archive digest is invalid` |
| `ConversationArchiveSourceError` | `conversation archive source is invalid` |
| `ConversationArchiveSourceChangedError` | `conversation archive source changed` |
| `ConversationArchiveDestinationError` | `conversation archive destination is invalid` |
| `ConversationArchiveConflictError` | `conversation archive destination conflicts` |
| `ConversationArchiveDestinationChangedError` | `conversation archive destination changed` |

`ConversationArchiveSourceChangedError` is a source error.
`ConversationArchiveConflictError` and
`ConversationArchiveDestinationChangedError` are destination errors. An
invalid, expired, released, wrong-thread, or wrong-workspace lease instead
raises the existing content-free `ConversationStorageCoordinationError`.

For topology checks, symlinks, hard-linked files, nonregular files, invalid
identifiers, unknown interactive/conversation artifacts, orphan transcripts,
empty present transcripts, malformed records, and changed paths fail closed.
Excluded data is preserved, but an unsafe excluded entry also fails rather
than being followed or silently ignored. See
[Filesystem safety](../operations/filesystem-safety.md#conversation-archive-filesystem-safety)
and [Recovery](../operations/recovery-and-limitations.md#conversation-archive-recovery).

<a id="archive-non-goals"></a>

## Privacy boundary and non-goals

Archive values and bytes contain plaintext subjects and transcript content.
The archive API provides no encryption, secure erase, authentication,
authorization, managed archive file, network transport, or retention policy.
Its digest is integrity evidence only. A caller that stores or transmits the
bytes owns all resulting copies and exposure.

The archive layer provides transport infrastructure, not a complete workspace
backup, arbitrary restore, ordinary catalog/event mutation or default
selection, cross-workspace authorization, backend activation, dual write,
`/settings` integration, MySQL behavior, or RAG integration. The separate
same-workspace migration workflow composes the filesystem and SQLite adapters;
it still does not copy or delete the excluded workspace channels, provision a
database, activate a backend, or establish cross-workspace authorization.
Catalog and transcript domain contracts remain in
[Conversation catalog](conversation-catalog.md) and
[Conversation transcripts](conversation-transcripts.md).
