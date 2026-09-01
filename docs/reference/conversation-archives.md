# Canonical conversation archives

> **Availability:** canonical conversation archive values, deterministic bytes,
> and privileged filesystem/SQLite export/import are direct Python APIs. There
> is no raw archive shell command. Provider-free `story storage transfer` and
> `story storage verify` compose these adapters for two already-ready stores in
> one workspace; `StoryRuntime` does not run archive work automatically.

Key terms: **canonical**—one logical value has exactly one accepted byte image;
**authoritative conversation data**—catalog metadata and its complete v2
collaboration history; **additive import**—create absent identities without deleting or
replacing existing ones; **quiescent**—ordinary conversation writers cannot
change the source or destination during the operation.

<a id="public-archive-api"></a>

## Public API

The archive implementation is intentionally absent from the 118-name
`story_writing_agents` root facade. Its 21 archive-owned names and two
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
`FilesystemCollaborationEventStore` set. The set is used for the complete call
and is not exposed on the adapter. A wrong-root lease therefore fails before
collaborator materialization or filesystem I/O, while later mutation of the
original workspace cannot redirect root A transfer into root B. This topology
belongs to the archive adapter; it is separate from composed conversation
service pinning and SQLite's independent database owner.

The optional callback is invoked after each addressed filesystem conversation
has complete exact metadata and, when nonempty, an exact collaboration-history
file, and after
that complete value has been reread. It runs under the caller's active lease;
an exception stops the import without deleting already completed create-only
records. The transfer coordinator uses this hook to publish progress only
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
metadata/event field and canonical event BLOB, and commits the complete archive
import in one SQLite transaction. Any row in the legacy `conversation_events`
table rejects export or import before mutation; only `collaboration_events` is
archive authority. The derived FTS companion is excluded. A best-effort FTS
refresh after commit cannot change the canonical result or parity. The adapter
and composer remain direct-module exports rather than subpackage or
package-root facade names.

<a id="archive-logical-values"></a>

## Immutable logical values

All three archive value types are frozen, slotted dataclasses. Their exact constructor
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
    events: tuple[CollaborationEvent, ...],
)
```

`ConversationArchiveEvent` no longer exists. Each item in `events` is the
canonical schema-v2 `story_writing_agents.conversations.CollaborationEvent`,
including its event/turn/step IDs, batch identity and digest, sequence and
timestamp, typed sender/recipient, closed event kind, and typed payload. This
is why proposals, confirmations, specialist replies, execution results,
interruptions, and recovery facts survive a backend transfer exactly.

The values, validators, canonical serializer, digest verification, and
deserializer own no mutable state or external resource and perform no I/O.

The top-level archive schema is the exact integer `2`; schema-1 archive bytes,
booleans, and other versions fail. The `schema_version` inside each
`ConversationArchiveConversation` is deliberately `1`, because it is the
unchanged catalog-metadata schema—not the archive schema. Conversation and
event fields preserve the exact public catalog and collaboration values,
including archived status, timestamps, typed outcomes, and empty history. An
archive may contain no sessions, but a present session group must contain at
least one conversation.

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
shapes and global counts before constructing values. Subject UTF-8 plus each
canonical encoded event's bytes consume one incremental 32 MiB budget at each applicable scope;
exhaustion fails immediately without inspecting later text.

<a id="archive-wire-format"></a>

## Canonical wire format and bounds

`serialize_conversation_archive()` emits one compact strict-UTF-8 JSON object
with exact field order and one final LF. It emits no BOM, insignificant
whitespace, or second newline. The top-level fields are exactly
`schema_version`, `sessions`, and `digest`. The digest object is exactly
`{"algorithm":"sha256","value":"LOWERCASE_HEX"}`.
Nested session and conversation field order follows the constructor field order
above. Events use the collaboration event codec's exact canonical field order;
`status` and event enums use their exact value strings.

SHA-256 covers the canonical JSON image containing only `schema_version` and
`sessions`, including that image's final LF. Deserialization verifies the
digest and then requires byte-for-byte equality with a fresh canonical
serialization. It rejects malformed UTF-8/JSON, duplicate, missing, or extra
fields, non-finite constants, noncanonical field or collection order,
alternate escaping/spacing/newlines, invalid domain values, and unsupported
versions. The digest detects accidental or untrusted-byte modification; it is
not a signature, encryption, or proof of who produced the archive.

The fixed version-2 archive bounds are:

| Constant | Exact value | Applied to |
| --- | ---: | --- |
| `CONVERSATION_ARCHIVE_MAX_RAW_BYTES` | 67,108,864 | Complete serialized or input byte image |
| `CONVERSATION_ARCHIVE_MAX_SESSIONS` | 1,024 | One archive |
| `CONVERSATION_ARCHIVE_MAX_CONVERSATIONS` | 65,536 | One session and the archive-wide total |
| `CONVERSATION_ARCHIVE_MAX_EVENTS` | 1,000,000 | One conversation, one session, and the archive-wide total |
| `CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES` | 33,554,432 | Sums of subjects and canonical encoded event bytes at conversation, session, and archive scopes |

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

Filesystem export reads only authoritative conversation metadata and v2 history
files beneath:

```text
discussion_logs/<session_id>/interactive/conversations/<conversation_id>/
├── metadata.json
├── collaboration-history.jsonl      # present only for nonempty history
└── .collaboration-history.lock      # excluded safe lock, never archived
```

It includes active and archived conversations, every exact event kind, and
metadata-only conversations with empty history. A normally initialized empty
workspace exports `ConversationArchive(2, ())`; a missing source
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
`FilesystemCollaborationEventStore.read()`; it never uses a catalog-wide list.
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
| One included metadata or history file | 67,108,864 bytes (64 MiB) | Existing file size or exact planned encoded bytes |
| Total included metadata and history bytes | 134,217,728 bytes (128 MiB) | Complete scan or projected destination-plus-payload union; excluded bytes never contribute |
| Logical subject and canonical event UTF-8 | 33,554,432 bytes (32 MiB) | Complete scan or projected destination-plus-payload union |
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
history bytes for every missing file, projects paths, descendant entries,
included bytes, logical values, and canonical wire against every bound, and
checks every planned target and ancestor path. Unsafe or malformed layout, a
late addressed conflict, or any projected overflow therefore fails before an
earlier absent identity is installed. Import then:

- creates absent conversation directories and exact metadata create-only;
- publishes one complete canonical JSONL history file only when events are present;
- accepts exact metadata/history domain-value collisions without rewriting
  them;
- adds absent archive identities while preserving unrelated complete
  destination conversations and excluded state;
- rejects a matching identity whose metadata or history differs;
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
its nonempty history file. Reapplying the identical canonical payload under a
fresh valid lease can complete those exact recognized prefixes. A divergent
file, unknown artifact, or changed post-preflight destination is never treated
as resumable and is never overwritten.

Any `transcript.jsonl` beneath the canonical conversation namespace is legacy
schema-v1 data. Export or import rejects the complete filesystem archive before
destination mutation; it never converts or mixes that file with v2 history.

<a id="conversation-storage-transfer"></a>

## Same-workspace transfer and verification

The direct `story_writing_agents.conversation_storage.migration` module and
`story storage` commands turn the two archive adapters into one bounded
filesystem↔SQLite workflow. They always operate inside one initialized
workspace. The source must be the active backend, the destination must be the
other backend, and SQLite must already be provisioned and ready.

The internal filename `migration.py` is retained only to avoid an incidental
module move in this change. It exports transfer-named APIs only and is not a
schema converter, compatibility layer, or public `migrate` command.

Transfer copies the complete canonical archive: active and archived
conversations, empty histories, exact timestamps/revisions, and every event
kind including execution outcomes and recovery facts. It excludes all non-conversation workspace
channels and the derived FTS companion. A deterministic plan must have no
conflict, SQLite-unrepresentable record, or destination-only identity before
any transfer. The coordinator then performs destination work and requires fresh
source and destination exports to equal the original canonical source bytes.

For a filesystem-active workspace, use dry-run before transfer and verify
afterward:

```bash
story storage transfer --from filesystem --to sqlite --dry-run
story storage transfer --from filesystem --to sqlite
story storage verify --from filesystem --to sqlite
```

Representative content-free dry-run output is:

```json
{"checkpoint":"none","counts":{"conflict":0,"create":3,"destination_only":0,"identical":0,"unsupported":0},"dry_run":true,"failure":null,"from":"filesystem","operation":"transfer","outcome":"work_required","parity":"unverified","schema_version":1,"source":{"conversations":3,"events":25},"to":"sqlite"}
```

The real transfer returns `outcome:"completed"`, `parity:"verified"`, and
`checkpoint:"completed"`; the following independent `verify` returns
`operation:"verify"`, `parity:"verified"`, and `checkpoint:"none"`.

Neither operation changes the active backend. Transfer retains a strict,
privacy-limited per-direction checkpoint for safe replay of exact complete
filesystem conversations and terminal audit. Dry-run never writes a checkpoint;
verify ignores it. Exact JSON fields, exit codes, failure codes, and recovery
steps are in the [CLI reference](cli.md#conversation-storage-transfer) and
[transfer recovery](../operations/recovery-and-limitations.md#conversation-storage-transfer-recovery).

The visible state flow is deliberately explicit:

```text
dry-run: work_required
  -> transfer: completed + parity verified + checkpoint completed
  -> verify: completed + parity verified
  -> storage use DESTINATION: desired backend saved
  -> next story launch: destination may become active after fresh readiness/parity checks
```

Transfer never provisions, selects, activates, dual-writes, deletes the source,
or backs up the workspace. Reversing direction uses the same commands with
`--from sqlite --to filesystem`; SQLite must then be active. A retry rereads
the source, validates any direction checkpoint and exact destination records,
and resumes only trusted create-or-identical work.

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
identifiers, unknown interactive/conversation artifacts, legacy transcripts,
empty present histories, malformed records, and changed paths fail closed.
Excluded data is preserved, but an unsafe excluded entry also fails rather
than being followed or silently ignored. See
[Filesystem safety](../operations/filesystem-safety.md#conversation-archive-filesystem-safety)
and [Recovery](../operations/recovery-and-limitations.md#conversation-archive-recovery).

<a id="archive-non-goals"></a>

## Privacy boundary and non-goals

Archive values and bytes contain plaintext subjects and collaboration payloads.
The archive API provides no encryption, secure erase, authentication,
authorization, managed archive file, network transport, or retention policy.
Its digest is integrity evidence only. A caller that stores or transmits the
bytes owns all resulting copies and exposure.

The archive layer provides transport infrastructure, not a complete workspace
backup, arbitrary restore, ordinary catalog/event mutation or default
selection, cross-workspace authorization, backend activation, dual write,
`/settings` integration, MySQL behavior, or RAG integration. The separate
same-workspace transfer workflow composes the filesystem and SQLite adapters;
it still does not copy or delete the excluded workspace channels, provision a
database, activate a backend, or establish cross-workspace authorization.
Catalog and current transcript domain contracts remain in
[Conversation catalog](conversation-catalog.md) and
[Conversation transcripts](conversation-transcripts.md).
